"""Outlier detection, exact in DuckDB on the full data, per field and optionally per group.

A value is an outlier when |score| > threshold:
- iqr (Tukey's fences, default 1.5): score = distance beyond the nearer quartile in IQRs;
  fences q1 - k IQR and q3 + k IQR (quartiles are R type 7).
- zscore (default 3): z = (x - mean) / sd. With n values |z| can't exceed (n - 1) / sqrt(n),
  so small samples get a warning when that bound is below the threshold.
- mad (Iglewicz & Hoaglin's modified z, default 3.5): M = (x - median) / (MAD / 0.6745).
  When MAD = 0 (over half the values tie) the mean absolute deviation x 1.2533 is used
  instead, with a warning (as IBM SPSS); when that is 0 too, nothing is an outlier.

Queries: fences from one UNPIVOT + GROUP BY (field, group) pass, so quantiles of many fields
finalize in parallel; then outlier counts, and the most extreme rows per field, against a
small table of fences. Group keys match as text, NULL included.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from scipy import stats as sps
from sqlglot import exp

from viz.contracts import Scalar, StatResult
from viz.stats.fields import key_expr, key_value, measure_expr
from viz.stats.groups import cap_groups
from viz.stats.models import (
    OutlierMethod,
    OutlierRequest,
    OutlierRow,
    OutlierSummary,
    OutlierValues,
)
from viz.stats.relation import Relation
from viz.stats.results import stat_result
from viz.stats.sql import alias, lit, num

DEFAULT_THRESHOLD: dict[OutlierMethod, float] = {"iqr": 1.5, "zscore": 3.0, "mad": 3.5}
MAD_CONSTANT = float(sps.norm.ppf(0.75))  # 0.6745: MAD / 0.6745 estimates sigma
MEAN_AD_CONSTANT = math.sqrt(math.pi / 2)  # 1.2533: mean |x - median| x this estimates sigma

_METHOD = {
    "iqr": "Tukey's fences",
    "zscore": "z-scores",
    "mad": "modified z-scores (median absolute deviation)",
}


@dataclass(frozen=True)
class _Fence:
    field: int
    group_text: str | None
    group_raw: Any
    n: int
    center: float | None
    spread: float | None
    low: float | None
    high: float | None
    # Where scores are measured from: (low anchor, high anchor) = quartiles for iqr.
    anchor_low: float | None
    anchor_high: float | None


def _sql(expression: exp.Expr) -> str:
    return expression.sql(dialect="duckdb")


def outliers(rel: Relation, req: OutlierRequest) -> StatResult:
    fields = list(dict.fromkeys(req.fields))
    exprs = [measure_expr(rel, f) for f in fields]
    include = list(dict.fromkeys(req.include))
    for name in include:
        rel.require(name)
    threshold = req.threshold if req.threshold is not None else DEFAULT_THRESHOLD[req.method]
    warnings: list[str] = []
    source = rel
    group_raw: exp.Expr | None = None
    if req.group_by is not None:
        rel.require(req.group_by)
        capped = cap_groups(rel, [req.group_by], req.max_groups)
        source = capped.relation
        if capped.warning:
            warnings.append(capped.warning)
        group_raw = key_expr(rel, req.group_by)
    selects: list[exp.Expr] = [
        alias(exp.cast(group_raw.copy(), "VARCHAR") if group_raw is not None else exp.Null(), "_gt"),
        alias(group_raw.copy() if group_raw is not None else exp.Null(), "_gr"),
        *[alias(key_expr(rel, name), f"_i{j}") for j, name in enumerate(include)],
        *[alias(e, f"_c{i}") for i, e in enumerate(exprs)],
    ]
    on = ", ".join(f"_c{i}" for i in range(len(fields)))
    unpivoted = (
        f"(UNPIVOT ({_sql(source.select(*selects))}) ON {on} INTO NAME _f VALUE _v)"
    )
    fences = _fences(rel, unpivoted, req.method, threshold, warnings, fields)
    counts = _counts(rel, unpivoted, fences)
    summaries: list[OutlierSummary] = []
    for fence in fences:
        n_low, n_high = counts.get((fence.field, fence.group_text), (0, 0))
        summaries.append(
            OutlierSummary(
                field=fields[fence.field],
                group=_group(rel, req.group_by, fence.group_raw),
                n=fence.n,
                center=fence.center,
                spread=fence.spread,
                low=fence.low,
                high=fence.high,
                n_low=n_low,
                n_high=n_high,
                share=(n_low + n_high) / fence.n if fence.n else None,
            )
        )
    rows: list[OutlierRow] = []
    if req.max_rows > 0 and any(s.n_low + s.n_high for s in summaries):
        rows = _rows(rel, unpivoted, fences, fields, include, req, req.max_rows)
    total = sum(s.n_low + s.n_high for s in summaries)
    values = OutlierValues(
        method=req.method,
        threshold=threshold,
        group_by=req.group_by,
        summaries=summaries,
        rows=rows,
    )
    n = int(rel.rows(rel.select(exp.Count(this=exp.Star())))[0][0])
    if total == 0:
        warnings.append("No outliers found.")
    return stat_result(
        values.model_dump(by_alias=True),
        method=f"Outliers by {_METHOD[req.method]} (threshold {threshold:g}, exact)",
        n=n,
        sampled=False,
        warnings=warnings,
    )


def _group(rel: Relation, group_by: str | None, raw: Any) -> list[Scalar] | None:
    if group_by is None:
        return None
    return [key_value(rel, group_by, raw)]


def _fences(
    rel: Relation,
    unpivoted: str,
    method: OutlierMethod,
    k: float,
    warnings: list[str],
    fields: Sequence[str],
) -> list[_Fence]:
    if method == "iqr":
        stats = "count(*) AS n, quantile_cont(_v, [0.25, 0.5, 0.75]) AS q"
        sql = f"SELECT _gt, any_value(_gr) AS _gr, _f, {stats} FROM {unpivoted} GROUP BY _gt, _f"
    elif method == "zscore":
        stats = "count(*) AS n, avg(_v) AS m, stddev_samp(_v) AS s"
        sql = f"SELECT _gt, any_value(_gr) AS _gr, _f, {stats} FROM {unpivoted} GROUP BY _gt, _f"
    else:
        sql = (
            f"WITH _u AS (SELECT _gt, _gr, _f, _v FROM {unpivoted}), "
            "_m AS (SELECT _gt, _f, quantile_cont(_v, 0.5) AS med FROM _u GROUP BY _gt, _f) "
            "SELECT _u._gt, any_value(_u._gr) AS _gr, _u._f, count(*) AS n, "
            "any_value(_m.med) AS med, quantile_cont(abs(_v - _m.med), 0.5) AS mad, "
            "avg(abs(_v - _m.med)) AS meanad FROM _u JOIN _m "
            "ON _u._gt IS NOT DISTINCT FROM _m._gt AND _u._f = _m._f GROUP BY _u._gt, _u._f"
        )
    out: list[_Fence] = []
    fallback: set[str] = set()
    for row in rel.arrow(sql).to_pylist():
        index = int(row["_f"][2:])
        n = int(row["n"])
        center = spread = low = high = None
        anchor_low = anchor_high = None
        if method == "iqr":
            q1, median, q3 = row["q"]
            center, spread = median, q3 - q1
            low, high = q1 - k * spread, q3 + k * spread
            anchor_low, anchor_high = q1, q3
        elif method == "zscore":
            center, sd = row["m"], row["s"]
            if sd is not None:
                spread = sd
                low, high = center - k * sd, center + k * sd
            if n > 1 and (n - 1) / math.sqrt(n) <= k:
                warnings.append(
                    f"'{fields[index]}': with {n} values no |z| can exceed {k:g}, so z-scores "
                    "can't flag outliers; use the IQR or MAD method."
                )
        else:
            center = row["med"]
            spread = row["mad"] / MAD_CONSTANT
            if spread == 0:
                spread = row["meanad"] * MEAN_AD_CONSTANT
                if spread > 0:
                    fallback.add(fields[index])
            if spread > 0:
                low, high = center - k * spread, center + k * spread
            else:
                low = high = None
        if method != "iqr":
            anchor_low = anchor_high = center
        out.append(
            _Fence(index, row["_gt"], row["_gr"], n, center, spread, low, high, anchor_low,
                   anchor_high)
        )
    for name in fields:
        if name in fallback:
            warnings.append(
                f"'{name}': over half the values are equal (MAD = 0), so the mean absolute "
                "deviation was used instead."
            )
    out.sort(key=lambda f: (f.field, f.group_text is None, f.group_text or ""))
    return out


def _fence_table(fences: Sequence[_Fence]) -> str:
    """(_gt, _f, lo, hi, al, ah, sp): the fences as an inline VALUES table."""
    rows = []
    for f in fences:
        if f.low is None or f.high is None:
            continue
        parts = [
            _sql(exp.cast(lit(f.group_text), "VARCHAR")),
            _sql(lit(f"_c{f.field}")),
            _sql(num(float(f.low))),
            _sql(num(float(f.high))),
            _sql(num(float(f.anchor_low if f.anchor_low is not None else f.low))),
            _sql(num(float(f.anchor_high if f.anchor_high is not None else f.high))),
            _sql(num(float(f.spread or 0.0))),
        ]
        rows.append(f"({', '.join(parts)})")
    if not rows:
        return ""
    return f"(VALUES {', '.join(rows)}) AS _b(_gt, _f, lo, hi, al, ah, sp)"


def _joined(unpivoted: str, table: str) -> str:
    return (
        f"FROM {unpivoted} AS _u JOIN {table} "
        "ON _u._gt IS NOT DISTINCT FROM _b._gt AND _u._f = _b._f"
    )


def _counts(
    rel: Relation, unpivoted: str, fences: Sequence[_Fence]
) -> dict[tuple[int, str | None], tuple[int, int]]:
    table = _fence_table(fences)
    if not table:
        return {}
    sql = (
        "SELECT _u._gt, _u._f, count(*) FILTER (WHERE _v < lo) AS n_low, "
        f"count(*) FILTER (WHERE _v > hi) AS n_high {_joined(unpivoted, table)} "
        "GROUP BY _u._gt, _u._f"
    )
    return {
        (int(f[2:]), gt): (int(lo), int(hi)) for gt, f, lo, hi in rel.rows(sql)
    }


def _rows(
    rel: Relation,
    unpivoted: str,
    fences: Sequence[_Fence],
    fields: Sequence[str],
    include: Sequence[str],
    req: OutlierRequest,
    limit: int,
) -> list[OutlierRow]:
    table = _fence_table(fences)
    if not table:
        return []
    # Score: distance from the anchor in spreads; ordered by it (or by raw distance).
    score = "CASE WHEN sp > 0 THEN (CASE WHEN _v > ah THEN _v - ah ELSE _v - al END) / sp END"
    distance = "abs(CASE WHEN _v > ah THEN _v - ah ELSE _v - al END)"
    extras = "".join(f", _u._i{j}" for j in range(len(include)))
    sql = (
        f"SELECT _u._gr, _u._f, _v, {score} AS score, _v > hi AS high{extras} "
        f"{_joined(unpivoted, table)} WHERE _v < lo OR _v > hi "
        f"QUALIFY row_number() OVER (PARTITION BY _u._f ORDER BY {distance} DESC, _v) "
        f"<= {int(limit)} ORDER BY _u._f, {distance} DESC, _v"
    )
    out: list[OutlierRow] = []
    for row in rel.arrow(sql).to_pylist():
        index = int(row["_f"][2:])
        out.append(
            OutlierRow(
                field=fields[index],
                group=_group(rel, req.group_by, row["_gr"]),
                value=float(row["_v"]),
                score=row["score"],
                side="high" if row["high"] else "low",
                row={name: key_value(rel, name, row[f"_i{j}"]) for j, name in enumerate(include)},
            )
        )
    # Fields in request order; within a field, most extreme first (SQL order, stable sort).
    out.sort(key=lambda r: fields.index(r.field))
    return out
