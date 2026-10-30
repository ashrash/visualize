"""Correlation matrices: Pearson exact in DuckDB; Spearman and Kendall in SciPy on a sample.

Pearson: DuckDB's corr() is shift-sensitive (about 1e-8 relative error at mean 1e9, sd 1)
and overflows silently near 1e200 (it returned 0.0). So a first pass finds each field's
mean and range, and corr() runs on z = x * 2^-e - c * 2^-e, where c is the mean and 2^e
bounds the field's spread around it: every z is within [-1, 1], the correlation is
unchanged, and the result agrees with NumPy on centred data to ~1e-15.

p-values and intervals (as scipy.stats.pearsonr / spearmanr / kendalltau):
- Pearson: t = r sqrt((n - 2) / (1 - r^2)) on n - 2 df; Fisher z interval, se 1/sqrt(n - 3).
- Spearman: SciPy's t approximation; Fisher z interval with Bonett & Wright's (2000)
  se = sqrt((1 + r^2 / 2) / (n - 3)).
- Kendall: tau-b with SciPy's p-value (exact for small samples without ties); Fisher z
  interval with Fieller et al.'s (1957) se = sqrt(0.437 / (n - 4)).

Missing values: `pairwise` uses, for each pair, the rows where both are present (as
pandas' DataFrame.corr); `listwise` only the rows where every field is present. Rank
correlations run on one shared random sample, capped so that rows x pairs stays within
RANK_BUDGET (1M rows for a single pair, at least 5,000 rows).
"""

from __future__ import annotations

import math
import warnings as py_warnings
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy import stats as sps
from sqlglot import exp

from viz.contracts import StatResult
from viz.stats.errors import StatsInputError
from viz.stats.fields import measure_expr, measure_note
from viz.stats.models import CorrelationMethod, CorrelationRequest, CorrelationValues, MissingPolicy
from viz.stats.relation import Relation
from viz.stats.results import stat_result
from viz.stats.sampling import DEFAULT_SEED, MODEL_CAP, reservoir_sample
from viz.stats.sql import alias, count_if, count_star, fn, num

RANK_BUDGET = 5_000_000
RANK_MIN_ROWS = 5_000

_METHOD = {
    "pearson": "Pearson correlation",
    "spearman": "Spearman rank correlation",
    "kendall": "Kendall rank correlation (tau-b)",
}


@dataclass
class CorrelationMatrix:
    fields: list[str]
    method: CorrelationMethod
    coefficient: np.ndarray
    p_value: np.ndarray
    ci_low: np.ndarray
    ci_high: np.ndarray
    n: np.ndarray
    # Rows considered (the sample size when sampled; listwise: complete rows).
    rows: int
    n_total: int | None = None
    n_missing: int = 0
    sampled: bool = False
    warnings: list[str] = field(default_factory=list)


def rank_cap(n_fields: int) -> int:
    pairs = n_fields * (n_fields - 1) // 2
    return int(min(MODEL_CAP, max(RANK_MIN_ROWS, RANK_BUDGET // max(1, pairs))))


def fisher_interval(
    r: float, n: int, method: CorrelationMethod, ci_level: float
) -> tuple[float | None, float | None]:
    """Fisher z interval for a correlation coefficient (None when n is too small)."""
    if method == "pearson":
        if n < 4:
            return None, None
        se = 1 / math.sqrt(n - 3)
    elif method == "spearman":
        if n < 4:
            return None, None
        se = math.sqrt((1 + r * r / 2) / (n - 3))
    else:
        if n < 5:
            return None, None
        se = math.sqrt(0.437 / (n - 4))
    if abs(r) >= 1:
        return r, r
    z = math.atanh(r)
    crit = float(sps.norm.ppf(0.5 + ci_level / 2))
    return math.tanh(z - crit * se), math.tanh(z + crit * se)


def pearson_p(r: float, n: int) -> float | None:
    if n < 3:
        return None
    if abs(r) >= 1:
        return 0.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return float(min(1.0, 2 * sps.t.sf(abs(t), n - 2)))


def _scale(count: int, mean: float | None, lo: float | None, hi: float | None) -> tuple[float, float] | None:
    """(k, c * k): z = x * k - c * k lies in [-1, 1]. None for a field without spread."""
    if not count or lo is None or hi is None or lo == hi:
        return None
    center = mean if mean is not None and math.isfinite(mean) and lo <= mean <= hi else lo / 2 + hi / 2
    half = max(abs(hi / 2 - center / 2), abs(lo / 2 - center / 2))
    if half == 0:
        return None
    exponent = math.frexp(half)[1] + 1  # 2 * half < 2^exponent
    k = math.ldexp(1.0, -exponent)
    return k, center * k


class _Pairs:
    """Collects per-pair warnings into one sentence per reason."""

    def __init__(self) -> None:
        self.by_reason: dict[str, list[str]] = {}

    def add(self, reason: str, a: str, b: str) -> None:
        self.by_reason.setdefault(reason, []).append(f"{a} & {b}")

    def render(self) -> list[str]:
        out = []
        for reason, pairs in self.by_reason.items():
            shown = ", ".join(pairs[:3]) + (f" and {len(pairs) - 3} more" if len(pairs) > 3 else "")
            out.append(f"{reason} ({shown}).")
        return out


def correlation_matrix(
    rel: Relation,
    fields: Sequence[str],
    *,
    method: CorrelationMethod = "pearson",
    missing: MissingPolicy = "pairwise",
    ci_level: float = 0.95,
    seed: int = DEFAULT_SEED,
) -> CorrelationMatrix:
    """Correlations of every pair of `fields` (numeric, boolean 0/1, or dates as days)."""
    names = list(dict.fromkeys(fields))
    if len(names) < 2:
        raise StatsInputError("Choose at least 2 different fields to correlate.")
    exprs = [measure_expr(rel, f, temporal=True) for f in names]
    if method == "pearson":
        matrix = _pearson(rel, names, exprs, missing)
    else:
        matrix = _ranked(rel, names, exprs, method, missing, seed)
    k = len(names)
    notes = _Pairs()
    for i in range(k):
        for j in range(i + 1, k):
            r, n = matrix.coefficient[i, j], int(matrix.n[i, j])
            if not math.isfinite(r):
                reason = (
                    "Fewer than 2 rows have both values"
                    if n < 2
                    else "No variation where both values are present, so the correlation "
                    "is undefined"
                )
                notes.add(reason, names[i], names[j])
                continue
            if method == "pearson":
                matrix.p_value[i, j] = _none(pearson_p(r, n))
            if not math.isfinite(matrix.p_value[i, j]):
                notes.add("Too few pairs for a p-value", names[i], names[j])
            low, high = fisher_interval(r, n, method, ci_level)
            matrix.ci_low[i, j], matrix.ci_high[i, j] = _none(low), _none(high)
            for name in ("p_value", "ci_low", "ci_high"):
                getattr(matrix, name)[j, i] = getattr(matrix, name)[i, j]
    for i in range(k):
        if matrix.n[i, i] >= 2 and math.isfinite(matrix.coefficient[i, i]):
            continue
        matrix.coefficient[i, i] = np.nan
    matrix.warnings += notes.render()
    matrix.warnings += [note for f in names if (note := measure_note(rel, f))]
    return matrix


def _varies(values: np.ndarray) -> bool:
    return values.size > 1 and float(np.ptp(values)) > 0


def _none(value: float | None) -> float:
    return np.nan if value is None else value


def _empty(k: int) -> np.ndarray:
    return np.full((k, k), np.nan)


def _present(expr: exp.Expr) -> exp.Expr:
    return exp.Not(this=exp.Is(this=expr.copy(), expression=exp.Null()))


def _pearson(
    rel: Relation, names: list[str], exprs: list[exp.Expr], missing: MissingPolicy
) -> CorrelationMatrix:
    k = len(names)
    complete = exp.and_(*[_present(e) for e in exprs])
    base = rel.narrow(complete) if missing == "listwise" else rel
    selects: list[exp.Expr] = [alias(count_star(), "rows")]
    if missing == "listwise":
        selects.append(alias(count_if(complete), "complete"))
    for i, e in enumerate(exprs):
        selects += [
            alias(exp.Count(this=e.copy()), f"n{i}"),
            alias(fn("avg", e.copy()), f"m{i}"),
            alias(fn("min", e.copy()), f"lo{i}"),
            alias(fn("max", e.copy()), f"hi{i}"),
        ]
    first = rel.arrow(rel.select(*selects)).to_pylist()[0]
    total = int(first["rows"])
    rows = int(first["complete"]) if missing == "listwise" else total
    if missing == "listwise" and rows != total:
        # Means and ranges over the complete rows (the ones the coefficients use).
        first = base.arrow(base.select(*selects)).to_pylist()[0]
    scales = [
        _scale(int(first[f"n{i}"]), first[f"m{i}"], first[f"lo{i}"], first[f"hi{i}"])
        for i in range(k)
    ]
    inner: list[exp.Expr] = []
    for i, (e, s) in enumerate(zip(exprs, scales, strict=True)):
        # No spread: 0 where present, so pairs are still counted (and corr is undefined).
        scaled = exp.Mul(this=e.copy(), expression=num(0.0 if s is None else s[0]))
        z = scaled if s is None else exp.Sub(this=scaled, expression=num(s[1]))
        inner.append(alias(exp.cast(z, "DOUBLE"), f"z{i}"))
    pairs = [(i, j) for i in range(k) for j in range(i + 1, k)]
    outer: list[exp.Expr] = []
    for i, j in pairs:
        outer.append(alias(fn("corr", exp.column(f"z{i}"), exp.column(f"z{j}")), f"r{i}_{j}"))
        outer.append(
            alias(fn("regr_count", exp.column(f"z{i}"), exp.column(f"z{j}")), f"n{i}_{j}")
        )
    subquery = base.select(*inner).subquery("_z")
    result = rel.arrow(exp.select(*outer).from_(subquery)).to_pylist()[0]
    matrix = CorrelationMatrix(
        fields=names,
        method="pearson",
        coefficient=_empty(k),
        p_value=_empty(k),
        ci_low=_empty(k),
        ci_high=_empty(k),
        n=np.zeros((k, k), dtype=np.int64),
        rows=rows,
        n_missing=total - rows if missing == "listwise" else 0,
    )
    for i in range(k):
        matrix.n[i, i] = int(first[f"n{i}"])
        matrix.coefficient[i, i] = 1.0 if scales[i] is not None else np.nan
    for i, j in pairs:
        r = result[f"r{i}_{j}"]
        n = int(result[f"n{i}_{j}"] or 0)
        if scales[i] is None or scales[j] is None or r is None or not math.isfinite(r):
            r = np.nan
        else:
            r = min(1.0, max(-1.0, float(r)))
        matrix.coefficient[i, j] = matrix.coefficient[j, i] = r
        matrix.n[i, j] = matrix.n[j, i] = n
    return matrix


def _ranked(
    rel: Relation,
    names: list[str],
    exprs: list[exp.Expr],
    method: CorrelationMethod,
    missing: MissingPolicy,
    seed: int,
) -> CorrelationMatrix:
    k = len(names)
    sample = reservoir_sample(
        rel, names, cap=rank_cap(k), seed=seed, dropna=missing == "listwise"
    )
    columns = [sample.column(f) for f in names]
    masks = [np.isfinite(c) for c in columns]
    matrix = CorrelationMatrix(
        fields=names,
        method=method,
        coefficient=_empty(k),
        p_value=_empty(k),
        ci_low=_empty(k),
        ci_high=_empty(k),
        n=np.zeros((k, k), dtype=np.int64),
        rows=sample.n,
        n_total=sample.n_total,
        n_missing=sample.n_missing,
        sampled=sample.sampled,
    )
    for i in range(k):
        matrix.n[i, i] = int(masks[i].sum())
        matrix.coefficient[i, i] = 1.0 if _varies(columns[i][masks[i]]) else np.nan
    for i in range(k):
        for j in range(i + 1, k):
            mask = masks[i] & masks[j]
            x, y = columns[i][mask], columns[j][mask]
            n = int(mask.sum())
            matrix.n[i, j] = matrix.n[j, i] = n
            if n < 2 or not (_varies(x) and _varies(y)):
                continue
            with py_warnings.catch_warnings():
                py_warnings.simplefilter("ignore", RuntimeWarning)
                result = sps.spearmanr(x, y) if method == "spearman" else sps.kendalltau(x, y)
            r = float(result.statistic)
            p = float(result.pvalue)
            if not math.isfinite(r):
                continue
            matrix.coefficient[i, j] = matrix.coefficient[j, i] = min(1.0, max(-1.0, r))
            matrix.p_value[i, j] = p if n >= 3 and math.isfinite(p) else np.nan
    return matrix


def correlation(rel: Relation, req: CorrelationRequest) -> StatResult:
    matrix = correlation_matrix(
        rel, req.fields, method=req.method, missing=req.missing, ci_level=req.ci_level
    )

    def grid(values: np.ndarray) -> list[list[float | None]]:
        return [[float(x) if math.isfinite(x) else None for x in row] for row in values]

    values = CorrelationValues(
        fields=matrix.fields,
        method=req.method,
        missing=req.missing,
        ci_level=req.ci_level,
        coefficient=grid(matrix.coefficient),
        p_value=grid(matrix.p_value),
        ci_low=grid(matrix.ci_low),
        ci_high=grid(matrix.ci_high),
        n=matrix.n.tolist(),
    )
    exact = " (exact)" if req.method == "pearson" else ""
    return stat_result(
        values.model_dump(by_alias=True),
        method=f"{_METHOD[req.method]}{exact}, {req.missing} deletion",
        n=matrix.rows,
        n_total=matrix.n_total,
        n_missing=matrix.n_missing,
        sampled=matrix.sampled,
        warnings=matrix.warnings,
    )
