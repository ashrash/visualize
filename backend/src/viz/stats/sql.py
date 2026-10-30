"""Small SQLGlot builders shared by the SQL kernels. Never format user input into SQL."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlglot import exp

from viz.contracts import Column, Scalar
from viz.stats.relation import Relation, col

_FLOAT_TYPES = ("DOUBLE", "FLOAT", "REAL", "FLOAT4", "FLOAT8")
_INTEGER_TYPES = (
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
    "INT",
)


def is_float(column: Column) -> bool:
    return column.type.upper().startswith(_FLOAT_TYPES)


def is_integer(column: Column) -> bool:
    return column.type.upper() in _INTEGER_TYPES


def is_boolean(column: Column) -> bool:
    return column.type.upper() in ("BOOLEAN", "BOOL")


def fn(name: str, *args: exp.Expr) -> exp.Expr:
    """A DuckDB function call, passed through untouched by SQLGlot's dialect rewrites."""
    return exp.Anonymous(this=name, expressions=list(args))


def num(value: float | int) -> exp.Expr:
    """A numeric literal that round-trips exactly (floats are cast to DOUBLE)."""
    if isinstance(value, bool):
        raise TypeError("booleans are not numbers here")
    if isinstance(value, int):
        return exp.Literal.number(value)
    return exp.cast(exp.Literal.number(repr(float(value))), "DOUBLE")


def num_list(values: Iterable[float]) -> exp.Expr:
    literals: list[exp.Expr] = [exp.Literal.number(repr(float(v))) for v in values]
    return exp.cast(exp.Array(expressions=literals), "DOUBLE[]")


def lit(value: Scalar) -> exp.Expr:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int | float):
        return num(value)
    return exp.Literal.string(value)


def as_double(expr: exp.Expr) -> exp.Expr:
    return exp.cast(expr, "DOUBLE")


def valid(column: Column) -> exp.Expr:
    """Usable value: not NULL and, for float columns, not NaN or +/-inf."""
    not_null: exp.Expr = exp.Not(this=exp.Is(this=col(column.name), expression=exp.Null()))
    if is_float(column):
        return exp.and_(not_null, fn("isfinite", col(column.name)))
    return not_null


def all_valid(columns: Sequence[Column]) -> exp.Expr | None:
    conditions = [valid(c) for c in columns]
    return exp.and_(*conditions) if conditions else None


def count_star() -> exp.Expr:
    return exp.Count(this=exp.Star())


def count_if(condition: exp.Expr) -> exp.Expr:
    return exp.Filter(this=count_star(), expression=exp.Where(this=condition))


def alias(expr: exp.Expr, name: str) -> exp.Expr:
    return exp.alias_(expr, name, quoted=True)


def valid_rows(rel: Relation, fields: Sequence[str]) -> Relation:
    """`rel` narrowed to rows where every field has a usable value."""
    condition = all_valid([rel.columns[f] for f in fields])
    return rel if condition is None else rel.narrow(condition)
