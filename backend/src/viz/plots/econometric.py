"""Plots.md §8 Statistical & econometric: Kaplan-Meier, forest, ROC, precision-recall, Lorenz,
quantile plot, marginal effects, coefficient plot, impulse responses and event study.
Owner: plots-multivariate-econometric.

- Lorenz curves, Gini and quantile plots are exact, in SQL (row_number windows, never
  quantile_disc with a long probability list: see CLAUDE.md §12).
- Model fits (regressions, survival, ROC/PR) run on a seeded reservoir sample (1M rows).
- Every result is a StatResult; nothing returns NaN (undefined parts are null plus a warning).
- Inference conventions are stated in each result's `method`, and match the reference
  implementation named there (statsmodels, scikit-learn, R's survival/metafor/fixest).
"""

from __future__ import annotations

import math
import warnings as py_warnings
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from pydantic import Field
from scipy import linalg
from scipy import stats as sps
from sqlglot import exp

from viz.contracts import ColumnSemantic, PlotType, StatResult
from viz.plots.base import (
    CATEGORICAL,
    NUMERIC_OR_TIME,
    QUANT,
    Channel,
    PlotParams,
    PlotRequest,
    register_plot,
)
from viz.plots.multivariate import Rows, complete_rows
from viz.stats.errors import StatsInputError
from viz.stats.groups import cap_groups
from viz.stats.relation import Relation, col
from viz.stats.results import stat_result
from viz.stats.sampling import MODEL_CAP
from viz.stats.sql import alias, as_double, count_if, count_star, fn, num_list, valid_rows

MAX_TERMS = 30
MAX_LEVELS = 50
MAX_CURVE_POINTS = 2000
MAX_SERIES_ROWS = 100_000

CiLevel = Field(default=0.95, ge=0.5, le=0.999)


def _z(level: float) -> float:
    return float(sps.norm.ppf(0.5 + level / 2))


def _sql(query: exp.Expr) -> str:
    return query.sql(dialect="duckdb")


def _canonical(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# Words that read as "the event happened" / "it did not", for two-valued text fields.
_POSITIVE_WORDS = frozenset(
    {
        "yes", "y", "true", "t", "positive", "pos", "event", "case", "success",
        "dead", "died", "death", "deceased", "failed", "failure", "fail", "relapse",
        "relapsed", "recurrence", "progressed", "progression", "churned", "default",
    }
)  # fmt: skip
_NEGATIVE_WORDS = frozenset(
    {
        "no", "n", "false", "f", "negative", "neg", "none", "control", "alive", "survived",
        "living", "censored", "censor", "retained", "active",
    }
)  # fmt: skip


def binary_outcome(
    values: Sequence[object],
    name: str,
    positive: str | None = None,
    *,
    need_both: bool = True,
) -> tuple[np.ndarray, str, list[str]]:
    """0/1 codes for a two-valued field, the label treated as 1, and notes.

    In order: an explicit `positive` value; 0/1 (1); false/true (true); 1/2 (2, the R
    `Surv` convention where 1 = censored and 2 = event); a pair where exactly one value reads
    as positive ("died", "yes", ...) or exactly one reads as negative ("survived", "no", ...);
    otherwise the last value in sorted order. Every guess beyond 0/1 and true/false adds a
    note naming the value used, and callers expose `positive` so the user can override it.
    """
    text = [_canonical(v) for v in values]
    levels = sorted(set(text))
    notes: list[str] = []
    lowered = {level.lower(): level for level in levels}
    if positive:
        chosen = lowered.get(positive.strip().lower())
        if chosen is None:
            raise StatsInputError(
                f"'{positive}' is not a value of '{name}' (it has {', '.join(levels[:5])})."
            )
    elif set(levels) <= {"0", "1"}:
        chosen = "1"
    elif set(lowered) <= {"false", "true"}:
        chosen = lowered.get("true", "true")
    else:
        if len(levels) > 2:
            raise StatsInputError(
                f"'{name}' must have exactly two values (such as 0/1); it has {len(levels)}."
            )
        positives = [lowered[w] for w in lowered if w in _POSITIVE_WORDS]
        negatives = [lowered[w] for w in lowered if w in _NEGATIVE_WORDS]
        if set(levels) <= {"1", "2"}:
            chosen = "2"
            notes.append(
                f"'{name}' is coded 1/2: treating 2 as the event and 1 as censored "
                "(R's Surv convention). Set the event value to change this."
            )
        else:
            if len(positives) == 1:
                chosen = positives[0]
            elif len(negatives) == 1 and len(levels) == 2:
                chosen = next(level for level in levels if level != negatives[0])
            else:
                chosen = levels[-1]
            notes.append(
                f"Treating '{name}' = {chosen} as the event (positive) value. "
                "Set it explicitly if that's wrong."
            )
    if len(levels) > 2:
        raise StatsInputError(
            f"'{name}' must have exactly two values (such as 0/1); it has {len(levels)}."
        )
    codes = np.array([1 if t == chosen else 0 for t in text], dtype=float)
    if need_both and (codes.min(initial=1) == 1 or codes.max(initial=0) == 0):
        raise StatsInputError(f"'{name}' needs both outcomes; every row has the same value.")
    return codes, chosen, notes


def _thin(n: int, cap: int = MAX_CURVE_POINTS) -> np.ndarray:
    """At most `cap` indices from 0..n-1, evenly spaced, always keeping both ends."""
    if n <= cap:
        return np.arange(n)
    return np.unique(np.linspace(0, n - 1, cap).round().astype(int))


def _groups(
    req: PlotRequest, rel: Relation, channel: str, max_groups: int
) -> tuple[Relation, list[str]]:
    name = req.field(channel)
    if name is None:
        return rel, []
    capped = cap_groups(rel, [name], max_groups)
    return capped.relation, [capped.warning] if capped.warning else []


# ---------------------------------------------------------------------------------------------
# Regression design and fitting (coefficient and marginal-effects plots)
# ---------------------------------------------------------------------------------------------

Model = Literal["ols", "logit", "probit", "poisson"]
MODEL_LABEL = {"ols": "OLS", "logit": "Logit", "probit": "Probit", "poisson": "Poisson"}


@dataclass(frozen=True)
class Design:
    matrix: np.ndarray
    terms: list[str]
    # Index of the term's source field in the x channel; -1 for the intercept.
    sources: list[int]
    notes: list[str]


def design_matrix(
    rows: Rows,
    rel: Relation,
    fields: Sequence[str],
    *,
    intercept: bool = True,
    standardize_numeric: bool = False,
) -> Design:
    """Numeric fields as columns, categorical fields as 0/1 dummies (first level = reference)."""
    columns: list[np.ndarray] = []
    terms: list[str] = []
    sources: list[int] = []
    notes: list[str] = []
    if intercept:
        columns.append(np.ones(rows.n))
        terms.append("Intercept")
        sources.append(-1)
    for k, name in enumerate(fields):
        column = rel.columns[name]
        if column.semantic == ColumnSemantic.QUANTITATIVE:
            values = rows.matrix([name])[:, 0]
            if standardize_numeric:
                sd = float(values.std(ddof=1)) if len(values) > 1 else 0.0
                if sd > 0:
                    values = (values - values.mean()) / sd
            columns.append(values)
            terms.append(name)
            sources.append(k)
            continue
        text = [_canonical(v) for v in rows.table.column(name).to_pylist()]
        levels = sorted(set(text))
        if len(levels) > MAX_LEVELS:
            raise StatsInputError(
                f"'{name}' has {len(levels)} distinct values; a model term takes at most "
                f"{MAX_LEVELS}."
            )
        if len(levels) < 2:
            notes.append(f"Left out '{name}': it has a single value.")
            continue
        codes = np.array(text, dtype=object)
        for level in levels[1:]:
            columns.append((codes == level).astype(float))
            terms.append(f"{name}: {level}")
            sources.append(k)
        notes.append(f"'{name}' is compared with its first value, {levels[0]}.")
    if len(terms) > MAX_TERMS + 1:
        raise StatsInputError(f"The model has {len(terms)} terms; at most {MAX_TERMS} fit here.")
    matrix = np.column_stack(columns) if columns else np.empty((rows.n, 0))
    return Design(matrix, terms, sources, notes)


def check_rank(matrix: np.ndarray, terms: Sequence[str]) -> None:
    """Raise a readable error when the design is rank deficient (collinear terms)."""
    n, p = matrix.shape
    if p == 0:
        raise StatsInputError("Choose at least one predictor.")
    if n <= p:
        raise StatsInputError(f"The model has {p} terms but only {n} complete rows.")
    # Scale columns to unit length first, so ill-conditioned but full-rank designs (NIST's
    # Longley, years next to an intercept) aren't flagged.
    norms = np.linalg.norm(matrix, axis=0)
    scaled = matrix / np.where(norms > 0, norms, 1.0)
    _, r, pivots = linalg.qr(scaled, mode="economic", pivoting=True)
    diag = np.abs(np.diag(r))
    rank = int((diag > diag[0] * max(n, p) * 1e-10).sum()) if diag[0] > 0 else 0
    if rank < p:
        dependent = [terms[i] for i in pivots[rank:]]
        raise StatsInputError(
            "These terms are exact combinations of the others (or constant), so their effects "
            f"can't be separated: {', '.join(dependent)}."
        )


def _outcome(rows: Rows, rel: Relation, name: str, model: Model) -> tuple[np.ndarray, list[str]]:
    column = rel.columns[name]
    notes: list[str] = []
    if model in ("logit", "probit"):
        codes, positive, notes = binary_outcome(rows.table.column(name).to_pylist(), name)
        if positive not in ("1", "true"):
            notes.append(f"Modelling the probability that '{name}' = {positive}.")
        return codes, notes
    if column.semantic != ColumnSemantic.QUANTITATIVE:
        raise StatsInputError(f"'{name}' must be numeric for a {MODEL_LABEL[model]} model.")
    y = rows.matrix([name])[:, 0]
    if model == "poisson":
        if np.any(y < 0):
            raise StatsInputError(f"A Poisson model needs counts; '{name}' has negative values.")
        if not np.allclose(y, np.round(y)):
            notes.append(f"'{name}' has non-integer values; read the model as quasi-Poisson.")
    return y, notes


def fit_model(
    model: Model,
    y: np.ndarray,
    x: np.ndarray,
    *,
    robust: bool = False,
    clusters: np.ndarray | None = None,
) -> tuple[Any, list[str]]:
    """Fit with statsmodels; perfect separation and failed convergence become clear errors."""
    import statsmodels.api as sm
    from statsmodels.tools.sm_exceptions import (
        ConvergenceWarning,
        PerfectSeparationError,
        PerfectSeparationWarning,
    )

    if clusters is not None:
        cov: dict[str, Any] = {"cov_type": "cluster", "cov_kwds": {"groups": clusters}}
    elif robust:
        cov = {"cov_type": "HC1"}
    else:
        cov = {"cov_type": "nonrobust"}
    notes: list[str] = []
    with py_warnings.catch_warnings(record=True) as caught:
        py_warnings.simplefilter("always")
        try:
            if model == "ols":
                result = sm.OLS(y, x).fit(**cov)
            else:
                family = {"logit": sm.Logit, "probit": sm.Probit, "poisson": sm.Poisson}[model]
                result = family(y, x).fit(disp=0, maxiter=200, **cov)
        except PerfectSeparationError as exc:
            raise StatsInputError(_SEPARATION) from exc
        except np.linalg.LinAlgError as exc:
            raise StatsInputError(
                "The model can't be fitted: the predictors are (nearly) collinear."
            ) from exc
    categories = {w.category for w in caught}
    if any(issubclass(c, PerfectSeparationWarning) for c in categories):
        raise StatsInputError(_SEPARATION)
    if model != "ols":
        converged = bool(getattr(result, "mle_retvals", {}).get("converged", True))
        if not converged or any(issubclass(c, ConvergenceWarning) for c in categories):
            notes.append("The model did not fully converge; treat the estimates as rough.")
    params = np.asarray(result.params)
    bse = np.asarray(result.bse)
    if not (np.all(np.isfinite(params)) and np.all(np.isfinite(bse))):
        raise StatsInputError(_SEPARATION if model != "ols" else "The model can't be fitted.")
    return result, notes


_SEPARATION = (
    "Perfect separation: some predictor (or combination) predicts the outcome exactly, so the "
    "effects are infinite. Remove that predictor or merge sparse categories."
)


def _model_fit(
    req: PlotRequest, model: Model, robust: bool, *, standardize: bool = False
) -> tuple[Any, Design, Rows, list[str], str | None]:
    y_name = req.field("y")
    assert y_name is not None
    xs = req.fields("x")
    if y_name in xs:
        raise StatsInputError("The outcome can't also be a predictor.")
    cluster = req.field("cluster")
    rel = req.relation
    extras = [cluster] if cluster and cluster not in (y_name, *xs) else []
    rows = complete_rows(rel, [y_name, *xs, *extras], cap=MODEL_CAP, min_rows=3)
    y, notes = _outcome(rows, rel, y_name, model)
    design = design_matrix(rows, rel, xs, standardize_numeric=standardize)
    check_rank(design.matrix, design.terms)
    groups = None
    if cluster:
        labels = [_canonical(v) for v in rows.table.column(cluster).to_pylist()]
        _, groups = np.unique(np.array(labels, dtype=object), return_inverse=True)
        n_clusters = int(groups.max()) + 1
        if n_clusters < 2:
            raise StatsInputError(f"Clustering by '{cluster}' needs at least 2 clusters.")
        if n_clusters < 30:
            notes.append(
                f"Only {n_clusters} clusters: clustered standard errors are unreliable below "
                "about 30."
            )
    result, fit_notes = fit_model(model, y, design.matrix, robust=robust, clusters=groups)
    notes = [*rows.notes(), *design.notes, *notes, *fit_notes]
    se_label = (
        f"standard errors clustered by {cluster}"
        if cluster
        else ("robust (HC1) standard errors" if robust else "classical standard errors")
    )
    return result, design, rows, notes, se_label


def _fit_summary(result: Any, model: Model) -> dict[str, object]:
    summary: dict[str, object] = {"nObs": int(result.nobs)}
    if model == "ols":
        summary["r2"] = float(result.rsquared)
        summary["adjR2"] = float(result.rsquared_adj)
    else:
        summary["pseudoR2"] = float(result.prsquared)
        summary["llf"] = float(result.llf)
    summary["aic"] = float(result.aic)
    summary["bic"] = float(result.bic)
    return summary


# ---------------------------------------------------------------------------------------------
# Coefficient plot
# ---------------------------------------------------------------------------------------------


class CoefficientParams(PlotParams):
    model: Model = "ols"
    robust: bool = False
    standardize: bool = False
    ci_level: float = CiLevel
    # Cosmetic.
    show_intercept: bool = False
    exponentiate: bool = False
    sort: Literal["model", "estimate"] = "model"


REGRESSION_CHANNELS = [
    Channel("y", None),
    Channel("x", None, min=1, max=MAX_TERMS),
    Channel("cluster", None, min=0),
]


@register_plot(PlotType.COEFFICIENT, channels=REGRESSION_CHANNELS, params=CoefficientParams)
def coefficient(req: PlotRequest) -> StatResult:
    params = req.params
    assert isinstance(params, CoefficientParams)
    result, design, rows, notes, se_label = _model_fit(
        req, params.model, params.robust, standardize=params.standardize
    )
    ci = np.asarray(result.conf_int(alpha=1 - params.ci_level))
    stat_name = "t" if params.model == "ols" else "z"
    terms = [
        {
            "term": term,
            "estimate": float(result.params[i]),
            "se": float(result.bse[i]),
            "ciLow": float(ci[i, 0]),
            "ciHigh": float(ci[i, 1]),
            "statistic": float(result.tvalues[i]),
            "pValue": float(result.pvalues[i]),
            "intercept": design.sources[i] < 0,
        }
        for i, term in enumerate(design.terms)
    ]
    scale = "; numeric predictors per SD" if params.standardize else ""
    return stat_result(
        {
            "terms": terms,
            "model": params.model,
            "outcome": req.field("y"),
            "ciLevel": params.ci_level,
            "statistic": stat_name,
            **_fit_summary(result, params.model),
        },
        method=f"{MODEL_LABEL[params.model]} regression, {se_label}{scale}",
        n=rows.n,
        n_total=rows.n_total,
        n_missing=rows.n_missing,
        warnings=notes,
    )


# ---------------------------------------------------------------------------------------------
# Marginal effects
# ---------------------------------------------------------------------------------------------


class MarginalParams(PlotParams):
    model: Model = "logit"
    at: Literal["overall", "mean", "median"] = "overall"
    discrete: bool = True
    robust: bool = False
    ci_level: float = CiLevel
    # Cosmetic.
    sort: Literal["model", "estimate"] = "model"


@register_plot(PlotType.MARGINAL_EFFECTS, channels=REGRESSION_CHANNELS, params=MarginalParams)
def marginal_effects(req: PlotRequest) -> StatResult:
    params = req.params
    assert isinstance(params, MarginalParams)
    result, design, rows, notes, se_label = _model_fit(req, params.model, params.robust)
    slopes = [i for i, s in enumerate(design.sources) if s >= 0]
    alpha = 1 - params.ci_level
    if params.model == "ols":
        # Linear model without interactions: dy/dx is the coefficient everywhere.
        ci = np.asarray(result.conf_int(alpha=alpha))
        estimate = np.asarray(result.params)[slopes]
        se = np.asarray(result.bse)[slopes]
        low, high = ci[slopes, 0], ci[slopes, 1]
        pvalues = np.asarray(result.pvalues)[slopes]
        how = "analytic (the OLS slopes)"
    else:
        margeff = result.get_margeff(at=params.at, method="dydx", dummy=params.discrete)
        estimate = np.asarray(margeff.margeff)
        se = np.asarray(margeff.margeff_se)
        interval = np.asarray(margeff.conf_int(alpha=alpha))
        low, high = interval[:, 0], interval[:, 1]
        pvalues = np.asarray(margeff.pvalues)
        where = {
            "overall": "averaged over rows",
            "mean": "at the means",
            "median": "at the medians",
        }
        how = f"dy/dx {where[params.at]}, delta-method standard errors"
        if params.discrete:
            how += "; 0/1 terms as a discrete change"
    effects = [
        {
            "term": design.terms[i],
            "estimate": float(estimate[k]),
            "se": float(se[k]),
            "ciLow": float(low[k]),
            "ciHigh": float(high[k]),
            "pValue": float(pvalues[k]),
        }
        for k, i in enumerate(slopes)
    ]
    scale = "probability" if params.model in ("logit", "probit") else "outcome"
    return stat_result(
        {
            "effects": effects,
            "model": params.model,
            "outcome": req.field("y"),
            "scale": scale,
            "ciLevel": params.ci_level,
            **_fit_summary(result, params.model),
        },
        method=f"Marginal effects of a {MODEL_LABEL[params.model]} model: {how}; {se_label}",
        n=rows.n,
        n_total=rows.n_total,
        n_missing=rows.n_missing,
        warnings=notes,
    )


# ---------------------------------------------------------------------------------------------
# Kaplan-Meier
# ---------------------------------------------------------------------------------------------


class KaplanMeierParams(PlotParams):
    ci_level: float = CiLevel
    ci_type: Literal["log-log", "log", "plain"] = "log-log"
    max_groups: int = Field(default=10, ge=1, le=30)
    # The event field's value meaning "the event happened"; "" = 1/true/2-of-1,2/automatic.
    event_value: str = Field(default="", max_length=200)
    # Cosmetic.
    show_ci: bool = True
    show_censored: bool = True
    show_median: bool = False


# Band transform -> statsmodels' quantile_ci method, so the median's interval matches the band.
_MEDIAN_CI = {"log-log": "cloglog", "log": "log", "plain": "linear"}


def km_bands(
    survival: np.ndarray, se: np.ndarray, level: float, kind: str
) -> tuple[np.ndarray, np.ndarray]:
    """Pointwise bands from Greenwood's SE of S(t), as R's survfit conf.type."""
    z = _z(level)
    s = np.asarray(survival, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        se_log = np.where(s > 0, se / s, np.nan)  # SE of log S
        if kind == "plain":
            lower, upper = s - z * se, s + z * se
        elif kind == "log":
            lower, upper = s * np.exp(-z * se_log), s * np.exp(z * se_log)
        else:
            log_s = np.log(s)
            a = np.exp(-np.exp(np.log(-log_s) + z * se_log / log_s))
            b = np.exp(-np.exp(np.log(-log_s) - z * se_log / log_s))
            lower, upper = np.minimum(a, b), np.maximum(a, b)
    lower = np.where(s >= 1, 1.0, np.where(s <= 0, 0.0, lower))
    upper = np.where(s >= 1, 1.0, np.where(s <= 0, 0.0, upper))
    no_se = ~np.isfinite(se_log) | (se <= 0)
    lower = np.where(no_se & (s > 0) & (s < 1), s, lower)
    upper = np.where(no_se & (s > 0) & (s < 1), s, upper)
    return np.clip(lower, 0.0, 1.0), np.clip(upper, 0.0, 1.0)


@register_plot(
    PlotType.KAPLAN_MEIER,
    channels=[Channel("time", QUANT), Channel("event", None), Channel("color", CATEGORICAL, min=0)],
    params=KaplanMeierParams,
)
def kaplan_meier(req: PlotRequest) -> StatResult:
    """Kaplan-Meier curves (statsmodels SurvfuncRight) with Greenwood bands and log-rank test.

    Survival, Greenwood SEs, bands and the log-rank test match R's survfit/survdiff. The median
    follows statsmodels: the first time S(t) <= 0.5. Where S(t) sits exactly at 0.5 over an
    interval R reports the interval's midpoint instead, so the two can differ there.
    """
    from statsmodels.duration.survfunc import SurvfuncRight, survdiff

    params = req.params
    assert isinstance(params, KaplanMeierParams)
    time_name, event_name = req.field("time"), req.field("event")
    assert time_name is not None and event_name is not None
    color = req.field("color")
    rel, notes = _groups(req, req.relation, "color", params.max_groups)
    rows = complete_rows(rel, [time_name, event_name], [color] if color else [], cap=MODEL_CAP)
    notes = [*notes, *rows.notes()]
    times = rows.matrix([time_name])[:, 0]
    if np.any(times < 0):
        raise StatsInputError(f"'{time_name}' has negative times.")
    events, _, event_notes = binary_outcome(
        rows.table.column(event_name).to_pylist(),
        event_name,
        params.event_value or None,
        need_both=False,
    )
    notes += event_notes
    keys = (
        np.array([("(missing)" if v is None else v) for v in rows.text(color)], dtype=object)
        if color
        else np.array(["All"] * rows.n, dtype=object)
    )
    groups_out: list[dict[str, object]] = []
    for key in sorted(set(keys.tolist())):
        mask = keys == key
        t, e = times[mask], events[mask]
        n_events = int(e.sum())
        entry: dict[str, object] = {"key": key, "n": int(mask.sum()), "events": n_events}
        if n_events == 0:
            notes.append(f"{key}: no events, so survival stays at 1.")
            entry.update(
                time=[0.0, float(t.max())],
                survival=[1.0, 1.0],
                lower=[1.0, 1.0],
                upper=[1.0, 1.0],
                atRisk=[int(mask.sum()), int((t >= t.max()).sum())],
                median=None,
                medianLow=None,
                medianHigh=None,
            )
            surv_times = np.array([0.0])
            surv_prob = np.array([1.0])
        else:
            sf = SurvfuncRight(t, e)
            surv_times = np.asarray(sf.surv_times, dtype=float)
            surv_prob = np.asarray(sf.surv_prob, dtype=float)
            lower, upper = km_bands(
                surv_prob, np.asarray(sf.surv_prob_se), params.ci_level, params.ci_type
            )
            keep = _thin(len(surv_times))
            if len(keep) < len(surv_times):
                notes.append(f"{key}: the curve is drawn through {len(keep):,} of its steps.")
            median = float(sf.quantile(0.5))
            try:
                # Same transform as the band (statsmodels' names). log(0) where S reaches 0
                # leaves those ends infinite (null).
                with np.errstate(divide="ignore", invalid="ignore"):
                    m_low, m_high = sf.quantile_ci(
                        0.5, alpha=1 - params.ci_level, method=_MEDIAN_CI[params.ci_type]
                    )
            except (ValueError, IndexError, ZeroDivisionError):  # degenerate intervals
                m_low = m_high = math.nan
            entry.update(
                time=np.r_[0.0, surv_times[keep]],
                survival=np.r_[1.0, surv_prob[keep]],
                lower=np.r_[1.0, lower[keep]],
                upper=np.r_[1.0, upper[keep]],
                atRisk=np.r_[int(mask.sum()), np.asarray(sf.n_risk)[keep]].astype(int),
                median=median,
                medianLow=m_low,
                medianHigh=m_high,
            )
            if not math.isfinite(median):
                notes.append(f"{key}: survival never falls to 50%, so the median is undefined.")
        censored = np.sort(t[e == 0])
        index = np.searchsorted(surv_times, censored, side="right") - 1
        at_censor = np.where(index >= 0, surv_prob[np.clip(index, 0, None)], 1.0)
        if n_events == 0:
            at_censor = np.ones_like(censored)
        pick = _thin(len(censored), 500)
        entry["censorTime"] = censored[pick]
        entry["censorSurvival"] = at_censor[pick]
        groups_out.append(entry)
    log_rank = None
    if len(groups_out) > 1:
        if events.sum() == 0:
            notes.append("No events at all, so the log-rank test is undefined.")
        else:
            statistic, p_value = survdiff(times, events, keys)
            log_rank = {
                "statistic": float(statistic),
                "df": len(groups_out) - 1,
                "pValue": float(p_value),
            }
    return stat_result(
        {
            "groups": groups_out,
            "logRank": log_rank,
            "ciLevel": params.ci_level,
            "ciType": params.ci_type,
        },
        method=(
            f"Kaplan-Meier estimate, Greenwood {params.ci_type} pointwise "
            f"{params.ci_level:.0%} band" + ("; log-rank test" if log_rank else "")
        ),
        n=rows.n,
        n_total=rows.n_total,
        n_missing=rows.n_missing,
        warnings=notes,
    )


# ---------------------------------------------------------------------------------------------
# ROC and precision-recall
# ---------------------------------------------------------------------------------------------


class RocParams(PlotParams):
    positive: str = ""
    ci_level: float = CiLevel
    # Cosmetic.
    show_diagonal: bool = True
    show_best: bool = True


def _midrank(x: np.ndarray) -> np.ndarray:
    return np.asarray(sps.rankdata(x, method="average"), dtype=float)


def delong(scores: np.ndarray, truth: np.ndarray) -> tuple[float, float]:
    """AUC and its DeLong variance (Sun & Xu's fast algorithm, O(n log n))."""
    pos, neg = scores[truth == 1], scores[truth == 0]
    m, n = len(pos), len(neg)
    tx, ty = _midrank(pos), _midrank(neg)
    tz = _midrank(np.concatenate([pos, neg]))
    auc = (tz[:m].sum() - m * (m + 1) / 2) / (m * n)
    v01 = (tz[:m] - tx) / n
    v10 = 1.0 - (tz[m:] - ty) / m
    s01 = float(np.var(v01, ddof=1)) if m > 1 else 0.0
    s10 = float(np.var(v10, ddof=1)) if n > 1 else 0.0
    return float(auc), s01 / m + s10 / n


def _classifier_rows(
    req: PlotRequest, positive: str
) -> tuple[Rows, np.ndarray, str, list[str], list[str]]:
    truth = req.field("truth")
    assert truth is not None
    scores = req.fields("score")
    if truth in scores:
        raise StatsInputError("The outcome can't also be a score.")
    rows = complete_rows(req.relation, [truth, *scores], cap=MODEL_CAP, min_rows=2)
    y, chosen, notes = binary_outcome(rows.table.column(truth).to_pylist(), truth, positive or None)
    return rows, y, chosen, scores, [*rows.notes(), *notes]


SCORE_CHANNELS = [Channel("truth", None), Channel("score", QUANT, min=1, max=6)]


@register_plot(PlotType.ROC, channels=SCORE_CHANNELS, params=RocParams)
def roc(req: PlotRequest) -> StatResult:
    from sklearn.metrics import roc_curve

    params = req.params
    assert isinstance(params, RocParams)
    rows, y, chosen, scores, notes = _classifier_rows(req, params.positive)
    z = _z(params.ci_level)
    curves = []
    for name in scores:
        s = rows.matrix([name])[:, 0]
        fpr, tpr, thresholds = roc_curve(y, s, drop_intermediate=True)
        auc, variance = delong(s, y)
        se = math.sqrt(max(variance, 0.0))
        best = int(np.argmax(tpr - fpr))
        keep = _thin(len(fpr))
        if auc < 0.5:
            notes.append(f"{name}: AUC is below 0.5, so higher scores go with the negative class.")
        if auc in (0.0, 1.0):
            notes.append(
                f"{name}: the classes are perfectly separated, so the AUC interval has zero width."
            )
        elif se == 0:
            notes.append(f"{name}: the AUC's standard error is zero, so its interval has no width.")
        curves.append(
            {
                "score": name,
                "fpr": fpr[keep],
                "tpr": tpr[keep],
                "threshold": thresholds[keep],
                "auc": auc,
                "aucSe": se,
                "aucLow": max(0.0, auc - z * se),
                "aucHigh": min(1.0, auc + z * se),
                "best": {
                    "threshold": float(thresholds[best])
                    if math.isfinite(thresholds[best])
                    else None,
                    "fpr": float(fpr[best]),
                    "tpr": float(tpr[best]),
                    "youden": float(tpr[best] - fpr[best]),
                },
            }
        )
    return stat_result(
        {
            "curves": curves,
            "positive": chosen,
            "nPositive": int(y.sum()),
            "nNegative": int(len(y) - y.sum()),
            "ciLevel": params.ci_level,
        },
        method=f"ROC curve; AUC with a DeLong {params.ci_level:.0%} interval",
        n=rows.n,
        n_total=rows.n_total,
        n_missing=rows.n_missing,
        warnings=notes,
    )


class PrecisionRecallParams(PlotParams):
    positive: str = ""
    # Cosmetic.
    show_baseline: bool = True
    show_best: bool = True


@register_plot(PlotType.PRECISION_RECALL, channels=SCORE_CHANNELS, params=PrecisionRecallParams)
def precision_recall(req: PlotRequest) -> StatResult:
    from sklearn.metrics import average_precision_score, precision_recall_curve

    params = req.params
    assert isinstance(params, PrecisionRecallParams)
    rows, y, chosen, scores, notes = _classifier_rows(req, params.positive)
    curves = []
    for name in scores:
        s = rows.matrix([name])[:, 0]
        precision, recall, thresholds = precision_recall_curve(y, s)
        ap = float(average_precision_score(y, s))
        with np.errstate(invalid="ignore", divide="ignore"):
            f1 = np.where(
                precision + recall > 0, 2 * precision * recall / (precision + recall), 0.0
            )
        best = int(np.argmax(f1[:-1])) if len(f1) > 1 else 0
        keep = _thin(len(precision))
        # The last point (recall 0, precision 1) has no threshold.
        threshold = np.r_[thresholds, np.nan]
        curves.append(
            {
                "score": name,
                "recall": recall[keep],
                "precision": precision[keep],
                "threshold": threshold[keep],
                "averagePrecision": ap,
                "best": {
                    "threshold": float(threshold[best]) if math.isfinite(threshold[best]) else None,
                    "precision": float(precision[best]),
                    "recall": float(recall[best]),
                    "f1": float(f1[best]),
                },
            }
        )
    return stat_result(
        {
            "curves": curves,
            "positive": chosen,
            "prevalence": float(y.mean()),
            "nPositive": int(y.sum()),
            "nNegative": int(len(y) - y.sum()),
        },
        method="Precision-recall curve; average precision (step-wise area, as scikit-learn)",
        n=rows.n,
        n_total=rows.n_total,
        n_missing=rows.n_missing,
        warnings=notes,
    )


# ---------------------------------------------------------------------------------------------
# Lorenz curve and quantile plot (exact, SQL)
# ---------------------------------------------------------------------------------------------


class LorenzParams(PlotParams):
    points: int = Field(default=200, ge=10, le=MAX_CURVE_POINTS)
    max_groups: int = Field(default=10, ge=1, le=30)
    # Cosmetic.
    show_equality: bool = True
    fill: bool = True


def _ranked(rel: Relation, value: str, group: str | None, extra: exp.Expr | None = None) -> str:
    """value v, group g, rank rn, group size n and running sum, over usable rows."""
    target = valid_rows(rel, [value])
    if extra is not None:
        target = target.narrow(extra)
    g = exp.cast(col(group), "VARCHAR") if group else exp.Literal.string("All")
    base = _sql(target.select(alias(g, "g"), alias(as_double(col(value)), "v")))
    return (
        "SELECT g, v, row_number() OVER w AS rn, count(*) OVER (PARTITION BY g) AS n, "
        "sum(v) OVER (w ROWS UNBOUNDED PRECEDING) AS cum, sum(v) OVER (PARTITION BY g) AS total "
        f"FROM ({base}) AS _b WINDOW w AS (PARTITION BY g ORDER BY v)"
    )


@register_plot(
    PlotType.LORENZ,
    channels=[Channel("y", QUANT), Channel("color", CATEGORICAL, min=0)],
    params=LorenzParams,
)
def lorenz(req: PlotRequest) -> StatResult:
    params = req.params
    assert isinstance(params, LorenzParams)
    name = req.field("y")
    assert name is not None
    group = req.field("color")
    rel, notes = _groups(req, req.relation, "color", params.max_groups)
    usable = valid_rows(rel, [name])
    negative = exp.LT(this=col(name), expression=exp.Literal.number(0))
    counts = usable.rows(usable.select(count_star(), count_if(negative)))[0]
    total_rows = rel.count()
    n_missing = total_rows - int(counts[0])
    if counts[1]:
        notes.append(f"Left out {int(counts[1]):,} negative values: a Lorenz curve needs shares.")
    ranked = _ranked(rel, name, group, exp.GTE(this=col(name), expression=exp.Literal.number(0)))
    points = int(params.points)
    curve_sql = (
        f"SELECT g, rn, n, cum / NULLIF(total, 0) AS share FROM ({ranked}) AS _r "
        f"WHERE rn = n OR rn % greatest(1, n // {points}) = 0 ORDER BY g, rn"
    )
    gini_sql = (
        "SELECT g, count(*) AS n, sum(v) AS s, sum(rn * v) AS w "
        f"FROM ({ranked}) AS _r GROUP BY g ORDER BY g"
    )
    curve = rel.arrow(curve_sql).to_pydict()
    groups_out: list[dict[str, object]] = []
    n_used = 0
    for g, n, s, w in rel.rows(gini_sql):
        n, s, w = int(n), float(s or 0), float(w or 0)
        n_used += n
        mask = [i for i, key in enumerate(curve["g"]) if key == g]
        x = [0.0] + [curve["rn"][i] / n for i in mask]
        share = [0.0] + [curve["share"][i] for i in mask]
        gini: float | None = 2 * w / (n * s) - (n + 1) / n if s > 0 else None
        if gini is None:
            notes.append(f"{g}: every value is zero, so the Gini coefficient is undefined.")
        groups_out.append(
            {
                "key": g if g is not None else "(missing)",
                "n": n,
                "total": s,
                "gini": gini,
                "x": x,
                "y": share if s > 0 else None,
            }
        )
    if not groups_out:
        raise StatsInputError(f"'{name}' has no non-negative values.")
    return stat_result(
        {"groups": groups_out},
        method="Lorenz curve and Gini coefficient (exact, all rows)",
        n=n_used,
        n_missing=n_missing,
        sampled=False,
        warnings=notes,
    )


class QuantileParams(PlotParams):
    max_points: int = Field(default=1000, ge=10, le=MAX_CURVE_POINTS)
    max_groups: int = Field(default=10, ge=1, le=30)
    # Cosmetic.
    show_quartiles: bool = True
    connect: bool = False


@register_plot(
    PlotType.QUANTILE,
    channels=[Channel("y", QUANT), Channel("color", CATEGORICAL, min=0)],
    params=QuantileParams,
)
def quantile_plot(req: PlotRequest) -> StatResult:
    params = req.params
    assert isinstance(params, QuantileParams)
    name = req.field("y")
    assert name is not None
    group = req.field("color")
    rel, notes = _groups(req, req.relation, "color", params.max_groups)
    ranked = _ranked(rel, name, group)
    m = int(params.max_points)
    # Exact order statistics at evenly spaced ranks: every rank when n <= max_points.
    sql = (
        f"SELECT g, n, (rn - 0.5) / n AS f, v FROM ({ranked}) AS _r "
        f"WHERE n <= {m} OR rn = 1 OR rn = n "
        f"OR floor((rn - 1) * ({m} - 1) / (n - 1)) <> floor((rn - 2) * ({m} - 1) / (n - 1)) "
        "ORDER BY g, rn"
    )
    data = rel.arrow(sql).to_pydict()
    usable = valid_rows(rel, [name])
    probs = fn("quantile_cont", as_double(col(name)), num_list([0.25, 0.5, 0.75]))
    if group:
        g_expr = exp.cast(col(group), "VARCHAR")
        query = usable.select(alias(g_expr, "g"), probs).group_by(g_expr.copy())
    else:
        query = usable.select(alias(exp.Literal.string("All"), "g"), probs)
    quartiles = {g: q for g, q in usable.rows(query) if q is not None}
    groups_out = []
    exact = True
    n_used = 0
    for key in sorted(quartiles, key=lambda k: (k is None, str(k))):
        mask = [i for i, g in enumerate(data["g"]) if g == key]
        n = int(data["n"][mask[0]]) if mask else 0
        n_used += n
        exact = exact and n <= m
        q1, median, q3 = quartiles[key]
        groups_out.append(
            {
                "key": key if key is not None else "(missing)",
                "n": n,
                "f": [data["f"][i] for i in mask],
                "value": [data["v"][i] for i in mask],
                "q1": q1,
                "median": median,
                "q3": q3,
            }
        )
    if n_used == 0:
        raise StatsInputError(f"'{name}' has no values.")
    if not exact:
        notes.append(f"Large groups are drawn through {m:,} exact order statistics each.")
    return stat_result(
        {"groups": groups_out, "exact": exact},
        method="Quantile plot: order statistics at f = (i - 0.5) / n (exact, all rows)",
        n=n_used,
        n_missing=rel.count() - n_used,
        sampled=False,
        warnings=notes,
    )


# ---------------------------------------------------------------------------------------------
# Forest plot (meta-analysis)
# ---------------------------------------------------------------------------------------------


class ForestParams(PlotParams):
    measure: Literal["difference", "ratio"] = "difference"
    pooled: Literal["random", "fixed", "none"] = "random"
    ci_level: float = CiLevel
    max_rows: int = Field(default=200, ge=1, le=1000)
    # Cosmetic.
    sort: Literal["data", "estimate", "label"] = "data"
    null_line: bool = True


@dataclass(frozen=True)
class Pooled:
    estimate: float
    se: float
    tau2: float
    q: float
    df: int
    i2: float | None
    weights: np.ndarray


def pool_effects(y: np.ndarray, v: np.ndarray, method: Literal["fixed", "random"]) -> Pooled:
    """Inverse-variance pooling; random effects with the DerSimonian-Laird tau^2."""
    w = 1.0 / v
    fixed = float((w * y).sum() / w.sum())
    q = float((w * (y - fixed) ** 2).sum())
    df = len(y) - 1
    tau2 = 0.0
    if method == "random" and df > 0:
        c = float(w.sum() - (w**2).sum() / w.sum())
        tau2 = max(0.0, (q - df) / c) if c > 0 else 0.0
    weights = 1.0 / (v + tau2)
    estimate = float((weights * y).sum() / weights.sum())
    i2 = max(0.0, (q - df) / q) if df > 0 and q > 0 else (0.0 if df > 0 else None)
    return Pooled(estimate, math.sqrt(1.0 / weights.sum()), tau2, q, df, i2, weights)


@register_plot(
    PlotType.FOREST,
    channels=[
        Channel("label", None),
        Channel("estimate", QUANT),
        Channel("lower", QUANT, min=0),
        Channel("upper", QUANT, min=0),
        Channel("se", QUANT, min=0),
        Channel("color", CATEGORICAL, min=0),
    ],
    params=ForestParams,
)
def forest(req: PlotRequest) -> StatResult:
    params = req.params
    assert isinstance(params, ForestParams)
    label, est = req.field("label"), req.field("estimate")
    lower, upper, se_name, color = (req.field(c) for c in ("lower", "upper", "se", "color"))
    assert label is not None and est is not None
    if se_name is None and (lower is None or upper is None):
        raise StatsInputError("Choose a standard error field, or both lower and upper limits.")
    required = [est, *(f for f in (lower, upper, se_name) if f)]
    rows = complete_rows(
        req.relation,
        list(dict.fromkeys(required)),
        [f for f in (label, color) if f and f not in required],
        cap=params.max_rows,
    )
    notes = rows.notes("studies")
    if rows.sampled:
        notes.append(
            f"Showing {rows.n} of {rows.n_total} studies (the limit is {params.max_rows})."
        )
    ratio = params.measure == "ratio"
    z = _z(params.ci_level)
    estimate = rows.matrix([est])[:, 0]
    low = rows.matrix([lower])[:, 0] if lower else None
    high = rows.matrix([upper])[:, 0] if upper else None
    if ratio:
        bad = estimate <= 0
        if low is not None and high is not None:
            bad |= (low <= 0) | (high <= 0)
        if np.any(bad):
            raise StatsInputError("Ratios (and their limits) must be positive.")
    to_scale = np.log if ratio else (lambda a: a)
    y = to_scale(estimate)
    if se_name:
        se = rows.matrix([se_name])[:, 0]
    else:
        assert low is not None and high is not None
        se = (to_scale(high) - to_scale(low)) / (2 * z)
        if np.any(high < low):
            raise StatsInputError("Some rows have an upper limit below the lower limit.")
    if low is None or high is None:
        back = np.exp if ratio else (lambda a: a)
        low, high = back(y - z * se), back(y + z * se)
    usable = se > 0
    if not np.all(usable):
        notes.append(
            f"{int((~usable).sum())} studies have no width (SE 0) and are left out of pooling."
        )
    labels = rows.text(label)
    groups = rows.text(color) if color else [None] * rows.n
    back_fn = np.exp if ratio else (lambda a: a)
    pooled: dict[str, object] | None = None
    subgroups: list[dict[str, object]] = []
    weights = np.full(rows.n, np.nan)

    def summarize(mask: np.ndarray, key: str | None) -> tuple[dict[str, object], np.ndarray] | None:
        m = mask & usable
        if m.sum() == 0:
            return None
        method: Literal["fixed", "random"] = "fixed" if params.pooled == "fixed" else "random"
        result = pool_effects(y[m], se[m] ** 2, method)
        if result.df == 0:
            notes.append((f"{key}: " if key else "") + "one study, so heterogeneity is undefined.")
        q_p = float(sps.chi2.sf(result.q, result.df)) if result.df > 0 else None
        center = result.estimate
        return (
            {
                "key": key,
                "k": int(m.sum()),
                "estimate": float(back_fn(center)),
                "lower": float(back_fn(center - z * result.se)),
                "upper": float(back_fn(center + z * result.se)),
                "se": result.se,
                "pValue": float(2 * sps.norm.sf(abs(center / result.se))),
                "tau2": result.tau2,
                "q": result.q if result.df > 0 else None,
                "qDf": result.df,
                "qPValue": q_p,
                "i2": result.i2 if result.df > 0 else None,
            },
            m,
        )

    if params.pooled != "none":
        overall = summarize(np.ones(rows.n, dtype=bool), None)
        if overall is not None:
            pooled, m = overall
            method: Literal["fixed", "random"] = "fixed" if params.pooled == "fixed" else "random"
            w = pool_effects(y[m], se[m] ** 2, method).weights
            weights[m] = 100 * w / w.sum()
        if color:
            for key in sorted({g for g in groups if g is not None}):
                part = summarize(np.array([g == key for g in groups]), key)
                if part is not None:
                    subgroups.append(part[0])
    study_rows = [
        {
            "label": labels[i] if labels[i] is not None else f"Row {i + 1}",
            "group": groups[i],
            "estimate": float(estimate[i]),
            "lower": float(low[i]),
            "upper": float(high[i]),
            "se": float(se[i]),
            "weight": weights[i],
        }
        for i in range(rows.n)
    ]
    model = {
        "random": "random effects (DerSimonian-Laird)",
        "fixed": "fixed effect (inverse variance)",
        "none": "no pooling",
    }[params.pooled]
    scale = " on the log scale" if ratio else ""
    return stat_result(
        {
            "rows": study_rows,
            "pooled": pooled,
            "subgroups": subgroups,
            "measure": params.measure,
            "ciLevel": params.ci_level,
            "nullValue": 1.0 if ratio else 0.0,
        },
        method=f"Forest plot, {model}{scale}",
        n=rows.n,
        n_total=rows.n_total,
        n_missing=rows.n_missing,
        sampled=rows.sampled,
        warnings=notes,
    )


# ---------------------------------------------------------------------------------------------
# Impulse response functions (VAR)
# ---------------------------------------------------------------------------------------------


class ImpulseParams(PlotParams):
    # 0 = choose by AIC (up to 8 lags).
    lags: int = Field(default=0, ge=0, le=24)
    periods: int = Field(default=12, ge=1, le=60)
    orthogonalized: bool = True
    ci_level: float = CiLevel
    # Cosmetic: show one impulse (a field name) or all.
    impulse: str = ""


@register_plot(
    PlotType.IMPULSE_RESPONSE,
    channels=[Channel("y", QUANT, min=2, max=6), Channel("time", NUMERIC_OR_TIME)],
    params=ImpulseParams,
)
def impulse_response(req: PlotRequest) -> StatResult:
    from statsmodels.tsa.api import VAR

    params = req.params
    assert isinstance(params, ImpulseParams)
    fields = req.fields("y")
    time = req.field("time")
    assert time is not None
    if time in fields or len(set(fields)) != len(fields):
        raise StatsInputError("Choose distinct series, and a separate time field.")
    rel = req.relation
    usable = valid_rows(rel, [time, *fields])
    total = rel.count()
    n = usable.count()
    if n > MAX_SERIES_ROWS:
        raise StatsInputError(
            f"There are {n:,} time points; a VAR here takes at most {MAX_SERIES_ROWS:,}. "
            "Aggregate by period first (for example with a pipeline)."
        )
    table = usable.arrow(
        usable.select(col(time), *[alias(as_double(col(f)), f) for f in fields]).order_by(col(time))
    )
    stamps = table.column(0).to_pylist()
    if len(set(stamps)) != len(stamps):
        raise StatsInputError(
            f"'{time}' repeats; a VAR needs one row per period (aggregate first)."
        )
    data = np.column_stack([table.column(f).to_numpy(zero_copy_only=False) for f in fields])
    k = len(fields)
    notes: list[str] = []
    if n < total:
        notes.append(f"Left out {total - n:,} periods with a missing value; gaps are joined.")

    def too_few(lags: int) -> bool:
        # Each equation estimates k * lags + 1 coefficients from n - lags periods.
        return n - lags <= k * lags + 1

    wanted = params.lags or 1
    if too_few(wanted):
        raise StatsInputError(
            f"{n} periods are too few for {k} series with {wanted} lag"
            f"{'s' if wanted > 1 else ''}; use fewer lags or series."
        )
    model = VAR(data)
    try:
        if params.lags == 0:
            max_lags = max(lag for lag in range(1, 9) if not too_few(lag))
            try:
                order = model.select_order(maxlags=max_lags)
                lags = max(1, int(order.aic))
                chosen = f"{lags} lag{'s' if lags > 1 else ''} chosen by AIC"
            except ValueError:  # too few periods to compare lag orders
                lags, chosen = 1, "1 lag (too few periods to choose by AIC)"
        else:
            lags = params.lags
            chosen = f"{lags} lag{'s' if lags > 1 else ''}"
        result = model.fit(lags)
        irf = result.irf(params.periods)
    except (np.linalg.LinAlgError, ValueError) as exc:
        raise StatsInputError(
            "The VAR can't be fitted: a series is constant or the series are collinear."
        ) from exc
    responses = irf.orth_irfs if params.orthogonalized else irf.irfs
    stderr = irf.stderr(orth=params.orthogonalized)
    z = _z(params.ci_level)
    out = []
    for j, impulse in enumerate(fields):
        for i, response in enumerate(fields):
            path = responses[:, i, j]
            err = stderr[:, i, j]
            out.append(
                {
                    "impulse": impulse,
                    "response": response,
                    "irf": path,
                    "lower": path - z * err,
                    "upper": path + z * err,
                }
            )
    stable = bool(result.is_stable())
    if not stable:
        notes.append("The VAR is not stable (a root is on or outside the unit circle).")
    if params.orthogonalized:
        notes.append(
            "Orthogonalized (Cholesky) responses depend on the order of the series: "
            f"{' > '.join(fields)}."
        )
    return stat_result(
        {
            "variables": fields,
            "periods": params.periods,
            "lags": lags,
            "orthogonalized": params.orthogonalized,
            "ciLevel": params.ci_level,
            "stable": stable,
            "responses": out,
        },
        method=(
            f"VAR({lags}) impulse responses ({chosen}), asymptotic {params.ci_level:.0%} bands"
        ),
        n=n,
        n_missing=total - n,
        sampled=False,
        warnings=notes,
    )


# ---------------------------------------------------------------------------------------------
# Event study
# ---------------------------------------------------------------------------------------------


class EventStudyParams(PlotParams):
    window_start: int = Field(default=-5, ge=-30, le=-1)
    window_end: int = Field(default=5, ge=0, le=30)
    reference: int = Field(default=-1, ge=-30, le=30)
    fixed_effects: Literal["unit_time", "unit", "time", "none"] = "unit_time"
    bin_endpoints: bool = True
    cluster: bool = True
    ci_level: float = CiLevel


def demean(
    matrix: np.ndarray, factors: Sequence[np.ndarray], *, tol: float = 1e-12, max_iter: int = 10_000
) -> np.ndarray:
    """Partial out fixed effects by alternating projections (exact for one factor and for
    balanced two-way panels; converges for unbalanced ones)."""
    out = np.array(matrix, dtype=float, copy=True)
    if not factors:
        return out
    counts = [np.bincount(f).astype(float) for f in factors]
    scale = max(1.0, float(np.abs(out).max(initial=0.0)))
    for _ in range(max_iter if len(factors) > 1 else 1):
        before = out.copy()
        for f, c in zip(factors, counts, strict=True):
            sums = np.zeros((len(c), out.shape[1]))
            np.add.at(sums, f, out)
            out -= (sums / c[:, None])[f]
        if np.abs(out - before).max(initial=0.0) <= tol * scale:
            break
    return out


@register_plot(
    PlotType.EVENT_STUDY,
    channels=[
        Channel("y", QUANT),
        Channel("unit", None),
        Channel("time", QUANT),
        Channel("treatTime", QUANT, min=0),
        Channel("eventTime", QUANT, min=0),
        Channel("x", QUANT, min=0, max=10),
    ],
    params=EventStudyParams,
)
def event_study(req: PlotRequest) -> StatResult:
    params = req.params
    assert isinstance(params, EventStudyParams)
    lo, hi, ref = params.window_start, params.window_end, params.reference
    if not lo <= ref <= hi:
        raise StatsInputError("The reference period must lie inside the event window.")
    y_name, unit, time = req.field("y"), req.field("unit"), req.field("time")
    assert y_name is not None and unit is not None and time is not None
    treat, event = req.field("treatTime"), req.field("eventTime")
    if (treat is None) == (event is None):
        raise StatsInputError(
            "Choose either the treatment period or the event time (periods since treatment)."
        )
    controls = req.fields("x")
    timing = event or treat
    assert timing is not None
    rows = complete_rows(
        req.relation, [y_name, unit, time, *controls], [timing], cap=MODEL_CAP, min_rows=3
    )
    notes = rows.notes()
    y = rows.matrix([y_name])[:, 0]
    period = rows.matrix([time])[:, 0]
    timing_values = rows.matrix([timing])[:, 0]
    rel_time = timing_values if event else period - timing_values
    treated = np.isfinite(rel_time)
    if not treated.any():
        raise StatsInputError("No row is treated: every treatment time is empty.")
    rounded = np.round(rel_time)
    if np.any(np.abs(rel_time[treated] - rounded[treated]) > 1e-9):
        notes.append("Event times were rounded to whole periods.")
    rel_time = rounded
    keep = np.ones(rows.n, dtype=bool)
    if params.bin_endpoints:
        rel_time = np.where(treated, np.clip(rel_time, lo, hi), rel_time)
    else:
        outside = treated & ((rel_time < lo) | (rel_time > hi))
        if outside.any():
            notes.append(f"Left out {int(outside.sum()):,} treated rows outside the window.")
        keep = ~outside
    units = np.array([_canonical(v) for v in rows.table.column(unit).to_pylist()], dtype=object)
    _, unit_codes = np.unique(units[keep], return_inverse=True)
    _, time_codes = np.unique(period[keep], return_inverse=True)
    y, rel_time, treated = y[keep], rel_time[keep], treated[keep]
    x_controls = rows.matrix(controls)[keep] if controls else np.empty((len(y), 0))
    periods = [k for k in range(lo, hi + 1) if k != ref]
    counts = {k: int((treated & (rel_time == k)).sum()) for k in range(lo, hi + 1)}
    present = [k for k in periods if counts[k] > 0]
    missing = [k for k in periods if counts[k] == 0]
    if missing:
        notes.append(f"No treated rows at event time {', '.join(map(str, missing))}.")
    if counts[ref] == 0:
        notes.append(f"No treated rows at the reference period {ref}.")
    if not present:
        raise StatsInputError("No treated rows fall in the event window.")
    dummies = np.column_stack([(treated & (rel_time == k)).astype(float) for k in present])
    x = np.column_stack([dummies, x_controls])
    terms = [f"event {k}" for k in present] + list(controls)
    factors: list[np.ndarray] = []
    if params.fixed_effects in ("unit_time", "unit"):
        factors.append(unit_codes)
    if params.fixed_effects in ("unit_time", "time"):
        factors.append(time_codes)
    if factors:
        both = demean(np.column_stack([y, x]), factors)
        yd, xd = both[:, 0], both[:, 1:]
    else:
        yd, xd = y, np.column_stack([np.ones(len(y)), x])
        terms = ["Intercept", *terms]
    try:
        check_rank(xd, terms)
    except StatsInputError as exc:
        raise StatsInputError(
            f"{exc} With staggered timing and no never-treated units, bin the endpoints or "
            "narrow the window."
        ) from exc
    n, p = xd.shape
    xtx_inv = np.linalg.inv(xd.T @ xd)
    beta = xtx_inv @ (xd.T @ yd)
    resid = yd - xd @ beta
    n_units = int(unit_codes.max()) + 1
    n_times = int(time_codes.max()) + 1
    absorbed = {
        "unit_time": n_units + n_times - 1,
        "unit": n_units,
        "time": n_times,
        "none": 0,
    }[params.fixed_effects]
    if params.cluster:
        # Cluster by unit; fixed effects nested in the clusters (unit) don't count toward the
        # small-sample correction (as reghdfe and fixest).
        nested = n_units if params.fixed_effects in ("unit_time", "unit") else 0
        k_eff = p + absorbed - nested
        if n_units < 2:
            raise StatsInputError("Clustering by unit needs at least 2 units.")
        scores = np.zeros((n_units, p))
        np.add.at(scores, unit_codes, xd * resid[:, None])
        meat = scores.T @ scores
        correction = n_units / (n_units - 1) * (n - 1) / max(1, n - k_eff)
        cov = correction * xtx_inv @ meat @ xtx_inv
        df_test = n_units - 1
        se_label = f"standard errors clustered by {unit}"
        if n_units < 30:
            notes.append(f"Only {n_units} clusters: clustered standard errors are unreliable.")
    else:
        df_resid = n - p - absorbed
        if df_resid <= 0:
            raise StatsInputError("Too few rows for this many fixed effects and terms.")
        sigma2 = float(resid @ resid) / df_resid
        cov = sigma2 * xtx_inv
        df_test = df_resid
        se_label = "classical standard errors"
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    t_crit = float(sps.t.ppf(0.5 + params.ci_level / 2, df_test))
    offset = 0 if factors else 1
    estimates = []
    for k in range(lo, hi + 1):
        if k == ref:
            estimates.append(
                {
                    "relTime": k,
                    "estimate": 0.0,
                    "se": None,
                    "ciLow": None,
                    "ciHigh": None,
                    "n": counts[k],
                    "reference": True,
                }
            )
            continue
        if k not in present:
            continue
        j = offset + present.index(k)
        estimates.append(
            {
                "relTime": k,
                "estimate": float(beta[j]),
                "se": float(se[j]),
                "ciLow": float(beta[j] - t_crit * se[j]),
                "ciHigh": float(beta[j] + t_crit * se[j]),
                "pValue": float(2 * sps.t.sf(abs(beta[j] / se[j]), df_test)) if se[j] > 0 else None,
                "n": counts[k],
                "reference": False,
            }
        )
    pre = [offset + present.index(k) for k in present if k < ref]
    pre_trend = None
    if pre:
        b = beta[pre]
        v = cov[np.ix_(pre, pre)]
        try:
            wald = float(b @ np.linalg.solve(v, b))
            q = len(pre)
            pre_trend = {
                "statistic": wald / q,
                "df": [q, df_test],
                "pValue": float(sps.f.sf(wald / q, q, df_test)),
            }
        except np.linalg.LinAlgError:
            notes.append("The pre-trend test is undefined (singular covariance).")
    fe_label = {
        "unit_time": "unit and period fixed effects",
        "unit": "unit fixed effects",
        "time": "period fixed effects",
        "none": "no fixed effects",
    }[params.fixed_effects]
    binned = "binned endpoints" if params.bin_endpoints else "window only"
    return stat_result(
        {
            "estimates": estimates,
            "preTrend": pre_trend,
            "reference": ref,
            "window": [lo, hi],
            "ciLevel": params.ci_level,
            "nUnits": n_units,
            "nPeriods": n_times,
            "nTreatedUnits": len(np.unique(unit_codes[treated])),
            "fixedEffects": params.fixed_effects,
        },
        method=f"Event study: OLS with event-time dummies ({binned}, reference {ref}), "
        f"{fe_label}, {se_label}",
        n=n,
        n_total=rows.n_total,
        n_missing=rows.n_missing,
        warnings=notes,
    )
