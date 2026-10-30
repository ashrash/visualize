"""Regression curves with confidence bands: OLS (linear, polynomial, log/exp/power) and LOWESS.

OLS is solved by QR on a centred and scaled polynomial basis (well conditioned up to degree
6), with a rank check, and coefficients are converted back to the original scale. Bands are
pointwise: mean confidence band t * sqrt(g' Cov g), prediction band adding sigma^2.

Trendline forms (as Excel's, fitted by least squares in the transformed space):
- linear / polynomial: y = b0 + b1 x + ... + bd x^d
- logarithmic (log_x): y = b0 + b1 ln x
- exponential (log_y): ln y = b0 + b1 x, i.e. y = a e^(b x) with a = e^b0
- power (log_x, log_y): ln y = b0 + b1 ln x, i.e. y = a x^b with a = e^b0
With log_y, R^2 and the bands refer to ln y; the bands are back-transformed (they bound the
median of y, not the mean).

LOWESS uses statsmodels (tricube weights, local linear fits, robustness iterations); its
band is a bootstrap over (x, y) pairs, run in the process pool when one is given. The band
shows the sampling variability of the smooth only, not its smoothing bias: where the span
flattens a peak, the band is centred on the flattened curve.

BLAS/OpenMP threads are capped per process by the engine (data-core), in the API process and
in the pool workers; threadpoolctl limits are process-global, so kernels don't set their own.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from concurrent.futures import Executor
from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np
from scipy import linalg
from scipy import stats as sps

from viz.contracts import TrendlineKind
from viz.stats import _workers
from viz.stats.errors import StatsInputError
from viz.stats.relation import Relation
from viz.stats.sampling import DEFAULT_SEED, LOWESS_CAP, MODEL_CAP, reservoir_sample

MAX_DEGREE = 6
LOWESS_BAND_WARNING = "The band shows sampling variability of the smooth, not smoothing bias."

FitKind = Literal["linear", "polynomial", "logarithmic", "exponential", "power", "loess"]


@dataclass(frozen=True)
class FitCurve:
    kind: FitKind
    # The curve on an evenly spaced x grid (original units; temporal x is epoch ms).
    x: np.ndarray
    y: np.ndarray
    # Mean confidence band; None when undefined (no residual degrees of freedom) or not asked.
    lower: np.ndarray | None
    upper: np.ndarray | None
    # Prediction band (OLS with prediction=True only).
    pred_lower: np.ndarray | None
    pred_upper: np.ndarray | None
    # Coefficients b0..bd on the original scale of the fitted space (see module docstring);
    # empty for LOWESS.
    coef: list[float]
    coef_se: list[float] | None
    r2: float | None
    adj_r2: float | None
    # Residual standard deviation; None when df_resid is 0.
    sigma: float | None
    df_resid: int | None
    n: int
    ci_level: float | None
    # What was fitted and how the band was made, for display.
    method: str = ""
    degree: int = 1
    log_x: bool = False
    log_y: bool = False
    # max |u| over the data, u = x (or ln x): term sizes |b_k| * x_scale^k for the label.
    x_scale: float = 1.0
    sampled: bool = False
    n_total: int | None = None
    n_missing: int = 0
    warnings: list[str] = field(default_factory=list)

    def equation(self, digits: int = 4) -> str | None:
        """A label like "y = 1.234x + 5.678"; None for LOWESS."""
        if not self.coef:
            return None

        def f(value: float) -> str:
            return f"{value:.{digits}g}"

        b = self.coef
        if self.kind == "exponential":
            return f"y = {f(math.exp(b[0]))}e^({f(b[1])}x)"
        if self.kind == "power":
            return f"y = {f(math.exp(b[0]))}x^{f(b[1])}"
        variable = "ln(x)" if self.log_x else "x"
        # Compare terms by their size over the data, not by coefficient: with x ~ 1e12 a slope
        # of 3e-10 is a large term. Only round-off-sized terms are dropped.
        sizes = [abs(c) * self.x_scale**k for k, c in enumerate(b)]
        largest = max(sizes)
        terms: list[str] = []
        for power in range(len(b) - 1, -1, -1):
            value = b[power]
            if sizes[power] <= 1e-10 * largest:  # round-off, e.g. the x term of -x^2 + 4
                continue
            body = f(abs(value))
            if power >= 1:
                body = "" if body == "1" else body
                body += variable + (f"^{power}" if power > 1 else "")
            if not terms:
                terms.append(("-" if value < 0 else "") + body)
            else:
                terms.append(("- " if value < 0 else "+ ") + body)
        return "y = " + (" ".join(terms) or "0")


def _clean_pairs(
    x: np.ndarray | Sequence[float], y: np.ndarray | Sequence[float]
) -> tuple[np.ndarray, np.ndarray, int]:
    xa = np.asarray(x, dtype=float).ravel()
    ya = np.asarray(y, dtype=float).ravel()
    if len(xa) != len(ya):
        raise ValueError("x and y must have the same length")
    keep = np.isfinite(xa) & np.isfinite(ya)
    return xa[keep], ya[keep], int((~keep).sum())


def _kind_of(degree: int, log_x: bool, log_y: bool) -> FitKind:
    if log_x and log_y:
        return "power"
    if log_y:
        return "exponential"
    if log_x:
        return "logarithmic"
    return "linear" if degree == 1 else "polynomial"


def _to_original_basis(degree: int, center: float, scale: float) -> np.ndarray:
    """T with beta_original = T @ beta_scaled, for z = (u - center) / scale."""
    t = np.zeros((degree + 1, degree + 1))
    for k in range(degree + 1):
        # z^k = sum_j C(k, j) u^j (-center)^(k-j) / scale^k
        for j in range(k + 1):
            t[j, k] = math.comb(k, j) * (-center) ** (k - j) / scale**k
    return t


def ols_curve(
    x: np.ndarray | Sequence[float],
    y: np.ndarray | Sequence[float],
    *,
    degree: int = 1,
    log_x: bool = False,
    log_y: bool = False,
    ci_level: float | None = 0.95,
    prediction: bool = False,
    grid_size: int = 100,
    grid: np.ndarray | None = None,
) -> FitCurve:
    """Least-squares polynomial fit of y on x (or on ln x / for ln y), with pointwise bands."""
    if not 1 <= degree <= MAX_DEGREE:
        raise StatsInputError(f"The polynomial degree must be between 1 and {MAX_DEGREE}.")
    if (log_x or log_y) and degree != 1:
        raise StatsInputError("Log, exponential and power fits are degree 1.")
    if ci_level is not None and not 0 < ci_level < 1:
        raise StatsInputError("The confidence level must be between 0 and 1.")
    xa, ya, n_missing = _clean_pairs(x, y)
    warnings: list[str] = []
    positive = np.ones(len(xa), dtype=bool)
    if log_x:
        positive &= xa > 0
    if log_y:
        positive &= ya > 0
    dropped = int((~positive).sum())
    if dropped:
        what = " and ".join(v for v, on in (("x", log_x), ("y", log_y)) if on)
        warnings.append(f"Left out {dropped:,} points where {what} is not positive.")
        xa, ya = xa[positive], ya[positive]
    n = len(xa)
    p = degree + 1
    if n < p:
        raise StatsInputError(f"This fit needs at least {p} points; there are {n}.")
    u = np.log(xa) if log_x else xa
    v = np.log(ya) if log_y else ya
    center = float(u.mean())
    scale = float(np.std(u))
    if scale == 0:
        raise StatsInputError("x has no variation, so a line can't be fitted.")
    z = (u - center) / scale
    design = np.vander(z, p, increasing=True)
    q, r = np.linalg.qr(design)
    singular = np.linalg.svd(r, compute_uv=False)
    if singular[-1] <= singular[0] * max(n, p) * np.finfo(float).eps:
        raise StatsInputError(f"A degree-{degree} fit needs at least {p} distinct x values.")
    with np.errstate(over="ignore", invalid="ignore"):  # huge y: checked for finiteness below
        beta = linalg.solve_triangular(r, q.T @ v)
        residuals = v - design @ beta
        rss = float(residuals @ residuals)
        tss = float(((v - v.mean()) ** 2).sum())
    df = n - p
    sigma = math.sqrt(rss / df) if df > 0 and math.isfinite(rss / df) else None
    r_inv = linalg.solve_triangular(r, np.eye(p))
    transform = _to_original_basis(degree, center, scale)
    coef = (transform @ beta).tolist()
    coef_se = None
    if sigma is not None:
        cov = sigma**2 * (r_inv @ r_inv.T)
        coef_se = np.sqrt(np.diag(transform @ cov @ transform.T)).tolist()

    r2 = adj_r2 = None
    if not (math.isfinite(rss) and math.isfinite(tss)):
        sigma = None
        coef_se = None
        warnings.append("The values are too large for R² and the band; they were left out.")
    elif tss > 0:
        r2 = 1.0 - rss / tss
        adj_r2 = 1.0 - (1.0 - r2) * (n - 1) / df if df > 0 else None
    else:
        warnings.append("y has no variation, so R² is undefined.")
    if not all(math.isfinite(c) for c in coef):
        raise StatsInputError("The values are too large to fit.")

    if grid is None:
        grid = np.linspace(float(xa.min()), float(xa.max()), grid_size)
    grid = np.asarray(grid, dtype=float)
    ug = np.log(grid) if log_x else grid
    basis = np.vander((ug - center) / scale, p, increasing=True)
    fit = basis @ beta
    lower = upper = pred_lower = pred_upper = None
    if ci_level is not None and sigma is not None:
        t = float(sps.t.ppf(0.5 + ci_level / 2, df))
        se_mean = sigma * np.sqrt(((basis @ r_inv) ** 2).sum(axis=1))
        lower, upper = fit - t * se_mean, fit + t * se_mean
        if prediction:
            se_pred = np.sqrt(se_mean**2 + sigma**2)
            pred_lower, pred_upper = fit - t * se_pred, fit + t * se_pred
    elif ci_level is not None and df <= 0:
        warnings.append("Too few points for a confidence band.")
    if log_y:
        fit = np.exp(fit)
        if lower is not None and upper is not None:
            lower, upper = np.exp(lower), np.exp(upper)
            warnings.append("The band is back-transformed from ln(y): it bounds the median of y.")
        if pred_lower is not None and pred_upper is not None:
            pred_lower, pred_upper = np.exp(pred_lower), np.exp(pred_upper)
    kind = _kind_of(degree, log_x, log_y)
    band = f"; {ci_level:.0%} t confidence band" if lower is not None and ci_level else ""
    return FitCurve(
        kind=kind,
        x=grid,
        y=fit,
        lower=lower,
        upper=upper,
        pred_lower=pred_lower,
        pred_upper=pred_upper,
        coef=coef,
        coef_se=coef_se,
        r2=r2,
        adj_r2=adj_r2,
        sigma=sigma,
        df_resid=df,
        n=n,
        ci_level=ci_level,
        method=f"OLS {kind} fit" + (f" (degree {degree})" if degree > 1 else "") + band,
        degree=degree,
        log_x=log_x,
        log_y=log_y,
        x_scale=float(np.abs(u).max()),
        n_missing=n_missing + dropped,
        warnings=warnings,
    )


def lowess_curve(
    x: np.ndarray | Sequence[float],
    y: np.ndarray | Sequence[float],
    *,
    frac: float = 2.0 / 3.0,
    it: int = 3,
    ci_level: float | None = 0.95,
    n_boot: int = 200,
    band_points: int = 1000,
    seed: int = DEFAULT_SEED,
    grid_size: int = 100,
    executor: Executor | None = None,
) -> FitCurve:
    """LOWESS smooth on a grid, with a bootstrap band over (x, y) pairs.

    The band is pointwise: fit + quantiles of (bootstrap fit - fit). Above `band_points`
    the bootstrap runs on a random subsample of m points and the deviations are scaled by
    sqrt(m / n): with the span a fixed fraction of the data, each local fit averages
    frac * n points, so its standard error shrinks as 1/sqrt(n) while its bias does not
    depend on n (m-out-of-n bootstrap). This keeps the band cheap at 10k points.
    """
    if not 0 < frac <= 1:
        raise StatsInputError("The LOWESS span must be between 0 and 1.")
    xa, ya, n_missing = _clean_pairs(x, y)
    n = len(xa)
    if n < 3:
        raise StatsInputError(f"LOWESS needs at least 3 points; there are {n}.")
    if np.ptp(xa) == 0:
        raise StatsInputError("x has no variation, so a curve can't be fitted.")
    grid = np.linspace(float(xa.min()), float(xa.max()), grid_size)
    delta = 0.01 * float(np.ptp(xa))
    fitted = _workers.lowess_fitted(xa, ya, frac, it, delta)
    order = np.argsort(xa, kind="stable")
    curve = np.interp(grid, xa[order], fitted[order])
    rss = float(((ya - fitted) ** 2).sum())
    tss = float(((ya - ya.mean()) ** 2).sum())
    warnings: list[str] = []
    lower = upper = None
    if ci_level is not None:
        if not 0 < ci_level < 1:
            raise StatsInputError("The confidence level must be between 0 and 1.")
        if n_boot < 20:
            raise StatsInputError("Use at least 20 bootstrap resamples for a LOWESS band.")
        root = np.random.SeedSequence(seed)
        bx, by_, center = xa, ya, curve
        if n > band_points:
            pick = np.random.default_rng(root.spawn(1)[0]).choice(n, band_points, replace=False)
            # Keep the extreme x values so the subsample spans the whole grid.
            pick = np.unique(np.concatenate([pick[: band_points - 2], [order[0], order[-1]]]))
            bx, by_ = xa[pick], ya[pick]
            sub_fit = _workers.lowess_fitted(bx, by_, frac, it, delta)
            sub_order = np.argsort(bx, kind="stable")
            center = np.interp(grid, bx[sub_order], sub_fit[sub_order])
        seeds = root.spawn(4)
        chunks = np.array_split(np.arange(n_boot), len(seeds))
        jobs = [
            (bx, by_, grid, frac, it, delta, len(chunk), int(s.generate_state(1)[0]))
            for chunk, s in zip(chunks, seeds, strict=True)
        ]
        if executor is None:
            parts = [_workers.lowess_bootstrap(*job) for job in jobs]
        else:
            futures = [executor.submit(_workers.lowess_bootstrap, *job) for job in jobs]
            parts = [f.result() for f in futures]
        deviations = (np.vstack(parts) - center) * math.sqrt(len(bx) / n)
        alpha = 1 - ci_level
        low_q, high_q = np.nanquantile(deviations, [alpha / 2, 1 - alpha / 2], axis=0)
        lower, upper = curve + low_q, curve + high_q
        warnings.append(LOWESS_BAND_WARNING)
    return FitCurve(
        kind="loess",
        x=grid,
        y=curve,
        lower=lower,
        upper=upper,
        pred_lower=None,
        pred_upper=None,
        coef=[],
        coef_se=None,
        r2=1.0 - rss / tss if tss > 0 else None,
        adj_r2=None,
        sigma=None,
        df_resid=None,
        n=n,
        ci_level=ci_level,
        method=(
            f"LOWESS (span {frac:.2g}, {it} robustness iterations)"
            + (
                "; bootstrap band of the smooth (sampling variability only)"
                if lower is not None
                else ""
            )
        ),
        n_missing=n_missing,
        warnings=warnings,
    )


_TREND_FORMS: dict[TrendlineKind, tuple[bool, bool]] = {
    TrendlineKind.LINEAR: (False, False),
    TrendlineKind.POLYNOMIAL: (False, False),
    TrendlineKind.LOGARITHMIC: (True, False),
    TrendlineKind.EXPONENTIAL: (False, True),
    TrendlineKind.POWER: (True, True),
}


def fit_trend(
    rel: Relation,
    x_field: str,
    y_field: str,
    kind: TrendlineKind,
    *,
    degree: int = 2,
    ci_level: float | None = 0.95,
    grid_size: int = 100,
    seed: int = DEFAULT_SEED,
    executor: Executor | None = None,
) -> FitCurve:
    """A trendline over a relation: samples (1M rows OLS, 10k LOESS), fits, records sampling.

    Moving averages are window queries, not fits; chartdata computes those.
    """
    if kind == TrendlineKind.MOVING_AVERAGE:
        raise ValueError("moving averages are computed in SQL by chartdata, not fitted")
    cap = LOWESS_CAP if kind == TrendlineKind.LOESS else MODEL_CAP
    sample = reservoir_sample(rel, [x_field, y_field], cap=cap, seed=seed)
    x, y = sample.column(x_field), sample.column(y_field)
    if kind == TrendlineKind.LOESS:
        curve = lowess_curve(
            x, y, ci_level=ci_level, grid_size=grid_size, seed=seed, executor=executor
        )
    else:
        log_x, log_y = _TREND_FORMS[kind]
        curve = ols_curve(
            x,
            y,
            degree=degree if kind == TrendlineKind.POLYNOMIAL else 1,
            log_x=log_x,
            log_y=log_y,
            ci_level=ci_level,
            grid_size=grid_size,
        )
    return replace(
        curve,
        sampled=sample.sampled,
        n_total=sample.n_total,
        n_missing=sample.n_missing + curve.n_missing,
        warnings=[*curve.warnings, *sample.notes()],
    )
