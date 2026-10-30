"""Export endpoints: start an export job, then download the file it produced."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from viz.api.deps import DbDep, SettingsDep, StorageDep
from viz.api.jobs import JobsDep
from viz.contracts import ExportRequest, Job, JobKind, new_id
from viz.errors import NotFoundError
from viz.exports.service import ExportParams, check_request
from viz.ingest import catalog

router = APIRouter(prefix="/exports", tags=["exports"])

_MEDIA_TYPES = {
    ".csv": "text/csv; charset=utf-8",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.post("", response_model=Job, status_code=202)
def start_export(
    body: ExportRequest, db: DbDep, jobs: JobsDep, storage: StorageDep, settings: SettingsDep
) -> Job:
    """Export a source (with filters, sort and a column subset) as CSV or Excel.
    The finished job's `result.export.downloadUrl` serves the file until it expires."""
    check_request(body)
    dataset = catalog.get_dataset(db, body.source.id)
    storage.sweep(
        staged_max_age_s=settings.staged_retention_s,
        exports_max_age_s=settings.export_retention_s,
    )
    params = ExportParams(export_id=new_id(), request=body)
    return jobs.submit(
        JobKind.EXPORT, params, title=f"Export {dataset.name} ({body.format.value.upper()})"
    )


@router.get(
    "/{export_id}/download",
    response_class=FileResponse,
    responses={200: {"content": {m: {} for m in _MEDIA_TYPES.values()}}},
)
def download_export(export_id: str, storage: StorageDep) -> FileResponse:
    path = storage.find_export(export_id)
    if path is None:
        raise NotFoundError("this export has expired or doesn't exist; export again")
    return FileResponse(
        path,
        filename=path.name,
        media_type=_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
    )
