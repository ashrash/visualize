"""Import a source file as a new Parquet version and profile its columns.

Phase 0 handles CSV, TSV and JSON. Excel (fastexcel), refresh and import jobs with progress
arrive in Wave 1 (data-core).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlglot import exp

from viz.contracts import Column, ColumnSemantic, Dataset, DatasetVersion, SourceKind
from viz.engine import Engine
from viz.errors import UnsupportedFormatError
from viz.storage import LocalStorage

_EXTENSIONS = {
    ".csv": SourceKind.CSV,
    ".tsv": SourceKind.TSV,
    ".tab": SourceKind.TSV,
    ".json": SourceKind.JSON,
    ".ndjson": SourceKind.JSON,
    ".jsonl": SourceKind.JSON,
    ".xlsx": SourceKind.EXCEL,
    ".xls": SourceKind.EXCEL,
    ".xlsb": SourceKind.EXCEL,
    ".ods": SourceKind.EXCEL,
}

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


def detect_kind(file_name: str) -> SourceKind:
    kind = _EXTENSIONS.get(Path(file_name).suffix.lower())
    if kind is None:
        raise UnsupportedFormatError(
            f"unsupported file type: {file_name!r} (use CSV, TSV, JSON or Excel)"
        )
    return kind


def infer_semantic(name: str, duck_type: str) -> ColumnSemantic:
    if name.strip().lower() in _GEO_NAMES:
        return ColumnSemantic.GEO
    upper = duck_type.upper()
    if upper.startswith(_TEMPORAL_PREFIXES):
        return ColumnSemantic.TEMPORAL
    if upper.startswith(_NUMERIC_PREFIXES):
        return ColumnSemantic.QUANTITATIVE
    return ColumnSemantic.NOMINAL


def _csv_kwargs(dataset: Dataset) -> dict[str, Any]:
    options = dataset.source.options
    kwargs: dict[str, Any] = {}
    if dataset.source.kind == SourceKind.TSV:
        kwargs["sep"] = "\t"
    if options.delimiter:
        kwargs["sep"] = options.delimiter
    if options.header is not None:
        kwargs["header"] = options.header
    if options.encoding:
        kwargs["encoding"] = options.encoding
    return kwargs


def profile_parquet(engine: Engine, path: Path) -> tuple[list[Column], int]:
    """Exact row and null counts plus approximate distinct counts, in one pass."""
    with engine.cursor() as cur:
        relation = cur.read_parquet(str(path))
        names: list[str] = list(relation.columns)
        types = [str(t) for t in relation.types]
        selects: list[exp.Expr] = [exp.alias_(exp.Count(this=exp.Star()), "__rows")]
        for i, name in enumerate(names):
            column = exp.column(name, quoted=True)
            selects.append(exp.alias_(exp.Count(this=column), f"__nn{i}"))
            selects.append(
                exp.alias_(
                    exp.Anonymous(this="approx_count_distinct", expressions=[column]), f"__d{i}"
                )
            )
        sql = exp.select(*selects).from_("t").sql(dialect="duckdb")
        result = relation.query("t", sql)
        if result is None:
            raise RuntimeError("profiling query returned no result")
        rows = result.fetchall()
    stats = rows[0]
    row_count = int(stats[0])
    columns = [
        Column(
            name=name,
            type=types[i],
            semantic=infer_semantic(name, types[i]),
            null_count=row_count - int(stats[1 + 2 * i]),
            approx_distinct=int(stats[2 + 2 * i]),
        )
        for i, name in enumerate(names)
    ]
    return columns, row_count


def import_version(
    engine: Engine, storage: LocalStorage, dataset: Dataset, source_file: Path
) -> DatasetVersion:
    """Convert `source_file` to the dataset's next Parquet version. Never overwrites."""
    number = len(dataset.versions) + 1
    rel_path = storage.parquet_rel(dataset.id, number)
    out = storage.resolve(rel_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    kind = dataset.source.kind
    with engine.cursor() as cur:
        if kind in (SourceKind.CSV, SourceKind.TSV):
            relation = cur.read_csv(str(source_file), **_csv_kwargs(dataset))
        elif kind == SourceKind.JSON:
            relation = cur.read_json(str(source_file))
        else:
            raise UnsupportedFormatError("Excel import is not available yet")
        relation.write_parquet(str(out), compression="zstd")

    columns, row_count = profile_parquet(engine, out)
    return DatasetVersion(
        n=number,
        parquet_path=rel_path,
        row_count=row_count,
        columns=columns,
        imported_at=datetime.now(UTC),
    )
