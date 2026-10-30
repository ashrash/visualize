"""HTTP tests for /api/pipelines: inspect, preview, formula check, functions, materialize."""

from __future__ import annotations

import json
from typing import Any

import pyarrow as pa
from fastapi.testclient import TestClient

from viz.contracts import ARROW_META_KEY


def pipeline(dataset_id: str, steps: list[dict[str, Any]], pid: str = "p1") -> dict[str, Any]:
    return {"id": pid, "source": {"kind": "dataset", "id": dataset_id}, "steps": steps}


STEPS = [
    {"id": "f", "op": "filter", "predicates": [{"op": "range", "field": "units", "min": 1}]},
    {"id": "c", "op": "calc", "name": "revenue", "expression": "coalesce(sales, 0) * units"},
    {"id": "off", "op": "select", "fields": ["region"], "enabled": False},
    {
        "id": "g",
        "op": "group_aggregate",
        "by": ["region"],
        "aggregations": [{"field": "revenue", "fn": "sum", "asName": "total"}],
    },
]


def preview(client: TestClient, body: dict[str, Any]) -> tuple[pa.Table, dict[str, Any]]:
    response = client.post("/api/pipelines/preview", json=body)
    assert response.status_code == 200, response.text
    table = pa.ipc.open_stream(response.content).read_all()
    return table, json.loads(table.schema.metadata[ARROW_META_KEY.encode()])


def test_inspect_reports_schema_per_step(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    body = {"pipeline": pipeline(sales_dataset["id"], STEPS), "includeSql": True}
    result = client.post("/api/pipelines/inspect", json=body).json()
    assert result["ok"] is True
    assert [s["status"] for s in result["steps"]] == ["ok", "ok", "disabled", "ok"]
    assert [c["name"] for c in result["steps"][1]["columns"]][-1] == "revenue"
    assert [c["name"] for c in result["columns"]] == ["region", "total"]
    assert result["columns"][1]["semantic"] == "quantitative"
    assert result["sql"].startswith("WITH")


def test_inspect_reports_errors_without_failing(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    steps = [
        {"id": "a", "op": "calc", "name": "x", "expression": "getenv('HOME')"},
        {"id": "b", "op": "sort", "keys": [{"field": "x"}]},
    ]
    response = client.post(
        "/api/pipelines/inspect", json={"pipeline": pipeline(sales_dataset["id"], steps)}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["ok"] is False
    assert [s["status"] for s in result["steps"]] == ["error", "blocked"]
    assert "not allowed" in result["steps"][0]["error"]["message"]


def test_preview_any_step_with_paging_sort_and_filters(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    p = pipeline(sales_dataset["id"], STEPS)
    table, meta = preview(client, {"pipeline": p})
    # group_aggregate output is ordered by the group keys.
    assert table.to_pylist() == [
        {"region": "East", "total": 0.0},
        {"region": "North", "total": 1371.5},
        {"region": "South", "total": 215.25},
    ]
    assert meta["totalRows"] == 3

    table, meta = preview(
        client,
        {
            "pipeline": p,
            "stepId": "c",
            "sort": [{"field": "revenue", "descending": True}],
            "filters": [{"op": "in", "field": "REGION", "values": ["North"]}],
            "offset": 1,
            "limit": 1,
        },
    )
    assert table.column("revenue").to_pylist() == [361.5]
    assert meta["totalRows"] == 3 and meta["limit"] == 1 and meta["offset"] == 1
    assert "__viz_rn" not in table.column_names


def test_preview_of_failed_step_is_422(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    steps = [{"id": "s", "op": "select", "fields": ["nope"]}]
    response = client.post(
        "/api/pipelines/preview", json={"pipeline": pipeline(sales_dataset["id"], steps)}
    )
    assert response.status_code == 422
    assert "nope" in response.json()["detail"]


def test_preview_warns_about_failed_casts(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    steps = [{"id": "k", "op": "cast", "casts": [{"field": "region", "to": "integer"}]}]
    _, meta = preview(client, {"pipeline": pipeline(sales_dataset["id"], steps), "stepId": "k"})
    assert meta["warnings"] == ["6 value(s) in 'region' couldn't be converted and are now empty"]


def test_formula_check(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    p = pipeline(sales_dataset["id"], STEPS)
    ok = client.post(
        "/api/pipelines/formula/check",
        json={"expression": "revenue / UNITS", "pipeline": p, "beforeStepId": "g"},
    ).json()
    assert ok == {"ok": True, "error": None, "outputType": "DOUBLE", "fields": ["revenue", "units"]}

    bad = client.post(
        "/api/pipelines/formula/check",
        json={"expression": "revenue + 1", "pipeline": p, "beforeStepId": "c"},
    ).json()
    assert bad["ok"] is False and "revenue" in bad["error"]["message"]

    typed = client.post(
        "/api/pipelines/formula/check",
        json={"expression": "region + 1", "pipeline": p, "beforeStepId": "c"},
    ).json()
    assert typed["ok"] is False and typed["error"]["message"]


def test_formula_functions(client: TestClient) -> None:
    functions = client.get("/api/pipelines/formula/functions").json()
    names = {f["name"] for f in functions}
    assert {"round", "date_trunc", "coalesce", "regexp_replace"} <= names
    assert "getenv" not in names and "read_csv" not in names


def test_pipeline_as_chart_source_needs_the_pipeline(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    response = client.post(
        "/api/pipelines/inspect",
        json={
            "pipeline": {"id": "outer", "source": {"kind": "pipeline", "id": "missing"}},
        },
    )
    assert response.status_code == 404


def test_materialize_writes_parquet_and_later_compiles_read_it(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    steps = [dict(s) for s in STEPS]
    steps[3]["materialize"] = True
    p = pipeline(sales_dataset["id"], steps)
    before = client.post("/api/pipelines/inspect", json={"pipeline": p}).json()
    assert before["steps"][3]["materialized"] == "pending"

    result = client.post("/api/pipelines/materialize", json={"pipeline": p}).json()
    assert result == {"jobs": {}, "ready": ["g"]}

    after = client.post("/api/pipelines/inspect", json={"pipeline": p, "includeSql": True}).json()
    assert after["steps"][3]["materialized"] == "ready"
    assert "derived" in after["sql"]
    table, _ = preview(client, {"pipeline": p})
    assert table.column("total").to_pylist() == [0.0, 1371.5, 215.25]
