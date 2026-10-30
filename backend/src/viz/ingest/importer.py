"""Import a source file as a new Parquet version and profile its columns.

The flow (run inside an import/refresh job):
1. `prepare_source`: take a staged upload or a local path; copy it into `sources/` (uploads
   always; path imports up to `copy_source_max_mb`; non-UTF-8 text is transcoded on the way),
   otherwise read it in place.
2. `write_version`: reader -> clean column names -> Parquet (zstd), written atomically.
3. `profile_parquet`: exact row and null counts plus approximate distinct counts, one pass.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from sqlglot import exp

from viz.config import Settings
from viz.contracts import Column, ColumnSemantic, DatasetVersion, ImportOptions, SourceKind
from viz.errors import InvalidSpecError, NotFoundError
from viz.ingest import readers
from viz.storage import LocalStorage

_NUMERIC_PREFIXES = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
)
_TEMPORAL_PREFIXES = ("DATE", "TIMESTAMP", "TIME")
_GEO_NAMES = {
    "lat",
    "latitude",
    "lon",
    "lng",
    "long",
    "longitude",
    "country",
    "country_code",
    "iso2",
    "iso3",
    "iso_a2",
    "iso_a3",
}
_COPY_CHUNK = 8 * 1024 * 1024

detect_kind = readers.detect_kind
Progress = Callable[[float | None, str | None], None]


def infer_semantic(name: str, duck_type: str) -> ColumnSemantic:
    if name.strip().lower() in _GEO_NAMES:
        return ColumnSemantic.GEO
    upper = duck_type.upper()
    if upper.startswith(_TEMPORAL_PREFIXES):
        return ColumnSemantic.TEMPORAL
    if upper.startswith(_NUMERIC_PREFIXES):
        return ColumnSemantic.QUANTITATIVE
    return ColumnSemantic.NOMINAL


def profile_relation(relation: duckdb.DuckDBPyRelation) -> tuple[list[Column], int]:
    """Exact row and null counts plus approximate distinct counts, in one pass."""
    names: list[str] = list(relation.columns)
    types = [str(t) for t in relation.types]
    selects: list[exp.Expr] = [exp.alias_(exp.Count(this=exp.Star()), "__rows")]
    for i, name in enumerate(names):
        column = exp.column(name, quoted=True)
        selects.append(exp.alias_(exp.Count(this=column), f"__nn{i}"))
        selects.append(
            exp.alias_(exp.Anonymous(this="approx_count_distinct", expressions=[column]), f"__d{i}")
        )
    sql = exp.select(*selects).from_("__t").sql(dialect="duckdb")
    result = relation.query("__t", sql)
    if result is None:
        raise RuntimeError("profiling query returned no result")
    stats = result.fetchall()[0]
    row_count = int(stats[0])
    columns = [
        Column(
            name=name,
            type=types[i],
            semantic=infer_semantic(name, types[i]),
            null_count=row_count - int(stats[1 + 2 * i]),
            approx_distinct=int(stats[2 + 2 * i] or 0),
        )
        for i, name in enumerate(names)
    ]
    return columns, row_count


def profile_parquet(cur: duckdb.DuckDBPyConnection, path: Path) -> tuple[list[Column], int]:
    return profile_relation(cur.read_parquet(str(path)))


@dataclass
class PreparedSource:
    # The file readers open (UTF-8 for text kinds).
    read_path: Path
    # Data-dir-relative copy, or None when read in place.
    source_rel: str | None
    file_name: str
    kind: SourceKind
    # Options with the encoding resolved (so a refresh repeats it).
    options: ImportOptions
    # Where a moved staged upload came from (moved back by `rollback_source`).
    moved_from: Path | None = None
    warnings: list[str] = field(default_factory=list)


_TEXT_KINDS = (SourceKind.CSV, SourceKind.TSV, SourceKind.JSON)
# Encoding assumed when a file isn't UTF-8 at all and the user didn't pick one.
FALLBACK_ENCODING = "cp1252"


class NotUtf8Error(InvalidSpecError):
    """DuckDB found bytes that aren't UTF-8 (the file needs an explicit encoding)."""


def not_utf8_warning(encoding: str) -> str:
    return (
        f"The file isn't valid UTF-8, so it was read as {encoding}. If characters look wrong, "
        "set Encoding and refresh."
    )


def replaced_warning(count: int) -> str:
    return (
        f"{count:,} byte{'s' if count != 1 else ''} that aren't valid UTF-8 were replaced "
        "with \ufffd. Set Encoding if the file uses another encoding."
    )


@dataclass(frozen=True)
class EncodingChoice:
    """How to read a text file: `transcode_from` None means it's clean UTF-8 as is."""

    transcode_from: str | None
    # The encoding to record in the version's options (so a refresh repeats the choice).
    recorded: str | None
    warnings: tuple[str, ...] = ()


def choose_encoding(
    path: Path,
    kind: SourceKind,
    options: ImportOptions,
    *,
    max_bytes: int | None = None,
    on_bytes: Callable[[int], None] | None = None,
) -> EncodingChoice:
    """Pick the encoding for a text file. Import scans the whole file; preview its first
    `max_bytes`. Both use the same rules, so they agree:

    - an explicit non-UTF-8 encoding is used as given;
    - a UTF-16 BOM means UTF-16;
    - clean UTF-8 is read as is;
    - mostly-UTF-8 text (valid multi-byte sequences, and no more invalid bytes than valid
      ones) or an explicit "utf-8" is transcoded as UTF-8 with the bad bytes replaced, and a
      warning counts them;
    - anything else (no valid multi-byte sequence at all) is read as cp1252.
    """
    if kind not in _TEXT_KINDS:
        return EncodingChoice(None, None)
    explicit = readers.check_encoding(options.encoding) if options.encoding else None
    if explicit is not None and not readers.is_utf8(explicit):
        return EncodingChoice(explicit, explicit)
    if readers.detect_encoding(readers.read_head(path, 4)) == "utf-16":
        warn = () if explicit else (not_utf8_warning("utf-16"),)
        return EncodingChoice("utf-16", "utf-16", warn)
    stats = readers.scan_utf8(path, max_bytes=max_bytes, on_chunk=on_bytes)
    if stats.invalid == 0:
        return EncodingChoice(None, explicit)
    if explicit is not None or (stats.multibyte > 0 and stats.invalid <= stats.multibyte):
        return EncodingChoice("utf-8", "utf-8", (replaced_warning(stats.invalid),))
    return EncodingChoice(
        FALLBACK_ENCODING, FALLBACK_ENCODING, (not_utf8_warning(FALLBACK_ENCODING),)
    )


def _copy(src: Path, dst: Path, on_bytes: Callable[[int], None]) -> None:
    copied = 0
    try:
        with src.open("rb") as fin, dst.open("wb") as fout:
            while chunk := fin.read(_COPY_CHUNK):
                fout.write(chunk)
                copied += len(chunk)
                on_bytes(copied)
    except PermissionError as exc:
        dst.unlink(missing_ok=True)
        raise readers.locked_file_error(src) from exc
    except BaseException:
        dst.unlink(missing_ok=True)
        raise


def prepare_source(
    storage: LocalStorage,
    settings: Settings,
    dataset_id: str,
    *,
    staged_id: str | None,
    path: str | None,
    kind: SourceKind | None,
    options: ImportOptions,
    progress: Progress,
) -> PreparedSource:
    """Put the source where the readers can open it. Reports progress in [0, 0.3].

    Text files are scanned in full first (`choose_encoding`), so a bad byte deep in the file
    is handled here instead of failing the conversion. A staged upload is moved into
    `sources/<id>/` (moved back by `rollback_source` if the import fails; `finish_source`
    deletes the staged folder on success)."""
    if staged_id is not None:
        original = storage.find_staged(staged_id)
        if original is None:
            raise NotFoundError("the uploaded file has expired; upload it again")
        in_place_allowed = False
    elif path is not None:
        original = Path(path).expanduser()
        if not original.is_file():
            raise NotFoundError(f"no file at {original}")
        in_place_allowed = True
    else:
        raise InvalidSpecError("give a staged upload or a path")

    resolved_kind = readers.sniff_kind(original, kind or readers.detect_kind(original.name))
    size = original.stat().st_size

    def scan_progress(done: int) -> None:
        progress(0.1 * done / max(1, size), None)

    def copy_progress(done: int) -> None:
        progress(0.1 + 0.2 * done / max(1, size), None)

    if resolved_kind in _TEXT_KINDS:
        progress(0.0, "Checking text encoding")
    try:
        choice = choose_encoding(original, resolved_kind, options, on_bytes=scan_progress)
    except PermissionError as exc:
        raise readers.locked_file_error(original) from exc
    resolved_options = options
    if choice.recorded is not None and choice.recorded != options.encoding:
        resolved_options = options.model_copy(update={"encoding": choice.recorded})
    warnings = list(choice.warnings)

    if choice.transcode_from is not None:
        progress(0.1, f"Converting text from {choice.transcode_from} to UTF-8")
        target = storage.new_source_path(dataset_id, original.name)
        try:
            readers.transcode(original, target, choice.transcode_from, on_chunk=copy_progress)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return PreparedSource(
            target,
            storage.rel(target),
            original.name,
            resolved_kind,
            resolved_options,
            None,
            warnings,
        )
    if staged_id is not None:
        target = storage.new_source_path(dataset_id, original.name)
        shutil.move(original, target)
        return PreparedSource(
            target,
            storage.rel(target),
            original.name,
            resolved_kind,
            resolved_options,
            original,
            warnings,
        )
    if in_place_allowed and size > settings.copy_source_max_mb * 1024 * 1024:
        return PreparedSource(
            original, None, original.name, resolved_kind, resolved_options, None, warnings
        )
    progress(0.1, "Copying source file")
    target = storage.new_source_path(dataset_id, original.name)
    _copy(original, target, copy_progress)
    return PreparedSource(
        target,
        storage.rel(target),
        original.name,
        resolved_kind,
        resolved_options,
        None,
        warnings,
    )


def rollback_source(storage: LocalStorage, source: PreparedSource | None) -> None:
    """Undo `prepare_source` after a failed import: move a staged upload back, or delete the
    copy made under `sources/`."""
    if source is None or source.source_rel is None:
        return
    created = storage.resolve(source.source_rel)
    if source.moved_from is not None:
        source.moved_from.parent.mkdir(parents=True, exist_ok=True)
        if created.exists():
            shutil.move(created, source.moved_from)
    else:
        created.unlink(missing_ok=True)


def finish_source(storage: LocalStorage, staged_id: str | None) -> None:
    """After a successful import: the staged upload (moved or transcoded) is no longer needed."""
    if staged_id is not None:
        storage.delete_staged(staged_id)


def open_relation(
    cur: duckdb.DuckDBPyConnection,
    path: Path,
    kind: SourceKind,
    options: ImportOptions,
    *,
    excel_rows: int | None = None,
) -> duckdb.DuckDBPyRelation:
    """A relation over the source with clean column names and type overrides applied.
    Text files must already be UTF-8."""
    try:
        if kind in (SourceKind.CSV, SourceKind.TSV):
            relation = readers.read_csv(cur, path, kind, options)
            return readers.finalize(relation, options, cast_types=False)
        if kind == SourceKind.JSON:
            relation = readers.read_json(cur, path, options)
        else:
            table, _sheet, _total = readers.read_excel(path, options, n_rows=excel_rows)
            relation = cur.from_arrow(table)
        return readers.finalize(relation, options, cast_types=True)
    except (duckdb.InterruptException, duckdb.FatalException):
        raise
    except duckdb.Error as exc:
        raise read_error(exc, path) from exc


def read_error_message(exc: Exception) -> str:
    text = str(exc).strip()
    first = text.splitlines()[0] if text else type(exc).__name__
    return f"couldn't read the file with these options: {first}"


_LOCKED_MARKERS = ("being used by another process", "permission denied", "access is denied")


def read_error(exc: Exception, path: Path) -> Exception:
    """Map a reader error to a readable domain error."""
    text = str(exc)
    lowered = text.lower()
    if "not utf-8 encoded" in lowered or "invalid unicode" in lowered:
        cause = next((ln.strip() for ln in text.splitlines() if "utf-8" in ln.lower()), "")
        return NotUtf8Error(
            "The file isn't valid UTF-8"
            + (f" ({cause})" if cause else "")
            + ". Set Encoding to the file's encoding (e.g. windows-1252) and try again."
        )
    if isinstance(exc, duckdb.IOException) and any(m in lowered for m in _LOCKED_MARKERS):
        return readers.locked_file_error(path)
    return InvalidSpecError(read_error_message(exc))


def write_version(
    cur: duckdb.DuckDBPyConnection,
    storage: LocalStorage,
    source: PreparedSource,
    dataset_id: str,
    number: int,
) -> DatasetVersion:
    """Convert the prepared source to `datasets/<id>/v<number>.parquet`, then profile it."""
    rel_path = storage.parquet_rel(dataset_id, number)
    relation = open_relation(cur, source.read_path, source.kind, source.options)
    with storage.atomic_write(rel_path) as tmp:
        try:
            relation.write_parquet(str(tmp), compression="zstd")
        except (duckdb.InterruptException, duckdb.FatalException):
            raise
        except duckdb.Error as exc:
            raise read_error(exc, source.read_path) from exc
    columns, row_count = profile_parquet(cur, storage.resolve(rel_path))
    return DatasetVersion(
        n=number,
        parquet_path=rel_path,
        row_count=row_count,
        columns=columns,
        imported_at=datetime.now(UTC),
        source_path=source.source_rel,
        options=source.options,
        size_bytes=storage.size(rel_path),
    )


def schema_changes(before: list[Column], after: list[Column]) -> list[str]:
    """Readable warnings for columns removed, added or retyped by a refresh."""
    old = {c.name: c.type for c in before}
    new = {c.name: c.type for c in after}
    warnings: list[str] = []
    removed = [n for n in old if n not in new]
    added = [n for n in new if n not in old]
    retyped = [f"{n} ({old[n]} -> {new[n]})" for n in new if n in old and old[n] != new[n]]
    if removed:
        warnings.append(f"Columns removed: {', '.join(removed)}. Charts using them will break.")
    if added:
        warnings.append(f"Columns added: {', '.join(added)}.")
    if retyped:
        warnings.append(f"Column types changed: {', '.join(retyped)}.")
    return warnings
