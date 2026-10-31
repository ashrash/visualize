"""Exact per-cell moments in DuckDB: the sufficient statistics of t-tests, ANOVA and t-CIs.

`cell_moments` returns n, mean, variance (ddof=1), skewness (G1) and excess kurtosis (G2)
for each cell (combination of key values), computed on the full filtered data.

Precision: DuckDB's online variance loses about eps * (|mean| / sd) relative accuracy (a
2e-8 relative error at mean 1e9, sd 1). So a first pass finds the overall mean C, and the
second computes every moment of (x - C): shift-invariant, and well conditioned whenever
the cells share a common offset (the NIST StRD "difficult" ANOVA pattern). Cell means are
C + avg(x - C).

`levene` is the Brown-Forsythe variant (deviations from the cell median), as R's
car::leveneTest and scipy.stats.levene(center="median"): a one-way ANOVA on |x - median|.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from scipy import stats as sps
from sqlglot import exp

from viz.stats.errors import StatsInputError
from viz.stats.relation import Relation
from viz.stats.sql import alias, count_if, count_star, fn, num

Key = tuple[object, ...]


@dataclass(frozen=True)
class Cell:
    # Key values as read (via fields.key_expr); () when ungrouped.
    key: Key
    n: int
    # Mean minus the overall centre C (see CellMoments.mean).
    offset: float
    # Sample variance (ddof=1); None when n < 2.
    var: float | None
    skewness: float | None
    kurtosis: float | None
    min: float
    max: float


@dataclass(frozen=True)
class CellMoments:
    # The overall mean of every value used (the centre the offsets are measured from).
    center: float
    cells: list[Cell]
    # Rows considered (after filters and the extra condition).
    rows: int
    # Rows used: value and every key present.
    used: int

    @property
    def n_missing(self) -> int:
        return self.rows - self.used

    def mean(self, cell: Cell) -> float:
        return self.center + cell.offset

    def by_key(self) -> dict[Key, Cell]:
        return {c.key: c for c in self.cells}


def _not_null(*exprs: exp.Expr) -> exp.Expr:
    parts = [exp.Not(this=exp.Is(this=e.copy(), expression=exp.Null())) for e in exprs]
    return exp.and_(*parts)


def _finite(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def cell_moments(
    rel: Relation,
    value: exp.Expr,
    keys: Sequence[exp.Expr] = (),
    *,
    condition: exp.Expr | None = None,
) -> CellMoments:
    """Moments of `value` (DOUBLE, NULL = missing) per combination of `keys`.

    Rows where `value` or any key is NULL are left out (counted in rows - used). Raises
    StatsInputError when the values overflow a double.
    """
    base = rel if condition is None else rel.narrow(condition)
    usable = _not_null(value, *keys)
    totals = base.rows(
        base.select(
            alias(count_star(), "rows"),
            alias(count_if(usable), "used"),
            alias(exp.Filter(this=fn("avg", value), expression=exp.Where(this=usable)), "c"),
        )
    )[0]
    rows, used, center = int(totals[0]), int(totals[1]), totals[2]
    if used == 0:
        return CellMoments(0.0, [], rows, 0)
    if center is None or not math.isfinite(center):
        raise StatsInputError("The values are too large for this statistic.")
    shifted = exp.Sub(this=value.copy(), expression=num(float(center)))
    key_aliases = [alias(k.copy(), f"_k{i}") for i, k in enumerate(keys)]
    query = base.narrow(usable).select(
        *key_aliases,
        alias(count_star(), "n"),
        alias(fn("avg", shifted), "offset"),
        alias(fn("var_samp", shifted.copy()), "var"),
        alias(fn("skewness", shifted.copy()), "skew"),
        alias(fn("kurtosis", shifted.copy()), "kurt"),
        alias(fn("min", value.copy()), "lo"),
        alias(fn("max", value.copy()), "hi"),
    )
    if keys:
        query = query.group_by(*[k.copy() for k in keys])
    cells: list[Cell] = []
    for row in rel.arrow(query).to_pylist():
        var = row["var"]
        if var is not None and not math.isfinite(var):
            raise StatsInputError("The values are too large for this statistic.")
        cells.append(
            Cell(
                key=tuple(row[f"_k{i}"] for i in range(len(keys))),
                n=int(row["n"]),
                offset=float(row["offset"]),
                var=None if var is None else max(0.0, float(var)),
                skewness=_finite(row["skew"]),
                kurtosis=_finite(row["kurt"]),
                min=float(row["lo"]),
                max=float(row["hi"]),
            )
        )
    return CellMoments(float(center), cells, rows, used)


@dataclass(frozen=True)
class OneWay:
    """One-way ANOVA F from group sizes, means and variances."""

    statistic: float | None
    df1: int
    df2: int
    p_value: float | None
    ss_between: float
    ss_within: float


def one_way_f(
    ns: Sequence[int], means: Sequence[float], variances: Sequence[float | None]
) -> OneWay:
    """Classic one-way ANOVA from per-group n, mean and variance (None when n = 1)."""
    total = sum(ns)
    k = len(ns)
    grand = sum(n * m for n, m in zip(ns, means, strict=True)) / total
    ss_between = sum(n * (m - grand) ** 2 for n, m in zip(ns, means, strict=True))
    ss_within = sum((n - 1) * (v or 0.0) for n, v in zip(ns, variances, strict=True))
    df1, df2 = k - 1, total - k
    statistic = p_value = None
    if df1 > 0 and df2 > 0 and ss_within > 0:
        statistic = (ss_between / df1) / (ss_within / df2)
        p_value = float(sps.f.sf(statistic, df1, df2))
    elif df1 > 0 and df2 > 0 and ss_between > 0:
        statistic, p_value = math.inf, 0.0
    return OneWay(statistic, df1, df2, p_value, ss_between, ss_within)


@dataclass(frozen=True)
class Levene:
    statistic: float | None
    df1: int
    df2: int
    p_value: float | None


def levene(
    rel: Relation,
    value: exp.Expr,
    keys: Sequence[exp.Expr],
    *,
    condition: exp.Expr | None = None,
) -> Levene | None:
    """Brown-Forsythe test for equal variances across the cells of `keys` (exact, SQL).

    None when there are fewer than 2 cells. Statistic None when every deviation is 0.
    """
    base = rel if condition is None else rel.narrow(condition)
    usable = _not_null(value, *keys)
    names = [f"_k{i}" for i in range(len(keys))]
    inner = base.narrow(usable).select(
        *[alias(k.copy(), n) for k, n in zip(keys, names, strict=True)], alias(value.copy(), "_v")
    )
    key_list = ", ".join(f'"{n}"' for n in names)
    sql = (
        f"WITH _u AS ({inner.sql(dialect='duckdb')}), "
        f"_m AS (SELECT {key_list}, quantile_cont(_v, 0.5) AS _med FROM _u GROUP BY {key_list}) "
        f"SELECT count(*) AS n, avg(abs(_v - _med)) AS zbar, var_samp(abs(_v - _med)) AS zvar "
        f"FROM _u JOIN _m USING ({key_list}) GROUP BY {key_list}"
    )
    rows = rel.rows(sql)
    if len(rows) < 2:
        return None
    result = one_way_f([int(r[0]) for r in rows], [float(r[1]) for r in rows], [r[2] for r in rows])
    return Levene(result.statistic, result.df1, result.df2, result.p_value)
