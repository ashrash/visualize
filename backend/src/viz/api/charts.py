"""Chart endpoints: the plot catalogue, the chart-data protocol and trendline overlays.

Chart data is cancellable: the work runs in the threadpool while the endpoint watches for a
client disconnect, and a newer request in the same slot supersedes older ones (409).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool

from viz.api.deps import DbDep, EngineDep, SettingsDep, StorageDep
from viz.chartdata.overlays import overlay_key, overlays
from viz.chartdata.service import chart_data, slot_key
from viz.chartdata.slots import Ticket, slots_for
from viz.contracts import (
    ARROW_STREAM_MEDIA_TYPE,
    ChartDataRequest,
    ChartStatResponse,
    OverlayResult,
    PlotInfo,
)
from viz.engine import Engine, ipc_bytes
from viz.plots import plot_infos
from viz.transform import runtime

router = APIRouter(tags=["charts"])

_CHART_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {
            ARROW_STREAM_MEDIA_TYPE: {},
            "application/json": {"schema": {"$ref": "#/components/schemas/ChartStatResponse"}},
        },
        "description": "Arrow IPC (meta under viz.meta), or JSON ChartStatResponse for plots "
        "that use a stat transform",
    }
}
_POLL_S = 0.1


async def run_cancellable[T](
    request: Request, engine: Engine, ticket: Ticket, work: Callable[[], T]
) -> T:
    """Run `work` in the threadpool. If the client disconnects, cancel `ticket` and interrupt
    its query, but only while it is still the newest request in its slot: an aborted older
    request must never cancel the newer one that replaced it.

    TODO(data-core): use data-core's run_cancellable (owner tokens + arrival tickets).
    """
    task = asyncio.ensure_future(run_in_threadpool(work))
    while True:
        done, _ = await asyncio.wait({task}, timeout=_POLL_S)
        if done:
            return task.result()
        if await request.is_disconnected():
            if slots_for(engine).cancel(ticket):
                runtime.interrupt(engine, ticket.key)
            return await task


@router.get("/plots", response_model=list[PlotInfo])
def list_plots() -> list[PlotInfo]:
    return plot_infos()


@router.post("/charts/data", response_class=Response, responses=_CHART_RESPONSE)
async def get_chart_data(
    body: ChartDataRequest,
    request: Request,
    db: DbDep,
    engine: EngineDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> Response:
    # The ticket is taken here, before any work starts, so the disconnect watcher and the
    # worker agree on which request is which.
    slots = slots_for(engine)
    ticket = slots.begin(slot_key(body))
    try:
        result = await run_cancellable(
            request,
            engine,
            ticket,
            lambda: chart_data(
                engine, storage, db, body, mark_cap=settings.mark_cap, ticket=ticket
            ),
        )
    finally:
        slots.end(ticket)
    if result.stat is not None:
        payload = ChartStatResponse(result=result.stat, meta=result.meta)
        return Response(payload.model_dump_json(), media_type="application/json")
    assert result.table is not None
    data = await run_in_threadpool(ipc_bytes, result.table, result.meta)
    return Response(data, media_type=ARROW_STREAM_MEDIA_TYPE)


@router.post("/charts/overlays", response_model=list[OverlayResult])
async def get_chart_overlays(
    body: ChartDataRequest,
    request: Request,
    db: DbDep,
    engine: EngineDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> list[OverlayResult]:
    slots = slots_for(engine)
    ticket = slots.begin(overlay_key(body))
    try:
        return await run_cancellable(
            request,
            engine,
            ticket,
            lambda: overlays(engine, storage, db, body, mark_cap=settings.mark_cap, ticket=ticket),
        )
    finally:
        slots.end(ticket)
