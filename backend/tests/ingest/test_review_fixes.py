"""Review fixes in ingest: ties, views, rollback, staging, uploads, encodings, references."""

from __future__ import annotations

import collections
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pytest
import xlsxwriter
from fastapi.testclient import TestClient

from viz.contracts import ARROW_META_KEY, Dataset, ImportOptions
from viz.errors import ConflictError
from viz.ingest import catalog, readers

Stage = Callable[[str, bytes], dict[str, Any]]
RunImport = Callable[..., dict[str, Any]]
Write = Callable[[str, bytes | str], Path]


def read_arrow(content: bytes) -> tuple[pa.Table, dict[str, Any]]:
    table = pa.ipc.open_stream(content).read_all()
    return table, json.loads(table.schema.metadata[ARROW_META_KEY.encode()])


def rows(client: TestClient, dataset_id: str, **body: Any) -> tuple[pa.Table, dict[str, Any]]:
    response = client.post(f"/api/datasets/{dataset_id}/rows", json=body)
    assert response.status_code == 200, response.text
    return read_arrow(response.content)


def import_job(client: TestClient, **body: Any) -> Any:
    response = client.post("/api/datasets/import", json=body)
    assert response.status_code == 202, response.text
    return client.app.state.jobs.wait(response.json()["id"], 120)  # type: ignore[attr-defined]


# -- B3: deterministic sorted pages --------------------


def test_sorting_by_a_low_cardinality_column_pages_every_row_once(
    client: TestClient, write: Write
) -> None:
    path = write("ties.csv", "")
    duckdb.sql(
        f"COPY (SELECT i AS id, i % 3 AS g FROM range(60000) t(i)) TO '{path.as_posix()}' (HEADER)"
    )
    job = import_job(client, path=str(path))
    ds = job.result.dataset_id
    for sort in ([{"field": "g"}], [{"field": "g", "descending": True}]):
        seen: collections.Counter[int] = collections.Counter()
        last_g: int | None = None
        for offset in range(0, 60000, 1000):
            table, _ = rows(client, ds, offset=offset, limit=1000, sort=sort)
            assert "file_row_number" not in table.column_names
            seen.update(table.column("id").to_pylist())
            last_g = table.column("g").to_pylist()[-1]
        assert len(seen) == 60000 and max(seen.values()) == 1, sort
        assert last_g is not None


def test_views_use_the_same_tiebreak(client: TestClient, write: Write) -> None:
    path = write("ties2.csv", "")
    duckdb.sql(
        f"COPY (SELECT i AS id, i % 2 AS g FROM range(5000) t(i)) TO '{path.as_posix()}' (HEADER)"
    )
    ds = import_job(client, path=str(path)).result.dataset_id
    views = client.app.state.row_views  # type: ignore[attr-defined]
    views.min_rows = 0
    body = {"sort": [{"field": "g"}], "offset": 2000, "limit": 500}
    direct, _ = rows(client, ds, **body)
    views.wait_idle()
    served, _ = rows(client, ds, **body)
    assert served.column("id").to_pylist() == direct.column("id").to_pylist()


# -- views are bounded --------------------


def test_views_build_only_past_the_first_page_and_supersede(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    ds = sales_dataset["id"]
    views = client.app.state.row_views  # type: ignore[attr-defined]
    views.min_rows = 0
    rows(client, ds, sort=[{"field": "sales"}])
    views.wait_idle()
    assert list(views.directory.glob("*.parquet")) == []  # first page: no build
    rows(client, ds, sort=[{"field": "sales"}], offset=2)
    rows(client, ds, sort=[{"field": "units"}], offset=2)  # supersedes the queued/running one
    views.wait_idle()
    files = list(views.directory.glob("*.parquet"))
    assert len(files) <= 2
    rows(client, ds, sort=[{"field": "units"}], offset=2)
    assert views.total_bytes > 0


def test_views_respect_the_byte_budget(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    ds = sales_dataset["id"]
    views = client.app.state.row_views  # type: ignore[attr-defined]
    views.min_rows = 0
    rows(client, ds, sort=[{"field": "sales"}], offset=1)
    views.wait_idle()
    one = views.total_bytes
    assert one > 0
    views.max_bytes = int(one * 1.5)
    rows(client, ds, sort=[{"field": "units"}], offset=1)
    views.wait_idle()
    rows(client, ds, sort=[{"field": "region"}], offset=1)
    views.wait_idle()
    assert views.total_bytes <= views.max_bytes
    assert len(list(views.directory.glob("*.parquet"))) == 1


# -- failed imports leave nothing behind --------------------


def _orphans(client: TestClient) -> list[str]:
    storage = client.app.state.storage  # type: ignore[attr-defined]
    known = {d["id"] for d in client.get("/api/datasets").json()}
    found = []
    for parent in (storage.sources_dir, storage.datasets_dir):
        found += [p.name for p in parent.iterdir() if p.name != "_staged" and p.name not in known]
    return found


def test_failed_import_keeps_the_upload_and_removes_files(client: TestClient, stage: Stage) -> None:
    staged = stage("bad.csv", b"n\n1\n2\nthree\n")
    job = import_job(client, stagedId=staged["stagedId"], options={"columnTypes": {"n": "INTEGER"}})
    assert job.status == "failed"
    storage = client.app.state.storage  # type: ignore[attr-defined]
    assert storage.find_staged(staged["stagedId"]) is not None  # can retry with new options
    assert _orphans(client) == []
    # Retrying with working options succeeds and then removes the staged upload.
    job = import_job(client, stagedId=staged["stagedId"])
    assert job.status == "succeeded"
    assert storage.find_staged(staged["stagedId"]) is None


def test_failed_refresh_leaves_only_the_old_version(
    client: TestClient, write: Write, run_import: RunImport
) -> None:
    path = write("r.csv", "n\n1\n")
    dataset = run_import(path=str(path), options={"columnTypes": {"n": "INTEGER"}})
    path.write_text("n\n1\nthree\n", encoding="utf-8")
    response = client.post(f"/api/datasets/{dataset['id']}/refresh", json={})
    job = client.app.state.jobs.wait(response.json()["id"], 60)  # type: ignore[attr-defined]
    assert job.status == "failed"
    root = client.app.state.storage.root  # type: ignore[attr-defined]
    assert sorted(p.name for p in (root / "datasets" / dataset["id"]).iterdir()) == ["v1.parquet"]
    assert len(list((root / "sources" / dataset["id"]).iterdir())) == 1


def test_startup_sweep_removes_orphaned_directories(client: TestClient) -> None:
    storage = client.app.state.storage  # type: ignore[attr-defined]
    orphan = storage.datasets_dir / "0000dead-beef"
    orphan.mkdir()
    (orphan / "v1.parquet").write_bytes(b"x")
    (storage.sources_dir / "0000dead-beef").mkdir()
    with client.app.state.sessionmaker() as db:  # type: ignore[attr-defined]
        removed = catalog.sweep_orphans(db, storage)
    assert len(removed) == 2 and not orphan.exists()


# -- staging, uploads, expiry --------------------


def test_malformed_staged_ids_are_404(client: TestClient) -> None:
    for body in ({"stagedId": "../../etc"}, {"stagedId": "zzzz"}):
        assert client.post("/api/datasets/preview", json=body).status_code == 404
        assert client.post("/api/datasets/import", json=body).status_code == 404
    assert client.delete("/api/datasets/staged/zzzz").status_code == 404


def test_upload_size_limit_is_checked_before_and_while_streaming(client: TestClient) -> None:
    settings = client.app.state.settings  # type: ignore[attr-defined]
    settings.max_upload_mb = 1
    big = b"a\n" + b"1\n" * (700 * 1024)  # ~1.4 MB
    response = client.post("/api/datasets/staged", files={"file": ("big.csv", big)})
    assert response.status_code == 413
    staged_root = client.app.state.storage.staged_dir  # type: ignore[attr-defined]
    assert list(staged_root.iterdir()) == []

    # Without a Content-Length (chunked), the streaming counter still stops it.
    boundary = "XyZ"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="big.csv"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()

    def chunks() -> Any:
        yield head
        for _ in range(30):
            yield b"1\n" * 32 * 1024
        yield tail

    response = client.post(
        "/api/datasets/staged",
        content=chunks(),
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )
    assert response.status_code == 413
    assert list(staged_root.iterdir()) == []


def test_streamed_upload_keeps_name_and_fields(client: TestClient) -> None:
    response = client.post(
        "/api/datasets/staged", files={"file": ("sales [2024] ü.csv", b"a,b\n1,2\n")}
    )
    assert response.status_code == 201, response.text
    assert response.json()["fileName"] == "sales [2024] ü.csv"
    response = client.post(
        "/api/datasets/upload",
        files={"file": ("semi.csv", b"a;b\n1;2\n")},
        data={"name": "Semi", "options": json.dumps({"delimiter": ";"})},
    )
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "Semi"
    assert [c["name"] for c in response.json()["versions"][0]["columns"]] == ["a", "b"]
    assert client.post("/api/datasets/staged", content=b"x").status_code == 422
    response = client.post("/api/datasets/staged", files={"file": ("empty.csv", b"")})
    assert response.status_code == 422 and "empty" in response.json()["detail"]


def test_export_downloads_expire(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    response = client.post(
        "/api/exports",
        json={"source": {"kind": "dataset", "id": sales_dataset["id"]}, "format": "csv"},
    )
    job = client.app.state.jobs.wait(response.json()["id"], 60)  # type: ignore[attr-defined]
    url = job.result.export.download_url
    assert client.get(url).status_code == 200
    client.app.state.settings.export_retention_s = -1  # type: ignore[attr-defined]
    assert client.get(url).status_code == 404


# -- encodings, line endings, Excel, JSON --------------------


def test_mixed_line_endings_import(client: TestClient, stage: Stage, run_import: RunImport) -> None:
    content = b"\xef\xbb\xbfid,name\r\n1,J\xc3\xbcrgen\n2,Zo\xc3\xab\r\n3,x\n"
    dataset = run_import(stagedId=stage("eol.csv", content)["stagedId"])
    table, _ = rows(client, dataset["id"])
    assert table.column("name").to_pylist() == ["Jürgen", "Zoë", "x"]


def test_late_non_utf8_byte(client: TestClient, stage: Stage) -> None:
    body = b"id,city\n" + b"".join(b"%d,Paris\n" % i for i in range(20000)) + b"20001,Montr\xe9al\n"
    staged = stage("late.csv", body)
    response = client.post("/api/datasets/preview", json={"stagedId": staged["stagedId"]})
    assert response.status_code == 200
    _, meta = read_arrow(response.content)
    assert any("isn't valid UTF-8" in w for w in meta["warnings"])
    response = client.post(
        "/api/datasets/preview",
        json={"stagedId": staged["stagedId"], "options": {"encoding": "utf-8"}},
    )
    assert response.status_code == 422 and "windows-1252" in response.json()["detail"]
    job = import_job(client, stagedId=staged["stagedId"])
    assert job.status == "succeeded" and job.result.warnings
    table, meta = rows(client, job.result.dataset_id, offset=20000)
    assert table.column("city").to_pylist() == ["Montréal"]


def test_excel_estimate_accounts_for_skip_rows(tmp_path: Path) -> None:
    path = tmp_path / "book.xlsx"
    book = xlsxwriter.Workbook(str(path))
    sheet = book.add_worksheet("S1")
    for r, row in enumerate([["h1", "h2"], [1, 2], [3, 4], [5, 6]]):
        sheet.write_row(r, 0, row)
    book.close()
    _, _, total = readers.read_excel(path, ImportOptions(skip_rows=1))
    assert total == 2


def test_json_hint_finds_nested_lists(client: TestClient, stage: Stage) -> None:
    content = json.dumps({"meta": {"page": 1}, "data": {"items": [{"a": 1}], "n": 1}}).encode()
    response = client.post(
        "/api/datasets/preview", json={"stagedId": stage("n.json", content)["stagedId"]}
    )
    _, meta = read_arrow(response.content)
    assert any("data.items" in w for w in meta["warnings"])


def test_search_is_case_insensitive_on_both_sides(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    for needle in ("gIzMo", "GIZMO", "gizmo"):
        _, meta = rows(client, sales_dataset["id"], search=needle)
        assert meta["totalRows"] == 2


# -- references, delete, bundles --------------------


def test_projects_using_scans_source_refs(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    ds = sales_dataset["id"]
    doc = {
        "datasetIds": [],
        "pipelines": [{"steps": [{"op": "join", "right": {"kind": "dataset", "id": ds}}]}],
    }
    assert catalog.referenced_dataset_ids(doc) == [ds]
    assert (
        catalog.referenced_dataset_ids({"charts": [{"source": {"kind": "pipeline", "id": ds}}]})
        == []
    )


def test_delete_keeps_everything_when_files_are_in_use(
    client: TestClient, sales_dataset: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ds = sales_dataset["id"]
    real_replace = os.replace

    def locked(src: Any, dst: Any) -> None:
        if str(src).endswith(os.path.join("sources", ds)):
            raise PermissionError(32, "in use")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", locked)
    response = client.delete(f"/api/datasets/{ds}?force=true")
    assert response.status_code == 409 and "in use" in response.json()["detail"]
    assert client.get(f"/api/datasets/{ds}").status_code == 200
    root = client.app.state.storage.root  # type: ignore[attr-defined]
    assert (root / "datasets" / ds).is_dir()  # moved back
    monkeypatch.setattr(os, "replace", real_replace)
    assert client.delete(f"/api/datasets/{ds}?force=true").status_code == 204


def test_bundle_move_and_no_overwrite(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    storage = client.app.state.storage  # type: ignore[attr-defined]
    dataset = Dataset.model_validate(sales_dataset)
    src = storage.root / dataset.current().parquet_path
    copy = storage.temp_dir / "bundle.parquet"
    copy.write_bytes(src.read_bytes())
    with client.app.state.sessionmaker() as db:  # type: ignore[attr-defined]
        with pytest.raises(ConflictError):
            catalog.register_dataset_bundle(db, storage, dataset, {1: copy})
        moved = catalog.register_dataset_bundle(
            db, storage, dataset, {1: copy}, new_id="11111111-2222", move=True
        )
    assert not copy.exists()
    assert (storage.root / moved.current().parquet_path).is_file()


def test_xlsx_sheet_names_are_sanitised() -> None:
    from viz.exports.service import excel_sheet_name

    assert excel_sheet_name("Report [Q1]") == "Report  Q1"
    assert excel_sheet_name("'quoted'") == "quoted"
    assert excel_sheet_name("a/b\\c:d*e?f") == "a b c d e f"
    assert excel_sheet_name("x" * 40) == "x" * 31
    assert excel_sheet_name("[]") == "Data"
    assert excel_sheet_name("History") == "Data"
