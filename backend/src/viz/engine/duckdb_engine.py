"""Process-wide DuckDB instance (CLAUDE.md §6, Concurrency model).

- One in-memory DuckDB per process; data lives in Parquet files, so nothing here is durable.
- Each query gets its own cursor (a DuckDB connection is not thread-safe, and in DuckDB 2.0 a
  cursor refuses a new query while a result is open).
- A semaphore caps concurrent queries so bursts queue instead of exhausting memory.
- Queries started with a `key` interrupt any older query with the same key (e.g. one chart
  re-querying after a filter change), and every query is interrupted after the timeout.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pyarrow as pa

from viz.config import Settings
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


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _scaled_default_memory_limit(con: duckdb.DuckDBPyConnection) -> str | None:
    row = con.execute("SELECT current_setting('memory_limit')").fetchone()
    if row is None:
        return None
    amount, _, unit = str(row[0]).partition(" ")
    if unit not in _BYTES_PER_UNIT:
        return None
    limit = float(amount) * _BYTES_PER_UNIT[unit] * _DEFAULT_MEMORY_SHARE_OF_DUCKDB_DEFAULT
    return f"{int(limit / 2**20)}MiB"


class Engine:
    def __init__(
        self,
        *,
        temp_dir: Path,
        threads: int | None = None,
        memory_limit: str | None = None,
        max_concurrent_queries: int = 8,
        query_timeout_s: float = 60.0,
    ) -> None:
        self._con = duckdb.connect(":memory:")
        if threads:
            self._con.execute(f"SET threads = {int(threads)}")
        limit = memory_limit or _scaled_default_memory_limit(self._con)
        if limit:
            self._con.execute(f"SET memory_limit = {_sql_string(limit)}")
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._con.execute(f"SET temp_directory = {_sql_string(str(temp_dir))}")

        self._slots = threading.BoundedSemaphore(max_concurrent_queries)
        self._timeout_s = query_timeout_s
        self._lock = threading.Lock()
        self._active: dict[str, duckdb.DuckDBPyConnection] = {}
        self._interrupt_reason: dict[int, str] = {}

    @classmethod
    def from_settings(cls, settings: Settings, temp_dir: Path) -> Engine:
        return cls(
            temp_dir=temp_dir,
            threads=settings.duckdb_threads,
            memory_limit=settings.duckdb_memory_limit,
            max_concurrent_queries=settings.max_concurrent_queries,
            query_timeout_s=settings.query_timeout_s,
        )

    def _interrupt(self, cur: duckdb.DuckDBPyConnection, reason: str) -> None:
        with self._lock:
            self._interrupt_reason.setdefault(id(cur), reason)
        cur.interrupt()

    @contextmanager
    def cursor(self, key: str | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
        """A private cursor for one query. Fully consume results inside the block."""
        if not self._slots.acquire(timeout=self._timeout_s):
            raise EngineBusyError("too many queries running; try again")
        cur = self._con.cursor()
        if key is not None:
            with self._lock:
                previous = self._active.get(key)
                self._active[key] = cur
            if previous is not None:
                self._interrupt(previous, "superseded")
        timer = threading.Timer(self._timeout_s, self._interrupt, args=(cur, "timed out"))
        timer.daemon = True
        timer.start()
        try:
            yield cur
        except duckdb.InterruptException as exc:
            with self._lock:
                reason = self._interrupt_reason.get(id(cur), "interrupted")
            raise QueryInterruptedError(reason) from exc
        finally:
            timer.cancel()
            with self._lock:
                if key is not None and self._active.get(key) is cur:
                    del self._active[key]
                self._interrupt_reason.pop(id(cur), None)
            cur.close()
            self._slots.release()

    def query_arrow(
        self,
        sql: str,
        params: Sequence[object] | None = None,
        *,
        key: str | None = None,
    ) -> pa.Table:
        with self.cursor(key) as cur:
            return cur.sql(sql, params=params).to_arrow_table()

    def close(self) -> None:
        self._con.close()
