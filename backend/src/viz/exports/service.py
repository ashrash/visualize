"""CSV and Excel exports of underlying data (MVP §9), run as export jobs.

- CSV is written by DuckDB (`COPY ... TO`, via `relation.write_csv`). Values are written as
  they are: a cell starting with `=`, `+`, `-` or `@` is *not* escaped, so a spreadsheet app
  may treat it as a formula ("CSV injection"). Whether to guard against that is an open
  product decision; Excel exports below are safe either way.
- Excel is streamed through xlsxwriter in constant-memory mode from Arrow record batches.
  Strings are never turned into formulas, URLs or numbers (no formula injection). Excel holds
  at most 1,048,576 rows including the header; past that the export is truncated with a
  warning suggesting CSV.
"""

from __future__ import annotations

import datetime as dt
import decimal
import math
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
from pydantic import BaseModel
from sqlglot import exp

from viz.contracts import (
    Dataset,
    ExportFile,
    ExportFormat,
    ExportRequest,
    JobKind,
    JobResult,
)
from viz.errors import InvalidSpecError, NotImplementedYetError
from viz.ingest import catalog
from viz.ingest.rows import filtered_select, pick_version
from viz.jobs import JobContext, job_handler, job_result
from viz.storage import LocalStorage
from viz.storage.local import safe_file_name

# Excel's sheet limit is 1,048,576 rows; one is the header.
EXCEL_MAX_DATA_ROWS = 1_048_575
EXCEL_MAX_CELL_CHARS = 32_767
_BATCH_ROWS = 10_000
_EXTENSIONS = {ExportFormat.CSV: ".csv", ExportFormat.XLSX: ".xlsx"}


class ExportParams(BaseModel):
    export_id: str
    request: ExportRequest


def export_file_name(request: ExportRequest, source_name: str) -> str:
    ext = _EXTENSIONS[request.format]
    base = safe_file_name(request.file_name or source_name or "export")
    if Path(base).suffix.lower() in _EXTENSIONS.values():
        base = Path(base).stem
    return f"{base}{ext}"


def check_request(request: ExportRequest) -> None:
    """Fail fast (before a job starts) on requests the server can't run."""
    if request.source.kind != "dataset":
        # TODO(transform): resolve pipeline sources via transform's resolver
        # (relation SQL + version tokens), passing request.pipelines.
        raise NotImplementedYetError("exporting a pipeline's output isn't available yet")
    if request.delimiter is not None and len(request.delimiter) != 1:
        raise InvalidSpecError("the CSV delimiter must be a single character")


def source_query(storage: LocalStorage, dataset: Dataset, request: ExportRequest) -> exp.Select:
    version = pick_version(dataset, request.version)
    path = storage.resolve(version.parquet_path)
    return filtered_select(path, version, request.filters, None, request.columns, request.sort)


def write_csv(cur: duckdb.DuckDBPyConnection, sql: str, out: Path, delimiter: str | None) -> None:
    cur.sql(sql).write_csv(str(out), sep=delimiter or ",", header=True)


_SHEET_FORBIDDEN = re.compile(r"[\[\]:*?/\\]")


def excel_sheet_name(name: str) -> str:
    """A valid worksheet name: no `[]:*?/\\`, no leading/trailing apostrophe, <= 31 chars,
    not the reserved "History"."""
    cleaned = _SHEET_FORBIDDEN.sub(" ", name).strip().strip("'").strip()
    cleaned = cleaned[:31].strip().strip("'").strip()
    if not cleaned or cleaned.lower() == "history":
        return "Data"
    return cleaned


def _cell_writer(field: pa.Field, formats: dict[str, Any]) -> tuple[str, Any]:
    """(xlsxwriter method name, cell format) for an Arrow column type."""
    t = field.type
    if pa.types.is_timestamp(t):
        return "write_datetime", formats["datetime"]
    if pa.types.is_date(t):
        return "write_datetime", formats["date"]
    if pa.types.is_time(t):
        return "write_datetime", formats["time"]
    if pa.types.is_duration(t):
        return "write_datetime", formats["duration"]
    if pa.types.is_boolean(t):
        return "write_boolean", None
    if pa.types.is_integer(t) or pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "write_number", None
    return "write_string", None


def write_xlsx(
    reader: pa.RecordBatchReader,
    out: Path,
    *,
    on_rows: Callable[[int], None],
    sheet_name: str = "Data",
) -> list[str]:
    """Stream batches into a workbook. Returns warnings."""
    import xlsxwriter

    warnings: list[str] = []
    truncated_cells = 0
    book = xlsxwriter.Workbook(
        str(out),
        {
            "constant_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
            "strings_to_numbers": False,
            "nan_inf_to_errors": True,
            "remove_timezone": True,
        },
    )
    try:
        sheet = book.add_worksheet(excel_sheet_name(sheet_name))
        header = book.add_format({"bold": True})
        formats = {
            "datetime": book.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"}),
            "date": book.add_format({"num_format": "yyyy-mm-dd"}),
            "time": book.add_format({"num_format": "hh:mm:ss"}),
            "duration": book.add_format({"num_format": "[h]:mm:ss"}),
        }
        schema = reader.schema
        sheet.write_row(0, 0, schema.names, header)
        writers = [_cell_writer(f, formats) for f in schema]
        methods = [(getattr(sheet, name), name, fmt) for name, fmt in writers]
        row = 1
        for batch in reader:
            columns = [batch.column(i).to_pylist() for i in range(batch.num_columns)]
            for values in zip(*columns, strict=True):
                for c, value in enumerate(values):
                    if value is None:
                        continue
                    method, name, fmt = methods[c]
                    if name == "write_number":
                        if isinstance(value, decimal.Decimal):
                            value = float(value)
                        elif isinstance(value, float) and math.isnan(value):
                            continue
                    elif name == "write_string":
                        if not isinstance(value, str):
                            value = str(value)
                        if len(value) > EXCEL_MAX_CELL_CHARS:
                            value = value[:EXCEL_MAX_CELL_CHARS]
                            truncated_cells += 1
                    elif isinstance(value, dt.time):
                        value = value.replace(tzinfo=None)
                    if fmt is None:
                        method(row, c, value)
                    else:
                        method(row, c, value, fmt)
                row += 1
            on_rows(row - 1)
        sheet.freeze_panes(1, 0)
    finally:
        book.close()
    if truncated_cells:
        warnings.append(
            f"{truncated_cells:,} text cells were longer than Excel's 32,767-character limit "
            "and were cut short."
        )
    return warnings


@job_handler(JobKind.EXPORT, ExportParams)
def run_export(ctx: JobContext, params: ExportParams) -> JobResult:
    request = params.request
    check_request(request)
    with ctx.session() as db:
        dataset = catalog.get_dataset(db, request.source.id)
    query = source_query(ctx.storage, dataset, request)
    sql = query.sql(dialect="duckdb")
    file_name = export_file_name(request, dataset.name)
    out = ctx.storage.export_path(params.export_id, file_name)
    tmp = out.with_name(out.name + ".tmp")
    warnings: list[str] = []

    ctx.progress(0.0, "Counting rows")
    count_sql = exp.select(exp.Count(this=exp.Star())).from_(exp.Subquery(this=query.copy()))
    try:
        with ctx.cursor() as cur:
            total = int(cur.sql(count_sql.sql(dialect="duckdb")).fetchall()[0][0])
        if request.format == ExportFormat.CSV:
            ctx.progress(0.05, "Writing CSV")
            with ctx.cursor() as cur:
                write_csv(cur, sql, tmp, request.delimiter)
            written = total
        else:
            written = min(total, EXCEL_MAX_DATA_ROWS)
            if total > EXCEL_MAX_DATA_ROWS:
                warnings.append(
                    f"Excel holds at most {EXCEL_MAX_DATA_ROWS:,} rows, so the first "
                    f"{EXCEL_MAX_DATA_ROWS:,} of {total:,} rows were exported. Export as CSV "
                    "to get every row."
                )
                sql = query.copy().limit(EXCEL_MAX_DATA_ROWS).sql(dialect="duckdb")
            ctx.progress(0.05, "Writing Excel workbook")

            def on_rows(done: int) -> None:
                ctx.progress(0.05 + 0.95 * done / max(1, written), None)

            with ctx.cursor() as cur:
                reader = cur.sql(sql).to_arrow_reader(_BATCH_ROWS)
                warnings += write_xlsx(reader, tmp, on_rows=on_rows, sheet_name=dataset.name)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)

    expires = datetime.now(UTC) + timedelta(seconds=ctx.settings.export_retention_s)
    export = ExportFile(
        export_id=params.export_id,
        file_name=out.name,
        format=request.format,
        size_bytes=out.stat().st_size,
        row_count=written,
        download_url=ctx.storage.download_url(params.export_id),
        expires_at=expires,
    )
    return job_result(dataset_id=dataset.id, export=export, warnings=warnings)
