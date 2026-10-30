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
import pyarrow.compute as pc
from pydantic import Field
from sqlglot import exp

from viz.chartdata.basic import measure_label
from viz.chartdata.reduce import aggregate_query, minmaxlttb, over_groups
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


def _top_colors(
    req: PlotRequest, color_dim: exp.Expr, y0: Encoding, limit: int
) -> tuple[list[Any], bool]:
    grouped = aggregate_query(
        req.relation, [("color", color_dim)], [("__viz_size", fn("abs", measure_sql(y0)))]
    )
    query = (
        over_groups(grouped)
        .order_by(ordered(col("__viz_size"), desc=True), ordered(col("color")))
        .limit(limit + 1)
    )
    values = req.relation.arrow(query).column("color").to_pylist()
    return values[:limit], len(values) > limit


def _color_condition(color_dim: exp.Expr, keep: Sequence[Any]) -> exp.Expr:
    present = [lit(v) for v in keep if v is not None]
    parts: list[exp.Expr] = []
    if present:
        parts.append(exp.In(this=color_dim.copy(), expressions=present))
    if any(v is None for v in keep):
        parts.append(exp.Is(this=color_dim.copy(), expression=exp.Null()))
    return exp.or_(*parts) if parts else exp.false()


def _drop_helpers(table: pa.Table) -> pa.Table:
    return table.drop_columns([n for n in table.column_names if n.startswith("__viz_")])


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
    """GROUP BY x[, color], SUM(y): the same shape as `area`, downsampled with MinMaxLTTB when
    an ordered x has more points than the cap per series."""
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
    color = _color_info(req)
    if color is not None:
        color_field, color_enc = color
        color_dim = dimension_sql(color_enc)
        fields["color"] = color_enc.label or color_field
        keep, more = _top_colors(req, color_dim, ys[0], params.max_series)
        if more:
            relation = relation.narrow(_color_condition(color_dim, keep))
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
    table = relation.arrow(over_groups(grouped).order_by(*order))

    per_measure_cap = max(1, req.mark_cap // len(ys))
    n_series = max(1, len({v for v in table.column("color").to_pylist()}) if color else 1)
    y_names = [f"y{i}" for i in range(len(ys))]
    reduction, method = Reduction.AGGREGATED, "group_by"
    if ordered_x and table.num_rows > per_measure_cap:
        n_out = max(3, per_measure_cap // n_series)
        table, reduced = minmaxlttb(
            table, x="x", ys=y_names, series="color" if color else None, n_out=n_out
        )
        if reduced:
            reduction, method = Reduction.DOWNSAMPLED, "minmaxlttb"
            params_out["nOut"] = n_out
    elif table.num_rows > per_measure_cap:
        order_col = pc.sort_indices(table, sort_keys=[(y_names[0], "descending")])[:per_measure_cap]
        table = table.take(order_col)
        table = table.take(pc.sort_indices(table, sort_keys=[("x", "ascending")]))
        params_out["truncatedGroups"] = True
        warnings.append(_truncation_warning(per_measure_cap))
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
        relation = relation.narrow(_color_condition(color_dim, keep))
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
    if table.num_rows > cap:
        table = table.slice(0, cap)
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
    first_is_total: bool = True
    last_is_total: bool = True


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
    table = req.relation.arrow(over_groups(grouped).order_by(order_expr))
    if table.num_rows == 0:
        raise InvalidSpecError("No rows to draw a waterfall from.")

    xs = table.column("x").to_pylist()
    ys = pc.cast(table.column("__viz_y"), pa.float64()).to_pylist()
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
        params={},
        warnings=[],
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
    levels = req.fields("label")
    if not levels:
        raise InvalidSpecError("Choose at least one field for 'label'.")
    size_enc = _first_encoding(req, "size")
    measure = _size_measure(size_enc)

    dims = [(f"label{i}", col(field)) for i, field in enumerate(levels)]
    grouped = aggregate_query(req.relation, dims, [("size", measure)])
    table = req.relation.arrow(over_groups(grouped))
    warnings: list[str] = []
    params_out: dict[str, Any] = {}
    last = f"label{len(levels) - 1}"
    if table.num_rows > max_leaves:
        order = pc.sort_indices(table, sort_keys=[("size", "descending")])
        keep = order[:max_leaves]
        rest = order[max_leaves:]
        kept = table.take(keep)
        if len(rest):
            dropped = table.take(rest)
            other_last = pa.array(["(Other)"] * dropped.num_rows, pa.string())
            dropped = dropped.set_column(dropped.schema.get_field_index(last), last, other_last)
            merged = pa.concat_tables([kept, dropped])
            group_cols = [f"label{i}" for i in range(len(levels))]
            # Re-aggregate: rows that now share the same path (after renaming) must merge.
            df_groups: dict[tuple[Any, ...], float] = {}
            for row in merged.to_pylist():
                key = tuple(row[c] for c in group_cols)
                df_groups[key] = df_groups.get(key, 0.0) + (row["size"] or 0.0)
            cols: dict[str, list[Any]] = {c: [] for c in group_cols}
            cols["size"] = []
            for key, value in df_groups.items():
                for c, v in zip(group_cols, key, strict=True):
                    cols[c].append(v)
                cols["size"].append(value)
            table = pa.table(cols)
            params_out["rolledUpLeaves"] = len(rest)
            warnings.append(f"The {len(rest)} smallest leaves are grouped into '(Other)'.")
        else:
            table = kept
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


def _crosstab_stat(req: PlotRequest) -> TableOutput:
    params = req.params
    assert isinstance(params, CrosstabParams)
    x_field, x_enc, _ = _x_info(req)
    color_field, color_enc = _color_info(req) or (None, None)
    if color_field is None or color_enc is None:
        raise InvalidSpecError("Choose a field for 'color'.")
    size_enc = _first_encoding(req, "size")
    measure = _size_measure(size_enc)

    grouped = aggregate_query(
        req.relation, [("x", col(x_field)), ("color", col(color_field))], [("value", measure)]
    )
    table = req.relation.arrow(over_groups(grouped))
    warnings: list[str] = []
    params_out: dict[str, Any] = {}

    def _cap_dim(tbl: pa.Table, dim: str, limit: int) -> tuple[pa.Table, bool]:
        totals: dict[Any, float] = {}
        for row in tbl.to_pylist():
            totals[row[dim]] = totals.get(row[dim], 0.0) + (row["value"] or 0.0)
        if len(totals) <= limit:
            return tbl, False
        keep = {k for k, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:limit]}
        rows = []
        merged: dict[tuple[Any, Any], float] = {}
        for row in tbl.to_pylist():
            key_val = row[dim] if row[dim] in keep else "(Other)"
            other = "x" if dim == "color" else "color"
            merge_key = (row[other], key_val) if dim == "color" else (key_val, row[other])
            merged[merge_key] = merged.get(merge_key, 0.0) + (row["value"] or 0.0)
        for (xv, cv), value in merged.items():
            rows.append({"x": xv, "color": cv, "value": value})
        return pa.Table.from_pylist(rows), True

    table, cx = _cap_dim(table, "x", params.max_x)
    table, cc = _cap_dim(table, "color", params.max_color)
    if cx or cc:
        params_out["rolledUp"] = True
        warnings.append("Small categories are grouped into '(Other)'.")

    rows = table.to_pylist()
    x_totals: dict[Any, float] = {}
    for row in rows:
        x_totals[row["x"]] = x_totals.get(row["x"], 0.0) + (row["value"] or 0.0)
    grand_total = sum(x_totals.values()) or 1.0
    x_shares = []
    shares = []
    for row in rows:
        xt = x_totals[row["x"]] or 1.0
        x_shares.append(xt / grand_total)
        shares.append((row["value"] or 0.0) / xt)
    out = pa.table(
        {
            "x": [r["x"] for r in rows],
            "color": [r["color"] for r in rows],
            "size": [r["value"] for r in rows],
            "xShare": pa.array(x_shares, pa.float64()),
            "share": pa.array(shares, pa.float64()),
        }
    )
    fields = {
        "x": x_enc.label or x_field,
        "color": color_enc.label or color_field,
        "size": _size_label(size_enc),
    }
    return TableOutput(
        table=out,
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
