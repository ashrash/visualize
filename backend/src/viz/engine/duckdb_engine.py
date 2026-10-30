"""Process-wide DuckDB instance (CLAUDE.md §6, Concurrency model).

- One in-memory DuckDB per process; data lives in Parquet files, so nothing here is durable.
- Each query gets its own cursor (a DuckDB connection is not thread-safe, and in DuckDB 2.0 a
  cursor refuses a new query while a result is open).
- Interactive cursors (`cursor`) take a slot from a semaphore, so bursts queue instead of
  exhausting memory, and are interrupted after the query timeout.
- A query started with a `key` interrupts any older query with the same key (e.g. one chart
  re-querying after a filter change). With a `generation`, ordering is by generation instead
  of arrival: a request whose generation is older than the newest one seen for its key is
  refused as superseded, and never interrupts the newer query.
- Background cursors (`background_cursor`) are for jobs: no slot (the job executor caps them),
  no timeout, still interruptible by key. With `job_threads` set they run on a second
  in-memory DuckDB with fewer threads, so imports don't starve dashboard queries.
- `cache` holds query results keyed by dataset versions; `pool` runs pure-Python CPU work.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
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
# Share of the memory budget given to the job instance when it is enabled.
_JOB_MEMORY_SHARE = 1 / 3


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _limit_bytes(con: duckdb.DuckDBPyConnection) -> int | None:
    row = con.execute("SELECT current_setting('memory_limit')").fetchone()
    if row is None:
        return None
    amount, _, unit = str(row[0]).partition(" ")
    if unit not in _BYTES_PER_UNIT:
        return None
    return int(float(amount) * _BYTES_PER_UNIT[unit])


def _mib(n: float) -> str:
    return f"{max(64, int(n / 2**20))}MiB"


def _configure(
    con: duckdb.DuckDBPyConnection, threads: int | None, memory_limit: str, temp_dir: Path
) -> None:
    if threads:
        con.execute(f"SET threads = {int(threads)}")
    con.execute(f"SET memory_limit = {_sql_string(memory_limit)}")
    con.execute(f"SET temp_directory = {_sql_string(str(temp_dir))}")
    # Lets `query_progress()` report on long scans; never prints.
    con.execute("SET enable_progress_bar = true")
    con.execute("SET enable_progress_bar_print = false")


@dataclass
class _Active:
    cur: duckdb.DuckDBPyConnection
    generation: int | None


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
        job_threads: int | None = None,
        prewarm: bool = False,
    ) -> None:
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(":memory:")
        if memory_limit:
            main_limit = memory_limit
            job_limit = memory_limit
        else:
            total = _limit_bytes(self._con)
            budget = (total or 2**33) * _DEFAULT_MEMORY_SHARE_OF_DUCKDB_DEFAULT
            if job_threads:
                main_limit = _mib(budget * (1 - _JOB_MEMORY_SHARE))
                job_limit = _mib(budget * _JOB_MEMORY_SHARE)
            else:
                main_limit = job_limit = _mib(budget)
        _configure(self._con, threads, main_limit, temp_dir)
        self._job_con: duckdb.DuckDBPyConnection | None = None
        if job_threads:
            self._job_con = duckdb.connect(":memory:")
            job_temp = temp_dir / "jobs"
            job_temp.mkdir(parents=True, exist_ok=True)
            _configure(self._job_con, job_threads, job_limit, job_temp)

        self._slots = threading.BoundedSemaphore(max_concurrent_queries)
        self._timeout_s = query_timeout_s
        self._lock = threading.Lock()
        self._active: dict[str, _Active] = {}
        self._latest_generation: LRUCache[str, int] = LRUCache(maxsize=10_000)
        self._interrupt_reason: dict[int, str] = {}

        self.cache = ResultCache(cache_mb * 2**20)
        self.pool = ProcessPool(process_workers, blas_threads=blas_threads)
        if prewarm:
            self.pool.prewarm()

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

    def _interrupt_cursor(self, cur: duckdb.DuckDBPyConnection, reason: str) -> None:
        with self._lock:
            self._interrupt_reason.setdefault(id(cur), reason)
        cur.interrupt()

    def interrupt(self, key: str, reason: str = "cancelled") -> bool:
        """Interrupt the query running under `key`. Returns False if none is running."""
        with self._lock:
            active = self._active.get(key)
        if active is None:
            return False
        self._interrupt_cursor(active.cur, reason)
        return True

    def progress(self, key: str) -> float | None:
        """Progress (0-1) of the query running under `key`, or None if DuckDB can't tell."""
        with self._lock:
            active = self._active.get(key)
        if active is None:
            return None
        try:
            value = float(active.cur.query_progress())
        except duckdb.Error:
            return None
        return None if value < 0 else min(1.0, value / 100.0)

    def _check_generation(self, key: str | None, generation: int | None) -> None:
        """Record `generation` as the newest for `key`, or refuse it if a newer one exists."""
        if key is None or generation is None:
            return
        with self._lock:
            latest = self._latest_generation.get(key)
            if latest is not None and generation < latest:
                raise QueryInterruptedError("superseded")
            self._latest_generation[key] = generation

    def _register(
        self, key: str | None, generation: int | None, cur: duckdb.DuckDBPyConnection
    ) -> None:
        if key is None:
            return
        with self._lock:
            if generation is not None:
                latest = self._latest_generation.get(key)
                if latest is not None and generation < latest:
                    raise QueryInterruptedError("superseded")
            previous = self._active.get(key)
            self._active[key] = _Active(cur, generation)
        if previous is not None:
            self._interrupt_cursor(previous.cur, "superseded")

    def _unregister(self, key: str | None, cur: duckdb.DuckDBPyConnection) -> None:
        with self._lock:
            if key is not None:
                active = self._active.get(key)
                if active is not None and active.cur is cur:
                    del self._active[key]
            self._interrupt_reason.pop(id(cur), None)

    def _reason(self, cur: duckdb.DuckDBPyConnection) -> str:
        with self._lock:
            return self._interrupt_reason.get(id(cur), "interrupted")

    # -- cursors ------------------------------------------------------------------------

    @contextmanager
    def cursor(
        self, key: str | None = None, generation: int | None = None
    ) -> Iterator[duckdb.DuckDBPyConnection]:
        """A private cursor for one interactive query. Fully consume results inside the block."""
        self._check_generation(key, generation)
        if not self._slots.acquire(timeout=self._timeout_s):
            raise EngineBusyError("too many queries running; try again")
        try:
            cur = self._con.cursor()
            try:
                self._register(key, generation, cur)
            except BaseException:
                cur.close()
                raise
        except BaseException:
            self._slots.release()
            raise
        timer = threading.Timer(self._timeout_s, self._interrupt_cursor, args=(cur, "timed out"))
        timer.daemon = True
        timer.start()
        try:
            yield cur
        except duckdb.InterruptException as exc:
            raise QueryInterruptedError(self._reason(cur)) from exc
        finally:
            timer.cancel()
            self._unregister(key, cur)
            cur.close()
            self._slots.release()

    @contextmanager
    def background_cursor(self, key: str | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
        """A cursor for job work: no query slot, no timeout, interruptible via `interrupt(key)`."""
        con = self._job_con or self._con
        cur = con.cursor()
        try:
            self._register(key, None, cur)
        except BaseException:
            cur.close()
            raise
        try:
            yield cur
        except duckdb.InterruptException as exc:
            raise QueryInterruptedError(self._reason(cur)) from exc
        finally:
            self._unregister(key, cur)
            cur.close()

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
        self.pool.close()
        if self._job_con is not None:
            self._job_con.close()
        self._con.close()
