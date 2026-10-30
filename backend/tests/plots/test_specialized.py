"""Known-answer tests for the specialized plot family's stat transforms."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from viz.contracts import ChartSpec, Encoding, PlotType, SourceRef, Viewport
from viz.plots.base import PlotParams, PlotRequest
from viz.plots.specialized import (
    DensityParams,
    DumbbellParams,
    ErrorBarParams,
    GanttParams,
    GraphParams,
    RaincloudParams,
    SortableBarParams,
    dumbbell,
    error_bar,
    gantt,
    lollipop,
    network,
    pareto,
    raincloud,
    ridgeline,
    sankey,
)
from viz.stats.relation import Relation

MakeRelation = Callable[..., Relation]


def _spec(
    plot_type: PlotType, encodings: dict[str, list[Encoding]], params: dict | None = None
) -> ChartSpec:
    return ChartSpec(
        id="c1",
        plot_type=plot_type,
        source=SourceRef(kind="dataset", id="d1"),
        encodings=encodings,
        params=params or {},
    )


def _request(
    relation: Relation,
    plot_type: PlotType,
    encodings: dict[str, list[Encoding]],
    params_model=PlotParams,
    params: dict | None = None,
    mark_cap: int = 50_000,
) -> PlotRequest:
    spec = _spec(plot_type, encodings, params)
    return PlotRequest(
        spec=spec,
        params=params_model.model_validate(params or {}),
        relation=relation,
        viewport=Viewport(),
        mark_cap=mark_cap,
    )


def test_sankey_aggregates_and_reports_totals(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "source": ["Ads", "Ads", "Search"],
            "target": ["Home", "Home", "Home"],
            "value": [10.0, 5.0, 20.0],
        }
    )
    req = _request(
        rel,
        PlotType.SANKEY,
        {
            "source": [Encoding(field="source")],
            "target": [Encoding(field="target")],
            "size": [Encoding(field="value")],
        },
        GraphParams,
    )
    out = sankey(req)
    links = out.values["links"]
    assert len(links) == 2
    by_source = {link["source"]: link["value"] for link in links}
    assert by_source["Ads"] == pytest.approx(15.0)
    assert by_source["Search"] == pytest.approx(20.0)
    nodes = {node["name"]: node["value"] for node in out.values["nodes"]}
    assert nodes["Home"] == pytest.approx(35.0)


def test_sankey_caps_nodes_into_other(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "source": ["A", "B", "C"],
            "target": ["X", "X", "X"],
            "value": [100.0, 2.0, 1.0],
        }
    )
    req = _request(
        rel,
        PlotType.SANKEY,
        {
            "source": [Encoding(field="source")],
            "target": [Encoding(field="target")],
            "size": [Encoding(field="value")],
        },
        GraphParams,
        params={"maxNodes": 2},
    )
    out = sankey(req)
    names = {node["name"] for node in out.values["nodes"]}
    assert "(Other)" in names
    assert "A" in names and "X" in names


def test_sankey_drops_edges_that_would_cycle(table_relation: MakeRelation) -> None:
    rel = table_relation({"source": ["A", "B"], "target": ["B", "A"], "value": [1.0, 1.0]})
    req = _request(
        rel,
        PlotType.SANKEY,
        {"source": [Encoding(field="source")], "target": [Encoding(field="target")]},
        GraphParams,
    )
    out = sankey(req)
    # A cyclic 2-node graph can keep at most one of the two edges as a DAG.
    assert len(out.values["links"]) <= 1
    assert any("loop" in w for w in out.warnings)


def test_network_is_undirected_and_keeps_cycles(table_relation: MakeRelation) -> None:
    rel = table_relation({"source": ["A", "B"], "target": ["B", "A"], "value": [1.0, 2.0]})
    req = _request(
        rel,
        PlotType.NETWORK,
        {"source": [Encoding(field="source")], "target": [Encoding(field="target")]},
        GraphParams,
    )
    out = network(req)
    assert len(out.values["links"]) == 2


def test_gantt_returns_rows_sorted_by_start(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "task": ["C", "A", "B"],
            "start": ["2024-03-01", "2024-01-01", "2024-02-01"],
            "end": ["2024-03-10", "2024-01-10", "2024-02-10"],
        },
        semantics=None,
    )
    req = _request(
        rel,
        PlotType.GANTT,
        {
            "label": [Encoding(field="task")],
            "start": [Encoding(field="start")],
            "end": [Encoding(field="end")],
        },
        GanttParams,
    )
    out = gantt(req)
    assert out.table.column("label").to_pylist() == ["A", "B", "C"]


def test_pareto_sorts_descending_with_cumulative_percent(table_relation: MakeRelation) -> None:
    rel = table_relation({"defect": ["Scratch", "Dent", "Crack"], "count": [10.0, 50.0, 40.0]})
    req = _request(
        rel,
        PlotType.PARETO,
        {"x": [Encoding(field="defect")], "y": [Encoding(field="count")]},
    )
    out = pareto(req)
    rows = out.table.to_pylist()
    assert [r["x"] for r in rows] == ["Dent", "Crack", "Scratch"]
    assert rows[-1]["cumPercent"] == pytest.approx(100.0)
    assert rows[0]["cumPercent"] == pytest.approx(50.0)
    assert rows[1]["cumPercent"] == pytest.approx(90.0)


def test_error_bar_sd_around_the_mean(table_relation: MakeRelation) -> None:
    rel = table_relation({"species": ["A", "A", "A", "B"], "mass": [10.0, 20.0, 30.0, 5.0]})
    req = _request(
        rel,
        PlotType.ERROR_BAR,
        {"x": [Encoding(field="species")], "y": [Encoding(field="mass")]},
        ErrorBarParams,
        params={"kind": "sd"},
    )
    out = error_bar(req)
    rows = {r["x"]: r for r in out.table.to_pylist()}
    assert rows["A"]["y0"] == pytest.approx(20.0)
    # sample stddev of [10, 20, 30] is 10.
    assert rows["A"]["y0_lo"] == pytest.approx(10.0)
    assert rows["A"]["y0_hi"] == pytest.approx(30.0)
    assert rows["B"]["y0_lo"] is None  # n=1: stddev is undefined


def test_lollipop_sorts_by_value_by_default(table_relation: MakeRelation) -> None:
    rel = table_relation({"defect": ["A", "B", "C"], "count": [5.0, 50.0, 10.0]})
    req = _request(
        rel,
        PlotType.LOLLIPOP,
        {"x": [Encoding(field="defect")], "y": [Encoding(field="count")]},
        SortableBarParams,
    )
    out = lollipop(req)
    assert out.table.column("x").to_pylist() == ["B", "C", "A"]


def test_dumbbell_pairs_two_measures(table_relation: MakeRelation) -> None:
    rel = table_relation({"country": ["A", "B"], "exp2000": [60.0, 70.0], "exp2020": [65.0, 68.0]})
    req = _request(
        rel,
        PlotType.DUMBBELL,
        {
            "x": [Encoding(field="country")],
            "y": [Encoding(field="exp2000"), Encoding(field="exp2020")],
        },
        DumbbellParams,
    )
    out = dumbbell(req)
    rows = {r["x"]: (r["y0"], r["y1"]) for r in out.table.to_pylist()}
    assert rows["A"] == (60.0, 65.0)
    assert rows["B"] == (70.0, 68.0)
    # Default sort is by descending delta (A: +5, B: -2), so A comes first.
    assert out.table.column("x").to_pylist()[0] == "A"


def test_ridgeline_returns_one_curve_per_group(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "temp": [10.0, 11.0, 9.0, 12.0, 30.0, 31.0, 29.0, 32.0],
            "month": ["Jan", "Jan", "Jan", "Jan", "Jul", "Jul", "Jul", "Jul"],
        }
    )
    req = _request(
        rel,
        PlotType.RIDGELINE,
        {"value": [Encoding(field="temp")], "group": [Encoding(field="month")]},
        DensityParams,
    )
    out = ridgeline(req)
    groups = {g["key"]: g for g in out.values["groups"]}
    assert set(groups) == {"Jan", "Jul"}
    assert groups["Jan"]["n"] == 4
    assert len(groups["Jan"]["x"]) == len(groups["Jan"]["density"])


def test_raincloud_has_box_density_and_points_per_group(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "mass": [10.0, 20.0, 30.0, 40.0, 50.0, 1.0, 2.0, 3.0, 4.0, 5.0],
            "species": ["A"] * 5 + ["B"] * 5,
        }
    )
    req = _request(
        rel,
        PlotType.RAINCLOUD,
        {"value": [Encoding(field="mass")], "group": [Encoding(field="species")]},
        RaincloudParams,
    )
    out = raincloud(req)
    groups = {g["key"]: g for g in out.values["groups"]}
    # DuckDB quantile_cont is NumPy's "linear" method (R type 7): for [10,20,30,40,50],
    # p=0.25 -> index 1.0 -> 20.0, p=0.75 -> index 3.0 -> 40.0.
    assert groups["A"]["box"]["median"] == pytest.approx(30.0)
    assert groups["A"]["box"]["q1"] == pytest.approx(20.0)
    assert groups["A"]["box"]["q3"] == pytest.approx(40.0)
    assert len(groups["A"]["points"]) == 5
    assert len(groups["A"]["density"]["x"]) == len(groups["A"]["density"]["density"])


def test_raincloud_without_a_group_gives_one_group(table_relation: MakeRelation) -> None:
    rel = table_relation({"mass": [10.0, 20.0, 30.0, 40.0, 50.0]})
    req = _request(
        rel,
        PlotType.RAINCLOUD,
        {"value": [Encoding(field="mass")]},
        RaincloudParams,
    )
    out = raincloud(req)
    assert len(out.values["groups"]) == 1
    assert out.values["groups"][0]["key"] is None
    assert out.values["groups"][0]["box"]["median"] == pytest.approx(30.0)
