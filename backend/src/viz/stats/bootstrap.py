"""Bootstrap confidence intervals (scipy.stats.bootstrap, seeded and memory-bounded).

method="auto" uses BCa up to 10,000 observations (its jackknife costs O(n^2)) and the
percentile method above that, where the two agree closely. Named statistics are vectorized
and picklable; a statistic that isn't vectorized runs in the process pool when one is given.
"""

from __future__ import annotations

import math
import warnings as py_warnings
from collections.abc import Callable, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Literal

import numpy as np
from scipy import stats as sps

from viz.stats import _workers
from viz.stats.errors import StatsInputError
from viz.stats.sampling import DEFAULT_SEED

Method = Literal["auto", "bca", "percentile", "basic"]

BCA_MAX_N = 10_000
_BATCH_ELEMENTS = 20_000_000


def mean(x: np.ndarray, axis: int = -1) -> np.ndarray:
    return np.mean(x, axis=axis)


def median(x: np.ndarray, axis: int = -1) -> np.ndarray:
    return np.asarray(np.median(x, axis=axis))


def std(x: np.ndarray, axis: int = -1) -> np.ndarray:
    return np.std(x, axis=axis, ddof=1)


def quantile_stat(x: np.ndarray, axis: int = -1, *, q: float = 0.5) -> np.ndarray:
    return np.asarray(np.quantile(x, q, axis=axis))


def trimmed_mean(x: np.ndarray, axis: int = -1, *, proportion: float = 0.1) -> np.ndarray:
    result: np.ndarray = sps.trim_mean(x, proportion, axis=axis)
    return result


def pearson_r(x: np.ndarray, y: np.ndarray, axis: int = -1) -> np.ndarray:
    dx = x - x.mean(axis=axis, keepdims=True)
    dy = y - y.mean(axis=axis, keepdims=True)
    num = (dx * dy).sum(axis=axis)
    den = np.sqrt((dx * dx).sum(axis=axis) * (dy * dy).sum(axis=axis))
    with np.errstate(invalid="ignore", divide="ignore"):
        return num / den


STATISTICS: dict[str, Callable[..., np.ndarray]] = {
    "mean": mean,
    "median": median,
    "std": std,
    "trimmed_mean": trimmed_mean,
    "pearson_r": pearson_r,
}


def quantile(q: float) -> Callable[..., np.ndarray]:
    """A picklable statistic for the q-th quantile."""
    return partial(quantile_stat, q=q)


@dataclass(frozen=True)
class Interval:
    estimate: float
    low: float
    high: float
    se: float
    # "bca", "percentile" or "basic" (what actually ran).
    method: str
    ci_level: float
    n: int
    n_resamples: int
    n_missing: int = 0
    warnings: list[str] = field(default_factory=list)


def bootstrap_ci(
    data: np.ndarray | Sequence[np.ndarray],
    statistic: str | Callable[..., Any],
    *,
    ci_level: float = 0.95,
    n_resamples: int = 2000,
    method: Method = "auto",
    paired: bool = True,
    vectorized: bool = True,
    seed: int = DEFAULT_SEED,
    executor: Executor | None = None,
) -> Interval:
    """Two-sided bootstrap CI of `statistic` over one or more samples.

    `data` is one array or a sequence of arrays. With paired=True (e.g. pearson_r) rows are
    resampled together and a row with any non-finite value is dropped; otherwise each sample
    is cleaned and resampled on its own. A callable statistic takes (*samples, axis=-1).
    """
    if not 0 < ci_level < 1:
        raise StatsInputError("The confidence level must be between 0 and 1.")
    if n_resamples < 100:
        raise StatsInputError("Use at least 100 bootstrap resamples.")
    samples, n_missing = _clean(data, paired)
    n = min(len(s) for s in samples)
    if n < 2:
        raise StatsInputError(f"A bootstrap interval needs at least 2 values; there are {n}.")
    if isinstance(statistic, str):
        if statistic not in STATISTICS:
            raise StatsInputError(f"Unknown statistic '{statistic}'.")
        function: Callable[..., Any] = STATISTICS[statistic]
        vectorized = True
    else:
        function = statistic
    estimate = float(function(*samples, axis=-1) if vectorized else function(*samples))
    chosen = ("bca" if n <= BCA_MAX_N else "percentile") if method == "auto" else method
    notes: list[str] = []
    if all(np.ptp(s) == 0 for s in samples) or not math.isfinite(estimate):
        if not math.isfinite(estimate):
            raise StatsInputError("The statistic is undefined for these values.")
        notes.append("All values are equal, so the interval has zero width.")
        return Interval(estimate, estimate, estimate, 0.0, chosen, ci_level, n, 0, n_missing, notes)

    kwargs: dict[str, Any] = {
        "data": tuple(samples),
        "n_resamples": n_resamples,
        "batch": max(1, _BATCH_ELEMENTS // max(1, sum(len(s) for s in samples))),
        "vectorized": vectorized,
        "paired": paired and len(samples) > 1,
        "confidence_level": ci_level,
        "method": "BCa" if chosen == "bca" else chosen,
        "rng": np.random.default_rng(seed),
    }
    result = _run(kwargs, function, executor, vectorized)
    low, high = float(result.confidence_interval.low), float(result.confidence_interval.high)
    if not (math.isfinite(low) and math.isfinite(high)) and chosen == "bca":
        kwargs["method"], chosen = "percentile", "percentile"
        kwargs["rng"] = np.random.default_rng(seed)
        result = _run(kwargs, function, executor, vectorized)
        low, high = float(result.confidence_interval.low), float(result.confidence_interval.high)
        notes.append("BCa was undefined for these data; used the percentile method.")
    se = float(result.standard_error)
    if not (math.isfinite(low) and math.isfinite(high)):
        notes.append("Every resample gave the same value, so the interval has zero width.")
        low = high = estimate
        se = 0.0
    return Interval(estimate, low, high, se, chosen, ci_level, n, n_resamples, n_missing, notes)


def _clean(data: np.ndarray | Sequence[np.ndarray], paired: bool) -> tuple[list[np.ndarray], int]:
    arrays = (
        [np.asarray(data, dtype=float)]
        if isinstance(data, np.ndarray) and data.ndim == 1
        else [np.asarray(d, dtype=float).ravel() for d in data]
    )
    if paired and len(arrays) > 1:
        if len({len(a) for a in arrays}) != 1:
            raise ValueError("paired samples must have the same length")
        keep = np.logical_and.reduce([np.isfinite(a) for a in arrays])
        return [a[keep] for a in arrays], int((~keep).sum())
    cleaned = [a[np.isfinite(a)] for a in arrays]
    return cleaned, sum(len(a) - len(c) for a, c in zip(arrays, cleaned, strict=True))


def _run(
    kwargs: dict[str, Any],
    function: Callable[..., Any],
    executor: Executor | None,
    vectorized: bool,
) -> Any:
    with py_warnings.catch_warnings():
        py_warnings.simplefilter("ignore", category=sps.DegenerateDataWarning)
        py_warnings.simplefilter("ignore", category=RuntimeWarning)
        if executor is not None and not vectorized:
            return executor.submit(_workers.run_bootstrap, kwargs, function).result()
        return sps.bootstrap(statistic=function, **kwargs)
