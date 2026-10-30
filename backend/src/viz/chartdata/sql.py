"""SQL building blocks (SQLGlot). Never format user input into SQL strings."""

from __future__ import annotations

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


def col(name: str) -> exp.Column:
    return exp.column(name, quoted=True)


def lit(value: Scalar) -> exp.Expr:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int | float):
        return exp.Literal.number(value)
    return exp.Literal.string(value)


def parquet_source(path: Path) -> exp.Expr:
    return exp.Anonymous(this="read_parquet", expressions=[exp.Literal.string(str(path))])


def predicate_sql(predicate: Predicate) -> exp.Expr:
    column = col(predicate.field)
    if isinstance(predicate, InPredicate):
        present = [lit(v) for v in predicate.values if v is not None]
        parts: list[exp.Expr] = []
        if present:
            parts.append(exp.In(this=column, expressions=present))
        if any(v is None for v in predicate.values):
            parts.append(exp.Is(this=column, expression=exp.Null()))
        matched = exp.or_(*parts) if parts else exp.false()
        return exp.Not(this=exp.Paren(this=matched)) if predicate.op == "not_in" else matched
    if isinstance(predicate, EqPredicate):
        if predicate.value is None:
            return exp.Is(this=column, expression=exp.Null())
        return exp.EQ(this=column, expression=lit(predicate.value))
    if isinstance(predicate, RangePredicate):
        bounds: list[exp.Expr] = []
        if predicate.min is not None:
            lower = exp.GTE if predicate.include_min else exp.GT
            bounds.append(lower(this=column, expression=lit(predicate.min)))
        if predicate.max is not None:
            upper = exp.LTE if predicate.include_max else exp.LT
            bounds.append(upper(this=column, expression=lit(predicate.max)))
        return exp.and_(*bounds) if bounds else exp.true()
    if isinstance(predicate, IsNullPredicate):
        is_null = exp.Is(this=column, expression=exp.Null())
        return is_null if predicate.is_null else exp.Not(this=is_null)
    raise TypeError(f"unknown predicate {predicate!r}")


def where_sql(predicates: list[Predicate]) -> exp.Expr | None:
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


def measure_sql(encoding: Encoding) -> exp.Expr:
    """Aggregate expression cast to DOUBLE so the browser gets plain numbers."""
    agg = encoding.agg or (AggFn.SUM if encoding.field else AggFn.COUNT)
    if agg == AggFn.COUNT:
        target: exp.Expr = col(encoding.field) if encoding.field else exp.Star()
        value: exp.Expr = exp.Count(this=target)
    elif agg == AggFn.COUNT_DISTINCT:
        if not encoding.field:
            raise ValueError("count_distinct needs a field")
        value = exp.Count(this=exp.Distinct(expressions=[col(encoding.field)]))
    else:
        if not encoding.field:
            raise ValueError(f"{agg} needs a field")
        value = exp.Anonymous(this=_AGG_FUNCTIONS[agg], expressions=[col(encoding.field)])
    return exp.Cast(this=value, to=exp.DataType.build("DOUBLE"))


def dimension_sql(encoding: Encoding) -> exp.Expr:
    if not encoding.field:
        raise ValueError("a dimension needs a field")
    column = col(encoding.field)
    if encoding.time_unit is None:
        return column
    unit = exp.Literal.string(encoding.time_unit.value)
    return exp.Anonymous(this="date_trunc", expressions=[unit, column])
