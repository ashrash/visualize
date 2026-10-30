"""Chart endpoints: the plot catalogue and the chart-data protocol."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response

from viz.api.deps import DbDep, EngineDep, SettingsDep, StorageDep
from viz.chartdata.service import chart_data
from viz.contracts import ARROW_STREAM_MEDIA_TYPE, PLOT_FAMILY, ChartDataRequest, PlotInfo
from viz.engine import ipc_bytes

router = APIRouter(tags=["charts"])

_ARROW_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {"content": {ARROW_STREAM_MEDIA_TYPE: {}}, "description": "Arrow IPC"}
}


@router.get("/plots", response_model=list[PlotInfo])
def list_plots() -> list[PlotInfo]:
    return [PlotInfo(type=plot, family=family) for plot, family in PLOT_FAMILY.items()]


@router.post("/charts/data", response_class=Response, responses=_ARROW_RESPONSE)
def get_chart_data(
    body: ChartDataRequest,
    db: DbDep,
    engine: EngineDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> Response:
    table, meta = chart_data(engine, storage, db, body, mark_cap=settings.mark_cap)
    return Response(ipc_bytes(table, meta), media_type=ARROW_STREAM_MEDIA_TYPE)
