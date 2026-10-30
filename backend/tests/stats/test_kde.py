from __future__ import annotations

import time

import numpy as np
import pytest
from scipy import stats as sps

from viz.stats.errors import StatsInputError
from viz.stats.kde import bandwidth, kde, kde2d, kde_by_group


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
    result = kde2d(x, y, grid_size=(40, 30))
    assert result.density.shape == (30, 40)
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
