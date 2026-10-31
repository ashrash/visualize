"""t-tests, exact on the full data: they need only each sample's n, mean and variance.

Matches R's t.test and scipy.stats.ttest_1samp / ttest_ind / ttest_rel:
- one_sample: t = (mean - mu) / (sd / sqrt(n)), df = n - 1
- welch: t = (diff - mu) / sqrt(v1/n1 + v2/n2), Welch-Satterthwaite df
- student: pooled variance, df = n1 + n2 - 2
- paired: a one-sample test of the differences field - paired_field
Intervals are for the estimate (mean, difference, or mean difference); a one-sided
alternative gives a one-sided interval (the open side is null). Cohen's d divides by the
sample SD (one-sample, paired) or the pooled SD (two-sample).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats as sps
from sqlglot import exp

from viz.contracts import (
    Alternative,
    AssumptionCheck,
    SampleSummary,
    Scalar,
    StatResult,
    TTestRequest,
    TTestValues,
)
from viz.stats.checks import levene_check, normality_warning, p_text, shapiro
from viz.stats.errors import StatsInputError
from viz.stats.fields import check_distinct, match_case, measure_expr
from viz.stats.moments import cell_moments, levene
from viz.stats.relation import Relation
from viz.stats.results import stat_result
from viz.stats.sql import alias, count_star, fn

# Fetch raw values for the normality check only for samples this small.
_SHAPIRO_FETCH = 5_000

_METHOD = {
    "one_sample": "One-sample t-test",
    "welch": "Welch two-sample t-test",
    "student": "Two-sample t-test (equal variances)",
    "paired": "Paired t-test",
}


@dataclass(frozen=True)
class TResult:
    statistic: float | None
    df: float | None
    p_value: float | None
    ci_low: float | None
    ci_high: float | None


def t_inference(
    estimate: float,
    se: float,
    df: float,
    mu: float,
    alternative: Alternative,
    ci_level: float,
) -> TResult:
    """t statistic, p-value and interval for `estimate` with standard error `se`."""
    if not (se > 0 and math.isfinite(se) and df > 0):
        return TResult(None, df if df > 0 else None, None, None, None)
    t = (estimate - mu) / se
    if alternative == "two_sided":
        p = float(2 * sps.t.sf(abs(t), df))
        crit = float(sps.t.ppf(0.5 + ci_level / 2, df))
        low, high = estimate - crit * se, estimate + crit * se
        return TResult(t, df, min(1.0, p), low, high)
    crit = float(sps.t.ppf(ci_level, df))
    if alternative == "less":
        return TResult(t, df, float(sps.t.cdf(t, df)), None, estimate + crit * se)
    return TResult(t, df, float(sps.t.sf(t, df)), estimate - crit * se, None)


def _summary(
    label: str, group: list[Scalar] | None, n: int, mean: float | None, var: float | None
) -> SampleSummary:
    std = math.sqrt(var) if var is not None else None
    sem = std / math.sqrt(n) if std is not None and n > 0 else None
    return SampleSummary(label=label, group=group, n=n, mean=mean, std=std, sem=sem)


def _values(
    rel: Relation, value: exp.Expr, condition: exp.Expr, key: exp.Expr | None = None
) -> dict[int, np.ndarray]:
    """Raw values (finite floats) under `condition`, split by `key` (0/1) when given."""
    selects = [alias(value.copy(), "v")]
    if key is not None:
        selects.append(alias(key.copy(), "k"))
    present = exp.Not(this=exp.Is(this=value.copy(), expression=exp.Null()))
    table = rel.arrow(rel.narrow(exp.and_(condition, present)).select(*selects))
    values = np.asarray(table.column("v").to_numpy(zero_copy_only=False), dtype=float)
    if key is None:
        return {0: values}
    keys = np.asarray(table.column("k").to_numpy(zero_copy_only=False))
    return {int(k): values[keys == k] for k in np.unique(keys)}


def _validate(req: TTestRequest) -> None:
    kind = req.kind
    if kind in ("welch", "student"):
        if req.group_field is None or req.groups is None:
            raise StatsInputError("A two-sample t-test needs a grouping field and two groups.")
        if req.paired_field is not None:
            raise StatsInputError("A two-sample t-test doesn't use a paired field.")
        check_distinct([req.field, req.group_field], "the values and the groups")
        if req.groups[0] == req.groups[1]:
            raise StatsInputError("Choose two different groups.")
    elif kind == "paired":
        if req.paired_field is None:
            raise StatsInputError("A paired t-test needs a second field to pair with.")
        if req.group_field is not None or req.groups is not None:
            raise StatsInputError("A paired t-test compares two fields, not groups.")
        check_distinct([req.field, req.paired_field], "the paired measurements")
    elif req.group_field is not None or req.groups is not None or req.paired_field is not None:
        raise StatsInputError("A one-sample t-test uses a single field.")


def t_test(rel: Relation, req: TTestRequest) -> StatResult:
    _validate(req)
    if req.kind in ("welch", "student"):
        return _two_sample(rel, req)
    return _one_sample(rel, req)


def _one_sample(rel: Relation, req: TTestRequest) -> StatResult:
    paired = req.kind == "paired"
    x = measure_expr(rel, req.field)
    warnings: list[str] = []
    if paired:
        assert req.paired_field is not None
        y = measure_expr(rel, req.paired_field)
        value: exp.Expr = exp.Sub(this=x, expression=y)
        what = f"the differences {req.field} - {req.paired_field}"
    else:
        value = x
        what = f"'{req.field}'"
    moments = cell_moments(rel, value)
    n = moments.used
    if n < 2:
        raise StatsInputError(
            f"A t-test needs at least 2 {'pairs' if paired else 'values'}; there {_are(n)}."
        )
    cell = moments.cells[0]
    mean = moments.mean(cell)
    var = cell.var or 0.0
    sd = math.sqrt(var)
    se = sd / math.sqrt(n)
    inference = t_inference(mean, se, n - 1, req.mu, req.alternative, req.ci_level)
    if se == 0:
        warnings.append(
            f"All {'differences' if paired else 'values'} are equal, so the t statistic is "
            "undefined."
        )
    samples: list[SampleSummary] = []
    if paired:
        assert req.paired_field is not None
        samples = _paired_summaries(rel, req.field, req.paired_field, value)
        samples.append(_summary("difference", None, n, mean, cell.var))
    else:
        samples = [_summary(req.field, None, n, mean, cell.var)]

    checks: list[AssumptionCheck] = []
    name = "Shapiro-Wilk normality" + (" of the differences" if paired else "")
    fetched = _values(rel, value, exp.true())[0] if n <= _SHAPIRO_FETCH else None
    check = shapiro(fetched, name, n)
    checks.append(check)
    note = normality_warning(check, what, n, "the Wilcoxon signed-rank test")
    if note:
        warnings.append(note)

    values_model = TTestValues(
        kind=req.kind,
        alternative=req.alternative,
        ci_level=req.ci_level,
        mu=req.mu,
        statistic=inference.statistic,
        df=inference.df,
        p_value=inference.p_value,
        estimate=mean,
        std_error=se,
        ci_low=inference.ci_low,
        ci_high=inference.ci_high,
        cohens_d=(mean - req.mu) / sd if sd > 0 else None,
        samples=samples,
        checks=checks,
    )
    return stat_result(
        values_model.model_dump(by_alias=True),
        method=_METHOD[req.kind] + " (exact)",
        n=n,
        n_missing=moments.n_missing,
        sampled=False,
        warnings=warnings,
    )


def _paired_summaries(
    rel: Relation, first: str, second: str, difference: exp.Expr
) -> list[SampleSummary]:
    """n, mean and SD of each field over the complete pairs (for display)."""
    both = exp.Not(this=exp.Is(this=difference.copy(), expression=exp.Null()))
    out: list[SampleSummary] = []
    selects: list[exp.Expr] = [alias(count_star(), "n")]
    for i, name in enumerate((first, second)):
        v = measure_expr(rel, name)
        selects += [alias(fn("avg", v), f"m{i}"), alias(fn("var_samp", v.copy()), f"v{i}")]
    row = rel.arrow(rel.narrow(both).select(*selects)).to_pylist()[0]
    n = int(row["n"])
    for i, name in enumerate((first, second)):
        var = row[f"v{i}"]
        out.append(_summary(name, None, n, row[f"m{i}"], None if var is None else max(0.0, var)))
    return out


def _are(n: int) -> str:
    return f"is {n}" if n == 1 else f"are {n}"


def _two_sample(rel: Relation, req: TTestRequest) -> StatResult:
    assert req.group_field is not None and req.groups is not None
    groups = list(req.groups)
    value = measure_expr(rel, req.field)
    key = match_case(rel, req.group_field, groups)
    in_groups = exp.Not(this=exp.Is(this=key.copy(), expression=exp.Null()))
    moments = cell_moments(rel, value, [key], condition=in_groups)
    cells = moments.by_key()
    labels = [f"{req.group_field} = {g}" for g in groups]
    for index, text in enumerate(labels):
        if (index,) not in cells:
            raise StatsInputError(f"No rows with {text} and a value for '{req.field}'.")
    a, b = cells[(0,)], cells[(1,)]
    welch = req.kind == "welch"
    warnings: list[str] = []
    if welch and (a.n < 2 or b.n < 2):
        raise StatsInputError("Welch's t-test needs at least 2 values in each group.")
    if not welch and a.n + b.n < 3:
        raise StatsInputError("A two-sample t-test needs at least 3 values in all.")
    va, vb = a.var or 0.0, b.var or 0.0
    diff = a.offset - b.offset
    pooled_df = a.n + b.n - 2
    pooled_var = ((a.n - 1) * va + (b.n - 1) * vb) / pooled_df if pooled_df > 0 else 0.0
    if welch:
        ua, ub = va / a.n, vb / b.n
        se = math.sqrt(ua + ub)
        denominator = (ua**2 / (a.n - 1) if a.n > 1 else 0.0) + (
            ub**2 / (b.n - 1) if b.n > 1 else 0.0
        )
        df = (ua + ub) ** 2 / denominator if denominator > 0 else float(pooled_df)
    else:
        se = math.sqrt(pooled_var * (1 / a.n + 1 / b.n))
        df = float(pooled_df)
    inference = t_inference(diff, se, df, req.mu, req.alternative, req.ci_level)
    if se == 0:
        warnings.append("Both groups are constant, so the t statistic is undefined.")

    samples = [
        _summary(str(g), [g], c.n, moments.mean(c), c.var)
        for g, c in zip(groups, (a, b), strict=True)
    ]
    checks: list[AssumptionCheck] = []
    small = [i for i, c in enumerate((a, b)) if c.n <= _SHAPIRO_FETCH]
    fetched: dict[int, np.ndarray] = {}
    if small:
        which = exp.In(this=key.copy(), expressions=[exp.Literal.number(i) for i in small])
        fetched = _values(rel, value, which, key)
    for i, c in enumerate((a, b)):
        values = fetched.get(i, np.empty(0)) if i in small else None
        check = shapiro(values, f"Shapiro-Wilk normality ({groups[i]})", c.n)
        checks.append(check)
        note = normality_warning(
            check, f"'{req.field}' in {labels[i]}", c.n, "the Mann-Whitney U test"
        )
        if note:
            warnings.append(note)
    if not welch:
        bf = levene_check(
            levene(rel, value, [key], condition=in_groups),
            "Levene (Brown-Forsythe) equal variances",
        )
        checks.append(bf)
        if bf.passed is False and bf.p_value is not None:
            warnings.append(
                f"The groups' variances differ (Levene {p_text(bf.p_value)}); Welch's t-test "
                "doesn't assume equal variances."
            )

    values_model = TTestValues(
        kind=req.kind,
        alternative=req.alternative,
        ci_level=req.ci_level,
        mu=req.mu,
        statistic=inference.statistic,
        df=inference.df,
        p_value=inference.p_value,
        estimate=diff,
        std_error=se,
        ci_low=inference.ci_low,
        ci_high=inference.ci_high,
        cohens_d=(diff - req.mu) / math.sqrt(pooled_var) if pooled_var > 0 else None,
        samples=samples,
        checks=checks,
    )
    return stat_result(
        values_model.model_dump(by_alias=True),
        method=_METHOD[req.kind] + " (exact)",
        n=a.n + b.n,
        n_missing=moments.n_missing,
        sampled=False,
        warnings=warnings,
    )
