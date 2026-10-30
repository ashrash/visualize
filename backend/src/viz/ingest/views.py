"""Materialized grid views, so deep pages of a sorted or filtered grid stay fast.

DuckDB answers `ORDER BY ... LIMIT 200 OFFSET 5,000,000` over 10M rows by sorting every
column (~1.4 s), and a deep filtered page has to re-scan the filter (~0.8 s). Instead, once a
second or deeper page of a sorted/filtered/searched view of a large dataset is requested, a
background build writes the whole view to `temp/views/<dataset>@v<n>-<hash>.parquet`. Until
it is ready pages are answered directly; afterwards every page is a `file_row_number` range
read (~25 ms) and the total row count is free.

Bounds
- Builds run one at a time on the engine's background DuckDB instance (fewer threads), so
  they don't starve interactive queries.
- At most one build per dataset is queued or running: a newer view of the same dataset (the
  user changed the sort) drops the older unfinished one.
- Ready views are LRU-evicted to stay under `max_bytes` (a 10M-row sorted view is ~320 MB);
  a dataset whose Parquet file alone exceeds the budget never gets views.
- Views are deleted with their dataset and wiped at startup. They're a cache.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
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
DEFAULT_MAX_BYTES = 1024 * 2**20


@dataclass(eq=False)
class _View:
    vid: str
    path: Path
    dataset_id: str
    rows: int | None = None  # set when ready
    size: int = 0
    failed: bool = False
    last_used: float = 0.0
    future: Future[None] | None = None


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
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.engine = engine
        self.storage = storage
        self.min_rows = min_rows
        self.max_bytes = max_bytes
        self.directory = storage.temp_dir / "views"
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._views: dict[str, _View] = {}
        # dataset id -> the view being built (queued or running) for it.
        self._building: dict[str, _View] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="viz-views")
        self._closed = False

    def _eligible(self, version: DatasetVersion, request: RowsRequest) -> bool:
        if not needs_view(request) or version.row_count < self.min_rows:
            return False
        return version.size_bytes is None or version.size_bytes <= self.max_bytes

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
        if not self._eligible(version, request):
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
        """Queue a build of this view once the user pages past the first page. Called after a
        direct page was answered, so the build never slows that page."""
        if request.offset == 0 or not self._eligible(version, request):
            return
        vid = view_id(dataset.id, version.n, request)
        superseded: _View | None = None
        with self._lock:
            if vid in self._views or self._closed:
                return
            superseded = self._building.get(dataset.id)
            if superseded is not None:
                self._views.pop(superseded.vid, None)
            view = _View(
                vid, self.directory / f"{vid}.parquet", dataset.id, last_used=time.monotonic()
            )
            self._views[vid] = view
            self._building[dataset.id] = view
            view.future = self._executor.submit(self._build, view, select_sql.copy())
        if superseded is not None:
            self._discard(superseded)

    def _discard(self, view: _View) -> None:
        if view.future is not None:
            view.future.cancel()
        self.engine.interrupt(f"view:{view.vid}")
        view.path.unlink(missing_ok=True)

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

    def _build(self, view: _View, select_sql: exp.Select) -> None:
        with self._lock:
            if self._views.get(view.vid) is not view:
                return
        tmp = view.path.with_name(f".{view.path.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with self.engine.background_cursor(f"view:{view.vid}") as cur:
                cur.sql(select_sql.sql(dialect="duckdb")).write_parquet(
                    str(tmp), compression="zstd"
                )
                rows = int(cur.read_parquet(str(tmp)).aggregate("count(*)").fetchall()[0][0])
            tmp.replace(view.path)
            size = view.path.stat().st_size
        except Exception:
            log.info("grid view %s not built", view.vid, exc_info=True)
            tmp.unlink(missing_ok=True)
            with self._lock:
                view.failed = True
                if self._building.get(view.dataset_id) is view:
                    del self._building[view.dataset_id]
            return
        with self._lock:
            if self._building.get(view.dataset_id) is view:
                del self._building[view.dataset_id]
            if self._views.get(view.vid) is not view:  # dropped while building
                view.path.unlink(missing_ok=True)
                return
            view.rows, view.size = rows, size
            victims = self._evict_locked()
        for victim in victims:
            victim.path.unlink(missing_ok=True)

    def _evict_locked(self) -> list[_View]:
        """Drop least recently used ready views until the total fits in `max_bytes`."""
        ready = sorted(
            (v for v in self._views.values() if v.rows is not None), key=lambda v: v.last_used
        )
        total = sum(v.size for v in ready)
        victims: list[_View] = []
        for victim in ready:
            if total <= self.max_bytes:
                break
            del self._views[victim.vid]
            total -= victim.size
            victims.append(victim)
        for vid in [vid for vid, v in self._views.items() if v.failed]:
            del self._views[vid]
        return victims

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return sum(v.size for v in self._views.values() if v.rows is not None)

    def drop(self, dataset_id: str) -> None:
        """Forget and delete every view of a dataset."""
        with self._lock:
            doomed = [v for v in self._views.values() if v.dataset_id == dataset_id]
            for view in doomed:
                del self._views[view.vid]
            self._building.pop(dataset_id, None)
        for view in doomed:
            self._discard(view)

    def wait_idle(self, timeout_s: float = 60.0) -> None:
        """Block until queued builds finish (tests and benchmarks)."""
        self._executor.submit(lambda: None).result(timeout=timeout_s)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            building = list(self._building.values())
        for view in building:
            self._discard(view)
        self._executor.shutdown(wait=True, cancel_futures=True)
