"""Top-level, picklable functions for the process pool (CLAUDE.md §6).

Workers get capped arrays, never a large DataFrame. They import heavy modules lazily so the
pool's spawn start-up stays cheap for work that doesn't need them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np


def lowess_fitted(x: np.ndarray, y: np.ndarray, frac: float, it: int, delta: float) -> np.ndarray:
    """LOWESS fitted values at each x, in input order."""
    from statsmodels.nonparametric.smoothers_lowess import lowess

    fitted: np.ndarray = lowess(y, x, frac=frac, it=it, delta=delta, return_sorted=False)
    return fitted


def lowess_bootstrap(
    x: np.ndarray,
    y: np.ndarray,
    grid: np.ndarray,
    frac: float,
    it: int,
    delta: float,
    count: int,
    seed: int,
) -> np.ndarray:
    """`count` LOWESS refits on resampled (x, y) pairs, each interpolated onto `grid`."""
    rng = np.random.default_rng(seed)
    n = len(x)
    out = np.full((count, len(grid)), np.nan)
    for b in range(count):
        idx = rng.integers(0, n, n)
        xb, yb = x[idx], y[idx]
        if np.ptp(xb) == 0:
            continue
        fitted = lowess_fitted(xb, yb, frac, it, delta)
        order = np.argsort(xb, kind="stable")
        # Outside the resample's x range there is no fit: leave NaN (no flat extrapolation).
        out[b] = np.interp(grid, xb[order], fitted[order], left=np.nan, right=np.nan)
    return out


def run_bootstrap(kwargs: dict[str, Any], statistic: Callable[..., Any]) -> Any:
    """scipy.stats.bootstrap in a worker, for statistics that aren't vectorized."""
    from scipy import stats

    return stats.bootstrap(statistic=statistic, **kwargs)
