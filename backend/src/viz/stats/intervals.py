"""Confidence intervals for the mean, median, SD or a quantile, per field and group.

Estimates are always exact on all values (SQL moments; quantile_cont, R type 7). Intervals:
- t (mean): mean +/- t(1 - alpha/2, n - 1) sd / sqrt(n), exact from SQL moments (R's t.test).
- order statistic (median, quantiles): the distribution-free interval [x_(l), x_(u)] with
  binomial ranks (Hahn & Meeker; DescTools::QuantileCI "exact"): coverage is at least the
  level for any n and any continuous distribution. The ranks are read exactly in SQL.
- asymptotic (SD): s * exp(-/+ z se / 2) with se^2 = G2 / n + 2 / (n - 1) the variance of
  ln s^2, using the exact excess kurtosis G2 (the log-scale delta method; Bonett 2006 without
  his small-sample terms, which vanish above 10,000 values).
- bootstrap: scipy.stats.bootstrap on all of a group's values (BCa up to 2,000 values,
  percentile above), never on a subsample.

Choice: `auto` uses t for the mean, order statistics for the median and quantiles, and for
the SD the bootstrap up to 10,000 values (BOOTSTRAP_MAX) and the asymptotic interval above.
`bootstrap` bootstraps groups of up to 10,000 values and uses the statistic's exact interval
above (with a warning). Bootstrap work is bounded across groups (BOOTSTRAP_WORK); groups past
the bound get the exact interval too, with a warning.

Why not subsample: rescaling a bootstrap of m values by sqrt(m/n) undercovered badly for
SDs of skewed data and extreme quantiles (0.66-0.85 at a nominal 0.95). Simulated coverage
of the asymptotic SD interval: 0.93-0.97 at n >= 20,000 for normal, exponential and
lognormal(0, 0.5 and 1) data. For very heavy tails (lognormal(0, 1)) every method,
bootstrap included, undercovers at moderate n (about 0.87 at 2,000 and 0.92 at 10,000), so
SD intervals for data with excess kurtosis above 20 carry a warning.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from scipy import stats as sps
from sqlglot import exp

from viz.contracts import (
    ConfidenceInterval,
    ConfidenceIntervalRequest,
    ConfidenceIntervalValues,
    IntervalMethod,
    Scalar,
    StatResult,
)
from viz.stats.bootstrap import bootstrap_ci, quantile
from viz.stats.errors import StatsInputError
from viz.stats.fields import key_expr, key_value, label, measure_expr
from viz.stats.groups import cap_groups
from viz.stats.moments import Cell, CellMoments, cell_moments
from viz.stats.relation import Relation
from viz.stats.results import stat_result
from viz.stats.sampling import DEFAULT_SEED
from viz.stats.sql import alias, fn, lit, num

BOOTSTRAP_MAX = 10_000
BCA_MAX = 2_000
# Values x resamples (plus n^2 for BCa's jackknife) bootstrapped per request: ~1.5 s.
BOOTSTRAP_WORK = 200_000_000
HEAVY_TAILS = 20.0

Key = tuple[Any, ...]
Plan = Literal["t", "order", "asymptotic", "bootstrap"]
_ANALYTIC: dict[str, Plan] = {
    "mean": "t",
    "median": "order",
    "quantile": "order",
    "std": "asymptotic",
}
_ANALYTIC_NAME = {"t": "t", "order": "order-statistic", "asymptotic": "asymptotic"}


def _present(*exprs: exp.Expr) -> exp.Expr:
    return exp.and_(*[exp.Not(this=exp.Is(this=e.copy(), expression=exp.Null())) for e in exprs])


def _statistic(req: ConfidenceIntervalRequest) -> str | Callable[..., np.ndarray]:
    if req.statistic == "quantile":
        assert req.quantile is not None
        return quantile(req.quantile)
    return req.statistic


def order_ranks(n: int, p: float, ci_level: float) -> tuple[int | None, int | None, float]:
    """1-based ranks (l, u) with P(x_(l) <= Q(p) <= x_(u)) >= ci_level, and that coverage.

    Equal tails: l - 1 is the largest k with B(k; n, p) <= alpha/2 and u - 1 the smallest k
    with B(k; n, p) >= 1 - alpha/2. None marks a side that no rank can bound at this level.
    """
    alpha = 1 - ci_level
    k = int(sps.binom.ppf(alpha / 2, n, p))
    if sps.binom.cdf(k, n, p) > alpha / 2:
        k -= 1
    low = k + 1 if k >= 0 else None
    upper = int(sps.binom.ppf(1 - alpha / 2, n, p)) + 1
    high = upper if upper <= n else None
    below = sps.binom.cdf(low - 1, n, p) if low is not None else 0.0
    above = sps.binom.cdf(high - 1, n, p) if high is not None else 1.0
    return low, high, float(above - below)


def asymptotic_sd(
    n: int, var: float, kurtosis: float | None, ci_level: float
) -> tuple[float, float, float] | None:
    """(low, high, se of s) from the log-scale delta method; None when undefined."""
    if n < 4 or kurtosis is None or var <= 0:
        return None
    var_log = kurtosis / n + 2 / (n - 1)
    if var_log <= 0:
        return None
    se_log = math.sqrt(var_log)
    z = float(sps.norm.ppf(0.5 + ci_level / 2))
    s = math.sqrt(var)
    return s * math.exp(-z * se_log / 2), s * math.exp(z * se_log / 2), s * se_log / 2


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
        raise StatsInputError("The t interval is for the mean; use another method for others.")
    fields = list(dict.fromkeys(req.fields))
    source = rel
    warnings: list[str] = []
    key: exp.Expr | None = None
    if req.group_by is not None:
        rel.require(req.group_by)
        if req.group_by in fields:
            raise StatsInputError("Choose different fields for the values and the groups.")
        capped = cap_groups(rel, [req.group_by], req.max_groups)
        source = capped.relation
        if capped.warning:
            warnings.append(capped.warning)
        key = key_expr(rel, req.group_by)
    intervals: list[ConfidenceInterval] = []
    budget = _Budget(BOOTSTRAP_WORK)
    for name in fields:
        value = measure_expr(rel, name)
        intervals += _field(source, req, name, value, key, warnings, budget, seed, executor)
    n = int(rel.rows(rel.select(exp.Count(this=exp.Star())))[0][0])
    statistic = req.statistic if req.statistic != "quantile" else f"{req.quantile:g} quantile"
    methods = sorted({i.method for i in intervals})
    return stat_result(
        ConfidenceIntervalValues(
            statistic=req.statistic,
            quantile=req.quantile,
            ci_level=req.ci_level,
            group_by=req.group_by,
            intervals=intervals,
        ).model_dump(by_alias=True),
        method=f"Confidence intervals for the {statistic} ({', '.join(methods) or 'none'})",
        n=n,
        sampled=False,
        warnings=warnings,
    )


@dataclass
class _Budget:
    left: float


@dataclass
class _Planned:
    cell: Cell
    plan: Plan
    # Why the bootstrap wasn't used: "size" (over BOOTSTRAP_MAX) or "budget"; None otherwise.
    fallback: str | None = None


def _plans(
    req: ConfidenceIntervalRequest, cells: Sequence[Cell], budget: _Budget
) -> list[_Planned]:
    analytic = _ANALYTIC[req.statistic]
    wants_bootstrap = req.method == "bootstrap" or (
        req.method == "auto" and analytic == "asymptotic"
    )
    if not wants_bootstrap:
        return [_Planned(c, analytic) for c in cells]
    planned: dict[Key, _Planned] = {}
    for cell in sorted(cells, key=lambda c: c.n):  # smallest first: as many as fit
        if cell.n > BOOTSTRAP_MAX:
            planned[cell.key] = _Planned(cell, analytic, "size")
            continue
        work = cell.n * req.n_resamples + (cell.n**2 if cell.n <= BCA_MAX else 0)
        if work > budget.left:
            planned[cell.key] = _Planned(cell, analytic, "budget")
            continue
        budget.left -= work
        planned[cell.key] = _Planned(cell, "bootstrap")
    return [planned[c.key] for c in cells]


def _field(
    rel: Relation,
    req: ConfidenceIntervalRequest,
    name: str,
    value: exp.Expr,
    key: exp.Expr | None,
    warnings: list[str],
    budget: _Budget,
    seed: int,
    executor: Executor | None,
) -> list[ConfidenceInterval]:
    # Group by the key and its text form: the text identifies groups in later queries.
    keys = [key, exp.cast(key.copy(), "VARCHAR")] if key is not None else []
    moments = cell_moments(rel, value, keys)
    cells = sorted(moments.cells, key=lambda c: tuple((v is None, v) for v in c.key[:1]))
    estimates = _estimates(rel, req, value, keys, moments)
    planned = _plans(req, cells, budget)
    # Keys are never NULL here (cell_moments drops rows with a missing key).
    texts: list[str | None] = [str(p.cell.key[1]) if keys else None for p in planned]
    boot_values = _values(
        rel, value, key, [t for p, t in zip(planned, texts, strict=True) if p.plan == "bootstrap"]
    )
    order: dict[str | None, tuple[int | None, int | None, float]] = {}
    if req.statistic in ("median", "quantile"):
        q = 0.5 if req.statistic == "median" else float(req.quantile or 0.5)
        for p, text in zip(planned, texts, strict=True):
            if p.plan == "order" and p.cell.n > 0:
                order[text] = order_ranks(p.cell.n, q, req.ci_level)
    bounds = _order_values(rel, value, key, order)

    def where(group: list[Scalar] | None) -> str:
        return f"'{name}'" + (f" ({label([req.group_by or ''], group)})" if group else "")

    out: list[ConfidenceInterval] = []
    statistic = _statistic(req)
    for p, text in zip(planned, texts, strict=True):
        cell = p.cell
        group = [key_value(rel, req.group_by, cell.key[0])] if req.group_by else None
        estimate = estimates.get(cell.key)
        low = high = se = None
        method = _method_name(p.plan, cell.n)
        if p.plan == "t" and cell.n >= 2 and cell.var is not None:
            mean = moments.mean(cell)
            se = math.sqrt(cell.var / cell.n)
            crit = float(sps.t.ppf(0.5 + req.ci_level / 2, cell.n - 1))
            low, high = mean - crit * se, mean + crit * se
        elif p.plan == "order" and text in order:
            l_rank, u_rank, coverage = order[text]
            low = bounds.get((text, l_rank)) if l_rank is not None else None
            high = bounds.get((text, u_rank)) if u_rank is not None else None
            if cell.n >= 2 and (l_rank is None or u_rank is None):
                warnings.append(
                    f"{where(group)}: with {cell.n} values no order statistic gives the "
                    f"{_side(l_rank, u_rank)} bound at {req.ci_level:.0%}."
                )
            elif coverage > req.ci_level + 0.01:
                warnings.append(
                    f"{where(group)}: the order-statistic interval covers {coverage:.1%} "
                    f"(the closest achievable with {cell.n} values)."
                )
        elif p.plan == "asymptotic" and cell.var is not None:
            result = asymptotic_sd(cell.n, cell.var, cell.kurtosis, req.ci_level)
            if result is not None:
                low, high, se = result
            elif cell.n >= 2 and cell.var == 0:
                low = high = 0.0
        elif p.plan == "bootstrap" and cell.n >= 2:
            interval = bootstrap_ci(
                boot_values.get(text, np.empty(0)),
                statistic,
                ci_level=req.ci_level,
                n_resamples=req.n_resamples,
                method="bca" if cell.n <= BCA_MAX else "percentile",
                seed=seed,
                executor=executor,
            )
            warnings += [f"{where(group)}: {w}" for w in interval.warnings]
            low, high, se = interval.low, interval.high, interval.se
            method = _BOOTSTRAP_NAME[interval.method]
        if req.statistic == "std" and cell.kurtosis is not None and cell.kurtosis > HEAVY_TAILS:
            warnings.append(
                f"{where(group)}: heavy tails (excess kurtosis {cell.kurtosis:.3g}); SD "
                "intervals tend to be too narrow unless there are very many values."
            )
        if cell.n < 2:
            warnings.append(f"{where(group)}: an interval needs at least 2 values.")
        out.append(
            ConfidenceInterval(
                field=name,
                group=group,
                n=cell.n,
                estimate=estimate,
                ci_low=low,
                ci_high=high,
                std_error=se,
                method=method,
            )
        )
    _fallback_warnings(req, name, planned, warnings)
    return out


_BOOTSTRAP_NAME: dict[str, IntervalMethod] = {
    "bca": "bca bootstrap",
    "percentile": "percentile bootstrap",
    "basic": "basic bootstrap",
}


def _method_name(plan: Plan, n: int) -> IntervalMethod:
    if plan == "t":
        return "t"
    if plan == "order":
        return "order statistic"
    if plan == "asymptotic":
        return "asymptotic"
    return "bca bootstrap" if n <= BCA_MAX else "percentile bootstrap"


def _side(low: int | None, high: int | None) -> str:
    if low is None and high is None:
        return "lower or upper"
    return "lower" if low is None else "upper"


def _fallback_warnings(
    req: ConfidenceIntervalRequest, name: str, planned: Sequence[_Planned], warnings: list[str]
) -> None:
    analytic = _ANALYTIC_NAME[_ANALYTIC[req.statistic]]
    sized = sum(1 for p in planned if p.fallback == "size")
    if sized and req.method == "bootstrap":
        warnings.append(
            f"'{name}': above {BOOTSTRAP_MAX:,} values the {analytic} interval (exact on all "
            "values) is used instead of the bootstrap."
        )
    over = sum(1 for p in planned if p.fallback == "budget")
    if over:
        warnings.append(
            f"'{name}': {over} group(s) got the {analytic} interval instead of the bootstrap "
            "to bound the running time; use fewer resamples or groups to bootstrap them all."
        )


def _estimates(
    rel: Relation,
    req: ConfidenceIntervalRequest,
    value: exp.Expr,
    keys: list[exp.Expr],
    moments: CellMoments,
) -> dict[Key, float]:
    if req.statistic == "mean":
        return {c.key: moments.mean(c) for c in moments.cells}
    if req.statistic == "std":
        return {c.key: math.sqrt(c.var) for c in moments.cells if c.var is not None}
    q = 0.5 if req.statistic == "median" else float(req.quantile or 0.5)
    return _quantile_by_key(rel, value, keys, q)


def _text_key(key: exp.Expr | None) -> exp.Expr:
    return exp.cast(key.copy(), "VARCHAR") if key is not None else exp.cast(exp.Null(), "VARCHAR")


def _values(
    rel: Relation, value: exp.Expr, key: exp.Expr | None, texts: Sequence[str | None]
) -> dict[str | None, np.ndarray]:
    """All values of the groups with these text keys (each has at most BOOTSTRAP_MAX)."""
    if not texts:
        return {}
    condition = _present(value) if key is None else _present(value, key)
    narrowed = rel.narrow(condition)
    if key is not None:
        narrowed = narrowed.narrow(
            exp.In(this=_text_key(key), expressions=[lit(t) for t in texts if t is not None])
        )
    table = rel.arrow(narrowed.select(alias(_text_key(key), "_kt"), alias(value.copy(), "_v")))
    keys = table.column("_kt").to_pylist()
    values = np.asarray(table.column("_v").to_numpy(zero_copy_only=False), dtype=float)
    rows: dict[str | None, list[int]] = {}
    for i, k in enumerate(keys):
        rows.setdefault(k, []).append(i)
    return {k: values[np.asarray(idx, dtype=np.int64)] for k, idx in rows.items()}


def _order_values(
    rel: Relation,
    value: exp.Expr,
    key: exp.Expr | None,
    ranks: dict[str | None, tuple[int | None, int | None, float]],
) -> dict[tuple[str | None, int], float]:
    """x_(rank) within each group, exactly: a row_number window joined to the wanted ranks."""
    wanted = [(t, r) for t, (lo, hi, _) in ranks.items() for r in (lo, hi) if r is not None]
    if not wanted:
        return {}
    condition = _present(value) if key is None else _present(value, key)
    inner = rel.narrow(condition).select(alias(_text_key(key), "_kt"), alias(value.copy(), "_v"))
    targets = ", ".join(
        f"({exp.cast(lit(t), 'VARCHAR').sql(dialect='duckdb')}, {int(r)})" for t, r in wanted
    )
    sql = (
        f"WITH _r AS (SELECT _kt, _v, row_number() OVER (PARTITION BY _kt ORDER BY _v) AS _rn "
        f"FROM ({inner.sql(dialect='duckdb')}) AS _d) "
        f"SELECT _r._kt, _r._rn, _r._v FROM _r JOIN (VALUES {targets}) AS _w(_kt, _rn) "
        "ON _r._kt IS NOT DISTINCT FROM _w._kt AND _r._rn = _w._rn"
    )
    return {(kt, int(rn)): float(v) for kt, rn, v in rel.rows(sql)}
