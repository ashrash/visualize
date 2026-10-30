"""Engine additions: generations, interrupt, background cursors, cache, pool, cancellation."""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections.abc import Iterator
from concurrent.futures import Executor
from pathlib import Path

import anyio
import pyarrow as pa
import pytest

from viz.engine import Engine, ProcessPool, ResultCache, run_cancellable, version_token
from viz.errors import EngineBusyError, QueryInterruptedError

SLOW_SQL = "SELECT count(*) FROM range(100000000000) t"


def wait_for(predicate: object, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return True
        time.sleep(0.02)
    return False


def run_in_thread(fn: object) -> tuple[threading.Thread, list[BaseException]]:
    errors: list[BaseException] = []

    def target() -> None:
        try:
            fn()  # type: ignore[operator]
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    return thread, errors


@pytest.fixture
def small_engine(tmp_path: Path) -> Iterator[Engine]:
    eng = Engine(temp_dir=tmp_path / "t", max_concurrent_queries=1, query_timeout_s=0.5, cache_mb=1)
    yield eng
    eng.close()


# -- generations ------------------------------------------------------------------------------


def test_older_generation_is_refused_without_interrupting_newer(engine: Engine) -> None:
    thread, errors = run_in_thread(lambda: engine.query_arrow(SLOW_SQL, key="c", generation=5))
    assert wait_for(lambda: engine.progress("c") is not None or "c" in engine._active)
    with pytest.raises(QueryInterruptedError) as info:
        engine.query_arrow("SELECT 1", key="c", generation=4)
    assert info.value.reason == "superseded" and info.value.status_code == 409
    time.sleep(0.2)
    assert thread.is_alive(), "the newer query must keep running"
    engine.interrupt("c")
    thread.join(10)
    assert errors and isinstance(errors[0], QueryInterruptedError)
    assert errors[0].reason == "cancelled"


def test_newer_generation_supersedes_running_older(engine: Engine) -> None:
    thread, errors = run_in_thread(lambda: engine.query_arrow(SLOW_SQL, key="c", generation=1))
    assert wait_for(lambda: "c" in engine._active)
    assert engine.query_arrow("SELECT 1 AS one", key="c", generation=2).num_rows == 1
    thread.join(10)
    assert isinstance(errors[0], QueryInterruptedError) and errors[0].reason == "superseded"


def test_without_generation_behaviour_is_unchanged(engine: Engine) -> None:
    engine.query_arrow("SELECT 1", key="k", generation=9)
    # No generation: never refused, even after a generation was recorded.
    assert engine.query_arrow("SELECT 2 AS x", key="k").to_pylist() == [{"x": 2}]


# -- interrupt / background ---------------------------------------------------------------------


def test_interrupt_returns_false_when_nothing_runs(engine: Engine) -> None:
    assert engine.interrupt("nothing") is False


def test_background_cursor_skips_slots_and_timeout(small_engine: Engine) -> None:
    def hold_slot() -> None:
        with small_engine.cursor() as cur:
            cur.sql("SELECT 1").fetchall()
            time.sleep(0.3)

    holder, _ = run_in_thread(hold_slot)
    time.sleep(0.05)

    def background() -> None:
        with small_engine.background_cursor("job:x") as cur:
            cur.sql(SLOW_SQL).fetchall()

    thread, errors = run_in_thread(background)
    # Past the 0.5 s interactive timeout, the background query still runs.
    time.sleep(0.8)
    assert thread.is_alive()
    assert small_engine.interrupt("job:x")
    thread.join(10)
    holder.join(10)
    assert isinstance(errors[0], QueryInterruptedError) and errors[0].reason == "cancelled"


def test_slots_are_capped(small_engine: Engine) -> None:
    thread, _ = run_in_thread(lambda: small_engine.query_arrow(SLOW_SQL))
    time.sleep(0.1)
    started = time.monotonic()
    with contextlib.suppress(EngineBusyError):
        small_engine.query_arrow("SELECT 1")
    # The single slot was held by the slow query until its 0.5 s timeout.
    assert time.monotonic() - started >= 0.25
    thread.join(10)


def test_separate_job_instance(tmp_path: Path) -> None:
    eng = Engine(temp_dir=tmp_path / "t", job_threads=1, memory_limit="1GB")
    try:
        with eng.background_cursor() as cur:
            assert cur.sql("SELECT current_setting('threads')").fetchall()[0][0] == 1
        assert eng.query_arrow("SELECT 1 AS a").num_rows == 1
    finally:
        eng.close()


# -- cache --------------------------------------------------------------------------------------


def test_query_arrow_cached_hits_and_respects_versions(engine: Engine) -> None:
    v1 = [version_token("d", 1)]
    first = engine.query_arrow_cached("SELECT ? AS a", [1], versions=v1)
    second = engine.query_arrow_cached("SELECT ? AS a", [1], versions=v1)
    assert (first.cached, second.cached) == (False, True)
    assert second.table.equals(first.table)
    assert not engine.query_arrow_cached("SELECT ? AS a", [2], versions=v1).cached
    assert not engine.query_arrow_cached("SELECT ? AS a", [1], versions=["ds:d@v2"]).cached


def test_cache_is_bounded_by_bytes_and_evicts_by_prefix() -> None:
    cache = ResultCache(max_bytes=1000)
    for i in range(5):
        cache.put(["ds:a@v1"], f"q{i}", i, nbytes=300)
    assert cache.current_bytes <= 1000
    assert cache.get(["ds:a@v1"], "q0") is None  # least recently used went first
    assert cache.get(["ds:a@v1"], "q4") == 4
    cache.put(["ds:b@v1"], "q", "b", nbytes=10)
    assert cache.evict("ds:a@") >= 1
    assert cache.get(["ds:a@v1"], "q4") is None
    assert cache.get(["ds:b@v1"], "q") == "b"
    cache.put(["x"], "huge", "x", nbytes=5000)  # larger than the budget: not stored
    assert cache.get(["x"], "huge") is None


def test_get_or_compute() -> None:
    cache = ResultCache(max_bytes=10_000)
    calls: list[int] = []

    def compute() -> pa.Table:
        calls.append(1)
        return pa.table({"a": [1]})

    _, hit1 = cache.get_or_compute(["v"], "k", compute, nbytes=lambda t: t.nbytes)
    value, hit2 = cache.get_or_compute(["v"], "k", compute, nbytes=lambda t: t.nbytes)
    assert (hit1, hit2, len(calls)) == (False, True, 1)
    assert value.num_rows == 1


# -- process pool ---------------------------------------------------------------------------------


def test_pool_is_an_executor_and_runs_spawned_work() -> None:
    pool = ProcessPool(1, warm_modules=())
    try:
        assert isinstance(pool, Executor)
        pids = [f.result(60) for f in pool.prewarm()]
        assert pids and all(isinstance(p, int) for p in pids)
        assert pool.run(math.factorial, 10) == 3628800
        assert list(pool.map(math.isqrt, [4, 9, 16])) == [2, 3, 4]
        assert pool.submit(pow, 2, 10).result(60) == 1024
    finally:
        pool.close()


def test_pool_timeout_recycles_workers() -> None:
    pool = ProcessPool(1, warm_modules=())
    try:
        pool.prewarm()[0].result(60)
        with pytest.raises(QueryInterruptedError):
            pool.run(time.sleep, 30, timeout_s=0.5)
        assert pool.run(abs, -3, timeout_s=60) == 3
    finally:
        pool.close()


# -- run_cancellable ------------------------------------------------------------------------------


class _FakeRequest:
    def __init__(self, disconnect_after_s: float | None) -> None:
        self._at = None if disconnect_after_s is None else time.monotonic() + disconnect_after_s

    async def is_disconnected(self) -> bool:
        return self._at is not None and time.monotonic() >= self._at


def test_run_cancellable_returns_result(engine: Engine) -> None:
    async def main() -> object:
        return await run_cancellable(
            _FakeRequest(None), engine, "r1", lambda: engine.query_arrow("SELECT 7 AS x", key="r1")
        )

    table = anyio.run(main)
    assert isinstance(table, pa.Table) and table.to_pylist() == [{"x": 7}]


def test_run_cancellable_interrupts_on_disconnect(engine: Engine) -> None:
    async def main() -> object:
        return await run_cancellable(
            _FakeRequest(0.3), engine, "r2", lambda: engine.query_arrow(SLOW_SQL, key="r2")
        )

    started = time.monotonic()
    with pytest.raises(QueryInterruptedError) as info:
        anyio.run(main)
    assert info.value.reason == "cancelled"
    assert time.monotonic() - started < 5
