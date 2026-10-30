"""Comparison & Composition plot family (Plots.md section 5).

stacked_area, percent_stacked_bar: generic grouped aggregation, the same column convention as
the basic family (`x`, optional `color`, `y0..`), registered here because they are separate
`PlotType` keys the generic aggregate path (`viz.chartdata.basic.AGGREGATE_PLOTS`, owned by
transform) does not cover.

waterfall: a running sum, drawn by the frontend as stacked bars on a transparent base.
funnel: GROUP BY + sort, the same shape as a sorted bar chart.
treemap, sunburst: a hierarchical group-by (`label0..labelN`, `size`), with the smallest leaves
rolled into "(Other)" above a cap. The frontend's `hierarchyToTree` primitive turns the flat
rows into a tree.
mosaic, marimekko: a two-way crosstab with row/column width and height shares, drawn by the
frontend as custom rectangles.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pyarrow as pa
from pydantic import Field
from sqlglot import exp

from viz.chartdata.basic import measure_label
from viz.chartdata.reduce import aggregate_query, m4_query, minmaxlttb, numeric_x, over_groups
from viz.chartdata.sql import (
    alias,
    cast,
    col,
    default_agg,
    dimension_sql,
    fn,
    lit,
    measure_sql,
    ordered,
    row_number,
    subquery,
    window,
)
from viz.contracts import AggFn, ColumnSemantic, Encoding, PlotType, Reduction
from viz.errors import InvalidSpecError
from viz.plots.base import (
    CATEGORICAL,
    QUANT,
    Channel,
    PlotParams,
    PlotRequest,
    TableOutput,
    register_plot,
)

ORDERED_SEMANTICS = frozenset({ColumnSemantic.TEMPORAL, ColumnSemantic.QUANTITATIVE})
ANY_AXIS = ORDERED_SEMANTICS | CATEGORICAL
DEFAULT_MAX_SERIES = 50
DEFAULT_MAX_LEAVES = 50
DEFAULT_MAX_CROSSTAB = 20


def _default_ys(req: PlotRequest) -> list[Encoding]:
    ys = list(req.spec.encodings.get("y", []))
    return ys if ys else [Encoding(agg=AggFn.COUNT)]


def _x_info(req: PlotRequest) -> tuple[str, Encoding, ColumnSemantic]:
    x_field = req.field("x")
    if x_field is None:
        raise InvalidSpecError("Choose a field for 'x'.")
    x_enc = req.spec.encodings["x"][0]
    return x_field, x_enc, req.relation.columns[x_field].semantic


def _first_encoding(req: PlotRequest, channel: str) -> Encoding | None:
    items = req.spec.encodings.get(channel)
    return items[0] if items else None


def _color_info(req: PlotRequest) -> tuple[str, Encoding] | None:
    color_enc = _first_encoding(req, "color")
    if color_enc is None or color_enc.field is None:
        return None
    return color_enc.field, color_enc


def _truncation_warning(limit: int) -> str:
    return f"Showing the first {limit:,} groups; narrow the data with a filter."


def _top_values(
    req: PlotRequest, dim_expr: exp.Expr, y0: Encoding, limit: int, *, dim_name: str = "color"
) -> tuple[list[Any], bool]:
    """The `limit` values of `dim_expr` with the largest total |y0|, and whether more exist."""
    grouped = aggregate_query(
        req.relation, [(dim_name, dim_expr)], [("__viz_size", fn("abs", measure_sql(y0)))]
    )
    query = (
        over_groups(grouped)
        .order_by(ordered(col("__viz_size"), desc=True), ordered(col(dim_name)))
        .limit(limit + 1)
    )
    values = req.relation.arrow(query).column(dim_name).to_pylist()
    return values[:limit], len(values) > limit


def _top_colors(
    req: PlotRequest, color_dim: exp.Expr, y0: Encoding, limit: int
) -> tuple[list[Any], bool]:
    return _top_values(req, color_dim, y0, limit, dim_name="color")


def _dim_condition(dim_expr: exp.Expr, keep: Sequence[Any]) -> exp.Expr:
    present = [lit(v) for v in keep if v is not None]
    parts: list[exp.Expr] = []
    if present:
        parts.append(exp.In(this=dim_expr.copy(), expressions=present))
    if any(v is None for v in keep):
        parts.append(exp.Is(this=dim_expr.copy(), expression=exp.Null()))
    return exp.or_(*parts) if parts else exp.false()


def _drop_helpers(table: pa.Table) -> pa.Table:
    return table.drop_columns([n for n in table.column_names if n.startswith("__viz_")])


def _truncate_whole_groups(table: pa.Table, group_col: str, cap: int) -> tuple[pa.Table, bool]:
    """`table` (already sorted by `group_col`) cut at a `group_col` boundary at or below `cap`
    rows, never slicing through the middle of a group (which would ship a bar that doesn't sum
    to what the rest of the chart implies, e.g. a 100% stack whose shares no longer add to 100).
    The first group is always kept in full even if it alone exceeds `cap`."""
    if table.num_rows <= cap:
        return table, False
    keys = table.column(group_col).to_pylist()
    cut = 0
    start = 0
    for i in range(1, len(keys) + 1):
        if i == len(keys) or keys[i] != keys[start]:
            if cut > 0 and i > cap:
                break
            cut = i
            start = i
    return table.slice(0, cut), cut < len(keys)


def _size_measure(size_enc: Encoding | None) -> exp.Expr:
    """`size` channel measure, or `count(*)` when no field is bound."""
    if size_enc is not None:
        return measure_sql(size_enc)
    return cast(exp.Count(this=exp.Star()), "DOUBLE")


def _size_label(size_enc: Encoding | None) -> str:
    if size_enc is None:
        return "Count"
    return size_enc.label or measure_label(size_enc)


class StackedAreaParams(PlotParams):
    max_series: int = Field(default=DEFAULT_MAX_SERIES, ge=1, le=500)


@register_plot(
    PlotType.STACKED_AREA,
    channels=[
        Channel("x", ANY_AXIS, min=1, max=1),
        Channel("y", QUANT, min=0, max=None),
        Channel("color", CATEGORICAL, min=0, max=1),
    ],
    params=StackedAreaParams,
)
def stacked_area(req: PlotRequest) -> TableOutput:
    """GROUP BY x[, color], SUM(y): the same shape as `area`.

    An ordered x is reduced the same way the basic family's own line/area charts are: SQL-side
    M4 (per pixel-column extremes, sized to the viewport, never pulling the full series into
    Python) followed by MinMaxLTTB. A categorical x over the cap keeps the largest x categories
    by total |y0| (not an arbitrary row prefix, which could split a stack's bars across
    categories unevenly).
    """
    params = req.params
    assert isinstance(params, StackedAreaParams)
    x_field, x_enc, x_semantic = _x_info(req)
    x_dim = dimension_sql(x_enc)
    ordered_x = x_semantic in ORDERED_SEMANTICS
    ys = _default_ys(req)
    for y in ys:
        if y.field is None and default_agg(y) != AggFn.COUNT:
            raise InvalidSpecError(f"{default_agg(y).value} needs a field")

    relation = req.relation
    warnings: list[str] = []
    params_out: dict[str, Any] = {}
    fields = {"x": x_enc.label or x_field}
    dims: list[tuple[str, exp.Expr]] = [("x", x_dim)]
    n_series = 1
    color = _color_info(req)
    if color is not None:
        color_field, color_enc = color
        color_dim = dimension_sql(color_enc)
        fields["color"] = color_enc.label or color_field
        keep, more = _top_colors(req, color_dim, ys[0], params.max_series)
        n_series = max(1, len(keep))
        if more:
            relation = relation.narrow(_dim_condition(color_dim, keep))
            params_out["truncatedSeries"] = True
            warnings.append(
                f"Showing the {params.max_series} largest of the {fields['color']} series; "
                "filter to see the rest."
            )
        dims.append(("color", color_dim))

    measures = [(f"y{i}", measure_sql(y)) for i, y in enumerate(ys)]
    for i, y in enumerate(ys):
        fields[f"y{i}"] = measure_label(y)
    grouped = aggregate_query(relation, dims, measures)
    order = [ordered(col("x"))] + ([ordered(col("color"))] if color is not None else [])

    per_measure_cap = max(1, req.mark_cap // len(ys))
    y_names = [f"y{i}" for i in range(len(ys))]
    reduction, method = Reduction.AGGREGATED, "group_by"
    if ordered_x:
        n_out = max(3, min(2 * max(1, req.viewport.width_px), per_measure_cap // n_series))
        buckets = max(1, min(req.viewport.width_px, n_out))
        m4 = m4_query(
            grouped,
            x="x",
            x_position=numeric_x(col("x"), x_semantic),
            ys=y_names,
            series="color" if color is not None else None,
            buckets=buckets,
        )
        m4_table = relation.arrow(m4)
        groups = int(m4_table.column("__viz_groups")[0].as_py() or 0) if m4_table.num_rows else 0
        if groups <= per_measure_cap:
            # Small enough after all: fetch the exact (ungrouped-by-pixel) values instead.
            table = relation.arrow(over_groups(grouped).order_by(*order))
        else:
            table, reduced = minmaxlttb(
                m4_table,
                x="x",
                ys=y_names,
                series="color" if color is not None else None,
                n_out=n_out,
            )
            if reduced:
                reduction, method = Reduction.DOWNSAMPLED, "m4+minmaxlttb"
                params_out["nOut"] = n_out
    else:
        cap = per_measure_cap
        keep_x, more_x = _top_values(req, x_dim, ys[0], cap, dim_name="x")
        if more_x:
            relation = relation.narrow(_dim_condition(x_dim, keep_x))
            grouped = aggregate_query(relation, dims, measures)
            params_out["truncatedGroups"] = True
            warnings.append(_truncation_warning(cap))
        table = relation.arrow(over_groups(grouped).order_by(*order))
    return TableOutput(
        table=table,
        reduction=reduction,
        method=method,
        fields=fields,
        params=params_out,
        warnings=warnings,
    )


class PercentStackedBarParams(PlotParams):
    max_series: int = Field(default=DEFAULT_MAX_SERIES, ge=1, le=500)


@register_plot(
    PlotType.PERCENT_STACKED_BAR,
    channels=[
        Channel("x", ANY_AXIS, min=1, max=1),
        Channel("y", QUANT, min=0, max=1),
        Channel("color", CATEGORICAL, min=1, max=1),
    ],
    params=PercentStackedBarParams,
)
def percent_stacked_bar(req: PlotRequest) -> TableOutput:
    """GROUP BY x, color, SUM(y), then normalize so every x's shares sum to 100.

    The frontend reuses the basic bar renderer with `stack: "stack"` (not "percent") on
    already-normalized values, so the shared client-side percent normalization never runs
    twice.
    """
    params = req.params
    assert isinstance(params, PercentStackedBarParams)
    x_field, x_enc, _ = _x_info(req)
    x_dim = dimension_sql(x_enc)
    ys = _default_ys(req)
    y0 = ys[0]
    if y0.field is None and default_agg(y0) != AggFn.COUNT:
        raise InvalidSpecError(f"{default_agg(y0).value} needs a field")
    color_field, color_enc = _color_info(req) or (None, None)
    if color_field is None or color_enc is None:
        raise InvalidSpecError("Choose a field for 'color'.")
    color_dim = dimension_sql(color_enc)

    relation = req.relation
    warnings: list[str] = []
    params_out: dict[str, Any] = {}
    keep, more = _top_colors(req, color_dim, y0, params.max_series)
    if more:
        relation = relation.narrow(_dim_condition(color_dim, keep))
        params_out["truncatedSeries"] = True
        warnings.append(
            f"Showing the {params.max_series} largest "
            f"{color_enc.label or color_field} series; filter to see the rest."
        )

    grouped = aggregate_query(
        relation, [("x", x_dim), ("color", color_dim)], [("__viz_v", measure_sql(y0))]
    )
    per_x_total = window(fn("sum", col("__viz_v")), partition_by=[col("x")])
    share = exp.Case(
        ifs=[
            exp.If(
                this=exp.NEQ(this=per_x_total, expression=lit(0.0)),
                true=cast(
                    exp.Mul(
                        this=exp.Div(this=col("__viz_v"), expression=per_x_total.copy()),
                        expression=lit(100.0),
                    ),
                    "DOUBLE",
                ),
            )
        ],
        default=lit(0.0),
    )
    final = (
        exp.select(col("x"), col("color"), alias(share, "y0"))
        .from_(subquery(grouped, "__viz_g"))
        .order_by(ordered(col("x")), ordered(col("color")))
    )
    table = relation.arrow(final)
    cap = max(1, req.mark_cap)
    # Cut at a whole x boundary: a row-count slice could split one x's colors across the cut,
    # shipping a partial bar whose shares no longer sum to 100.
    table, truncated = _truncate_whole_groups(table, "x", cap)
    if truncated:
        params_out["truncatedGroups"] = True
        warnings.append(_truncation_warning(cap))
    fields = {
        "x": x_enc.label or x_field,
        "color": color_enc.label or color_field,
        "y0": f"Share of {measure_label(y0)}",
    }
    return TableOutput(
        table=table,
        reduction=Reduction.AGGREGATED,
        method="group_by+normalize",
        fields=fields,
        params=params_out,
        warnings=warnings,
    )


class WaterfallParams(PlotParams):
    # When no 'kind' encoding is bound, treat the first/last category as a running total.
    # last_is_total defaults to False: a trailing bar reset to its own value (rather than
    # continuing the running sum) misrepresents a plain delta sequence as already totalling to
    # its last value. Use `append_total` for a genuinely computed grand-total bar instead.
    first_is_total: bool = True
    last_is_total: bool = False
    # Add one more bar after the data: the true running total (not a reset), independent of
    # last_is_total/kind.
    append_total: bool = False


@register_plot(
    PlotType.WATERFALL,
    channels=[
        Channel("x", CATEGORICAL, min=1, max=1),
        Channel("y", QUANT, min=1, max=1),
        Channel("order", QUANT, min=0, max=1),
        Channel("kind", CATEGORICAL, min=0, max=1),
    ],
    params=WaterfallParams,
)
def waterfall(req: PlotRequest) -> TableOutput:
    """A running sum over `y`, in `order` (or x) order. A 'kind' field equal to "total"
    (case-insensitive) resets the running sum to that row's own value; otherwise the first
    and/or last category can be forced to be a total via params."""
    params = req.params
    assert isinstance(params, WaterfallParams)
    x_field, x_enc, _ = _x_info(req)
    y_field = req.field("y")
    assert y_field is not None
    order_field = req.field("order")
    kind_field = req.field("kind")

    dims: list[tuple[str, exp.Expr]] = [("x", col(x_field))]
    measures: list[tuple[str, exp.Expr]] = [("__viz_y", fn("sum", cast(col(y_field), "DOUBLE")))]
    if order_field is not None:
        measures.append(("__viz_order", fn("min", cast(col(order_field), "DOUBLE"))))
    if kind_field is not None:
        measures.append(("__viz_kind", fn("min", col(kind_field))))
    grouped = aggregate_query(req.relation, dims, measures)
    order_expr = ordered(col("__viz_order")) if order_field is not None else ordered(col("x"))
    cap = max(1, req.mark_cap)
    table = req.relation.arrow(over_groups(grouped).order_by(order_expr).limit(cap + 1))
    if table.num_rows == 0:
        raise InvalidSpecError("No rows to draw a waterfall from.")
    warnings: list[str] = []
    params_out: dict[str, Any] = {}
    if table.num_rows > cap:
        table = table.slice(0, cap)
        params_out["truncatedGroups"] = True
        warnings.append(_truncation_warning(cap))

    xs = table.column("x").to_pylist()
    raw_ys = table.column("__viz_y").to_pylist()
    n_null = sum(1 for y in raw_ys if y is None)
    if n_null:
        warnings.append(
            f"{n_null:,} categor{'y has' if n_null == 1 else 'ies have'} no value; "
            "treated as a change of 0."
        )
    ys = [0.0 if y is None else float(y) for y in raw_ys]
    kinds = table.column("__viz_kind").to_pylist() if kind_field is not None else [None] * len(xs)
    is_total = [False] * len(xs)
    for i, k in enumerate(kinds):
        if isinstance(k, str) and k.strip().lower() == "total":
            is_total[i] = True
    if kind_field is None:
        if params.first_is_total and xs:
            is_total[0] = True
        if params.last_is_total and xs:
            is_total[-1] = True

    starts: list[float] = []
    ends: list[float] = []
    running = 0.0
    for y, total in zip(ys, is_total, strict=True):
        if total:
            starts.append(0.0)
            ends.append(y)
            running = y
        else:
            starts.append(running)
            ends.append(running + y)
            running += y
    if params.append_total:
        xs.append("Total")
        starts.append(0.0)
        ends.append(running)
        is_total.append(True)
    bases = [min(s, e) for s, e in zip(starts, ends, strict=True)]
    heights = [abs(e - s) for s, e in zip(starts, ends, strict=True)]
    directions = [
        "total" if t else ("increase" if e >= s else "decrease")
        for t, s, e in zip(is_total, starts, ends, strict=True)
    ]
    out = pa.table(
        {
            "x": xs,
            "base": pa.array(bases, pa.float64()),
            "y0": pa.array(heights, pa.float64()),
            "start": pa.array(starts, pa.float64()),
            "end": pa.array(ends, pa.float64()),
            "direction": directions,
        }
    )
    fields = {
        "x": x_enc.label or x_field,
        "y0": measure_label(req.spec.encodings["y"][0]),
    }
    return TableOutput(
        table=out,
        reduction=Reduction.AGGREGATED,
        method="running_sum",
        fields=fields,
        params=params_out,
        warnings=warnings,
    )


@register_plot(
    PlotType.FUNNEL,
    channels=[
        Channel("x", CATEGORICAL, min=1, max=1),
        Channel("y", QUANT, min=0, max=1),
        Channel("order", QUANT, min=0, max=1),
    ],
)
def funnel(req: PlotRequest) -> TableOutput:
    """GROUP BY x, SUM(y); ordered by `order` when given, else by value descending."""
    x_field, x_enc, _ = _x_info(req)
    ys = _default_ys(req)
    y0 = ys[0]
    if y0.field is None and default_agg(y0) != AggFn.COUNT:
        raise InvalidSpecError(f"{default_agg(y0).value} needs a field")
    order_field = req.field("order")
    dims: list[tuple[str, exp.Expr]] = [("x", col(x_field))]
    measures: list[tuple[str, exp.Expr]] = [("y0", measure_sql(y0))]
    if order_field is not None:
        measures.append(("__viz_order", fn("min", cast(col(order_field), "DOUBLE"))))
    grouped = aggregate_query(req.relation, dims, measures)
    order_expr = (
        ordered(col("__viz_order")) if order_field is not None else ordered(col("y0"), desc=True)
    )
    cap = max(1, req.mark_cap)
    table = req.relation.arrow(over_groups(grouped).order_by(order_expr).limit(cap + 1))
    warnings: list[str] = []
    params_out: dict[str, Any] = {}
    if table.num_rows > cap:
        table = table.slice(0, cap)
        params_out["truncatedGroups"] = True
        warnings.append(_truncation_warning(cap))
    table = _drop_helpers(table)
    fields = {"x": x_enc.label or x_field, "y0": measure_label(y0)}
    return TableOutput(
        table=table,
        reduction=Reduction.AGGREGATED,
        method="group_by",
        fields=fields,
        params=params_out,
        warnings=warnings,
    )


class HierarchyParams(PlotParams):
    max_leaves: int = Field(default=DEFAULT_MAX_LEAVES, ge=1, le=2000)


def _hierarchy_stat(req: PlotRequest, max_leaves: int) -> TableOutput:
    """GROUP BY every hierarchy level, then rank and roll up the smallest leaves into "(Other)"
    entirely in SQL: a window `row_number()` over the leaf totals, a `CASE` that relabels the
    last level past the cap, and a re-aggregate. Capping in SQL (rather than pulling every leaf
    into Python first) keeps this interruptible and bounded by `max_leaves`, not by the number
    of distinct leaf paths, which can be far larger. The last level is always cast to VARCHAR,
    so a boolean or numeric last level never collides with the literal "(Other)" string even
    when nothing is rolled up.
    """
    levels = req.fields("label")
    if not levels:
        raise InvalidSpecError("Choose at least one field for 'label'.")
    size_enc = _first_encoding(req, "size")
    raw_measure = _size_measure(size_enc)
    label_cols = [f"label{i}" for i in range(len(levels))]
    last = label_cols[-1]

    dims = [(name, col(field)) for name, field in zip(label_cols, levels, strict=True)]
    leaves = aggregate_query(req.relation, dims, [("size", raw_measure)])
    stats_row = req.relation.arrow(
        exp.select(
            alias(exp.Count(this=exp.Star()), "n"),
            alias(
                exp.Count(
                    this=exp.Case(
                        ifs=[
                            exp.If(this=exp.LT(this=col("size"), expression=lit(0.0)), true=lit(1))
                        ]
                    )
                ),
                "neg",
            ),
        ).from_(subquery(leaves, "__viz_leaves"))
    ).to_pylist()[0]
    leaf_count = int(stats_row["n"] or 0)
    neg_count = int(stats_row["neg"] or 0)

    rank = row_number(order_by=[ordered(col("size"), desc=True)])
    ranked = exp.select(exp.Star(), alias(rank, "__viz_rank")).from_(
        subquery(leaves, "__viz_leaves")
    )
    relabeled_last = exp.Case(
        ifs=[
            exp.If(
                this=exp.LTE(this=col("__viz_rank"), expression=lit(max_leaves)),
                true=cast(col(last), "VARCHAR"),
            )
        ],
        default=lit("(Other)"),
    )
    relabel_select = [
        alias(relabeled_last, last) if name == last else col(name) for name in label_cols
    ] + [alias(fn("greatest", col("size"), lit(0.0)), "size")]
    relabeled = exp.select(*relabel_select).from_(subquery(ranked, "__viz_ranked"))
    final = (
        exp.select(*[col(n) for n in label_cols], alias(fn("sum", col("size")), "size"))
        .from_(subquery(relabeled, "__viz_relabeled"))
        .group_by(*[col(n) for n in label_cols])
    )
    table = req.relation.arrow(final)

    warnings: list[str] = []
    params_out: dict[str, Any] = {}
    if leaf_count > max_leaves:
        rolled = leaf_count - max_leaves
        params_out["rolledUpLeaves"] = rolled
        warnings.append(f"The {rolled} smallest leaves are grouped into '(Other)'.")
    if neg_count:
        warnings.append(
            f"{neg_count:,} negative value{'s' if neg_count != 1 else ''} clamped to 0."
        )
    fields = {f"label{i}": field for i, field in enumerate(levels)}
    fields["size"] = _size_label(size_enc)
    return TableOutput(
        table=table,
        reduction=Reduction.AGGREGATED,
        method="hierarchical_group_by",
        fields=fields,
        params=params_out,
        warnings=warnings,
    )


@register_plot(
    PlotType.TREEMAP,
    channels=[
        Channel("label", CATEGORICAL, min=1, max=8),
        Channel("size", QUANT, min=0, max=1),
    ],
    params=HierarchyParams,
)
def treemap(req: PlotRequest) -> TableOutput:
    params = req.params
    assert isinstance(params, HierarchyParams)
    return _hierarchy_stat(req, params.max_leaves)


@register_plot(
    PlotType.SUNBURST,
    channels=[
        Channel("label", CATEGORICAL, min=1, max=8),
        Channel("size", QUANT, min=0, max=1),
    ],
    params=HierarchyParams,
)
def sunburst(req: PlotRequest) -> TableOutput:
    params = req.params
    assert isinstance(params, HierarchyParams)
    return _hierarchy_stat(req, params.max_leaves)


class CrosstabParams(PlotParams):
    max_x: int = Field(default=DEFAULT_MAX_CROSSTAB, ge=1, le=200)
    max_color: int = Field(default=DEFAULT_MAX_CROSSTAB, ge=1, le=200)


def _crosstab_cells(
    req: PlotRequest, x_field: str, color_field: str, measure: exp.Expr
) -> exp.Select:
    return aggregate_query(
        req.relation, [("x", col(x_field)), ("color", col(color_field))], [("value", measure)]
    )


def _crosstab_stat(req: PlotRequest) -> TableOutput:
    """A GROUP BY x, color crosstab with row/column shares, entirely in SQL: `dense_rank()`
    window functions (over each side's own total, so no join is needed) relabel the smallest x
    categories and color segments past their caps to "(Other)" (VARCHAR, so a non-text column
    never collides with it), then the shares are themselves window functions over the
    re-aggregated cells. As with the hierarchy rollup, this keeps the cap bounded by
    `max_x`/`max_color` rather than by the number of distinct (x, color) pairs."""
    params = req.params
    assert isinstance(params, CrosstabParams)
    x_field, x_enc, _ = _x_info(req)
    color_field, color_enc = _color_info(req) or (None, None)
    if color_field is None or color_enc is None:
        raise InvalidSpecError("Choose a field for 'color'.")
    size_enc = _first_encoding(req, "size")
    measure = _size_measure(size_enc)

    stats_row = req.relation.arrow(
        exp.select(
            alias(fn("count", exp.Distinct(expressions=[col("x")])), "nx"),
            alias(fn("count", exp.Distinct(expressions=[col("color")])), "nc"),
        ).from_(subquery(_crosstab_cells(req, x_field, color_field, measure), "__viz_cells"))
    ).to_pylist()[0]
    n_x, n_color = int(stats_row["nx"] or 0), int(stats_row["nc"] or 0)

    cells = _crosstab_cells(req, x_field, color_field, measure)
    scored = exp.select(
        exp.Star(),
        alias(window(fn("sum", col("value")), partition_by=[col("x")]), "__viz_xt"),
        alias(window(fn("sum", col("value")), partition_by=[col("color")]), "__viz_ct"),
    ).from_(subquery(cells, "__viz_cells"))
    ranked = exp.select(
        exp.Star(),
        alias(window(fn("dense_rank"), order_by=[ordered(col("__viz_xt"), desc=True)]), "__viz_xr"),
        alias(window(fn("dense_rank"), order_by=[ordered(col("__viz_ct"), desc=True)]), "__viz_cr"),
    ).from_(subquery(scored, "__viz_scored"))
    x_relabel = exp.Case(
        ifs=[
            exp.If(
                this=exp.LTE(this=col("__viz_xr"), expression=lit(params.max_x)),
                true=cast(col("x"), "VARCHAR"),
            )
        ],
        default=lit("(Other)"),
    )
    color_relabel = exp.Case(
        ifs=[
            exp.If(
                this=exp.LTE(this=col("__viz_cr"), expression=lit(params.max_color)),
                true=cast(col("color"), "VARCHAR"),
            )
        ],
        default=lit("(Other)"),
    )
    relabeled = exp.select(
        alias(x_relabel, "x"), alias(color_relabel, "color"), col("value")
    ).from_(subquery(ranked, "__viz_ranked"))
    final = (
        exp.select(col("x"), col("color"), alias(fn("sum", col("value")), "size"))
        .from_(subquery(relabeled, "__viz_relabeled"))
        .group_by(col("x"), col("color"))
    )
    with_shares = (
        exp.select(
            col("x"),
            col("color"),
            col("size"),
            alias(
                exp.Div(
                    this=window(fn("sum", col("size")), partition_by=[col("x")]),
                    expression=exp.Nullif(this=window(fn("sum", col("size"))), expression=lit(0.0)),
                ),
                "xShare",
            ),
            alias(
                exp.Div(
                    this=col("size"),
                    expression=exp.Nullif(
                        this=window(fn("sum", col("size")), partition_by=[col("x")]),
                        expression=lit(0.0),
                    ),
                ),
                "share",
            ),
        )
        .from_(subquery(final, "__viz_final"))
        .order_by(ordered(col("x")), ordered(col("color")))
    )
    table = req.relation.arrow(with_shares)

    warnings: list[str] = []
    params_out: dict[str, Any] = {}
    if n_x > params.max_x or n_color > params.max_color:
        params_out["rolledUp"] = True
        warnings.append("Small categories are grouped into '(Other)'.")
    fields = {
        "x": x_enc.label or x_field,
        "color": color_enc.label or color_field,
        "size": _size_label(size_enc),
    }
    return TableOutput(
        table=table,
        reduction=Reduction.AGGREGATED,
        method="crosstab",
        fields=fields,
        params=params_out,
        warnings=warnings,
    )


@register_plot(
    PlotType.MOSAIC,
    channels=[
        Channel("x", CATEGORICAL, min=1, max=1),
        Channel("color", CATEGORICAL, min=1, max=1),
        Channel("size", QUANT, min=0, max=1),
    ],
    params=CrosstabParams,
)
def mosaic(req: PlotRequest) -> TableOutput:
    return _crosstab_stat(req)


@register_plot(
    PlotType.MARIMEKKO,
    channels=[
        Channel("x", CATEGORICAL, min=1, max=1),
        Channel("color", CATEGORICAL, min=1, max=1),
        Channel("size", QUANT, min=0, max=1),
    ],
    params=CrosstabParams,
)
def marimekko(req: PlotRequest) -> TableOutput:
    return _crosstab_stat(req)
