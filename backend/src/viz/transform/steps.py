"""One SQL builder per pipeline step op (CLAUDE.md §3 Pipeline).

Each builder receives the step's input (a CTE name plus its visible columns) and returns a
SELECT over that CTE. Every builder:
- projects columns explicitly and quotes every identifier;
- carries the hidden row-order column RN, so row order stays deterministic (ORDER BY inside a
  CTE is not guaranteed to survive); steps that change row identity reassign it;
- raises StepProblem for anything the user can fix. DuckDB bind errors are caught by the
  compiler, which runs DESCRIBE on each step's output.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlglot import exp

from viz.chartdata.sql import (
    RN,
    agg_sql,
    alias,
    cast,
    col,
    fn,
    ident,
    lit,
    ordered,
    predicate_sql,
    row_number,
    star_except,
    subquery,
    table,
    with_ctes,
)
from viz.contracts import AggFn, ColumnSemantic, SchemaField
from viz.contracts.pipeline import (
    CalcStep,
    CastStep,
    DataType,
    DedupeStep,
    DropMissingStep,
    FillMissingStep,
    FillStrategy,
    FilterStep,
    GroupAggregateStep,
    JoinStep,
    NormalizeStep,
    PivotStep,
    RenameStep,
    SelectStep,
    SortStep,
    Step,
    UnionStep,
    UnpivotStep,
)
from viz.transform.formula import FormulaError, validate_formula

if TYPE_CHECKING:
    from viz.transform.compiler import Compiler

RESERVED_PREFIX = "__viz_"
MAX_PIVOT_COLUMNS = 1000
_LEFT, _RIGHT = "__viz_l", "__viz_r"


class StepProblem(Exception):
    """A user-fixable problem with one step."""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.start = start
        self.end = end


@dataclass
class StepInput:
    compiler: Compiler
    # CTE holding the step's input, and the CTEs it depends on.
    name: str
    columns: list[SchemaField]
    ctes: list[tuple[str, exp.Query]] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.columns]

    def resolve(self, name: str) -> str:
        """The schema's spelling of `name` (DuckDB identifiers are case-insensitive)."""
        lowered = name.lower()
        for column in self.columns:
            if column.name.lower() == lowered:
                return column.name
        raise StepProblem(f"unknown field {name!r}", field=name)

    def schema(self, name: str) -> SchemaField:
        actual = self.resolve(name)
        return next(c for c in self.columns if c.name == actual)

    def source(self) -> exp.Table:
        return table(self.name)

    def query_sql(self, query: exp.Select) -> exp.Select:
        """`query` (which reads the input CTE) with the CTE chain attached."""
        return with_ctes(self.ctes, query)


def _check_name(name: str, what: str) -> str:
    if not name or not name.strip():
        raise StepProblem(f"{what} can't be empty")
    if name.lower().startswith(RESERVED_PREFIX):
        raise StepProblem(f"names starting with {RESERVED_PREFIX!r} are reserved", field=name)
    return name


def _unique(names: Sequence[str], what: str) -> None:
    seen: set[str] = set()
    for name in names:
        if name.lower() in seen:
            raise StepProblem(f"{what} {name!r} appears twice", field=name)
        seen.add(name.lower())


def _project(
    inp: StepInput,
    replace: dict[str, exp.Expr] | None = None,
    *,
    keep: Sequence[str] | None = None,
    append: Sequence[tuple[str, exp.Expr]] = (),
) -> list[exp.Expr]:
    """Explicit projection: input columns (optionally replaced or subset), extras, then RN."""
    replace = replace or {}
    names = inp.names if keep is None else list(keep)
    items: list[exp.Expr] = []
    for name in names:
        items.append(alias(replace[name], name) if name in replace else col(name))
    items.extend(alias(expr, name) for name, expr in append)
    items.append(col(RN))
    return items


def _formula(inp: StepInput, text: str) -> exp.Expr:
    try:
        return validate_formula(text, inp.names).expression
    except FormulaError as exc:
        raise StepProblem(exc.message, start=exc.start, end=exc.end) from exc


def _finite(expr: exp.Expr) -> exp.Expr:
    """DOUBLE value, with NaN/±inf treated as missing (a single NaN poisons avg)."""
    as_double = cast(expr, "DOUBLE")
    return exp.Case(ifs=[exp.If(this=fn("isfinite", as_double), true=as_double.copy())])


_SIMPLE_TYPE = re.compile(r"^[A-Z][A-Z0-9_ ]*(\(\d+(, ?\d+)?\))?$")


def _type_cast(value: exp.Expr, duck_type: str) -> exp.Expr:
    """Cast a literal to a column's type. Only simple scalar type names are rebuilt: nested
    types embed field names from the data, which SQLGlot re-renders unquoted."""
    if _SIMPLE_TYPE.match(duck_type.upper()):
        try:
            return exp.Cast(this=value, to=exp.DataType.build(duck_type, dialect="duckdb"))
        except Exception:
            return value
    return value


def _stats_join(
    inp: StepInput, aggregates: list[tuple[str, exp.Expr]]
) -> tuple[exp.Select, Callable[[str], exp.Column]]:
    """FROM input CROSS JOIN (one row of whole-table aggregates)."""
    stats = exp.select(*(alias(e, n) for n, e in aggregates)).from_(inp.source())
    query = exp.select().from_(inp.source()).join(subquery(stats, "__viz_st"), join_type="cross")
    return query, lambda n: col(n, "__viz_st")


# --- builders ------------------------------------------------------------------------------


def filter_step(step: FilterStep, inp: StepInput) -> exp.Select:
    conditions: list[exp.Expr] = []
    for predicate in step.predicates:
        target = col(inp.resolve(predicate.field))
        conditions.append(exp.Paren(this=predicate_sql(predicate, target)))
    if step.expression and step.expression.strip():
        formula = _formula(inp, step.expression)
        probe = inp.query_sql(exp.select(alias(formula.copy(), "v")).from_(inp.source()))
        kind = inp.compiler.describe(probe)[0][1]
        if kind != "BOOLEAN":
            raise StepProblem(f"the filter formula must be true or false, but it returns {kind}")
        conditions.append(exp.Paren(this=formula))
    query = exp.select(exp.Star()).from_(inp.source())
    return query.where(exp.and_(*conditions)) if conditions else query


def sort_step(step: SortStep, inp: StepInput) -> exp.Select:
    if not step.keys:
        return exp.select(exp.Star()).from_(inp.source())
    keys = [
        ordered(col(inp.resolve(k.field)), desc=k.descending, nulls_last=k.nulls_last)
        for k in step.keys
    ]
    _unique([inp.resolve(k.field) for k in step.keys], "sort field")
    new_rn = row_number([*keys, ordered(col(RN))])
    return exp.select(star_except(RN), alias(new_rn, RN)).from_(inp.source())


def select_step(step: SelectStep, inp: StepInput) -> exp.Select:
    if not step.fields:
        raise StepProblem("select at least one column")
    names = [inp.resolve(f) for f in step.fields]
    _unique(names, "column")
    return exp.select(*_project(inp, keep=names)).from_(inp.source())


def rename_step(step: RenameStep, inp: StepInput) -> exp.Select:
    mapping = {
        inp.resolve(old): _check_name(new, "a new column name") for old, new in step.mapping.items()
    }
    final = [mapping.get(name, name) for name in inp.names]
    _unique(final, "column")
    items = [
        alias(col(name), mapping[name]) if name in mapping else col(name) for name in inp.names
    ]
    return exp.select(*items, col(RN)).from_(inp.source())


_CAST_TYPES = {
    DataType.INTEGER: "BIGINT",
    DataType.FLOAT: "DOUBLE",
    DataType.STRING: "VARCHAR",
    DataType.BOOLEAN: "BOOLEAN",
    DataType.DATE: "DATE",
    DataType.TIMESTAMP: "TIMESTAMP",
}


def cast_expression(target: exp.Expr, to: DataType, fmt: str | None) -> exp.Expr:
    """TRY_CAST (invalid values become NULL), or try_strptime when a format is given."""
    if fmt:
        if to not in (DataType.DATE, DataType.TIMESTAMP):
            raise StepProblem("a format applies only to date and timestamp casts")
        parsed = fn("try_strptime", cast(target, "VARCHAR"), lit(fmt))
        return cast(parsed, "DATE") if to == DataType.DATE else parsed
    return cast(target, _CAST_TYPES[to], safe=True)


def cast_step(step: CastStep, inp: StepInput) -> exp.Select:
    replace: dict[str, exp.Expr] = {}
    for item in step.casts:
        name = inp.resolve(item.field)
        if name in replace:
            raise StepProblem(f"column {name!r} is cast twice", field=name)
        if item.format:
            inp.compiler.check_strptime_format(item.format)
        replace[name] = cast_expression(col(name), item.to, item.format)
    return exp.select(*_project(inp, replace)).from_(inp.source())


def fill_missing_step(step: FillMissingStep, inp: StepInput) -> exp.Select:
    replace: dict[str, exp.Expr] = {}
    aggregates: list[tuple[str, exp.Expr]] = []
    pending: list[tuple[str, str]] = []  # (column, stat alias) for mean/median/mode
    for i, fill in enumerate(step.fills):
        name = inp.resolve(fill.field)
        if name in replace or any(name == p[0] for p in pending):
            raise StepProblem(f"column {name!r} is filled twice", field=name)
        column = col(name)
        if fill.strategy == FillStrategy.VALUE:
            if fill.value is None:
                raise StepProblem("enter a value to fill with", field=name)
            replace[name] = exp.Coalesce(
                this=column, expressions=[_type_cast(lit(fill.value), inp.schema(name).type)]
            )
        elif fill.strategy in (FillStrategy.MEAN, FillStrategy.MEDIAN):
            if inp.schema(name).semantic != ColumnSemantic.QUANTITATIVE:
                raise StepProblem(f"{fill.strategy.value} needs a numeric column", field=name)
            fname = "avg" if fill.strategy == FillStrategy.MEAN else "median"
            aggregates.append((f"__viz_s{i}", fn(fname, _finite(column))))
            pending.append((name, f"__viz_s{i}"))
        elif fill.strategy == FillStrategy.MODE:
            aggregates.append((f"__viz_s{i}", fn("mode", column)))
            pending.append((name, f"__viz_s{i}"))
        else:
            forward = fill.strategy == FillStrategy.FORWARD
            frame = (
                exp.WindowSpec(
                    kind="ROWS", start="UNBOUNDED", start_side="PRECEDING", end="CURRENT ROW"
                )
                if forward
                else exp.WindowSpec(
                    kind="ROWS", start="CURRENT ROW", end="UNBOUNDED", end_side="FOLLOWING"
                )
            )
            value = exp.LastValue(this=column) if forward else exp.FirstValue(this=column)
            replace[name] = exp.Window(
                this=exp.IgnoreNulls(this=value),
                order=exp.Order(expressions=[ordered(col(RN))]),
                spec=frame,
                over="OVER",
            )
    if not aggregates:
        return exp.select(*_project(inp, replace)).from_(inp.source())
    query, stat = _stats_join(inp, aggregates)
    for name, stat_alias in pending:
        replace[name] = exp.Coalesce(this=col(name), expressions=[stat(stat_alias)])
    return query.select(*_project(inp, replace))


def drop_missing_step(step: DropMissingStep, inp: StepInput) -> exp.Select:
    names = inp.names if step.fields is None else [inp.resolve(f) for f in step.fields]
    query = exp.select(exp.Star()).from_(inp.source())
    if not names:
        return query
    nulls = [exp.Is(this=col(n), expression=exp.Null()) for n in names]
    missing = exp.or_(*nulls) if step.how == "any" else exp.and_(*nulls)
    return query.where(exp.Not(this=exp.Paren(this=missing)))


def dedupe_step(step: DedupeStep, inp: StepInput) -> exp.Select:
    names = inp.names if step.fields is None else [inp.resolve(f) for f in step.fields]
    _unique(names, "column")
    first = row_number([ordered(col(RN), desc=step.keep == "last")], [col(n) for n in names])
    query = exp.select(exp.Star()).from_(inp.source())
    return query.qualify(exp.EQ(this=first, expression=lit(1)))


def calc_step(step: CalcStep, inp: StepInput) -> exp.Select:
    name = _check_name(step.name, "the column name")
    expression = _formula(inp, step.expression)
    lowered = {n.lower(): n for n in inp.names}
    if name.lower() in lowered:
        existing = lowered[name.lower()]
        items = [alias(expression, name) if n == existing else col(n) for n in inp.names]
        return exp.select(*items, col(RN)).from_(inp.source())
    return exp.select(*_project(inp, append=[(name, expression)])).from_(inp.source())


def group_aggregate_step(step: GroupAggregateStep, inp: StepInput) -> exp.Select:
    by = [inp.resolve(f) for f in step.by]
    if not by and not step.aggregations:
        raise StepProblem("choose columns to group by or aggregations to compute")
    items: list[exp.Expr] = [col(n) for n in by]
    names = list(by)
    for agg in step.aggregations:
        out = _check_name(agg.as_name, "an aggregation name")
        if agg.fn != AggFn.COUNT and agg.field is None:
            raise StepProblem(f"{agg.fn.value} needs a field", field=out)
        target = col(inp.resolve(agg.field)) if agg.field else None
        items.append(alias(agg_sql(agg.fn, target), out))
        names.append(out)
    _unique(names, "output column")
    items.append(alias(row_number([ordered(col(n)) for n in by]), RN))
    query = exp.select(*items).from_(inp.source())
    return query.group_by(*(col(n) for n in by)) if by else query


def join_step(step: JoinStep, inp: StepInput) -> exp.Select:
    if not step.on:
        raise StepProblem("choose at least one pair of join keys")
    right = inp.compiler.source_body(step.right)
    right_names = {c.name.lower(): c.name for c in right.columns}
    pairs: list[tuple[str, str]] = []
    for key in step.on:
        left_name = inp.resolve(key.left)
        right_name = right_names.get(key.right.lower())
        if right_name is None:
            raise StepProblem(f"unknown field {key.right!r} in the joined data", field=key.right)
        pairs.append((left_name, right_name))
    shared = {lk: rk for lk, rk in pairs if lk.lower() == rk.lower()}
    coalesce = step.how in ("right", "full")
    items: list[exp.Expr] = []
    used: set[str] = set()
    for name in inp.names:
        value: exp.Expr = col(name, _LEFT)
        if coalesce and name in shared:
            value = exp.Coalesce(this=value, expressions=[col(shared[name], _RIGHT)])
        items.append(alias(value, name))
        used.add(name.lower())
    skip = {rk for rk in shared.values()}
    for column in right.columns:
        if column.name in skip:
            continue
        out = column.name
        if out.lower() in used:
            out = f"{column.name}{step.suffix}"
            n = 2
            while out.lower() in used:
                out = f"{column.name}{step.suffix}_{n}"
                n += 1
        _check_name(out, "a joined column name")
        items.append(alias(col(column.name, _RIGHT), out))
        used.add(out.lower())
    new_rn = row_number([ordered(col(RN, _LEFT)), ordered(col(RN, _RIGHT))])
    items.append(alias(new_rn, RN))
    condition = exp.and_(
        *(exp.EQ(this=col(lk, _LEFT), expression=col(rk, _RIGHT)) for lk, rk in pairs)
    )
    join_type = {"inner": "inner", "left": "left", "right": "right", "full": "full outer"}
    left_source = exp.Table(this=ident(inp.name), alias=exp.TableAlias(this=ident(_LEFT)))
    return (
        exp.select(*items)
        .from_(left_source)
        .join(subquery(right.body, _RIGHT), on=condition, join_type=join_type[step.how])
    )


def union_step(step: UnionStep, inp: StepInput) -> exp.Select:
    if not step.others:
        return exp.select(exp.Star()).from_(inp.source())
    branch = "__viz_b"
    others = [inp.compiler.source_body(ref) for ref in step.others]
    branches: list[exp.Select] = []
    if step.by_name:
        branches.append(exp.select(exp.Star(), alias(lit(0), branch)).from_(inp.source()))
        for i, other in enumerate(others, start=1):
            branches.append(
                exp.select(exp.Star(), alias(lit(i), branch)).from_(
                    subquery(other.body, f"__viz_u{i}")
                )
            )
    else:
        width = len(inp.columns)
        branches.append(
            exp.select(*(col(n) for n in inp.names), col(RN), alias(lit(0), branch)).from_(
                inp.source()
            )
        )
        for i, other in enumerate(others, start=1):
            if len(other.columns) != width:
                raise StepProblem(
                    f"union by position needs {width} columns in every input; "
                    f"input {i + 1} has {len(other.columns)}"
                )
            items = [alias(col(o.name), n) for o, n in zip(other.columns, inp.names, strict=True)]
            branches.append(
                exp.select(*items, col(RN), alias(lit(i), branch)).from_(
                    subquery(other.body, f"__viz_u{i}")
                )
            )
    combined: exp.Query = branches[0]
    for nxt in branches[1:]:
        combined = exp.Union(this=combined, expression=nxt, distinct=False, by_name=step.by_name)
    new_rn = row_number([ordered(col(branch)), ordered(col(RN))])
    return exp.select(star_except(RN, branch), alias(new_rn, RN)).from_(
        subquery(combined, "__viz_un")
    )


def pivot_step(step: PivotStep, inp: StepInput) -> exp.Select:
    index = [inp.resolve(f) for f in step.index]
    pivot_col = inp.resolve(step.columns)
    values = inp.resolve(step.values)
    _unique(index, "index column")
    if pivot_col in index or values in index:
        raise StepProblem("the pivot and value columns can't also be index columns")
    key = "__viz_k"
    probe = exp.select(alias(cast(col(pivot_col), "VARCHAR"), key)).from_(inp.source()).distinct()
    probe = probe.order_by(ordered(col(key))).limit(MAX_PIVOT_COLUMNS + 1)
    found = inp.compiler.distinct_values(inp.query_sql(probe))
    if len(found) > MAX_PIVOT_COLUMNS:
        raise StepProblem(
            f"{step.columns!r} has more than {MAX_PIVOT_COLUMNS} distinct values; "
            "filter or group it first",
            field=step.columns,
        )
    used = {n.lower() for n in index}
    items: list[exp.Expr] = [col(n) for n in index]
    for value in found:
        base = "null" if value is None else (value if value.strip() else "(empty)")
        out, n = base, 2
        while out.lower() in used or out.lower().startswith(RESERVED_PREFIX):
            out, n = f"{base}_{n}", n + 1
        used.add(out.lower())
        match = (
            exp.Is(this=col(key), expression=exp.Null())
            if value is None
            else exp.EQ(this=col(key), expression=lit(value))
        )
        aggregate = exp.Filter(this=agg_sql(step.fn, col(values)), expression=exp.Where(this=match))
        items.append(alias(aggregate, out))
    items.append(alias(row_number([ordered(col(n)) for n in index]), RN))
    inner = exp.select(
        *(col(n) for n in index), alias(cast(col(pivot_col), "VARCHAR"), key), col(values)
    ).from_(inp.source())
    query = exp.select(*items).from_(subquery(inner, "__viz_p"))
    return query.group_by(*(col(n) for n in index)) if index else query


def unpivot_step(step: UnpivotStep, inp: StepInput) -> exp.Select:
    ids = [inp.resolve(f) for f in step.id_fields]
    value_fields = [inp.resolve(f) for f in step.value_fields]
    if not value_fields:
        raise StepProblem("choose at least one column to unpivot")
    _unique([*ids, *value_fields], "column")
    name_out = _check_name(step.name_field, "the name column")
    value_out = _check_name(step.value_field, "the value column")
    _unique([*ids, name_out, value_out], "output column")
    types = {inp.schema(v).type for v in value_fields}
    semantics = {inp.schema(v).semantic for v in value_fields}
    values: list[exp.Expr] = [col(v) for v in value_fields]
    if len(types) > 1:
        target = "DOUBLE" if semantics == {ColumnSemantic.QUANTITATIVE} else "VARCHAR"
        values = [cast(v, target) for v in values]
    position = "__viz_pos"
    inner = exp.select(
        *(col(n) for n in ids),
        alias(fn("unnest", exp.Array(expressions=[lit(v) for v in value_fields])), name_out),
        alias(fn("unnest", exp.Array(expressions=values)), value_out),
        alias(fn("unnest", exp.Array(expressions=[lit(i) for i in range(len(values))])), position),
        col(RN),
    ).from_(inp.source())
    new_rn = row_number([ordered(col(RN)), ordered(col(position))])
    return exp.select(star_except(RN, position), alias(new_rn, RN)).from_(
        subquery(inner, "__viz_unp")
    )


def normalize_step(step: NormalizeStep, inp: StepInput) -> exp.Select:
    if not step.fields:
        return exp.select(exp.Star()).from_(inp.source())
    names = [inp.resolve(f) for f in step.fields]
    _unique(names, "column")
    aggregates: list[tuple[str, exp.Expr]] = []
    formulas: dict[str, tuple[str, str]] = {}
    for i, name in enumerate(names):
        if inp.schema(name).semantic != ColumnSemantic.QUANTITATIVE:
            raise StepProblem(f"normalize needs numeric columns; {name!r} isn't", field=name)
        value = _finite(col(name))
        a, b = f"__viz_s{i}a", f"__viz_s{i}b"
        if step.method == "zscore":
            aggregates += [(a, fn("avg", value)), (b, fn("stddev_samp", value.copy()))]
        elif step.method == "minmax":
            aggregates += [(a, fn("min", value)), (b, fn("max", value.copy()))]
        else:
            q1 = fn("quantile_cont", value.copy(), lit(0.25))
            q3 = fn("quantile_cont", value.copy(), lit(0.75))
            aggregates += [(a, fn("median", value)), (b, exp.Sub(this=q3, expression=q1))]
        formulas[name] = (a, b)
    query, stat = _stats_join(inp, aggregates)
    replace: dict[str, exp.Expr] = {}
    for name, (a, b) in formulas.items():
        centered = exp.Sub(this=_finite(col(name)), expression=stat(a))
        if step.method == "minmax":
            scale: exp.Expr = exp.Sub(this=stat(b), expression=stat(a))
        else:
            scale = stat(b)
        denominator = exp.Nullif(this=exp.Paren(this=scale), expression=lit(0))
        replace[name] = exp.Div(this=exp.Paren(this=centered), expression=denominator)
    return query.select(*_project(inp, replace))


def build_step(step: Step, inp: StepInput) -> exp.Select:
    if isinstance(step, FilterStep):
        return filter_step(step, inp)
    if isinstance(step, SortStep):
        return sort_step(step, inp)
    if isinstance(step, SelectStep):
        return select_step(step, inp)
    if isinstance(step, RenameStep):
        return rename_step(step, inp)
    if isinstance(step, CastStep):
        return cast_step(step, inp)
    if isinstance(step, FillMissingStep):
        return fill_missing_step(step, inp)
    if isinstance(step, DropMissingStep):
        return drop_missing_step(step, inp)
    if isinstance(step, DedupeStep):
        return dedupe_step(step, inp)
    if isinstance(step, CalcStep):
        return calc_step(step, inp)
    if isinstance(step, GroupAggregateStep):
        return group_aggregate_step(step, inp)
    if isinstance(step, JoinStep):
        return join_step(step, inp)
    if isinstance(step, UnionStep):
        return union_step(step, inp)
    if isinstance(step, PivotStep):
        return pivot_step(step, inp)
    if isinstance(step, UnpivotStep):
        return unpivot_step(step, inp)
    if isinstance(step, NormalizeStep):
        return normalize_step(step, inp)
    raise TypeError(f"unknown step {step!r}")
