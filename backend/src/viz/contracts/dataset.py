"""Datasets: an imported source plus its immutable Parquet versions."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, model_validator

from viz.contracts.base import Contract, Scalar
from viz.contracts.predicate import Predicate


class SourceKind(StrEnum):
    CSV = "csv"
    TSV = "tsv"
    EXCEL = "excel"
    JSON = "json"


class JsonFormat(StrEnum):
    AUTO = "auto"
    ARRAY = "array"
    NEWLINE_DELIMITED = "newline_delimited"


class ImportOptions(Contract):
    """Overrides for auto-detection. Leave a field unset to let the reader detect it."""

    delimiter: str | None = None
    encoding: str | None = None
    header: bool | None = None
    sheet: str | None = None
    header_row: int | None = None
    # CSV: lines before the header. Excel: rows after header_row.
    skip_rows: int | None = None
    quote: str | None = None
    escape: str | None = None
    # "." or ",".
    decimal_separator: str | None = None
    null_strings: list[str] | None = None
    # strptime formats, e.g. "%d/%m/%Y".
    date_format: str | None = None
    timestamp_format: str | None = None
    # Column -> DuckDB type name; checked against an allowlist.
    column_types: dict[str, str] | None = None
    all_varchar: bool | None = None
    json_format: JsonFormat | None = None
    # Dotted key path to the records array, e.g. "data.items".
    json_path: str | None = None


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
    # Data-dir-relative copy this version was built from (None when read in place).
    source_path: str | None = None
    # Options used to build this version.
    options: ImportOptions | None = None
    # Parquet file size.
    size_bytes: int | None = None


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


def _exactly_one_source(staged_id: str | None, path: str | None) -> None:
    if (staged_id is None) == (path is None):
        raise ValueError("give exactly one of stagedId or path")


class StagedFile(Contract):
    """An uploaded file waiting to be previewed and imported. Server-produced."""

    staged_id: str
    file_name: str
    kind: SourceKind
    size_bytes: int
    # Excel sheet names; empty for other kinds.
    sheets: list[str]


class ImportPreviewRequest(Contract):
    staged_id: str | None = None
    path: str | None = None
    kind: SourceKind | None = None
    options: ImportOptions = Field(default_factory=ImportOptions)
    limit: int = Field(default=1000, ge=1, le=1000)

    @model_validator(mode="after")
    def _one_source(self) -> ImportPreviewRequest:
        _exactly_one_source(self.staged_id, self.path)
        return self


class ImportPreviewMeta(Contract):
    """Arrow metadata of a preview response. Server-produced."""

    kind: SourceKind
    # What auto-detection chose (delimiter, header, quote, formats, sheet).
    detected: ImportOptions
    sheets: list[str]
    # Profiled on the preview rows only.
    columns: list[Column]
    rows_shown: int
    # Exact for Excel; a byte-based estimate for CSV/JSON.
    estimated_rows: int | None
    warnings: list[str]


class ImportRequest(Contract):
    staged_id: str | None = None
    path: str | None = None
    name: str | None = None
    kind: SourceKind | None = None
    options: ImportOptions = Field(default_factory=ImportOptions)

    @model_validator(mode="after")
    def _one_source(self) -> ImportRequest:
        _exactly_one_source(self.staged_id, self.path)
        return self


class RefreshRequest(Contract):
    # A re-upload; omit to re-read source.originalPath.
    staged_id: str | None = None
    # Omit to reuse the current version's options.
    options: ImportOptions | None = None


class DatasetUpdate(Contract):
    name: str | None = None


class SortKey(Contract):
    field: str
    desc: bool = False


class RowsRequest(Contract):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1)
    sort: list[SortKey] = Field(default_factory=list)
    filters: list[Predicate] = Field(default_factory=list)
    # Case-insensitive "contains" across text columns.
    search: str | None = None
    columns: list[str] | None = None
    # Default: the current version.
    version: int | None = None


class ColumnValuesRequest(Contract):
    field: str
    search: str | None = None
    filters: list[Predicate] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=1000)


class ValueCount(Contract):
    value: Scalar
    count: int


class ColumnValues(Contract):
    """Distinct values with counts, for filter dropdowns. Server-produced."""

    field: str
    values: list[ValueCount]
    truncated: bool
