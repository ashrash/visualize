"""Chart data for aggregate plot types (Phase 0: the basic family).

Binning, downsampling, sampling, pipelines as sources and caching arrive in Wave 1 (transform).
"""

from __future__ import annotations

import time

import pyarrow as pa
from sqlalchemy.orm import Session
from sqlglot import exp

from viz.chartdata.sql import dimension_sql, measure_sql, parquet_source, where_sql
from viz.contracts import (
    AggFn,
    ChartDataMeta,
    ChartDataRequest,
    ColumnSemantic,
    Encoding,
    PlotType,
    Reduction,
)
from viz.engine import Engine
from viz.errors import InvalidSpecError, NotImplementedYetError
from viz.ingest.catalog import get_dataset
from viz.storage import LocalStorage

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


def _measure_label(encoding: Encoding) -> str:
    if encoding.label:
        return encoding.label
    agg = encoding.agg or (AggFn.SUM if encoding.field else AggFn.COUNT)
    if encoding.field is None:
        return "Count"
    return f"{_AGG_LABELS[agg]} of {encoding.field}"


def chart_data(
    engine: Engine,
    storage: LocalStorage,
    db: Session,
    request: ChartDataRequest,
    *,
    mark_cap: int,
) -> tuple[pa.Table, ChartDataMeta]:
    spec = request.spec
    if spec.plot_type not in AGGREGATE_PLOTS:
        raise NotImplementedYetError(f"plot type {spec.plot_type.value!r} is not implemented yet")
    if spec.source.kind != "dataset":
        raise NotImplementedYetError("pipelines as chart sources are not implemented yet")

    version = get_dataset(db, spec.source.id).current()
    known = {c.name: c for c in version.columns}

    x_list = spec.encodings.get("x", [])
    y_list = spec.encodings.get("y", [])
    color_list = spec.encodings.get("color", [])
    if len(x_list) != 1:
        raise InvalidSpecError("this chart needs exactly one x encoding")
    if not y_list:
        y_list = [Encoding(agg=AggFn.COUNT)]
    x = x_list[0]
    color = color_list[0] if color_list else None

    filters = [*spec.filters, *request.filters]
    referenced = {e.field for e in (x, *y_list, *color_list) if e.field}
    referenced |= {p.field for p in filters}
    unknown = sorted(referenced - known.keys())
    if unknown:
        raise InvalidSpecError(f"unknown field(s): {', '.join(unknown)}")

    selects: list[exp.Expr] = [exp.alias_(dimension_sql(x), "x", quoted=True)]
    group_by: list[exp.Expr] = [exp.column("x", quoted=True)]
    fields = {"x": x.label or x.field or "x"}
    if color is not None:
        selects.append(exp.alias_(dimension_sql(color), "color", quoted=True))
        group_by.append(exp.column("color", quoted=True))
        fields["color"] = color.label or color.field or "color"
    for i, y in enumerate(y_list):
        selects.append(exp.alias_(measure_sql(y), f"y{i}", quoted=True))
        fields[f"y{i}"] = _measure_label(y)

    query = exp.select(*selects).from_(parquet_source(storage.resolve(version.parquet_path)))
    condition = where_sql(filters)
    if condition is not None:
        query = query.where(condition)
    query = query.group_by(*group_by)

    x_column = known[x.field] if x.field else None
    x_is_ordered = x_column is not None and x_column.semantic in (
        ColumnSemantic.TEMPORAL,
        ColumnSemantic.QUANTITATIVE,
    )
    if x.sort == "desc":
        query = query.order_by(exp.Ordered(this=exp.column("x", quoted=True), desc=True))
    elif x.sort == "asc" or x_is_ordered:
        query = query.order_by(exp.column("x", quoted=True))
    else:
        query = query.order_by(exp.Ordered(this=exp.column("y0", quoted=True), desc=True))

    limit = max(1, mark_cap // len(y_list))
    query = query.limit(limit + 1)

    started = time.perf_counter()
    table = engine.query_arrow(query.sql(dialect="duckdb"), key=f"chart:{spec.id}")
    elapsed_ms = (time.perf_counter() - started) * 1000

    warnings: list[str] = []
    if table.num_rows > limit:
        table = table.slice(0, limit)
        warnings.append(f"Showing the first {limit:,} groups; narrow the data with a filter.")

    meta = ChartDataMeta(
        rows_scanned=version.row_count,
        rows_returned=table.num_rows,
        reduction=Reduction.AGGREGATED,
        method="group_by",
        fields=fields,
        cached=False,
        ms=round(elapsed_ms, 2),
        warnings=warnings,
    )
    return table, meta
