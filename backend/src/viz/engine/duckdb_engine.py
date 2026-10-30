"""Process-wide DuckDB instance (CLAUDE.md §6, Concurrency model).

Basics
- One in-memory DuckDB per process; data lives in Parquet files, so nothing here is durable.
- Each query gets its own cursor (a DuckDB connection is not thread-safe, and in DuckDB 2.0 a
  cursor refuses a new query while a result is open). Consume results inside the block.
- Interactive cursors (`cursor`) take a slot from a semaphore (`max_concurrent_queries`), so
  bursts queue instead of exhausting memory, and are interrupted after `query_timeout_s`.
- Background cursors (`background_cursor`) are for jobs and grid-view builds: no slot (their
  executors cap them), no timeout. They run on a second, smaller DuckDB instance
  (`job_threads`, default a quarter of the cores) so imports and builds don't starve
  dashboard queries. `job_threads=0` shares the main instance.

Keys, generations and request owners (cancelling stale work)
- `key` names a slot such as "chart:<id>". A newer query on a key supersedes the older one
  *as soon as it arrives* (before it waits for a slot): the older one is interrupted and
  fails with `QueryInterruptedError("superseded")` (HTTP 409).
- `generation` orders requests on one key. Generations must be monotonic per key. A request
  whose generation is older than the newest seen for its key is refused as superseded and
  never interrupts the newer query; an equal generation (several queries of one request)
  is allowed. Without an explicit generation, a request's *ticket* is used (see below).
  Don't mix explicit generations and tickets on one key.
- A request owner is per-request state in a contextvar. `request_scope()` (or
  `run_cancellable`, which uses it) creates one with a ticket from a process-wide monotonic
  counter taken at arrival, so a stale request can never supersede a newer one. anyio's
  `run_sync` copies the context, so cursors opened in the threadpool know their owner.
  `cancel_owner(owner)` (called on client disconnect) interrupts only that owner's cursors,
  and its later cursors refuse to start. Nothing is cancelled by key on disconnect.
- Interrupts are sticky here even though DuckDB's aren't: a cancelled cursor is
  re-interrupted every 50 ms until its block exits, so an interrupt that lands between two
  statements isn't lost. The same loop enforces the timeout.

Failure handling
- `duckdb.FatalException` invalidates a DuckDB instance. The engine then recreates it with
  the same settings; the failing and in-flight requests get `EngineBusyError` (503).

`cache` holds query results keyed by dataset versions; `pool` runs pure-Python CPU work.
"""

from __future__ import annotations

import contextlib
import itertools
import os
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pyarrow as pa
from cachetools import LRUCache

from viz.config import Settings
from viz.engine.cache import CachedTable, ResultCache
from viz.engine.pool import ProcessPool, default_blas_threads, limit_main_process_blas
from viz.errors import EngineBusyError, QueryInterruptedError

_BYTES_PER_UNIT = {
    "bytes": 1,
    "KiB": 2**10,
    "MiB": 2**20,
    "GiB": 2**30,
    "TiB": 2**40,
}
# DuckDB's default memory_limit is 80% of RAM; we default to ~60%.
_DEFAULT_MEMORY_SHARE_OF_DUCKDB_DEFAULT = 0.75
# Share of the memory budget given to the background instance.
_JOB_MEMORY_SHARE = 1 / 3
_REAPER_INTERVAL_S = 0.05
_SLOT_POLL_S = 0.05
RESTARTED = "the query engine hit an internal error and was restarted; try again"


# -- request owners -------------------------------------------------------------------------


@dataclass(eq=False)
class RequestOwner:
    """One request's identity for cancellation. `ticket` orders requests by arrival."""

    ticket: int
    cancelled: bool = False


_tickets = itertools.count(1)
_tickets_lock = threading.Lock()
_owner_var: ContextVar[RequestOwner | None] = ContextVar("viz_request_owner", default=None)


def new_owner() -> RequestOwner:
    with _tickets_lock:
        return RequestOwner(next(_tickets))


def current_owner() -> RequestOwner | None:
    return _owner_var.get()


@contextmanager
def request_scope(owner: RequestOwner | None = None) -> Iterator[RequestOwner]:
    """Run the block as one request: its cursors carry this owner and its ticket."""
    owner = owner or new_owner()
    token = _owner_var.set(owner)
    try:
        yield owner
    finally:
        _owner_var.reset(token)


# -- instances ------------------------------------------------------------------------------


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _default_limit_bytes() -> int:
    con = duckdb.connect(":memory:")
    try:
        row = con.execute("SELECT current_setting('memory_limit')").fetchone()
    finally:
        con.close()
    if row is not None:
        amount, _, unit = str(row[0]).partition(" ")
        if unit in _BYTES_PER_UNIT:
            return int(float(amount) * _BYTES_PER_UNIT[unit])
    return 2**33


def _mib(n: float) -> str:
    return f"{max(64, int(n / 2**20))}MiB"


class _Instance:
    """One in-memory DuckDB database with its settings, so it can be recreated."""

    def __init__(self, threads: int | None, memory_limit: str, temp_dir: Path) -> None:
        self.threads = threads
        self.memory_limit = memory_limit
        self.temp_dir = temp_dir
        temp_dir.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(":memory:")
        if threads:
            self.con.execute(f"SET threads = {int(threads)}")
        self.con.execute(f"SET memory_limit = {_sql_string(memory_limit)}")
        self.con.execute(f"SET temp_directory = {_sql_string(str(temp_dir))}")

    def fresh(self) -> _Instance:
        return _Instance(self.threads, self.memory_limit, self.temp_dir)

    def close(self) -> None:
        with contextlib.suppress(duckdb.Error):
            self.con.close()


@dataclass(eq=False)
class _Entry:
    key: str | None
    generation: int | None
    owner: RequestOwner | None
    cur: duckdb.DuckDBPyConnection | None = None
    instance: _Instance | None = None
    deadline: float | None = None
    cancelled: str | None = None
    started: float = field(default_factory=time.monotonic)


# -- engine ---------------------------------------------------------------------------------


class Engine:
    def __init__(
        self,
        *,
        temp_dir: Path,
        threads: int | None = None,
        memory_limit: str | None = None,
        max_concurrent_queries: int = 8,
        query_timeout_s: float = 60.0,
        cache_mb: int = 256,
        process_workers: int = 2,
        blas_threads: int | None = None,
        job_threads: int | None = 0,
        prewarm: bool = False,
    ) -> None:
        """`job_threads`: None = auto (a quarter of the cores, at least 2), 0 = share the
        main instance, n = a background instance with n threads."""
        if job_threads is None:
            job_threads = max(2, (os.cpu_count() or 4) // 4)
        if memory_limit:
            main_limit = job_limit = memory_limit
        else:
            budget = _default_limit_bytes() * _DEFAULT_MEMORY_SHARE_OF_DUCKDB_DEFAULT
            if job_threads:
                main_limit = _mib(budget * (1 - _JOB_MEMORY_SHARE))
                job_limit = _mib(budget * _JOB_MEMORY_SHARE)
            else:
                main_limit = job_limit = _mib(budget)
        self._main = _Instance(threads, main_limit, temp_dir)
        self._bg: _Instance | None = (
            _Instance(job_threads, job_limit, temp_dir / "jobs") if job_threads else None
        )

        self._slots = threading.BoundedSemaphore(max_concurrent_queries)
        self._timeout_s = query_timeout_s
        self._lock = threading.Lock()
        self._by_key: dict[str, _Entry] = {}
        self._live: set[_Entry] = set()
        self._latest_generation: LRUCache[str, int] = LRUCache(maxsize=10_000)
        self.restarts = 0

        self.cache = ResultCache(cache_mb * 2**20)
        self.pool = ProcessPool(process_workers, blas_threads=blas_threads)
        if prewarm:
            self.pool.prewarm()
        self._closed = threading.Event()
        self._reaper = threading.Thread(target=self._reap, name="viz-engine-reaper", daemon=True)
        self._reaper.start()

    @classmethod
    def from_settings(cls, settings: Settings, temp_dir: Path) -> Engine:
        limit_main_process_blas(
            settings.blas_threads or default_blas_threads(settings.process_workers)
        )
        return cls(
            temp_dir=temp_dir,
            threads=settings.duckdb_threads,
            memory_limit=settings.duckdb_memory_limit,
            max_concurrent_queries=settings.max_concurrent_queries,
            query_timeout_s=settings.query_timeout_s,
            cache_mb=settings.cache_mb,
            process_workers=settings.process_workers,
            blas_threads=settings.blas_threads,
            job_threads=settings.job_duckdb_threads,
            prewarm=settings.prewarm_process_pool,
        )

    # -- cancellation -------------------------------------------------------------------

    def _cancel_locked(self, entry: _Entry, reason: str) -> None:
        if entry.cancelled is None:
            entry.cancelled = reason
        if entry.cur is not None:
            with contextlib.suppress(duckdb.Error):  # closed in a race; nothing left to stop
                entry.cur.interrupt()

    def interrupt(self, key: str, reason: str = "cancelled") -> bool:
        """Cancel the query registered under `key`. Returns False if there is none."""
        with self._lock:
            entry = self._by_key.get(key)
            if entry is None:
                return False
            self._cancel_locked(entry, reason)
        return True

    def cancel_owner(self, owner: RequestOwner, reason: str = "cancelled") -> int:
        """Cancel every cursor of one request; its later cursors refuse to start."""
        with self._lock:
            owner.cancelled = True
            entries = [e for e in self._live if e.owner is owner]
            for entry in entries:
                self._cancel_locked(entry, reason)
        return len(entries)

    def progress(self, key: str) -> float | None:
        """Progress (0-1) of the query under `key`, or None (DuckDB 2.0 reports -1 from
        Python, see CLAUDE.md §12)."""
        with self._lock:
            entry = self._by_key.get(key)
            cur = entry.cur if entry is not None else None
            if cur is None:
                return None
            try:
                value = float(cur.query_progress())
            except duckdb.Error:
                return None
        return None if value < 0 else min(1.0, value / 100.0)

    def _reap(self) -> None:
        """Enforce timeouts and keep cancelled cursors interrupted (DuckDB interrupts that
        land between statements are otherwise lost)."""
        while not self._closed.wait(_REAPER_INTERVAL_S):
            now = time.monotonic()
            with self._lock:
                for entry in self._live:
                    if entry.cancelled is None:
                        if entry.deadline is not None and now >= entry.deadline:
                            self._cancel_locked(entry, "timed out")
                    elif entry.cur is not None:
                        self._cancel_locked(entry, entry.cancelled)

    # -- registration ---------------------------------------------------------------------

    def _register(self, key: str | None, generation: int | None) -> _Entry:
        owner = current_owner()
        if owner is not None and owner.cancelled:
            raise QueryInterruptedError("cancelled")
        if generation is None and owner is not None and key is not None:
            generation = owner.ticket
        entry = _Entry(key, generation, owner)
        with self._lock:
            if key is not None and generation is not None:
                latest = self._latest_generation.get(key)
                if latest is not None and generation < latest:
                    raise QueryInterruptedError("superseded")
                self._latest_generation[key] = generation
            if key is not None:
                previous = self._by_key.get(key)
                self._by_key[key] = entry
                if previous is not None:
                    self._cancel_locked(previous, "superseded")
            self._live.add(entry)
        return entry

    def _attach(self, entry: _Entry, instance: _Instance, timeout_s: float | None) -> None:
        with self._lock:
            if entry.cancelled is not None:
                raise QueryInterruptedError(entry.cancelled)
            try:
                cur = instance.con.cursor()
            except duckdb.FatalException:
                self._recover(instance)
                raise EngineBusyError(RESTARTED) from None
            except duckdb.ConnectionException as exc:
                if self._closed.is_set():
                    raise EngineBusyError("the query engine is shutting down") from exc
                raise
            entry.cur = cur
            entry.instance = instance
            if timeout_s is not None:
                entry.deadline = time.monotonic() + timeout_s

    def _release(self, entry: _Entry) -> None:
        with self._lock:
            self._live.discard(entry)
            if entry.key is not None and self._by_key.get(entry.key) is entry:
                del self._by_key[entry.key]
            cur, entry.cur = entry.cur, None
            if cur is not None:
                with contextlib.suppress(duckdb.Error):
                    cur.close()

    def _acquire_slot(self, entry: _Entry) -> None:
        deadline = time.monotonic() + self._timeout_s
        while not self._slots.acquire(timeout=_SLOT_POLL_S):
            if entry.cancelled is not None:
                raise QueryInterruptedError(entry.cancelled)
            if time.monotonic() >= deadline:
                raise EngineBusyError("too many queries running; try again")

    def _recover(self, instance: _Instance) -> None:
        """Replace a DuckDB instance invalidated by a fatal error. Caller may hold `_lock`."""
        if instance is self._main:
            self._main = instance.fresh()
        elif instance is self._bg:
            self._bg = instance.fresh()
        else:
            return
        self.restarts += 1
        instance.close()

    def _translate(self, entry: _Entry, exc: duckdb.Error) -> Exception | None:
        """Map a DuckDB error from inside a cursor block to our error, or None to re-raise."""
        if isinstance(exc, duckdb.InterruptException):
            return QueryInterruptedError(entry.cancelled or "interrupted")
        if isinstance(exc, duckdb.FatalException):
            with self._lock:
                if entry.instance is not None:
                    self._recover(entry.instance)
            return EngineBusyError(RESTARTED)
        with self._lock:
            stale = entry.instance is not None and entry.instance not in (self._main, self._bg)
        return EngineBusyError(RESTARTED) if stale else None

    @contextmanager
    def _run(
        self, entry: _Entry, instance: _Instance, timeout_s: float | None
    ) -> Iterator[duckdb.DuckDBPyConnection]:
        self._attach(entry, instance, timeout_s)
        assert entry.cur is not None
        try:
            yield entry.cur
        except duckdb.Error as exc:
            mapped = self._translate(entry, exc)
            if mapped is None:
                raise
            raise mapped from exc

    # -- cursors ------------------------------------------------------------------------

    @contextmanager
    def cursor(
        self, key: str | None = None, generation: int | None = None
    ) -> Iterator[duckdb.DuckDBPyConnection]:
        """A private cursor for one interactive query. Fully consume results inside the block."""
        entry = self._register(key, generation)
        acquired = False
        try:
            self._acquire_slot(entry)
            acquired = True
            with self._run(entry, self._main, self._timeout_s) as cur:
                yield cur
        finally:
            self._release(entry)
            if acquired:
                self._slots.release()

    @contextmanager
    def background_cursor(self, key: str | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
        """A cursor for job work: no query slot, no timeout, interruptible via `interrupt(key)`."""
        entry = self._register(key, None)
        try:
            with self._run(entry, self._bg or self._main, None) as cur:
                yield cur
        finally:
            self._release(entry)

    def active_interactive(self) -> int:
        """Interactive cursors currently holding or waiting for a slot."""
        with self._lock:
            return sum(1 for e in self._live if e.deadline is not None or e.cur is None)

    # -- queries ------------------------------------------------------------------------

    def query_arrow(
        self,
        sql: str,
        params: Sequence[object] | None = None,
        *,
        key: str | None = None,
        generation: int | None = None,
    ) -> pa.Table:
        with self.cursor(key, generation) as cur:
            return cur.sql(sql, params=params).to_arrow_table()

    def query_arrow_cached(
        self,
        sql: str,
        params: Sequence[object] | None = None,
        *,
        versions: Sequence[str],
        key: str | None = None,
        generation: int | None = None,
    ) -> CachedTable:
        """`query_arrow` through the result cache. `versions` must name every input version."""
        found = self.cache.get(versions, sql, params)
        if isinstance(found, pa.Table):
            return CachedTable(found, True)
        table = self.query_arrow(sql, params, key=key, generation=generation)
        self.cache.put(versions, sql, table, nbytes=table.nbytes, params=params)
        return CachedTable(table, False)

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            for entry in list(self._live):
                self._cancel_locked(entry, "cancelled")
        self.pool.close()
        if self._bg is not None:
            self._bg.close()
        self._main.close()
