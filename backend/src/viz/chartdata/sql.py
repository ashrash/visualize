"""SQL building blocks (SQLGlot). Never format user input into SQL strings.

Every identifier goes through `ident`/`col` (always quoted) and every value through `lit`
(SQLGlot escapes it), so user-supplied names and values can't change the query's shape.
data-core imports `col`, `lit`, `predicate_sql`, `where_sql` and `parquet_source`; keep them
stable.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from pathlib import Path

from sqlglot import exp

from viz.contracts import (
    AggFn,
    Encoding,
    EqPredicate,
    InPredicate,
    IsNullPredicate,
    Predicate,
    RangePredicate,
    Scalar,
)

DIALECT = "duckdb"


def ident(name: str) -> exp.Identifier:
    return exp.to_identifier(name, quoted=True)


def col(name: str, table: str | None = None) -> exp.Column:
    if table is None:
        return exp.column(name, quoted=True)
    return exp.Column(this=ident(name), table=ident(table))


def table(name: str) -> exp.Table:
    return exp.Table(this=ident(name))


def subquery(query: exp.Query, alias: str) -> exp.Subquery:
    return exp.Subquery(this=query, alias=exp.TableAlias(this=ident(alias)))


def alias(expression: exp.Expr, name: str) -> exp.Alias:
    return exp.Alias(this=expression, alias=ident(name))


def lit(value: Scalar) -> exp.Expr:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int):
        return exp.Literal.number(value)
    if isinstance(value, float):
        if math.isfinite(value):
            return exp.Literal.number(repr(value))
        # Literal.number(nan) renders as a bare `nan`, which DuckDB reads as a column name.
        text = "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
        return exp.Cast(this=exp.Literal.string(text), to=exp.DataType.build("DOUBLE"))
    return exp.Literal.string(value)


def fn(name: str, *args: exp.Expr) -> exp.Expr:
    """A DuckDB function call by fixed (never user-supplied) name."""
    return exp.Anonymous(this=name, expressions=list(args))


def cast(expression: exp.Expr, to: str, *, safe: bool = False) -> exp.Expr:
    node = exp.TryCast if safe else exp.Cast
    return node(this=expression, to=exp.DataType.build(to, dialect=DIALECT))


def star_except(*names: str) -> exp.Star:
    return exp.Star(except_=[col(n) for n in names]) if names else exp.Star()


def ordered(expression: exp.Expr, *, desc: bool = False, nulls_last: bool = True) -> exp.Ordered:
    # DuckDB sorts NULLs last by default in both directions; be explicit.
    return exp.Ordered(this=expression, desc=desc, nulls_first=not nulls_last)


def window(
    function: exp.Expr,
    *,
    partition_by: Sequence[exp.Expr] = (),
    order_by: Sequence[exp.Ordered] = (),
    frame: exp.WindowSpec | None = None,
) -> exp.Window:
    args: dict[str, object] = {"this": function, "over": "OVER"}
    if partition_by:
        args["partition_by"] = list(partition_by)
    if order_by:
        args["order"] = exp.Order(expressions=list(order_by))
    if frame is not None:
        args["spec"] = frame
    return exp.Window(**args)


def row_number(order_by: Sequence[exp.Ordered] = (), partition_by: Sequence[exp.Expr] = ()) -> exp.Expr:
    return window(exp.RowNumber(), partition_by=partition_by, order_by=order_by)


def with_ctes(ctes: Iterable[tuple[str, exp.Query]], final: exp.Select) -> exp.Select:
    # Copy: the same CTE bodies are reused across queries, and SQLGlot nodes keep a parent.
    items = [exp.CTE(this=q.copy(), alias=exp.TableAlias(this=ident(n))) for n, q in ctes]
    if items:
        final.set("with_", exp.With(expressions=items))
    return final


def to_sql(expression: exp.Expr) -> str:
    return expression.sql(dialect=DIALECT, comments=False)


def parquet_source(path: Path) -> exp.Expr:
    return exp.Anonymous(this="read_parquet", expressions=[exp.Literal.string(str(path))])


def predicate_sql(predicate: Predicate, column: exp.Expr | None = None) -> exp.Expr:
    """`column` overrides the field reference (e.g. a qualified or resolved column)."""
    target = column if column is not None else col(predicate.field)
    if isinstance(predicate, InPredicate):
        present = [lit(v) for v in predicate.values if v is not None]
        parts: list[exp.Expr] = []
        if present:
            parts.append(exp.In(this=target.copy(), expressions=present))
        if any(v is None for v in predicate.values):
            parts.append(exp.Is(this=target.copy(), expression=exp.Null()))
        matched = exp.or_(*parts) if parts else exp.false()
        if predicate.op == "not_in":
            # NOT IN on a NULL column gives NULL (dropped by WHERE); keep NULLs unless excluded.
            keep_nulls = not any(v is None for v in predicate.values)
            negated: exp.Expr = exp.Not(this=exp.Paren(this=matched))
            if keep_nulls:
                negated = exp.or_(
                    exp.Paren(this=negated),
                    exp.Is(this=target.copy(), expression=exp.Null()),
                )
            return negated
        return matched
    if isinstance(predicate, EqPredicate):
        if predicate.value is None:
            return exp.Is(this=target, expression=exp.Null())
        return exp.EQ(this=target, expression=lit(predicate.value))
    if isinstance(predicate, RangePredicate):
        bounds: list[exp.Expr] = []
        if predicate.min is not None:
            lower = exp.GTE if predicate.include_min else exp.GT
            bounds.append(lower(this=target.copy(), expression=_bound(predicate, predicate.min)))
        if predicate.max is not None:
            upper = exp.LTE if predicate.include_max else exp.LT
            bounds.append(upper(this=target.copy(), expression=_bound(predicate, predicate.max)))
        return exp.and_(*bounds) if bounds else exp.true()
    if isinstance(predicate, IsNullPredicate):
        is_null = exp.Is(this=target, expression=exp.Null())
        return is_null if predicate.is_null else exp.Not(this=is_null)
    raise TypeError(f"unknown predicate {predicate!r}")


def _bound(predicate: RangePredicate, value: float | str) -> exp.Expr:
    if predicate.op == "between_dates" and isinstance(value, str):
        return cast(lit(value), "TIMESTAMP")
    return lit(value)


def where_sql(predicates: Sequence[Predicate]) -> exp.Expr | None:
    if not predicates:
        return None
    return exp.and_(*(exp.Paren(this=predicate_sql(p)) for p in predicates))


_AGG_FUNCTIONS = {
    AggFn.SUM: "sum",
    AggFn.MEAN: "avg",
    AggFn.MEDIAN: "median",
    AggFn.MIN: "min",
    AggFn.MAX: "max",
    AggFn.STD: "stddev_samp",
    AggFn.VAR: "var_samp",
}


def agg_sql(agg: AggFn, target: exp.Expr | None) -> exp.Expr:
    """Aggregate `target` (None = count(*)). Natural DuckDB result type."""
    if agg == AggFn.COUNT:
        return exp.Count(this=target if target is not None else exp.Star())
    if target is None:
        raise ValueError(f"{agg.value} needs a field")
    if agg == AggFn.COUNT_DISTINCT:
        return exp.Count(this=exp.Distinct(expressions=[target]))
    return exp.Anonymous(this=_AGG_FUNCTIONS[agg], expressions=[target])


def default_agg(encoding: Encoding) -> AggFn:
    return encoding.agg or (AggFn.SUM if encoding.field else AggFn.COUNT)


def measure_sql(encoding: Encoding) -> exp.Expr:
    """Aggregate expression cast to DOUBLE so the browser gets plain numbers."""
    target = col(encoding.field) if encoding.field else None
    return exp.Cast(
        this=agg_sql(default_agg(encoding), target), to=exp.DataType.build("DOUBLE")
    )


def dimension_sql(encoding: Encoding) -> exp.Expr:
    if not encoding.field:
        raise ValueError("a dimension needs a field")
    column = col(encoding.field)
    if encoding.time_unit is None:
        return column
    unit = exp.Literal.string(encoding.time_unit.value)
    return exp.Anonymous(this="date_trunc", expressions=[unit, column])
