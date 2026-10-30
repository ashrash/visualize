from __future__ import annotations

import itertools
import time

import numpy as np
import pytest
from scipy import stats as sps

from viz.stats.errors import StatsInputError
from viz.stats.kde import (
    WIDE_RANGE_WARNING,
    bandwidth,
    cell_density,
    direct_density,
    kde,
    kde2d,
    kde_by_group,
)


def _scipy_density(x: np.ndarray, h: float, at: np.ndarray) -> np.ndarray:
    factor = h / np.std(x, ddof=1)
    return sps.gaussian_kde(x, bw_method=factor)(at)


def test_bandwidth_rules_match_r() -> None:
    x = np.arange(1, 11, dtype=float)
    # R: bw.nrd0(1:10) = 0.9 * min(sd, IQR/1.34) * 10^-0.2; sd(1:10) = 3.0277 < 4.5/1.34.
    sd = np.std(x, ddof=1)
    assert bandwidth(x, "silverman") == pytest.approx(0.9 * sd * 10**-0.2)
    assert bandwidth(x, "silverman") == pytest.approx(1.719, abs=5e-4)
    assert bandwidth(x, "scott") == pytest.approx(1.06 * sd * 10**-0.2)
    # Heavy tails: the IQR term wins.
    y = np.array([0.0, 1, 2, 3, 4, 5, 6, 7, 8, 1000])
    q1, q3 = np.quantile(y, [0.25, 0.75])
    assert bandwidth(y) == pytest.approx(0.9 * (q3 - q1) / 1.34 * 10**-0.2)
    # IQR of 0 falls back to sd.
    z = np.array([1.0] * 9 + [5.0])
    assert bandwidth(z) == pytest.approx(0.9 * np.std(z, ddof=1) * 10**-0.2)
    assert bandwidth(z, 0.5) == 0.5


@pytest.mark.parametrize("bw", ["silverman", "scott", 0.7])
def test_kde_matches_scipy(bw: str | float) -> None:
    x = np.random.default_rng(5).normal(3.0, 2.0, 4000)
    curve = kde(x, bw=bw)  # type: ignore[arg-type]
    expected = _scipy_density(x, curve.bandwidth, curve.x)
    assert np.max(np.abs(curve.density - expected)) < 1e-3 * expected.max()
    assert np.trapezoid(curve.density, curve.x) == pytest.approx(1.0, abs=1e-3)
    assert curve.n == 4000 and len(curve.x) == 512


def test_kde_bimodal_with_outliers() -> None:
    rng = np.random.default_rng(6)
    x = np.concatenate([rng.normal(0, 1, 3000), rng.normal(8, 0.5, 1000), [500.0, -400.0]])
    curve = kde(x, grid=np.linspace(-5, 12, 400))
    expected = _scipy_density(x, curve.bandwidth, curve.x)
    assert np.max(np.abs(curve.density - expected)) < 1e-3 * expected.max()


def test_kde_options() -> None:
    x = np.random.default_rng(7).exponential(1.0, 2000)
    base = kde(x)
    wider = kde(x, adjust=2.0)
    assert wider.bandwidth == pytest.approx(2 * base.bandwidth)
    clipped = kde(x, clip=(0.0, None))
    assert clipped.x.min() == 0.0
    ignored_nan = kde(np.concatenate([x, [np.nan, np.inf]]))
    np.testing.assert_allclose(ignored_nan.density, base.density)


def test_kde_degenerate_inputs() -> None:
    with pytest.raises(StatsInputError, match="at least 2"):
        kde([1.0])
    with pytest.raises(StatsInputError, match="All values are equal"):
        kde([2.0, 2.0, 2.0])
    with pytest.raises(StatsInputError, match="positive"):
        kde([1.0, 2.0], bw=-1.0)
    assert kde([2.0, 2.0, 2.0], bw=0.5).density.max() > 0


def test_kde_by_group_common_grid_and_skips() -> None:
    rng = np.random.default_rng(8)
    values = np.concatenate([rng.normal(0, 1, 500), rng.normal(5, 2, 300), [1.0], [4.0, 4.0]])
    groups = ["a"] * 500 + ["b"] * 300 + ["tiny"] + [None, None]
    result = kde_by_group(values, groups)
    assert [c.key for c in result.curves] == ["a", "b"]
    assert [c.n for c in result.curves] == [500, 300]
    np.testing.assert_array_equal(result.curves[0].curve.x, result.curves[1].curve.x)
    for c in result.curves:
        part = values[np.array(groups, dtype=object) == c.key]
        assert c.curve.bandwidth == pytest.approx(bandwidth(part))
        expected = _scipy_density(part, c.curve.bandwidth, c.curve.x)
        assert np.max(np.abs(c.curve.density - expected)) < 1e-3 * expected.max()
    assert result.warnings == [
        "Group 'tiny' has fewer than 2 values; no density.",
        "Group 'null' has only one distinct value; no density.",
    ]
    pooled = kde_by_group(values[:800], groups[:800], common_bw=True)
    assert pooled.curves[0].curve.bandwidth == pooled.curves[1].curve.bandwidth


def test_kde2d_matches_direct_sum() -> None:
    rng = np.random.default_rng(9)
    x = rng.normal(0, 1, 600)
    y = 0.5 * x + rng.normal(0, 0.5, 600)
    result = kde2d(x, y, grid_size=(120, 90))
    assert result.density.shape == (90, 120) and result.method == "binned"
    xx, yy = np.meshgrid(result.xs, result.ys)
    direct = np.zeros_like(xx)
    for xi, yi in zip(x, y, strict=True):
        direct += sps.norm.pdf(xx, xi, result.bw_x) * sps.norm.pdf(yy, yi, result.bw_y)
    direct /= len(x)
    assert np.max(np.abs(result.density - direct)) < 2e-3 * direct.max()
    dx, dy = np.diff(result.xs)[0], np.diff(result.ys)[0]
    assert result.density.sum() * dx * dy == pytest.approx(1.0, abs=0.01)
    assert result.bw_x == pytest.approx(bandwidth(x)) and result.bw_y == pytest.approx(bandwidth(y))


def test_kde_is_fast_enough() -> None:
    x = np.random.default_rng(10).normal(size=100_000)
    started = time.perf_counter()
    kde(x)
    one = time.perf_counter() - started
    groups = np.repeat(np.arange(20), 5000)
    started = time.perf_counter()
    kde_by_group(np.random.default_rng(11).normal(size=100_000), groups)
    twenty = time.perf_counter() - started
    assert one < 0.5 and twenty < 2.0, (one, twenty)


# ---------------------------------------------------------------- review regressions


def _exact_mass(x: np.ndarray, h: float, lo: float, hi: float) -> float:
    return float(np.mean(sps.norm.cdf((hi - x) / h) - sps.norm.cdf((lo - x) / h)))


@pytest.mark.parametrize("sigma", [1.5, 2.0, 3.0, 4.0])
def test_kde_mass_on_skewed_data(sigma: float) -> None:
    """Lognormal data: the range is up to ~1e6 bandwidths wide (the fine-grid cap binds)."""
    x = np.random.default_rng(13).lognormal(0.0, sigma, 100_000)
    h = bandwidth(x)
    grid = np.linspace(x.min() - 3 * h, np.quantile(x, 0.99), 200_001)
    curve = kde(x, grid=grid)
    mass = np.trapezoid(curve.density, grid)
    assert mass == pytest.approx(_exact_mass(x, h, grid[0], grid[-1]), abs=2e-3), sigma
    # The default 512-point grid can't resolve the bandwidth: cell averages keep the mass.
    default = kde(x)
    assert default.method == "cell_average" and default.warnings == [WIDE_RANGE_WARNING]
    step = default.x[1] - default.x[0]
    assert default.density.sum() * step == pytest.approx(1.0, abs=1e-3), sigma
    # The peak cell holds the mass the exact KDE puts there.
    top = int(np.argmax(default.density))
    lo, hi = default.x[top] - step / 2, default.x[top] + step / 2
    assert default.density[top] * step == pytest.approx(_exact_mass(x, h, lo, hi), rel=1e-3)


def test_shared_grid_uses_one_method_and_keeps_mass() -> None:
    rng = np.random.default_rng(17)
    values = np.concatenate([rng.lognormal(0.0, 2.0, 3000), rng.normal(5.0, 1.0, 3000)])
    groups = ["lognormal"] * 3000 + ["normal"] * 3000
    result = kde_by_group(values, groups)
    assert {c.curve.method for c in result.curves} == {"cell_average"}
    for c in result.curves:
        step = c.curve.x[1] - c.curve.x[0]
        assert c.curve.density.sum() * step == pytest.approx(1.0, abs=2e-3), c.key
    fine = kde_by_group(rng.normal(size=4000), ["a"] * 2000 + ["b"] * 2000)
    assert {c.curve.method for c in fine.curves} == {"binned"} and fine.warnings == []


def test_duplicate_grid_points() -> None:
    x = np.random.default_rng(18).lognormal(0.0, 3.0, 5000)
    h = bandwidth(x)
    points = np.array([1.0, 1.0, 2.0, 0.5, 2.0])
    out = cell_density(x, points, h)
    assert np.all(np.isfinite(out))
    assert out[0] == out[1] and out[2] == out[4]
    np.testing.assert_allclose(out[[3, 0, 2]], cell_density(x, np.array([0.5, 1.0, 2.0]), h))
    curve = kde(x, grid=points)
    assert np.all(np.isfinite(curve.density))


def test_direct_and_cell_densities_are_exact() -> None:
    rng = np.random.default_rng(14)
    x = rng.lognormal(0.0, 3.0, 20_000)
    h = bandwidth(x)
    points = np.concatenate([rng.choice(x, 200), np.linspace(0, 50, 50), [1e6]])
    exact = np.array([np.mean(sps.norm.pdf((p - x) / h)) / h for p in points])
    np.testing.assert_allclose(direct_density(x, points, h), exact, rtol=1e-6, atol=1e-12)
    # Cell averages: mass between midpoints of neighbouring (sorted) points over the width.
    coarse = np.array([-5.0, 0.5, 2.0, 10.0, 300.0])
    edges = np.array([-7.75, -2.25, 1.25, 6.0, 155.0, 445.0])
    mass = [_exact_mass(x, h, a, b) for a, b in itertools.pairwise(edges)]
    expected = np.array(mass) / np.diff(edges)
    np.testing.assert_allclose(cell_density(x, coarse[::-1], h), expected[::-1], rtol=1e-6)
    # On a grid much finer than the bandwidth, cell averages are point densities.
    fine = np.linspace(1.0, 3.0, 2001)
    np.testing.assert_allclose(cell_density(x, fine, h), direct_density(x, fine, h), rtol=2e-4)


def test_kde2d_on_skewed_data() -> None:
    rng = np.random.default_rng(15)
    x = rng.lognormal(0.0, 3.0, 20_000)
    y = rng.normal(0.0, 1.0, 20_000)
    result = kde2d(x, y, grid_size=(60, 40))
    assert result.method == "cell_average" and result.warnings == [WIDE_RANGE_WARNING]
    dx, dy = result.xs[1] - result.xs[0], result.ys[1] - result.ys[0]
    assert result.density.sum() * dx * dy == pytest.approx(1.0, abs=1e-3)
    # Exact: the mass of cell (i, j) over its area, from per-axis normal probabilities.
    for i, j in ((0, 20), (1, 10), (30, 25)):
        px = sps.norm.cdf((result.xs[i] + dx / 2 - x) / result.bw_x) - sps.norm.cdf(
            (result.xs[i] - dx / 2 - x) / result.bw_x
        )
        py = sps.norm.cdf((result.ys[j] + dy / 2 - y) / result.bw_y) - sps.norm.cdf(
            (result.ys[j] - dy / 2 - y) / result.bw_y
        )
        assert result.density[j, i] == pytest.approx(np.mean(px * py) / (dx * dy), rel=1e-6)
    # Moderately skewed (no cap): the binned estimate still integrates to 1.
    mild = kde2d(rng.gamma(2.0, 1.0, 2000), rng.normal(0.0, 1.0, 2000), grid_size=(200, 100))
    assert mild.method == "binned"
    cell = (mild.xs[1] - mild.xs[0]) * (mild.ys[1] - mild.ys[0])
    assert mild.density.sum() * cell == pytest.approx(1.0, abs=0.02)


def test_grouped_kde_reports_wide_ranges() -> None:
    rng = np.random.default_rng(16)
    values = np.concatenate([rng.lognormal(0, 4, 5000), rng.normal(size=5000)])
    groups = ["skewed"] * 5000 + ["normal"] * 5000
    result = kde_by_group(values, groups)
    assert WIDE_RANGE_WARNING in result.warnings
