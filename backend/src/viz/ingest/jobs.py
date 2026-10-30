"""Import and refresh job handlers. Params are plain models so a queue worker can run them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from viz.contracts import (
    Dataset,
    DatasetSource,
    DatasetVersion,
    ImportOptions,
    JobKind,
    JobResult,
    SourceKind,
)
from viz.errors import NotFoundError
from viz.ingest import catalog
from viz.ingest.importer import (
    PROSE_MIN_ROWS,
    PreparedSource,
    Progress,
    finish_source,
    median_text_length,
    prepare_source,
    rollback_source,
    schema_changes,
    shape_warnings,
    write_version,
)
from viz.jobs import JobContext, job_handler, job_result


class ImportParams(BaseModel):
    dataset_id: str
    name: str
    staged_id: str | None = None
    path: str | None = None
    kind: SourceKind | None = None
    options: ImportOptions = Field(default_factory=ImportOptions)


class RefreshParams(BaseModel):
    dataset_id: str
    # Re-upload; None re-reads source.originalPath.
    staged_id: str | None = None
    # None reuses the current version's options.
    options: ImportOptions | None = None
    # Kind of a re-upload (resolved at submit); None re-reads with the dataset's kind.
    kind: SourceKind | None = None


def dataset_key(dataset_id: str) -> str:
    """Dedupe key: one import/refresh at a time per dataset."""
    return f"dataset:{dataset_id}"


def _progress(ctx: JobContext) -> Progress:
    def report(fraction: float | None, message: str | None) -> None:
        ctx.progress(fraction, message)

    return report


# The staged upload is marked in use from submit until the job ends (`on_done` releases it,
# see `api/datasets.py`), so queued jobs are protected from DELETE and the expiry sweep too.


@job_handler(JobKind.IMPORT, ImportParams)
def run_import(ctx: JobContext, params: ImportParams) -> JobResult:
    if params.staged_id is not None:
        ctx.storage.touch_staged(params.staged_id)
    return _import(ctx, params)


@job_handler(JobKind.REFRESH, RefreshParams)
def run_refresh(ctx: JobContext, params: RefreshParams) -> JobResult:
    if params.staged_id is not None:
        ctx.storage.touch_staged(params.staged_id)
    return _refresh(ctx, params)


def _shape(cur: Any, ctx: JobContext, kind: SourceKind, version: DatasetVersion) -> list[str]:
    median = None
    if len(version.columns) == 1 and version.row_count > PROSE_MIN_ROWS:
        median = median_text_length(
            cur, ctx.storage.resolve(version.parquet_path), version.columns[0]
        )
    return shape_warnings(kind, version.columns, version.row_count, median)


def _import(ctx: JobContext, params: ImportParams) -> JobResult:
    """A new dataset. On failure or cancel nothing is left behind: `sources/<id>` and
    `datasets/<id>` are removed; the staged upload is untouched."""
    source: PreparedSource | None = None
    try:
        ctx.progress(0.0, "Reading source")
        source = prepare_source(
            ctx.storage,
            ctx.settings,
            params.dataset_id,
            staged_id=params.staged_id,
            path=params.path,
            kind=params.kind,
            options=params.options,
            progress=_progress(ctx),
        )
        ctx.progress(0.3, "Converting to Parquet")
        with ctx.cursor(progress=(0.3, 0.9)) as cur:
            version = write_version(cur, ctx.storage, source, params.dataset_id, 1)
            source.warnings.extend(_shape(cur, ctx, source.kind, version))
        ctx.progress(0.95, "Saving")
        original = str(Path(params.path).expanduser().resolve()) if params.path else None
        dataset = Dataset(
            id=params.dataset_id,
            name=params.name,
            source=DatasetSource(
                kind=source.kind,
                file_name=source.file_name,
                original_path=original,
                options=source.options,
            ),
            versions=[version],
        )
        ctx.check_cancelled()
        with ctx.session() as db:
            catalog.save_dataset(db, dataset)
    except BaseException:
        rollback_source(ctx.storage, source)
        ctx.storage.delete_dataset(params.dataset_id)
        raise
    finish_source(ctx.storage, params.staged_id)
    return job_result(dataset_id=dataset.id, version=version.n, warnings=source.warnings)


def _refresh(ctx: JobContext, params: RefreshParams) -> JobResult:
    """A new version of an existing dataset. On failure the new source copy and Parquet file
    are removed; earlier versions and the staged upload are untouched."""
    with ctx.session() as db:
        dataset = catalog.get_dataset(db, params.dataset_id)
    options = params.options or (dataset.current().options if dataset.versions else None)
    options = options or dataset.source.options
    path = None
    if params.staged_id is None:
        if not dataset.source.original_path:
            raise NotFoundError(
                "this dataset was uploaded, so there's no original file to re-read; upload it again"
            )
        path = dataset.source.original_path
    number = max((v.n for v in dataset.versions), default=0) + 1
    source: PreparedSource | None = None
    try:
        ctx.progress(0.0, "Reading source")
        source = prepare_source(
            ctx.storage,
            ctx.settings,
            dataset.id,
            staged_id=params.staged_id,
            path=path,
            kind=dataset.source.kind if params.staged_id is None else params.kind,
            options=options,
            progress=_progress(ctx),
        )
        ctx.progress(0.3, "Converting to Parquet")
        with ctx.cursor(progress=(0.3, 0.9)) as cur:
            version = write_version(cur, ctx.storage, source, dataset.id, number)
            shape = _shape(cur, ctx, source.kind, version)
        ctx.progress(0.95, "Saving")
        warnings = list(source.warnings) + shape
        if dataset.versions:
            warnings += schema_changes(dataset.current().columns, version.columns)
        ctx.check_cancelled()
        with ctx.session() as db:
            latest = catalog.get_dataset(db, dataset.id)  # re-read: a rename may have happened
            source_update: dict[str, object] = {"options": source.options, "kind": source.kind}
            if params.staged_id is not None:
                source_update["file_name"] = source.file_name
            updated = latest.model_copy(
                update={
                    "source": latest.source.model_copy(update=source_update),
                    "versions": [*latest.versions, version],
                }
            )
            catalog.save_dataset(db, updated)
    except BaseException:
        rollback_source(ctx.storage, source)
        ctx.storage.resolve(ctx.storage.parquet_rel(dataset.id, number)).unlink(missing_ok=True)
        raise
    finish_source(ctx.storage, params.staged_id)
    ctx.engine.cache.evict(f"ds:{dataset.id}@")  # old versions stay valid; this frees memory
    return job_result(dataset_id=dataset.id, version=version.n, warnings=warnings)
