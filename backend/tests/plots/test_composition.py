"""Known-answer tests for the composition plot family's stat transforms."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from viz.contracts import ChartSpec, Encoding, PlotType, Reduction, SourceRef, Viewport
from viz.plots.base import PlotParams, PlotRequest
from viz.plots.composition import (
    CrosstabParams,
    HierarchyParams,
    PercentStackedBarParams,
    StackedAreaParams,
    WaterfallParams,
    funnel,
    marimekko,
    mosaic,
    percent_stacked_bar,
    stacked_area,
    sunburst,
    treemap,
    waterfall,
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
    params_model,
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


def test_stacked_area_sums_by_x_and_color(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "month": [1, 1, 2, 2, 3],
            "region": ["N", "S", "N", "S", "N"],
            "sales": [10.0, 5.0, 20.0, 1.0, 30.0],
        }
    )
    req = _request(
        rel,
        PlotType.STACKED_AREA,
        {
            "x": [Encoding(field="month")],
            "y": [Encoding(field="sales")],
            "color": [Encoding(field="region")],
        },
        StackedAreaParams,
    )
    out = stacked_area(req)
    rows = {(r["x"], r["color"]): r["y0"] for r in out.table.to_pylist()}
    assert rows == {(1, "N"): 10.0, (1, "S"): 5.0, (2, "N"): 20.0, (2, "S"): 1.0, (3, "N"): 30.0}
    assert out.reduction == Reduction.AGGREGATED


def test_percent_stacked_bar_shares_sum_to_100(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "quarter": ["Q1", "Q1", "Q1", "Q2"],
            "product": ["A", "B", "C", "A"],
            "units": [10.0, 30.0, 10.0, 5.0],
        }
    )
    req = _request(
        rel,
        PlotType.PERCENT_STACKED_BAR,
        {
            "x": [Encoding(field="quarter")],
            "y": [Encoding(field="units")],
            "color": [Encoding(field="product")],
        },
        PercentStackedBarParams,
    )
    out = percent_stacked_bar(req)
    rows = out.table.to_pylist()
    q1 = [r["y0"] for r in rows if r["x"] == "Q1"]
    assert sum(q1) == pytest.approx(100.0)
    by_color = {r["color"]: r["y0"] for r in rows if r["x"] == "Q1"}
    assert by_color["A"] == pytest.approx(20.0)
    assert by_color["B"] == pytest.approx(60.0)
    assert by_color["C"] == pytest.approx(20.0)
    q2 = [r["y0"] for r in rows if r["x"] == "Q2"]
    assert q2 == [pytest.approx(100.0)]


def test_waterfall_running_sum_with_kind(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "step": [1, 2, 3, 4],
            "item": ["Start", "Revenue", "Costs", "End"],
            "amount": [100.0, 50.0, -30.0, 120.0],
            "kind": ["total", "delta", "delta", "total"],
        }
    )
    req = _request(
        rel,
        PlotType.WATERFALL,
        {
            "x": [Encoding(field="item")],
            "y": [Encoding(field="amount")],
            "order": [Encoding(field="step")],
            "kind": [Encoding(field="kind")],
        },
        WaterfallParams,
    )
    out = waterfall(req)
    rows = out.table.to_pylist()
    assert [r["x"] for r in rows] == ["Start", "Revenue", "Costs", "End"]
    assert [r["start"] for r in rows] == [0.0, 100.0, 150.0, 0.0]
    assert [r["end"] for r in rows] == [100.0, 150.0, 120.0, 120.0]
    assert rows[0]["direction"] == "total"
    assert rows[1]["direction"] == "increase"
    assert rows[2]["direction"] == "decrease"
    assert rows[3]["direction"] == "total"


def test_waterfall_defaults_first_and_last_to_total(table_relation: MakeRelation) -> None:
    rel = table_relation({"item": ["A", "B", "C"], "amount": [10.0, -4.0, 6.0]})
    req = _request(
        rel,
        PlotType.WATERFALL,
        {"x": [Encoding(field="item")], "y": [Encoding(field="amount")]},
        WaterfallParams,
    )
    out = waterfall(req)
    rows = out.table.to_pylist()
    # A and C are forced totals (first/last); B is a running delta off A.
    assert rows[0]["start"] == 0.0 and rows[0]["end"] == 10.0
    assert rows[1]["start"] == 10.0 and rows[1]["end"] == 6.0
    assert rows[2]["start"] == 0.0 and rows[2]["end"] == 6.0


def test_funnel_sorts_by_explicit_order(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "stage_order": [2, 1, 3],
            "stage": ["Qualified", "Visitors", "Purchased"],
            "users": [500.0, 1000.0, 100.0],
        }
    )
    req = _request(
        rel,
        PlotType.FUNNEL,
        {
            "x": [Encoding(field="stage")],
            "y": [Encoding(field="users")],
            "order": [Encoding(field="stage_order")],
        },
        PlotParams,
    )
    out = funnel(req)
    rows = out.table.to_pylist()
    assert [r["x"] for r in rows] == ["Visitors", "Qualified", "Purchased"]
    assert [r["y0"] for r in rows] == [1000.0, 500.0, 100.0]


def test_funnel_defaults_to_value_descending(table_relation: MakeRelation) -> None:
    rel = table_relation({"stage": ["A", "B", "C"], "users": [10.0, 100.0, 50.0]})
    req = _request(
        rel,
        PlotType.FUNNEL,
        {"x": [Encoding(field="stage")], "y": [Encoding(field="users")]},
        PlotParams,
    )
    out = funnel(req)
    rows = out.table.to_pylist()
    assert [r["x"] for r in rows] == ["B", "C", "A"]


def test_treemap_rolls_small_leaves_into_other(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "region": ["N", "N", "N", "S"],
            "product": ["Widget", "Gadget", "Gizmo", "Widget"],
            "revenue": [100.0, 50.0, 1.0, 20.0],
        }
    )
    req = _request(
        rel,
        PlotType.TREEMAP,
        {
            "label": [Encoding(field="region"), Encoding(field="product")],
            "size": [Encoding(field="revenue")],
        },
        HierarchyParams,
        params={"maxLeaves": 2},
    )
    out = treemap(req)
    rows = out.table.to_pylist()
    by_product = {r["label1"]: r["size"] for r in rows if r["label0"] == "N"}
    assert by_product["Widget"] == 100.0
    assert by_product["Gadget"] == 50.0
    assert by_product.get("(Other)") == 1.0
    # 4 leaves total, max 2 kept (Widget/N=100, Gadget/N=50); Gizmo/N=1 and Widget/S=20 roll up.
    assert out.params.get("rolledUpLeaves") == 2
    assert any("Other" in w for w in out.warnings)


def test_sunburst_uses_count_without_a_size_field(table_relation: MakeRelation) -> None:
    rel = table_relation({"region": ["N", "N", "S"], "product": ["A", "B", "A"]})
    req = _request(
        rel,
        PlotType.SUNBURST,
        {"label": [Encoding(field="region"), Encoding(field="product")]},
        HierarchyParams,
    )
    out = sunburst(req)
    rows = {(r["label0"], r["label1"]): r["size"] for r in out.table.to_pylist()}
    assert rows == {("N", "A"): 1.0, ("N", "B"): 1.0, ("S", "A"): 1.0}


def test_mosaic_shares_within_each_column_sum_to_one(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "region": ["North", "North", "South"],
            "channel": ["Online", "Store", "Online"],
            "sales": [60.0, 40.0, 10.0],
        }
    )
    req = _request(
        rel,
        PlotType.MOSAIC,
        {
            "x": [Encoding(field="region")],
            "color": [Encoding(field="channel")],
            "size": [Encoding(field="sales")],
        },
        CrosstabParams,
    )
    out = mosaic(req)
    rows = out.table.to_pylist()
    north = [r for r in rows if r["x"] == "North"]
    assert sum(r["share"] for r in north) == pytest.approx(1.0)
    south = [r for r in rows if r["x"] == "South"]
    assert south[0]["share"] == pytest.approx(1.0)
    # xShare is per-x (not per-row): North = 100 of a 110 grand total.
    assert north[0]["xShare"] == pytest.approx(100.0 / 110.0)
    assert south[0]["xShare"] == pytest.approx(10.0 / 110.0)


def test_marimekko_is_the_same_crosstab_as_mosaic(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {"region": ["N", "S"], "channel": ["Online", "Online"], "sales": [1.0, 2.0]}
    )
    req = _request(
        rel,
        PlotType.MARIMEKKO,
        {
            "x": [Encoding(field="region")],
            "color": [Encoding(field="channel")],
            "size": [Encoding(field="sales")],
        },
        CrosstabParams,
    )
    out = marimekko(req)
    assert out.table.num_rows == 2
