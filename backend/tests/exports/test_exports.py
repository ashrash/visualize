"""CSV and Excel exports (MVP §9)."""

from __future__ import annotations

import csv
import io
from typing import Any

import fastexcel
import pytest
from fastapi.testclient import TestClient

from viz.exports import service


def export(client: TestClient, dataset_id: str, **body: Any) -> dict[str, Any]:
    payload = {"source": {"kind": "dataset", "id": dataset_id}, **body}
    response = client.post("/api/exports", json=payload)
    assert response.status_code == 202, response.text
    job = client.app.state.jobs.wait(response.json()["id"], 60)  # type: ignore[attr-defined]
    assert job.status == "succeeded", job.error
    return dict(job.model_dump(mode="json"))


def test_csv_export_with_filters_sort_and_columns(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    job = export(
        client,
        sales_dataset["id"],
        format="csv",
        filters=[{"op": "eq", "field": "region", "value": "North"}],
        sort=[{"field": "sales", "descending": True}],
        columns=["product", "sales"],
        fileName="north sales",
    )
    file = job["result"]["export"]
    assert file["fileName"] == "north sales.csv"
    assert file["rowCount"] == 3 and file["format"] == "csv"
    assert file["downloadUrl"] == f"/api/exports/{file['exportId']}/download"
    response = client.get(file["downloadUrl"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "north%20sales.csv" in response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows == [
        ["product", "sales"],
        ["Gadget", "200.0"],
        ["Widget", "120.5"],
        ["Gizmo", "10.0"],
    ]

    job = export(client, sales_dataset["id"], format="csv", delimiter=";")
    text = client.get(job["result"]["export"]["downloadUrl"]).text
    assert text.splitlines()[0] == "region;product;sales;units;order_date"
    assert job["result"]["export"]["fileName"] == "sales.csv"


def test_xlsx_export_reads_back_with_types(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    job = export(client, sales_dataset["id"], format="xlsx")
    file = job["result"]["export"]
    assert file["fileName"] == "sales.xlsx" and file["rowCount"] == 6
    content = client.get(file["downloadUrl"]).content
    sheet = fastexcel.read_excel(content).load_sheet(0)
    table = sheet.to_arrow()
    assert table.column_names == ["region", "product", "sales", "units", "order_date"]
    assert table.column("sales").to_pylist()[:4] == [120.5, 80.0, 200.0, None]
    assert str(table.column("order_date").to_pylist()[0]).startswith("2024-01-05")
    assert job["result"]["warnings"] == []


def test_xlsx_never_writes_formulas(client: TestClient, stage_csv: Any) -> None:
    dataset = stage_csv("evil.csv", b'name\n"=HYPERLINK(""http://x"")"\n')
    job = export(client, dataset["id"], format="xlsx")
    content = client.get(job["result"]["export"]["downloadUrl"]).content
    import zipfile

    sheet_xml = zipfile.ZipFile(io.BytesIO(content)).read("xl/worksheets/sheet1.xml").decode()
    assert "<f>" not in sheet_xml


def test_xlsx_row_limit_truncates_with_warning(
    client: TestClient, sales_dataset: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(service, "EXCEL_MAX_DATA_ROWS", 4)
    job = export(client, sales_dataset["id"], format="xlsx")
    assert job["result"]["export"]["rowCount"] == 4
    assert "first 4 of 6 rows" in job["result"]["warnings"][0]
    content = client.get(job["result"]["export"]["downloadUrl"]).content
    assert fastexcel.read_excel(content).load_sheet(0).height == 4


def test_errors(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    response = client.post(
        "/api/exports", json={"source": {"kind": "pipeline", "id": "p"}, "format": "csv"}
    )
    assert response.status_code == 501
    response = client.post(
        "/api/exports", json={"source": {"kind": "dataset", "id": "missing"}, "format": "csv"}
    )
    assert response.status_code == 404
    response = client.post(
        "/api/exports",
        json={
            "source": {"kind": "dataset", "id": sales_dataset["id"]},
            "format": "csv",
            "delimiter": ";;",
        },
    )
    assert response.status_code == 422
    assert client.get("/api/exports/0123456789abcdef/download").status_code == 404
    response = client.post(
        "/api/exports",
        json={
            "source": {"kind": "dataset", "id": sales_dataset["id"]},
            "format": "csv",
            "columns": ["nope"],
        },
    )
    job = client.app.state.jobs.wait(response.json()["id"], 60)  # type: ignore[attr-defined]
    assert job.status == "failed" and "nope" in (job.error or "")


def test_expired_exports_are_swept(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    job = export(client, sales_dataset["id"], format="csv")
    storage = client.app.state.storage  # type: ignore[attr-defined]
    assert storage.sweep(staged_max_age_s=1e9, exports_max_age_s=-1) >= 1
    assert client.get(job["result"]["export"]["downloadUrl"]).status_code == 404


@pytest.fixture
def stage_csv(client: TestClient) -> Any:
    def _stage(name: str, content: bytes) -> dict[str, Any]:
        response = client.post("/api/datasets/upload", files={"file": (name, content)})
        assert response.status_code == 201, response.text
        return dict(response.json())

    return _stage
