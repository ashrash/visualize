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


def test_stacked_area_categorical_x_keeps_top_x_values_not_top_rows(
    table_relation: MakeRelation,
) -> None:
    # A categorical x over the cap must keep whichever x categories have the largest total,
    # not just the first rows DuckDB happens to return (which could split a category's bars
    # unevenly, or drop the biggest one entirely).
    rel = table_relation(
        {"product": ["Small", "Big", "Medium"], "sales": [1.0, 100.0, 10.0]},
    )
    req = _request(
        rel,
        PlotType.STACKED_AREA,
        {"x": [Encoding(field="product")], "y": [Encoding(field="sales")]},
        StackedAreaParams,
        mark_cap=1,
    )
    out = stacked_area(req)
    rows = out.table.to_pylist()
    assert [r["x"] for r in rows] == ["Big"]
    assert out.params.get("truncatedGroups") is True


def test_stacked_area_categorical_x_with_color_stays_under_mark_cap(
    table_relation: MakeRelation,
) -> None:
    # A regression: each kept x can carry one row per color, so capping by x values alone
    # (ignoring how many colors multiply each one) could still return n_series x the cap.
    n_x, n_colors = 50, 10
    rel = table_relation(
        {
            "product": [f"p{i}" for i in range(n_x) for _ in range(n_colors)],
            "region": [f"r{c}" for _ in range(n_x) for c in range(n_colors)],
            "sales": [float((n_x - i) * 10 + c) for i in range(n_x) for c in range(n_colors)],
        }
    )
    req = _request(
        rel,
        PlotType.STACKED_AREA,
        {
            "x": [Encoding(field="product")],
            "y": [Encoding(field="sales")],
            "color": [Encoding(field="region")],
        },
        StackedAreaParams,
        mark_cap=100,
    )
    out = stacked_area(req)
    assert out.table.num_rows <= 100
    assert out.params.get("truncatedGroups") is True


def test_stacked_area_ordered_x_downsamples_with_m4(table_relation: MakeRelation) -> None:
    n = 500
    rel = table_relation({"t": list(range(n)), "v": [float(i % 7) for i in range(n)]})
    req = _request(
        rel,
        PlotType.STACKED_AREA,
        {"x": [Encoding(field="t")], "y": [Encoding(field="v")]},
        StackedAreaParams,
        mark_cap=50,
    )
    out = stacked_area(req)
    assert out.table.num_rows <= 50
    assert out.reduction == Reduction.DOWNSAMPLED
    assert out.method == "m4+minmaxlttb"
    # The series' endpoints must survive downsampling.
    xs = out.table.column("x").to_pylist()
    assert min(xs) == 0
    assert max(xs) == n - 1


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


def test_percent_stacked_bar_truncates_whole_x_groups_not_mid_bar(
    table_relation: MakeRelation,
) -> None:
    # A plain row-count slice could cut a quarter's colors in half, leaving a bar whose shares
    # no longer sum to 100. The cap must fall on a quarter boundary instead.
    rel = table_relation(
        {
            "quarter": ["Q1", "Q1", "Q1", "Q2", "Q2"],
            "product": ["A", "B", "C", "A", "B"],
            "units": [10.0, 30.0, 10.0, 5.0, 5.0],
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
        mark_cap=4,
    )
    out = percent_stacked_bar(req)
    rows = out.table.to_pylist()
    assert {r["x"] for r in rows} == {"Q1"}
    assert sum(r["y0"] for r in rows) == pytest.approx(100.0)
    assert out.params.get("truncatedGroups") is True


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


def test_waterfall_default_forces_only_the_first_category_to_total(
    table_relation: MakeRelation,
) -> None:
    # last_is_total defaults to False: a plain delta sequence's running total (12) must not be
    # misrepresented by resetting the last bar to its own value (6).
    rel = table_relation({"item": ["A", "B", "C"], "amount": [10.0, -4.0, 6.0]})
    req = _request(
        rel,
        PlotType.WATERFALL,
        {"x": [Encoding(field="item")], "y": [Encoding(field="amount")]},
        WaterfallParams,
    )
    out = waterfall(req)
    rows = out.table.to_pylist()
    assert rows[0]["start"] == 0.0 and rows[0]["end"] == 10.0
    assert rows[1]["start"] == 10.0 and rows[1]["end"] == 6.0
    assert rows[2]["start"] == 6.0 and rows[2]["end"] == 12.0
    assert [r["direction"] for r in rows] == ["total", "decrease", "increase"]


def test_waterfall_last_is_total_can_be_turned_back_on(table_relation: MakeRelation) -> None:
    rel = table_relation({"item": ["A", "B", "C"], "amount": [10.0, -4.0, 6.0]})
    req = _request(
        rel,
        PlotType.WATERFALL,
        {"x": [Encoding(field="item")], "y": [Encoding(field="amount")]},
        WaterfallParams,
        params={"lastIsTotal": True},
    )
    out = waterfall(req)
    rows = out.table.to_pylist()
    assert rows[2]["start"] == 0.0 and rows[2]["end"] == 6.0
    assert rows[2]["direction"] == "total"


def test_waterfall_append_total_adds_a_genuine_grand_total_bar(
    table_relation: MakeRelation,
) -> None:
    rel = table_relation({"item": ["A", "B", "C"], "amount": [10.0, -4.0, 6.0]})
    req = _request(
        rel,
        PlotType.WATERFALL,
        {"x": [Encoding(field="item")], "y": [Encoding(field="amount")]},
        WaterfallParams,
        params={"appendTotal": True},
    )
    out = waterfall(req)
    rows = out.table.to_pylist()
    assert len(rows) == 4
    assert rows[-1]["x"] == "Total"
    assert rows[-1]["direction"] == "total"
    # The true running total (10 - 4 + 6 = 12), not a reset to the last category's own value.
    assert rows[-1]["start"] == 0.0 and rows[-1]["end"] == 12.0


def test_waterfall_null_change_is_treated_as_zero(table_relation: MakeRelation) -> None:
    rel = table_relation({"item": ["A", "B", "C"], "amount": [10.0, None, 6.0]})
    req = _request(
        rel,
        PlotType.WATERFALL,
        {"x": [Encoding(field="item")], "y": [Encoding(field="amount")]},
        WaterfallParams,
    )
    out = waterfall(req)
    rows = out.table.to_pylist()
    assert rows[1]["start"] == 10.0 and rows[1]["end"] == 10.0
    assert any("no value" in w for w in out.warnings)


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


def test_treemap_handles_a_boolean_last_level_above_the_cap(table_relation: MakeRelation) -> None:
    # A regression for a real crash: mixing a BOOLEAN last level with the literal "(Other)"
    # string (when rolling up) raised an Arrow type error. The last level is always cast to
    # VARCHAR now, rollup or not.
    n = 60
    rel = table_relation(
        {
            "region": ["N"] * n,
            "flag": [i % 2 == 0 for i in range(n)],
            "revenue": [float(n - i) for i in range(n)],
        }
    )
    req = _request(
        rel,
        PlotType.TREEMAP,
        {
            "label": [Encoding(field="region"), Encoding(field="flag")],
            "size": [Encoding(field="revenue")],
        },
        HierarchyParams,
        params={"maxLeaves": 1},
    )
    out = treemap(req)
    rows = out.table.to_pylist()
    assert any(r["label1"] == "(Other)" for r in rows)
    assert all(isinstance(r["label1"], str) for r in rows)


def test_treemap_clamps_negative_sizes_and_warns(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {"region": ["N", "N", "S"], "product": ["A", "B", "A"], "growth": [-5.0, 10.0, 2.0]}
    )
    req = _request(
        rel,
        PlotType.TREEMAP,
        {
            "label": [Encoding(field="region"), Encoding(field="product")],
            "size": [Encoding(field="growth")],
        },
        HierarchyParams,
    )
    out = treemap(req)
    rows = {(r["label0"], r["label1"]): r["size"] for r in out.table.to_pylist()}
    assert rows[("N", "A")] == 0.0
    assert any("clamped" in w for w in out.warnings)


def test_treemap_bounds_total_rows_when_a_high_cardinality_parent_exceeds_mark_cap(
    table_relation: MakeRelation,
) -> None:
    # A regression: relabeling only the last level bounds leaves *within* each parent, not the
    # total row count. Many distinct, low-cardinality parents (each with 1-2 children) used to
    # return roughly one row per parent regardless of max_leaves, caught only by the generic
    # payload guard (which truncates mid-table rather than rolling up the smallest paths).
    n = 200
    rel = table_relation(
        {
            "parent": [f"p{i}" for i in range(n)],
            "child": ["only"] * n,
            "revenue": [float(n - i) for i in range(n)],
        }
    )
    req = _request(
        rel,
        PlotType.TREEMAP,
        {
            "label": [Encoding(field="parent"), Encoding(field="child")],
            "size": [Encoding(field="revenue")],
        },
        HierarchyParams,
        params={"maxLeaves": 2000},  # no per-parent rollup triggers; only the total is at risk
        mark_cap=50,
    )
    out = treemap(req)
    assert out.table.num_rows <= 50
    assert out.params.get("rolledUpParents") == n - 49
    assert any("more paths are grouped" in w for w in out.warnings)
    # The biggest parent must survive the rollup, not an arbitrary row-order prefix.
    rows = out.table.to_pylist()
    assert any(r["label0"] == "p0" and r["size"] == float(n) for r in rows)


def test_treemap_does_not_roll_up_parents_when_well_under_mark_cap(
    table_relation: MakeRelation,
) -> None:
    # The parent rollup is keyed on mark_cap (an actual size budget), not max_leaves (a per-
    # parent tidiness setting): a handful of parents, each with a couple of children, must not
    # be collapsed just because the total happens to exceed max_leaves.
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
    assert out.params.get("rolledUpParents") is None
    by_product = {r["label1"]: r["size"] for r in out.table.to_pylist() if r["label0"] == "N"}
    assert by_product["Gadget"] == 50.0


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


def test_mosaic_rolls_up_both_dimensions_with_a_numeric_x(table_relation: MakeRelation) -> None:
    # A regression for the same crash class as treemap: a numeric x rolled up past the cap used
    # to mix with the literal "(Other)" string and raise an Arrow type error.
    n = 5
    rel = table_relation(
        {
            "region": list(range(n)),
            "channel": ["Online"] * n,
            "sales": [float(n - i) for i in range(n)],
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
        params={"maxX": 2},
    )
    out = mosaic(req)
    rows = out.table.to_pylist()
    assert any(r["x"] == "(Other)" for r in rows)
    assert all(isinstance(r["x"], str) for r in rows)
    assert out.params.get("rolledUp") is True


def test_mosaic_cap_holds_when_every_x_total_ties(table_relation: MakeRelation) -> None:
    # A regression: dense_rank() with no tie-breaker gives every tied x the same rank, so all of
    # them pass the "<= max_x" filter and the cap doesn't hold at all. Every row here has the
    # same count (1), so every x's total ties.
    n = 500
    rel = table_relation({"region": [f"r{i}" for i in range(n)], "channel": ["Online"] * n})
    req = _request(
        rel,
        PlotType.MOSAIC,
        {"x": [Encoding(field="region")], "color": [Encoding(field="channel")]},
        CrosstabParams,
        params={"maxX": 20},
    )
    out = mosaic(req)
    rows = out.table.to_pylist()
    kept_x = {r["x"] for r in rows if r["x"] != "(Other)"}
    assert len(kept_x) == 20
    assert any(r["x"] == "(Other)" for r in rows)
    assert out.params.get("rolledUp") is True


def test_mosaic_color_cap_holds_when_every_color_total_ties(table_relation: MakeRelation) -> None:
    n = 500
    rel = table_relation({"region": ["North"] * n, "channel": [f"c{i}" for i in range(n)]})
    req = _request(
        rel,
        PlotType.MOSAIC,
        {"x": [Encoding(field="region")], "color": [Encoding(field="channel")]},
        CrosstabParams,
        params={"maxColor": 15},
    )
    out = mosaic(req)
    rows = out.table.to_pylist()
    kept_color = {r["color"] for r in rows if r["color"] != "(Other)"}
    assert len(kept_color) == 15
    assert any(r["color"] == "(Other)" for r in rows)
    assert out.params.get("rolledUp") is True
