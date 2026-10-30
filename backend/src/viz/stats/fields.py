"""How statistics read fields: measures as DOUBLE, group keys, and JSON-safe key values.

- Measures (`measure_expr`) are DOUBLE and NULL when missing: NaN and +/-inf count as
  missing; booleans are 0/1; dates and timestamps (where allowed) are days since 1970-01-01.
- Group keys (`key_expr`) are the native values, except dates and times, which are text
  (zoned timestamps in UTC) and come back as ISO-8601 (`key_value`).
- `match_case` maps rows to requested group values with the same semantics as filters.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Literal

from sqlglot import exp

from viz.chartdata.sql import predicate_sql
from viz.contracts import Column, ColumnSemantic, EqPredicate, Scalar
from viz.stats.errors import StatsInputError
from viz.stats.relation import Relation, col
from viz.stats.sql import alias, fn, is_boolean, is_float

Kind = Literal["numeric", "temporal", "categorical", "boolean"]

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
SECONDS_PER_DAY = 86_400.0


def field_kind(rel: Relation, name: str) -> Kind:
    """numeric (a numeric type not marked nominal), temporal, boolean or categorical."""
    column = rel.columns[name]
    upper = column.type.upper()
    if is_boolean(column):
        return "boolean"
    if upper.startswith(_TEMPORAL_PREFIXES):
        return "temporal"
    if upper.startswith(_NUMERIC_PREFIXES) and column.semantic != ColumnSemantic.NOMINAL:
        return "numeric"
    return "categorical"


def has_zone(rel: Relation, name: str) -> bool:
    upper = rel.columns[name].type.upper()
    return upper.startswith("TIMESTAMP WITH TIME ZONE") or upper == "TIMESTAMPTZ"


def _is_time_of_day(rel: Relation, name: str) -> bool:
    upper = rel.columns[name].type.upper()
    return upper == "TIME" or upper.startswith(("TIME WITH", "TIMETZ"))


def measure_expr(rel: Relation, name: str, *, temporal: bool = False) -> exp.Expr:
    """The field as DOUBLE, NULL when missing or non-finite.

    Raises StatsInputError for an unknown field or one that isn't numeric or boolean (or a
    date/timestamp, with `temporal=True`, measured in days since 1970-01-01).
    """
    rel.require(name)
    kind = field_kind(rel, name)
    column = col(name)
    if kind == "numeric":
        double = exp.cast(column, "DOUBLE")
        if is_float(rel.columns[name]):
            return exp.If(this=fn("isfinite", column), true=double, false=exp.Null())
        return double
    if kind == "boolean":
        return exp.cast(exp.cast(column, "INTEGER"), "DOUBLE")
    if kind == "temporal" and temporal and not _is_time_of_day(rel, name):
        seconds = fn("epoch", column)
        return exp.Div(this=seconds, expression=exp.Literal.number(repr(SECONDS_PER_DAY)))
    allowed = "a numeric, boolean or date" if temporal else "a numeric or boolean"
    if kind == "temporal":
        actual = "a time of day" if temporal else "a date/time field"
    else:
        actual = "categorical"
    raise StatsInputError(f"'{name}' must be {allowed} field; it is {actual}.")


def measure_note(rel: Relation, name: str) -> str | None:
    """A note on units for fields measured indirectly (dates as days)."""
    if field_kind(rel, name) == "temporal":
        return f"'{name}' is measured in days since 1970-01-01."
    return None


def key_expr(rel: Relation, name: str) -> exp.Expr:
    """A group key for output: native values, except dates and times as (UTC) text.

    NaN and +/-inf keys are NULL (missing), as for measures: DuckDB groups NaNs together
    but NaN != NaN in Python, so a NaN key would split or vanish downstream.
    """
    rel.require(name)
    column = col(name)
    if is_float(rel.columns[name]):
        return exp.If(this=fn("isfinite", column), true=column, false=exp.Null())
    if field_kind(rel, name) != "temporal":
        return column
    if has_zone(rel, name):
        return exp.cast(fn("timezone", exp.Literal.string("UTC"), column), "VARCHAR")
    return exp.cast(column, "VARCHAR")


def iso_text(value: Any, zoned: bool = False) -> Any:
    """DuckDB's text form of a date/timestamp, made ISO-8601 ("T" separator, UTC offset)."""
    if not isinstance(value, str):
        return value
    if len(value) > 10 and value[10] == " ":
        value = value[:10] + "T" + value[11:]
    return value + "+00:00" if zoned else value


def json_scalar(value: Any) -> Scalar:
    """A JSON scalar: numbers stay numbers (non-finite -> None), anything else becomes text."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else None


def key_value(rel: Relation, name: str, raw: Any) -> Scalar:
    """The JSON form of a key read through key_expr."""
    if field_kind(rel, name) == "temporal":
        return json_scalar(iso_text(raw, has_zone(rel, name)))
    return json_scalar(raw)


def label(names: Sequence[str], key: Sequence[Scalar] | None) -> str:
    if key is None:
        return "all"
    if len(names) == 1:
        return "null" if key[0] is None else str(key[0])
    return ", ".join(f"{n}={'null' if v is None else v}" for n, v in zip(names, key, strict=True))


def match_case(rel: Relation, name: str, values: Sequence[Scalar]) -> exp.Expr:
    """CASE WHEN field = values[0] THEN 0 WHEN field = values[1] THEN 1 ... END.

    Values compare as filters do (dates and timestamps as ISO text; None matches NULL).
    Rows matching none of the values give NULL.
    """
    column = rel.require(name)
    case = exp.Case()
    for index, value in enumerate(values):
        condition = predicate_sql(
            EqPredicate(op="eq", field=name, value=value), col(name), duck_type=column.type
        )
        case = case.when(condition, exp.Literal.number(index))
    return case


def derived(
    rel: Relation,
    columns: Sequence[tuple[str, exp.Expr, str, ColumnSemantic]],
    condition: exp.Expr,
) -> Relation:
    """A Relation over computed columns (name, expression, DuckDB type, semantic) of `rel`.

    Lets the column-based kernels (reservoir_sample, cap_groups) work on expressions.
    """
    query = rel.narrow(condition).select(*[alias(e.copy(), name) for name, e, _, _ in columns])
    return Relation(
        engine=rel.engine,
        source=query,
        where=None,
        columns={
            name: Column(name=name, type=t, semantic=s, null_count=0, approx_distinct=0)
            for name, _, t, s in columns
        },
        row_count=None,
        version_key=f"{rel.version_key}:derived",
        query_key=rel.query_key,
        generation=rel.generation,
    )


def check_distinct(names: Sequence[str | None], what: str) -> None:
    present = [n for n in names if n is not None]
    if len(set(present)) != len(present):
        raise StatsInputError(f"Choose different fields for {what}.")
