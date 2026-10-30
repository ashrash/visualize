"""Confidence intervals: t for the mean (exact), bootstrap for any statistic.

- t: mean +/- t(1 - alpha/2, n - 1) * sd / sqrt(n), from exact SQL moments (R's t.test).
- bootstrap: scipy.stats.bootstrap through stats.bootstrap.bootstrap_ci (BCa up to 10,000
  values, percentile above). Estimates are always exact on the full data (SQL moments and
  quantile_cont, R type 7).

Large groups: the bootstrap runs on a random sample of m values (BOOTSTRAP_CAP, lowered
per group so all groups together stay within BOOTSTRAP_TOTAL). An interval computed on m
values is about sqrt(n / m) times too wide for n values, so the sample's interval offsets
are rescaled by sqrt(m / n) around the full-data estimate (m-out-of-n bootstrap; valid
for the root-n consistent statistics offered here: mean, median, sd and quantiles of
continuous data). The interval's `sampled` flag says when this happened.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import Executor
from typing import Any

import numpy as np
from scipy import stats as sps
from sqlglot import exp

from viz.contracts import ColumnSemantic, Scalar, StatResult
from viz.stats.bootstrap import bootstrap_ci, quantile
from viz.stats.errors import StatsInputError
from viz.stats.fields import derived, field_kind, key_expr, key_value, label, measure_expr
from viz.stats.groups import cap_groups
from viz.stats.models import (
    ConfidenceInterval,
    ConfidenceIntervalRequest,
    ConfidenceIntervalValues,
)
from viz.stats.moments import cell_moments
from viz.stats.relation import Relation
from viz.stats.results import stat_result
from viz.stats.sampling import DEFAULT_SEED, reservoir_sample
from viz.stats.sql import alias, fn, num

# BCa's jackknife costs O(m^2) per group (a median at m = 10,000 took ~1 s), and the
# sqrt(m/n) rescaling makes m a cost knob rather than an accuracy one.
BOOTSTRAP_CAP = 5_000
BOOTSTRAP_TOTAL = 20_000

Key = tuple[Any, ...]


def _present(*exprs: exp.Expr) -> exp.Expr:
    return exp.and_(*[exp.Not(this=exp.Is(this=e.copy(), expression=exp.Null())) for e in exprs])


def _statistic(req: ConfidenceIntervalRequest) -> str | Callable[..., np.ndarray]:
    if req.statistic == "quantile":
        assert req.quantile is not None
        return quantile(req.quantile)
    return req.statistic


def _quantile_by_key(
    rel: Relation, value: exp.Expr, keys: list[exp.Expr], q: float
) -> dict[Key, float]:
    query = rel.narrow(_present(value, *keys)).select(
        *[alias(k.copy(), f"_k{i}") for i, k in enumerate(keys)],
        alias(fn("quantile_cont", value.copy(), num(float(q))), "_q"),
    )
    if keys:
        query = query.group_by(*[k.copy() for k in keys])
    return {
        tuple(row[f"_k{i}"] for i in range(len(keys))): float(row["_q"])
        for row in rel.arrow(query).to_pylist()
        if row["_q"] is not None
    }


def confidence_intervals(
    rel: Relation,
    req: ConfidenceIntervalRequest,
    *,
    seed: int = DEFAULT_SEED,
    executor: Executor | None = None,
) -> StatResult:
    if req.statistic == "quantile" and req.quantile is None:
        raise StatsInputError("Choose the quantile (between 0 and 1) to estimate.")
    if req.method == "t" and req.statistic != "mean":
        raise StatsInputError("The t interval is for the mean; use the bootstrap for others.")
    use_t = req.method == "t" or (req.method == "auto" and req.statistic == "mean")
    fields = list(dict.fromkeys(req.fields))
    source = rel
    warnings: list[str] = []
    keys: list[exp.Expr] = []
    names: list[str] = []
    if req.group_by is not None:
        rel.require(req.group_by)
        if req.group_by in fields:
            raise StatsInputError("Choose different fields for the values and the groups.")
        capped = cap_groups(rel, [req.group_by], req.max_groups)
        source = capped.relation
        if capped.warning:
            warnings.append(capped.warning)
        keys, names = [key_expr(rel, req.group_by)], [req.group_by]
    intervals: list[ConfidenceInterval] = []
    for name in fields:
        value = measure_expr(rel, name)
        if use_t:
            intervals += _t_intervals(source, req, name, value, keys)
        else:
            intervals += _bootstrap_intervals(
                source, req, name, value, keys, warnings, seed, executor
            )
    n = int(rel.rows(rel.select(exp.Count(this=exp.Star())))[0][0])
    statistic = req.statistic if req.statistic != "quantile" else f"{req.quantile:g} quantile"
    method = (
        f"t confidence intervals for the {statistic} (exact)"
        if use_t
        else f"Bootstrap confidence intervals for the {statistic} ({req.n_resamples:,} resamples)"
    )
    for interval in intervals:
        if interval.n < 2:
            where = label(names, interval.group) if interval.group is not None else "all rows"
            warnings.append(f"'{interval.field}' ({where}): an interval needs at least 2 values.")
    values = ConfidenceIntervalValues(
        statistic=req.statistic,
        quantile=req.quantile,
        ci_level=req.ci_level,
        group_by=req.group_by,
        intervals=intervals,
    )
    return stat_result(
        values.model_dump(by_alias=True),
        method=method,
        n=n,
        sampled=any(i.sampled for i in intervals),
        warnings=warnings,
    )


def _group_value(rel: Relation, req: ConfidenceIntervalRequest, key: Key) -> list[Scalar] | None:
    if req.group_by is None:
        return None
    return [key_value(rel, req.group_by, key[0])]


def _t_intervals(
    rel: Relation,
    req: ConfidenceIntervalRequest,
    name: str,
    value: exp.Expr,
    keys: list[exp.Expr],
) -> list[ConfidenceInterval]:
    moments = cell_moments(rel, value, keys)
    out: list[ConfidenceInterval] = []
    for cell in sorted(moments.cells, key=lambda c: tuple((v is None, v) for v in c.key)):
        mean = moments.mean(cell)
        low = high = se = None
        if cell.n >= 2 and cell.var is not None:
            se = math.sqrt(cell.var / cell.n)
            crit = float(sps.t.ppf(0.5 + req.ci_level / 2, cell.n - 1))
            low, high = mean - crit * se, mean + crit * se
        out.append(
            ConfidenceInterval(
                field=name,
                group=_group_value(rel, req, cell.key),
                n=cell.n,
                estimate=mean,
                ci_low=low,
                ci_high=high,
                std_error=se,
                method="t",
                sampled=False,
            )
        )
    return out


def _bootstrap_intervals(
    rel: Relation,
    req: ConfidenceIntervalRequest,
    name: str,
    value: exp.Expr,
    keys: list[exp.Expr],
    warnings: list[str],
    seed: int,
    executor: Executor | None,
) -> list[ConfidenceInterval]:
    moments = cell_moments(rel, value, keys)
    counts = {c.key: c.n for c in moments.cells}
    if req.statistic == "mean":
        exact = {c.key: moments.mean(c) for c in moments.cells}
    elif req.statistic == "std":
        exact = {c.key: math.sqrt(c.var) for c in moments.cells if c.var is not None}
    else:
        q = 0.5 if req.statistic == "median" else float(req.quantile or 0.5)
        exact = _quantile_by_key(rel, value, keys, q)
    columns: list[tuple[str, exp.Expr, str, ColumnSemantic]] = [
        ("_v", value, "DOUBLE", ColumnSemantic.QUANTITATIVE)
    ]
    if keys and req.group_by is not None:
        temporal = field_kind(rel, req.group_by) == "temporal"
        group_type = "VARCHAR" if temporal else rel.columns[req.group_by].type
        columns.append(("_k", keys[0], group_type, ColumnSemantic.NOMINAL))
    source = derived(rel, columns, _present(*keys) if keys else exp.true())
    sample = reservoir_sample(
        source,
        ["_v"],
        cap=BOOTSTRAP_CAP,
        by=["_k"] if keys else (),
        seed=seed,
        total_cap=BOOTSTRAP_TOTAL,
    )
    data = sample.column("_v")
    rows_of: dict[Key, list[int]] = {}
    if keys:
        for i, k in enumerate(sample.table.column("_k").to_pylist()):
            rows_of.setdefault((k,), []).append(i)
    else:
        rows_of[()] = list(range(sample.n))
    statistic = _statistic(req)
    out: list[ConfidenceInterval] = []
    for key in sorted(counts, key=lambda k: tuple((v is None, v) for v in k)):
        n = counts[key]
        group = _group_value(rel, req, key)
        values = data[np.asarray(rows_of.get(key, []), dtype=np.int64)]
        estimate = exact.get(key)
        if n < 2 or len(values) < 2 or estimate is None:
            out.append(
                ConfidenceInterval(
                    field=name,
                    group=group,
                    n=n,
                    estimate=estimate,
                    ci_low=None,
                    ci_high=None,
                    std_error=None,
                    method="bootstrap",
                    sampled=False,
                )
            )
            continue
        interval = bootstrap_ci(
            values,
            statistic,
            ci_level=req.ci_level,
            n_resamples=req.n_resamples,
            seed=seed,
            executor=executor,
        )
        where = f"'{name}'" + (f" ({label([req.group_by or ''], group)})" if group else "")
        warnings += [f"{where}: {w}" for w in interval.warnings]
        sampled = len(values) < n
        scale = math.sqrt(len(values) / n) if sampled else 1.0
        centre = interval.estimate if not sampled else estimate
        out.append(
            ConfidenceInterval(
                field=name,
                group=group,
                n=n,
                estimate=estimate,
                ci_low=centre + scale * (interval.low - interval.estimate),
                ci_high=centre + scale * (interval.high - interval.estimate),
                std_error=interval.se * scale,
                method=f"{interval.method} bootstrap",
                sampled=sampled,
            )
        )
    if any(i.sampled for i in out):
        warnings.append(
            f"'{name}': the bootstrap ran on a random sample of each large group; its interval "
            "was rescaled by sqrt(m/n) to the full data (m-out-of-n bootstrap)."
        )
    return out
