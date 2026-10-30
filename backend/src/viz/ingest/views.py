"""Materialized grid views, so deep pages of a sorted or filtered grid stay fast.

DuckDB answers `ORDER BY ... LIMIT 200 OFFSET 5,000,000` over 10M rows by sorting every
column (~1.4 s), and a deep filtered page has to re-scan the filter (~0.8 s). Instead, the
first request for a sorted/filtered/searched view of a large dataset starts a background
build that writes the whole view to `temp/views/<dataset>@v<n>-<hash>.parquet`. Until it is
ready pages are answered directly; afterwards every page is a `file_row_number` range read
(~25 ms) and the total row count is free.

Views are a cache: keyed by dataset version + filters + search + sort, LRU-limited in count,
deleted with their dataset, and wiped at startup.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
from sqlglot import exp

from viz.chartdata.sql import col
from viz.contracts import Dataset, DatasetVersion, RowsRequest
from viz.engine import Engine
from viz.storage import LocalStorage

log = logging.getLogger(__name__)

# Views are only worth building for datasets at least this large.
DEFAULT_MIN_ROWS = 500_000
DEFAULT_MAX_VIEWS = 8


@dataclass
class _View:
    path: Path
    dataset_id: str
    rows: int | None = None  # set when ready
    failed: bool = False
    last_used: float = 0.0


def view_id(dataset_id: str, version: int, request: RowsRequest) -> str:
    payload = {
        "filters": [p.model_dump(mode="json") for p in request.filters],
        "search": request.search or None,
        "sort": [k.model_dump(mode="json") for k in request.sort],
    }
    digest = hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]
    return f"{dataset_id}@v{version}-{digest}"


def needs_view(request: RowsRequest) -> bool:
    return bool(request.sort or request.filters or request.search)


class RowViews:
    def __init__(
        self,
        engine: Engine,
        storage: LocalStorage,
        *,
        min_rows: int = DEFAULT_MIN_ROWS,
        max_views: int = DEFAULT_MAX_VIEWS,
    ) -> None:
        self.engine = engine
        self.storage = storage
        self.min_rows = min_rows
        self.max_views = max_views
        self.directory = storage.temp_dir / "views"
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._views: dict[str, _View] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="viz-views")
        self._closed = False

    def page(
        self,
        dataset: Dataset,
        version: DatasetVersion,
        request: RowsRequest,
        *,
        limit: int,
        key: str | None,
    ) -> tuple[pa.Table, int] | None:
        """Serve a page from a ready view as (table, total rows), or None."""
        if not needs_view(request) or version.row_count < self.min_rows:
            return None
        with self._lock:
            view = self._views.get(view_id(dataset.id, version.n, request))
            if view is None or view.rows is None or view.failed:
                return None
            view.last_used = time.monotonic()
            rows, path = view.rows, view.path
        return self._read(path, request, limit, key), rows

    def ensure(
        self,
        dataset: Dataset,
        version: DatasetVersion,
        request: RowsRequest,
        select_sql: exp.Select,
    ) -> None:
        """Start building the view in the background if it's worthwhile and not known yet.
        Called after a direct page has been answered, so the build doesn't slow that page."""
        if not needs_view(request) or version.row_count < self.min_rows:
            return
        vid = view_id(dataset.id, version.n, request)
        with self._lock:
            if vid in self._views or self._closed:
                return
            self._views[vid] = _View(
                self.directory / f"{vid}.parquet", dataset.id, last_used=time.monotonic()
            )
            self._executor.submit(self._build, vid, select_sql.copy())

    def _read(self, path: Path, request: RowsRequest, limit: int, key: str | None) -> pa.Table:
        rownum = exp.column("file_row_number")
        source = exp.Anonymous(
            this="read_parquet",
            expressions=[
                exp.Literal.string(str(path)),
                exp.PropertyEQ(this=exp.column("file_row_number"), expression=exp.true()),
            ],
        )
        if request.columns:
            projection: list[exp.Expr] = [col(c) for c in request.columns]
        else:
            projection = [exp.Star(except_=[exp.column("file_row_number")])]
        query = (
            exp.select(*projection)
            .from_(source)
            .where(
                exp.and_(
                    exp.GTE(this=rownum, expression=exp.Literal.number(request.offset)),
                    exp.LT(this=rownum, expression=exp.Literal.number(request.offset + limit)),
                )
            )
            .order_by(rownum)
        )
        return self.engine.query_arrow(query.sql(dialect="duckdb"), key=key)

    def _build(self, vid: str, select_sql: exp.Select) -> None:
        with self._lock:
            view = self._views.get(vid)
        if view is None:
            return
        tmp = view.path.with_name(f".{view.path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with self.engine.background_cursor(f"view:{vid}") as cur:
                cur.sql(select_sql.sql(dialect="duckdb")).write_parquet(
                    str(tmp), compression="zstd"
                )
                rows = int(cur.read_parquet(str(tmp)).aggregate("count(*)").fetchall()[0][0])
            tmp.replace(view.path)
        except Exception:
            log.warning("building grid view %s failed", vid, exc_info=True)
            tmp.unlink(missing_ok=True)
            with self._lock:
                view.failed = True
            return
        with self._lock:
            if self._views.get(vid) is not view:  # dropped while building
                view.path.unlink(missing_ok=True)
                return
            view.rows = rows
            self._evict_locked()

    def _evict_locked(self) -> None:
        ready = sorted(
            (v for v in self._views.values() if v.rows is not None or v.failed),
            key=lambda v: v.last_used,
        )
        for victim in ready[: max(0, len(self._views) - self.max_views)]:
            for vid, v in list(self._views.items()):
                if v is victim:
                    del self._views[vid]
            victim.path.unlink(missing_ok=True)

    def drop(self, dataset_id: str) -> None:
        """Forget and delete every view of a dataset."""
        with self._lock:
            doomed = [vid for vid, v in self._views.items() if v.dataset_id == dataset_id]
            views = [self._views.pop(vid) for vid in doomed]
        for vid, view in zip(doomed, views, strict=True):
            self.engine.interrupt(f"view:{vid}")
            view.path.unlink(missing_ok=True)

    def wait_idle(self, timeout_s: float = 30.0) -> None:
        """Block until queued builds finish (tests and benchmarks)."""
        self._executor.submit(lambda: None).result(timeout=timeout_s)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            running = list(self._views)
        for vid in running:
            self.engine.interrupt(f"view:{vid}")
        self._executor.shutdown(wait=True, cancel_futures=True)
