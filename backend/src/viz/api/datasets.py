"""Dataset endpoints: import (upload or local path), catalog, and Arrow row pages."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, Response, UploadFile

from viz.api.deps import DbDep, EngineDep, SettingsDep, StorageDep
from viz.contracts import (
    ARROW_STREAM_MEDIA_TYPE,
    Dataset,
    DatasetSource,
    ImportFromPathRequest,
    RowsPageMeta,
    new_id,
)
from viz.engine import ipc_bytes
from viz.errors import ConflictError, InvalidSpecError, NotFoundError, PayloadTooLargeError
from viz.ingest import catalog
from viz.ingest.importer import detect_kind, import_version

router = APIRouter(prefix="/datasets", tags=["datasets"])

_ARROW_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {"content": {ARROW_STREAM_MEDIA_TYPE: {}}, "description": "Arrow IPC"}
}
_COPY_CHUNK = 1024 * 1024


@router.get("", response_model=list[Dataset])
def list_datasets(db: DbDep) -> list[Dataset]:
    return catalog.list_datasets(db)


@router.get("/{dataset_id}", response_model=Dataset)
def get_dataset(dataset_id: str, db: DbDep) -> Dataset:
    return catalog.get_dataset(db, dataset_id)


@router.post("/upload", response_model=Dataset, status_code=201)
def upload_dataset(
    file: Annotated[UploadFile, File()],
    db: DbDep,
    engine: EngineDep,
    storage: StorageDep,
    settings: SettingsDep,
    name: Annotated[str | None, Form()] = None,
) -> Dataset:
    file_name = file.filename or "upload.csv"
    kind = detect_kind(file_name)
    dataset = Dataset(
        id=new_id(),
        name=name or Path(file_name).stem,
        source=DatasetSource(kind=kind, file_name=file_name),
    )
    target = storage.new_source_path(dataset.id, file_name)
    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    with target.open("wb") as out:
        while chunk := file.file.read(_COPY_CHUNK):
            written += len(chunk)
            if written > limit:
                out.close()
                target.unlink(missing_ok=True)
                raise PayloadTooLargeError(
                    f"file is larger than {settings.max_upload_mb} MB; import it from its path"
                )
            out.write(chunk)
    dataset.versions.append(import_version(engine, storage, dataset, target))
    catalog.save_dataset(db, dataset)
    return dataset


@router.post("/import-path", response_model=Dataset, status_code=201)
def import_from_path(
    body: ImportFromPathRequest,
    db: DbDep,
    engine: EngineDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> Dataset:
    if not settings.allow_path_import:
        raise InvalidSpecError("importing from a path is disabled on this server")
    original = Path(body.path).expanduser()
    if not original.is_file():
        raise NotFoundError(f"no file at {original}")
    kind = body.kind or detect_kind(original.name)
    dataset = Dataset(
        id=new_id(),
        name=body.name or original.stem,
        source=DatasetSource(
            kind=kind,
            file_name=original.name,
            original_path=str(original.resolve()),
            options=body.options,
        ),
    )
    # Copy first: Windows locks files that are open in Excel, and the copy keeps the
    # imported version reproducible if the original changes mid-import.
    target = storage.new_source_path(dataset.id, original.name)
    try:
        shutil.copyfile(original, target)
    except PermissionError as exc:
        raise ConflictError(
            f"can't read {original.name}; if it's open in Excel or another app, close it and retry"
        ) from exc
    dataset.versions.append(import_version(engine, storage, dataset, target))
    catalog.save_dataset(db, dataset)
    return dataset


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
    version = catalog.get_dataset(db, dataset_id).current()
    limit = min(limit, settings.grid_page_max)
    path = storage.resolve(version.parquet_path)
    with engine.cursor(key=f"rows:{dataset_id}:{offset}") as cur:
        table = cur.read_parquet(str(path)).limit(limit, offset=offset).to_arrow_table()
    meta = RowsPageMeta(offset=offset, limit=limit, total_rows=version.row_count)
    return Response(ipc_bytes(table, meta), media_type=ARROW_STREAM_MEDIA_TYPE)
