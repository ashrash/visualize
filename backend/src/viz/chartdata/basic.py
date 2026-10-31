"""The generic aggregate path: bar, horizontal/grouped/stacked bar, line, area, pie, donut.

Columns: `x`, optional `color`, measures `y0..yN` (DOUBLE), and with style.errorBars
`y{i}_lo`/`y{i}_hi`. Rules (marks = rows x measures; cap = mark_cap):
- GROUP BY x[, color]. At most cap/len(y) rows are returned.
- Line/area with an ordered x (temporal or numeric): if a series has more than 2 x
  viewport.widthPx points it is downsampled with MinMaxLTTB to 2 x widthPx. When the grouped
  result is over the cap, it is first cut to ~4 points per pixel column with M4 in SQL.
- Other charts over the cap keep the top groups (by the y0 total per x), with a warning.
- More colors than params.maxSeries (default 50): keep the series with the largest |y0|.
- viewport.xRange (ordered x only) re-queries just the visible range; meta.xDomain keeps
  the full extent so a zoom slider keeps its scale.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import JsonValue
from scipy import stats as sps
from sqlglot import exp

from viz.chartdata.context import ChartContext, ChartOutput
from viz.chartdata.reduce import (
    aggregate_query,
    bucket_position,
    epoch_ms_values,
    m4_extent,
    m4_extent_query,
    m4_query,
    minmaxlttb,
    numeric_x,
    over_groups,
    x_range_predicate,
)
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
    window,
)
from viz.contracts import AggFn, ColumnSemantic, Encoding, ErrorBars, PlotType, Reduction
from viz.errors import InvalidSpecError
from viz.stats.relation import Relation

AGGREGATE_PLOTS = frozenset(
    {
        PlotType.BAR,
        PlotType.HORIZONTAL_BAR,
        PlotType.GROUPED_BAR,
        PlotType.STACKED_BAR,
        PlotType.LINE,
        PlotType.AREA,
        PlotType.PIE,
        PlotType.DONUT,
    }
)
SERIES_PLOTS = frozenset({PlotType.LINE, PlotType.AREA})
ORDERED = frozenset({ColumnSemantic.TEMPORAL, ColumnSemantic.QUANTITATIVE})
DEFAULT_MAX_SERIES = 50

_AGG_LABELS = {
    AggFn.COUNT: "Count",
    AggFn.COUNT_DISTINCT: "Distinct count",
    AggFn.SUM: "Sum",
    AggFn.MEAN: "Mean",
    AggFn.MEDIAN: "Median",
    AggFn.MIN: "Min",
    AggFn.MAX: "Max",
    AggFn.STD: "Std dev",
    AggFn.VAR: "Variance",
}


def measure_label(encoding: Encoding) -> str:
    if encoding.label:
        return encoding.label
    if encoding.field is None:
        return "Count"
    return f"{_AGG_LABELS[default_agg(encoding)]} of {encoding.field}"


def _resolved(ctx: ChartContext, encoding: Encoding) -> Encoding:
    if encoding.field is None:
        return encoding
    return encoding.model_copy(update={"field": ctx.column(encoding.field).name})


def _int_param(ctx: ChartContext, name: str, default: int, lo: int, hi: int) -> int:
    value = ctx.spec.params.get(name, default)
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise InvalidSpecError(f"'{name}' must be a whole number from {lo} to {hi}")
    return value


def epoch_ms_domain(column: pa.ChunkedArray) -> tuple[float, float] | None:
    """(min, max) of an x column as plain numbers (epoch milliseconds for temporal x)."""
    kind = column.type
    temporal = pa.types.is_timestamp(kind) or pa.types.is_date(kind)
    if not (temporal or pa.types.is_integer(kind) or pa.types.is_floating(kind)):
        return None
    values = epoch_ms_values(column)
    values = values[np.isfinite(values)]
    if not len(values):
        return None
    return float(values.min()), float(values.max())


def _domain_query(relation: Relation, x_dim: exp.Expr, semantic: ColumnSemantic) -> exp.Select:
    position = numeric_x(x_dim, semantic)
    return relation.select(
        alias(fn("min", position), "lo"), alias(fn("max", position.copy()), "hi")
    )


def _top_series(
    ctx: ChartContext, relation: Relation, color_dim: exp.Expr, y0: Encoding, limit: int
) -> tuple[list[Any], bool]:
    """The colors (at most `limit`, largest |y0| first) and whether there were more."""
    grouped = aggregate_query(
        relation, [("color", color_dim)], [("__viz_size", fn("abs", measure_sql(y0)))]
    )
    query = (
        over_groups(grouped)
        .order_by(ordered(col("__viz_size"), desc=True), ordered(col("color")))
        .limit(limit + 1)
    )
    values = ctx.query(query).column("color").to_pylist()
    return values[:limit], len(values) > limit


def _series_condition(color_dim: exp.Expr, keep: list[Any]) -> exp.Expr:
    present = [lit(v) for v in keep if v is not None]
    parts: list[exp.Expr] = []
    if present:
        parts.append(exp.In(this=color_dim.copy(), expressions=present))
    if any(v is None for v in keep):
        parts.append(exp.Is(this=color_dim.copy(), expression=exp.Null()))
    return exp.or_(*parts) if parts else exp.false()


def _error_bar_measures(ys: list[Encoding], bars: ErrorBars) -> list[tuple[str, exp.Expr]]:
    measures: list[tuple[str, exp.Expr]] = []
    if bars.kind == "fixed":
        return measures
    for i, y in enumerate(ys):
        if y.field is None:
            continue
        value = cast(col(y.field), "DOUBLE")
        finite = exp.Case(ifs=[exp.If(this=fn("isfinite", value), true=value.copy())])
        measures.append((f"__viz_sd{i}", fn("stddev_samp", finite)))
        measures.append((f"__viz_n{i}", exp.Count(this=finite.copy())))
    return measures


def _apply_error_bars(
    table: pa.Table, ys: list[Encoding], bars: ErrorBars, fields: dict[str, str]
) -> tuple[pa.Table, list[str]]:
    warnings: list[str] = []
    for i, y in enumerate(ys):
        center = pc.cast(table.column(f"y{i}"), pa.float64()).to_numpy(zero_copy_only=False)
        if bars.kind == "fixed":
            if bars.value is None:
                raise InvalidSpecError("fixed error bars need a value")
            half = np.full(len(center), abs(bars.value), dtype=np.float64)
        elif y.field is None:
            half = np.full(len(center), np.nan)
            warnings.append(f"Error bars need a field; {measure_label(y)!r} has none.")
        else:
            sd = pc.cast(table.column(f"__viz_sd{i}"), pa.float64()).to_numpy(zero_copy_only=False)
            n = pc.cast(table.column(f"__viz_n{i}"), pa.float64()).to_numpy(zero_copy_only=False)
            with np.errstate(divide="ignore", invalid="ignore"):
                if bars.kind == "sd":
                    half = sd
                elif bars.kind == "se":
                    half = sd / np.sqrt(n)
                else:
                    if not 0 < bars.level < 1:
                        raise InvalidSpecError("the confidence level must be between 0 and 1")
                    t = sps.t.ppf((1 + bars.level) / 2, np.where(n > 1, n - 1, np.nan))
                    half = t * sd / np.sqrt(n)
        lo, hi = center - half, center + half
        mask = ~np.isfinite(lo) | ~np.isfinite(hi)
        table = table.append_column(f"y{i}_lo", pa.array(lo, pa.float64(), mask=mask))
        table = table.append_column(f"y{i}_hi", pa.array(hi, pa.float64(), mask=mask))
        fields[f"y{i}_lo"] = f"{fields[f'y{i}']} (low)"
        fields[f"y{i}_hi"] = f"{fields[f'y{i}']} (high)"
    return table, warnings


def _truncation_warning(limit: int, x: Encoding, semantic: ColumnSemantic) -> str:
    text = f"Showing the first {limit:,} groups; narrow the data with a filter"
    if semantic == ColumnSemantic.TEMPORAL and x.time_unit is None:
        return text + ", or set a time unit on the x axis."
    if semantic == ColumnSemantic.QUANTITATIVE:
        return text + ", or use a histogram for a numeric x."
    return text + "."


def _drop_helpers(table: pa.Table) -> pa.Table:
    return table.drop_columns([n for n in table.column_names if n.startswith("__viz_")])


def aggregate_chart(ctx: ChartContext) -> ChartOutput:
    spec = ctx.spec
    x_enc = ctx.one("x", required=True)
    assert x_enc is not None
    x = _resolved(ctx, x_enc)
    ys = [_resolved(ctx, y) for y in ctx.encodings("y")] or [Encoding(agg=AggFn.COUNT)]
    color_enc = ctx.one("color", required=False)
    color = _resolved(ctx, color_enc) if color_enc and color_enc.field else None
    if spec.plot_type in (PlotType.PIE, PlotType.DONUT):
        color = None
    for y in ys:
        if y.field is None and default_agg(y) != AggFn.COUNT:
            raise InvalidSpecError(f"{default_agg(y).value} needs a field")

    assert x.field is not None
    x_column = ctx.column(x.field)
    if x.time_unit is not None and x_column.semantic != ColumnSemantic.TEMPORAL:
        raise InvalidSpecError(f"a time unit needs a date/time field; {x.field!r} isn't one")
    x_dim = dimension_sql(x)
    ordered_x = x_column.semantic in ORDERED
    series_plot = spec.plot_type in SERIES_PLOTS

    params: dict[str, JsonValue] = {}
    warnings: list[str] = []
    fields = {"x": x.label or x.field}
    relation = ctx.relation
    x_domain: tuple[float | str, float | str] | None = None
    zoomed = ctx.viewport.x_range is not None and ordered_x
    if zoomed:
        assert ctx.viewport.x_range is not None
        domain = ctx.query(_domain_query(relation, x_dim, x_column.semantic)).to_pylist()[0]
        if domain["lo"] is not None:
            x_domain = (float(domain["lo"]), float(domain["hi"]))
        relation = relation.narrow(
            x_range_predicate(
                x_dim, x_column.semantic, ctx.viewport.x_range, x.time_unit, x_column.type
            )
        )

    dimensions: list[tuple[str, exp.Expr]] = [("x", x_dim)]
    n_series = 1
    if color is not None:
        assert color.field is not None
        color_dim = dimension_sql(color)
        fields["color"] = color.label or color.field
        max_series = _int_param(ctx, "maxSeries", DEFAULT_MAX_SERIES, 1, 500)
        keep, more = _top_series(ctx, relation, color_dim, ys[0], max_series)
        n_series = max(1, len(keep))
        if more:
            relation = relation.narrow(_series_condition(color_dim, keep))
            params["truncatedSeries"] = True
            warnings.append(
                f"Showing the {max_series} largest of the {fields['color']} series; "
                "filter to see the rest."
            )
        dimensions.append(("color", color_dim))

    measures: list[tuple[str, exp.Expr]] = []
    for i, y in enumerate(ys):
        measures.append((f"y{i}", measure_sql(y)))
        fields[f"y{i}"] = measure_label(y)
    bars = spec.style.error_bars
    if bars is not None:
        measures.extend(_error_bar_measures(ys, bars))
        if bars.kind != "fixed" and any(default_agg(y) != AggFn.MEAN for y in ys):
            warnings.append(
                f"{bars.kind.upper()} error bars describe the spread around a mean; "
                "they're drawn around the chart's other aggregates as they are."
            )
    # Rows per group; summed for meta.rowsMatched (a window here costs ~0.3 s at 10M groups).
    measures.append(("__viz_n", exp.Count(this=exp.Star())))
    # A continuous series is ordered by x; that sort happens in Arrow after a cheap unordered
    # probe, since ORDER BY over millions of groups is the expensive part of the query.
    sort_in_arrow = series_plot and ordered_x and ys[0].sort is None
    by_rank = (
        not sort_in_arrow
        and x.sort is None
        and (ys[0].sort is not None or not (ordered_x or series_plot))
    )
    if by_rank:
        # Per-x total of y0 over the colors: orders categorical bars (and stacks) by size.
        rank = window(fn("sum", measure_sql(ys[0])), partition_by=[x_dim.copy()])
        measures.append(("__viz_rank", rank))

    grouped = aggregate_query(relation, dimensions, measures)
    order: list[exp.Ordered] = []
    if x.sort is not None:
        order.append(ordered(col("x"), desc=x.sort == "desc"))
    elif by_rank:
        order.append(ordered(col("__viz_rank"), desc=ys[0].sort != "asc"))
    order += [ordered(col("x")), *([ordered(col("color"))] if color is not None else [])]

    per_measure_cap = max(1, ctx.mark_cap // len(ys))
    continuous = series_plot and ordered_x
    y_names = [f"y{i}" for i in range(len(ys))]
    series_col = "color" if color is not None else None
    # Points per series: 2 x the viewport width, but all series together stay within the cap.
    n_out = max(3, min(2 * max(1, ctx.viewport.width_px), per_measure_cap // n_series))

    matched: int | None = None

    def m4_sql() -> exp.Select:
        # One cheap scan (often Parquet statistics alone) for x's extent and the row count:
        # with the extent known up front, M4 is a single pipeline over the grouped rows, and
        # the count is rowsMatched, so M4 also skips the per-group count.
        nonlocal matched
        extent_row = ctx.query(m4_extent_query(relation, x_dim, x_column.semantic)).to_pylist()
        matched = int(extent_row[0]["n"])
        lean = [(n, e) for n, e in measures if n not in ("__viz_n", "__viz_rank")]
        return m4_query(
            aggregate_query(relation, dimensions, lean),
            x="x",
            x_position=bucket_position(col("x"), x_column.semantic),
            ys=y_names,
            series=series_col,
            buckets=max(1, min(ctx.viewport.width_px, n_out)),
            extent=m4_extent(extent_row[0]),
        )

    # When the import profile says x alone has more distinct values than the cap (and there
    # is no time unit to merge them), go straight to M4 and skip the cap+1 probe, which is a
    # full extra aggregation. Filters or zoom may leave fewer groups than the profile says:
    # M4 reports the real group count, and if that fits the cap the exact probe runs instead.
    m4_table: pa.Table | None = None
    if continuous and x.time_unit is None and x_column.approx_distinct > per_measure_cap * 1.2:
        m4_table = ctx.query(m4_sql())
        groups = int(m4_table.column("__viz_groups")[0].as_py() or 0) if m4_table.num_rows else 0
        if groups <= per_measure_cap:
            m4_table = None
    if m4_table is not None:
        truncated = True
        table = m4_table
    else:
        probe = grouped if sort_in_arrow else over_groups(grouped).order_by(*order)
        table = ctx.query(probe.limit(per_measure_cap + 1))
        truncated = table.num_rows > per_measure_cap
    if truncated:
        if continuous and m4_table is None:
            table = ctx.query(m4_sql())
        if matched is None:
            count = relation.select(alias(exp.Count(this=exp.Star()), "n"))
            matched = int(ctx.query(count).column("n")[0].as_py())
        rows_matched = matched
    else:
        rows_matched = int(pc.sum(table.column("__viz_n")).as_py() or 0)
        if sort_in_arrow:
            keys = [("x", "descending" if x.sort == "desc" else "ascending", "at_end")]
            keys += [("color", "ascending", "at_end")] if color is not None else []
            table = table.take(pc.sort_indices(table, sort_keys=keys))
    reduction, method = Reduction.AGGREGATED, "group_by"

    if truncated:
        if continuous:
            table, _ = minmaxlttb(table, x="x", ys=y_names, series=series_col, n_out=n_out)
            reduction, method = Reduction.DOWNSAMPLED, "m4+minmaxlttb"
            params["nOut"] = n_out
        if table.num_rows > per_measure_cap:
            before = set(table.column("color").to_pylist()) if series_col else set()
            table = table.slice(0, per_measure_cap)
            params["truncatedGroups"] = True
            warnings.append(_truncation_warning(per_measure_cap, x, x_column.semantic))
            if series_col:
                dropped = before - set(table.column("color").to_pylist())
                if dropped:
                    names = ", ".join(sorted(str(d) for d in dropped)[:10])
                    warnings.append(f"Series not shown: {names}.")
    elif continuous:
        table, was_reduced = minmaxlttb(table, x="x", ys=y_names, series=series_col, n_out=n_out)
        if was_reduced:
            reduction, method = Reduction.DOWNSAMPLED, "group_by+minmaxlttb"
            params["nOut"] = n_out

    if bars is not None:
        table, bar_warnings = _apply_error_bars(table, ys, bars, fields)
        warnings += bar_warnings
        params["errorBars"] = bars.kind
    table = _drop_helpers(table)
    if ordered_x and x_domain is None:
        x_domain = epoch_ms_domain(table.column("x"))
    return ChartOutput(
        table=table,
        reduction=reduction,
        method=method,
        fields=fields,
        params=params,
        warnings=warnings,
        rows_matched=rows_matched,
        x_domain=x_domain,
    )
