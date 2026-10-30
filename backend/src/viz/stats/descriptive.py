"""Descriptive statistics, exact in DuckDB SQL on the full (filtered) data.

Definitions (fixed, see results.py): std and variance use ddof=1; skewness is the adjusted
Fisher-Pearson G1 and kurtosis the bias-corrected excess G2 (DuckDB skewness/kurtosis, equal
to SciPy with bias=False and to Excel SKEW/KURT); quantiles are linear (R type 7) for numbers
and quantile_disc (an actual value) for dates. NaN and +/-inf count as missing.

One scan computes moments and quantiles for every field (GROUPING SETS returns the groups and
the overall row together); one GROUP BY per field gives exact distinct counts and modes.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from cachetools import LRUCache
from sqlglot import exp

from viz.contracts import ColumnSemantic, DescribeRequest, FieldSummary, Quantile, StatResult
from viz.stats.relation import Relation, col
from viz.stats.results import dedupe, finite_or_none, stat_result
from viz.stats.sql import alias, count_star, fn, is_boolean, is_float, lit, num_list

Kind = Literal["numeric", "temporal", "categorical", "boolean"]
GroupKey = tuple[Any, ...] | None  # None = the overall row
ModeInfo = tuple[int, int, list[Any], int]  # distinct, mode count, first modes, tied modes

_QUARTILES = (0.25, 0.5, 0.75)
_MAX_MODES = 5
_NUMERIC_PREFIXES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "REAL",
    "DECIMAL",
)
_TEMPORAL_PREFIXES = ("DATE", "TIMESTAMP", "TIME")


def field_kind(rel: Relation, name: str) -> Kind:
    column = rel.columns[name]
    upper = column.type.upper()
    if is_boolean(column):
        return "boolean"
    if upper.startswith(_TEMPORAL_PREFIXES):
        return "temporal"
    if upper.startswith(_NUMERIC_PREFIXES) and column.semantic != ColumnSemantic.NOMINAL:
        return "numeric"
    return "categorical"


def _value(rel: Relation, name: str, kind: Kind) -> exp.Expr:
    """The field's usable value: NULL when missing (NaN and +/-inf count as missing)."""
    column = col(name)
    if kind == "numeric":
        double = exp.cast(column, "DOUBLE")
        if is_float(rel.columns[name]):
            return exp.If(this=fn("isfinite", column), true=double, false=exp.Null())
        return double
    return column


def _text(expr: exp.Expr) -> exp.Expr:
    return exp.cast(expr, "VARCHAR")


def _iso(value: Any) -> Any:
    """DuckDB's text form of a date/timestamp, made ISO-8601 ("T" separator)."""
    if isinstance(value, str) and len(value) > 10 and value[10] == " ":
        return value[:10] + "T" + value[11:]
    return value


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


@dataclass
class _Warnings:
    """Collects warnings per (field, reason), so many groups give one sentence."""

    by_reason: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    plain: list[str] = field(default_factory=list)

    def add(self, field_name: str, reason: str, where: str) -> None:
        self.by_reason.setdefault((field_name, reason), []).append(where)

    def render(self) -> list[str]:
        out = list(self.plain)
        for (field_name, reason), places in self.by_reason.items():
            shown = ", ".join(places[:3])
            more = f" and {len(places) - 3} more" if len(places) > 3 else ""
            out.append(f"'{field_name}': {reason} ({shown}{more}).")
        return dedupe(out)


@dataclass(frozen=True)
class _Groups:
    by: list[str]
    temporal: list[bool]
    # The relation restricted to the kept groups (== the input when nothing was dropped).
    restricted: Relation
    # Number of groups before truncation; 0 when nothing was dropped.
    truncated_from: int

    def exprs(self) -> list[exp.Expr]:
        """Group keys as output: native values, except dates/times as text."""
        return [_text(col(b)) if t else col(b) for b, t in zip(self.by, self.temporal, strict=True)]

    def key(self, row: dict[str, Any]) -> GroupKey:
        if not self.by or row["_gid"] != 0:
            return None
        values = (row[f"_g{i}"] for i in range(len(self.by)))
        return tuple(_iso(v) if t else v for v, t in zip(values, self.temporal, strict=True))

    def label(self, key: GroupKey) -> str:
        if key is None:
            return "overall"
        parts = (f"{b}={'null' if v is None else v}" for b, v in zip(self.by, key, strict=True))
        return ", ".join(parts)


def describe(rel: Relation, request: DescribeRequest) -> StatResult:
    """Descriptive statistics for request.fields, per group and overall."""
    fields = list(dict.fromkeys(request.fields))
    by = list(dict.fromkeys(request.group_by))
    for name in (*fields, *by):
        rel.require(name)
    percentiles = sorted(dict.fromkeys(float(p) for p in request.percentiles))
    probs = list(dict.fromkeys([*_QUARTILES, *percentiles]))
    kinds = {name: field_kind(rel, name) for name in fields}
    groups = _top_groups(rel, by, request.max_groups)
    warnings = _Warnings()
    if groups.truncated_from:
        warnings.plain.append(
            f"Showing the {request.max_groups:,} largest of {groups.truncated_from:,} groups."
        )

    # (relation, group by?, grouping sets?) passes: one combined pass when possible.
    passes: list[tuple[Relation, bool, bool]] = []
    if by and request.include_overall and not groups.truncated_from:
        passes.append((rel, True, True))
    else:
        if by:
            passes.append((groups.restricted, True, False))
        if request.include_overall or not by:
            passes.append((rel, False, False))

    rows: list[dict[str, Any]] = []
    modes: dict[tuple[str, GroupKey], ModeInfo] | None = {} if request.include_mode else None
    for source, grouped, sets in passes:
        pass_groups = groups if grouped else _Groups([], [], source, 0)
        pass_rows = _aggregate(source, fields, kinds, pass_groups, probs, grouping_sets=sets)
        numeric_quantiles = _numeric_quantiles(source, fields, kinds, pass_groups, probs, sets)
        for row in pass_rows:
            for i, name in enumerate(fields):
                if kinds[name] == "numeric":
                    row[f"_{i}_q"] = numeric_quantiles.get((row["_key"], i))
        rows += pass_rows
        if modes is not None:
            for name in fields:
                if sets and name in by:
                    # GROUPING SETS ((g, g), (g)) can't tell the overall row apart: two passes.
                    mode_passes = [(pass_groups, False), (_Groups([], [], source, 0), False)]
                else:
                    mode_passes = [(pass_groups, sets)]
                for mode_groups, mode_sets in mode_passes:
                    for key, info in _mode_rows(source, name, kinds[name], mode_groups, mode_sets):
                        modes[(name, key)] = info

    rows.sort(key=lambda r: (r["_key"] is not None, _sort_key(r["_key"])))
    summaries = [
        _summary(name, kinds[name], i, row, groups, percentiles, modes, warnings)
        for i, name in enumerate(fields)
        for row in rows
    ]
    overall = [r for r in rows if r["_key"] is None]
    n = int(overall[0]["_rows"]) if overall else sum(int(r["_rows"]) for r in rows)
    if n == 0:
        warnings.plain.append("No rows match the filters.")
    return stat_result(
        {
            "summaries": [s.model_dump(by_alias=True, mode="json") for s in summaries],
            "groupBy": by,
            "percentiles": percentiles,
        },
        method="Descriptive statistics (exact)",
        n=n,
        warnings=warnings.render(),
    )


def _sort_key(key: GroupKey) -> tuple[Any, ...]:
    if key is None:
        return ()
    return tuple((v is None, "" if v is None else v) for v in key)


def _top_groups(rel: Relation, by: list[str], max_groups: int) -> _Groups:
    """Keep the `max_groups` largest groups (ties broken by key)."""
    temporal = [field_kind(rel, b) == "temporal" for b in by]
    if not by:
        return _Groups(by, temporal, rel, 0)
    texts = [alias(_text(col(b)), f"_k{i}") for i, b in enumerate(by)]
    query = rel.select(*texts, alias(count_star(), "_n")).group_by(*[col(b) for b in by])
    query = query.order_by(
        exp.Ordered(this=exp.column("_n"), desc=True),
        *[exp.Ordered(this=exp.column(f"_k{i}"), nulls_first=False) for i in range(len(by))],
    )
    found = rel.rows(query)
    if len(found) <= max_groups:
        return _Groups(by, temporal, rel, 0)
    conditions: list[exp.Expr] = []
    for key in found[:max_groups]:
        parts: list[exp.Expr] = []
        for name, value in zip(by, key[:-1], strict=True):
            if value is None:
                parts.append(exp.Is(this=col(name), expression=exp.Null()))
            else:
                parts.append(exp.EQ(this=_text(col(name)), expression=lit(str(value))))
        conditions.append(exp.and_(*parts))
    return _Groups(by, temporal, rel.narrow(exp.or_(*conditions)), len(found))


def _group_clause(groups: _Groups, extra: list[exp.Expr], grouping_sets: bool) -> list[exp.Expr]:
    cols: list[exp.Expr] = [col(b) for b in groups.by]
    if grouping_sets:
        return [
            exp.GroupingSets(
                expressions=[
                    exp.Tuple(expressions=[*cols, *extra]),
                    exp.Tuple(expressions=list(extra)),
                ]
            )
        ]
    return [*cols, *extra]


def _key_selects(groups: _Groups) -> list[exp.Expr]:
    selects: list[exp.Expr] = [alias(e, f"_g{i}") for i, e in enumerate(groups.exprs())]
    if groups.by:
        selects.append(alias(fn("grouping", *[col(b) for b in groups.by]), "_gid"))
    return selects


def _aggregate(
    rel: Relation,
    fields: Sequence[str],
    kinds: dict[str, Kind],
    groups: _Groups,
    probs: Sequence[float],
    *,
    grouping_sets: bool,
) -> list[dict[str, Any]]:
    selects = [*_key_selects(groups), alias(count_star(), "_rows")]
    for i, name in enumerate(fields):
        kind = kinds[name]
        v = _value(rel, name, kind)
        selects.append(alias(exp.Count(this=v), f"_{i}_count"))
        if kind == "numeric":
            for stat, function in (
                ("mean", "avg"),
                ("std", "stddev_samp"),
                ("variance", "var_samp"),
                ("sum", "sum"),
                ("min", "min"),
                ("max", "max"),
                ("skewness", "skewness"),
                ("kurtosis", "kurtosis"),
            ):
                selects.append(alias(fn(function, v), f"_{i}_{stat}"))
        elif kind == "temporal":
            selects.append(alias(_text(fn("min", v)), f"_{i}_min"))
            selects.append(alias(_text(fn("max", v)), f"_{i}_max"))
            discrete = fn("quantile_disc", v, num_list(probs))
            selects.append(alias(exp.cast(discrete, "VARCHAR[]"), f"_{i}_q"))
        elif kind == "boolean":
            selects.append(alias(fn("avg", exp.cast(v, "INTEGER")), f"_{i}_mean"))
    query = rel.select(*selects)
    if groups.by:
        query = query.group_by(*_group_clause(groups, [], grouping_sets))
    rows: list[dict[str, Any]] = rel.arrow(query).to_pylist()
    for row in rows:
        row["_key"] = groups.key(row)
    return rows


def _numeric_quantiles(
    rel: Relation,
    fields: Sequence[str],
    kinds: dict[str, Kind],
    groups: _Groups,
    probs: Sequence[float],
    grouping_sets: bool,
) -> dict[tuple[GroupKey, int], list[float | None]]:
    """(group key, field index) -> quantile_cont at probs, for every numeric field at once.

    DuckDB finalizes each quantile aggregate on one thread, so quantiles of many fields in
    one SELECT run one after another. Unpivoted to (field, value) and grouped by field, they
    finalize in parallel. UNPIVOT drops NULLs, which are exactly the missing values.
    """
    numeric = [i for i, name in enumerate(fields) if kinds[name] == "numeric"]
    if not numeric:
        return {}
    keys = [alias(e, f"_g{i}") for i, e in enumerate(groups.exprs())]
    values = [alias(_value(rel, fields[i], "numeric"), f"_c{i}") for i in numeric]
    inner = rel.select(*keys, *values).sql(dialect="duckdb")
    group_names = [f"_g{i}" for i in range(len(groups.by))]
    on = ", ".join(f"_c{i}" for i in numeric)
    if group_names and grouping_sets:
        group_by = f"GROUPING SETS (({', '.join(group_names)}, _f), (_f))"
    else:
        group_by = ", ".join([*group_names, "_f"])
    gid = f"grouping({', '.join(group_names)}) AS _gid, " if group_names else ""
    sql = (
        f"SELECT {''.join(g + ', ' for g in group_names)}{gid}_f, "
        f"quantile_cont(_v, {num_list(probs).sql(dialect='duckdb')}) AS _q "
        f"FROM (UNPIVOT ({inner}) ON {on} INTO NAME _f VALUE _v) AS _u GROUP BY {group_by}"
    )
    out: dict[tuple[GroupKey, int], list[float | None]] = {}
    for row in rel.arrow(sql).to_pylist():
        out[(groups.key(row), int(row["_f"][2:]))] = row["_q"]
    return out


def _mode_rows(
    rel: Relation, name: str, kind: Kind, groups: _Groups, grouping_sets: bool
) -> list[tuple[GroupKey, ModeInfo]]:
    """Exact distinct count and modes (all ties, ascending, first few) per group."""
    v = _value(rel, name, kind)
    present = rel.narrow(exp.Not(this=exp.Is(this=v, expression=exp.Null())))
    counted = present.select(*_key_selects(groups), alias(v, "_v"), alias(count_star(), "_c"))
    counted = counted.group_by(*_group_clause(groups, [v], grouping_sets))
    partition_cols = [f"_g{i}" for i in range(len(groups.by))] + (["_gid"] if groups.by else [])
    partition = ", ".join(partition_cols)
    over = f"PARTITION BY {partition}" if partition else ""
    value_out = "CAST(_v AS VARCHAR)" if kind == "temporal" else "_v"
    sql = (
        f"SELECT {''.join(c + ', ' for c in partition_cols)}"
        f"any_value(_d) AS _d, any_value(_m) AS _m, "
        f"list({value_out} ORDER BY _v) "
        f"FILTER (WHERE _c = _m AND _m > 1 AND _rk <= {_MAX_MODES}) AS _modes, "
        f"count(*) FILTER (WHERE _c = _m AND _m > 1) AS _ties "
        f"FROM (SELECT *, max(_c) OVER ({over}) AS _m, count(*) OVER ({over}) AS _d, "
        f"row_number() OVER ({over} ORDER BY _c DESC, _v) AS _rk "
        f"FROM ({counted.sql(dialect='duckdb')}) AS _a) AS _b"
        f"{' GROUP BY ' + partition if partition else ''}"
    )
    out: list[tuple[GroupKey, ModeInfo]] = []
    for row in rel.arrow(sql).to_pylist():
        if row["_d"] is None:  # no values at all (an ungrouped aggregate still returns a row)
            continue
        values = row["_modes"] or []
        if kind == "temporal":
            values = [_iso(m) for m in values]
        out.append((groups.key(row), (int(row["_d"]), int(row["_m"]), values, int(row["_ties"]))))
    return out


def _summary(
    name: str,
    kind: Kind,
    i: int,
    row: dict[str, Any],
    groups: _Groups,
    percentiles: Sequence[float],
    modes: dict[tuple[str, GroupKey], ModeInfo] | None,
    warnings: _Warnings,
) -> FieldSummary:
    key: GroupKey = row["_key"]
    where = groups.label(key)
    count = int(row[f"_{i}_count"])
    missing = int(row["_rows"]) - count
    distinct: int | None = None
    mode_count: int | None = None
    mode_values: list[Any] = []
    if modes is not None:
        distinct = 0
        if (name, key) in modes:
            distinct, mode_count, mode_values, ties = modes[(name, key)]
            if mode_count == 1:
                mode_values = []
                warnings.add(name, "no value repeats, so there is no mode", where)
            elif ties > _MAX_MODES:
                warnings.add(name, f"{ties:,} values tie for the mode; showing {_MAX_MODES}", where)
    if count == 0:
        warnings.add(name, "no values", where)

    stats: dict[str, Any] = dict.fromkeys(
        ("mean", "std", "variance", "sem", "sum", "iqr", "min", "max", "median", "q1", "q3")
    )
    skewness: float | None = None
    kurtosis: float | None = None
    by_prob: dict[float, Any] = {}
    probs = list(dict.fromkeys([*_QUARTILES, *percentiles]))
    if kind == "numeric" and count > 0:
        for stat in ("mean", "std", "variance", "sum", "min", "max"):
            stats[stat] = finite_or_none(row[f"_{i}_{stat}"])
        skewness = finite_or_none(row[f"_{i}_skewness"])
        kurtosis = finite_or_none(row[f"_{i}_kurtosis"])
        if stats["std"] is not None:
            stats["sem"] = stats["std"] / math.sqrt(count)
        quantile_values = row[f"_{i}_q"] or [None] * len(probs)
        by_prob = dict(zip(probs, (finite_or_none(q) for q in quantile_values), strict=True))
        if count < 2:
            warnings.add(name, "standard deviation and variance need at least 2 values", where)
        elif stats["std"] == 0:
            warnings.add(
                name, "all values are equal, so skewness and kurtosis are undefined", where
            )
        else:
            if count < 3:
                warnings.add(name, "skewness needs at least 3 values", where)
            if count < 4:
                warnings.add(name, "kurtosis needs at least 4 values", where)
    elif kind == "temporal" and count > 0:
        stats["min"] = _iso(row[f"_{i}_min"])
        stats["max"] = _iso(row[f"_{i}_max"])
        by_prob = dict(zip(probs, (_iso(q) for q in row[f"_{i}_q"]), strict=True))
    elif kind == "boolean" and count > 0:
        stats["mean"] = finite_or_none(row[f"_{i}_mean"])

    if by_prob:
        stats["q1"], stats["median"], stats["q3"] = (by_prob[p] for p in _QUARTILES)
        if kind == "numeric" and stats["q1"] is not None and stats["q3"] is not None:
            stats["iqr"] = stats["q3"] - stats["q1"]

    return FieldSummary(
        field=name,
        group=None if key is None else [_scalar(v) for v in key],
        kind=kind,
        count=count,
        missing=missing,
        distinct=distinct,
        skewness=skewness,
        kurtosis=kurtosis,
        quantiles=[Quantile(p=p, value=by_prob.get(p)) for p in percentiles],
        modes=[_scalar(m) for m in mode_values],
        mode_count=mode_count,
        **stats,
    )


# ---------------------------------------------------------------- cache

_cache: LRUCache[tuple[str, str], StatResult] = LRUCache(maxsize=128)
_cache_lock = threading.Lock()


def describe_cached(rel: Relation, request: DescribeRequest) -> StatResult:
    """describe() behind an LRU keyed by (version_key, request): a refresh changes the key."""
    key = (rel.version_key, request.model_dump_json())
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    result = describe(rel, request)
    with _cache_lock:
        _cache[key] = result
    return result


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
