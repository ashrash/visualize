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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class PreparedSource:
    # The file readers open (UTF-8 for text kinds).
    read_path: Path
    # Data-dir-relative copy, or None when read in place.
    source_rel: str | None
    file_name: str
    kind: SourceKind
    # Options with the encoding resolved (so a refresh repeats it).
    options: ImportOptions


def resolve_text_encoding(path: Path, kind: SourceKind, options: ImportOptions) -> str | None:
    """Encoding to transcode from, or None when the file can be read as UTF-8."""
    if kind not in (SourceKind.CSV, SourceKind.TSV, SourceKind.JSON):
        return None
    if options.encoding:
        encoding = readers.check_encoding(options.encoding)
    else:
        encoding = readers.detect_encoding(readers.read_head(path))
    return None if readers.is_utf8(encoding) else encoding


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
    """Put the source where the readers can open it. Reports progress in [0, 0.3]."""
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
    size = original.stat().st_size
    resolved_options = options
    if encoding is not None:
        resolved_options = options.model_copy(update={"encoding": encoding})

    def on_bytes(done: int) -> None:
        progress(0.3 * done / max(1, size), None)

    if encoding is not None:
        progress(0.0, f"Converting text from {encoding} to UTF-8")
        target = storage.new_source_path(dataset_id, original.name)
        try:
            readers.transcode(original, target, encoding, on_chunk=on_bytes)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
    elif staged_id is not None:
        target = storage.new_source_path(dataset_id, original.name)
        shutil.move(original, target)
    elif in_place_allowed and size > settings.copy_source_max_mb * 1024 * 1024:
        return PreparedSource(original, None, original.name, resolved_kind, resolved_options)
    else:
        progress(0.0, "Copying source file")
        target = storage.new_source_path(dataset_id, original.name)
        _copy(original, target, on_bytes)
    if staged_id is not None:
        storage.delete_staged(staged_id)
    return PreparedSource(
        target, storage.rel(target), original.name, resolved_kind, resolved_options
    )


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
    except duckdb.InterruptException:
        raise
    except duckdb.Error as exc:
        raise InvalidSpecError(read_error_message(exc)) from exc


def read_error_message(exc: Exception) -> str:
    first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return f"couldn't read the file with these options: {first}"


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
        except duckdb.InterruptException:
            raise
        except duckdb.Error as exc:
            raise InvalidSpecError(read_error_message(exc)) from exc
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
