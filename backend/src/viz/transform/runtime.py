"""Narrow adapters over engine/, storage/ and jobs/ (owned by data-core).

transform and chartdata touch those modules only through these functions, so swapping in
data-core's Wave 1 APIs is a change to this file alone.

TODO(data-core): replace the stubs below once these land on main:
- `Engine.interrupt(key)` and `Engine.cursor(key, generation=n)` (see `interrupt`).
- `LocalStorage.derived_rel(key)` and `atomic_write` (see `derived_path`, `write_parquet`).
- jobs `submit(JobKind.MATERIALIZE, MaterializeParams, dedupe_key=...)` (see materialize.py).
"""

from __future__ import annotations

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


def query_arrow(engine: Engine, sql: str, *, key: str | None = None) -> pa.Table:
    """Run SQL through the engine (semaphore, timeout, per-key interruption)."""
    try:
        return engine.query_arrow(sql, key=key)
    except QueryInterruptedError:
        raise
    except USER_ERRORS as exc:
        raise InvalidSpecError(clean_error(exc)) from exc


_describe_cache: LRUCache[str, tuple[tuple[str, str], ...]] = LRUCache(maxsize=4096)
_describe_lock = threading.Lock()


def describe(engine: Engine, sql: str) -> tuple[tuple[str, str], ...]:
    """Column names and DuckDB types of a query, by binding it without running it.

    Cached by SQL text: the text embeds versioned or content-addressed Parquet paths, so a
    given text always has the same schema. Raises the DuckDB error on a bind failure.
    """
    with _describe_lock:
        hit = _describe_cache.get(sql)
    if hit is not None:
        return hit
    with engine.cursor() as cur:
        relation = cur.sql(sql)
        result = tuple(zip(relation.columns, (str(t) for t in relation.types), strict=True))
    with _describe_lock:
        _describe_cache[sql] = result
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
    """Write a query result to `target` atomically (temp file, then rename)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    try:
        with engine.cursor(key) as cur:
            cur.sql(sql).write_parquet(str(tmp))
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
