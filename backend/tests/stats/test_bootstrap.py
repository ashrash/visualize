from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from scipy import stats as sps

from viz.stats.bootstrap import bootstrap_ci, quantile
from viz.stats.errors import StatsInputError

# Efron & Tibshirani (1993), An Introduction to the Bootstrap, Table 3.1: law school data.
LSAT = np.array([576, 635, 558, 578, 666, 580, 555, 661, 651, 605, 653, 575, 545, 572, 594.0])
GPA = np.array(
    [3.39, 3.30, 2.81, 3.03, 3.44, 3.07, 3.00, 3.43, 3.36, 3.13, 3.12, 2.74, 2.76, 2.88, 2.96]
)


def test_law_school_correlation() -> None:
    result = bootstrap_ci((LSAT, GPA), "pearson_r", n_resamples=4000, seed=1)
    assert result.estimate == pytest.approx(0.7764, abs=5e-5)
    # The book reports bootstrap standard errors of about 0.13 (0.132 with B = 3200).
    assert 0.11 < result.se < 0.15
    assert result.method == "bca" and result.n == 15
    assert result.low < result.estimate < result.high < 1.0


def test_mean_interval_is_close_to_the_t_interval() -> None:
    x = np.random.default_rng(2).normal(10.0, 3.0, 200)
    result = bootstrap_ci(x, "mean", seed=3)
    low, high = sps.t.interval(0.95, len(x) - 1, loc=x.mean(), scale=sps.sem(x))
    half = (high - low) / 2
    assert abs(result.low - low) < 0.15 * half and abs(result.high - high) < 0.15 * half
    assert result.estimate == pytest.approx(x.mean())
    assert result.se == pytest.approx(sps.sem(x), rel=0.15)


def test_coverage_is_near_nominal() -> None:
    rng = np.random.default_rng(4)
    hits = 0
    reps = 200
    for i in range(reps):
        x = rng.normal(0.0, 1.0, 40)
        result = bootstrap_ci(x, "mean", n_resamples=999, seed=i)
        hits += result.low <= 0.0 <= result.high
    assert 0.88 <= hits / reps <= 0.99


def test_method_selection_and_named_statistics() -> None:
    big = np.random.default_rng(5).exponential(size=20_000)
    result = bootstrap_ci(big, "median", n_resamples=200)
    assert result.method == "percentile"
    assert result.low < np.median(big) < result.high
    q90 = bootstrap_ci(big[:500], quantile(0.9), n_resamples=500)
    assert q90.estimate == pytest.approx(np.quantile(big[:500], 0.9))
    trimmed = bootstrap_ci(big[:500], "trimmed_mean", n_resamples=500, method="basic")
    assert trimmed.method == "basic"
    assert trimmed.estimate == pytest.approx(sps.trim_mean(big[:500], 0.1))


def test_seeded_and_executor_runs_agree() -> None:
    x = np.random.default_rng(6).normal(size=60)

    def spread(sample: np.ndarray) -> float:  # not vectorized
        return float(np.max(sample) - np.min(sample))

    first = bootstrap_ci(x, spread, vectorized=False, n_resamples=300, seed=9)
    with ThreadPoolExecutor(1) as pool:
        pooled = bootstrap_ci(x, spread, vectorized=False, n_resamples=300, seed=9, executor=pool)
    assert (first.low, first.high) == (pooled.low, pooled.high)
    again = bootstrap_ci(x, "std", seed=9)
    assert again == bootstrap_ci(x, "std", seed=9)


def test_missing_values_are_dropped() -> None:
    paired = bootstrap_ci(
        (np.array([1.0, 2, 3, np.nan, 5, 6]), np.array([2.0, 1, 4, 3, np.inf, 7])),
        "pearson_r",
        n_resamples=200,
    )
    assert paired.n == 4 and paired.n_missing == 2


def test_degenerate_inputs() -> None:
    with pytest.raises(StatsInputError, match="at least 2 values"):
        bootstrap_ci(np.array([1.0]), "mean")
    with pytest.raises(StatsInputError, match="Unknown statistic"):
        bootstrap_ci(np.array([1.0, 2.0]), "mode")
    with pytest.raises(StatsInputError, match="at least 100"):
        bootstrap_ci(np.array([1.0, 2.0]), "mean", n_resamples=10)
    flat = bootstrap_ci(np.full(20, 3.0), "mean")
    assert (flat.low, flat.high, flat.se) == (3.0, 3.0, 0.0)
    assert flat.warnings == ["All values are equal, so the interval has zero width."]
    almost = bootstrap_ci(np.array([1.0] * 30 + [2.0]), "median", n_resamples=500)
    assert almost.low == almost.high == 1.0
    assert almost.warnings
