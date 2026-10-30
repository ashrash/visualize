"""Staged uploads and the pre-import preview (first ~1k rows, re-run on every option change)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import BinaryIO

import duckdb
import pyarrow as pa

from viz.config import Settings
from viz.contracts import (
    ImportOptions,
    ImportPreviewMeta,
    ImportPreviewRequest,
    JsonFormat,
    SourceKind,
    StagedFile,
    new_id,
)
from viz.engine import Engine
from viz.errors import InvalidSpecError, NotFoundError, PayloadTooLargeError
from viz.ingest import readers
from viz.ingest.importer import (
    open_relation,
    profile_relation,
    read_error_message,
    resolve_text_encoding,
)
from viz.storage import LocalStorage

_COPY_CHUNK = 1024 * 1024
# How much of a non-UTF-8 file is transcoded for a preview.
_PREVIEW_TRANSCODE_BYTES = 8 * 1024 * 1024
_ESTIMATE_SAMPLE_BYTES = 1024 * 1024


def stage_upload(
    storage: LocalStorage, settings: Settings, file_name: str, stream: BinaryIO
) -> StagedFile:
    """Save an upload under sources/_staged/<id>/ with a size limit and a type sniff."""
    kind = readers.detect_kind(file_name)
    staged_id = new_id()
    target = storage.staged_path(staged_id, file_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    try:
        with target.open("wb") as out:
            while chunk := stream.read(_COPY_CHUNK):
                written += len(chunk)
                if written > limit:
                    raise PayloadTooLargeError(
                        f"file is larger than {settings.max_upload_mb} MB; import it from its path"
                    )
                out.write(chunk)
        kind = readers.sniff_kind(target, kind)
        sheets = readers.excel_sheets(target) if kind == SourceKind.EXCEL else []
    except BaseException:
        storage.delete_staged(staged_id)
        raise
    return StagedFile(
        staged_id=staged_id,
        file_name=target.name,
        kind=kind,
        size_bytes=written,
        sheets=sheets,
    )


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
    source = resolve_input(storage, settings, request.staged_id, request.path)
    kind = readers.sniff_kind(source, request.kind or readers.detect_kind(source.name))
    options = request.options
    limit = min(request.limit, settings.preview_rows)
    warnings: list[str] = []
    sheets: list[str] = []
    estimated: int | None = None
    temp: Path | None = None
    try:
        read_path = source
        encoding = resolve_text_encoding(source, kind, options)
        if encoding is not None:
            temp = storage.temp_dir / f"preview-{uuid.uuid4().hex}{source.suffix}"
            readers.transcode(source, temp, encoding, max_bytes=_PREVIEW_TRANSCODE_BYTES)
            read_path = temp
            if not options.encoding:
                warnings.append(f"The file isn't UTF-8; reading it as {encoding}.")

        with engine.cursor() as cur:
            detected = _detect(cur, read_path, kind, options, encoding)
            if kind == SourceKind.EXCEL:
                table, sheet, total = readers.read_excel(source, options, n_rows=limit)
                sheets = readers.excel_sheets(source)
                detected = detected.model_copy(update={"sheet": sheet})
                estimated = total
                relation = readers.finalize(cur.from_arrow(table), options, cast_types=True)
            else:
                relation = open_relation(cur, read_path, kind, options)
            try:
                relation = relation.limit(limit)
                table = relation.to_arrow_table()
                columns, shown = profile_relation(cur.from_arrow(table))
            except duckdb.InterruptException:
                raise
            except duckdb.Error as exc:
                raise InvalidSpecError(read_error_message(exc)) from exc
        if kind != SourceKind.EXCEL:
            estimated = _estimate_rows(
                source, shown, bool(detected.header) if kind != SourceKind.JSON else False
            )
            if kind == SourceKind.JSON and shown == 1 and not options.json_path:
                nested = [c.name for c in columns if c.type.endswith("[]")]
                if nested:
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
