"""One- and two-way ANOVA, exact on the full data, from per-cell aggregates.

Every linear model whose design is constant within a cell (any model in the factors) has
residual sum of squares SS_within + sum_c n_c (mean_c - fit_c)^2, where SS_within =
sum_c (n_c - 1) var_c. So each model is fitted by weighted least squares on the cell means
(weights n_c), and nothing but the per-cell n, mean and variance is read from the data.

Sums of squares (as R's anova / car::Anova and statsmodels' anova_lm):
- Type I: sequential (a, then b given a, then a:b given both).
- Type II: each main effect given the other; the interaction given both main effects.
- Type III: each term given all others, with sum-to-zero contrasts. It needs every
  combination of levels to have data when the interaction is in the model.
One-way: all types agree. Also reported for one-way: eta^2 and omega^2, Welch's ANOVA
(no equal-variance assumption) and Tukey HSD pairwise comparisons (Tukey-Kramer for unequal
group sizes). Checks: Brown-Forsythe (Levene) across cells, and Shapiro-Wilk on the residuals
when there are at most 5,000 values.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats as sps
from sqlglot import exp

from viz.contracts import Scalar, StatResult
from viz.stats.checks import SHAPIRO_MAX, levene_check, normality_warning, p_text, shapiro
from viz.stats.errors import StatsInputError
from viz.stats.fields import check_distinct, key_expr, key_value, label, measure_expr
from viz.stats.models import (
    AnovaRequest,
    AnovaRow,
    AnovaValues,
    AssumptionCheck,
    SampleSummary,
    TukeyComparison,
    WelchAnova,
)
from viz.stats.moments import Cell, CellMoments, cell_moments, levene, one_way_f
from viz.stats.relation import Relation
from viz.stats.results import stat_result
from viz.stats.sql import alias

MAX_CELLS = 1000
# Tukey HSD is computed for at most this many groups (C(k, 2) studentized-range integrals).
MAX_TUKEY_GROUPS = 50


def _not_null(*exprs: exp.Expr) -> exp.Expr:
    return exp.and_(*[exp.Not(this=exp.Is(this=e.copy(), expression=exp.Null())) for e in exprs])


def _check_levels(
    rel: Relation, value: exp.Expr, keys: Sequence[exp.Expr], factors: Sequence[str], cap: int
) -> None:
    selects = [
        alias(exp.Count(this=exp.Distinct(expressions=[k.copy()])), f"d{i}")
        for i, k in enumerate(keys)
    ]
    counts = rel.rows(rel.narrow(_not_null(value, *keys)).select(*selects))[0]
    for name, count in zip(factors, counts, strict=True):
        if count > cap:
            raise StatsInputError(
                f"'{name}' has {int(count):,} levels; ANOVA supports at most {cap:,}. "
                "Filter the data or raise maxLevels (up to 200)."
            )


def anova(rel: Relation, req: AnovaRequest) -> StatResult:
    factors = list(req.factors)
    check_distinct([req.field, *factors], "the values and the factors")
    value = measure_expr(rel, req.field)
    keys = [key_expr(rel, f) for f in factors]
    _check_levels(rel, value, keys, factors, req.max_levels)
    moments = cell_moments(rel, value, keys)
    if moments.used == 0:
        raise StatsInputError(f"No rows have a value for '{req.field}' and every factor.")
    cells = sorted(moments.cells, key=lambda c: _sort_key(c.key))
    levels = [sorted({c.key[i] for c in cells}, key=_sort_one) for i in range(len(factors))]
    for name, present in zip(factors, levels, strict=True):
        if len(present) < 2:
            raise StatsInputError(f"'{name}' needs at least 2 levels with data; it has {len(present)}.")
    if len(cells) > MAX_CELLS:
        raise StatsInputError(
            f"The factors have {len(cells):,} combinations with data; at most {MAX_CELLS:,} "
            "are supported."
        )
    if len(factors) == 1:
        return _one_way(rel, req, moments, cells, value, keys)
    return _two_way(rel, req, moments, cells, levels, value, keys)


def _sort_one(value: object) -> tuple[int, object]:
    return (0, value) if value is not None else (1, 0)


def _sort_key(key: Sequence[object]) -> tuple[tuple[int, object], ...]:
    return tuple(_sort_one(v) for v in key)


def _summaries(
    rel: Relation, factors: Sequence[str], moments: CellMoments, cells: Sequence[Cell]
) -> list[SampleSummary]:
    out: list[SampleSummary] = []
    for c in cells:
        group = [key_value(rel, f, v) for f, v in zip(factors, c.key, strict=True)]
        std = math.sqrt(c.var) if c.var is not None else None
        out.append(
            SampleSummary(
                label=label(factors, group),
                group=group,
                n=c.n,
                mean=moments.mean(c),
                std=std,
                sem=std / math.sqrt(c.n) if std is not None else None,
            )
        )
    return out


@dataclass(frozen=True)
class _Error:
    """The residual line every F statistic divides by."""

    ss: float
    df: float

    @property
    def mean_sq(self) -> float:
        return self.ss / self.df

    def row(self) -> AnovaRow:
        return AnovaRow(
            term="Residuals",
            df=self.df,
            sum_sq=self.ss,
            mean_sq=self.mean_sq,
            statistic=None,
            p_value=None,
            partial_eta_sq=None,
        )


def _f_row(term: str, ss: float, df: float, error: _Error) -> AnovaRow:
    if df <= 0:
        return AnovaRow(
            term=term,
            df=0,
            sum_sq=0.0,
            mean_sq=None,
            statistic=None,
            p_value=None,
            partial_eta_sq=None,
        )
    ss = max(0.0, ss)
    mean_sq = ss / df
    statistic = p_value = None
    if error.ss > 0:
        statistic = mean_sq / error.mean_sq
        p_value = float(sps.f.sf(statistic, df, error.df))
    partial = ss / (ss + error.ss) if ss + error.ss > 0 else None
    return AnovaRow(
        term=term,
        df=df,
        sum_sq=ss,
        mean_sq=mean_sq,
        statistic=statistic,
        p_value=p_value,
        partial_eta_sq=partial,
    )


# ---------------------------------------------------------------- one-way


def _one_way(
    rel: Relation,
    req: AnovaRequest,
    moments: CellMoments,
    cells: list[Cell],
    value: exp.Expr,
    keys: list[exp.Expr],
) -> StatResult:
    factor = req.factors[0]
    ns = [c.n for c in cells]
    offsets = [c.offset for c in cells]
    variances = [c.var for c in cells]
    k, total = len(cells), sum(ns)
    if total - k < 1:
        raise StatsInputError("ANOVA needs more values than groups (some group needs 2 values).")
    result = one_way_f(ns, offsets, variances)
    warnings: list[str] = []
    error = _Error(result.ss_within, total - k)
    sse, df_error, ms_error = error.ss, error.df, error.mean_sq
    table = [_f_row(factor, result.ss_between, k - 1, error), error.row()]
    if sse == 0:
        warnings.append("Every group is constant, so the F statistic is undefined.")
    ss_total = result.ss_between + sse
    eta = result.ss_between / ss_total if ss_total > 0 else None
    omega = (
        (result.ss_between - (k - 1) * ms_error) / (ss_total + ms_error)
        if ss_total + ms_error > 0
        else None
    )
    welch = _welch(ns, offsets, variances)
    if welch.statistic is None:
        warnings.append("Welch's ANOVA needs at least 2 values and some spread in every group.")
    post_hoc = None
    if req.post_hoc:
        if k > MAX_TUKEY_GROUPS:
            warnings.append(f"Tukey HSD is computed for at most {MAX_TUKEY_GROUPS} groups.")
        elif ms_error > 0:
            groups = [key_value(rel, factor, c.key[0]) for c in cells]
            post_hoc = _tukey(groups, ns, offsets, ms_error, df_error, req.ci_level)
    checks, notes = _checks(rel, req, moments, cells, value, keys, None)
    return stat_result(
        AnovaValues(
            field=req.field,
            factors=list(req.factors),
            ss_type=req.ss_type,
            interaction=False,
            table=table,
            eta_sq=eta,
            omega_sq=omega,
            welch=welch,
            post_hoc=post_hoc,
            ci_level=req.ci_level,
            cells=_summaries(rel, req.factors, moments, cells),
            empty_cells=0,
            checks=checks,
        ).model_dump(by_alias=True),
        method="One-way ANOVA (exact)",
        n=total,
        n_missing=moments.n_missing,
        sampled=False,
        warnings=[*warnings, *notes],
    )


def _welch(
    ns: Sequence[int], means: Sequence[float], variances: Sequence[float | None]
) -> WelchAnova:
    """Welch's (1951) heteroscedastic one-way ANOVA, as R's oneway.test."""
    k = len(ns)
    if any(n < 2 for n in ns) or any(not v for v in variances):
        return WelchAnova(statistic=None, df1=None, df2=None, p_value=None)
    w = np.array([n / v for n, v in zip(ns, variances, strict=True) if v])
    m = np.asarray(means, dtype=float)
    n = np.asarray(ns, dtype=float)
    total = w.sum()
    grand = float((w * m).sum() / total)
    a = float((w * (m - grand) ** 2).sum()) / (k - 1)
    tmp = float(((1 - w / total) ** 2 / (n - 1)).sum())
    b = 1 + 2 * (k - 2) / (k**2 - 1) * tmp
    statistic = a / b
    df2 = (k**2 - 1) / (3 * tmp) if tmp > 0 else math.inf
    p = float(sps.f.sf(statistic, k - 1, df2))
    return WelchAnova(statistic=statistic, df1=k - 1, df2=df2, p_value=p)


def _tukey(
    groups: Sequence[Scalar],
    ns: Sequence[int],
    means: Sequence[float],
    ms_error: float,
    df_error: float,
    ci_level: float,
) -> list[TukeyComparison]:
    """Tukey-Kramer comparisons for every pair i < j: diff = mean_j - mean_i."""
    k = len(ns)
    pairs = [(i, j) for i in range(k) for j in range(i + 1, k)]
    diffs = np.array([means[j] - means[i] for i, j in pairs])
    se = np.array([math.sqrt(ms_error / 2 * (1 / ns[i] + 1 / ns[j])) for i, j in pairs])
    q = np.abs(diffs) / se
    p_values = np.clip(sps.studentized_range.sf(q, k, df_error), 0.0, 1.0)
    crit = float(sps.studentized_range.ppf(ci_level, k, df_error))
    return [
        TukeyComparison(
            a=groups[i],
            b=groups[j],
            diff=float(diffs[n]),
            ci_low=float(diffs[n] - crit * se[n]),
            ci_high=float(diffs[n] + crit * se[n]),
            p_value=float(p_values[n]),
        )
        for n, (i, j) in enumerate(pairs)
    ]


# ---------------------------------------------------------------- two-way


@dataclass(frozen=True)
class _Fit:
    # sum_c n_c (mean_c - fit_c)^2: added to SS_within for the model's residual SS.
    between: float
    rank: int
    fitted: np.ndarray


def _coding(index: np.ndarray, levels: int, kind: str) -> np.ndarray:
    """Columns for one factor: treatment (drop the first level) or sum-to-zero contrasts."""
    if kind == "treatment":
        return np.stack([(index == lv).astype(float) for lv in range(1, levels)], axis=1)
    columns = []
    for lv in range(levels - 1):
        column = (index == lv).astype(float)
        column[index == levels - 1] = -1.0
        columns.append(column)
    return np.stack(columns, axis=1)


def _interaction(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.einsum("ci,cj->cij", a, b).reshape(len(a), -1)


def _wls(blocks: Sequence[np.ndarray], weights: np.ndarray, y: np.ndarray) -> _Fit:
    x = np.concatenate([np.ones((len(y), 1)), *blocks], axis=1)
    root = np.sqrt(weights)
    coef, _, rank, _ = np.linalg.lstsq(x * root[:, None], y * root, rcond=None)
    fitted = x @ coef
    between = float((weights * (y - fitted) ** 2).sum())
    return _Fit(between, int(rank), fitted)


def _two_way(
    rel: Relation,
    req: AnovaRequest,
    moments: CellMoments,
    cells: list[Cell],
    levels: list[list[object]],
    value: exp.Expr,
    keys: list[exp.Expr],
) -> StatResult:
    fa, fb = req.factors
    index = [{v: i for i, v in enumerate(lv)} for lv in levels]
    ia = np.array([index[0][c.key[0]] for c in cells])
    ib = np.array([index[1][c.key[1]] for c in cells])
    n = np.array([c.n for c in cells], dtype=float)
    y = np.array([c.offset for c in cells])
    ss_within = float(sum((c.n - 1) * (c.var or 0.0) for c in cells))
    total = int(n.sum())
    na, nb = len(levels[0]), len(levels[1])
    empty = na * nb - len(cells)
    warnings: list[str] = []
    interaction = req.interaction
    if req.ss_type == 3 and interaction and empty:
        raise StatsInputError(
            f"Type III sums of squares need data in every combination of levels; {empty} "
            "are empty. Use Type II, or leave out the interaction."
        )
    coding = "sum" if req.ss_type == 3 else "treatment"
    a = _coding(ia, na, coding)
    b = _coding(ib, nb, coding)
    ab = _interaction(a, b)

    def fit(*blocks: np.ndarray) -> _Fit:
        return _wls(blocks, n, y)

    full = fit(a, b, ab) if interaction else fit(a, b)
    df_error = total - full.rank
    if df_error <= 0:
        raise StatsInputError(
            "No residual degrees of freedom: every combination of levels has a single value. "
            "Leave out the interaction."
        )
    error = _Error(ss_within + full.between, df_error)
    sse = error.ss

    def term(name: str, smaller: _Fit, larger: _Fit) -> AnovaRow:
        """The term that `larger` adds to `smaller`."""
        return _f_row(name, smaller.between - larger.between, larger.rank - smaller.rank, error)

    inter = f"{fa}:{fb}"
    if req.ss_type == 1:
        only_a, additive = fit(a), fit(a, b)
        rows = [term(fa, fit(), only_a), term(fb, only_a, additive)]
        if interaction:
            rows.append(term(inter, additive, full))
    elif req.ss_type == 2:
        only_a, only_b, additive = fit(a), fit(b), fit(a, b)
        rows = [term(fa, only_b, additive), term(fb, only_a, additive)]
        if interaction:
            rows.append(term(inter, additive, full))
    else:
        without_a = fit(b, ab) if interaction else fit(b)
        without_b = fit(a, ab) if interaction else fit(a)
        rows = [term(fa, without_a, full), term(fb, without_b, full)]
        if interaction:
            rows.append(term(inter, fit(a, b), full))
    rows.append(error.row())
    if empty:
        warnings.append(
            f"{empty} of {na * nb} combinations of levels have no data"
            + ("; the interaction has fewer degrees of freedom." if interaction else ".")
        )
    if sse == 0:
        warnings.append("Every cell is constant, so the F statistics are undefined.")
    fitted = dict(zip((c.key for c in cells), full.fitted.tolist(), strict=True))
    checks, notes = _checks(rel, req, moments, cells, value, keys, fitted)
    kind = {1: "I", 2: "II", 3: "III"}[req.ss_type]
    return stat_result(
        AnovaValues(
            field=req.field,
            factors=list(req.factors),
            ss_type=req.ss_type,
            interaction=interaction,
            table=rows,
            eta_sq=None,
            omega_sq=None,
            welch=None,
            post_hoc=None,
            ci_level=req.ci_level,
            cells=_summaries(rel, req.factors, moments, cells),
            empty_cells=empty,
            checks=checks,
        ).model_dump(by_alias=True),
        method=f"Two-way ANOVA, Type {kind} sums of squares (exact)",
        n=total,
        n_missing=moments.n_missing,
        sampled=False,
        warnings=[*warnings, *notes],
    )


# ---------------------------------------------------------------- checks


def _checks(
    rel: Relation,
    req: AnovaRequest,
    moments: CellMoments,
    cells: Sequence[Cell],
    value: exp.Expr,
    keys: Sequence[exp.Expr],
    fitted: dict[tuple[object, ...], float] | None,
) -> tuple[list[AssumptionCheck], list[str]]:
    """Levene (Brown-Forsythe) across cells and Shapiro-Wilk on the residuals."""
    notes: list[str] = []
    bf = levene_check(levene(rel, value, keys), "Levene (Brown-Forsythe) equal variances")
    if bf.passed is False and bf.p_value is not None:
        notes.append(
            f"The groups' variances differ (Levene {p_text(bf.p_value)})"
            + ("; Welch's ANOVA doesn't assume equal variances." if fitted is None else ".")
        )
    total = moments.used
    name = "Shapiro-Wilk normality of the residuals"
    if total > SHAPIRO_MAX:
        normal = shapiro(None, name, total)
    else:
        selects = [alias(k.copy(), f"_k{i}") for i, k in enumerate(keys)]
        table = rel.arrow(rel.narrow(_not_null(value, *keys)).select(*selects, alias(value, "_v")))
        means = (
            {c.key: c.offset for c in cells} if fitted is None else fitted
        )
        key_columns = [table.column(f"_k{i}").to_pylist() for i in range(len(keys))]
        values = np.asarray(table.column("_v").to_numpy(zero_copy_only=False), dtype=float)
        centre = np.array([means[tuple(k)] for k in zip(*key_columns, strict=True)])
        normal = shapiro(values - moments.center - centre, name)
    smallest = min(c.n for c in cells)
    note = normality_warning(
        normal,
        "the residuals",
        smallest,
        "the Kruskal-Wallis test" if len(req.factors) == 1 else "transforming the values",
        size="smallest group n",
    )
    if note:
        notes.append(note)
    return [bf, normal], notes
