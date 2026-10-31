from __future__ import annotations

import itertools
from typing import Any

import numpy as np
import pytest
from scipy import stats as sps

import viz.plots as plots
from viz.contracts import ChartSpec, Encoding, PlotType, Reduction, SourceRef, Viewport
from viz.plots.base import TableOutput
from viz.stats.relation import Relation

RNG_SEED = 20261031


def _spec(
    plot_type: PlotType, params: dict[str, object] | None = None, **channels: list[str | None]
) -> ChartSpec:
    return ChartSpec.model_validate(
        {
            "id": "c1",
            "plotType": plot_type.value,
            "source": SourceRef(kind="dataset", id="d1").model_dump(),
            "encodings": {
                name: [Encoding(field=f).model_dump(by_alias=True) for f in fields]
                for name, fields in channels.items()
            },
            "params": params or {},
        }
    )


def _run(
    plot_type: PlotType,
    relation: Relation,
    params: dict[str, object] | None = None,
    mark_cap: int = 50_000,
    **channels: list[str | None],
) -> TableOutput:
    out = plots.run_plot_stat(
        _spec(plot_type, params, **channels), relation, viewport=Viewport(), mark_cap=mark_cap
    )
    assert isinstance(out, TableOutput)
    return out


# ---------------------------------------------------------------- histogram


def test_histogram_fixed_width_matches_hand_calculation(table_relation: Any) -> None:
    # 10 values in [0, 10); width 2 -> 5 bins of width 2 each.
    values = [0.5, 1.5, 2.5, 2.9, 4.1, 5.0, 6.5, 7.5, 8.5, 9.9]
    rel = table_relation({"v": values})
    out = _run(PlotType.HISTOGRAM, rel, {"binWidth": 2.0}, x=["v"])
    assert out.table.column_names == ["x_lo", "x_hi", "count"]
    counts = out.table.column("count").to_pylist()
    assert sum(counts) == len(values)
    assert out.reduction == Reduction.BINNED
    assert out.params["binWidth"] == 2.0


def test_histogram_by_group_sums_to_group_sizes(table_relation: Any) -> None:
    rel = table_relation({"v": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "g": ["a", "a", "a", "b", "b", "b"]})
    out = _run(PlotType.HISTOGRAM, rel, {"bins": 3}, x=["v"], color=["g"])
    assert set(out.table.column_names) == {"x_lo", "x_hi", "count", "color"}
    totals: dict[str, int] = {}
    for color, count in zip(
        out.table.column("color").to_pylist(), out.table.column("count").to_pylist(), strict=True
    ):
        totals[color] = totals.get(color, 0) + count
    assert totals == {"a": 3, "b": 3}


def test_frequency_polygon_midpoints_match_histogram(table_relation: Any) -> None:
    values = [float(i) for i in range(20)]
    rel = table_relation({"v": values})
    out = _run(PlotType.FREQUENCY_POLYGON, rel, {"binWidth": 5.0}, x=["v"])
    assert out.table.column_names == ["x", "y0"]
    xs = out.table.column("x").to_pylist()
    # Midpoints are evenly spaced by the bin width.
    diffs = [round(b - a, 6) for a, b in itertools.pairwise(xs)]
    assert all(d == 5.0 for d in diffs)
    assert sum(out.table.column("y0").to_pylist()) == len(values)


# ---------------------------------------------------------------- density


def test_density_matches_kde_kernel(table_relation: Any) -> None:
    values = np.random.default_rng(RNG_SEED).normal(size=300)
    rel = table_relation({"v": values.tolist()})
    out = _run(PlotType.DENSITY, rel, {"bandwidth": 0.5, "gridSize": 64}, x=["v"])
    assert out.table.column_names == ["x", "y0"]
    from viz.stats.kde import kde

    expected = kde(values, bw=0.5, grid_size=64)
    np.testing.assert_allclose(out.table.column("y0").to_numpy(), expected.density)
    assert out.reduction == Reduction.NONE


def test_density_grouped_has_one_curve_per_group(table_relation: Any) -> None:
    rng = np.random.default_rng(RNG_SEED)
    v = np.concatenate([rng.normal(size=100), rng.normal(5, size=100)])
    g = ["a"] * 100 + ["b"] * 100
    rel = table_relation({"v": v.tolist(), "g": g})
    out = _run(PlotType.DENSITY, rel, {"gridSize": 32}, x=["v"], color=["g"])
    colors = set(out.table.column("color").to_pylist())
    assert colors == {"a", "b"}


# ---------------------------------------------------------------- box


def test_box_exact_quartiles_and_outlier(table_relation: Any) -> None:
    # 0..10 plus one extreme outlier.
    values = [*range(11), 1000.0]
    rel = table_relation({"v": [float(x) for x in values]})
    out = _run(PlotType.BOX, rel, x=[], y=["v"])
    row = out.table.to_pylist()[0]
    assert row["x"] == "All"
    expected_q1, expected_med, expected_q3 = np.quantile(values, [0.25, 0.5, 0.75], method="linear")
    assert row["q1"] == pytest.approx(expected_q1)
    assert row["median"] == pytest.approx(expected_med)
    assert row["q3"] == pytest.approx(expected_q3)
    outliers = out.params["outliers"]
    assert isinstance(outliers, list)
    assert [o["value"] for o in outliers] == [1000.0]
    assert row["hi"] < 1000.0


def test_box_by_group(table_relation: Any) -> None:
    rel = table_relation(
        {"v": [1.0, 2.0, 3.0, 10.0, 20.0, 30.0], "g": ["a", "a", "a", "b", "b", "b"]}
    )
    out = _run(PlotType.BOX, rel, x=["g"], y=["v"])
    rows = out.table.to_pylist()
    by_group = {row["x"]: row for row in rows}
    assert by_group["a"]["median"] == pytest.approx(2.0)
    assert by_group["b"]["median"] == pytest.approx(20.0)
    # Deterministic category order (the summary query has an explicit ORDER BY).
    assert [row["x"] for row in rows] == sorted(row["x"] for row in rows)


def test_box_groups_null_category_separately_and_matches_outliers(table_relation: Any) -> None:
    # A NULL group must be its own box (an inner JOIN without a NULL-safe condition would drop
    # it entirely), and its outliers must carry the same (null) category as its summary row.
    rel = table_relation(
        {
            "v": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 1000.0],
            "g": [None, None, None, "a", "a", "a", "a", "a", "a"],
        }
    )
    out = _run(PlotType.BOX, rel, x=["g"], y=["v"])
    rows = {row["x"]: row for row in out.table.to_pylist()}
    assert set(rows) == {None, "a"}
    assert rows[None]["median"] == pytest.approx(2.0)
    outliers = out.params["outliers"]
    assert isinstance(outliers, list)
    assert [o["x"] for o in outliers] == ["a"]


def test_box_outlier_values_are_not_double_counted_with_two_groups(table_relation: Any) -> None:
    # Regression check for the join-based rewrite: each group's fences must come from its own
    # quartiles, not another group's.
    rel = table_relation(
        {"v": [1.0, 2.0, 3.0, 100.0, 200.0, 300.0], "g": ["a", "a", "a", "b", "b", "b"]}
    )
    out = _run(PlotType.BOX, rel, x=["g"], y=["v"])
    assert out.params["outliers"] == []


# ---------------------------------------------------------------- violin


def test_violin_kde_matches_kde_by_group_kernel(table_relation: Any) -> None:
    rng = np.random.default_rng(RNG_SEED)
    v = np.concatenate([rng.normal(size=60), rng.normal(5, size=60)])
    g = ["a"] * 60 + ["b"] * 60
    rel = table_relation({"v": v.tolist(), "g": g})
    out = _run(PlotType.VIOLIN, rel, {"gridSize": 32}, x=["g"], y=["v"])
    assert set(out.table.column_names) == {"x", "y", "density", "n"}
    assert set(out.table.column("x").to_pylist()) == {"a", "b"}
    assert all(d >= 0 for d in out.table.column("density").to_pylist())
    assert out.reduction == Reduction.NONE

    from viz.stats.kde import kde_by_group

    expected = kde_by_group(v, np.array(g, dtype=object), grid_size=32, common_grid=True)
    rows = out.table.to_pylist()
    by_group: dict[object, list[float]] = {}
    for row in rows:
        by_group.setdefault(row["x"], []).append(row["density"])
    for curve in expected.curves:
        np.testing.assert_allclose(by_group[curve.key], curve.curve.density)


def test_violin_over_cap_is_sampled(table_relation: Any) -> None:
    from viz.stats.sampling import KDE_CAP

    rel = table_relation({"v": list(range(KDE_CAP + 500)), "g": ["a"] * (KDE_CAP + 500)})
    out = _run(PlotType.VIOLIN, rel, x=["g"], y=["v"])
    assert out.reduction == Reduction.SAMPLED
    assert out.table.column("n").to_pylist()[0] <= KDE_CAP


def test_violin_clamps_grid_size_to_the_mark_cap(table_relation: Any) -> None:
    # Rows are groups x grid_size; requesting the max gridSize (2048) with several groups would
    # otherwise return far more rows than the mark cap, and the generic cap guard would slice
    # the table mid-curve, cutting the last kept group's polygon off partway through.
    groups = [f"g{i}" for i in range(10)]
    rel = table_relation(
        {"v": [float(i) for i in range(100)], "g": [groups[i % 10] for i in range(100)]}
    )
    out = _run(PlotType.VIOLIN, rel, {"gridSize": 2048}, x=["g"], y=["v"], mark_cap=500)
    assert out.table.num_rows <= 500
    # Every group is still present (just at a coarser resolution), not truncated away.
    assert set(out.table.column("x").to_pylist()) == set(groups)


def test_violin_grid_size_clamp_handles_a_null_group(table_relation: Any) -> None:
    # The grid-size clamp counted distinct groups with np.unique, which sorts its input and
    # raises a TypeError comparing None to str/bool - reservoir_sample keeps NULL as its own
    # group, so any violin grouped by a column with at least one null value crashed.
    g = [None] * 4 + ["a"] * 4 + ["b"] * 4
    rel = table_relation({"v": [float(i) for i in range(12)], "g": g})
    out = _run(PlotType.VIOLIN, rel, {"gridSize": 2048}, x=["g"], y=["v"], mark_cap=300)
    assert set(out.table.column("x").to_pylist()) == {None, "a", "b"}


# ---------------------------------------------------------------- strip / swarm


@pytest.mark.parametrize("plot_type", [PlotType.STRIP, PlotType.SWARM])
def test_points_under_cap_are_not_reduced(table_relation: Any, plot_type: PlotType) -> None:
    rel = table_relation({"v": [1.0, 2.0, 3.0], "g": ["a", "b", "a"]})
    out = _run(plot_type, rel, x=["g"], y=["v"], mark_cap=1000)
    assert out.reduction == Reduction.NONE
    assert out.table.num_rows == 3


@pytest.mark.parametrize("plot_type", [PlotType.STRIP, PlotType.SWARM])
def test_points_over_cap_are_sampled(table_relation: Any, plot_type: PlotType) -> None:
    rel = table_relation({"v": list(range(500)), "g": ["a"] * 500})
    out = _run(plot_type, rel, x=["g"], y=["v"], mark_cap=50)
    assert out.reduction == Reduction.SAMPLED
    assert out.table.num_rows <= 50


def test_strip_without_x_defaults_to_single_category(table_relation: Any) -> None:
    rel = table_relation({"v": [1.0, 2.0, 3.0]})
    out = _run(PlotType.STRIP, rel, x=[], y=["v"])
    assert out.table.column("x").to_pylist() == ["All", "All", "All"]


# ---------------------------------------------------------------- Q-Q


def test_qq_matches_scipy_probplot(table_relation: Any) -> None:
    values = np.random.default_rng(RNG_SEED).normal(size=50)
    rel = table_relation({"v": values.tolist()})
    out = _run(PlotType.QQ, rel, x=["v"])
    expected = sps.probplot(values, dist="norm")
    np.testing.assert_allclose(sorted(out.table.column("y").to_pylist()), np.sort(values))
    assert out.params["slope"] is not None
    assert out.reduction == Reduction.NONE
    # probplot's osm (theoretical quantiles) match ours up to the plotting-position convention.
    np.testing.assert_allclose(
        np.sort(out.table.column("x").to_numpy()), np.sort(expected[0][0]), rtol=1e-6
    )


# ---------------------------------------------------------------- ECDF


def test_ecdf_exact_steps(table_relation: Any) -> None:
    rel = table_relation({"v": [1.0, 2.0, 2.0, 3.0]})
    out = _run(PlotType.ECDF, rel, x=["v"])
    assert out.reduction == Reduction.NONE
    rows = out.table.to_pylist()
    assert rows[0] == {"x": 1.0, "y": pytest.approx(0.25)}
    assert rows[1] == {"x": 2.0, "y": pytest.approx(0.75)}
    assert rows[2] == {"x": 3.0, "y": pytest.approx(1.0)}


def test_ecdf_grouped(table_relation: Any) -> None:
    rel = table_relation({"v": [1.0, 2.0, 3.0, 4.0], "g": ["a", "a", "b", "b"]})
    out = _run(PlotType.ECDF, rel, x=["v"], color=["g"])
    groups = set(out.table.column("color").to_pylist())
    assert groups == {"a", "b"}
