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
- `generation` orders requests on one key. Take generations from `next_generation()`: one
  process-wide monotonic counter shared with request tickets, never reset per key, so
  explicit generations and tickets compare correctly on the same key. An explicit generation
  larger than the counter advances the counter, so later tickets are still newer. A request
  whose generation is older than the newest seen for its key is refused as superseded and
  never interrupts the newer query; an equal generation (several queries of one request) is
  allowed. Without an explicit generation, the request's ticket is used (see below).
- `cancel(key, generation)` is the conditional cancel: it acts only if `generation` is still
  the newest for the key, and it is sticky: the running query of that generation is
  interrupted and later queries of that generation on the key refuse to start. So when
  client A aborts after B was sent on the same key, A's cancel is a no-op and B runs.
- A request owner is per-request state in a contextvar. `RequestScopeMiddleware` (every HTTP
  request), `request_scope()` or `run_cancellable` creates one with a ticket from the
  process-wide counter taken at arrival, so a stale request can never supersede a newer one.
  Starlette's threadpool and anyio's `run_sync` copy the context, so cursors opened by sync
  endpoints know their owner. Never open `request_scope` in a FastAPI dependency: sync
  dependencies run in a copied context, so the owner wouldn't reach the endpoint.
  Two cursors of the same owner on one key don't supersede each other.
  `cancel_owner(owner)` (called on client disconnect) interrupts only that owner's cursors,
  and its later cursors refuse to start. Nothing is cancelled by key on disconnect.
- Interrupts are sticky here even though DuckDB's aren't: a cancelled cursor is
  re-interrupted every 50 ms until its block exits, so an interrupt that lands between two
  statements isn't lost. The same loop enforces the timeout.

Memory and disk
- An explicit `memory_limit` (or the default ~60% of RAM) is split 2/3 main, 1/3 background,
  and `max_temp_directory_size` is split the same way over 90% of the free disk, so the two
  instances together never assume more than the machine has.

Failure handling
- `duckdb.FatalException` invalidates a DuckDB instance. The engine then recreates it with
  the same settings and new cursors use the new instance; the failing request gets
  `EngineBusyError` (503). Queries already running on the old instance finish normally if
  it still works (the old instance is closed once its last cursor is released); if it is
  really dead they fail, also with 503.

`cache` holds query results keyed by dataset versions; `pool` runs pure-Python CPU work.
"""

from __future__ import annotations

import contextlib
import os
import shutil
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
# Share of the free disk DuckDB may spill to (DuckDB's own default), split between instances.
_TEMP_DISK_SHARE = 0.9
_REAPER_INTERVAL_S = 0.05
_SLOT_POLL_S = 0.05
RESTARTED = "the query engine hit an internal error and was restarted; try again"


# -- request owners -------------------------------------------------------------------------


@dataclass(eq=False)
class RequestOwner:
    """One request's identity for cancellation. `ticket` orders requests by arrival."""

    ticket: int
    cancelled: bool = False


_ticket = 0
_tickets_lock = threading.Lock()
_owner_var: ContextVar[RequestOwner | None] = ContextVar("viz_request_owner", default=None)


def next_generation() -> int:
    """A fresh value from the process-wide monotonic counter (tickets use it too)."""
    global _ticket
    with _tickets_lock:
        _ticket += 1
        return _ticket


def observe_generation(generation: int) -> None:
    """Advance the counter to at least `generation` (an explicit generation from elsewhere),
    so tickets issued afterwards are newer than it."""
    global _ticket
    with _tickets_lock:
        if generation > _ticket:
            _ticket = generation


def new_owner() -> RequestOwner:
    return RequestOwner(next_generation())


def current_owner() -> RequestOwner | None:
    return _owner_var.get()


@contextmanager
def request_scope(owner: RequestOwner | None = None) -> Iterator[RequestOwner]:
    """Run the block as one request: its cursors carry this owner and its ticket."""
    owner = owner or new_owner()
    previous = _owner_var.get()
    _owner_var.set(owner)
    try:
        yield owner
    finally:
        # `set`, not `reset(token)`: the block may end in a different Context (e.g. a
        # generator finalised elsewhere), where a token reset raises.
        _owner_var.set(previous)


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


def _limit_to_bytes(limit: str) -> int:
    """Parse a DuckDB size string ("8GB", "6GiB", "4000MB") the way DuckDB does."""
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"SET memory_limit = {_sql_string(limit)}")
        row = con.execute("SELECT current_setting('memory_limit')").fetchone()
    finally:
        con.close()
    amount, _, unit = str(row[0] if row else "").partition(" ")
    if unit not in _BYTES_PER_UNIT:
        raise ValueError(f"can't read memory limit {limit!r}")
    return int(float(amount) * _BYTES_PER_UNIT[unit])


def _free_disk_bytes(path: Path) -> int | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(path).free
    except OSError:
        return None


class _Instance:
    """One in-memory DuckDB database with its settings, so it can be recreated."""

    def __init__(
        self,
        threads: int | None,
        memory_limit: str,
        temp_dir: Path,
        max_temp_size: str | None = None,
    ) -> None:
        self.threads = threads
        self.memory_limit = memory_limit
        self.temp_dir = temp_dir
        self.max_temp_size = max_temp_size
        # Cursors attached to this instance; a retired instance closes when it reaches 0.
        self.active = 0
        self.retired = False
        temp_dir.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(":memory:")
        if threads:
            self.con.execute(f"SET threads = {int(threads)}")
        self.con.execute(f"SET memory_limit = {_sql_string(memory_limit)}")
        self.con.execute(f"SET temp_directory = {_sql_string(str(temp_dir))}")
        if max_temp_size:
            self.con.execute(f"SET max_temp_directory_size = {_sql_string(max_temp_size)}")
        # Results must not depend on the server's zone (TIMESTAMPTZ casts and comparisons).
        self.con.execute("SET GLOBAL TimeZone = 'UTC'")  # GLOBAL: cursors inherit it

    def fresh(self) -> _Instance:
        return _Instance(self.threads, self.memory_limit, self.temp_dir, self.max_temp_size)

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
    interactive: bool = False


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
            budget = float(_limit_to_bytes(memory_limit))
        else:
            budget = _default_limit_bytes() * _DEFAULT_MEMORY_SHARE_OF_DUCKDB_DEFAULT
        free = _free_disk_bytes(temp_dir)
        disk = free * _TEMP_DISK_SHARE if free else None
        if job_threads:
            main_limit = _mib(budget * (1 - _JOB_MEMORY_SHARE))
            job_limit = _mib(budget * _JOB_MEMORY_SHARE)
            main_temp = _mib(disk * (1 - _JOB_MEMORY_SHARE)) if disk else None
            job_temp = _mib(disk * _JOB_MEMORY_SHARE) if disk else None
        else:
            main_limit = job_limit = _mib(budget)
            main_temp = job_temp = _mib(disk) if disk else None
        self._main = _Instance(threads, main_limit, temp_dir, main_temp)
        self._bg: _Instance | None = (
            _Instance(job_threads, job_limit, temp_dir / "jobs", job_temp) if job_threads else None
        )

        self._slots = threading.BoundedSemaphore(max_concurrent_queries)
        self._timeout_s = query_timeout_s
        self._lock = threading.Lock()
        self._by_key: dict[str, _Entry] = {}
        self._live: set[_Entry] = set()
        self._latest_generation: LRUCache[str, int] = LRUCache(maxsize=10_000)
        # key -> generation cancelled with `cancel(key, generation)` (sticky).
        self._cancelled_generation: LRUCache[str, int] = LRUCache(maxsize=10_000)
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

    def cancel(self, key: str, generation: int, reason: str = "cancelled") -> bool:
        """Cancel the query on `key` only if it belongs to `generation` and no newer
        generation has arrived for the key. Sticky: later queries of `generation` on `key`
        refuse to start. Returns False (and does nothing) if a newer generation exists."""
        with self._lock:
            latest = self._latest_generation.get(key)
            if latest is not None and generation < latest:
                return False
            self._cancelled_generation[key] = generation
            entry = self._by_key.get(key)
            if entry is not None and entry.generation == generation:
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

    def _register(
        self, key: str | None, generation: int | None, *, interactive: bool = False
    ) -> _Entry:
        owner = current_owner()
        if generation is not None:
            observe_generation(generation)
        elif owner is not None and key is not None:
            generation = owner.ticket
        entry = _Entry(key, generation, owner, interactive=interactive)
        with self._lock:
            if owner is not None and owner.cancelled:
                raise QueryInterruptedError("cancelled")
            if key is not None and generation is not None:
                latest = self._latest_generation.get(key)
                if latest is not None and generation < latest:
                    raise QueryInterruptedError("superseded")
                if self._cancelled_generation.get(key) == generation:
                    raise QueryInterruptedError("cancelled")
                self._latest_generation[key] = generation
            if key is not None:
                previous = self._by_key.get(key)
                self._by_key[key] = entry
                same_request = (
                    previous is not None and owner is not None and previous.owner is owner
                )
                if previous is not None and not same_request:
                    self._cancel_locked(previous, "superseded")
            self._live.add(entry)
        return entry

    def _attach(self, entry: _Entry, background: bool, timeout_s: float | None) -> None:
        with self._lock:
            if entry.cancelled is not None:
                raise QueryInterruptedError(entry.cancelled)
            # Resolved under the lock, so a concurrent recovery can't hand out a retired one.
            instance = (self._bg or self._main) if background else self._main
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
            instance.active += 1
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
                instance = entry.instance
                if instance is not None:
                    instance.active -= 1
                    if instance.retired and instance.active <= 0:
                        instance.close()

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
        # In-flight cursors may still finish on it; close once the last one is released.
        instance.retired = True
        if instance.active <= 0:
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
        self, entry: _Entry, background: bool, timeout_s: float | None
    ) -> Iterator[duckdb.DuckDBPyConnection]:
        self._attach(entry, background, timeout_s)
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
        """A private cursor for one interactive query. Fully consume results inside the block.

        `generation` (optional) orders requests on `key`; take it from `next_generation()`.
        A generation the counter never issued is accepted and advances the counter to it,
        so it can't lock the key against later tickets. Without one, the current request
        owner's ticket is used."""
        entry = self._register(key, generation, interactive=True)
        acquired = False
        try:
            self._acquire_slot(entry)
            acquired = True
            with self._run(entry, False, self._timeout_s) as cur:
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
            with self._run(entry, True, None) as cur:
                yield cur
        finally:
            self._release(entry)

    def active_interactive(self) -> int:
        """Interactive cursors currently holding or waiting for a slot."""
        with self._lock:
            return sum(1 for e in self._live if e.interactive)

    def wait_interactive_idle(self, quiet_s: float = 0.3, max_wait_s: float = 10.0) -> None:
        """Block until no interactive query has run for `quiet_s` (or `max_wait_s` passes).
        Background work that can wait (grid-view builds) calls this before starting."""
        deadline = time.monotonic() + max_wait_s
        quiet_since = time.monotonic()
        while time.monotonic() < deadline and not self._closed.is_set():
            if self.active_interactive():
                quiet_since = time.monotonic()
            elif time.monotonic() - quiet_since >= quiet_s:
                return
            time.sleep(0.02)

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
