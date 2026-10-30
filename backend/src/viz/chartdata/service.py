"""Chart-data dispatcher: ChartDataRequest -> reduced Arrow table (or StatResult) + meta.

1. Resolve the source (dataset or pipeline) and its filters to a Relation.
2. Look up the response cache (data version + everything that changes data).
3. Plot types with a server module (`viz.plots.get_plot`) run their stat via
   `run_plot_stat`; the basic family takes the generic aggregate path (basic.py).
4. Enforce the mark cap, normalize Arrow types for the browser, fill meta, cache.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import cast

import pyarrow as pa
from sqlalchemy.orm import Session

from viz.chartdata.arrow import browser_table
from viz.chartdata.basic import AGGREGATE_PLOTS, ORDERED, aggregate_chart, epoch_ms_domain
from viz.chartdata.cache import CachedChart, cache_for, chart_key
from viz.chartdata.context import ChartContext, ChartOutput
from viz.chartdata.reduce import numeric_x, x_range_predicate
from viz.chartdata.slots import Ticket, TicketEngine, slots_for
from viz.chartdata.sql import alias, col, fn
from viz.contracts import (
    ChartDataMeta,
    ChartDataRequest,
    ChartSpec,
    Encoding,
    Reduction,
    StatResult,
)
from viz.engine import Engine
from viz.errors import InvalidSpecError, NotImplementedYetError
from viz.plots import get_plot, run_plot_stat
from viz.plots.base import TableOutput
from viz.stats.relation import Relation
from viz.storage import LocalStorage
from viz.transform import runtime
from viz.transform.source import resolve_field, resolve_source

# Payloads above this get a warning (CLAUDE.md §6 budget: <= 5 MB per chart).
PAYLOAD_WARN_BYTES = 5 * 2**20


@dataclass(frozen=True)
class ChartResult:
    """Exactly one of `table` / `stat` is set."""

    table: pa.Table | None
    stat: StatResult | None
    meta: ChartDataMeta


def slot_key(request: ChartDataRequest) -> str:
    return f"chart:{request.slot or request.spec.id}"


def _resolve_encodings(spec: ChartSpec, relation: Relation) -> ChartSpec:
    """Encodings with field names spelled as in the source (DuckDB is case-insensitive)."""
    encodings: dict[str, list[Encoding]] = {}
    for channel, items in spec.encodings.items():
        encodings[channel] = [
            e.model_copy(update={"field": resolve_field(relation.columns, e.field)})
            if e.field is not None
            else e
            for e in items
        ]
    return spec.model_copy(update={"encodings": encodings})


def chart_data(
    engine: Engine,
    storage: LocalStorage,
    db: Session,
    request: ChartDataRequest,
    *,
    mark_cap: int,
    ticket: Ticket | None = None,
) -> ChartResult:
    """Chart data for `request`. The endpoint passes the `ticket` it took before any work
    started (so a disconnect can cancel exactly this request); direct callers can omit it."""
    started = time.perf_counter()
    slots = slots_for(engine)
    own_ticket = ticket is None
    if ticket is None:
        ticket = slots.begin(slot_key(request))
    try:
        spec = request.spec
        # Every query this request runs, including plot modules' own queries through the
        # Relation, first checks that the ticket is still the newest in its slot.
        guarded = cast(Engine, TicketEngine(engine, slots, ticket))
        relation = resolve_source(
            guarded,
            db,
            storage,
            spec.source,
            request.pipelines,
            [*spec.filters, *request.filters],
            query_key=ticket.key,
            generation=ticket.generation,
        )
        # TODO(data-core): enqueue pending materializations of a pipeline source as jobs.
        cache = cache_for(engine)
        cache_key = chart_key(relation.version_key, request, mark_cap)
        hit = cache.get(cache_key)
        if hit is not None:
            meta = hit.meta.model_copy(
                update={"cached": True, "ms": round((time.perf_counter() - started) * 1000, 2)}
            )
            return ChartResult(hit.table, hit.stat, meta)

        spec = _resolve_encodings(spec, relation)
        ctx = ChartContext(
            spec=spec,
            relation=relation,
            viewport=request.viewport,
            mark_cap=mark_cap,
            ticket=ticket,
            slots=slots,
        )
        definition = get_plot(spec.plot_type)
        if definition is None:
            if spec.plot_type not in AGGREGATE_PLOTS:
                raise NotImplementedYetError(
                    f"plot type {spec.plot_type.value!r} is not implemented yet"
                )
            result = _finish_table(ctx, aggregate_chart(ctx), started)
        else:
            result = _run_plot_module(ctx, started)
        cache.put(cache_key, CachedChart(result.table, result.stat, result.meta))
        return result
    finally:
        if own_ticket:
            slots.end(ticket)


def _run_plot_module(ctx: ChartContext, started: float) -> ChartResult:
    relation = ctx.relation
    x_domain: tuple[float | str, float | str] | None = None
    x_encodings = ctx.encodings("x")
    x_field = x_encodings[0].field if x_encodings else None
    if ctx.viewport.x_range is not None and x_field is not None:
        column = ctx.column(x_field)
        if column.semantic in ORDERED:
            position = numeric_x(col(column.name), column.semantic)
            domain = ctx.query(
                relation.select(
                    alias(fn("min", position), "lo"), alias(fn("max", position.copy()), "hi")
                )
            ).to_pylist()[0]
            if domain["lo"] is not None:
                x_domain = (float(domain["lo"]), float(domain["hi"]))
            relation = relation.narrow(
                x_range_predicate(
                    col(column.name),
                    column.semantic,
                    ctx.viewport.x_range,
                    duck_type=column.type,
                )
            )
    ctx.check_current()
    try:
        output = run_plot_stat(
            ctx.spec,
            relation,
            viewport=ctx.viewport,
            mark_cap=ctx.mark_cap,
            # TODO(data-core): pass the process-pool executor for pure-Python work.
            executor=None,
        )
    except runtime.USER_ERRORS as exc:
        raise InvalidSpecError(runtime.clean_error(exc)) from exc
    if isinstance(output, StatResult):
        meta = ChartDataMeta(
            rows_scanned=relation.row_count if relation.row_count is not None else output.n,
            rows_returned=output.n,
            reduction=Reduction.SAMPLED if output.sampled else Reduction.AGGREGATED,
            method=output.method,
            fields={},
            cached=False,
            ms=round((time.perf_counter() - started) * 1000, 2),
            warnings=list(output.warnings),
            rows_matched=output.n_total if output.n_total is not None else output.n,
            params={},
            x_domain=x_domain,
        )
        return ChartResult(None, output, meta)
    assert isinstance(output, TableOutput)
    rows_matched = output.rows_matched
    if rows_matched is None:
        ctx.check_current()
        rows_matched = relation.count()
    chart = ChartOutput(
        table=output.table,
        reduction=output.reduction,
        method=output.method,
        fields=dict(output.fields),
        params=dict(output.params),
        warnings=list(output.warnings),
        rows_matched=rows_matched,
        x_domain=x_domain,
    )
    if (
        chart.x_domain is None
        and "x" in output.table.column_names
        and x_field is not None
        and ctx.column(x_field).semantic in ORDERED
    ):
        chart.x_domain = epoch_ms_domain(output.table.column("x"))
    return _finish_table(ctx, chart, started, rows_scanned=output.rows_scanned)


def _finish_table(
    ctx: ChartContext, chart: ChartOutput, started: float, *, rows_scanned: int | None = None
) -> ChartResult:
    table = chart.table
    warnings = list(chart.warnings)
    measure_columns = [n for n in table.column_names if n.startswith("y") and n[1:].isdigit()]
    per_row = max(1, len(measure_columns))
    limit = max(1, ctx.mark_cap // per_row)
    if table.num_rows > limit:
        # A plot module returned more marks than the cap allows; never ship them.
        table = table.slice(0, limit)
        warnings.append(f"Showing the first {limit:,} rows of a larger result.")
        chart.params["truncatedGroups"] = True
    table = browser_table(table)
    if table.nbytes > PAYLOAD_WARN_BYTES:
        warnings.append(f"This chart's data is {table.nbytes / 2**20:.1f} MB; consider a filter.")
    scanned = rows_scanned
    if scanned is None:
        scanned = ctx.relation.row_count
    if scanned is None:
        scanned = chart.rows_matched or 0
    meta = ChartDataMeta(
        rows_scanned=scanned,
        rows_returned=table.num_rows,
        reduction=chart.reduction,
        method=chart.method,
        fields=chart.fields,
        cached=False,
        ms=round((time.perf_counter() - started) * 1000, 2),
        warnings=warnings,
        rows_matched=chart.rows_matched,
        params=chart.params,
        x_domain=chart.x_domain,
    )
    return ChartResult(table, None, meta)
