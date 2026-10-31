"""Linear and multiple regression: statsmodels OLS on a random sample of up to 1M rows.

Predictors: numeric, boolean (0/1), dates (days since 1970-01-01) and categorical fields.
A categorical field becomes one dummy per level except the first in sorted order (the
reference level, as R and statsmodels' formulas). Rows missing y or any predictor are left
out.

Before fitting, a pivoted QR of the column-scaled design finds exact collinearity and names
the aliased terms (statsmodels would silently fit a minimum-norm solution). Inference is
classical, or HC3 heteroscedasticity-consistent with `robust`. Checks: residual normality
(Shapiro-Wilk up to 5,000 rows, Jarque-Bera above), Breusch-Pagan heteroscedasticity (with an
intercept), and variance inflation factors (VIF > 10 warns). Without an intercept R^2 is
uncentred, as in statsmodels and R.
"""

from __future__ import annotations

import math
import warnings as py_warnings
from dataclasses import dataclass

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import statsmodels.api as sm
from scipy import linalg
from sqlglot import exp
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.stattools import jarque_bera

from viz.contracts import ColumnSemantic, Scalar, StatResult
from viz.stats.checks import ALPHA, SHAPIRO_MAX, normality_warning, p_text, shapiro
from viz.stats.errors import StatsInputError
from viz.stats.fields import derived, field_kind, key_value, measure_expr, measure_note
from viz.stats.models import (
    AssumptionCheck,
    ReferenceLevel,
    RegressionRequest,
    RegressionTerm,
    RegressionValues,
)
from viz.stats.relation import Relation, col
from viz.stats.results import stat_result
from viz.stats.sampling import DEFAULT_SEED, MODEL_CAP, Sample, reservoir_sample
from viz.stats.sql import alias

# Rows x coefficients budget for the sample (QR costs n p^2).
DESIGN_BUDGET = 50_000_000
MAX_COEFFICIENTS = 200
VIF_WARN = 10.0
# R's lm.fit default tolerance for aliased columns.
ALIAS_TOLERANCE = 1e-7
INTERCEPT = "(Intercept)"


@dataclass(frozen=True)
class _Term:
    name: str
    field: str | None
    level: Scalar


def _present(*exprs: exp.Expr) -> exp.Expr:
    return exp.and_(*[exp.Not(this=exp.Is(this=e.copy(), expression=exp.Null())) for e in exprs])


def _sort_one(value: object) -> tuple[int, object]:
    return (0, value) if value is not None else (1, 0)


def _levels(values: pa.ChunkedArray) -> tuple[np.ndarray, list[object]]:
    """Codes into the sorted list of distinct values."""
    encoded = pc.dictionary_encode(values).combine_chunks()
    dictionary = encoded.dictionary.to_pylist()
    order = sorted(range(len(dictionary)), key=lambda i: _sort_one(dictionary[i]))
    rank = np.empty(len(order), dtype=np.int64)
    rank[order] = np.arange(len(order))
    codes = rank[np.asarray(encoded.indices.to_numpy(zero_copy_only=False), dtype=np.int64)]
    return codes, [dictionary[i] for i in order]


def linear_regression(
    rel: Relation, req: RegressionRequest, *, seed: int = DEFAULT_SEED
) -> StatResult:
    predictors = list(dict.fromkeys(req.x))
    if req.y in predictors:
        raise StatsInputError(f"'{req.y}' can't be both the response and a predictor.")
    y_expr = measure_expr(rel, req.y)
    columns: list[tuple[str, exp.Expr, str, ColumnSemantic]] = [
        ("_y", y_expr, "DOUBLE", ColumnSemantic.QUANTITATIVE)
    ]
    categorical: list[str] = []
    for i, name in enumerate(predictors):
        rel.require(name)
        if field_kind(rel, name) == "categorical":
            categorical.append(name)
            column = rel.columns[name]
            columns.append((f"_x{i}", col(name), column.type, ColumnSemantic.NOMINAL))
        else:
            expr = measure_expr(rel, name, temporal=True)
            columns.append((f"_x{i}", expr, "DOUBLE", ColumnSemantic.QUANTITATIVE))
    complete = _present(*[e for _, e, _, _ in columns])
    coefficients = int(req.intercept) + len(predictors) - len(categorical)
    if categorical:
        selects = [
            alias(exp.Count(this=exp.Distinct(expressions=[col(n)])), f"d{j}")
            for j, n in enumerate(categorical)
        ]
        counts = rel.rows(rel.narrow(complete).select(*selects))[0]
        for name, count in zip(categorical, counts, strict=True):
            if count > req.max_levels:
                raise StatsInputError(
                    f"'{name}' has {int(count):,} levels; regression supports at most "
                    f"{req.max_levels:,} per categorical predictor."
                )
            coefficients += max(0, int(count) - 1)
    if coefficients > MAX_COEFFICIENTS:
        raise StatsInputError(
            f"The model would have {coefficients} coefficients; at most {MAX_COEFFICIENTS} "
            "are supported."
        )
    cap = int(min(MODEL_CAP, max(100_000, DESIGN_BUDGET // max(1, coefficients))))
    source = derived(rel, columns, exp.true())
    sample = reservoir_sample(source, [c[0] for c in columns], cap=cap, seed=seed)
    warnings = list(sample.warnings)

    y = sample.column("_y")
    blocks: list[np.ndarray] = []
    terms: list[_Term] = []
    references: list[ReferenceLevel] = []
    if req.intercept:
        blocks.append(np.ones((sample.n, 1)))
        terms.append(_Term(INTERCEPT, None, None))
    for i, name in enumerate(predictors):
        if name in categorical:
            codes, levels = _levels(sample.table.column(f"_x{i}"))
            if len(levels) < 2 and sample.n > 0:
                raise StatsInputError(
                    f"'{name}' has a single level among the rows used, so it can't be a predictor."
                )
            shown = [key_value(rel, name, v) for v in levels]
            if levels:
                references.append(ReferenceLevel(field=name, level=shown[0]))
            for code in range(1, len(levels)):
                blocks.append((codes == code).astype(float)[:, None])
                terms.append(_Term(f"{name}[{shown[code]}]", name, shown[code]))
        else:
            blocks.append(sample.column(f"_x{i}")[:, None])
            terms.append(_Term(name, name, None))
            if note := measure_note(rel, name):
                warnings.append(note)
    n, p = sample.n, len(terms)
    if n <= p:
        raise StatsInputError(
            f"The model has {p} coefficients, so it needs more than {p} complete rows; "
            f"there are {n}."
        )
    design = np.concatenate(blocks, axis=1)
    _check_rank(design, terms, req.intercept)
    return _fit(req, design, y, terms, references, sample, warnings)


def _check_rank(design: np.ndarray, terms: list[_Term], intercept: bool) -> None:
    """Name the terms that are linear combinations of the terms before them.

    As R's lm: in a QR of the unit-norm columns, |R_jj| is the distance of column j from
    the span of the earlier columns; below ALIAS_TOLERANCE the term is aliased. Earlier
    terms (the intercept, then the predictors in the order given) are kept.
    """
    norms = np.linalg.norm(design, axis=0)
    scaled = design / np.where(norms > 0, norms, 1.0)
    r = linalg.qr(scaled, mode="r")[0]
    distance = np.abs(np.diag(r))
    aliased = [terms[j].name for j in np.flatnonzero(distance < ALIAS_TOLERANCE)]
    if not aliased:
        return
    others = "the intercept and the other predictors" if intercept else "the other predictors"
    listed = ", ".join(f"'{a}'" for a in aliased)
    raise StatsInputError(
        f"{listed} {'is' if len(aliased) == 1 else 'are'} a linear combination of {others} "
        "(perfect multicollinearity, or a predictor without variation). Remove "
        f"{'it' if len(aliased) == 1 else 'them'} and fit again."
    )


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _vif(design: np.ndarray, terms: list[_Term]) -> list[float | None]:
    """Per field, GVIF^(1/df) (Fox & Monette 1992); needs an intercept.

    GVIF = det(R_FF) det(R_OO) / det(R) for the predictors' correlation matrix R, a field's
    columns F and the others O. For a one-column field it is the ordinary VIF. A categorical
    field's dummies share one value, GVIF^(1/df), which is comparable with a VIF and, unlike
    per-dummy VIFs, doesn't depend on which level is the reference.
    """
    out: list[float | None] = [None] * len(terms)
    if not terms or terms[0].name != INTERCEPT:
        return out
    predictors = design[:, 1:]
    p = predictors.shape[1]
    if p == 1:
        out[1] = 1.0
        return out
    blocks: dict[str, list[int]] = {}
    for j, term in enumerate(terms[1:]):
        blocks.setdefault(term.field or term.name, []).append(j)
    if len(blocks) == 1:
        for j in range(p):
            out[j + 1] = 1.0
        return out
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(predictors, rowvar=False)
    sign, log_all = np.linalg.slogdet(corr)
    if sign <= 0 or not np.isfinite(log_all):
        return out
    for columns in blocks.values():
        rest = [j for j in range(p) if j not in columns]
        _, log_block = np.linalg.slogdet(corr[np.ix_(columns, columns)])
        _, log_rest = np.linalg.slogdet(corr[np.ix_(rest, rest)])
        value = _finite(math.exp((log_block + log_rest - log_all) / len(columns)))
        for j in columns:
            out[j + 1] = value
    return out


def _fit(
    req: RegressionRequest,
    design: np.ndarray,
    y: np.ndarray,
    terms: list[_Term],
    references: list[ReferenceLevel],
    sample: Sample,
    warnings: list[str],
) -> StatResult:
    n = sample.n
    with py_warnings.catch_warnings():
        py_warnings.simplefilter("ignore")
        model = sm.OLS(y, design).fit(method="qr", cov_type="HC3" if req.robust else "nonrobust")
        residuals = np.asarray(model.resid)
        rss = float(residuals @ residuals)
        scale = float(np.abs(y).max()) if y.size else 0.0
        exact = rss <= (1e-12 * max(scale, 1e-300)) ** 2 * n
        conf = np.asarray(model.conf_int(alpha=1 - req.ci_level))
    vifs = _vif(design, terms)
    out_terms: list[RegressionTerm] = []
    for j, term in enumerate(terms):
        inference = not exact
        out_terms.append(
            RegressionTerm(
                term=term.name,
                field=term.field,
                level=term.level,
                estimate=_finite(model.params[j]),
                std_error=_finite(model.bse[j]) if inference else None,
                statistic=_finite(model.tvalues[j]) if inference else None,
                p_value=_finite(model.pvalues[j]) if inference else None,
                ci_low=_finite(conf[j, 0]) if inference else None,
                ci_high=_finite(conf[j, 1]) if inference else None,
                vif=vifs[j],
            )
        )
    if exact:
        warnings.append(
            "The predictors fit y exactly (no residual variation), so standard errors, "
            "tests and intervals are undefined."
        )
    total_ss = float(((y - y.mean()) ** 2).sum()) if req.intercept else float(y @ y)
    if total_ss == 0:
        warnings.append(f"'{req.y}' has no variation, so R² is undefined.")
    if not req.intercept:
        warnings.append("Without an intercept, R² is uncentred (as in R and statsmodels).")
    inflated = list(
        dict.fromkeys(
            t.field or t.name
            for t, v in zip(terms, vifs, strict=True)
            if v is not None and v > VIF_WARN
        )
    )
    if inflated:
        warnings.append(
            "Strong multicollinearity (VIF > 10): "
            + ", ".join(f"'{t}'" for t in inflated)
            + ". Their coefficients are unstable."
        )
    checks = _checks(req, design, residuals, exact, model.df_resid, warnings)
    defined = not exact and total_ss > 0
    kind = "multiple" if len(req.x) > 1 or len(terms) - int(req.intercept) > 1 else "linear"
    method = f"OLS {kind} regression" + (" with HC3 robust standard errors" if req.robust else "")
    values = RegressionValues(
        y=req.y,
        terms=out_terms,
        reference_levels=references,
        r2=_finite(model.rsquared) if total_ss > 0 else None,
        adj_r2=_finite(model.rsquared_adj) if total_ss > 0 else None,
        f_statistic=_finite(model.fvalue) if defined else None,
        f_p_value=_finite(model.f_pvalue) if defined else None,
        df_model=float(model.df_model),
        df_resid=float(model.df_resid),
        sigma=_finite(math.sqrt(max(0.0, model.mse_resid))),
        log_likelihood=_finite(model.llf) if not exact else None,
        aic=_finite(model.aic) if not exact else None,
        bic=_finite(model.bic) if not exact else None,
        condition_number=_finite(model.condition_number),
        ci_level=req.ci_level,
        robust=req.robust,
        checks=checks,
    )
    return stat_result(
        values.model_dump(by_alias=True),
        method=method,
        n=n,
        n_total=sample.n_total,
        n_missing=sample.n_missing,
        sampled=sample.sampled,
        warnings=warnings,
    )


def _checks(
    req: RegressionRequest,
    design: np.ndarray,
    residuals: np.ndarray,
    exact: bool,
    df_resid: float,
    warnings: list[str],
) -> list[AssumptionCheck]:
    n = len(residuals)
    checks: list[AssumptionCheck] = []
    if exact:
        none = AssumptionCheck(
            name="Normality of the residuals",
            statistic=None,
            p_value=None,
            passed=None,
            note="The fit is exact.",
        )
        return [none]
    if n <= SHAPIRO_MAX:
        normal = shapiro(residuals, "Shapiro-Wilk normality of the residuals")
    else:
        jb, p, _, _ = jarque_bera(residuals)
        normal = AssumptionCheck(
            name="Jarque-Bera normality of the residuals",
            statistic=_finite(jb),
            p_value=_finite(p),
            passed=bool(p >= ALPHA),
            note=None
            if p >= ALPHA
            else f"With {n:,} rows, inference is robust to non-normal residuals.",
        )
    checks.append(normal)
    note = normality_warning(
        normal, "the residuals", int(df_resid), "a transformation of y", size="residual df"
    )
    if note:
        warnings.append(note)
    if req.intercept and design.shape[1] >= 2:
        with py_warnings.catch_warnings():
            py_warnings.simplefilter("ignore")
            lm, lm_p, _, _ = het_breuschpagan(residuals, design)
        passed = bool(lm_p >= ALPHA)
        checks.append(
            AssumptionCheck(
                name="Breusch-Pagan constant variance",
                statistic=_finite(lm),
                p_value=_finite(lm_p),
                passed=passed,
                note=None if passed else "The residual variance changes with the predictors.",
            )
        )
        if not passed and not req.robust:
            warnings.append(
                f"The residual variance isn't constant (Breusch-Pagan {p_text(float(lm_p))}); "
                "standard errors may be off. Consider robust standard errors."
            )
    return checks
