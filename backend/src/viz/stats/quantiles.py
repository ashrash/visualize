"""Exact distribution kernels, computed in DuckDB on the full (filtered) data.

- quantiles: `linear` = DuckDB quantile_cont = NumPy "linear" = R type 7 (Excel PERCENTILE.INC);
  `inverted_cdf` = quantile_disc = NumPy "inverted_cdf" = R type 1.
- ecdf: exact steps, or exact quantiles on a probability grid when there are too many values.
- bin_edges / histogram: NumPy's bin rules and bin semantics ([start, end), last bin closed).
- qq: Q-Q points with Filliben plotting positions, as scipy.stats.probplot.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
import pyarrow as pa
from scipy import stats as sps
from sqlglot import exp

from viz.contracts import ColumnSemantic
from viz.stats.errors import StatsInputError
from viz.stats.relation import Relation, col
from viz.stats.sql import (
    alias,
    as_double,
    count_if,
    count_star,
    fn,
    is_integer,
    num,
    num_list,
    valid,
    valid_rows,
)

QUANT = frozenset({ColumnSemantic.QUANTITATIVE})
NUMERIC_OR_TIME = frozenset({ColumnSemantic.QUANTITATIVE, ColumnSemantic.TEMPORAL})

QuantileMethod = Literal["linear", "inverted_cdf"]
BinRule = Literal["auto", "fd", "sturges", "scott", "sqrt"]


def _sql(expression: exp.Expr) -> str:
    return expression.sql(dialect="duckdb")


def check_probs(probs: Sequence[float]) -> list[float]:
    values = [float(p) for p in probs]
    if not values:
        raise StatsInputError("Choose at least one probability.")
    for p in values:
        if not (0.0 <= p <= 1.0):
            raise StatsInputError(f"Probabilities must be between 0 and 1; got {p}.")
    return values


def _value_expr(rel: Relation, field_name: str) -> exp.Expr:
    if rel.columns[field_name].semantic == ColumnSemantic.QUANTITATIVE:
        return as_double(col(field_name))
    return col(field_name)


def _group_cols(by: Sequence[str]) -> list[exp.Expr]:
    return [col(b) for b in by]


def _check_by(rel: Relation, by: Sequence[str], reserved: Sequence[str]) -> None:
    for name in by:
        rel.require(name)
        if name in reserved or name.startswith("_"):
            raise StatsInputError(f"Can't group by '{name}': the name is reserved here.")


# ---------------------------------------------------------------- quantiles


def quantiles(
    rel: Relation,
    field_name: str,
    probs: Sequence[float],
    *,
    by: Sequence[str] = (),
    method: QuantileMethod = "linear",
) -> pa.Table:
    """Exact quantiles: a long table [*by, p, value]. Groups without values are left out."""
    rel.require(field_name, NUMERIC_OR_TIME)
    _check_by(rel, by, ("p", "value"))
    ps = check_probs(probs)
    function = "quantile_cont" if method == "linear" else "quantile_disc"
    target = valid_rows(rel, [field_name])
    query = target.select(
        *_group_cols(by),
        alias(fn(function, _value_expr(rel, field_name), num_list(ps)), "_q"),
    )
    if by:
        query = query.group_by(*_group_cols(by)).order_by(*_group_cols(by))
    out: dict[str, list[Any]] = {name: [] for name in [*by, "p", "value"]}
    for row in target.rows(query):
        if row[-1] is None:
            continue
        for p, value in zip(ps, row[-1], strict=True):
            for i, name in enumerate(by):
                out[name].append(row[i])
            out["p"].append(p)
            out["value"].append(value)
    return pa.table(out)


# ---------------------------------------------------------------- ECDF


@dataclass(frozen=True)
class Ecdf:
    # Long table [*by, x, p]: p = F(x). Exact steps, or points on the ECDF's step curve.
    table: pa.Table
    exact: bool
    n: int


def ecdf(rel: Relation, field_name: str, *, max_points: int = 2000, by: Sequence[str] = ()) -> Ecdf:
    """The empirical CDF per group.

    With at most `max_points` distinct values, every step is returned exactly. Otherwise the
    points are exact quantile_disc values at p = k/max_points: each lies on the ECDF's step
    curve (F(x) >= p > F(x-)).
    """
    rel.require(field_name, NUMERIC_OR_TIME)
    _check_by(rel, by, ("x", "p"))
    if max_points < 2:
        raise ValueError("max_points must be >= 2")
    target = valid_rows(rel, [field_name])
    value = _value_expr(rel, field_name)
    n, distinct = target.rows(
        target.select(count_star(), fn("approx_count_distinct", col(field_name)))
    )[0]
    names = [*by, "x", "p"]
    if n == 0:
        return Ecdf(pa.table({name: [] for name in names}), True, 0)

    groups = ", ".join(_sql(c) for c in _group_cols(by))
    partition = f"PARTITION BY {groups}" if by else ""
    group_select = f"{groups}, " if by else ""
    if distinct <= max_points:
        counted = target.select(*_group_cols(by), alias(value, "_x"), alias(count_star(), "_c"))
        counted = counted.group_by(*_group_cols(by), value)
        sql = (
            f"SELECT {group_select}_x AS x, "
            f"(sum(_c) OVER ({partition} ORDER BY _x ROWS BETWEEN UNBOUNDED PRECEDING "
            f"AND CURRENT ROW))::DOUBLE / (sum(_c) OVER ({partition}))::DOUBLE AS p "
            f"FROM ({_sql(counted)}) AS _t ORDER BY {group_select}_x"
        )
        return Ecdf(target.arrow(sql), True, int(n))

    # Order statistic of rank r is quantile_disc(k/m) for the k with ceil(k*n/m) = r; such a
    # k exists iff floor(r*m/n) > floor((r-1)*m/n), and the largest is floor(r*m/n). One
    # parallel sort per group (quantile_disc with a long list is far slower in DuckDB).
    m = int(max_points)
    ranked = target.select(*_group_cols(by), alias(value, "_x"))
    # Without groups the count is known; a count(*) OVER () would cost another full pass.
    count = f"count(*) OVER ({partition})" if by else str(int(n))
    sql = (
        f"SELECT {group_select}_x, (_rn * {m}) // _cnt AS _k FROM ("
        f"SELECT *, row_number() OVER ({partition} ORDER BY _x) AS _rn, "
        f"{count} AS _cnt FROM ({_sql(ranked)}) AS _a) AS _b "
        f"WHERE (_rn * {m}) // _cnt > ((_rn - 1) * {m}) // _cnt "
        f"ORDER BY {group_select}_x, _k"
    )
    out: dict[str, list[Any]] = {name: [] for name in names}
    previous: tuple[Any, ...] | None = None
    for row in target.arrow(sql).to_pylist():
        key = tuple(row[name] for name in by)
        p = row["_k"] / m
        if previous == (*key, row["_x"]):
            out["p"][-1] = p  # ties share one vertical step: keep the highest p
            continue
        previous = (*key, row["_x"])
        for name in by:
            out[name].append(row[name])
        out["x"].append(row["_x"])
        out["p"].append(p)
    return Ecdf(pa.table(out), False, int(n))


# ---------------------------------------------------------------- bins and histogram


@dataclass(frozen=True)
class Bins:
    edges: np.ndarray
    width: float
    rule: str
    # True when the rule asked for more than max_bins bins.
    capped: bool


def _rule_width(rule: str, n: int, ptp: float, iqr: float | None, std: float | None) -> float:
    """Bin width by NumPy's selectors (numpy.lib._histograms_impl). std is ddof=0."""
    if rule == "sqrt":
        return ptp / math.sqrt(n)
    if rule == "sturges":
        return ptp / (math.log2(n) + 1.0)
    if rule == "scott":
        if std is None:
            raise ValueError("scott needs std")
        return float((24.0 * math.sqrt(math.pi) / n) ** (1.0 / 3.0) * std)
    if rule == "fd":
        if iqr is None:
            raise ValueError("fd needs iqr")
        return 2.0 * iqr / math.cbrt(n)
    if rule == "auto":
        fd = _rule_width("fd", n, ptp, iqr, std)
        sturges = _rule_width("sturges", n, ptp, iqr, std)
        return min(fd, sturges) if fd else sturges
    raise StatsInputError(f"Unknown bin rule '{rule}'.")


_NICE_STEPS = (1.0, 2.0, 2.5, 5.0, 10.0)
_NICE_INTEGER_STEPS = (1.0, 2.0, 5.0, 10.0)


def nice_width(raw: float, *, integer: bool = False) -> float:
    """Round a width up to {1, 2, 2.5, 5} x 10^k ({1, 2, 5} x 10^k and >= 1 for integers)."""
    if integer and raw <= 1.0:
        return 1.0
    exponent = math.floor(math.log10(raw))
    base = 10.0**exponent
    steps = _NICE_INTEGER_STEPS if integer and exponent >= 0 else _NICE_STEPS
    for step in steps:
        if raw <= step * base * (1 + 1e-12):
            return step * base
    return 10.0 * base


def _round_edges(edges: np.ndarray, width: float) -> np.ndarray:
    decimals = max(0, -math.floor(math.log10(width)) + 3)
    return np.round(edges, decimals)


def bin_edges(
    n: int,
    lo: float,
    hi: float,
    *,
    iqr: float | None = None,
    std: float | None = None,
    rule: BinRule = "auto",
    bins: int | None = None,
    width: float | None = None,
    nice: bool = True,
    integer: bool = False,
    max_bins: int = 1000,
) -> Bins:
    """Equal-width bin edges covering [lo, hi].

    Precedence: `width`, then `bins`, then `rule`. With nice=False and a rule, the edges equal
    numpy.histogram_bin_edges, except that a rule giving zero width (FD or Scott on data with
    no spread) falls back to Sturges instead of one bin. With nice=True the width is rounded
    up to a nice number and the first edge snapped down to a multiple of it.
    """
    if n < 1:
        raise StatsInputError("No values to bin.")
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi < lo:
        raise StatsInputError("The bin range must be finite with min <= max.")
    constant = lo == hi
    if constant:  # NumPy widens a degenerate range by 0.5 on each side (and uses one bin).
        lo, hi = lo - 0.5, hi + 0.5
    ptp = hi - lo
    if width is not None:
        if not (width > 0 and math.isfinite(width)):
            raise StatsInputError("The bin width must be positive.")
        raw, label = float(width), "width"
    elif bins is not None:
        if bins < 1:
            raise StatsInputError("The number of bins must be at least 1.")
        raw, label = ptp / bins, "count"
    elif constant:
        raw, label = ptp, rule
    else:
        raw, label = _rule_width(rule, n, ptp, iqr, std), rule
        if raw <= 0:  # FD or Scott on data without spread
            raw = _rule_width("sturges", n, ptp, iqr, std)

    count = max(1, math.ceil(ptp / raw))
    capped = count > max_bins
    if capped:
        raw, count = ptp / max_bins, max_bins

    if not nice:
        if label == "width":
            edges = lo + raw * np.arange(count + 1)
            return Bins(edges, raw, label, capped)
        return Bins(np.linspace(lo, hi, count + 1), ptp / count, label, capped)

    step = float(width) if width is not None and not capped else nice_width(raw, integer=integer)
    start = math.floor(lo / step) * step
    count = max(1, math.ceil((hi - start) / step - 1e-9))
    edges = _round_edges(start + step * np.arange(count + 1), step)
    while edges[-1] < hi:
        edges = np.append(edges, _round_edges(np.array([edges[-1] + step]), step))
    if edges[0] > lo:
        edges = np.insert(edges, 0, _round_edges(np.array([edges[0] - step]), step))
    return Bins(edges, step, label, capped)


@dataclass(frozen=True)
class Histogram:
    # Long table [*by, binStart, binEnd, count], every bin of every group (zeros included).
    table: pa.Table
    edges: np.ndarray
    width: float
    rule: str
    # Values binned, missing (null/NaN/inf) values, and values outside `range`.
    n: int
    n_missing: int
    n_outside: int
    warnings: list[str] = field(default_factory=list)


def histogram(
    rel: Relation,
    field_name: str,
    *,
    rule: BinRule = "auto",
    bins: int | None = None,
    width: float | None = None,
    range: tuple[float, float] | None = None,
    by: Sequence[str] = (),
    nice: bool = True,
    max_bins: int = 1000,
) -> Histogram:
    """Equal-width histogram with shared edges across groups, binned exactly in SQL."""
    column = rel.require(field_name, QUANT)
    _check_by(rel, by, ("binStart", "binEnd", "count"))
    x = as_double(col(field_name))
    target = valid_rows(rel, [field_name])
    total, n_valid = rel.rows(rel.select(count_star(), count_if(valid(column))))[0]
    n_missing = int(total - n_valid)

    binned = target
    if range is not None:
        lo_r, hi_r = float(range[0]), float(range[1])
        if not (math.isfinite(lo_r) and math.isfinite(hi_r)) or hi_r <= lo_r:
            raise StatsInputError("The histogram range must be finite with min < max.")
        binned = target.narrow(exp.Between(this=x, low=num(lo_r), high=num(hi_r)))
    n, lo, hi, quartiles, std = binned.rows(
        binned.select(
            count_star(),
            fn("min", x),
            fn("max", x),
            fn("quantile_cont", x, num_list([0.25, 0.75])),
            fn("stddev_pop", x),
        )
    )[0]
    n = int(n)
    if n == 0:
        raise StatsInputError(f"'{field_name}' has no values to bin.")
    if range is not None:
        lo, hi = lo_r, hi_r
    iqr = float(quartiles[1] - quartiles[0])
    edge_set = bin_edges(
        n,
        float(lo),
        float(hi),
        iqr=iqr,
        std=float(std or 0.0),
        rule=rule,
        bins=bins,
        width=width,
        nice=nice,
        integer=is_integer(column) and range is None,
        max_bins=max_bins,
    )
    edges = edge_set.edges
    k = len(edges) - 1
    rows = binned.rows(_bin_sql(binned, x, by, edges))

    starts, ends = edges[:-1].tolist(), edges[1:].tolist()
    counts: dict[tuple[Any, ...], np.ndarray] = {}
    for row in rows:
        key = tuple(row[: len(by)])
        counts.setdefault(key, np.zeros(k, dtype=np.int64))[int(row[len(by)])] += int(row[-1])
    if not by and not counts:
        counts[()] = np.zeros(k, dtype=np.int64)
    out: dict[str, list[Any]] = {name: [] for name in [*by, "binStart", "binEnd", "count"]}
    for key, values in counts.items():
        for i, name in enumerate(by):
            out[name].extend([key[i]] * k)
        out["binStart"].extend(starts)
        out["binEnd"].extend(ends)
        out["count"].extend(values.tolist())

    warnings = []
    n_outside = int(n_valid) - n if range is not None else 0
    if n_outside:
        warnings.append(f"{n_outside:,} values outside the range were left out.")
    if edge_set.capped:
        warnings.append(f"Limited to {max_bins:,} bins.")
    table = pa.table(out)
    return Histogram(
        table=table,
        edges=edges,
        width=edge_set.width,
        rule=edge_set.rule,
        n=n,
        n_missing=n_missing,
        n_outside=n_outside,
        warnings=warnings,
    )


def _bin_sql(rel: Relation, x: exp.Expr, by: Sequence[str], edges: np.ndarray) -> str:
    """Counts per (group, bin) with NumPy's exact semantics.

    The bin index comes from floor((x - lo) * k / span), then is corrected by one against the
    actual edges, as numpy.histogram does, so values on an edge land where NumPy puts them.
    """
    k = len(edges) - 1
    lo_edge, hi_edge = float(edges[0]), float(edges[-1])
    inside = rel.narrow(exp.Between(this=x, low=num(lo_edge), high=num(hi_edge)))
    inner = _sql(inside.select(*_group_cols(by), alias(x, "_x")))
    group_select = "".join(f"{_sql(c)}, " for c in _group_cols(by))
    edge_list = _sql(num_list(edges.tolist()))
    first = (
        f"least(greatest(floor((_x - {_sql(num(lo_edge))}) * {_sql(num(k / (hi_edge - lo_edge)))}),"
        f" 0), {k - 1})::BIGINT"
    )
    return (
        f"SELECT {group_select}_b, count(*) AS _c FROM ("
        f" SELECT {group_select}_i0"
        f"  - (_x < list_extract({edge_list}, _i0 + 1))::BIGINT"
        f"  + (_x >= list_extract({edge_list}, _i0 + 2) AND _i0 < {k - 1})::BIGINT AS _b"
        f" FROM (SELECT {group_select}_x, {first} AS _i0 FROM ({inner}) AS _s) AS _t"
        f") AS _u GROUP BY ALL ORDER BY ALL"
    )


# ---------------------------------------------------------------- Q-Q


# Distributions a Q-Q plot may compare against, with their shape parameter names.
QQ_DISTRIBUTIONS: dict[str, tuple[str, ...]] = {
    "norm": (),
    "expon": (),
    "uniform": (),
    "logistic": (),
    "laplace": (),
    "lognorm": ("s",),
    "t": ("df",),
    "chi2": ("df",),
    "gamma": ("a",),
    "weibull_min": ("c",),
}


@dataclass(frozen=True)
class QQ:
    theoretical: np.ndarray
    sample: np.ndarray
    slope: float
    intercept: float
    # Correlation of the points (probplot's r); None when undefined.
    r: float | None
    n: int
    # False when the points are order statistics at evenly spaced ranks (n > max_points).
    exact: bool
    dist: str


def filliben(ranks: np.ndarray, n: int) -> np.ndarray:
    """Filliben's uniform order-statistic medians (as scipy.stats.probplot)."""
    ranks = np.asarray(ranks, dtype=float)
    last = 0.5 ** (1.0 / n)
    out = (ranks - 0.3175) / (n + 0.365)
    out = np.where(ranks == n, last, out)
    return np.where(ranks == 1, 1.0 - last, out)


def qq(
    rel: Relation,
    field_name: str,
    *,
    dist: str = "norm",
    shape: Sequence[float] = (),
    max_points: int = 2000,
    line: Literal["quartiles", "fit"] = "quartiles",
) -> QQ:
    """Q-Q points against a standard distribution (loc 0, scale 1) and a reference line.

    `line="quartiles"` passes through the sample and theoretical quartiles (R qqline);
    `line="fit"` is the least-squares line through the points (scipy.stats.probplot).
    """
    rel.require(field_name, QUANT)
    if dist not in QQ_DISTRIBUTIONS:
        raise StatsInputError(f"Unknown distribution '{dist}'.")
    if len(shape) != len(QQ_DISTRIBUTIONS[dist]):
        names = ", ".join(QQ_DISTRIBUTIONS[dist]) or "none"
        raise StatsInputError(f"'{dist}' takes these shape parameters: {names}.")
    frozen = getattr(sps, dist)(*shape)
    target = valid_rows(rel, [field_name])
    x = as_double(col(field_name))
    n = int(target.count())
    if n < 2:
        raise StatsInputError("A Q-Q plot needs at least 2 values.")

    if n <= max_points:
        sample = np.sort(
            np.asarray(target.arrow(target.select(alias(x, "x"))).column("x").to_numpy())
        )
        ranks = np.arange(1, n + 1)
        exact = True
    else:
        ranks = np.unique(np.round(np.linspace(1, n, max_points)).astype(np.int64))
        ranked = _sql(target.select(alias(x, "_x")))
        wanted = ", ".join(str(int(r)) for r in ranks)
        sql = (
            f"SELECT _x FROM (SELECT _x, row_number() OVER (ORDER BY _x) AS _rn "
            f"FROM ({ranked}) AS _a) AS _b WHERE _rn IN ({wanted}) ORDER BY _rn"
        )
        sample = np.asarray(target.arrow(sql).column("_x").to_numpy(), dtype=float)
        exact = False
    theoretical = frozen.ppf(filliben(ranks, n))

    r: float | None = None
    if np.ptp(sample) > 0:
        r = float(np.corrcoef(theoretical, sample)[0, 1])
    if line == "fit":
        slope, intercept = np.polyfit(theoretical, sample, 1)
    else:
        q1, q3 = target.rows(target.select(fn("quantile_cont", x, num_list([0.25, 0.75]))))[0][0]
        t1, t3 = frozen.ppf([0.25, 0.75])
        slope = (q3 - q1) / (t3 - t1)
        intercept = q1 - slope * t1
    return QQ(
        theoretical=np.asarray(theoretical, dtype=float),
        sample=sample,
        slope=float(slope),
        intercept=float(intercept),
        r=r,
        n=n,
        exact=exact,
        dist=dist,
    )
