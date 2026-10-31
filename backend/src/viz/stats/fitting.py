"""Distribution fitting: maximum likelihood (SciPy `fit`) on a sample, ranked by AIC.

Each candidate is fitted by scipy.stats.<dist>.fit. The positive distributions (lognormal,
gamma, Weibull, exponential) fix loc = 0, as R's fitdistrplus and MASS::fitdistr do: a free
location makes their likelihood unbounded or the fit unstable. Beta fixes loc = 0 and
scale = 1 and needs values strictly inside (0, 1). A candidate whose support excludes some
values is skipped with the reason.

Ranking: AIC = 2k - 2 log L with k the number of estimated parameters (BIC = k ln n - 2 log L
too), Akaike weights exp(-dAIC / 2) normalised. The Kolmogorov-Smirnov statistic measures
the largest gap between the empirical and fitted CDFs; its p-value is approximate (too
large) because the parameters were estimated from the same data.

Fits run on a random sample of up to 100,000 values (FIT_CAP); with that many values the
ranking is decided by likelihood differences far larger than the sampling noise.
"""

from __future__ import annotations

import math
import warnings as py_warnings
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy import stats as sps
from sqlglot import exp

from viz.contracts import ColumnSemantic, StatResult
from viz.stats.errors import StatsInputError
from viz.stats.fields import derived, measure_expr
from viz.stats.models import (
    Distribution,
    DistributionFit,
    DistributionFitRequest,
    DistributionFitValues,
    FitParameter,
    SkippedDistribution,
)
from viz.stats.relation import Relation
from viz.stats.results import stat_result
from viz.stats.sampling import DEFAULT_SEED, KDE_CAP, reservoir_sample

FIT_CAP = KDE_CAP
MAX_BINS = 100


@dataclass(frozen=True)
class _Spec:
    label: str
    fixed: dict[str, float] = field(default_factory=dict)
    # Support: "real", "positive" (x > 0) or "unit" (0 < x < 1).
    support: str = "real"
    default: bool = True


SPECS: dict[Distribution, _Spec] = {
    "norm": _Spec("Normal"),
    "lognorm": _Spec("Lognormal", {"floc": 0.0}, "positive"),
    "gamma": _Spec("Gamma", {"floc": 0.0}, "positive"),
    "weibull_min": _Spec("Weibull", {"floc": 0.0}, "positive"),
    "expon": _Spec("Exponential", {"floc": 0.0}, "positive"),
    "logistic": _Spec("Logistic"),
    "laplace": _Spec("Laplace"),
    "uniform": _Spec("Uniform"),
    "beta": _Spec("Beta", {"floc": 0.0, "fscale": 1.0}, "unit"),
    "gumbel_r": _Spec("Gumbel (maximum)"),
    "t": _Spec("Student's t", default=False),
    "cauchy": _Spec("Cauchy", default=False),
}
DEFAULT_DISTRIBUTIONS: tuple[Distribution, ...] = tuple(d for d, s in SPECS.items() if s.default)


@dataclass(frozen=True)
class _Fit:
    distribution: Distribution
    names: list[str]
    params: tuple[float, ...]
    fixed: set[str]
    log_likelihood: float
    k: int
    ks_statistic: float
    ks_p_value: float


def _param_names(dist: sps.rv_continuous) -> list[str]:
    shapes = [s.strip() for s in dist.shapes.split(",")] if dist.shapes else []
    return [*shapes, "loc", "scale"]


def fit_one(x: np.ndarray, name: Distribution) -> _Fit | str:
    """The ML fit of `name` to finite values `x`, or the reason it can't be fitted."""
    spec = SPECS[name]
    if spec.support == "positive" and x.min() <= 0:
        return "needs every value to be greater than 0"
    if spec.support == "unit" and (x.min() <= 0 or x.max() >= 1):
        return "needs every value to be strictly between 0 and 1"
    dist = getattr(sps, name)
    names = _param_names(dist)
    with py_warnings.catch_warnings(), np.errstate(all="ignore"):
        py_warnings.simplefilter("ignore")
        try:
            params = tuple(float(p) for p in dist.fit(x, **spec.fixed))
        except (ValueError, RuntimeError, FloatingPointError, sps.FitError):
            return "the fit did not converge"
        log_likelihood = float(dist.logpdf(x, *params).sum())
        ks = sps.kstest(x, dist.cdf, args=params)
    if not all(math.isfinite(p) for p in params):
        return "the fit did not converge"
    if not math.isfinite(log_likelihood):
        return "the fitted density is zero at some values"
    fixed = {key[1:] for key in spec.fixed}
    return _Fit(
        distribution=name,
        names=names,
        params=params,
        fixed=fixed,
        log_likelihood=log_likelihood,
        k=len(names) - len(fixed),
        ks_statistic=float(ks.statistic),
        ks_p_value=float(ks.pvalue),
    )


def _histogram(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    edges = np.histogram_bin_edges(x, bins="auto")
    if len(edges) > MAX_BINS + 1:
        edges = np.linspace(float(x.min()), float(x.max()), MAX_BINS + 1)
    density, edges = np.histogram(x, bins=edges, density=True)
    return edges, density


def fit_distributions(
    rel: Relation, req: DistributionFitRequest, *, seed: int = DEFAULT_SEED
) -> StatResult:
    value = measure_expr(rel, req.field)
    source = derived(rel, [("_v", value, "DOUBLE", ColumnSemantic.QUANTITATIVE)], exp.true())
    sample = reservoir_sample(source, ["_v"], cap=FIT_CAP, seed=seed)
    x = sample.column("_v")
    if len(x) < 3:
        raise StatsInputError(f"Fitting needs at least 3 values; there are {len(x)}.")
    if float(np.ptp(x)) == 0:
        raise StatsInputError("All values are equal, so no continuous distribution fits.")
    wanted: Sequence[Distribution] = (
        list(dict.fromkeys(req.distributions)) if req.distributions else DEFAULT_DISTRIBUTIONS
    )
    fits: list[_Fit] = []
    skipped: list[SkippedDistribution] = []
    for name in wanted:
        outcome = fit_one(x, name)
        if isinstance(outcome, str):
            skipped.append(SkippedDistribution(distribution=name, reason=outcome))
        else:
            fits.append(outcome)
    n = len(x)
    warnings = list(sample.notes())
    distinct = len(np.unique(x))
    if distinct < min(20, n // 2):
        warnings.append(
            f"'{req.field}' has only {distinct} distinct values; continuous distributions may "
            "describe it poorly."
        )
    if fits:
        warnings.append(
            "Kolmogorov-Smirnov p-values are approximate (too large): the parameters were "
            "estimated from the same data."
        )
    edges, density = _histogram(x)
    grid = np.linspace(float(edges[0]), float(edges[-1]), req.grid_size)
    aic = {f.distribution: 2 * f.k - 2 * f.log_likelihood for f in fits}
    best_aic = min(aic.values()) if aic else math.nan
    weights = {d: math.exp(-(a - best_aic) / 2) for d, a in aic.items()}
    total_weight = sum(weights.values())
    out: list[DistributionFit] = []
    for f in sorted(fits, key=lambda f: aic[f.distribution]):
        dist = getattr(sps, f.distribution)
        with np.errstate(all="ignore"):
            pdf = dist.pdf(grid, *f.params)
        out.append(
            DistributionFit(
                distribution=f.distribution,
                label=SPECS[f.distribution].label,
                params=[
                    FitParameter(name=p, value=v, fixed=p in f.fixed)
                    for p, v in zip(f.names, f.params, strict=True)
                ],
                log_likelihood=f.log_likelihood,
                aic=aic[f.distribution],
                bic=f.k * math.log(n) - 2 * f.log_likelihood,
                delta_aic=aic[f.distribution] - best_aic,
                aic_weight=weights[f.distribution] / total_weight,
                ks_statistic=f.ks_statistic,
                ks_p_value=f.ks_p_value,
                pdf=[float(v) if math.isfinite(v) else None for v in pdf],
            )
        )
    for item in skipped:
        warnings.append(f"{SPECS[item.distribution].label} was not fitted: {item.reason}.")
    if not out:
        raise StatsInputError(
            "None of the distributions could be fitted: "
            + "; ".join(f"{SPECS[s.distribution].label} {s.reason}" for s in skipped)
            + "."
        )
    values = DistributionFitValues(
        field=req.field,
        fits=out,
        skipped=skipped,
        best=out[0].distribution,
        edges=edges.tolist(),
        density=density.tolist(),
        x=grid.tolist(),
    )
    return stat_result(
        values.model_dump(by_alias=True),
        method="Maximum-likelihood distribution fits, ranked by AIC",
        n=n,
        n_total=sample.n_total,
        n_missing=sample.n_missing,
        sampled=sample.sampled,
        warnings=warnings,
    )
