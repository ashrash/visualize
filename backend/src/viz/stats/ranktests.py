"""Rank-based tests in SciPy, on a random sample above 1M values (per test, split by group).

- mann_whitney: scipy.stats.mannwhitneyu (exact for small samples without ties, otherwise
  normal with tie and continuity corrections, as R's wilcox.test). The statistic is U of
  the first group; the effect size is the rank-biserial correlation 2 U / (n1 n2) - 1
  (positive when the first group tends to be larger).
- wilcoxon: scipy.stats.wilcoxon on field - paired_field (or field - mu), zero differences
  dropped (Wilcoxon's method). The statistic is W+ (sum of the ranks of positive
  differences, R's V); the effect size is the matched-pairs rank-biserial (W+ - W-) / (W+ + W-).
- kruskal: scipy.stats.kruskal; effect size epsilon squared H / (n - 1).
"""

from __future__ import annotations

import math
import warnings as py_warnings

import numpy as np
from scipy import stats as sps
from sqlglot import exp

from viz.contracts import ColumnSemantic, Scalar, StatResult
from viz.stats.errors import StatsInputError
from viz.stats.fields import (
    check_distinct,
    derived,
    field_kind,
    key_expr,
    key_value,
    label,
    match_case,
    measure_expr,
)
from viz.stats.models import RankSampleSummary, RankTestRequest, RankTestValues
from viz.stats.relation import Relation
from viz.stats.results import stat_result
from viz.stats.sampling import DEFAULT_SEED, MODEL_CAP, reservoir_sample
from viz.stats.sql import num

_METHOD = {
    "mann_whitney": "Mann-Whitney U test",
    "wilcoxon": "Wilcoxon signed-rank test",
    "kruskal": "Kruskal-Wallis H test",
}


def _present(*exprs: exp.Expr) -> exp.Expr:
    return exp.and_(*[exp.Not(this=exp.Is(this=e.copy(), expression=exp.Null())) for e in exprs])


def _validate(req: RankTestRequest) -> None:
    if req.kind == "mann_whitney":
        if req.group_field is None or req.groups is None or len(req.groups) != 2:
            raise StatsInputError("The Mann-Whitney U test needs a grouping field and two groups.")
        if req.groups[0] == req.groups[1]:
            raise StatsInputError("Choose two different groups.")
    if req.kind == "kruskal" and req.group_field is None:
        raise StatsInputError("The Kruskal-Wallis test needs a grouping field.")
    if req.kind in ("mann_whitney", "kruskal"):
        if req.paired_field is not None:
            raise StatsInputError("This test compares groups, not paired fields.")
        check_distinct([req.field, req.group_field], "the values and the groups")
    if req.kind == "wilcoxon":
        if req.group_field is not None or req.groups is not None:
            raise StatsInputError("The Wilcoxon signed-rank test compares two fields, not groups.")
        check_distinct([req.field, req.paired_field], "the paired measurements")


def rank_test(rel: Relation, req: RankTestRequest, *, seed: int = DEFAULT_SEED) -> StatResult:
    _validate(req)
    if req.kind == "wilcoxon":
        return _wilcoxon(rel, req, seed)
    return _groups(rel, req, seed)


def _wilcoxon(rel: Relation, req: RankTestRequest, seed: int) -> StatResult:
    x = measure_expr(rel, req.field)
    if req.paired_field is not None:
        d: exp.Expr = exp.Sub(this=x, expression=measure_expr(rel, req.paired_field))
        what = f"{req.field} - {req.paired_field}"
    else:
        d = exp.Sub(this=x, expression=num(float(req.mu)))
        what = f"{req.field} - {req.mu:g}"
    source = derived(rel, [("_v", d, "DOUBLE", ColumnSemantic.QUANTITATIVE)], exp.true())
    sample = reservoir_sample(source, ["_v"], cap=MODEL_CAP, seed=seed)
    values = sample.column("_v")
    warnings = sample.notes()
    nonzero = values[values != 0]
    zeros = len(values) - len(nonzero)
    if zeros:
        warnings.append(f"{zeros:,} zero differences were left out (Wilcoxon's method).")
    if len(nonzero) < 1:
        raise StatsInputError("Every difference is zero, so the test is undefined.")
    ranks = sps.rankdata(np.abs(nonzero))
    w_plus = float(ranks[nonzero > 0].sum())
    w_minus = float(ranks[nonzero < 0].sum())
    with py_warnings.catch_warnings():
        py_warnings.simplefilter("ignore")
        result = sps.wilcoxon(nonzero, alternative=req.alternative.replace("_", "-"))
    p = float(result.pvalue)
    total = w_plus + w_minus
    summary = RankSampleSummary(
        label=what, group=None, n=len(values), median=float(np.median(values)), mean_rank=None
    )
    return stat_result(
        RankTestValues(
            kind="wilcoxon",
            alternative=req.alternative,
            statistic=w_plus,
            df=None,
            p_value=p if math.isfinite(p) else None,
            effect_size=(w_plus - w_minus) / total if total > 0 else None,
            effect_size_name="matched-pairs rank-biserial correlation",
            samples=[summary],
        ).model_dump(by_alias=True),
        method=_METHOD["wilcoxon"] + (" (paired)" if req.paired_field else f" vs {req.mu:g}"),
        n=sample.n,
        n_total=sample.n_total,
        n_missing=sample.n_missing,
        sampled=sample.sampled,
        warnings=warnings,
    )


def _groups(rel: Relation, req: RankTestRequest, seed: int) -> StatResult:
    assert req.group_field is not None
    value = measure_expr(rel, req.field)
    chosen: list[Scalar] | None = list(req.groups) if req.groups is not None else None
    if chosen is not None:
        if len({repr(g) for g in chosen}) != len(chosen):
            raise StatsInputError("Each group may appear only once.")
        key: exp.Expr = match_case(rel, req.group_field, chosen)
        key_type = "INTEGER"
    else:
        key = key_expr(rel, req.group_field)
        temporal = field_kind(rel, req.group_field) == "temporal"
        key_type = "VARCHAR" if temporal else rel.columns[req.group_field].type
    source = derived(
        rel,
        [
            ("_k", key, key_type, ColumnSemantic.NOMINAL),
            ("_v", value, "DOUBLE", ColumnSemantic.QUANTITATIVE),
        ],
        _present(key),
    )
    sample = reservoir_sample(
        source,
        ["_v"],
        cap=MODEL_CAP,
        by=["_k"],
        seed=seed,
        max_groups=req.max_groups,
        total_cap=MODEL_CAP,
    )
    keys = sample.table.column("_k").to_pylist()
    values = sample.column("_v")
    present = sorted(set(keys), key=lambda k: (k is None, k))
    if chosen is not None:
        for index, wanted in enumerate(chosen):
            if index not in present:
                raise StatsInputError(
                    f"No rows with {req.group_field} = {wanted} and a value for '{req.field}'."
                )
        present = list(range(len(chosen)))
        group_keys: list[Scalar] = list(chosen)
    else:
        group_keys = [key_value(rel, req.group_field, k) for k in present]
    if len(present) < 2:
        raise StatsInputError(f"'{req.group_field}' needs at least 2 groups with data.")
    key_array = np.array(keys, dtype=object)
    samples = [values[key_array == k] for k in present]
    warnings = sample.notes()
    pooled = sps.rankdata(np.concatenate(samples))
    bounds = np.cumsum([0, *[len(s) for s in samples]])
    summaries = [
        RankSampleSummary(
            label=label([req.group_field], [g]),
            group=[g],
            n=len(s),
            median=float(np.median(s)),
            mean_rank=float(pooled[bounds[i] : bounds[i + 1]].mean()),
        )
        for i, (g, s) in enumerate(zip(group_keys, samples, strict=True))
    ]
    with py_warnings.catch_warnings():
        py_warnings.simplefilter("ignore")
        if req.kind == "mann_whitney":
            if len(samples) != 2:
                raise StatsInputError("The Mann-Whitney U test compares exactly two groups.")
            a, b = samples
            result = sps.mannwhitneyu(a, b, alternative=req.alternative.replace("_", "-"))
            statistic = float(result.statistic)
            effect: float | None = 2 * statistic / (len(a) * len(b)) - 1
            effect_name = "rank-biserial correlation"
            df = None
        else:
            if req.alternative != "two_sided":
                raise StatsInputError("The Kruskal-Wallis test is two-sided only.")
            try:
                result = sps.kruskal(*samples)
            except ValueError as exc:  # every value identical
                raise StatsInputError("All values are equal, so the test is undefined.") from exc
            statistic = float(result.statistic)
            n = sum(len(s) for s in samples)
            effect = statistic / (n - 1) if n > 1 else None
            effect_name = "epsilon squared"
            df = float(len(samples) - 1)
    p = float(result.pvalue)
    if not math.isfinite(p) or not math.isfinite(statistic):
        raise StatsInputError("All values are equal, so the test is undefined.")
    return stat_result(
        RankTestValues(
            kind=req.kind,
            alternative=req.alternative,
            statistic=statistic,
            df=df,
            p_value=p,
            effect_size=effect,
            effect_size_name=effect_name,
            samples=summaries,
        ).model_dump(by_alias=True),
        method=_METHOD[req.kind],
        n=sample.n,
        n_total=sample.n_total,
        n_missing=sample.n_missing,
        sampled=sample.sampled,
        warnings=warnings,
    )
