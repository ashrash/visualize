"""Process pool for pure-Python CPU work (bootstrap, force layouts, long loops). CLAUDE.md §6.

- Workers start with `spawn` on every OS (Windows and macOS do anyway), so worker functions
  must be top-level and picklable. Pass a Parquet path + SQL or a capped Arrow table, never a
  large DataFrame.
- The initializer caps BLAS/OpenMP threads per worker and imports the scientific stack, so
  `prewarm()` at startup pays the ~1-2 s SciPy import once instead of on the first request.
- A running task can't be cancelled. On timeout (or a crashed worker) the pool is torn down
  and recreated, and the caller gets `QueryInterruptedError("timed out")`.
"""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import threading
from collections.abc import Callable
from concurrent.futures import Executor, Future, ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
from typing import ParamSpec, TypeVar

from viz.errors import QueryInterruptedError

P = ParamSpec("P")
T = TypeVar("T")

WARM_MODULES = ("numpy", "scipy.stats", "statsmodels.api", "sklearn")
_THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


def _init_worker(blas_threads: int, modules: tuple[str, ...]) -> None:
    for name in _THREAD_ENV:
        os.environ[name] = str(blas_threads)
    import importlib

    from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

    for module in modules:
        with contextlib.suppress(ImportError):  # optional module missing
            importlib.import_module(module)
    threadpool_limits(limits=blas_threads)


def _noop() -> int:
    return os.getpid()


def default_blas_threads(workers: int) -> int:
    return max(1, (os.cpu_count() or 2) // max(1, workers))


_main_limits_applied = False
_main_limits_lock = threading.Lock()


def limit_main_process_blas(threads: int) -> None:
    """Cap BLAS/OpenMP threads in this (API) process once, so concurrent in-process fits in
    the request threadpool don't oversubscribe the cores."""
    global _main_limits_applied
    with _main_limits_lock:
        if _main_limits_applied:
            return
        from threadpoolctl import threadpool_limits

        threadpool_limits(limits=max(1, threads))
        _main_limits_applied = True


class ProcessPool(Executor):
    """A `concurrent.futures.Executor` (submit/map/shutdown) plus `run` and `prewarm`."""

    def __init__(
        self,
        workers: int = 2,
        *,
        blas_threads: int | None = None,
        warm_modules: tuple[str, ...] = WARM_MODULES,
    ) -> None:
        self.workers = max(1, workers)
        self.blas_threads = blas_threads or default_blas_threads(self.workers)
        self._warm_modules = warm_modules
        self._lock = threading.Lock()
        self._executor: ProcessPoolExecutor | None = None

    def _get(self) -> ProcessPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._executor = ProcessPoolExecutor(
                    max_workers=self.workers,
                    mp_context=multiprocessing.get_context("spawn"),
                    initializer=_init_worker,
                    initargs=(self.blas_threads, self._warm_modules),
                )
            return self._executor

    def prewarm(self) -> list[Future[int]]:
        """Start every worker now without blocking. The futures resolve to worker pids."""
        executor = self._get()
        return [executor.submit(_noop) for _ in range(self.workers)]

    def submit(self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> Future[T]:
        try:
            return self._get().submit(fn, *args, **kwargs)
        except BrokenProcessPool:
            self.restart()
            return self._get().submit(fn, *args, **kwargs)

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=cancel_futures)

    def run(self, fn: Callable[..., T], *args: object, timeout_s: float | None = None) -> T:
        """Run `fn(*args)` in a worker and block for the result. Call from a worker thread."""
        future = self.submit(fn, *args)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeout as exc:
            self.restart()
            raise QueryInterruptedError("timed out") from exc
        except BrokenProcessPool:
            self.restart()
            raise

    def restart(self) -> None:
        """Tear the pool down (killing running tasks); the next call starts a fresh one."""
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            _terminate(executor)

    def close(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
            _terminate(executor)


def _terminate(executor: ProcessPoolExecutor) -> None:
    processes = list((getattr(executor, "_processes", None) or {}).values())
    executor.shutdown(wait=False, cancel_futures=True)
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join(timeout=5)
