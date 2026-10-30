"""Pipeline endpoints: inspect (per-step schema), preview, formula check, materialize.

All requests are stateless: the client sends the pipeline and every pipeline it references,
so unsaved edits preview immediately.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response

from viz.api.deps import DbDep, EngineDep, SettingsDep, StorageDep
from viz.contracts import (
    ARROW_STREAM_MEDIA_TYPE,
    FormulaCheckRequest,
    FormulaCheckResult,
    FormulaFunction,
    MaterializeRequest,
    MaterializeResult,
    PipelineInspectRequest,
    PipelineInspectResult,
    PipelinePreviewRequest,
)
from viz.engine import ipc_bytes
from viz.transform import service

router = APIRouter(prefix="/pipelines", tags=["pipelines"])

_ARROW_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {"content": {ARROW_STREAM_MEDIA_TYPE: {}}, "description": "Arrow IPC"}
}


@router.post("/inspect", response_model=PipelineInspectResult)
def inspect_pipeline(
    body: PipelineInspectRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> PipelineInspectResult:
    return service.inspect(engine, db, storage, body)


@router.post("/preview", response_class=Response, responses=_ARROW_RESPONSE)
def preview_pipeline(
    body: PipelinePreviewRequest,
    db: DbDep,
    engine: EngineDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> Response:
    table, meta = service.preview(engine, db, storage, body, page_max=settings.grid_page_max)
    return Response(ipc_bytes(table, meta), media_type=ARROW_STREAM_MEDIA_TYPE)


@router.post("/formula/check", response_model=FormulaCheckResult)
def check_formula(
    body: FormulaCheckRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> FormulaCheckResult:
    return service.check_formula(engine, db, storage, body)


@router.get("/formula/functions", response_model=list[FormulaFunction])
def formula_functions() -> list[FormulaFunction]:
    return service.functions()


@router.post("/materialize", response_model=MaterializeResult)
def materialize_pipeline(
    body: MaterializeRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> MaterializeResult:
    return service.materialize(engine, db, storage, body)
