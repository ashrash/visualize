"""Narrow adapters over engine/ and storage/ (owned by data-core).

transform and chartdata touch DuckDB and the data dir only through these functions and the
stats Relation, so engine or storage changes stay local to this file.

Cancellation is the engine's: a request scope (`viz.engine.run_cancellable` in async
endpoints) gives every cursor the request's arrival ticket as its generation, so an older
request on the same key is refused ("superseded", 409) and never interrupts a newer one.
"""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path

import duckdb
import pyarrow as pa
from cachetools import LRUCache

from viz.engine import Engine
from viz.errors import InvalidSpecError, QueryInterruptedError
from viz.storage import LocalStorage

# DuckDB errors caused by the user's spec or data rather than by a bug in our SQL.
USER_ERRORS: tuple[type[Exception], ...] = (
    duckdb.BinderException,
    duckdb.ConversionException,
    duckdb.InvalidInputException,
    duckdb.OutOfRangeException,
    duckdb.ParserException,
    duckdb.CatalogException,
    duckdb.InvalidTypeException,
    duckdb.TypeMismatchException,
    duckdb.ConstraintException,
)

_LINE_INFO = re.compile(r"\n?LINE \d+:.*", re.DOTALL)
_PREFIX = re.compile(r"^[A-Za-z ]+ Error: ")


def clean_error(exc: BaseException) -> str:
    """DuckDB error text without the error-class prefix or the echoed SQL."""
    text = _LINE_INFO.sub("", str(exc)).strip()
    return _PREFIX.sub("", text).strip()


def query_arrow(
    engine: Engine, sql: str, *, key: str | None = None, generation: int | None = None
) -> pa.Table:
    """Run SQL through the engine (query slot, timeout, per-key ordering and cancellation).

    Leave `generation` None inside a request scope: the engine then uses the request's
    arrival ticket. Pass one only if it came from `viz.engine.next_generation()`.
    """
    try:
        return engine.query_arrow(sql, key=key, generation=generation)
    except QueryInterruptedError:
        raise
    except USER_ERRORS as exc:
        raise InvalidSpecError(clean_error(exc)) from exc


def sql_key(sql: str) -> str:
    """Cache key for generated SQL: a digest, so caches don't hold large query texts."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


_describe_cache: LRUCache[str, tuple[tuple[str, str], ...]] = LRUCache(maxsize=4096)
_describe_lock = threading.Lock()


def describe(engine: Engine, sql: str) -> tuple[tuple[str, str], ...]:
    """Column names and DuckDB types of a query, by binding it without running it.

    Cached by a digest of the SQL text: the text embeds versioned or content-addressed
    Parquet paths, so a given text always has the same schema. Raises the DuckDB error on a
    bind failure.
    """
    digest = sql_key(sql)
    with _describe_lock:
        hit = _describe_cache.get(digest)
    if hit is not None:
        return hit
    with engine.cursor() as cur:
        relation = cur.sql(sql)
        result = tuple(zip(relation.columns, (str(t) for t in relation.types), strict=True))
    with _describe_lock:
        _describe_cache[digest] = result
    return result


def derived_rel(storage: LocalStorage, key: str) -> str:
    """Data-dir path of the content-addressed Parquet file for a materialized prefix."""
    return storage.derived_rel(key)


def derived_path(storage: LocalStorage, key: str) -> Path:
    return storage.resolve(derived_rel(storage, key))


def write_parquet(
    cur: duckdb.DuckDBPyConnection, storage: LocalStorage, sql: str, rel: str
) -> None:
    """Write a query result to `rel` atomically (storage.atomic_write: temp file, rename)."""
    with storage.atomic_write(rel) as tmp:
        cur.sql(sql).write_parquet(str(tmp))
