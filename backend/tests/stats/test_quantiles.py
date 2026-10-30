from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from scipy import stats as sps

from viz.stats.errors import StatsInputError
from viz.stats.quantiles import bin_edges, ecdf, filliben, histogram, nice_width, qq, quantiles

RNG_SEED = 20261030


def _values_sql(values: np.ndarray, groups: list[str] | None = None) -> str:
    """A SELECT over literal rows; NaN, inf and None are written as such."""
    rows = []
    for i, v in enumerate(values.tolist()):
        if v is None:
            text = "NULL"
        elif np.isnan(v):
            text = "'nan'::DOUBLE"
        elif np.isinf(v):
            text = "'inf'::DOUBLE" if v > 0 else "'-inf'::DOUBLE"
        else:
            text = f"{v!r}::DOUBLE"
        group = f", '{groups[i]}'" if groups else ""
        rows.append(f"({text}{group})")
    cols = "x, g" if groups else "x"
    return f"SELECT * FROM (VALUES {', '.join(rows)}) t({cols})"


@pytest.fixture
def gamma() -> np.ndarray:
    return np.random.default_rng(RNG_SEED).gamma(2.0, 3.0, 400)


def test_quantiles_match_numpy(make_relation: Any, gamma: np.ndarray) -> None:
    dirty = np.concatenate([gamma, [np.nan, np.inf, -np.inf]])
    rel = make_relation(_values_sql(dirty))
    probs = [0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0]
    for method in ("linear", "inverted_cdf"):
        table = quantiles(rel, "x", probs, method=method).table  # type: ignore[arg-type]
        assert table.column("p").to_pylist() == probs
        np.testing.assert_allclose(
            table.column("value").to_pylist(),
            np.quantile(gamma, probs, method=method),
            rtol=1e-12,
        )


def test_quantiles_by_group(make_relation: Any, gamma: np.ndarray) -> None:
    groups = ["a" if v < 5 else "b" for v in gamma]
    rel = make_relation(_values_sql(gamma, groups))
    table = quantiles(rel, "x", [0.1, 0.9], by=["g"]).table
    rows = table.to_pylist()
    for g in ("a", "b"):
        expected = np.quantile(gamma[np.array(groups) == g], [0.1, 0.9])
        got = [r["value"] for r in rows if r["g"] == g]
        np.testing.assert_allclose(got, expected, rtol=1e-12)


def test_quantiles_of_dates(make_relation: Any) -> None:
    rel = make_relation(
        "SELECT * FROM (VALUES (DATE '2024-01-01'), (DATE '2024-01-03'), (DATE '2024-01-09')) t(d)"
    )
    table = quantiles(rel, "d", [0.5], method="inverted_cdf").table
    assert str(table.column("value")[0].as_py()) == "2024-01-03"


def test_quantile_inputs_are_checked(make_relation: Any, gamma: np.ndarray) -> None:
    rel = make_relation(_values_sql(gamma, ["a"] * len(gamma)))
    with pytest.raises(StatsInputError, match="between 0 and 1"):
        quantiles(rel, "x", [1.5])
    with pytest.raises(StatsInputError, match="must be a date/time or numeric field"):
        quantiles(rel, "g", [0.5])
    with pytest.raises(StatsInputError, match="Unknown field"):
        quantiles(rel, "x", [0.5], by=["nope"])


def test_ecdf_exact_matches_scipy(make_relation: Any) -> None:
    x = np.random.default_rng(1).integers(0, 30, 300).astype(float)
    rel = make_relation(_values_sql(np.concatenate([x, [np.nan]])))
    result = ecdf(rel, "x", max_points=100)
    expected = sps.ecdf(x).cdf
    assert result.exact and result.n == 300
    np.testing.assert_array_equal(result.table.column("x").to_numpy(), expected.quantiles)
    np.testing.assert_allclose(result.table.column("p").to_numpy(), expected.probabilities)


def test_ecdf_grid_points_lie_on_the_step_curve(make_relation: Any) -> None:
    x = np.random.default_rng(2).normal(size=5000)
    rel = make_relation(_values_sql(x))
    result = ecdf(rel, "x", max_points=100)
    assert not result.exact and result.table.num_rows == 101
    sorted_x = np.sort(x)
    for xv, p in zip(
        result.table.column("x").to_pylist(), result.table.column("p").to_pylist(), strict=True
    ):
        above = np.searchsorted(sorted_x, xv, side="right") / len(x)  # F(x)
        below = np.searchsorted(sorted_x, xv, side="left") / len(x)  # F(x-)
        assert above >= p - 1e-12 and below < p + 1e-12


def test_ecdf_by_group(make_relation: Any) -> None:
    x = np.array([1.0, 2.0, 2.0, 5.0, 1.0, 3.0])
    rel = make_relation(_values_sql(x, ["a", "a", "a", "a", "b", "b"]))
    rows = ecdf(rel, "x", by=["g"]).table.to_pylist()
    assert rows == [
        {"g": "a", "x": 1.0, "p": 0.25},
        {"g": "a", "x": 2.0, "p": 0.75},
        {"g": "a", "x": 5.0, "p": 1.0},
        {"g": "b", "x": 1.0, "p": 0.5},
        {"g": "b", "x": 3.0, "p": 1.0},
    ]


@pytest.mark.parametrize("rule", ["auto", "fd", "sturges", "scott", "sqrt"])
def test_bin_edges_match_numpy_rules(rule: str, gamma: np.ndarray) -> None:
    q1, q3 = np.percentile(gamma, [25, 75])
    edges = bin_edges(
        len(gamma),
        float(gamma.min()),
        float(gamma.max()),
        iqr=float(q3 - q1),
        std=float(np.std(gamma)),
        rule=rule,  # type: ignore[arg-type]
        nice=False,
    )
    np.testing.assert_allclose(edges.edges, np.histogram_bin_edges(gamma, bins=rule), rtol=1e-12)


def test_bin_edges_with_count_and_width() -> None:
    np.testing.assert_allclose(bin_edges(10, 0.0, 9.0, bins=3, nice=False).edges, [0, 3, 6, 9])
    assert bin_edges(10, 0.0, 9.0, width=4.0, nice=False).edges.tolist() == [0, 4, 8, 12]
    assert bin_edges(10, 0.3, 9.1, width=2.0).edges.tolist() == [0, 2, 4, 6, 8, 10]


@pytest.mark.parametrize(
    ("raw", "integer", "expected"),
    [(0.37, False, 0.5), (1.2, False, 2.0), (2.3, False, 2.5), (7.0, False, 10.0),
     (0.0042, False, 0.005), (2.3, True, 5.0), (0.4, True, 1.0), (13.0, True, 20.0)],
)  # fmt: skip
def test_nice_width(raw: float, integer: bool, expected: float) -> None:
    assert nice_width(raw, integer=integer) == pytest.approx(expected)


def test_nice_edges_cover_the_data(gamma: np.ndarray) -> None:
    result = bin_edges(len(gamma), float(gamma.min()), float(gamma.max()), iqr=3.0, rule="fd")
    assert result.edges[0] <= gamma.min() and result.edges[-1] >= gamma.max()
    steps = np.diff(result.edges)
    np.testing.assert_allclose(steps, result.width)
    assert result.edges[0] / result.width == pytest.approx(round(result.edges[0] / result.width))


def test_bin_edges_degenerate_inputs() -> None:
    assert bin_edges(5, 3.0, 3.0, iqr=0.0, std=0.0, nice=False).edges.tolist() == [2.5, 3.5]
    capped = bin_edges(10**6, 0.0, 1.0, width=1e-9, nice=False, max_bins=50)
    assert capped.capped and len(capped.edges) == 51
    with pytest.raises(StatsInputError, match="No values"):
        bin_edges(0, 0.0, 1.0)
    with pytest.raises(StatsInputError, match="positive"):
        bin_edges(3, 0.0, 1.0, width=0.0)


def test_histogram_counts_match_numpy(make_relation: Any, gamma: np.ndarray) -> None:
    dirty = np.concatenate([gamma, [np.nan, None, np.inf]])  # type: ignore[list-item]
    rel = make_relation(_values_sql(dirty.astype(object)))
    result = histogram(rel, "x")
    counts, _ = np.histogram(gamma, bins=result.edges)
    assert result.table.column("count").to_pylist() == counts.tolist()
    assert result.table.column("binStart").to_pylist() == result.edges[:-1].tolist()
    assert (result.n, result.n_missing, result.n_outside) == (400, 3, 0)


def test_histogram_values_on_edges(make_relation: Any) -> None:
    x = np.array([0, 1, 1, 2, 2, 2, 3, 4, 4, 10], dtype=float)
    rel = make_relation(_values_sql(x))
    for kwargs in ({"width": 1.0}, {"width": 2.0}, {"bins": 3, "nice": False}, {"bins": 7}):
        result = histogram(rel, "x", **kwargs)  # type: ignore[arg-type]
        counts, _ = np.histogram(x, bins=result.edges)
        assert result.table.column("count").to_pylist() == counts.tolist(), kwargs


def test_histogram_tenths_on_edges(make_relation: Any) -> None:
    x = np.round(np.arange(0, 3.01, 0.1), 10)
    rel = make_relation(_values_sql(x))
    result = histogram(rel, "x", width=0.1, nice=False)
    counts, _ = np.histogram(x, bins=result.edges)
    assert result.table.column("count").to_pylist() == counts.tolist()


def test_histogram_by_group_zero_fills(make_relation: Any) -> None:
    x = np.array([0.5, 1.5, 9.5, 0.2])
    rel = make_relation(_values_sql(x, ["a", "a", "a", "b"]))
    result = histogram(rel, "x", width=1.0, by=["g"])
    rows = result.table.to_pylist()
    k = len(result.edges) - 1
    assert len(rows) == 2 * k
    b_counts = [r["count"] for r in rows if r["g"] == "b"]
    assert sum(b_counts) == 1 and b_counts[0] == 1


def test_histogram_range_drops_outside_values(make_relation: Any, gamma: np.ndarray) -> None:
    rel = make_relation(_values_sql(gamma))
    result = histogram(rel, "x", range=(2.0, 8.0), bins=6, nice=False)
    counts, edges = np.histogram(gamma, bins=6, range=(2.0, 8.0))
    np.testing.assert_allclose(result.edges, edges)
    assert result.table.column("count").to_pylist() == counts.tolist()
    outside = int(((gamma < 2.0) | (gamma > 8.0)).sum())
    assert result.n_outside == outside
    assert result.warnings == [f"{outside:,} values outside the range were left out."]


def test_histogram_rejects_bad_input(make_relation: Any) -> None:
    rel = make_relation("SELECT * FROM (VALUES ('a', NULL::DOUBLE)) t(s, x)")
    with pytest.raises(StatsInputError, match="must be a numeric field"):
        histogram(rel, "s")
    with pytest.raises(StatsInputError, match="no values to bin"):
        histogram(rel, "x")


def test_qq_matches_probplot(make_relation: Any, gamma: np.ndarray) -> None:
    rel = make_relation(_values_sql(gamma))
    result = qq(rel, "x", line="fit")
    (osm, osr), (slope, intercept, r) = sps.probplot(gamma, dist="norm", fit=True)
    np.testing.assert_allclose(result.theoretical, osm, rtol=1e-12)
    np.testing.assert_allclose(result.sample, osr, rtol=1e-12)
    assert result.slope == pytest.approx(slope, rel=1e-10)
    assert result.intercept == pytest.approx(intercept, rel=1e-10)
    assert result.r == pytest.approx(r, rel=1e-10)
    assert result.exact and result.n == 400


def test_qq_quartile_line_and_shape(make_relation: Any, gamma: np.ndarray) -> None:
    rel = make_relation(_values_sql(gamma))
    result = qq(rel, "x", dist="t", shape=(5,))
    (osm, _), _ = sps.probplot(gamma, sparams=(5,), dist="t")
    np.testing.assert_allclose(result.theoretical, osm, rtol=1e-12)
    q1, q3 = np.quantile(gamma, [0.25, 0.75])
    t1, t3 = sps.t(5).ppf([0.25, 0.75])
    assert result.slope == pytest.approx((q3 - q1) / (t3 - t1), rel=1e-12)
    assert result.intercept == pytest.approx(q1 - result.slope * t1, rel=1e-12)


def test_qq_order_statistics_above_max_points(make_relation: Any) -> None:
    x = np.random.default_rng(3).normal(10, 2, 3000)
    rel = make_relation(_values_sql(x))
    result = qq(rel, "x", max_points=200)
    ranks = np.unique(np.round(np.linspace(1, 3000, 200)).astype(int))
    assert not result.exact and len(result.sample) == len(ranks)
    np.testing.assert_array_equal(result.sample, np.sort(x)[ranks - 1])
    np.testing.assert_allclose(result.theoretical, sps.norm.ppf(filliben(ranks, 3000)))


def test_qq_rejects_bad_input(make_relation: Any) -> None:
    rel = make_relation(_values_sql(np.array([1.0])))
    with pytest.raises(StatsInputError, match="at least 2 values"):
        qq(rel, "x")
    with pytest.raises(StatsInputError, match="Unknown distribution"):
        qq(rel, "x", dist="os")
    with pytest.raises(StatsInputError, match="shape parameters: df"):
        qq(rel, "x", dist="t")


# ---------------------------------------------------------------- review regressions


@pytest.mark.parametrize(
    ("name", "values", "kwargs", "expected_bins"),
    [
        ("ratings 1-5", np.random.default_rng(0).integers(1, 6, 10_000), {}, 5),
        ("dice 1-6", np.random.default_rng(1).integers(1, 7, 100), {}, 6),
        (
            "ages 0-100 width 5",
            np.random.default_rng(2).integers(0, 101, 5_000),
            {"width": 5.0},
            21,
        ),
    ],
)
def test_integer_histograms_give_the_top_value_its_own_bin(
    table_relation: Any, name: str, values: np.ndarray, kwargs: dict[str, Any], expected_bins: int
) -> None:
    import pyarrow as pa

    result = histogram(table_relation({"v": pa.array(values, type=pa.int64())}), "v", **kwargs)
    rows = result.table.to_pylist()
    assert len(rows) == expected_bins, name
    top = int(values.max())
    assert rows[-1]["binStart"] == top and rows[-1]["binEnd"] > top
    assert rows[-1]["count"] == int((values == top).sum())
    counts, _ = np.histogram(values, bins=result.edges)
    assert [r["count"] for r in rows] == counts.tolist()
    if not kwargs:  # width 1: one bar per integer value
        assert [r["count"] for r in rows] == [
            int((values == v).sum()) for v in range(values.min(), top + 1)
        ]


def test_constant_integer_histogram(table_relation: Any) -> None:
    import pyarrow as pa

    result = histogram(table_relation({"v": pa.array([7, 7, 7], type=pa.int64())}), "v")
    assert result.table.to_pylist() == [{"binStart": 7.0, "binEnd": 8.0, "count": 3}]


def test_histogram_rejects_overflowing_ranges(make_relation: Any) -> None:
    rel = make_relation(_values_sql(np.array([-1.7e308, 0.0, 1.7e308])))
    with pytest.raises(StatsInputError, match=r"too wide a range|too large"):
        histogram(rel, "x")
    with pytest.raises(StatsInputError, match="too wide a range"):
        bin_edges(3, -1.7e308, 1.7e308)


def test_fd_fallback_reports_sturges() -> None:
    edges = bin_edges(10, 0.0, 10.0, iqr=0.0, std=1.0, rule="fd", nice=False)
    assert edges.rule == "sturges"
    assert len(edges.edges) - 1 == int(np.ceil(10 / (10 / (np.log2(10) + 1))))


def test_grouped_kernels_cap_the_number_of_groups(table_relation: Any) -> None:
    import pyarrow as pa

    groups = [f"g{i:02d}" for i in range(30) for _ in range(i + 1)]  # g29 is the largest
    values = np.arange(len(groups), dtype=float)
    rel = table_relation({"g": pa.array(groups), "x": values})
    hist = histogram(rel, "x", by=["g"], max_groups=5)
    kept = {r["g"] for r in hist.table.to_pylist()}
    assert kept == {"g25", "g26", "g27", "g28", "g29"}
    assert "Showing the 5 largest of 30 groups." in hist.warnings
    assert hist.n == sum(range(26, 31))
    q = quantiles(rel, "x", [0.5], by=["g"], max_groups=3)
    assert {r["g"] for r in q.table.to_pylist()} == {"g27", "g28", "g29"}
    assert q.warnings == ["Showing the 3 largest of 30 groups."]
    e = ecdf(rel, "x", by=["g"], max_groups=2)
    assert {r["g"] for r in e.table.to_pylist()} == {"g28", "g29"} and e.warnings


def test_approximate_ecdf_starts_at_the_minimum(make_relation: Any) -> None:
    x = np.random.default_rng(3).normal(size=3000)
    result = ecdf(make_relation(_values_sql(x)), "x", max_points=50)
    first = result.table.to_pylist()[0]
    assert first == {"x": x.min(), "p": pytest.approx(1 / 3000)}


@pytest.mark.parametrize("shape", [(-1.0,), (0.0,), (float("nan"),), (float("inf"),)])
def test_qq_rejects_bad_shape_parameters(make_relation: Any, shape: tuple[float]) -> None:
    rel = make_relation(_values_sql(np.arange(10.0)))
    with pytest.raises(StatsInputError, match="Shape parameters must be positive"):
        qq(rel, "x", dist="t", shape=shape)
