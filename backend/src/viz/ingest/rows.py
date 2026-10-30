"""Grid pages and distinct values: server-side sort, filter (Predicates) and search."""

from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pyarrow as pa
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


def search_sql(version: DatasetVersion, search: str | None) -> exp.Expr | None:
    """Case-insensitive "contains" across text (VARCHAR/ENUM) columns only; numbers and dates
    aren't searched (filter them with predicates)."""
    if not search:
        return None
    needle = exp.Lower(this=lit(search))
    parts: list[exp.Expr] = [
        exp.Anonymous(
            this="contains",
            expressions=[exp.Lower(this=col(c.name)), needle],
        )
        for c in version.columns
        if c.type.upper().startswith(_TEXT_PREFIXES)
    ]
    return exp.or_(*parts) if parts else exp.false()


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
    conditions = [c for c in (where_sql(filters), search_sql(version, search)) if c is not None]
    if conditions:
        query = query.where(exp.and_(*(exp.Paren(this=c) for c in conditions)))
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
) -> tuple[pa.Table, RowsPageMeta]:
    version = pick_version(dataset, request.version)
    path = storage.resolve(version.parquet_path)
    limit = min(request.limit, max_limit)
    _check_fields(version, [k.field for k in request.sort] + list(request.columns or []))
    if views is not None:
        served = views.page(dataset, version, request, limit=limit, key=key)
        if served is not None:
            table, total = served
            return table, _meta(request, limit, total, version)

    query = filtered_select(
        path, version, request.filters, request.search, request.columns, request.sort
    )
    query = query.limit(limit).offset(request.offset)
    table = engine.query_arrow(query.sql(dialect="duckdb"), key=key)

    if request.filters or request.search:
        count_query = exp.select(exp.alias_(exp.Count(this=exp.Star()), "n")).from_(
            exp.Subquery(this=filtered_select(path, version, request.filters, request.search, None))
        )
        counted = engine.query_arrow_cached(
            count_query.sql(dialect="duckdb"),
            versions=[version_token(dataset.id, version.n)],
            key=key,
        )
        total = int(counted.table.column(0)[0].as_py())
    else:
        total = version.row_count
    if views is not None:
        full = filtered_select(path, version, request.filters, request.search, None, request.sort)
        views.ensure(dataset, version, request, full)
    return table, _meta(request, limit, total, version)


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
