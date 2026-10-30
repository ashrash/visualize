"""Grid rows (sort, filter, search, columns) and distinct values."""

from __future__ import annotations

import json
from typing import Any

import pyarrow as pa
from fastapi.testclient import TestClient

from viz.contracts import ARROW_META_KEY


def read_arrow(content: bytes) -> tuple[pa.Table, dict[str, Any]]:
    table = pa.ipc.open_stream(content).read_all()
    return table, json.loads(table.schema.metadata[ARROW_META_KEY.encode()])


def rows(client: TestClient, dataset_id: str, **body: Any) -> tuple[pa.Table, dict[str, Any]]:
    response = client.post(f"/api/datasets/{dataset_id}/rows", json=body)
    assert response.status_code == 200, response.text
    return read_arrow(response.content)


def test_sort_with_nulls_last_and_first(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    ds = sales_dataset["id"]
    table, meta = rows(client, ds, sort=[{"field": "sales", "descending": True}])
    assert table.column("sales").to_pylist() == [200.0, 120.5, 80.0, 55.25, 10.0, None]
    assert meta["totalRows"] == 6 and meta["datasetRows"] == 6 and meta["version"] == 1
    table, _ = rows(client, ds, sort=[{"field": "sales", "nullsLast": False}])
    assert table.column("sales").to_pylist()[0] is None
    table, _ = rows(
        client,
        ds,
        sort=[{"field": "region"}, {"field": "units", "descending": True}],
        columns=["region", "units"],
    )
    assert table.column_names == ["region", "units"]
    assert table.to_pylist()[:2] == [
        {"region": "East", "units": 1},
        {"region": "North", "units": 5},
    ]


def test_each_predicate_and_filtered_total(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    ds = sales_dataset["id"]
    cases: list[tuple[dict[str, Any], int]] = [
        ({"op": "in", "field": "region", "values": ["North", "East"]}, 4),
        ({"op": "not_in", "field": "region", "values": ["North"]}, 3),
        ({"op": "eq", "field": "product", "value": "Gizmo"}, 2),
        ({"op": "range", "field": "sales", "min": 50, "max": 120.5}, 3),
        ({"op": "range", "field": "sales", "min": 50, "max": 120.5, "includeMax": False}, 2),
        (
            {
                "op": "between_dates",
                "field": "order_date",
                "min": "2024-02-01",
                "max": "2024-02-28",
            },
            2,
        ),
        ({"op": "is_null", "field": "sales"}, 1),
        ({"op": "is_null", "field": "sales", "isNull": False}, 5),
    ]
    for predicate, expected in cases:
        table, meta = rows(client, ds, filters=[predicate], limit=2)
        assert meta["totalRows"] == expected, predicate
        assert table.num_rows == min(2, expected)
        assert meta["datasetRows"] == 6


def test_search_and_paging(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    ds = sales_dataset["id"]
    table, meta = rows(client, ds, search="GIZ")
    assert meta["totalRows"] == 2
    assert set(table.column("product").to_pylist()) == {"Gizmo"}
    table, meta = rows(client, ds, search="nomatch")
    assert table.num_rows == 0 and meta["totalRows"] == 0
    table, meta = rows(client, ds, offset=4, limit=10)
    assert table.num_rows == 2 and meta["offset"] == 4


def test_unknown_columns_and_versions_are_422_and_404(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    ds = sales_dataset["id"]
    for body in (
        {"sort": [{"field": "nope"}]},
        {"columns": ["nope"]},
        {"filters": [{"op": "eq", "field": "nope", "value": 1}]},
    ):
        response = client.post(f"/api/datasets/{ds}/rows", json=body)
        assert response.status_code == 422, body
        assert "nope" in response.json()["detail"]
    assert client.post(f"/api/datasets/{ds}/rows", json={"version": 9}).status_code == 404


def test_column_values(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    ds = sales_dataset["id"]
    response = client.post(f"/api/datasets/{ds}/values", json={"field": "region"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["values"][0] == {"value": "North", "count": 3}
    assert {v["value"] for v in body["values"]} == {"North", "South", "East"}
    assert body["truncated"] is False

    body = client.post(f"/api/datasets/{ds}/values", json={"field": "region", "limit": 1}).json()
    assert body["truncated"] is True and len(body["values"]) == 1

    body = client.post(
        f"/api/datasets/{ds}/values",
        json={
            "field": "product",
            "search": "DG",
            "filters": [{"op": "eq", "field": "region", "value": "North"}],
        },
    ).json()
    assert body["values"] == [{"value": "Widget", "count": 1}, {"value": "Gadget", "count": 1}] or (
        body["values"] == [{"value": "Gadget", "count": 1}, {"value": "Widget", "count": 1}]
    )

    body = client.post(f"/api/datasets/{ds}/values", json={"field": "order_date"}).json()
    assert body["values"][0]["value"].startswith("2024-")
