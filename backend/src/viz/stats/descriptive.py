"""Descriptive statistics, exact in DuckDB SQL on the full (filtered) data.

Definitions (fixed, see results.py): std and variance use ddof=1; skewness is the adjusted
Fisher-Pearson G1 and kurtosis the bias-corrected excess G2 (DuckDB skewness/kurtosis, equal
to SciPy with bias=False and to Excel SKEW/KURT); quantiles are linear (R type 7) for numbers
and quantile_disc (an actual value) for dates. NaN and +/-inf count as missing.

Numbers are summarised as DOUBLE: integers beyond 2^53 (e.g. BIGINT ids near 2^63) lose
precision, and HUGEINT/DECIMAL values are rounded to 15-17 significant digits.
Dates and times are reported as ISO-8601 text; TIMESTAMP WITH TIME ZONE values in UTC.

Queries:
- one scan computes the moments of every field (GROUPING SETS returns the groups and the
  overall row together); values near the top of the double range are rescaled by a power of
  two and the moments retried, since DuckDB's kurtosis/skewness overflow there;
- numeric quantiles come from one UNPIVOT + GROUP BY field, so they finalize in parallel;
- one GROUP BY per field gives exact distinct counts and modes (see _modes); with modes on,
  low-cardinality temporal fields take their quantiles from those value counts too
  (_quantiles_from_counts), other temporal fields from quantile_disc in the first scan.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import duckdb
from cachetools import LRUCache
from sqlglot import exp

from viz.contracts import DescribeRequest, FieldSummary, Quantile, StatResult
from viz.stats.errors import StatsInputError
from viz.stats.fields import Kind, field_kind
from viz.stats.fields import has_zone as _has_zone
from viz.stats.fields import iso_text as _iso
from viz.stats.fields import json_scalar as _scalar
from viz.stats.groups import cap_groups
from viz.stats.relation import Relation, col
from viz.stats.results import dedupe, finite_or_none, stat_result
from viz.stats.sql import alias, count_star, fn, is_float, num, num_list

__all__ = ["clear_cache", "describe", "describe_cached", "field_kind"]

GroupKey = tuple[Any, ...] | None  # None = the overall row
ModeInfo = tuple[int, int, list[Any], bool]  # distinct, mode count, first modes, more tie

_QUARTILES = (0.25, 0.5, 0.75)
_MAX_MODES = 5
# Mode candidates kept per (field, group) by the fast top-k: the values with the highest
# counts. When this many all tie for the mode, a tie-exact query picks the smallest ones.
_MODE_CANDIDATES = 64
# Temporal fields with at most this many distinct values (dataset profile) take their
# quantiles from the mode query's value counts (_quantiles_from_counts).
_COUNTED_QUANTILES_MAX_DISTINCT = 100_000
# Outside these magnitudes the moments are computed on rescaled values (DuckDB's kurtosis
# overflows from about 1e77, skewness from about 1e103; below ~1e-50 x^4 underflows).
_RESCALE_ABOVE = 1e50
_RESCALE_BELOW = 1e-50
_MOMENTS = (
    ("mean", "avg"),
    ("std", "stddev_samp"),
    ("variance", "var_samp"),
    ("sum", "sum"),
    ("min", "min"),
    ("max", "max"),
    ("skewness", "skewness"),
    ("kurtosis", "kurtosis"),
)
# How each moment scales when the values are divided by s.
_SCALE_POWER = {"mean": 1, "std": 1, "variance": 2, "sum": 1, "min": 1, "max": 1}


def _value(rel: Relation, name: str, kind: Kind) -> exp.Expr:
    """The field's usable value: NULL when missing (NaN and +/-inf count as missing).

    Zoned timestamps become naive UTC timestamps, so text output doesn't depend on the
    server's time zone (ordering is unchanged).
    """
    column = col(name)
    if kind == "numeric":
        double = exp.cast(column, "DOUBLE")
        if is_float(rel.columns[name]):
            return exp.If(this=fn("isfinite", column), true=double, false=exp.Null())
        return double
    if kind == "temporal" and _has_zone(rel, name):
        return fn("timezone", exp.Literal.string("UTC"), column)
    return column


def _text(expr: exp.Expr) -> exp.Expr:
    return exp.cast(expr, "VARCHAR")


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
    zoned: list[bool]
    # The relation restricted to the kept groups (== the input when nothing was dropped).
    restricted: Relation
    # Groups found, and how many were dropped (0 = all kept).
    n_groups: int
    dropped: int
    # What GROUP BY uses: the columns, with NaN/+-inf float keys as NULL (missing), since
    # DuckDB groups NaNs together but NaN != NaN when the rows are matched up in Python.
    keys: list[exp.Expr]

    @staticmethod
    def none(rel: Relation) -> _Groups:
        return _Groups([], [], [], rel, 1, 0, [])

    def exprs(self, rel: Relation) -> list[exp.Expr]:
        """Group keys as output: native values, except dates/times as text."""
        out: list[exp.Expr] = []
        for b, temporal, key in zip(self.by, self.temporal, self.keys, strict=True):
            out.append(_text(_value(rel, b, "temporal")) if temporal else key.copy())
        return out

    def key(self, row: dict[str, Any]) -> GroupKey:
        if not self.by or row["_gid"] != 0:
            return None
        values = [row[f"_g{i}"] for i in range(len(self.by))]
        return tuple(
            _iso(v, z) if t else v
            for v, t, z in zip(values, self.temporal, self.zoned, strict=True)
        )

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
    capped = cap_groups(rel, by, request.max_groups)
    groups = _Groups(
        by,
        [field_kind(rel, b) == "temporal" for b in by],
        [field_kind(rel, b) == "temporal" and _has_zone(rel, b) for b in by],
        capped.relation,
        capped.n_groups,
        capped.dropped,
        [_group_key(rel, b) for b in by],
    )
    warnings = _Warnings()
    if capped.warning:
        warnings.plain.append(capped.warning)

    # (relation, grouped?, grouping sets?) passes: one combined pass when possible.
    passes: list[tuple[Relation, bool, bool]] = []
    if by and request.include_overall and not groups.dropped:
        passes.append((rel, True, True))
    else:
        if by:
            passes.append((groups.restricted, True, False))
        if request.include_overall or not by:
            passes.append((rel, False, False))

    # Temporal fields whose quantiles come from their mode query's value counts.
    counted = frozenset(
        i
        for i, name in enumerate(fields)
        if request.include_mode and _quantiles_from_counts(rel, name, kinds[name])
    )
    rows: list[dict[str, Any]] = []
    modes: dict[tuple[str, GroupKey], ModeInfo] | None = {} if request.include_mode else None
    for source, grouped, sets in passes:
        pass_groups = groups if grouped else _Groups.none(source)
        pass_rows = _moments(source, fields, kinds, pass_groups, probs, sets, warnings, counted)
        quantiles = _numeric_quantiles(source, fields, kinds, pass_groups, probs, sets)
        if modes is not None:
            pass_modes, quantiles_from_counts = _modes(
                source, fields, kinds, pass_groups, sets, probs, counted
            )
            modes.update(pass_modes)
            quantiles.update(quantiles_from_counts)
        for row in pass_rows:
            for i, name in enumerate(fields):
                if kinds[name] == "numeric" or i in counted:
                    row[f"_{i}_q"] = quantiles.get((row["_key"], i))
        rows += pass_rows

    rows.sort(key=lambda r: (r["_key"] is not None, _sort_key(r["_key"])))
    zoned = {name for name in fields if kinds[name] == "temporal" and _has_zone(rel, name)}
    summaries = [
        _summary(name, kinds[name], i, row, groups, percentiles, modes, warnings, name in zoned)
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


def _group_key(rel: Relation, name: str) -> exp.Expr:
    if is_float(rel.columns[name]):
        return exp.If(this=fn("isfinite", col(name)), true=col(name), false=exp.Null())
    return col(name)


def _group_clause(groups: _Groups, extra: list[exp.Expr], grouping_sets: bool) -> list[exp.Expr]:
    cols: list[exp.Expr] = [k.copy() for k in groups.keys]
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


def _key_selects(rel: Relation, groups: _Groups) -> list[exp.Expr]:
    selects: list[exp.Expr] = [alias(e, f"_g{i}") for i, e in enumerate(groups.exprs(rel))]
    if groups.by:
        selects.append(alias(fn("grouping", *[k.copy() for k in groups.keys]), "_gid"))
    return selects


def _moments(
    rel: Relation,
    fields: Sequence[str],
    kinds: dict[str, Kind],
    groups: _Groups,
    probs: Sequence[float],
    grouping_sets: bool,
    warnings: _Warnings,
    counted: frozenset[int] = frozenset(),
) -> list[dict[str, Any]]:
    """Moments per group, recomputed on rescaled values where magnitudes are extreme.

    DuckDB's kurtosis/skewness overflow above ~1e77 (raising an error) and lose precision
    below ~1e-50 (x^4 underflows). Fields with any group outside [1e-50, 1e50] get their
    moments recomputed with each group divided by its own power of two near max|x|: exact,
    and safe however far apart the groups' magnitudes are.

    Temporal fields in `counted` get their quantiles elsewhere (_quantiles_from_counts).
    """
    numeric = [i for i, name in enumerate(fields) if kinds[name] == "numeric"]
    try:
        rows = _aggregate(rel, fields, kinds, groups, probs, grouping_sets, frozenset(), counted)
        extreme = [i for i in numeric if any(_extreme(row[f"_{i}_absmax"]) for row in rows)]
    except duckdb.OutOfRangeException:
        extreme = numeric
        rows = _aggregate(
            rel, fields, kinds, groups, probs, grouping_sets, frozenset(extreme), counted
        )
    if not extreme:
        return rows
    # A grouping-sets pass needs the groups and the overall row rescaled separately.
    passes = [groups, _Groups.none(rel)] if grouping_sets and groups.by else [groups]
    scaled: dict[GroupKey, dict[str, Any]] = {}
    try:
        for pass_groups in passes:
            scaled.update(_scaled_moments(rel, fields, extreme, pass_groups))
    except duckdb.OutOfRangeException as exc:
        raise StatsInputError("The values are too large to summarise.") from exc
    for row in rows:
        values = scaled.get(row["_key"], {})
        for i in extreme:
            for stat, _ in _MOMENTS:
                row[f"_{i}_{stat}"] = values.get(f"_{i}_{stat}")
    for i in extreme:  # after an overflow every field is recomputed; name only the extreme
        if any(_extreme(row[f"_{i}_absmax"]) for row in rows):
            warnings.plain.append(
                f"'{fields[i]}': moments were computed on rescaled values "
                "(magnitudes beyond 1e+/-50)."
            )
    return rows


def _extreme(biggest: float | None) -> bool:
    if biggest is None or not biggest > 0:
        return False
    return bool(biggest > _RESCALE_ABOVE or biggest < _RESCALE_BELOW)


def _scaled_moments(
    rel: Relation, fields: Sequence[str], indices: Sequence[int], groups: _Groups
) -> dict[GroupKey, dict[str, Any]]:
    """Moments of x / s, s = 2^floor(log2(max|x|)) within each group, scaled back by s."""
    keys = [alias(e, f"_g{k}") for k, e in enumerate(groups.exprs(rel))]
    values = [alias(_value(rel, fields[i], "numeric"), f"_v{i}") for i in indices]
    inner = rel.select(*keys, *values).sql(dialect="duckdb")
    key_names = [f"_g{k}" for k in range(len(groups.by))]
    over = f"PARTITION BY {', '.join(key_names)}" if key_names else ""
    peaks = ", ".join(f"max(abs(_v{i})) OVER ({over}) AS _m{i}" for i in indices)
    powers = ", ".join(
        f"CASE WHEN _m{i} > 0 THEN pow(2.0, floor(log2(_m{i}))) ELSE 1.0 END AS _s{i}"
        for i in indices
    )
    aggregates: list[str] = []
    for i in indices:
        aggregates += [f"{function}(_v{i} / _s{i}) AS _{i}_{stat}" for stat, function in _MOMENTS]
        aggregates.append(f"any_value(_s{i}) AS _{i}_scale")
    gid = ["0 AS _gid"] if key_names else []
    selects = ", ".join([*key_names, *gid, *aggregates])
    group_by = f" GROUP BY {', '.join(key_names)}" if key_names else ""
    sql = (
        f"SELECT {selects} FROM (SELECT *, {powers} FROM (SELECT *, {peaks} FROM ({inner}) "
        f"AS _a) AS _b) AS _c{group_by}"
    )
    out: dict[GroupKey, dict[str, Any]] = {}
    for row in rel.arrow(sql).to_pylist():
        for i in indices:
            scale = row[f"_{i}_scale"]
            for stat, power in _SCALE_POWER.items():
                value = row[f"_{i}_{stat}"]
                if value is not None and scale is not None:
                    for _ in range(power):
                        value *= scale
                    row[f"_{i}_{stat}"] = value
        out[groups.key(row)] = row
    return out


def _aggregate(
    rel: Relation,
    fields: Sequence[str],
    kinds: dict[str, Kind],
    groups: _Groups,
    probs: Sequence[float],
    grouping_sets: bool,
    skip_moments: frozenset[int],
    counted: frozenset[int],
) -> list[dict[str, Any]]:
    selects = [*_key_selects(rel, groups), alias(count_star(), "_rows")]
    for i, name in enumerate(fields):
        kind = kinds[name]
        v = _value(rel, name, kind)
        selects.append(alias(exp.Count(this=v), f"_{i}_count"))
        if kind == "numeric":
            # min and max never overflow, so they're computed even when moments are skipped;
            # max|x| = max(|min|, |max|) decides whether the moments need rescaling.
            for stat, function in _MOMENTS:
                if i not in skip_moments or stat in ("min", "max"):
                    selects.append(alias(fn(function, v), f"_{i}_{stat}"))
        elif kind == "temporal":
            selects.append(alias(_text(fn("min", v)), f"_{i}_min"))
            selects.append(alias(_text(fn("max", v)), f"_{i}_max"))
            if i not in counted:
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
        for i, name in enumerate(fields):
            if kinds[name] == "numeric":
                low, high = row[f"_{i}_min"], row[f"_{i}_max"]
                row[f"_{i}_absmax"] = None if low is None else max(abs(low), abs(high))
    return rows


def _numeric_quantiles(
    rel: Relation,
    fields: Sequence[str],
    kinds: dict[str, Kind],
    groups: _Groups,
    probs: Sequence[float],
    grouping_sets: bool,
) -> dict[tuple[GroupKey, int], list[Any]]:
    """(group key, field index) -> quantile_cont at probs, for every numeric field at once.

    DuckDB finalizes each quantile aggregate on one thread, so quantiles of many fields in
    one SELECT run one after another. Unpivoted to (field, value) and grouped by field, they
    finalize in parallel. UNPIVOT drops NULLs, which are exactly the missing values.
    """
    numeric = [i for i, name in enumerate(fields) if kinds[name] == "numeric"]
    if not numeric:
        return {}
    keys = [alias(e, f"_g{i}") for i, e in enumerate(groups.exprs(rel))]
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
    out: dict[tuple[GroupKey, int], list[Any]] = {}
    for row in rel.arrow(sql).to_pylist():
        out[(groups.key(row), int(row["_f"][2:]))] = row["_q"]
    return out


def _quantiles_from_counts(rel: Relation, name: str, kind: Kind) -> bool:
    """Whether a temporal field's quantiles come from its mode query's value counts.

    quantile_disc finalizes each group's quantiles on one thread over every value: a serial
    tail on the moments query (at 10M rows 0.16 s ungrouped, 0.47 s grouped by 7 groups).
    With few distinct values, a running count over the sorted (value, count) pairs that the
    mode query builds anyway gives the same values for a fraction of that. With many (e.g.
    timestamps) that sort would be the expensive part, so those keep quantile_disc.
    """
    if kind != "temporal":
        return False
    column = rel.columns[name]
    if column.approx_distinct > 0:  # datasets are profiled; pipeline columns report 0
        return column.approx_distinct <= _COUNTED_QUANTILES_MAX_DISTINCT
    return column.type.upper() == "DATE"


def _modes(
    rel: Relation,
    fields: Sequence[str],
    kinds: dict[str, Kind],
    groups: _Groups,
    grouping_sets: bool,
    probs: Sequence[float] = (),
    counted: frozenset[int] = frozenset(),
) -> tuple[dict[tuple[str, GroupKey], ModeInfo], dict[tuple[GroupKey, int], list[Any]]]:
    """Exact distinct count and modes per (field, group), one query per field; plus the
    quantiles at `probs` (as text) of the temporal fields in `counted`, per (group, field).

    One query per field, not one UNION ALL: DuckDB runs the branches of a UNION ALL at the
    same time, so every field's value-count hash table is alive at once. At 10M rows that
    took 2-3x the peak memory and was slower (grouped 3.4 s vs 2.6 s, ungrouped 0.84 s vs
    0.73 s), and it is what made describe sensitive to a low memory_limit.
    """
    out: dict[tuple[str, GroupKey], ModeInfo] = {}
    quantiles: dict[tuple[GroupKey, int], list[Any]] = {}
    for i, name in enumerate(fields):
        kind = kinds[name]
        if grouping_sets and name in groups.by:
            # GROUPING SETS ((g, g), (g)) can't tell the overall row apart: two queries.
            shapes = [(groups, False, None), (_Groups.none(rel), False, groups)]
        else:
            shapes = [(groups, grouping_sets, None)]
        field_probs = tuple(probs) if i in counted else ()
        for branch_groups, sets, output_groups in shapes:
            branch = _ModeBranch(
                rel, i, name, kind, branch_groups, sets, output_groups, field_probs
            )
            rows = rel.arrow(branch.sql(exact=False)).to_pylist()
            if any(_needs_exact_ties(row) for row in rows):
                rows = rel.arrow(branch.sql(exact=True)).to_pylist()
            for row in rows:
                if row["_m"] is None:  # no values (an ungrouped aggregate still returns a row)
                    continue
                key = groups.key(row)
                out[(name, key)] = _mode_info(rel, name, kind, row)
                if field_probs:
                    quantiles[(key, i)] = row["_q"]
    return out, quantiles


def _needs_exact_ties(row: dict[str, Any]) -> bool:
    """True when every fast candidate ties for the mode.

    More values may then tie than were kept, so the smallest of them are unknown. A mode
    count of 1 shows no modes, so it never needs the exact query.
    """
    ties = row["_ties"]
    return ties is not None and ties >= _MODE_CANDIDATES and int(row["_m"]) > 1


def _mode_info(rel: Relation, name: str, kind: Kind, row: dict[str, Any]) -> ModeInfo:
    mode_count = int(row["_m"])
    tied = [t for t in row["_top"] if t["c"] == mode_count]
    values: list[Any] = []
    zoned = kind == "temporal" and _has_zone(rel, name)
    for t in tied[:_MAX_MODES]:
        if t["n"] is not None:
            values.append(t["n"])
        elif kind == "boolean":
            values.append(t["t"] == "true")
        else:
            values.append(_iso(t["t"], zoned) if kind == "temporal" else t["t"])
    return int(row["_d"]), mode_count, values, len(tied) > _MAX_MODES


@dataclass(frozen=True)
class _ModeBranch:
    """One field's distinct count and modes per group, as SQL.

    Output columns: _f (field index), the group keys (_g0.., _gid), _d (distinct values),
    _m (mode count), _top (up to _MAX_MODES + 1 of the values with count _m, smallest first,
    as {n: number, t: text, c: count}) and _ties (how many values with count _m were found;
    NULL for the tie-exact query). With `probs`, also _q: quantile_disc at probs, as text.
    """

    rel: Relation
    i: int
    name: str
    kind: Kind
    groups: _Groups
    grouping_sets: bool
    # The ungrouped query of a grouped pass still outputs the key columns (NULL, _gid = 1).
    output_groups: _Groups | None = None
    probs: tuple[float, ...] = ()

    def sql(self, *, exact: bool) -> str:
        """The fast query (`exact=False`) or the tie-exact one.

        The fast query keeps the _MODE_CANDIDATES values with the highest counts (max_by
        keyed on the count alone, which is cheap) and sorts the ones that tie for the mode.
        It is exact whenever fewer values tie; otherwise (_needs_exact_ties) run the exact
        query, which ranks every value by (count desc, value) with a struct-keyed top-k:
        the same answer, but several times slower on high-cardinality fields.
        """
        v = _value(self.rel, self.name, self.kind)
        present = self.rel.narrow(exp.Not(this=exp.Is(this=v, expression=exp.Null())))
        counted = present.select(
            *_key_selects(self.rel, self.groups), alias(v, "_v"), alias(count_star(), "_c")
        )
        counted = counted.group_by(*_group_clause(self.groups, [v], self.grouping_sets))
        counts = counted.sql(dialect="duckdb")
        n_keys = len((self.output_groups or self.groups).by)
        key_names = [*(f"_g{k}" for k in range(n_keys)), "_gid"] if n_keys else []
        if self.groups.by:
            inner_keys, group_by = key_names, f" GROUP BY {', '.join(key_names)}"
        else:
            nulls = [f"NULL AS _g{k}" for k in range(n_keys)]
            inner_keys, group_by = nulls + (["1 AS _gid"] if n_keys else []), ""
        keys = "".join(f"{k}, " for k in inner_keys)
        source, quantiles = self._quantiles(counts, key_names if self.groups.by else [])
        if exact:
            top = (
                f"min_by(struct_pack(n := {self._number('_v')}, t := {self._text('_v')}, "
                f"c := _c), struct_pack(nc := -_c, v := _v), {_MAX_MODES + 1})"
            )
            return (
                f"SELECT {self.i} AS _f, {keys}count(*) AS _d, max(_c) AS _m, {top} AS _top, "
                f"NULL::BIGINT AS _ties{quantiles} FROM ({source}) AS _a{group_by}"
            )
        candidates = (
            f"SELECT {keys}count(*) AS _d, max(_c) AS _m, "
            f"max_by(struct_pack(v := _v, c := _c), _c, {_MODE_CANDIDATES}) AS _k{quantiles} "
            f"FROM ({source}) AS _a{group_by}"
        )
        # The candidates that tie for the mode, sorted by value (natively, like ORDER BY).
        ties = "list_filter(_k, lambda _x: _x.c = _m)"
        tied_values = f"list_sort(list_transform({ties}, lambda _x: _x.v))"
        top = (
            f"list_transform(list_slice({tied_values}, 1, {_MAX_MODES + 1}), "
            f"lambda _e: struct_pack(n := {self._number('_e')}, t := {self._text('_e')}, "
            "c := _m))"
        )
        outer_keys = "".join(f"{k}, " for k in key_names)
        passed = ", _q" if self.probs else ""
        return (
            f"SELECT {self.i} AS _f, {outer_keys}_d, _m, {top} AS _top, len({ties}) AS _ties"
            f"{passed} FROM ({candidates}) AS _b"
        )

    def _quantiles(self, counts: str, partition: Sequence[str]) -> tuple[str, str]:
        """(source, select) adding _q: quantile_disc at self.probs, from the value counts.

        Each value gets its running count _cum (in value order, per group) and the group's
        total _n. The value at 0-based rank r is the smallest with _cum > r, and rank is
        DuckDB's quantile_disc index, max(1, n - floor(n - n * p)) - 1, in the same double
        arithmetic, so the values equal quantile_disc's (tests compare the two).
        """
        if not self.probs:
            return counts, ""
        over = f"PARTITION BY {', '.join(partition)} " if partition else ""
        source = (
            f"SELECT *, sum(_c) OVER ({over}ORDER BY _v ROWS UNBOUNDED PRECEDING) AS _cum, "
            f"sum(_c) OVER ({over.strip()}) AS _n FROM ({counts}) AS _w"
        )
        values = []
        for p in self.probs:
            p_sql = num(p).sql(dialect="duckdb")
            rank = f"greatest(1, _n - floor(_n::DOUBLE - _n::DOUBLE * {p_sql})) - 1"
            values.append(f"CAST(min(_v) FILTER (WHERE _cum > {rank}) AS VARCHAR)")
        return source, f", [{', '.join(values)}] AS _q"

    def _number(self, value: str) -> str:
        return f"{value}::DOUBLE" if self.kind == "numeric" else "NULL::DOUBLE"

    def _text(self, value: str) -> str:
        return "NULL::VARCHAR" if self.kind == "numeric" else f"CAST({value} AS VARCHAR)"


def _summary(
    name: str,
    kind: Kind,
    i: int,
    row: dict[str, Any],
    groups: _Groups,
    percentiles: Sequence[float],
    modes: dict[tuple[str, GroupKey], ModeInfo] | None,
    warnings: _Warnings,
    zoned: bool,
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
            distinct, mode_count, mode_values, more_tie = modes[(name, key)]
            if mode_count == 1:
                mode_values = []
                warnings.add(name, "no value repeats, so there is no mode", where)
            elif more_tie:
                warnings.add(
                    name,
                    f"more than {_MAX_MODES} values tie for the mode; showing {_MAX_MODES}",
                    where,
                )
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
            if row[f"_{i}_{stat}"] is not None and stats[stat] is None:
                warnings.add(name, f"the {stat} is too large to represent", where)
        skewness = finite_or_none(row[f"_{i}_skewness"])
        kurtosis = finite_or_none(row[f"_{i}_kurtosis"])
        if stats["std"] is not None:
            stats["sem"] = stats["std"] / math.sqrt(count)
        quantile_values = row[f"_{i}_q"] or [None] * len(probs)
        by_prob = dict(zip(probs, (finite_or_none(q) for q in quantile_values), strict=True))
        if count < 2:
            warnings.add(name, "standard deviation and variance need at least 2 values", where)
        elif stats["min"] is not None and stats["min"] == stats["max"]:
            warnings.add(
                name, "all values are equal, so skewness and kurtosis are undefined", where
            )
        elif skewness is None or kurtosis is None:
            if count < 3:
                warnings.add(name, "skewness needs at least 3 values", where)
            if count < 4:
                warnings.add(name, "kurtosis needs at least 4 values", where)
            if count >= 4:
                warnings.add(name, "skewness and kurtosis could not be computed", where)
    elif kind == "temporal" and count > 0:
        stats["min"] = _iso(row[f"_{i}_min"], zoned)
        stats["max"] = _iso(row[f"_{i}_max"], zoned)
        by_prob = dict(zip(probs, (_iso(q, zoned) for q in row[f"_{i}_q"]), strict=True))
    elif kind == "boolean" and count > 0:
        stats["mean"] = finite_or_none(row[f"_{i}_mean"])

    if by_prob:
        stats["q1"], stats["median"], stats["q3"] = (by_prob[p] for p in _QUARTILES)
        if kind == "numeric" and stats["q1"] is not None and stats["q3"] is not None:
            stats["iqr"] = finite_or_none(stats["q3"] - stats["q1"])

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
