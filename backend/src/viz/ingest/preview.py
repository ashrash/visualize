"""Staged uploads and the pre-import preview (first ~1k rows, re-run on every option change)."""

from __future__ import annotations

import contextlib
import uuid
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.compute as pc

from viz.config import Settings
from viz.contracts import (
    ImportOptions,
    ImportPreviewMeta,
    ImportPreviewRequest,
    JsonFormat,
    SourceKind,
    StagedFile,
)
from viz.engine import Engine
from viz.errors import ConflictError, InvalidSpecError, NotFoundError
from viz.ingest import readers
from viz.ingest.importer import (
    EncodingChoice,
    NotUtf8Error,
    choose_encoding,
    open_relation,
    profile_relation,
    read_error,
    shape_warnings,
)
from viz.ingest.upload import Upload
from viz.storage import LocalStorage

# How much of a non-UTF-8 file is transcoded for a preview.
_PREVIEW_TRANSCODE_BYTES = 8 * 1024 * 1024
_ESTIMATE_SAMPLE_BYTES = 1024 * 1024


def finish_staging(
    storage: LocalStorage, upload: Upload, kind: SourceKind | None = None
) -> StagedFile:
    """Sniff a streamed upload's type and list Excel sheets; deletes it if it's unusable."""
    try:
        chosen = kind
        kind = readers.sniff_kind(upload.path, readers.detect_kind(upload.file_name, kind))
        sheets = readers.excel_sheets(upload.path) if kind == SourceKind.EXCEL else []
        if chosen is not None:
            storage.set_staged_kind(upload.staged_id, kind.value)
    except BaseException:
        with contextlib.suppress(ConflictError):  # don't mask the real error
            storage.delete_staged(upload.staged_id)
        raise
    return StagedFile(
        staged_id=upload.staged_id,
        file_name=upload.file_name,
        kind=kind,
        size_bytes=upload.size,
        sheets=sheets,
    )


def staged_kind(storage: LocalStorage, staged_id: str | None) -> SourceKind | None:
    """The kind chosen when the file was staged (`?kind=`), if any."""
    value = storage.staged_kind(staged_id) if staged_id else None
    return SourceKind(value) if value else None


def resolve_input(
    storage: LocalStorage, settings: Settings, staged_id: str | None, path: str | None
) -> Path:
    if staged_id is not None:
        found = storage.find_staged(staged_id)
        if found is None:
            raise NotFoundError("the uploaded file has expired; upload it again")
        return found
    if not settings.allow_path_import:
        raise InvalidSpecError("importing from a path is disabled on this server")
    original = Path(path or "").expanduser()
    if not original.is_file():
        raise NotFoundError(f"no file at {original}")
    return original


def _estimate_rows(path: Path, shown: int, header: bool) -> int | None:
    """Rows in a text file, extrapolated from the line length of its first megabyte."""
    size = path.stat().st_size
    head = readers.read_head(path, _ESTIMATE_SAMPLE_BYTES)
    lines = head.count(b"\n")
    if lines == 0:
        return shown
    if size <= len(head):
        if not head.endswith(b"\n"):
            lines += 1
        return max(shown, lines - (1 if header else 0))
    return max(shown, int(size / (len(head) / lines)) - (1 if header else 0))


def preview(
    engine: Engine, storage: LocalStorage, settings: Settings, request: ImportPreviewRequest
) -> tuple[pa.Table, ImportPreviewMeta]:
    """First rows with the given options. The encoding is chosen by the same rules as the
    import (`choose_encoding`) over the first 8 MB, so preview and import agree. If DuckDB
    still finds invalid UTF-8 (it shouldn't), the preview is retried with replacement."""
    try:
        return _preview(engine, storage, settings, request, None)
    except NotUtf8Error:
        return _preview(engine, storage, settings, request, "utf-8")


def _preview(
    engine: Engine,
    storage: LocalStorage,
    settings: Settings,
    request: ImportPreviewRequest,
    encoding_override: str | None,
) -> tuple[pa.Table, ImportPreviewMeta]:
    source = resolve_input(storage, settings, request.staged_id, request.path)
    kind = readers.sniff_kind(
        source,
        request.kind or staged_kind(storage, request.staged_id) or readers.detect_kind(source.name),
    )
    options = request.options
    limit = min(request.limit, settings.preview_rows)
    warnings: list[str] = []
    sheets: list[str] = []
    estimated: int | None = None
    temp: Path | None = None
    try:
        read_path = source
        if encoding_override is not None:
            choice = EncodingChoice(encoding_override, encoding_override)
        else:
            choice = choose_encoding(source, kind, options, max_bytes=_PREVIEW_TRANSCODE_BYTES)
        encoding = choice.recorded
        warnings.extend(choice.warnings)
        if choice.transcode_from is not None:
            temp = storage.temp_dir / f"preview-{uuid.uuid4().hex}{source.suffix}"
            readers.transcode(
                source, temp, choice.transcode_from, max_bytes=_PREVIEW_TRANSCODE_BYTES
            )
            read_path = temp

        excel_table: pa.Table | None = None
        if kind == SourceKind.EXCEL:
            # Parse the sheet before taking an interactive query slot.
            excel_table, sheet, estimated = readers.read_excel(source, options, n_rows=limit)
            sheets = readers.excel_sheets(source)
        with engine.cursor() as cur:
            detected = _detect(cur, read_path, kind, options, encoding)
            if excel_table is not None:
                detected = detected.model_copy(update={"sheet": sheet})
                relation = readers.finalize(cur.from_arrow(excel_table), options, cast_types=True)
            else:
                relation = open_relation(cur, read_path, kind, options)
            try:
                relation = relation.limit(limit)
                table = relation.to_arrow_table()
                columns, shown = profile_relation(cur.from_arrow(table))
                nested = list_paths(relation) if kind == SourceKind.JSON else []
            except (duckdb.InterruptException, duckdb.FatalException):
                raise
            except duckdb.Error as exc:
                raise read_error(exc, read_path) from exc
        if kind != SourceKind.EXCEL:
            estimated = _estimate_rows(
                source, shown, bool(detected.header) if kind != SourceKind.JSON else False
            )
            median = None
            if table.num_columns == 1 and pa.types.is_string(table.schema.field(0).type):
                lengths = pc.utf8_length(table.column(0))
                median_value = pc.approximate_median(lengths).as_py()
                median = None if median_value is None else float(median_value)
            warnings.extend(shape_warnings(kind, columns, shown, median))
            if kind == SourceKind.JSON and shown == 1 and not options.json_path and nested:
                warnings.append(
                    "This looks like a single JSON object. To import a list inside it, "
                    f"set the JSON path to one of: {', '.join(nested)}."
                )
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)

    meta = ImportPreviewMeta(
        kind=kind,
        detected=detected,
        sheets=sheets,
        columns=columns,
        rows_shown=shown,
        estimated_rows=estimated,
        warnings=warnings,
    )
    return table, meta


def list_paths(relation: duckdb.DuckDBPyRelation, limit: int = 8) -> list[str]:
    """Dotted paths to lists of records inside the relation's (struct) columns, for the
    "set the JSON path" hint, e.g. ["data.items", "rows"]."""
    found: list[str] = []

    def walk(dtype: duckdb.sqltypes.DuckDBPyType, path: str) -> None:
        if len(found) >= limit:
            return
        kind = dtype.id
        if kind == "list":
            found.append(path)
        elif kind == "struct":
            for name, child in dtype.children:
                if isinstance(child, duckdb.sqltypes.DuckDBPyType):
                    walk(child, f"{path}.{name}")

    for name, dtype in zip(relation.columns, relation.types, strict=True):
        walk(dtype, name)
    return found


def _detect(
    cur: duckdb.DuckDBPyConnection,
    path: Path,
    kind: SourceKind,
    options: ImportOptions,
    encoding: str | None,
) -> ImportOptions:
    """Auto-detected options, overridden by anything the user set explicitly."""
    detected = ImportOptions()
    if kind in (SourceKind.CSV, SourceKind.TSV):
        try:
            detected = readers.sniff_csv(cur, path)
        except duckdb.Error:
            detected = ImportOptions()
    elif kind == SourceKind.JSON:
        detected = ImportOptions(json_format=readers.guess_json_format(readers.read_head(path)))
    elif kind == SourceKind.EXCEL:
        detected = ImportOptions(header=True, header_row=0)
    merged = detected.model_dump(by_alias=False)
    merged["encoding"] = (encoding or "utf-8") if kind != SourceKind.EXCEL else None
    for name, value in options.model_dump(by_alias=False, exclude_none=True).items():
        merged[name] = value
    if kind == SourceKind.JSON and merged.get("json_format") is None:
        merged["json_format"] = JsonFormat.AUTO
    return ImportOptions.model_validate(merged)
