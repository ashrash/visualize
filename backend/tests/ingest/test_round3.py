"""Round 3: encoding threshold counts characters (B), staged uploads in use (nit 1),
warning wording (nit 3)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from viz.contracts import ARROW_META_KEY, ImportOptions, SourceKind
from viz.ingest import importer
from viz.ingest.importer import choose_encoding, replaced_warning
from viz.ingest.staging import using_staged

Stage = Callable[[str, bytes], dict[str, Any]]


def read_arrow(content: bytes) -> tuple[pa.Table, dict[str, Any]]:
    table = pa.ipc.open_stream(content).read_all()
    return table, json.loads(table.schema.metadata[ARROW_META_KEY.encode()])


def import_job(client: TestClient, **body: Any) -> Any:
    response = client.post("/api/datasets/import", json=body)
    assert response.status_code == 202, response.text
    return client.app.state.jobs.wait(response.json()["id"], 120)  # type: ignore[attr-defined]


def test_cp1252_with_accidental_utf8_pairs_stays_cp1252(client: TestClient, stage: Stage) -> None:
    """Repro a2: 40 cp1252 "Zoé" rows plus 20 "CafÃ©" rows (whose bytes C3 A9 happen to be
    valid UTF-8) must be read as cp1252, not as UTF-8 with 40 replacements."""
    text = (
        "id,name\n"
        + "".join(f"{i},Zoé\n" for i in range(40))
        + "".join(f"{i},CafÃ©\n" for i in range(20))
    )
    staged = stage("a2.csv", text.encode("cp1252"))
    _, meta = read_arrow(
        client.post("/api/datasets/preview", json={"stagedId": staged["stagedId"]}).content
    )
    assert meta["detected"]["encoding"] == "cp1252"
    job = import_job(client, stagedId=staged["stagedId"])
    rows = client.post(f"/api/datasets/{job.result.dataset_id}/rows", json={"limit": 100})
    names = read_arrow(rows.content)[0].column("name").to_pylist()
    assert names[0] == "Zoé" and names[-1] == "CafÃ©" and "�" not in "".join(names)


def test_threshold_is_a_small_share_of_characters(tmp_path: Path) -> None:
    path = tmp_path / "t.csv"

    def picked(content: bytes) -> str | None:
        path.write_bytes(content)
        return choose_encoding(path, SourceKind.CSV, ImportOptions()).transcode_from

    good = "".join(f"{i},Zürich Müller\n" for i in range(100)).encode()  # 300 multi-byte chars
    assert picked(good + b"x,\xe9\n") == "utf-8"  # 1 bad of 300: replace it
    assert picked(good + b"x,\xe9\n" * 20) == "cp1252"  # 20 of 300 (6.7%) is too many
    assert importer.MAX_INVALID_SHARE == 0.02


def test_utf16_with_bom_gives_no_encoding_warning(client: TestClient, stage: Stage) -> None:
    staged = stage("u16.csv", "id,name\n1,Zürich\n2,北京\n".encode("utf-16"))
    table, meta = read_arrow(
        client.post("/api/datasets/preview", json={"stagedId": staged["stagedId"]}).content
    )
    assert table.column("name").to_pylist() == ["Zürich", "北京"]
    assert meta["warnings"] == []
    job = import_job(client, stagedId=staged["stagedId"])
    assert job.status == "succeeded" and job.result.warnings == []


def test_replaced_warning_grammar() -> None:
    assert replaced_warning(1).startswith("1 byte that isn't valid UTF-8 was replaced")
    assert replaced_warning(3).startswith("3 bytes that aren't valid UTF-8 were replaced")


def test_upload_overflow_from_fields_isnt_called_a_large_file(client: TestClient) -> None:
    client.app.state.settings.max_upload_mb = 1  # type: ignore[attr-defined]
    boundary = b"Bnd77"
    fields = b"".join(
        b"--"
        + boundary
        + b'\r\nContent-Disposition: form-data; name="f'
        + str(i).encode()
        + b'"\r\n\r\n'
        + b"v" * 60000
        + b"\r\n"
        for i in range(30)
    )
    body = fields + b"--" + boundary + b"--\r\n"

    def chunks() -> Any:
        for i in range(0, len(body), 65536):
            yield body[i : i + 65536]

    response = client.post(
        "/api/datasets/staged",
        content=chunks(),
        headers={"content-type": "multipart/form-data; boundary=Bnd77"},
    )
    assert response.status_code == 413
    assert "file is larger" not in response.json()["detail"]
    assert "in total" in response.json()["detail"]


def test_delete_staged_while_an_import_reads_it_is_409(client: TestClient, stage: Stage) -> None:
    staged = stage("busy.csv", b"a\n1\n")
    with using_staged(staged["stagedId"]):  # what a running import/refresh job holds
        response = client.delete(f"/api/datasets/staged/{staged['stagedId']}")
        assert response.status_code == 409 and "still reading" in response.json()["detail"]
    assert client.delete(f"/api/datasets/staged/{staged['stagedId']}").status_code == 204


def test_running_import_marks_its_upload_in_use(
    client: TestClient, stage: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = stage("slow.csv", b"a\n1\n2\n")
    entered, release = threading.Event(), threading.Event()
    real = importer.write_version

    def slow_write(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr("viz.ingest.jobs.write_version", slow_write)
    response = client.post("/api/datasets/import", json={"stagedId": staged["stagedId"]})
    assert entered.wait(10)
    assert client.delete(f"/api/datasets/staged/{staged['stagedId']}").status_code == 409
    release.set()
    job = client.app.state.jobs.wait(response.json()["id"], 60)  # type: ignore[attr-defined]
    assert job.status == "succeeded"
    assert client.delete(f"/api/datasets/staged/{staged['stagedId']}").status_code == 204


def test_vanished_upload_is_reported_as_deleted(
    client: TestClient, stage: Stage, monkeypatch: pytest.MonkeyPatch
) -> None:
    staged = stage("gone.csv", b"a\n1\n")
    real = importer.choose_encoding

    def vanish(path: Path, *args: Any, **kwargs: Any) -> Any:
        path.unlink()  # someone deleted it right after the job started
        return real(path, *args, **kwargs)

    monkeypatch.setattr(importer, "choose_encoding", vanish)
    job = import_job(client, stagedId=staged["stagedId"])
    assert job.status == "failed" and "was deleted" in (job.error or "")


# -- qa: unsupported text files -------------------------------------------------------------

PROSE = b"Meeting notes for Tuesday\nWe talked about the roadmap and the budget.\n"


def test_unknown_extension_is_415_listing_supported_kinds(client: TestClient) -> None:
    response = client.post("/api/datasets/staged", files={"file": ("notes.txt", PROSE)})
    assert response.status_code == 415
    detail = response.json()["detail"]
    assert ".csv" in detail and ".xlsx" in detail and ".json" in detail
    assert client.app.state.storage.staged_dir.exists()  # type: ignore[attr-defined]
    assert list(client.app.state.storage.staged_dir.iterdir()) == []  # type: ignore[attr-defined]


def test_unknown_extension_with_an_explicit_kind_is_accepted(client: TestClient) -> None:
    response = client.post(
        "/api/datasets/staged?kind=csv", files={"file": ("export.txt", b"a;b\n1;2\n")}
    )
    assert response.status_code == 201, response.text
    staged = response.json()
    assert staged["kind"] == "csv"
    job = import_job(client, stagedId=staged["stagedId"], kind="csv")
    assert job.status == "succeeded"
    dataset = client.get(f"/api/datasets/{job.result.dataset_id}").json()
    assert [c["name"] for c in dataset["versions"][0]["columns"]] == ["a", "b"]


def test_prose_read_as_csv_warns_one_column_no_rows(client: TestClient, stage: Stage) -> None:
    staged = stage("notes.csv", b"Meeting notes for Tuesday\n")
    _, meta = read_arrow(
        client.post("/api/datasets/preview", json={"stagedId": staged["stagedId"]}).content
    )
    assert any("one column with no rows" in w for w in meta["warnings"])
    job = import_job(client, stagedId=staged["stagedId"])
    assert job.status == "succeeded"
    assert any("one column with no rows" in w for w in job.result.warnings)
