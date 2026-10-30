"""Dataset endpoints: staging, preview, import/refresh jobs, catalog, grid rows and values.

Wizard flow: POST /datasets/staged (or use a local path) -> POST /datasets/preview on every
option change -> POST /datasets/import (202 Job) -> follow /jobs/{id}/events.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile
from pydantic import ValidationError

from viz.api.deps import DbDep, EngineDep, SettingsDep, StorageDep
from viz.api.jobs import JobsDep
from viz.config import Settings
from viz.contracts import (
    ARROW_STREAM_MEDIA_TYPE,
    ColumnValues,
    ColumnValuesRequest,
    Dataset,
    DatasetUpdate,
    ImportFromPathRequest,
    ImportOptions,
    ImportPreviewRequest,
    ImportRequest,
    Job,
    JobKind,
    JobStatus,
    RefreshRequest,
    RowsRequest,
    SourceKind,
    StagedFile,
    new_id,
)
from viz.engine import ipc_bytes, run_cancellable
from viz.errors import ConflictError, InvalidSpecError
from viz.ingest import catalog, readers
from viz.ingest.jobs import ImportParams, RefreshParams, dataset_key
from viz.ingest.preview import preview, resolve_input, stage_upload
from viz.ingest.rows import column_values, rows_key, rows_page
from viz.jobs import JobRunner
from viz.storage import LocalStorage

router = APIRouter(prefix="/datasets", tags=["datasets"])

_ARROW_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {"content": {ARROW_STREAM_MEDIA_TYPE: {}}, "description": "Arrow IPC"}
}
# Sync import endpoints wait this long for their job.
_SYNC_IMPORT_TIMEOUT_S = 3600.0


# -- catalog -------------------------------------------------------------------------------


@router.get("", response_model=list[Dataset])
def list_datasets(db: DbDep) -> list[Dataset]:
    return catalog.list_datasets(db)


@router.get("/{dataset_id}", response_model=Dataset)
def get_dataset(dataset_id: str, db: DbDep) -> Dataset:
    return catalog.get_dataset(db, dataset_id)


@router.patch("/{dataset_id}", response_model=Dataset)
def update_dataset(dataset_id: str, body: DatasetUpdate, db: DbDep) -> Dataset:
    dataset = catalog.get_dataset(db, dataset_id)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise InvalidSpecError("a dataset name can't be empty")
        dataset = dataset.model_copy(update={"name": name})
    catalog.save_dataset(db, dataset)
    return dataset


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: str,
    db: DbDep,
    engine: EngineDep,
    storage: StorageDep,
    jobs: JobsDep,
    force: Annotated[bool, Query()] = False,
) -> Response:
    dataset = catalog.get_dataset(db, dataset_id)
    if jobs.find_active(dataset_key(dataset_id)) is not None:
        raise ConflictError(f"{dataset.name} is being imported or refreshed; try again shortly")
    users = catalog.projects_using(db, dataset_id)
    if users and not force:
        raise ConflictError(f"{dataset.name} is used by: {', '.join(users)}")
    catalog.delete_dataset_row(db, dataset_id)
    storage.delete_dataset(dataset_id)
    engine.cache.evict(f"ds:{dataset_id}@")
    return Response(status_code=204)


# -- staging, preview, import ----------------------------------------------------------------


@router.post("/staged", response_model=StagedFile, status_code=201)
def stage_file(
    file: Annotated[UploadFile, File()], storage: StorageDep, settings: SettingsDep
) -> StagedFile:
    return stage_upload(storage, settings, file.filename or "upload.csv", file.file)


@router.delete("/staged/{staged_id}", status_code=204)
def delete_staged(staged_id: str, storage: StorageDep) -> Response:
    try:
        storage.delete_staged(staged_id)
    except ValueError as exc:
        raise InvalidSpecError(str(exc)) from exc
    return Response(status_code=204)


@router.post("/preview", response_class=Response, responses=_ARROW_RESPONSE)
def preview_import(
    body: ImportPreviewRequest, engine: EngineDep, storage: StorageDep, settings: SettingsDep
) -> Response:
    """First rows with the given options; meta (ImportPreviewMeta) has what was detected."""
    table, meta = preview(engine, storage, settings, body)
    return Response(ipc_bytes(table, meta), media_type=ARROW_STREAM_MEDIA_TYPE)


def _default_name(file_name: str, options: ImportOptions) -> str:
    stem = Path(file_name).stem
    return f"{stem} - {options.sheet}" if options.sheet else stem


def submit_import(
    jobs: JobRunner, storage: LocalStorage, settings: Settings, body: ImportRequest
) -> Job:
    """Validate the source now (404/415 before any job starts), then queue the import."""
    source = resolve_input(storage, settings, body.staged_id, body.path)
    kind = readers.sniff_kind(source, body.kind or readers.detect_kind(source.name))
    if body.options.column_types:
        for type_name in body.options.column_types.values():
            readers.normalize_type(type_name)
    dataset_id = new_id()
    name = (body.name or "").strip() or _default_name(source.name, body.options)
    params = ImportParams(
        dataset_id=dataset_id,
        name=name,
        staged_id=body.staged_id,
        path=body.path,
        kind=kind,
        options=body.options,
    )
    return jobs.submit(
        JobKind.IMPORT, params, title=f"Import {name}", dedupe_key=dataset_key(dataset_id)
    )


@router.post("/import", response_model=Job, status_code=202)
def import_dataset(
    body: ImportRequest, jobs: JobsDep, storage: StorageDep, settings: SettingsDep
) -> Job:
    return submit_import(jobs, storage, settings, body)


@router.post("/{dataset_id}/refresh", response_model=Job, status_code=202)
def refresh_dataset(
    dataset_id: str,
    body: RefreshRequest,
    db: DbDep,
    jobs: JobsDep,
    storage: StorageDep,
) -> Job:
    """Re-read the original file (or a re-upload) as a new version; old versions stay."""
    dataset = catalog.get_dataset(db, dataset_id)
    if body.staged_id is None and not dataset.source.original_path:
        raise ConflictError(
            "this dataset was uploaded, so there's no original file to re-read; upload it again"
        )
    if body.staged_id is not None and storage.find_staged(body.staged_id) is None:
        raise InvalidSpecError("the uploaded file has expired; upload it again")
    params = RefreshParams(dataset_id=dataset_id, staged_id=body.staged_id, options=body.options)
    return jobs.submit(
        JobKind.REFRESH, params, title=f"Refresh {dataset.name}", dedupe_key=dataset_key(dataset_id)
    )


def _wait_for_dataset(jobs: JobRunner, job: Job, db: DbDep) -> Dataset:
    done = jobs.wait(job.id, _SYNC_IMPORT_TIMEOUT_S)
    if done.status != JobStatus.SUCCEEDED or done.result is None or not done.result.dataset_id:
        raise InvalidSpecError(done.error or f"import {done.status.value}")
    return catalog.get_dataset(db, done.result.dataset_id)


@router.post("/upload", response_model=Dataset, status_code=201)
def upload_dataset(
    file: Annotated[UploadFile, File()],
    db: DbDep,
    jobs: JobsDep,
    storage: StorageDep,
    settings: SettingsDep,
    name: Annotated[str | None, Form()] = None,
    kind: Annotated[SourceKind | None, Form()] = None,
    options: Annotated[str | None, Form(description="ImportOptions as JSON")] = None,
) -> Dataset:
    """One-shot upload + import that waits for the job. The wizard uses staged + import."""
    try:
        parsed = ImportOptions.model_validate_json(options) if options else ImportOptions()
    except ValidationError as exc:
        raise InvalidSpecError(f"invalid options: {exc.errors()[0]['msg']}") from exc
    file_name = file.filename or "upload.csv"
    staged = stage_upload(storage, settings, file_name, file.file)
    try:
        job = submit_import(
            jobs,
            storage,
            settings,
            ImportRequest(staged_id=staged.staged_id, name=name, kind=kind, options=parsed),
        )
    except BaseException:
        storage.delete_staged(staged.staged_id)
        raise
    return _wait_for_dataset(jobs, job, db)


@router.post("/import-path", response_model=Dataset, status_code=201)
def import_from_path(
    body: ImportFromPathRequest,
    db: DbDep,
    jobs: JobsDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> Dataset:
    """One-shot import from a local path that waits for the job."""
    request = ImportRequest(path=body.path, name=body.name, kind=body.kind, options=body.options)
    job = submit_import(jobs, storage, settings, request)
    return _wait_for_dataset(jobs, job, db)


# -- grid rows and values ------------------------------------------------------------------------


@router.get("/{dataset_id}/rows", response_class=Response, responses=_ARROW_RESPONSE)
def get_rows(
    dataset_id: str,
    db: DbDep,
    engine: EngineDep,
    storage: StorageDep,
    settings: SettingsDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1)] = 200,
) -> Response:
    """Unsorted, unfiltered pages (kept for compatibility; the grid uses POST)."""
    dataset = catalog.get_dataset(db, dataset_id)
    request = RowsRequest(offset=offset, limit=limit)
    table, meta = rows_page(
        engine,
        storage,
        dataset,
        request,
        max_limit=settings.grid_page_max,
        key=rows_key(dataset_id, request),
    )
    return Response(ipc_bytes(table, meta), media_type=ARROW_STREAM_MEDIA_TYPE)


@router.post("/{dataset_id}/rows", response_class=Response, responses=_ARROW_RESPONSE)
async def query_rows(
    dataset_id: str,
    body: RowsRequest,
    request: Request,
    db: DbDep,
    engine: EngineDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> Response:
    """A grid page with sort, filters, search and a column subset. Meta: RowsPageMeta."""
    key = rows_key(dataset_id, body)

    def work() -> bytes:
        dataset = catalog.get_dataset(db, dataset_id)
        table, meta = rows_page(
            engine, storage, dataset, body, max_limit=settings.grid_page_max, key=key
        )
        return ipc_bytes(table, meta)

    content = await run_cancellable(request, engine, key, work)
    return Response(content, media_type=ARROW_STREAM_MEDIA_TYPE)


@router.post("/{dataset_id}/values", response_model=ColumnValues)
async def query_values(
    dataset_id: str,
    body: ColumnValuesRequest,
    request: Request,
    db: DbDep,
    engine: EngineDep,
    storage: StorageDep,
) -> ColumnValues:
    """Distinct values of one column with counts (most frequent first), for filter lists."""
    key = f"values:{dataset_id}:{body.field}"

    def work() -> ColumnValues:
        dataset = catalog.get_dataset(db, dataset_id)
        return column_values(engine, storage, dataset, body, key=key)

    return await run_cancellable(request, engine, key, work)
