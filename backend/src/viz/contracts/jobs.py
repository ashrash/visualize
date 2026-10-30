"""Background jobs (import, refresh, materialize, export) and data exports."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from viz.contracts.base import Contract, SortKey, SourceRef
from viz.contracts.pipeline import Pipeline
from viz.contracts.predicate import Predicate


class JobKind(StrEnum):
    IMPORT = "import"
    REFRESH = "refresh"
    MATERIALIZE = "materialize"
    EXPORT = "export"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExportFormat(StrEnum):
    CSV = "csv"
    XLSX = "xlsx"


class ExportFile(Contract):
    """Server-produced."""

    export_id: str
    file_name: str
    format: ExportFormat
    size_bytes: int
    row_count: int
    # /api/exports/{id}/download locally; a presigned S3 URL on AWS.
    download_url: str
    expires_at: datetime


class JobResult(Contract):
    """Server-produced."""

    dataset_id: str | None
    version: int | None
    pipeline_id: str | None
    export: ExportFile | None
    warnings: list[str]


class Job(Contract):
    """Server-produced. Each SSE event carries a full snapshot."""

    id: str
    kind: JobKind
    title: str
    status: JobStatus
    # 0-1; None while indeterminate.
    progress: float | None
    # Current stage, e.g. "Converting to Parquet".
    message: str | None
    # Bumps on every change; the SSE event id.
    seq: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    result: JobResult | None


class ExportRequest(Contract):
    source: SourceRef
    format: ExportFormat
    # Pipelines the source references, as for chart data.
    pipelines: list[Pipeline] = Field(default_factory=list)
    # Dataset sources only; default the current version.
    version: int | None = None
    filters: list[Predicate] = Field(default_factory=list)
    sort: list[SortKey] = Field(default_factory=list)
    columns: list[str] | None = None
    # Default "<source name>.<ext>".
    file_name: str | None = None
    # CSV only; default ",".
    delimiter: str | None = None
