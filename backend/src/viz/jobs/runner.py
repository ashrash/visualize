"""In-process job runner (CLAUDE.md §6, "Jobs vs interactive").

Jobs run on their own small thread pool (`max_concurrent_jobs`, default 1), never on the
request threadpool, and query DuckDB through `Engine.background_cursor`. Handlers are
registered per `JobKind` with a Pydantic params model, so the same handler can later run in an
SQS worker (a `QueueJobRunner` implementing `JobRunner`): never close over live objects.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import anyio
import duckdb
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from viz.config import Settings
from viz.contracts import ExportFile, Job, JobKind, JobResult, JobStatus, new_id
from viz.engine import Engine
from viz.errors import ConflictError, NotFoundError, QueryInterruptedError, VizError
from viz.storage import LocalStorage

log = logging.getLogger(__name__)

JobHandler = Callable[["JobContext", Any], JobResult]

TERMINAL = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})
_WATCH_POLL_S = 0.2
_PROGRESS_POLL_S = 0.25


class JobCancelledError(Exception):
    """Raised inside a handler (by `check_cancelled`) once the job has been cancelled."""


@dataclass(frozen=True)
class _Registration:
    params: type[BaseModel]
    handler: JobHandler


_HANDLERS: dict[JobKind, _Registration] = {}


def job_handler[P: BaseModel](kind: JobKind, params: type[P]) -> Callable[[JobHandler], JobHandler]:
    """Register `fn(ctx, params) -> JobResult` as the handler for `kind`."""

    def register(fn: JobHandler) -> JobHandler:
        _HANDLERS[kind] = _Registration(params, fn)
        return fn

    return register


def registered_kinds() -> set[JobKind]:
    return set(_HANDLERS)


def job_result(
    *,
    dataset_id: str | None = None,
    version: int | None = None,
    pipeline_id: str | None = None,
    export: ExportFile | None = None,
    warnings: list[str] | None = None,
) -> JobResult:
    return JobResult(
        dataset_id=dataset_id,
        version=version,
        pipeline_id=pipeline_id,
        export=export,
        warnings=warnings or [],
    )


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _State:
    job: Job
    params: BaseModel
    cancel: threading.Event = field(default_factory=threading.Event)
    dedupe_key: str | None = None
    future: Future[None] | None = None
    # Called once when the job reaches a final state (however it got there).
    on_done: Callable[[], None] | None = None
    # (low, high) while a DuckDB statement reports progress into that band.
    progress_band: tuple[float, float] | None = None
    finished_mono: float | None = None


class JobContext:
    """What a handler gets: services, progress reporting and cancellation."""

    def __init__(self, runner: InProcessJobRunner, job_id: str) -> None:
        self._runner = runner
        self.id = job_id
        self.engine = runner.engine
        self.storage = runner.storage
        self.settings = runner.settings

    @property
    def key(self) -> str:
        return f"job:{self.id}"

    def session(self) -> Session:
        return self._runner.sessionmaker()

    @property
    def cancelled(self) -> bool:
        return self._runner._state(self.id).cancel.is_set()

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelledError

    def progress(self, fraction: float | None, message: str | None = None) -> None:
        """Report progress (0-1, or None for indeterminate) and optionally a new stage."""
        self.check_cancelled()
        self._runner._update(
            self.id,
            progress=None if fraction is None else max(0.0, min(1.0, fraction)),
            message=message,
        )

    @contextmanager
    def cursor(
        self, progress: tuple[float, float] | None = None
    ) -> Iterator[duckdb.DuckDBPyConnection]:
        """A background DuckDB cursor, interrupted on cancel. With `progress=(lo, hi)`, DuckDB's
        own query progress is mapped into that band while the block runs."""
        self.check_cancelled()
        state = self._runner._state(self.id)
        with self.engine.background_cursor(self.key) as cur:
            state.progress_band = progress
            try:
                yield cur
            except QueryInterruptedError as exc:
                if state.cancel.is_set():
                    raise JobCancelledError from exc
                raise
            finally:
                state.progress_band = None
        self.check_cancelled()


class JobRunner(Protocol):
    """What routers use. In-process now; a queue-backed runner on AWS."""

    def submit(
        self,
        kind: JobKind,
        params: BaseModel,
        *,
        title: str,
        dedupe_key: str | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> Job: ...
    def get(self, job_id: str) -> Job: ...
    def find_active(self, dedupe_key: str) -> Job | None: ...
    def list(self, *, active: bool = False) -> list[Job]: ...
    def cancel(self, job_id: str) -> Job: ...
    def watch(self, job_id: str, *, heartbeat_s: float = 15.0) -> AsyncIterator[Job | None]: ...
    def wait(self, job_id: str, timeout_s: float) -> Job: ...
    def close(self) -> None: ...


class InProcessJobRunner:
    def __init__(
        self,
        *,
        engine: Engine,
        storage: LocalStorage,
        sessionmaker: sessionmaker[Session],
        settings: Settings,
        max_workers: int | None = None,
        retention_s: float | None = None,
    ) -> None:
        self.engine = engine
        self.storage = storage
        self.sessionmaker = sessionmaker
        self.settings = settings
        self._retention_s = settings.job_retention_s if retention_s is None else retention_s
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers or max(1, settings.max_concurrent_jobs),
            thread_name_prefix="viz-job",
        )
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._jobs: dict[str, _State] = {}
        self._closed = threading.Event()
        self._ticker = threading.Thread(target=self._tick, name="viz-job-progress", daemon=True)
        self._ticker.start()

    # -- public API ---------------------------------------------------------------------

    def submit(
        self,
        kind: JobKind,
        params: BaseModel,
        *,
        title: str,
        dedupe_key: str | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> Job:
        """Queue a job. `on_done` runs once when it ends (succeeded, failed, cancelled while
        queued or running, or dropped at shutdown); if `dedupe_key` returns an existing job
        instead, `on_done` runs immediately. It's local bookkeeping (e.g. releasing a staged
        upload) and a queue-backed runner would call it when the job's final state arrives."""
        try:
            return self._submit(kind, params, title=title, dedupe_key=dedupe_key, on_done=on_done)
        except BaseException:
            if on_done is not None:
                on_done()
            raise

    def _submit(
        self,
        kind: JobKind,
        params: BaseModel,
        *,
        title: str,
        dedupe_key: str | None,
        on_done: Callable[[], None] | None,
    ) -> Job:
        registration = _HANDLERS.get(kind)
        if registration is None:
            raise NotFoundError(f"no handler for job kind {kind}")
        if not isinstance(params, registration.params):
            raise TypeError(f"{kind} jobs take {registration.params.__name__}")
        if self._closed.is_set():
            raise ConflictError("the job runner is shutting down")
        with self._lock:
            self._prune()
            if dedupe_key is not None:
                for state in self._jobs.values():
                    if state.dedupe_key == dedupe_key and state.job.status not in TERMINAL:
                        if on_done is not None:
                            on_done()
                        return state.job
            job = Job(
                id=new_id(),
                kind=kind,
                title=title,
                status=JobStatus.QUEUED,
                progress=None,
                message="Queued",
                seq=0,
                created_at=_now(),
                started_at=None,
                finished_at=None,
                error=None,
                result=None,
            )
            state = _State(job=job, params=params, dedupe_key=dedupe_key, on_done=on_done)
            self._jobs[job.id] = state
        state.future = self._executor.submit(self._run, job.id, registration)
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._state_locked(job_id).job

    def find_active(self, dedupe_key: str) -> Job | None:
        """The queued or running job submitted with `dedupe_key`, if any."""
        with self._lock:
            for state in self._jobs.values():
                if state.dedupe_key == dedupe_key and state.job.status not in TERMINAL:
                    return state.job
        return None

    def list(self, *, active: bool = False) -> list[Job]:
        with self._lock:
            self._prune()
            jobs = [s.job for s in self._jobs.values()]
        if active:
            jobs = [j for j in jobs if j.status not in TERMINAL]
        return sorted(jobs, key=lambda j: j.created_at)

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            state = self._state_locked(job_id)
            if state.job.status in TERMINAL:
                return state.job
            state.cancel.set()
            queued = state.job.status == JobStatus.QUEUED
        if queued and state.future is not None and state.future.cancel():
            self._finish(job_id, JobStatus.CANCELLED, message="Cancelled")
        else:
            self._update(job_id, message="Cancelling")
            self.engine.interrupt(f"job:{job_id}", "cancelled")
        return self.get(job_id)

    def wait(self, job_id: str, timeout_s: float) -> Job:
        deadline = time.monotonic() + timeout_s
        with self._changed:
            while True:
                job = self._state_locked(job_id).job
                if job.status in TERMINAL:
                    return job
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"job {job_id} still {job.status}")
                self._changed.wait(remaining)

    async def watch(self, job_id: str, *, heartbeat_s: float = 15.0) -> AsyncIterator[Job | None]:
        """Yield the current snapshot, then each change; `None` is a keep-alive tick.
        Ends after a terminal snapshot."""
        last_seq = -1
        last_sent = time.monotonic()
        while True:
            try:
                job = self.get(job_id)
            except NotFoundError:  # pruned while watched
                return
            if job.seq != last_seq:
                last_seq = job.seq
                last_sent = time.monotonic()
                yield job
                if job.status in TERMINAL:
                    return
            elif time.monotonic() - last_sent >= heartbeat_s:
                last_sent = time.monotonic()
                yield None
            await anyio.sleep(_WATCH_POLL_S)

    def close(self, timeout_s: float = 10.0) -> None:
        self._closed.set()
        with self._lock:
            states = list(self._jobs.values())
        for state in states:
            if state.job.status not in TERMINAL:
                state.cancel.set()
                self.engine.interrupt(f"job:{state.job.id}", "cancelled")
        self._executor.shutdown(wait=False, cancel_futures=True)
        for state in states:
            if state.future is not None and state.future.cancelled():
                self._finish(state.job.id, JobStatus.CANCELLED, message="Cancelled")
        deadline = time.monotonic() + timeout_s
        for state in states:
            if state.future is not None and not state.future.done():
                # Shutting down: the outcome is already recorded on the job.
                with contextlib.suppress(Exception):
                    state.future.result(timeout=max(0.0, deadline - time.monotonic()))

    # -- internals ----------------------------------------------------------------------

    def _state(self, job_id: str) -> _State:
        with self._lock:
            return self._state_locked(job_id)

    def _state_locked(self, job_id: str) -> _State:
        state = self._jobs.get(job_id)
        if state is None:
            raise NotFoundError(f"job {job_id} not found")
        return state

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._changed:
            state = self._jobs.get(job_id)
            if state is None or state.job.status in TERMINAL:
                return
            if changes.get("message") is None:
                changes.pop("message", None)
            state.job = state.job.model_copy(update={**changes, "seq": state.job.seq + 1})
            self._changed.notify_all()

    def _finish(
        self,
        job_id: str,
        status: JobStatus,
        *,
        message: str | None = None,
        error: str | None = None,
        result: JobResult | None = None,
    ) -> None:
        with self._changed:
            state = self._jobs[job_id]
            if state.job.status in TERMINAL:
                return
            update: dict[str, Any] = {
                "status": status,
                "finished_at": _now(),
                "seq": state.job.seq + 1,
                "error": error,
                "result": result,
                "message": message,
            }
            if status == JobStatus.SUCCEEDED:
                update["progress"] = 1.0
            state.job = state.job.model_copy(update=update)
            state.finished_mono = time.monotonic()
            done, state.on_done = state.on_done, None
            self._changed.notify_all()
        if done is not None:
            try:
                done()
            except Exception:
                log.exception("on_done callback of job %s failed", job_id)

    def _run(self, job_id: str, registration: _Registration) -> None:
        state = self._state(job_id)
        if state.cancel.is_set():
            self._finish(job_id, JobStatus.CANCELLED, message="Cancelled")
            return
        self._update(job_id, status=JobStatus.RUNNING, started_at=_now(), message="Starting")
        ctx = JobContext(self, job_id)
        try:
            result = registration.handler(ctx, state.params)
        except JobCancelledError:
            self._finish(job_id, JobStatus.CANCELLED, message="Cancelled")
        except QueryInterruptedError as exc:
            if state.cancel.is_set():
                self._finish(job_id, JobStatus.CANCELLED, message="Cancelled")
            else:
                self._finish(job_id, JobStatus.FAILED, message="Failed", error=str(exc))
        except VizError as exc:
            self._finish(job_id, JobStatus.FAILED, message="Failed", error=str(exc))
        except Exception as exc:
            log.exception("job %s (%s) failed", job_id, state.job.kind)
            self._finish(
                job_id,
                JobStatus.FAILED,
                message="Failed",
                error=f"{type(exc).__name__}: {exc}",
            )
        else:
            if state.cancel.is_set():
                self._finish(job_id, JobStatus.CANCELLED, message="Cancelled")
            else:
                self._finish(job_id, JobStatus.SUCCEEDED, message="Done", result=result)

    def _tick(self) -> None:
        """Map DuckDB's progress for running statements into each job's progress band, and
        sweep expired staged uploads and export files every `sweep_interval_s`."""
        last_sweep = time.monotonic()
        while not self._closed.wait(_PROGRESS_POLL_S):
            if time.monotonic() - last_sweep >= self.settings.sweep_interval_s:
                last_sweep = time.monotonic()
                try:
                    self.storage.sweep(
                        staged_max_age_s=self.settings.staged_retention_s,
                        exports_max_age_s=self.settings.export_retention_s,
                    )
                except OSError:
                    log.warning("expiry sweep failed", exc_info=True)
            with self._lock:
                running = [
                    (s.job.id, s.progress_band, s.job.progress)
                    for s in self._jobs.values()
                    if s.progress_band is not None and s.job.status == JobStatus.RUNNING
                ]
            for job_id, band, current in running:
                if band is None:
                    continue
                fraction = self.engine.progress(f"job:{job_id}")
                if fraction is None:
                    continue
                low, high = band
                value = round(low + (high - low) * fraction, 3)
                if current is None or value > current:
                    self._update(job_id, progress=value)

    def _prune(self) -> None:
        """Forget finished jobs older than the retention window. Caller holds the lock."""
        cutoff = time.monotonic() - self._retention_s
        doomed = [
            job_id
            for job_id, s in self._jobs.items()
            if s.finished_mono is not None and s.finished_mono < cutoff
        ]
        for job_id in doomed:
            del self._jobs[job_id]
