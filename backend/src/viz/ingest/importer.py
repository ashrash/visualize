"""Import a source file as a new Parquet version and profile its columns.

The flow (run inside an import/refresh job):
1. `prepare_source`: take a staged upload or a local path; copy it into `sources/` (uploads
   always; path imports up to `copy_source_max_mb`; non-UTF-8 text is transcoded on the way),
   otherwise read it in place.
2. `write_version`: reader -> clean column names -> Parquet (zstd), written atomically.
3. `profile_parquet`: exact row and null counts plus approximate distinct counts, one pass.
"""

from __future__ import annotations

import codecs
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
# Encoding assumed when a file isn't valid UTF-8 and the user didn't pick one.
FALLBACK_ENCODING = "cp1252"


def resolve_text_encoding(path: Path, kind: SourceKind, options: ImportOptions) -> str | None:
    """Encoding to transcode from, or None when the file can be read as UTF-8."""
    if kind not in _TEXT_KINDS:
        return None
    if options.encoding:
        encoding = readers.check_encoding(options.encoding)
    else:
        encoding = readers.detect_encoding(readers.read_head(path))
    return None if readers.is_utf8(encoding) else encoding


class _NotUtf8(Exception):
    pass


def _copy(
    src: Path, dst: Path, on_bytes: Callable[[int], None], *, check_utf8: bool = False
) -> None:
    """Copy in chunks; with `check_utf8`, raise `_NotUtf8` at the first invalid byte."""
    copied = 0
    decoder = codecs.getincrementaldecoder("utf-8")() if check_utf8 else None
    try:
        with src.open("rb") as fin, dst.open("wb") as fout:
            while chunk := fin.read(_COPY_CHUNK):
                if decoder is not None:
                    try:
                        decoder.decode(chunk)
                    except UnicodeDecodeError as exc:
                        raise _NotUtf8 from exc
                fout.write(chunk)
                copied += len(chunk)
                on_bytes(copied)
            if decoder is not None:
                try:
                    decoder.decode(b"", final=True)
                except UnicodeDecodeError as exc:
                    raise _NotUtf8 from exc
    except PermissionError as exc:
        dst.unlink(missing_ok=True)
        raise readers.locked_file_error(src) from exc
    except BaseException:
        dst.unlink(missing_ok=True)
        raise


def _is_utf8(path: Path, on_bytes: Callable[[int], None]) -> bool:
    decoder = codecs.getincrementaldecoder("utf-8")()
    done = 0
    with path.open("rb") as fh:
        while chunk := fh.read(_COPY_CHUNK):
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError:
                return False
            done += len(chunk)
            on_bytes(done)
    try:
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


class NotUtf8Error(InvalidSpecError):
    """DuckDB found bytes that aren't UTF-8 (the file needs an explicit encoding)."""


def not_utf8_warning(encoding: str) -> str:
    return (
        f"The file isn't valid UTF-8, so it was read as {encoding}. If characters look wrong, "
        "set Encoding and refresh."
    )


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

    A staged upload is moved into `sources/<id>/` (moved back by `rollback_source` if the
    import fails; `finish_source` deletes the staged folder on success). Text that isn't
    UTF-8 is transcoded; when the user didn't set an encoding, uploads and copied files are
    checked in full while being moved or copied (cheap), so a bad byte deep in the file is
    caught here instead of failing the conversion."""
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
    encoding = resolve_text_encoding(original, resolved_kind, options)
    check_utf8 = resolved_kind in _TEXT_KINDS and encoding is None and not options.encoding
    size = original.stat().st_size
    warnings: list[str] = []

    def on_bytes(done: int) -> None:
        progress(0.3 * done / max(1, size), None)

    def transcoded(from_encoding: str) -> PreparedSource:
        progress(0.0, f"Converting text from {from_encoding} to UTF-8")
        target = storage.new_source_path(dataset_id, original.name)
        try:
            readers.transcode(original, target, from_encoding, on_chunk=on_bytes)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        opts = options.model_copy(update={"encoding": from_encoding})
        return PreparedSource(
            target, storage.rel(target), original.name, resolved_kind, opts, None, warnings
        )

    if encoding is not None:
        if not options.encoding:
            warnings.append(not_utf8_warning(encoding))
        return transcoded(encoding)
    if staged_id is not None:
        if check_utf8:
            progress(0.0, "Checking text encoding")
            if not _is_utf8(original, on_bytes):
                warnings.append(not_utf8_warning(FALLBACK_ENCODING))
                return transcoded(FALLBACK_ENCODING)
        target = storage.new_source_path(dataset_id, original.name)
        shutil.move(original, target)
        return PreparedSource(
            target, storage.rel(target), original.name, resolved_kind, options, original, warnings
        )
    if in_place_allowed and size > settings.copy_source_max_mb * 1024 * 1024:
        return PreparedSource(original, None, original.name, resolved_kind, options)
    progress(0.0, "Copying source file")
    target = storage.new_source_path(dataset_id, original.name)
    try:
        _copy(original, target, on_bytes, check_utf8=check_utf8)
    except _NotUtf8:
        warnings.append(not_utf8_warning(FALLBACK_ENCODING))
        return transcoded(FALLBACK_ENCODING)
    return PreparedSource(
        target, storage.rel(target), original.name, resolved_kind, options, None, warnings
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
