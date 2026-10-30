"""Round 4: .txt as CSV and remembered staging kinds (kind415.py), in-use uploads vs the
expiry sweep (inuse.py), prose shape warning (prose.py), views yield to jobs."""

from __future__ import annotations

import io
import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
import xlsxwriter
from fastapi.testclient import TestClient

from viz.contracts import ARROW_META_KEY
from viz.ingest import importer

Stage = Callable[[str, bytes], dict[str, Any]]
TSV = b"a\tb\n1\t2\n3\t4\n"


def read_arrow(content: bytes) -> tuple[pa.Table, dict[str, Any]]:
    table = pa.ipc.open_stream(content).read_all()
    return table, json.loads(table.schema.metadata[ARROW_META_KEY.encode()])


def import_job(client: TestClient, **body: Any) -> Any:
    response = client.post("/api/datasets/import", json=body)
    assert response.status_code == 202, response.text
    return client.app.state.jobs.wait(response.json()["id"], 120)  # type: ignore[attr-defined]


def columns_of(client: TestClient, dataset_id: str) -> list[str]:
    dataset = client.get(f"/api/datasets/{dataset_id}").json()
    return [c["name"] for c in dataset["versions"][-1]["columns"]]


# -- kind415.py ------------------------------


def test_txt_is_auto_detected_as_csv(client: TestClient, tmp_path: Path) -> None:
    response = client.post("/api/datasets/staged", files={"file": ("x.txt", TSV)})
    assert response.status_code == 201 and response.json()["kind"] == "csv"
    job = import_job(client, stagedId=response.json()["stagedId"])
    assert job.status == "succeeded" and columns_of(client, job.result.dataset_id) == ["a", "b"]
    path = tmp_path / "export.txt"  # Excel's "Text (Tab delimited)" export
    path.write_bytes(TSV)
    assert client.post("/api/datasets/preview", json={"path": str(path)}).status_code == 200
    job = import_job(client, path=str(path))
    assert job.status == "succeeded"
    ds = job.result.dataset_id
    refresh = client.post(f"/api/datasets/{ds}/refresh", json={})
    assert client.app.state.jobs.wait(refresh.json()["id"], 60).status == "succeeded"  # type: ignore[attr-defined]


def test_case_insensitive_extensions(client: TestClient) -> None:
    buffer = io.BytesIO()
    book = xlsxwriter.Workbook(buffer, {"in_memory": True})
    book.add_worksheet("S").write_row(0, 0, ["x", "y"])
    book.close()
    for name, body, kind in (
        ("UPPER.CSV", b"a,b\n1,2\n", "csv"),
        ("Mixed.Xlsx", buffer.getvalue(), "excel"),
        ("data.JSON", b'[{"a": 1}]', "json"),
    ):
        response = client.post("/api/datasets/staged", files={"file": (name, body)})
        assert response.status_code == 201 and response.json()["kind"] == kind, name


def test_no_extension_needs_a_kind_which_is_then_remembered(client: TestClient) -> None:
    assert (
        client.post("/api/datasets/staged", files={"file": ("noext", b"a,b\n1,2\n")}).status_code
        == 415
    )
    response = client.post(
        "/api/datasets/staged?kind=csv", files={"file": ("noext", b"a,b\n1,2\n")}
    )
    assert response.status_code == 201
    sid = response.json()["stagedId"]
    assert client.post("/api/datasets/preview", json={"stagedId": sid}).status_code == 200
    job = import_job(client, stagedId=sid)  # no kind repeated
    assert job.status == "succeeded" and columns_of(client, job.result.dataset_id) == ["a", "b"]
    sidecar = client.app.state.storage.staged_dir / sid / ".kind"  # type: ignore[attr-defined]
    assert sidecar.read_text() == "csv"
    # The sidecar is never mistaken for the upload itself.
    assert client.app.state.storage.find_staged(sid).name == "noext"  # type: ignore[attr-defined]


def test_staged_refresh_kind_falls_back_and_is_checked_at_submit(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "export.txt"
    path.write_bytes(TSV)
    ds = import_job(client, path=str(path), kind="tsv").result.dataset_id
    # Re-upload with an unrecognised name and no kind: the dataset's kind (tsv) is used.
    staged = client.post("/api/datasets/staged?kind=tsv", files={"file": ("new", b"a\tb\n9\t9\n")})
    response = client.post(
        f"/api/datasets/{ds}/refresh", json={"stagedId": staged.json()["stagedId"]}
    )
    assert response.status_code == 202
    job = client.app.state.jobs.wait(response.json()["id"], 60)  # type: ignore[attr-defined]
    assert job.status == "succeeded", job.error
    # A re-upload whose bytes don't match the kind is a synchronous 415, not a failed job.
    staged = client.post("/api/datasets/staged?kind=tsv", files={"file": ("bin", b"\x00\x01" * 50)})
    if staged.status_code == 201:
        response = client.post(
            f"/api/datasets/{ds}/refresh", json={"stagedId": staged.json()["stagedId"]}
        )
        assert response.status_code == 415
    else:
        assert staged.status_code == 415


# -- inuse.py ------------------------------


def test_sweep_skips_an_upload_a_job_is_using(client: TestClient, stage: Stage) -> None:
    """The hourly sweep lands between the encoding scan and the link of a >24 h-old upload."""
    storage = client.app.state.storage  # type: ignore[attr-defined]
    staged = stage("old.csv", ("id,name\n" + "".join(f"{i},Zürich\n" for i in range(500))).encode())
    sid = staged["stagedId"]
    folder = storage.staged_path(sid)
    old = time.time() - 2 * 86400
    os.utime(folder, (old, old))
    removed: list[int] = []
    real = importer._link_or_copy

    def sweep_then_link(src: Path, dst: Path, on_bytes: Any) -> None:
        assert storage.staged_in_use(sid)
        removed.append(storage.sweep(staged_max_age_s=86400, exports_max_age_s=3600))
        real(src, dst, on_bytes)

    importer._link_or_copy = sweep_then_link  # type: ignore[assignment]
    try:
        job = import_job(client, stagedId=sid)
    finally:
        importer._link_or_copy = real  # type: ignore[assignment]
    assert job.status == "succeeded", job.error
    assert removed == [0]
    assert time.time() - folder.stat().st_mtime < 3600  # touched when the job started
    assert not storage.staged_in_use(sid)  # released when the job ended


def test_queued_jobs_hold_their_upload(
    client: TestClient, stage: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = client.app.state.storage  # type: ignore[attr-defined]
    first, second = stage("a.csv", b"a\n1\n"), stage("b.csv", b"b\n2\n")
    entered, release = threading.Event(), threading.Event()
    real = importer.write_version

    def slow(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr("viz.ingest.jobs.write_version", slow)
    job_a = client.post("/api/datasets/import", json={"stagedId": first["stagedId"]}).json()
    assert entered.wait(10)
    job_b = client.post("/api/datasets/import", json={"stagedId": second["stagedId"]}).json()
    assert job_b["status"] == "queued"
    assert storage.staged_in_use(second["stagedId"])
    assert client.delete(f"/api/datasets/staged/{second['stagedId']}").status_code == 409
    release.set()
    jobs = client.app.state.jobs  # type: ignore[attr-defined]
    assert jobs.wait(job_a["id"], 60).status == "succeeded"
    assert jobs.wait(job_b["id"], 60).status == "succeeded"
    assert not storage.staged_in_use(second["stagedId"])


def test_cancelled_queued_job_releases_its_upload(
    client: TestClient, stage: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = client.app.state.storage  # type: ignore[attr-defined]
    first, second = stage("a.csv", b"a\n1\n"), stage("b.csv", b"b\n2\n")
    entered, release = threading.Event(), threading.Event()
    real = importer.write_version

    def slow(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr("viz.ingest.jobs.write_version", slow)
    job_a = client.post("/api/datasets/import", json={"stagedId": first["stagedId"]}).json()
    assert entered.wait(10)
    job_b = client.post("/api/datasets/import", json={"stagedId": second["stagedId"]}).json()
    assert client.post(f"/api/jobs/{job_b['id']}/cancel").json()["status"] == "cancelled"
    assert not storage.staged_in_use(second["stagedId"])
    release.set()
    client.app.state.jobs.wait(job_a["id"], 60)  # type: ignore[attr-defined]


# -- prose.py ------------------------------


def test_long_single_column_text_warns(client: TestClient) -> None:
    sentence = "We agreed to ship the new import wizard on Friday after the review meeting today"
    text = "".join(f"{sentence} number {i}\n" for i in range(6)).encode()
    sid = client.post("/api/datasets/staged", files={"file": ("n.txt", text)}).json()["stagedId"]
    _, meta = read_arrow(client.post("/api/datasets/preview", json={"stagedId": sid}).content)
    assert any("one column of long text" in w for w in meta["warnings"])
    job = import_job(client, stagedId=sid)
    assert job.status == "succeeded"
    assert any("one column of long text" in w for w in job.result.warnings)


def test_short_single_column_has_no_prose_warning(client: TestClient) -> None:
    sid = client.post(
        "/api/datasets/staged", files={"file": ("ids.csv", b"id\n1\n2\n3\n4\n5\n")}
    ).json()["stagedId"]
    _, meta = read_arrow(client.post("/api/datasets/preview", json={"stagedId": sid}).content)
    assert meta["warnings"] == []


# -- views yield to jobs ------------------------------


def test_a_job_stops_a_running_view_build(tmp_path: Path) -> None:
    from viz.engine import Engine

    eng = Engine(temp_dir=tmp_path / "t", job_threads=1)
    try:
        result: dict[str, str] = {}

        def build() -> None:
            try:
                with eng.background_cursor("view:x") as cur:
                    cur.sql("SELECT count(*) FROM range(100000000000) t").fetchall()
                result["view"] = "ran"
            except Exception as exc:
                result["view"] = getattr(exc, "reason", type(exc).__name__)

        thread = threading.Thread(target=build)
        thread.start()
        time.sleep(0.3)
        with eng.background_cursor("job:1") as cur:
            assert cur.sql("SELECT 1").fetchall() == [(1,)]
            assert eng.active_jobs() == 1
            assert eng.wait_interactive_idle(quiet_s=0.05, max_wait_s=0.2, no_jobs=True) is False
        thread.join(10)
        assert result["view"] == "yielded"
        assert eng.wait_interactive_idle(quiet_s=0.05, max_wait_s=2, no_jobs=True) is True
    finally:
        eng.close()
