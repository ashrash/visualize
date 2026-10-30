"""Narrow adapters over engine/, storage/ and jobs/ (owned by data-core).

transform and chartdata touch those modules only through these functions, so swapping in
data-core's Wave 1 APIs is a change to this file alone.

TODO(data-core): replace the stubs below once these land on main:
- `Engine.interrupt(key)` and `Engine.cursor(key, generation=n)` (see `interrupt`).
- `LocalStorage.derived_rel(key)` and `atomic_write` (see `derived_path`, `write_parquet`).
- jobs `submit(JobKind.MATERIALIZE, MaterializeParams, dedupe_key=...)` (see
  transform/service.py `materialize`).
"""

from __future__ import annotations

import hashlib
import inspect
import re
import threading
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import cast

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
    """Run SQL through the engine (semaphore, timeout, per-key interruption).

    `generation` is forwarded once the engine accepts it (an older generation then never
    interrupts a newer one); until then chartdata enforces it with slots.TicketEngine.
    """
    try:
        with open_cursor(engine, key, generation) as cur:
            return cur.sql(sql).to_arrow_table()
    except QueryInterruptedError:
        raise
    except USER_ERRORS as exc:
        raise InvalidSpecError(clean_error(exc)) from exc


def engine_takes_generation(engine: Engine) -> bool:
    """Whether `engine.cursor` accepts `generation=` (TODO(data-core): always, after merge)."""
    try:
        return "generation" in inspect.signature(engine.cursor).parameters
    except (TypeError, ValueError):
        return False


def open_cursor(
    engine: Engine, key: str | None, generation: int | None = None
) -> AbstractContextManager[duckdb.DuckDBPyConnection]:
    if generation is not None and engine_takes_generation(engine):
        opener = cast(
            Callable[..., AbstractContextManager[duckdb.DuckDBPyConnection]], engine.cursor
        )
        return opener(key, generation=generation)
    return engine.cursor(key)


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


def interrupt(engine: Engine, key: str) -> bool:
    """Interrupt the running query registered under `key`, if any."""
    method = getattr(engine, "interrupt", None)
    if callable(method):
        return bool(method(key))
    return False  # TODO(data-core): Engine.interrupt(key)


def derived_path(storage: LocalStorage, key: str) -> Path:
    """Content-addressed Parquet file for a materialized pipeline prefix."""
    method = getattr(storage, "derived_rel", None)
    rel = method(key) if callable(method) else f"derived/{key}.parquet"  # TODO(data-core)
    return storage.resolve(str(rel))


def write_parquet(engine: Engine, sql: str, target: Path, *, key: str | None = None) -> None:
    """Write a query result to `target` atomically (unique temp file, then rename).

    TODO(data-core): use storage.atomic_write and run as a JobKind.MATERIALIZE job.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with engine.cursor(key) as cur:
            cur.sql(sql).write_parquet(str(tmp))
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
