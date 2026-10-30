"""Staging, preview, import jobs and refresh (MVP §1)."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
import xlsxwriter
from fastapi.testclient import TestClient

from viz.contracts import ARROW_META_KEY

Stage = Callable[[str, bytes], dict[str, Any]]
RunImport = Callable[..., dict[str, Any]]
Write = Callable[[str, bytes | str], Path]


def read_arrow(content: bytes) -> tuple[pa.Table, dict[str, Any]]:
    table = pa.ipc.open_stream(content).read_all()
    return table, json.loads(table.schema.metadata[ARROW_META_KEY.encode()])


def preview(client: TestClient, **body: Any) -> tuple[pa.Table, dict[str, Any]]:
    response = client.post("/api/datasets/preview", json=body)
    assert response.status_code == 200, response.text
    return read_arrow(response.content)


def rows(client: TestClient, dataset_id: str, **body: Any) -> tuple[pa.Table, dict[str, Any]]:
    response = client.post(f"/api/datasets/{dataset_id}/rows", json=body)
    assert response.status_code == 200, response.text
    return read_arrow(response.content)


def workbook(sheets: dict[str, list[list[object]]]) -> bytes:
    buffer = io.BytesIO()
    book = xlsxwriter.Workbook(buffer, {"in_memory": True})
    for name, data in sheets.items():
        sheet = book.add_worksheet(name)
        for r, row in enumerate(data):
            sheet.write_row(r, 0, row)
    book.close()
    return buffer.getvalue()


# -- CSV ----------------------------------------------------------------------------------------


def test_preview_detects_and_import_honours_options(
    client: TestClient, stage: Stage, run_import: RunImport
) -> None:
    content = "city;price;when\nPune;1,5;2024-01-02\nOslo;NA;2024-02-03\n".encode()
    staged = stage("prices.csv", content)
    assert staged["kind"] == "csv" and staged["sheets"] == []

    table, meta = preview(client, stagedId=staged["stagedId"])
    assert meta["detected"]["delimiter"] == ";"
    assert meta["detected"]["header"] is True
    assert meta["detected"]["encoding"] == "utf-8"
    assert meta["rowsShown"] == 2

    options = {"decimalSeparator": ",", "nullStrings": ["NA"]}
    table, meta = preview(client, stagedId=staged["stagedId"], options=options)
    assert table.column("price").to_pylist() == [1.5, None]
    assert {c["name"]: c["semantic"] for c in meta["columns"]}["price"] == "quantitative"

    dataset = run_import(stagedId=staged["stagedId"], name="Prices", options=options)
    assert dataset["name"] == "Prices"
    version = dataset["versions"][0]
    assert version["rowCount"] == 2
    assert version["options"]["decimalSeparator"] == ","
    assert version["sourcePath"].startswith("sources/")
    assert version["sizeBytes"] > 0
    page, _ = rows(client, dataset["id"])
    assert page.column("price").to_pylist() == [1.5, None]


def test_preview_is_capped_and_estimates_rows(
    client: TestClient, stage: Stage
) -> None:
    lines = ["a,b"] + [f"{i},row{i:05d}" for i in range(5000)]
    staged = stage("many.csv", ("\n".join(lines) + "\n").encode())
    table, meta = preview(client, stagedId=staged["stagedId"])
    assert table.num_rows == 1000 and meta["rowsShown"] == 1000
    assert 4500 <= meta["estimatedRows"] <= 5500
    table, _ = preview(client, stagedId=staged["stagedId"], limit=10)
    assert table.num_rows == 10


def test_no_header_skip_rows_and_column_types(
    client: TestClient, stage: Stage, run_import: RunImport
) -> None:
    content = b"exported by tool\n1,x\n2,y\n"
    staged = stage("raw.csv", content)
    options = {"skipRows": 1, "header": False, "delimiter": ","}
    table, _ = preview(client, stagedId=staged["stagedId"], options=options)
    assert table.num_rows == 2
    dataset = run_import(
        stagedId=staged["stagedId"],
        options={**options, "columnTypes": {"column0": "VARCHAR"}},
    )
    types = {c["name"]: c["type"] for c in dataset["versions"][0]["columns"]}
    assert types["column0"] == "VARCHAR"


def test_bad_options_give_readable_422(client: TestClient, stage: Stage) -> None:
    staged = stage("t.csv", b"a,b\n1,2\n")
    response = client.post(
        "/api/datasets/preview",
        json={"stagedId": staged["stagedId"], "options": {"columnTypes": {"a": "GEOMETRY"}}},
    )
    assert response.status_code == 422
    assert "isn't supported" in response.json()["detail"]
    response = client.post(
        "/api/datasets/preview",
        json={"stagedId": staged["stagedId"], "options": {"columnTypes": {"a": "DATE"}}},
    )
    assert response.status_code == 422
    assert "couldn't read the file" in response.json()["detail"]


def test_non_utf8_is_detected_and_transcoded(
    client: TestClient, stage: Stage, run_import: RunImport
) -> None:
    content = "name,city\nJosé,Zürich\nRenée,Café\n".encode("cp1252")
    staged = stage("legacy.csv", content)
    table, meta = preview(client, stagedId=staged["stagedId"])
    assert table.column("name").to_pylist() == ["José", "Renée"]
    assert meta["detected"]["encoding"] == "cp1252"
    assert any("isn't UTF-8" in w for w in meta["warnings"])
    dataset = run_import(stagedId=staged["stagedId"])
    page, _ = rows(client, dataset["id"])
    assert page.column("city").to_pylist() == ["Zürich", "Café"]
    assert dataset["versions"][0]["options"]["encoding"] == "cp1252"


def test_utf8_bom_and_messy_headers(client: TestClient, stage: Stage, run_import: RunImport) -> None:
    content = "﻿ id , ,name\n1,2,a\n".encode()
    dataset = run_import(stagedId=stage("bom.csv", content)["stagedId"])
    names = [c["name"] for c in dataset["versions"][0]["columns"]]
    assert names[0] == "id" and names[2] == "name" and names[1]


def test_type_sniffing_rejects_mismatched_files(client: TestClient) -> None:
    binary = b"\x00\x01\x02garbage" * 10
    response = client.post("/api/datasets/staged", files={"file": ("x.csv", binary)})
    assert response.status_code == 415
    response = client.post("/api/datasets/staged", files={"file": ("x.json", b"hello")})
    assert response.status_code == 415
    response = client.post("/api/datasets/staged", files={"file": ("x.xlsx", b"a,b\n1,2\n")})
    assert response.status_code == 415
    response = client.post("/api/datasets/preview", json={"stagedId": "0123456789abcdef"})
    assert response.status_code == 404
    response = client.post("/api/datasets/preview", json={})
    assert response.status_code == 422


# -- JSON ---------------------------------------------------------------------------------------


def test_json_array_ndjson_and_path(client: TestClient, stage: Stage, run_import: RunImport) -> None:
    array = json.dumps([{"a": 1, "b": "x"}, {"a": 2, "b": None}]).encode()
    staged = stage("arr.json", array)
    _, meta = preview(client, stagedId=staged["stagedId"])
    assert meta["detected"]["jsonFormat"] == "array"
    assert run_import(stagedId=staged["stagedId"])["versions"][0]["rowCount"] == 2

    ndjson = b'{"a": 1}\n{"a": 2}\n{"a": 3}\n'
    staged = stage("lines.ndjson", ndjson)
    _, meta = preview(client, stagedId=staged["stagedId"])
    assert meta["detected"]["jsonFormat"] == "newline_delimited"
    dataset = run_import(stagedId=staged["stagedId"], options={"jsonFormat": "newline_delimited"})
    assert dataset["versions"][0]["rowCount"] == 3

    nested = json.dumps({"meta": {"v": 1}, "data": {"items": [{"k": "p"}, {"k": "q"}]}}).encode()
    staged = stage("nested.json", nested)
    _, meta = preview(client, stagedId=staged["stagedId"])
    table, meta = preview(client, stagedId=staged["stagedId"], options={"jsonPath": "data.items"})
    assert table.column("k").to_pylist() == ["p", "q"]
    response = client.post(
        "/api/datasets/preview",
        json={"stagedId": staged["stagedId"], "options": {"jsonPath": "nope"}},
    )
    assert response.status_code == 422 and "not found" in response.json()["detail"]

    listed = json.dumps({"rows": [{"k": 1}, {"k": 2}]}).encode()
    _, meta = preview(client, stagedId=stage("obj.json", listed)["stagedId"])
    assert any("JSON path" in w for w in meta["warnings"])


# -- Excel --------------------------------------------------------------------------------------


def test_excel_sheets_selection_and_header_row(
    client: TestClient, stage: Stage, run_import: RunImport
) -> None:
    content = workbook(
        {
            "Summary": [["title only"]],
            "Sales Data": [["report"], ["region", "amount"], ["North", 10.5], ["South", 3]],
        }
    )
    staged = stage("book.xlsx", content)
    assert staged["kind"] == "excel"
    assert staged["sheets"] == ["Summary", "Sales Data"]

    _, meta = preview(client, stagedId=staged["stagedId"])
    assert meta["detected"]["sheet"] == "Summary"
    assert meta["sheets"] == ["Summary", "Sales Data"]

    options = {"sheet": "Sales Data", "headerRow": 1}
    table, meta = preview(client, stagedId=staged["stagedId"], options=options)
    assert table.column_names == ["region", "amount"]
    assert meta["estimatedRows"] == 2

    response = client.post(
        "/api/datasets/preview",
        json={"stagedId": staged["stagedId"], "options": {"sheet": "Missing"}},
    )
    assert response.status_code == 422 and "Sales Data" in response.json()["detail"]

    dataset = run_import(stagedId=staged["stagedId"], options=options)
    assert dataset["name"] == "book - Sales Data"
    assert dataset["source"]["kind"] == "excel"
    page, _ = rows(client, dataset["id"])
    assert page.column("amount").to_pylist() == [10.5, 3.0]



# -- path imports, refresh ------------------------------------------------------------------------


def test_path_import_copies_small_files_and_reads_large_in_place(
    client: TestClient, write: Write, run_import: RunImport
) -> None:
    path = write("dåta fïle.csv", "a,b\n1,2\n")
    dataset = run_import(path=str(path))
    assert dataset["source"]["originalPath"] == str(path.resolve())
    assert dataset["versions"][0]["sourcePath"].startswith("sources/")

    client.app.state.settings.copy_source_max_mb = 0  # type: ignore[attr-defined]
    in_place = run_import(path=str(path))
    assert in_place["versions"][0]["sourcePath"] is None


def test_refresh_from_path_adds_version_and_reports_schema_changes(
    client: TestClient, write: Write, run_import: RunImport
) -> None:
    path = write("live.csv", "a,b\n1,2\n")
    dataset = run_import(path=str(path))
    v1 = dataset["versions"][0]["parquetPath"]
    client.patch(f"/api/datasets/{dataset['id']}", json={"name": "Renamed"})

    path.write_text("a,c\n1,x\n2,y\n3,z\n", encoding="utf-8")
    response = client.post(f"/api/datasets/{dataset['id']}/refresh", json={})
    assert response.status_code == 202, response.text
    job = client.app.state.jobs.wait(response.json()["id"], 60)  # type: ignore[attr-defined]
    assert job.status == "succeeded", job.error
    assert job.result.version == 2
    assert any("removed: b" in w for w in job.result.warnings)
    assert any("added: c" in w for w in job.result.warnings)

    refreshed = client.get(f"/api/datasets/{dataset['id']}").json()
    assert refreshed["name"] == "Renamed"
    assert [v["n"] for v in refreshed["versions"]] == [1, 2]
    assert refreshed["versions"][1]["rowCount"] == 3
    # v1 is untouched and still readable.
    data_dir = client.app.state.storage.root  # type: ignore[attr-defined]
    assert (data_dir / v1).is_file()
    page, meta = rows(client, dataset["id"], version=1)
    assert page.num_rows == 1 and meta["version"] == 1


def test_refresh_by_reupload(client: TestClient, stage: Stage, run_import: RunImport) -> None:
    dataset = run_import(stagedId=stage("up.csv", b"a\n1\n")["stagedId"])
    response = client.post(f"/api/datasets/{dataset['id']}/refresh", json={})
    assert response.status_code == 409
    staged = stage("up2.csv", b"a\n1\n2\n")
    response = client.post(
        f"/api/datasets/{dataset['id']}/refresh", json={"stagedId": staged["stagedId"]}
    )
    assert response.status_code == 202
    job = client.app.state.jobs.wait(response.json()["id"], 60)  # type: ignore[attr-defined]
    assert job.status == "succeeded", job.error
    refreshed = client.get(f"/api/datasets/{dataset['id']}").json()
    assert refreshed["versions"][-1]["rowCount"] == 2
    assert refreshed["source"]["fileName"] == "up2.csv"


def test_locked_file_gives_close_excel_message(
    client: TestClient, write: Write, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write("locked.csv", "a\n1\n")
    real_open = Path.open

    def fake_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == "locked.csv" and self.resolve() == path.resolve():
            raise PermissionError(13, "locked")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    response = client.post("/api/datasets/import", json={"path": str(path)})
    assert response.status_code == 409
    assert "close it" in response.json()["detail"]


def test_path_import_can_be_disabled(client: TestClient, write: Write) -> None:
    client.app.state.settings.allow_path_import = False  # type: ignore[attr-defined]
    response = client.post("/api/datasets/import", json={"path": str(write("x.csv", "a\n1\n"))})
    assert response.status_code == 422


# -- rename, delete, bundle --------------------------------------------------------------------------


def test_rename_and_delete(client: TestClient, stage: Stage, run_import: RunImport) -> None:
    dataset = run_import(stagedId=stage("d.csv", b"a\n1\n")["stagedId"])
    response = client.patch(f"/api/datasets/{dataset['id']}", json={"name": "  New name "})
    assert response.json()["name"] == "New name"
    assert client.patch(f"/api/datasets/{dataset['id']}", json={"name": " "}).status_code == 422

    project = client.post("/api/projects", json={"name": "P"}).json()
    project["datasetIds"] = [dataset["id"]]
    saved = client.put(f"/api/projects/{project['id']}", json=project)
    assert saved.status_code == 200, saved.text
    response = client.delete(f"/api/datasets/{dataset['id']}")
    assert response.status_code == 409 and "P" in response.json()["detail"]

    root = client.app.state.storage.root  # type: ignore[attr-defined]
    assert (root / "datasets" / dataset["id"]).is_dir()
    assert client.delete(f"/api/datasets/{dataset['id']}?force=true").status_code == 204
    assert client.get(f"/api/datasets/{dataset['id']}").status_code == 404
    assert not (root / "datasets" / dataset["id"]).exists()
    assert not (root / "sources" / dataset["id"]).exists()


def test_register_dataset_bundle(client: TestClient, stage: Stage, run_import: RunImport) -> None:
    from viz.contracts import Dataset
    from viz.ingest import catalog

    dataset = Dataset.model_validate(run_import(stagedId=stage("b.csv", b"a\n1\n")["stagedId"]))
    root = client.app.state.storage.root  # type: ignore[attr-defined]
    parquet = root / dataset.current().parquet_path
    with client.app.state.sessionmaker() as db:  # type: ignore[attr-defined]
        copy = catalog.register_dataset_bundle(
            db, client.app.state.storage, dataset, {1: parquet}, new_id="0" * 8 + "-copy"
        )
    assert copy.id == "0" * 8 + "-copy"
    assert copy.current().parquet_path == f"datasets/{copy.id}/v1.parquet"
    page, _ = rows(client, copy.id)
    assert page.column("a").to_pylist() == [1]
