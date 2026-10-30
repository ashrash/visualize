"""Chart-data protocol: cache, cancellation, pipeline sources, plot-module dispatch, Arrow
types the browser can use."""

from __future__ import annotations

import json
import threading
from datetime import time
from typing import Any

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from viz.chartdata.cache import chart_key
from viz.chartdata.slots import SlotRegistry
from viz.contracts import ChartDataRequest, PlotType, Reduction, StatResult
from viz.errors import QueryInterruptedError
from viz.plots import scoped_registry
from viz.plots.base import Channel, PlotRequest, TableOutput, register_plot

SALES = """region,product,sales,units,order_date
North,Widget,120.5,3,2024-01-05
South,Widget,80,2,2024-01-17
North,Gadget,200,5,2024-02-02
East,Gadget,,1,2024-02-20
South,Gizmo,55.25,1,2024-03-03
North,Gizmo,10,1,2024-03-15
"""


def bar(dataset_id: str, **extra: Any) -> dict[str, Any]:
    spec = {
        "id": "chart-1",
        "plotType": "bar",
        "source": {"kind": "dataset", "id": dataset_id},
        "encodings": {"x": [{"field": "region"}], "y": [{"field": "sales"}]},
    }
    spec.update(extra)
    return {"spec": spec}


def test_cache_hits_ignore_style_and_miss_on_data_changes(upload: Any, chart: Any) -> None:
    ds = upload(SALES)
    _, first = chart(bar(ds["id"]))
    assert first["cached"] is False
    _, again = chart(bar(ds["id"], style={"title": "Renamed", "palette": "viridis"}, name="x"))
    assert again["cached"] is True
    _, filtered = chart(
        {**bar(ds["id"]), "filters": [{"op": "eq", "field": "region", "value": "North"}]}
    )
    assert filtered["cached"] is False
    _, bars = chart(bar(ds["id"], style={"errorBars": {"kind": "sd"}}))
    assert bars["cached"] is False


def test_cache_key_covers_version_and_viewport_bucket() -> None:
    request = ChartDataRequest.model_validate(bar("d"))
    assert chart_key("d@v1", request, 50_000) != chart_key("d@v2", request, 50_000)
    wide = request.model_copy(
        update={"viewport": request.viewport.model_copy(update={"width_px": 801})}
    )
    wider = request.model_copy(
        update={"viewport": request.viewport.model_copy(update={"width_px": 900})}
    )
    assert chart_key("d@v1", wide, 50_000) == chart_key("d@v1", wider, 50_000)
    assert chart_key("d@v1", request, 50_000) != chart_key("d@v1", wide, 50_000)


def test_slot_registry_supersedes_older_generations() -> None:
    slots = SlotRegistry()
    first = slots.begin("chart:a")
    second = slots.begin("chart:a")
    other = slots.begin("chart:b")
    with pytest.raises(QueryInterruptedError) as info:
        slots.check("chart:a", first)
    assert info.value.status_code == 409
    slots.check("chart:a", second)
    slots.check("chart:b", other)
    slots.supersede("chart:b")
    with pytest.raises(QueryInterruptedError):
        slots.check("chart:b", other)
    for key in ("chart:a", "chart:a", "chart:b"):
        slots.end(key)
    assert slots.begin("chart:a") == 1  # idle slots are forgotten


def test_newer_request_in_the_same_slot_wins(client: TestClient, upload: Any) -> None:
    ds = upload(SALES)
    entered, release = threading.Event(), threading.Event()

    with scoped_registry():

        @register_plot(PlotType.SCATTER, channels=[Channel("x"), Channel("y")])
        def slow(req: PlotRequest) -> TableOutput:
            if req.params.model_dump() == {} and not entered.is_set():
                entered.set()
                release.wait(5)
            table = req.relation.arrow(req.relation.filtered("region", "sales"))
            return TableOutput(table=table, reduction=Reduction.NONE, method=None, fields={})

        body = {
            "spec": {
                "id": "same",
                "plotType": "scatter",
                "source": {"kind": "dataset", "id": ds["id"]},
                "encodings": {"x": [{"field": "region"}], "y": [{"field": "sales"}]},
            }
        }
        results: dict[str, int] = {}

        def first() -> None:
            results["first"] = client.post("/api/charts/data", json=body).status_code

        worker = threading.Thread(target=first)
        worker.start()
        assert entered.wait(5)
        newer = dict(body, filters=[{"op": "is_null", "field": "sales", "isNull": False}])
        assert client.post("/api/charts/data", json=newer).status_code == 200
        other_slot = dict(body, slot="widget-2")
        assert client.post("/api/charts/data", json=other_slot).status_code == 200
        release.set()
        worker.join(10)
        # The first request resumed after the newer one began: it stops before its next query.
        assert results["first"] == 409


def test_plot_module_table_output(client: TestClient, upload: Any, chart: Any) -> None:
    ds = upload(SALES)
    with scoped_registry():

        @register_plot(PlotType.SCATTER, channels=[Channel("x"), Channel("y")])
        def scatter(req: PlotRequest) -> TableOutput:
            x, y = req.field("x"), req.field("y")
            assert x == "units" and y == "sales"  # resolved to the source's spelling
            query = req.relation.select(
                *(__import__("sqlglot").exp.column(n, quoted=True) for n in (x, y))
            )
            table = req.relation.arrow(query).rename_columns(["x", "y"])
            return TableOutput(
                table=table,
                reduction=Reduction.NONE,
                method=None,
                fields={"x": x, "y": y},
                params={"pointSize": 3},
            )

        body = {
            "spec": {
                "id": "s",
                "plotType": "scatter",
                "source": {"kind": "dataset", "id": ds["id"]},
                "encodings": {"x": [{"field": "UNITS"}], "y": [{"field": "Sales"}]},
            },
            "viewport": {"xRange": [2, 5]},
        }
        table, meta = chart(body)
    assert sorted(table.column("x").to_pylist()) == [2, 3, 5]
    assert meta["params"] == {"pointSize": 3}
    assert meta["rowsMatched"] == 3 and meta["rowsScanned"] == 6
    assert meta["xDomain"] == [1.0, 5.0]


def test_plot_module_stat_result_is_json(client: TestClient, upload: Any) -> None:
    ds = upload(SALES)
    with scoped_registry():

        @register_plot(PlotType.QQ, channels=[Channel("x")])
        def qq(req: PlotRequest) -> StatResult:
            return StatResult(values={"slope": 1.0}, method="test", n=5, sampled=True, n_total=6)

        body = {
            "spec": {
                "id": "q",
                "plotType": "qq",
                "source": {"kind": "dataset", "id": ds["id"]},
                "encodings": {"x": [{"field": "sales"}]},
            }
        }
        response = client.post("/api/charts/data", json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["result"]["values"] == {"slope": 1.0}
    assert payload["meta"]["reduction"] == "sampled" and payload["meta"]["rowsMatched"] == 6


def test_plot_module_channel_errors_are_422(client: TestClient, upload: Any) -> None:
    ds = upload(SALES)
    with scoped_registry():

        @register_plot(PlotType.QQ, channels=[Channel("x")])
        def qq(req: PlotRequest) -> StatResult:  # pragma: no cover - never reached
            raise AssertionError

        body = {
            "spec": {
                "id": "q",
                "plotType": "qq",
                "source": {"kind": "dataset", "id": ds["id"]},
                "encodings": {},
            }
        }
        assert client.post("/api/charts/data", json=body).status_code == 422


def _pipeline(dataset_id: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": "pipe", "source": {"kind": "dataset", "id": dataset_id}, "steps": steps}


def test_pipeline_as_chart_source(upload: Any, chart: Any, client: TestClient) -> None:
    ds = upload(SALES)
    steps = [
        {
            "id": "f",
            "op": "filter",
            "predicates": [{"op": "is_null", "field": "sales", "isNull": False}],
        },
        {"id": "c", "op": "calc", "name": "revenue", "expression": "sales * units"},
    ]
    pipeline = _pipeline(ds["id"], steps)
    body = {
        "spec": {
            "id": "p",
            "plotType": "bar",
            "source": {"kind": "pipeline", "id": "pipe"},
            "encodings": {"x": [{"field": "region"}], "y": [{"field": "revenue"}]},
        },
        "pipelines": [pipeline],
    }
    table, meta = chart(body)
    assert table.to_pylist() == [{"x": "North", "y0": 1371.5}, {"x": "South", "y0": 215.25}]
    assert meta["rowsMatched"] == 5

    # The same chart over the materialized pipeline gives the same answer.
    steps[1]["materialize"] = True
    pipeline = _pipeline(ds["id"], steps)
    assert client.post("/api/pipelines/materialize", json={"pipeline": pipeline}).json()[
        "ready"
    ] == ["c"]
    table2, _ = chart({**body, "pipelines": [pipeline]})
    assert table2.to_pylist() == table.to_pylist()

    missing = client.post("/api/charts/data", json={**body, "pipelines": []})
    assert missing.status_code == 404
    broken = _pipeline(ds["id"], [{"id": "s", "op": "select", "fields": ["nope"]}])
    response = client.post("/api/charts/data", json={**body, "pipelines": [broken]})
    assert response.status_code == 422 and "'s'" in response.json()["detail"]


def test_decimal_time_and_hugeint_reach_the_browser_as_usable_types(
    upload: Any, client: TestClient
) -> None:
    ds = upload(SALES)
    steps = [
        {"id": "d", "op": "calc", "name": "price", "expression": "CAST(sales AS DECIMAL(10, 2))"},
        {"id": "t", "op": "calc", "name": "at", "expression": "CAST('10:30:00.120' AS TIME)"},
        {"id": "h", "op": "calc", "name": "big", "expression": "CAST(units AS HUGEINT)"},
    ]
    pipeline = _pipeline(ds["id"], steps)

    def arrow(response: Any) -> pa.Table:
        assert response.status_code == 200, response.text
        return pa.ipc.open_stream(response.content).read_all()

    preview = arrow(client.post("/api/pipelines/preview", json={"pipeline": pipeline}))
    assert preview.schema.field("price").type == pa.float64()
    assert preview.schema.field("at").type == pa.string()
    assert preview.schema.field("big").type == pa.float64()
    assert preview.column("price").to_pylist()[0] == 120.5
    assert preview.column("at").to_pylist()[0] == "10:30:00.120"
    assert preview.column("big").to_pylist()[0] == 3.0

    for x_field, y_field in (("price", "big"), ("at", "price"), ("big", "price")):
        body = {
            "spec": {
                "id": f"c-{x_field}",
                "plotType": "bar",
                "source": {"kind": "pipeline", "id": "pipe"},
                "encodings": {"x": [{"field": x_field}], "y": [{"field": y_field, "agg": "sum"}]},
            },
            "pipelines": [pipeline],
        }
        table = arrow(client.post("/api/charts/data", json=body))
        assert table.schema.field("y0").type == pa.float64()
        assert table.schema.field("x").type in (pa.float64(), pa.string())
        meta = json.loads(table.schema.metadata[b"viz.meta"])
        assert meta["rowsReturned"] == table.num_rows


def test_browser_table_unit() -> None:
    from viz.chartdata.arrow import browser_table

    table = pa.table(
        {
            "t": pa.array([time(10, 30), time(10, 30, 0, 120000), None], pa.time64("us")),
            "s": pa.array([time(1, 2, 3), None, None], pa.time32("s")),
        }
    )
    out = browser_table(table)
    assert out.column("t").to_pylist() == ["10:30:00", "10:30:00.120", None]
    assert out.column("s").to_pylist() == ["01:02:03", None, None]


def test_plots_catalogue(client: TestClient) -> None:
    plots = client.get("/api/plots").json()
    assert len(plots) == 74
    assert all("channels" in p for p in plots)
