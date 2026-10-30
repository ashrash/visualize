from __future__ import annotations

import json
from typing import Any

import pyarrow as pa
from fastapi.testclient import TestClient

from viz.contracts import ARROW_META_KEY


def _chart(client: TestClient, body: dict[str, Any]) -> tuple[pa.Table, dict[str, Any]]:
    response = client.post("/api/charts/data", json=body)
    assert response.status_code == 200, response.text
    table = pa.ipc.open_stream(response.content).read_all()
    return table, json.loads(table.schema.metadata[ARROW_META_KEY.encode()])


def _bar(dataset_id: str, **extra: Any) -> dict[str, Any]:
    spec = {
        "id": "chart-1",
        "plotType": "bar",
        "source": {"kind": "dataset", "id": dataset_id},
        "encodings": {"x": [{"field": "region"}], "y": [{"field": "sales", "agg": "sum"}]},
    }
    spec.update(extra)
    return {"spec": spec}


def test_bar_sums_by_category_sorted_desc(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    table, meta = _chart(client, _bar(sales_dataset["id"]))
    rows = table.to_pylist()
    assert [r["x"] for r in rows] == ["North", "South", "East"]
    assert rows[0]["y0"] == 330.5
    assert rows[2]["y0"] is None  # East only has a missing value
    assert meta["reduction"] == "aggregated"
    assert meta["rowsScanned"] == 6
    assert meta["fields"] == {"x": "region", "y0": "Sum of sales"}


def test_filters_and_count(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    body = _bar(
        sales_dataset["id"],
        encodings={"x": [{"field": "product"}], "y": [{"agg": "count"}]},
        filters=[{"op": "in", "field": "region", "values": ["North"]}],
    )
    body["filters"] = [{"op": "range", "field": "units", "min": 2}]
    table, _ = _chart(client, body)
    assert {r["x"]: r["y0"] for r in table.to_pylist()} == {"Widget": 1.0, "Gadget": 1.0}


def test_time_unit_and_color(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    body = _bar(
        sales_dataset["id"],
        plotType="line",
        encodings={
            "x": [{"field": "order_date", "timeUnit": "month"}],
            "y": [{"field": "units", "agg": "sum"}],
            "color": [{"field": "region"}],
        },
    )
    table, meta = _chart(client, body)
    assert table.num_rows == 6
    assert set(meta["fields"]) == {"x", "color", "y0"}


def test_unknown_field_and_unimplemented_plot(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    body = _bar(sales_dataset["id"], encodings={"x": [{"field": "nope"}]})
    response = client.post("/api/charts/data", json=body)
    assert response.status_code == 422
    assert "nope" in response.json()["detail"]

    body = _bar(sales_dataset["id"], plotType="violin")
    assert client.post("/api/charts/data", json=body).status_code == 501


def test_plot_catalogue(client: TestClient) -> None:
    plots = client.get("/api/plots").json()
    assert len(plots) == 74
    assert {"type": "sankey", "family": "specialized"} in [
        {"type": p["type"], "family": p["family"]} for p in plots
    ]
