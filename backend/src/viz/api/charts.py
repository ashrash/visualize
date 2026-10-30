"""Chart endpoints: the plot catalogue, the chart-data protocol and trendline overlays.

Chart data is cancellable. `viz.engine.run_cancellable` runs the work in the threadpool as
one request (an owner with an arrival ticket, taken before any work starts). Every query uses
the slot key with that ticket as its generation: a newer request in the same slot supersedes
older ones (409), and a client disconnect cancels only this request's queries.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool

from viz.api.deps import DbDep, EngineDep, SettingsDep, StorageDep
from viz.chartdata.overlays import overlay_key, overlays
from viz.chartdata.service import chart_data, slot_key
from viz.contracts import (
    ARROW_STREAM_MEDIA_TYPE,
    ChartDataRequest,
    ChartStatResponse,
    OverlayResult,
    PlotInfo,
)
from viz.engine import ipc_bytes, run_cancellable
from viz.plots import plot_infos

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
    result = await run_cancellable(
        request,
        engine,
        slot_key(body),
        lambda: chart_data(engine, storage, db, body, mark_cap=settings.mark_cap),
    )
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
    return await run_cancellable(
        request,
        engine,
        overlay_key(body),
        lambda: overlays(engine, storage, db, body, mark_cap=settings.mark_cap),
    )
