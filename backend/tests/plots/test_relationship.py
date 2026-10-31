from __future__ import annotations

from typing import Any

import numpy as np
import pytest

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


# ---------------------------------------------------------------- scatter / bubble


def test_scatter_under_cap_returns_raw_points(table_relation: Any) -> None:
    rel = table_relation({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]})
    out = _run(PlotType.SCATTER, rel, x=["a"], y=["b"], mark_cap=1000)
    assert out.reduction == Reduction.NONE
    assert out.table.column_names == ["x", "y"]
    assert out.table.column("x").to_pylist() == [1.0, 2.0, 3.0]


def test_scatter_over_cap_switches_to_bin2d(table_relation: Any) -> None:
    rng = np.random.default_rng(RNG_SEED)
    a = rng.normal(size=500).tolist()
    b = rng.normal(size=500).tolist()
    rel = table_relation({"a": a, "b": b})
    out = _run(PlotType.SCATTER, rel, x=["a"], y=["b"], mark_cap=50)
    assert out.reduction == Reduction.BINNED
    assert out.method == "bin2d"
    assert set(out.table.column_names) >= {"x_lo", "x_hi", "y_lo", "y_hi", "count"}
    assert sum(out.table.column("count").to_pylist()) == 500


def test_bubble_includes_size_column(table_relation: Any) -> None:
    rel = table_relation({"a": [1.0, 2.0], "b": [3.0, 4.0], "s": [10.0, 20.0]})
    out = _run(PlotType.BUBBLE, rel, x=["a"], y=["b"], size=["s"], mark_cap=1000)
    assert out.table.column_names == ["x", "y", "size"]
    assert out.table.column("size").to_pylist() == [10.0, 20.0]


def test_bubble_same_field_bound_to_two_channels_does_not_collide(table_relation: Any) -> None:
    # Binding one field to both x/size (or x/y) must not collapse into one output column.
    rel = table_relation({"a": [1.0, 2.0], "b": [3.0, 4.0]})
    out = _run(PlotType.BUBBLE, rel, x=["a"], y=["b"], size=["b"], mark_cap=1000)
    assert out.table.column_names == ["x", "y", "size"]
    assert out.table.column("y").to_pylist() == [3.0, 4.0]
    assert out.table.column("size").to_pylist() == [3.0, 4.0]


def test_scatter_same_field_for_x_and_y_does_not_collide(table_relation: Any) -> None:
    rel = table_relation({"a": [1.0, 2.0, 3.0]})
    out = _run(PlotType.SCATTER, rel, x=["a"], y=["a"], mark_cap=1000)
    assert out.table.column_names == ["x", "y"]
    assert out.table.column("x").to_pylist() == out.table.column("y").to_pylist() == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------- pair


def test_pair_returns_dimension_columns(table_relation: Any) -> None:
    rel = table_relation({"a": [1.0, 2.0, 3.0, 4.0], "b": [4.0, 3.0, 2.0, 1.0], "c": [1.0] * 4})
    out = _run(PlotType.PAIR, rel, dimensions=["a", "b", "c"])
    assert set(out.table.column_names) == {"dimension0", "dimension1", "dimension2"}
    assert out.params["dimensions"] == ["a", "b", "c"]
    assert out.table.num_rows == 4


def test_pair_caps_to_max_points(table_relation: Any) -> None:
    rel = table_relation({"a": list(range(1000)), "b": list(range(1000))})
    out = _run(PlotType.PAIR, rel, {"maxPoints": 100}, dimensions=["a", "b"])
    assert out.table.num_rows == 100
    assert out.reduction == Reduction.SAMPLED


def test_pair_respects_mark_cap_at_high_dimension_count(table_relation: Any) -> None:
    # marks drawn ~= rows x n*(n-1) off-diagonal cells; at 8 dims that's 56x, so the per-row cap
    # must shrink or a default maxPoints=20000 would draw over 1M marks against a 5000 budget.
    columns = {chr(ord("a") + i): list(range(500)) for i in range(8)}
    rel = table_relation(columns)
    out = _run(PlotType.PAIR, rel, mark_cap=5000, dimensions=list(columns))
    cells = 8 * 7
    assert out.table.num_rows * cells <= 5000


# ---------------------------------------------------------------- correlation heatmap / correlogram


def test_correlation_heatmap_perfect_correlation(table_relation: Any) -> None:
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [2.0, 4.0, 6.0, 8.0, 10.0]  # b = 2a, r = 1
    rel = table_relation({"a": a, "b": b})
    out = _run(PlotType.CORRELATION_HEATMAP, rel, dimensions=["a", "b"])
    rows = {(r["x"], r["y"]): r["value"] for r in out.table.to_pylist()}
    assert rows[("a", "b")] == pytest.approx(1.0)
    assert rows[("a", "a")] == pytest.approx(1.0)
    assert rows[("b", "a")] == pytest.approx(1.0)


def test_correlogram_includes_n_and_small_pvalue(table_relation: Any) -> None:
    a = list(range(1, 31))
    b = [x * 2 + 1 for x in a]
    rel = table_relation({"a": [float(x) for x in a], "b": [float(x) for x in b]})
    out = _run(PlotType.CORRELOGRAM, rel, dimensions=["a", "b"])
    rows = {(r["x"], r["y"]): r for r in out.table.to_pylist()}
    off_diag = rows[("a", "b")]
    assert off_diag["n"] == 30
    assert off_diag["p"] < 0.001


def test_correlation_matrix_a_single_nan_does_not_poison_the_whole_row(table_relation: Any) -> None:
    rel = table_relation(
        {
            "a": [1.0, 2.0, 3.0, 4.0, float("nan")],
            "b": [2.0, 4.0, 6.0, 8.0, 10.0],
            "c": [5.0, 4.0, 3.0, 2.0, 1.0],
        }
    )
    out = _run(PlotType.CORRELOGRAM, rel, dimensions=["a", "b", "c"])
    rows = {(r["x"], r["y"]): r for r in out.table.to_pylist()}
    # The NaN row is simply excluded from the a<->b and a<->c pairs (n=4), not NaN everywhere.
    assert rows[("a", "b")]["value"] == pytest.approx(1.0)
    assert rows[("a", "b")]["n"] == 4
    assert rows[("a", "c")]["value"] == pytest.approx(-1.0)
    # Pairs that don't involve "a" are unaffected and keep all 5 rows.
    assert rows[("b", "c")]["n"] == 5


def test_correlation_matrix_constant_column_is_null_not_nan(table_relation: Any) -> None:
    rel = table_relation({"a": [1.0, 2.0, 3.0], "b": [5.0, 5.0, 5.0]})
    out = _run(PlotType.CORRELATION_HEATMAP, rel, dimensions=["a", "b"])
    rows = {(r["x"], r["y"]): r["value"] for r in out.table.to_pylist()}
    assert rows[("a", "b")] is None


# ---------------------------------------------------------------- hexbin


def test_hexbin_counts_sum_to_n(table_relation: Any) -> None:
    rng = np.random.default_rng(RNG_SEED)
    a = rng.normal(size=300).tolist()
    b = rng.normal(size=300).tolist()
    rel = table_relation({"a": a, "b": b})
    out = _run(PlotType.HEXBIN, rel, x=["a"], y=["b"])
    assert out.reduction == Reduction.BINNED
    assert sum(out.table.column("count").to_pylist()) == 300


# ---------------------------------------------------------------- contour
# Not the official custom-contour series (see relationship.py's contour() docstring): this is a
# server-side bin2d density over the full filtered data, always binned, never sampled.


def test_contour_bins_over_the_full_data_regardless_of_cap(table_relation: Any) -> None:
    rel = table_relation({"a": [1.0, 2.0, 3.0], "b": [4.0, 5.0, 6.0]})
    out = _run(PlotType.CONTOUR, rel, {"grid": 8}, x=["a"], y=["b"], mark_cap=1000)
    assert out.reduction == Reduction.BINNED
    assert set(out.table.column_names) >= {"x_lo", "x_hi", "y_lo", "y_hi", "count"}
    assert sum(out.table.column("count").to_pylist()) == 3


def test_contour_grid_is_capped_by_mark_cap(table_relation: Any) -> None:
    rng = np.random.default_rng(RNG_SEED)
    a = rng.normal(size=1000).tolist()
    b = rng.normal(size=1000).tolist()
    rel = table_relation({"a": a, "b": b})
    out = _run(PlotType.CONTOUR, rel, {"grid": 200}, x=["a"], y=["b"], mark_cap=100)
    assert out.table.num_rows <= 100


def test_contour_same_field_for_x_and_y_does_not_collide(table_relation: Any) -> None:
    rel = table_relation({"a": [1.0, 2.0, 3.0]})
    out = _run(PlotType.CONTOUR, rel, {"grid": 8}, x=["a"], y=["a"], mark_cap=1000)
    assert sum(out.table.column("count").to_pylist()) == 3


# ---------------------------------------------------------------- regression


def test_regression_under_cap_returns_raw_points(table_relation: Any) -> None:
    rel = table_relation({"a": [1.0, 2.0, 3.0], "b": [2.0, 4.0, 6.0]})
    out = _run(PlotType.REGRESSION, rel, x=["a"], y=["b"], mark_cap=1000)
    assert out.reduction == Reduction.NONE
    assert out.table.num_rows == 3


def test_regression_over_cap_is_sampled(table_relation: Any) -> None:
    rng = np.random.default_rng(RNG_SEED)
    a = rng.normal(size=500).tolist()
    b = (2 * np.asarray(a) + rng.normal(size=500) * 0.1).tolist()
    rel = table_relation({"a": a, "b": b})
    out = _run(PlotType.REGRESSION, rel, x=["a"], y=["b"], mark_cap=50)
    assert out.reduction == Reduction.SAMPLED
    assert out.table.num_rows <= 50


def test_regression_same_field_for_x_and_y_does_not_collide(table_relation: Any) -> None:
    rel = table_relation({"a": [1.0, 2.0, 3.0]})
    out = _run(PlotType.REGRESSION, rel, x=["a"], y=["a"], mark_cap=1000)
    assert out.table.column_names == ["x", "y"]
    assert out.table.column("x").to_pylist() == out.table.column("y").to_pylist() == [1.0, 2.0, 3.0]


def test_regression_same_field_for_x_and_y_over_cap_does_not_collide(table_relation: Any) -> None:
    rel = table_relation({"a": list(range(500))})
    out = _run(PlotType.REGRESSION, rel, x=["a"], y=["a"], mark_cap=50)
    assert out.table.column_names == ["x", "y"]
    assert out.table.column("x").to_pylist() == out.table.column("y").to_pylist()
    assert out.table.num_rows <= 50


# ---------------------------------------------------------------- residual


def test_residual_of_a_perfect_line_is_near_zero(table_relation: Any) -> None:
    a = [float(x) for x in range(1, 21)]
    b = [2.0 * x + 1.0 for x in a]
    rel = table_relation({"a": a, "b": b})
    out = _run(PlotType.RESIDUAL, rel, x=["a"], y=["b"])
    residuals = out.table.column("y").to_numpy()
    np.testing.assert_allclose(residuals, np.zeros_like(residuals), atol=1e-8)
    assert out.params["slope"] == pytest.approx(2.0)
    assert out.params["intercept"] == pytest.approx(1.0)
    assert out.params["r2"] == pytest.approx(1.0)


def test_residual_with_noise_has_nonzero_spread(table_relation: Any) -> None:
    rng = np.random.default_rng(RNG_SEED)
    a = np.linspace(0, 10, 200)
    noise = rng.normal(scale=1.0, size=200)
    b = 3 * a + 2 + noise
    rel = table_relation({"a": a.tolist(), "b": b.tolist()})
    out = _run(PlotType.RESIDUAL, rel, x=["a"], y=["b"])
    residuals = out.table.column("y").to_numpy()
    assert residuals.std() > 0.1
    assert out.params["slope"] == pytest.approx(3.0, abs=0.3)


def test_residual_over_mark_cap_is_sampled_not_truncated(table_relation: Any) -> None:
    # Regression check: this data is well under MODEL_CAP (so the fit sample never looked
    # "sampled" by itself) but over mark_cap. Before the fix, the plot handed chartdata's
    # generic cap guard a MODEL_CAP-sized table, which silently truncated it to "the first
    # mark_cap rows" (biased toward however the data happens to be ordered) while this function
    # still reported `reduction: none`.
    rng = np.random.default_rng(RNG_SEED)
    n = 12_000
    a = np.arange(n, dtype=float)  # ordered, like a time axis: a biased truncation would show it
    b = 2 * a + 1 + rng.normal(scale=0.01, size=n)
    rel = table_relation({"a": a.tolist(), "b": b.tolist()})
    out = _run(PlotType.RESIDUAL, rel, x=["a"], y=["b"], mark_cap=5000)
    assert out.reduction == Reduction.SAMPLED
    assert out.table.num_rows <= 5000
    # A uniform sample of x in [0, 12000) should span most of the range: fitted ~= 1 + 2x, so the
    # max fitted value should be well above what "the first 5000 rows in file order" would give
    # (fitted maxing out around 1 + 2*5000 = 10001).
    fitted = out.table.column("x").to_numpy()
    assert fitted.max() > 15_000
