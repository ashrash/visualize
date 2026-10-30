from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
from fastapi.testclient import TestClient

from viz.contracts import ARROW_META_KEY, ARROW_STREAM_MEDIA_TYPE


def _read_arrow(content: bytes) -> tuple[pa.Table, dict[str, object]]:
    table = pa.ipc.open_stream(content).read_all()
    meta = json.loads(table.schema.metadata[ARROW_META_KEY.encode()])
    return table, meta


def test_upload_csv_profiles_columns(sales_dataset: dict[str, object]) -> None:
    version = sales_dataset["versions"][0]  # type: ignore[index]
    assert version["rowCount"] == 6
    columns = {c["name"]: c for c in version["columns"]}
    assert columns["sales"]["semantic"] == "quantitative"
    assert columns["sales"]["nullCount"] == 1
    assert columns["order_date"]["semantic"] == "temporal"
    assert columns["region"]["semantic"] == "nominal"
    assert columns["region"]["approxDistinct"] == 3


def test_rows_are_paged_as_arrow(client: TestClient, sales_dataset: dict[str, object]) -> None:
    response = client.get(f"/api/datasets/{sales_dataset['id']}/rows?offset=2&limit=3")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(ARROW_STREAM_MEDIA_TYPE)
    table, meta = _read_arrow(response.content)
    assert table.num_rows == 3
    assert table.column("region").to_pylist() == ["North", "East", "South"]
    assert {k: meta[k] for k in ("offset", "limit", "totalRows")} == {
        "offset": 2,
        "limit": 3,
        "totalRows": 6,
    }


def test_import_from_path_handles_awkward_names(client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "my data — ünïcode"
    folder.mkdir()
    path = folder / "odd file.csv"
    path.write_text('"we""ird col",Ümlaut\n1,a\n2,b\n', encoding="utf-8")
    response = client.post("/api/datasets/import-path", json={"path": str(path)})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source"]["originalPath"] == str(path.resolve())
    assert [c["name"] for c in body["versions"][0]["columns"]] == ['we"ird col', "Ümlaut"]
    assert client.get("/api/datasets").json()[0]["id"] == body["id"]


def test_json_and_tsv_imports(client: TestClient) -> None:
    records = b'[{"city": "Pune", "temp": 31.5}, {"city": "Oslo", "temp": null}]'
    response = client.post(
        "/api/datasets/upload", files={"file": ("weather.json", records, "application/json")}
    )
    assert response.status_code == 201, response.text
    version = response.json()["versions"][0]
    assert version["rowCount"] == 2
    assert {c["name"]: c["nullCount"] for c in version["columns"]} == {"city": 0, "temp": 1}

    tsv = b"a\tb\n1\tx\n2\ty\n"
    response = client.post(
        "/api/datasets/upload", files={"file": ("pairs.tsv", tsv, "text/tab-separated-values")}
    )
    assert response.status_code == 201, response.text
    assert [c["name"] for c in response.json()["versions"][0]["columns"]] == ["a", "b"]


def test_unsupported_and_missing_files(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/datasets/upload", files={"file": ("notes.docx", b"x", "application/octet-stream")}
    )
    assert response.status_code == 415
    response = client.post("/api/datasets/import-path", json={"path": str(tmp_path / "nope.csv")})
    assert response.status_code == 404
    assert client.get("/api/datasets/does-not-exist").status_code == 404
