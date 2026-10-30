"""Datasets: an imported source plus its immutable Parquet versions."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from viz.contracts.base import Contract


class SourceKind(StrEnum):
    CSV = "csv"
    TSV = "tsv"
    EXCEL = "excel"
    JSON = "json"


class ImportOptions(Contract):
    """Overrides for auto-detection. Leave a field unset to let the reader detect it."""

    delimiter: str | None = None
    encoding: str | None = None
    header: bool | None = None
    sheet: str | None = None
    header_row: int | None = None


class DatasetSource(Contract):
    kind: SourceKind
    file_name: str
    # Absolute path of the original file when imported from a local path (enables refresh).
    original_path: str | None = None
    options: ImportOptions = Field(default_factory=ImportOptions)


class ColumnSemantic(StrEnum):
    QUANTITATIVE = "quantitative"
    TEMPORAL = "temporal"
    NOMINAL = "nominal"
    ORDINAL = "ordinal"
    GEO = "geo"


class Column(Contract):
    name: str
    # DuckDB type name, e.g. BIGINT, DOUBLE, VARCHAR, DATE, TIMESTAMP.
    type: str
    semantic: ColumnSemantic
    null_count: int
    approx_distinct: int


class DatasetVersion(Contract):
    n: int
    # Relative to the data dir; resolved by storage/.
    parquet_path: str
    row_count: int
    columns: list[Column]
    imported_at: datetime


class Dataset(Contract):
    id: str
    name: str
    source: DatasetSource
    versions: list[DatasetVersion] = Field(default_factory=list)

    def current(self) -> DatasetVersion:
        if not self.versions:
            raise ValueError(f"dataset {self.id} has no versions")
        return self.versions[-1]


class ImportFromPathRequest(Contract):
    """Local mode only: read a file in place instead of uploading it."""

    path: str
    name: str | None = None
    kind: SourceKind | None = None
    options: ImportOptions = Field(default_factory=ImportOptions)
