"""Grid pages and distinct values: server-side sort, filter (Predicates) and search."""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.compute as pc
from sqlglot import exp

from viz.chartdata.sql import col, lit, parquet_source, where_sql
from viz.contracts import (
    ColumnValues,
    ColumnValuesRequest,
    Dataset,
    DatasetVersion,
    Predicate,
    RowsPageMeta,
    RowsRequest,
    Scalar,
    SortKey,
    ValueCount,
)
from viz.engine import Engine, version_token
from viz.errors import InvalidSpecError, NotFoundError
from viz.ingest.search import contains_folded, dictionary_columns
from viz.storage import LocalStorage

if TYPE_CHECKING:
    from viz.ingest.views import RowViews

_TEXT_PREFIXES = ("VARCHAR", "TEXT", "STRING", "ENUM")


def pick_version(dataset: Dataset, n: int | None) -> DatasetVersion:
    if n is None:
        return dataset.current()
    for version in dataset.versions:
        if version.n == n:
            return version
    raise NotFoundError(f"dataset {dataset.name!r} has no version {n}")


def _check_fields(version: DatasetVersion, fields: list[str]) -> None:
    known = {c.name for c in version.columns}
    unknown = sorted({f for f in fields if f not in known})
    if unknown:
        raise InvalidSpecError(f"unknown column(s): {', '.join(unknown)}")


ROW_NUMBER = "file_row_number"
# Sorted/filtered/searched pages of versions at least this large use late materialization.
LATE_MIN_ROWS = 200_000
# Up to this offset + limit, an unsorted filtered page and its row count share one scan.
SHARED_SCAN_ROWS = 10_000


def order_sql(sort: list[SortKey]) -> list[exp.Ordered]:
    return [
        exp.Ordered(this=col(k.field), desc=k.descending, nulls_first=not k.nulls_last)
        for k in sort
    ]


def rownum_source(path: Path) -> exp.Expr:
    """`read_parquet(path, file_row_number := true)`: the row number as an explicit column.
    (The *virtual* column inside a window/QUALIFY crashes DuckDB 2.0.0; see CLAUDE.md §12.)"""
    return exp.Anonymous(
        this="read_parquet",
        expressions=[
            exp.Literal.string(str(path)),
            exp.PropertyEQ(this=exp.column(ROW_NUMBER), expression=exp.true()),
        ],
    )


def search_sql(
    version: DatasetVersion, search: str | None, path: Path | None = None
) -> exp.Expr | None:
    """Case- and accent-insensitive "contains" ("zur" finds "Zürich") across text
    (VARCHAR/ENUM) columns only; numbers and dates aren't searched (filter them with
    predicates). `path` (the version's Parquet file) picks the cheap form per column; see
    `viz.ingest.search`."""
    if not search:
        return None
    layout = dictionary_columns(path) if path is not None else {}
    parts: list[exp.Expr] = [
        contains_folded(c.name, search, dictionary=layout.get(c.name, True))
        for c in version.columns
        if c.type.upper().startswith(_TEXT_PREFIXES)
    ]
    return exp.or_(*parts) if parts else exp.false()


def _conditions(
    path: Path, version: DatasetVersion, filters: list[Predicate], search: str | None
) -> exp.Expr | None:
    found = [c for c in (where_sql(filters), search_sql(version, search, path)) if c is not None]
    if not found:
        return None
    return exp.and_(*(exp.Paren(this=c) for c in found))


def filtered_select(
    path: Path,
    version: DatasetVersion,
    filters: list[Predicate],
    search: str | None,
    columns: list[str] | None,
    sort: list[SortKey] | None = None,
) -> exp.Select:
    """SELECT over one version with filters and search. With `sort`, rows are ordered by the
    sort keys and then by file row number, so ties page deterministically (DuckDB's parallel
    sort isn't stable); the row number is not in the output."""
    _check_fields(
        version,
        [p.field for p in filters] + list(columns or []) + [k.field for k in sort or []],
    )
    projection: list[exp.Expr]
    if sort:
        if columns:
            projection = [col(c) for c in columns]
        else:
            projection = [exp.Star(except_=[exp.column(ROW_NUMBER)])]
        query = exp.select(*projection).from_(rownum_source(path))
    else:
        projection = [col(c) for c in columns] if columns else [exp.Star()]
        query = exp.select(*projection).from_(parquet_source(path))
    conditions = _conditions(path, version, filters, search)
    if conditions is not None:
        query = query.where(conditions)
    if sort:
        query = query.order_by(*order_sql(sort), exp.Ordered(this=exp.column(ROW_NUMBER)))
    return query


def _request_hash(payload: object) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def rows_page(
    engine: Engine,
    storage: LocalStorage,
    dataset: Dataset,
    request: RowsRequest,
    *,
    max_limit: int,
    key: str | None = None,
    views: RowViews | None = None,
    late_min_rows: int = LATE_MIN_ROWS,
) -> tuple[pa.Table, RowsPageMeta]:
    """One grid page. Sorted, filtered or searched pages of a large version that no ready
    view serves are found in two steps (`_late_page`)."""
    version = pick_version(dataset, request.version)
    path = storage.resolve(version.parquet_path)
    limit = min(request.limit, max_limit)
    _check_fields(version, [k.field for k in request.sort] + list(request.columns or []))
    if views is not None:
        served = views.page(dataset, version, request, limit=limit, key=key)
        if served is not None:
            table, total = served
            return table, _meta(request, limit, total, version)

    needs_scan = bool(request.sort or request.filters or request.search)
    if needs_scan and version.row_count >= late_min_rows:
        table, total = _late_page(engine, path, dataset, version, request, limit, key)
    else:
        query = filtered_select(
            path, version, request.filters, request.search, request.columns, request.sort
        )
        query = query.limit(limit).offset(request.offset)
        table = engine.query_arrow(query.sql(dialect="duckdb"), key=key)
        if request.filters or request.search:
            total = _count(engine, path, dataset, version, request, key)
        else:
            total = version.row_count
    if views is not None:
        full = filtered_select(path, version, request.filters, request.search, None, request.sort)
        views.ensure(dataset, version, request, full)
    return table, _meta(request, limit, total, version)


def _count_sql(path: Path, version: DatasetVersion, request: RowsRequest) -> str:
    query = exp.select(exp.alias_(exp.Count(this=exp.Star()), "n")).from_(
        exp.Subquery(this=filtered_select(path, version, request.filters, request.search, None))
    )
    return query.sql(dialect="duckdb")


def _count(
    engine: Engine,
    path: Path,
    dataset: Dataset,
    version: DatasetVersion,
    request: RowsRequest,
    key: str | None,
) -> int:
    counted = engine.query_arrow_cached(
        _count_sql(path, version, request),
        versions=[version_token(dataset.id, version.n)],
        key=key,
    )
    return int(counted.table.column(0)[0].as_py())


def _late_page(
    engine: Engine,
    path: Path,
    dataset: Dataset,
    version: DatasetVersion,
    request: RowsRequest,
    limit: int,
    key: str | None,
) -> tuple[pa.Table, int]:
    """Late materialization: find the page's row numbers reading only the sort, filter and
    search columns, then fetch just those rows. A direct `SELECT * ... ORDER BY ... LIMIT`
    decodes every column of every row (first sorted page of 10M rows ~330 ms, a deep one
    ~2.4 s); the narrow pass costs ~20 ms (first page) to ~0.3 s (a full sort for a deep
    one), and the fetch ~15-140 ms depending on how many row groups the rows span.

    An unsorted filtered/searched page near the top also takes the filtered row count from
    the same scan (`count(*)` with `min(file_row_number, n)`), instead of a second scan."""
    versions = [version_token(dataset.id, version.n)]
    conditions = _conditions(path, version, request.filters, request.search)
    rownum = exp.column(ROW_NUMBER)
    end = request.offset + limit
    total: int | None = version.row_count if conditions is None else None
    count_sql = _count_sql(path, version, request) if conditions is not None else ""
    if total is None:
        cached = engine.cache.get(versions, count_sql)
        if isinstance(cached, pa.Table):
            total = int(cached.column(0)[0].as_py())

    rows: list[int]
    if conditions is not None and not request.sort and total is None and end <= SHARED_SCAN_ROWS:
        shared = (
            exp.select(
                exp.alias_(exp.Count(this=exp.Star()), "n"),
                exp.alias_(
                    exp.Anonymous(this="min", expressions=[rownum, exp.Literal.number(end)]),
                    "rows",
                ),
            )
            .from_(rownum_source(path))
            .where(conditions)
        )
        result = engine.query_arrow(shared.sql(dialect="duckdb"), key=key)
        total = int(result.column("n")[0].as_py())
        counted = pa.table({"n": pa.array([total], pa.int64())})
        engine.cache.put(versions, count_sql, counted, nbytes=counted.nbytes)
        rows = (result.column("rows")[0].as_py() or [])[request.offset : end]
    else:
        narrow = exp.select(rownum).from_(rownum_source(path))
        if conditions is not None:
            narrow = narrow.where(conditions)
        if request.sort:
            narrow = narrow.order_by(*order_sql(request.sort), exp.Ordered(this=rownum))
        narrow = narrow.limit(limit).offset(request.offset)
        rows = engine.query_arrow(narrow.sql(dialect="duckdb"), key=key).column(0).to_pylist()
        if total is None:
            total = _count(engine, path, dataset, version, request, key)
    return _fetch_rows(engine, path, request.columns, rows, key), total


def _fetch_rows(
    engine: Engine, path: Path, columns: list[str] | None, rows: list[int], key: str | None
) -> pa.Table:
    """The rows with these file row numbers, in the order given."""
    rownum = exp.column(ROW_NUMBER)
    projection: list[exp.Expr] = [exp.Star()]
    if columns:
        projection = [*(col(c) for c in columns), rownum]
    query = exp.select(*projection).from_(rownum_source(path))
    if rows:
        wanted = [exp.Literal.number(int(n)) for n in sorted(set(rows))]
        query = query.where(exp.In(this=rownum, expressions=wanted))
    else:
        query = query.where(exp.false())
    fetched = engine.query_arrow(query.sql(dialect="duckdb"), key=key)
    if rows:
        order = pc.index_in(pa.array(rows, pa.int64()), value_set=fetched.column(ROW_NUMBER))
        fetched = fetched.take(order)
    return fetched.drop_columns([ROW_NUMBER])


def _meta(request: RowsRequest, limit: int, total: int, version: DatasetVersion) -> RowsPageMeta:
    return RowsPageMeta(
        offset=request.offset,
        limit=limit,
        total_rows=total,
        dataset_rows=version.row_count,
        version=version.n,
    )


def rows_key(dataset_id: str, request: RowsRequest) -> str:
    return f"rows:{dataset_id}:{_request_hash(request.model_dump(mode='json'))}"


def to_scalar(value: object) -> Scalar:
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    return str(value)


def column_values(
    engine: Engine,
    storage: LocalStorage,
    dataset: Dataset,
    request: ColumnValuesRequest,
    *,
    key: str | None = None,
) -> ColumnValues:
    version = dataset.current()
    path = storage.resolve(version.parquet_path)
    _check_fields(version, [request.field])
    field = col(request.field)
    query = exp.select(
        exp.alias_(field, "value", quoted=True),
        exp.alias_(exp.Count(this=exp.Star()), "count", quoted=True),
    ).from_(parquet_source(path))
    conditions: list[exp.Expr] = []
    filters = where_sql(request.filters)
    if filters is not None:
        _check_fields(version, [p.field for p in request.filters])
        conditions.append(filters)
    if request.search:
        text = exp.Lower(this=exp.Cast(this=field, to=exp.DataType.build("VARCHAR")))
        conditions.append(
            exp.Anonymous(this="contains", expressions=[text, lit(request.search.lower())])
        )
    if conditions:
        query = query.where(exp.and_(*(exp.Paren(this=c) for c in conditions)))
    query = (
        query.group_by(exp.column("value", quoted=True))
        .order_by(
            exp.Ordered(this=exp.column("count", quoted=True), desc=True),
            exp.Ordered(this=exp.column("value", quoted=True), nulls_first=False),
        )
        .limit(request.limit + 1)
    )
    result = engine.query_arrow_cached(
        query.sql(dialect="duckdb"), versions=[version_token(dataset.id, version.n)], key=key
    )
    rows = result.table.to_pylist()
    truncated = len(rows) > request.limit
    values = [
        ValueCount(value=to_scalar(r["value"]), count=int(r["count"]))
        for r in rows[: request.limit]
    ]
    return ColumnValues(field=request.field, values=values, truncated=truncated)
