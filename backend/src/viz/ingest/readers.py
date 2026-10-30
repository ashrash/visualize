"""File readers: CSV/TSV and JSON through DuckDB, Excel through fastexcel.

Every reader returns a DuckDB relation on the caller's cursor, so the importer can write it
straight to Parquet and the preview can `limit()` it. Options are passed as reader keyword
arguments or SQLGlot-built SQL; nothing user-provided is formatted into a SQL string.
"""

from __future__ import annotations

import codecs
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
from sqlglot import exp
from sqlglot.errors import ParseError

from viz.contracts import ImportOptions, JsonFormat, SourceKind
from viz.errors import ConflictError, InvalidSpecError, UnsupportedFormatError

_EXTENSIONS = {
    ".csv": SourceKind.CSV,
    ".txt": SourceKind.CSV,
    ".tsv": SourceKind.TSV,
    ".tab": SourceKind.TSV,
    ".json": SourceKind.JSON,
    ".ndjson": SourceKind.JSON,
    ".jsonl": SourceKind.JSON,
    ".xlsx": SourceKind.EXCEL,
    ".xlsm": SourceKind.EXCEL,
    ".xls": SourceKind.EXCEL,
    ".xlsb": SourceKind.EXCEL,
    ".ods": SourceKind.EXCEL,
}
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_UTF16_BOMS = (codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)
_HEAD_BYTES = 64 * 1024
# Allowed base types for `columnTypes` overrides.
_ALLOWED_TYPES = {
    "BOOLEAN",
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
    "VARCHAR",
    "DATE",
    "TIME",
    "TIMESTAMP",
    "TIMESTAMPTZ",
    "INTERVAL",
    "UUID",
}
_TYPE_ALIASES = {
    "INT": "INTEGER",
    "TEXT": "VARCHAR",
    "STRING": "VARCHAR",
    "BOOL": "BOOLEAN",
    "REAL": "FLOAT",
    "DATETIME": "TIMESTAMP",
    "NUMERIC": "DECIMAL",
    "TIMESTAMP WITH TIME ZONE": "TIMESTAMPTZ",
}
MAX_JSON_OBJECT_BYTES = 1 << 30


def detect_kind(file_name: str) -> SourceKind:
    kind = _EXTENSIONS.get(Path(file_name).suffix.lower())
    if kind is None:
        raise UnsupportedFormatError(
            f"unsupported file type: {file_name!r} (use CSV, TSV, JSON or Excel)"
        )
    return kind


def read_head(path: Path, n: int = _HEAD_BYTES) -> bytes:
    try:
        with path.open("rb") as fh:
            return fh.read(n)
    except PermissionError as exc:
        raise locked_file_error(path) from exc


def locked_file_error(path: Path) -> ConflictError:
    return ConflictError(
        f"can't read {path.name}; if it's open in Excel or another app, close it and retry"
    )


def sniff_kind(path: Path, kind: SourceKind) -> SourceKind:
    """Check the file's leading bytes against its claimed kind. Returns the kind to use."""
    head = read_head(path, 8192)
    if not head:
        raise InvalidSpecError(f"{path.name} is empty")
    is_workbook = head.startswith(_ZIP_MAGIC) or head.startswith(_OLE_MAGIC)
    if kind == SourceKind.EXCEL:
        if not is_workbook:
            raise UnsupportedFormatError(f"{path.name} doesn't look like an Excel workbook")
        return kind
    if is_workbook:
        if path.suffix.lower() in (".csv", ".tsv", ".txt", ".json"):
            raise UnsupportedFormatError(
                f"{path.name} looks like an Excel workbook or a zip file, not text"
            )
        return SourceKind.EXCEL
    if b"\x00" in head and not head.startswith(_UTF16_BOMS):
        raise UnsupportedFormatError(f"{path.name} looks like a binary file, not {kind.value}")
    if kind == SourceKind.JSON:
        text = head.lstrip(codecs.BOM_UTF8).lstrip()
        if text and text[:1] not in (b"[", b"{"):
            raise UnsupportedFormatError(f"{path.name} doesn't look like JSON")
    return kind


def detect_encoding(head: bytes) -> str:
    """'utf-8', 'utf-16' or (when the bytes aren't valid UTF-8) the Windows default 'cp1252'."""
    if head.startswith(_UTF16_BOMS):
        return "utf-16"
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        decoder.decode(head, final=False)
    except UnicodeDecodeError:
        return "cp1252"
    return "utf-8"


@dataclass(frozen=True)
class Utf8Stats:
    # Decoded multi-byte UTF-8 characters (e.g. "é" counts once).
    multibyte: int
    # Invalid byte sequences (each replaced by one U+FFFD when decoding with "replace").
    invalid: int
    scanned: int


_ASCII = bytes(range(128))
_REPLACEMENT = "\N{REPLACEMENT CHARACTER}"


def scan_utf8(
    path: Path,
    *,
    max_bytes: int | None = None,
    on_chunk: Callable[[int], None] | None = None,
) -> Utf8Stats:
    """Count decoded multi-byte UTF-8 characters vs invalid sequences in a file (or its first
    `max_bytes`). Streams in C-speed chunks; about 1 s per GB."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    chars = invalid = scanned = 0
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(4 * 1024 * 1024):
                if max_bytes is not None and scanned + len(chunk) > max_bytes:
                    chunk = chunk[: max_bytes - scanned]
                scanned += len(chunk)
                if len(chunk.translate(None, _ASCII)):
                    text = decoder.decode(chunk)
                    # Non-ASCII characters: decoded multi-byte ones plus replacement chars.
                    high = len(text) - len(text.encode("ascii", "ignore"))
                    # Replacement characters minus genuine U+FFFD already in the file.
                    replaced = max(0, text.count(_REPLACEMENT) - chunk.count(b"\xef\xbf\xbd"))
                    invalid += replaced
                    chars += high - replaced
                if on_chunk is not None:
                    on_chunk(scanned)
                if max_bytes is not None and scanned >= max_bytes:
                    break
            if max_bytes is None or scanned < max_bytes:
                invalid += decoder.decode(b"", final=True).count(_REPLACEMENT)
    except PermissionError as exc:
        raise locked_file_error(path) from exc
    return Utf8Stats(multibyte=max(0, chars), invalid=invalid, scanned=scanned)


def is_utf8(encoding: str | None) -> bool:
    return encoding is None or codecs.lookup(encoding).name in ("utf-8", "utf-8-sig")


def check_encoding(encoding: str) -> str:
    try:
        return codecs.lookup(encoding).name
    except LookupError as exc:
        raise InvalidSpecError(f"unknown encoding {encoding!r}") from exc


def transcode(
    src: Path,
    dst: Path,
    encoding: str,
    *,
    max_bytes: int | None = None,
    on_chunk: Callable[[int], None] | None = None,
) -> None:
    """Re-encode `src` to UTF-8 at `dst`, streaming. With `max_bytes`, stop near that many
    bytes at a line boundary (for previews)."""
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    read = 0
    chunk_size = 4 * 1024 * 1024
    try:
        with src.open("rb") as fin, dst.open("wb") as fout:
            while chunk := fin.read(chunk_size):
                read += len(chunk)
                text = decoder.decode(chunk)
                if max_bytes is not None and read >= max_bytes:
                    text = text[: text.rfind("\n") + 1] if "\n" in text else text
                    fout.write(text.encode("utf-8"))
                    return
                fout.write(text.encode("utf-8"))
                if on_chunk is not None:
                    on_chunk(read)
            fout.write(decoder.decode(b"", final=True).encode("utf-8"))
    except PermissionError as exc:
        raise locked_file_error(src) from exc


def normalize_type(type_name: str) -> str:
    """Validate a `columnTypes` override against the allowlist; returns DuckDB spelling."""
    try:
        parsed = exp.DataType.build(type_name, dialect="duckdb")
    except (ParseError, ValueError) as exc:
        raise InvalidSpecError(f"unknown column type {type_name!r}") from exc
    sql = parsed.sql(dialect="duckdb").upper()
    base = sql.split("(")[0].strip()
    base = _TYPE_ALIASES.get(base, base)
    if base not in _ALLOWED_TYPES:
        raise InvalidSpecError(f"column type {type_name!r} isn't supported for import")
    return sql if base == "DECIMAL" else base


# -- CSV / TSV ------------------------------------------------------------------------------


def csv_kwargs(kind: SourceKind, options: ImportOptions) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if kind == SourceKind.TSV:
        kwargs["sep"] = "\t"
    if options.delimiter:
        kwargs["sep"] = "\t" if options.delimiter in ("\\t", "tab") else options.delimiter
    if options.header is not None:
        kwargs["header"] = options.header
    if options.skip_rows:
        kwargs["skiprows"] = options.skip_rows
    if options.quote:
        kwargs["quotechar"] = options.quote
    if options.escape:
        kwargs["escapechar"] = options.escape
    if options.decimal_separator:
        kwargs["decimal"] = options.decimal_separator
    if options.null_strings:
        kwargs["na_values"] = list(options.null_strings)
    if options.date_format:
        kwargs["date_format"] = options.date_format
    if options.timestamp_format:
        kwargs["timestamp_format"] = options.timestamp_format
    if options.all_varchar:
        kwargs["all_varchar"] = True
    if options.column_types:
        kwargs["dtype"] = {k: normalize_type(v) for k, v in options.column_types.items()}
    return kwargs


def read_csv(
    cur: duckdb.DuckDBPyConnection, path: Path, kind: SourceKind, options: ImportOptions
) -> duckdb.DuckDBPyRelation:
    """`path` must be UTF-8 (the importer transcodes anything else first).

    DuckDB's sniffer rejects files that mix CRLF and LF line endings (common after editing
    on two OSes); those are retried with `strict_mode=False`, which reads them correctly."""
    kwargs = csv_kwargs(kind, options)
    try:
        return cur.read_csv(str(path), **kwargs)
    except duckdb.InvalidInputException as exc:
        if "sniffing" not in str(exc).lower():
            raise
        return cur.read_csv(str(path), **kwargs, strict_mode=False)


def sniff_csv(cur: duckdb.DuckDBPyConnection, path: Path) -> ImportOptions:
    """What DuckDB's sniffer detects on its own (for the preview's `detected`)."""
    rows = cur.sql(
        "SELECT Delimiter, Quote, Escape, HasHeader, SkipRows, DateFormat, TimestampFormat "
        "FROM sniff_csv(?)",
        params=[str(path)],
    ).fetchall()
    if not rows:
        return ImportOptions()
    delimiter, quote, escape, has_header, skip_rows, date_format, timestamp_format = rows[0]

    def clean(value: object) -> str | None:
        text = None if value is None else str(value)
        return None if text in (None, "", "(empty)") else text

    return ImportOptions(
        delimiter=clean(delimiter),
        quote=clean(quote),
        escape=clean(escape),
        header=bool(has_header),
        skip_rows=int(skip_rows or 0),
        date_format=clean(date_format),
        timestamp_format=clean(timestamp_format),
    )


# -- JSON -----------------------------------------------------------------------------------


def guess_json_format(head: bytes) -> JsonFormat:
    text = head.lstrip(codecs.BOM_UTF8).lstrip()
    if text.startswith(b"["):
        return JsonFormat.ARRAY
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) >= 2:
        try:
            first = json.loads(lines[0])
        except ValueError:
            return JsonFormat.AUTO
        if isinstance(first, dict):
            return JsonFormat.NEWLINE_DELIMITED
    return JsonFormat.AUTO


def read_json(
    cur: duckdb.DuckDBPyConnection, path: Path, options: ImportOptions
) -> duckdb.DuckDBPyRelation:
    kwargs: dict[str, Any] = {}
    if options.json_format and options.json_format != JsonFormat.AUTO:
        kwargs["format"] = options.json_format.value
    if options.date_format:
        kwargs["date_format"] = options.date_format
    if options.timestamp_format:
        kwargs["timestamp_format"] = options.timestamp_format
    if options.json_path:
        kwargs["maximum_object_size"] = MAX_JSON_OBJECT_BYTES
    relation = cur.read_json(str(path), **kwargs)
    if options.json_path:
        relation = _unnest_path(relation, options.json_path)
    return relation


def _unnest_path(relation: duckdb.DuckDBPyRelation, json_path: str) -> duckdb.DuckDBPyRelation:
    keys = [k for k in json_path.strip().strip(".").split(".") if k]
    if not keys:
        raise InvalidSpecError("JSON path is empty")
    if keys[0] not in relation.columns:
        raise InvalidSpecError(
            f"JSON path {json_path!r} not found; top-level keys are: {', '.join(relation.columns)}"
        )
    target: exp.Expr = exp.column(keys[0], quoted=True)
    for key in keys[1:]:
        target = exp.Anonymous(this="struct_extract", expressions=[target, exp.Literal.string(key)])
    inner = exp.select(exp.alias_(exp.Unnest(expressions=[target]), "__rec", quoted=True)).from_(
        "__src"
    )
    outer = exp.select(exp.Unnest(expressions=[exp.column("__rec", quoted=True)])).from_(
        exp.Subquery(this=inner)
    )
    try:
        result = relation.query("__src", outer.sql(dialect="duckdb"))
    except duckdb.Error as exc:
        raise InvalidSpecError(
            f"JSON path {json_path!r} doesn't point at a list of records"
        ) from exc
    if result is None:  # pragma: no cover - query always returns a relation for SELECT
        raise InvalidSpecError("JSON path query returned nothing")
    return result


# -- Excel ----------------------------------------------------------------------------------


def excel_sheets(path: Path) -> list[str]:
    import fastexcel

    try:
        return list(fastexcel.read_excel(str(path)).sheet_names)
    except PermissionError as exc:
        raise locked_file_error(path) from exc
    except fastexcel.FastExcelError as exc:
        raise UnsupportedFormatError(f"can't open {path.name} as a workbook: {exc}") from exc


def read_excel(
    path: Path, options: ImportOptions, *, n_rows: int | None = None
) -> tuple[pa.Table, str, int]:
    """Load one sheet as Arrow. Returns (table, sheet name, total data rows in the sheet)."""
    import fastexcel

    try:
        reader = fastexcel.read_excel(str(path))
        sheets = list(reader.sheet_names)
        sheet_name = options.sheet or (sheets[0] if sheets else None)
        if sheet_name is None:
            raise InvalidSpecError(f"{path.name} has no sheets")
        if sheet_name not in sheets:
            raise InvalidSpecError(
                f"no sheet named {sheet_name!r}; sheets are: {', '.join(sheets)}"
            )
        header_row: int | None = options.header_row if options.header_row is not None else 0
        if options.header is False:
            header_row = None
        sheet = reader.load_sheet(
            sheet_name,
            header_row=header_row,
            skip_rows=options.skip_rows or None,
            n_rows=n_rows,
            schema_sample_rows=1000,
        )
        table = sheet.to_arrow()
        # total_height counts data rows after the header, before skip_rows.
        total = max(0, int(sheet.total_height) - int(options.skip_rows or 0))
    except PermissionError as exc:
        raise locked_file_error(path) from exc
    except fastexcel.FastExcelError as exc:
        raise InvalidSpecError(f"can't read sheet from {path.name}: {exc}") from exc
    return table, sheet_name, total


# -- shared post-processing -----------------------------------------------------------------


def clean_names(names: list[str]) -> list[str]:
    """Trim names, name blank ones `column_<n>`, and make duplicates unique."""
    seen: set[str] = set()
    out: list[str] = []
    for i, raw in enumerate(names):
        name = str(raw).strip()
        if not name or name.startswith("__UNNAMED__"):
            name = f"column_{i + 1}"
        candidate, n = name, 2
        while candidate.lower() in seen:
            candidate = f"{name}_{n}"
            n += 1
        seen.add(candidate.lower())
        out.append(candidate)
    return out


def finalize(
    relation: duckdb.DuckDBPyRelation, options: ImportOptions, *, cast_types: bool
) -> duckdb.DuckDBPyRelation:
    """Rename columns to clean names and (for Excel/JSON) apply `columnTypes` with TRY_CAST."""
    names = list(relation.columns)
    cleaned = clean_names(names)
    overrides = {k: normalize_type(v) for k, v in (options.column_types or {}).items()}
    if cast_types:
        unknown = sorted(set(overrides) - set(names) - set(cleaned))
        if unknown:
            raise InvalidSpecError(f"columnTypes names unknown columns: {', '.join(unknown)}")
    if cleaned == names and not (cast_types and overrides):
        return relation
    selects: list[exp.Expr] = []
    for old, new in zip(names, cleaned, strict=True):
        column: exp.Expr = exp.column(old, quoted=True)
        target = overrides.get(new, overrides.get(old)) if cast_types else None
        if target is not None:
            column = exp.TryCast(this=column, to=exp.DataType.build(target, dialect="duckdb"))
        selects.append(exp.alias_(column, new, quoted=True))
    sql = exp.select(*selects).from_("__src").sql(dialect="duckdb")
    result = relation.query("__src", sql)
    if result is None:  # pragma: no cover
        raise InvalidSpecError("column clean-up returned nothing")
    return result
