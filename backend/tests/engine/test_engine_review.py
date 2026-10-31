"""Review fixes: closed-cursor races, owners/tickets, sticky interrupts, fatal recovery, pool."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import anyio
import duckdb
import pytest

from viz.engine import Engine, ProcessPool, next_generation, request_scope, run_cancellable
from viz.errors import EngineBusyError, QueryInterruptedError

SLOW_SQL = "SELECT count(*) FROM range(100000000000) t"


def run_in_thread(fn: object) -> tuple[threading.Thread, list[BaseException], list[object]]:
    errors: list[BaseException] = []
    results: list[object] = []

    def target() -> None:
        try:
            results.append(fn())  # type: ignore[operator]
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=target)
    thread.start()
    return thread, errors, results


def wait_for(predicate: object, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def eng(tmp_path: Path) -> Iterator[Engine]:
    engine = Engine(temp_dir=tmp_path / "t", max_concurrent_queries=4, query_timeout_s=5.0)
    yield engine
    engine.close()


# -- B1: closed-cursor race ---------------------------------------------------------------------


def test_superseding_a_closed_cursor_does_not_poison_the_key(eng: Engine) -> None:
    """A's cursor is closed while still registered (the race window); B must still work and
    the key must stay usable."""
    release = threading.Event()

    def run_a() -> None:
        with eng.cursor("chart:1") as cur:
            cur.sql("SELECT 1").fetchall()
            cur.close()  # simulates A finishing in the gap before B interrupts it
            release.wait(5)

    thread_a, _errors_a, _ = run_in_thread(run_a)
    assert wait_for(lambda: "chart:1" in eng._by_key)
    assert eng.query_arrow("SELECT 2 AS x", key="chart:1").to_pylist() == [{"x": 2}]
    release.set()
    thread_a.join(5)
    for n in range(3):
        assert eng.query_arrow(f"SELECT {n} AS x", key="chart:1").num_rows == 1
    assert eng.interrupt("chart:1") is False
    assert eng._by_key == {} and eng._live == set()


def test_interrupt_and_cancel_never_raise_on_closed_cursors(eng: Engine) -> None:
    def run() -> None:
        with eng.cursor("k") as cur:
            cur.close()
            assert eng.interrupt("k") is True  # cursor closed underneath: no exception
            time.sleep(0.2)  # the reaper keeps re-interrupting a closed cursor safely

    thread, errors, _ = run_in_thread(run)
    thread.join(5)
    assert not errors


# -- B2: owners and tickets --------------------


class _FakeRequest:
    def __init__(self, t0: float, disconnect_at: float | None) -> None:
        self.t0, self.at = t0, disconnect_at

    async def is_disconnected(self) -> bool:
        return self.at is not None and time.perf_counter() - self.t0 >= self.at


def test_disconnect_cancels_only_its_own_request(eng: Engine) -> None:
    key = "values:ds:city"
    t0 = time.perf_counter()

    def r1() -> object:
        eng.query_arrow("SELECT 1", key=key)
        time.sleep(0.5)  # Python work between queries; R1 is aborted meanwhile
        return eng.query_arrow("SELECT 2", key=key)

    def r2() -> object:
        return eng.query_arrow(
            "SELECT count(*) FROM range(200000000) t(x) WHERE x % 7 = 3", key=key
        )

    out: dict[str, str] = {}

    async def main() -> None:
        async def first() -> None:
            try:
                await run_cancellable(_FakeRequest(t0, 0.3), eng, key, r1)
                out["r1"] = "ok"
            except QueryInterruptedError as exc:
                out["r1"] = exc.reason

        async def second() -> None:
            await anyio.sleep(0.2)
            await run_cancellable(_FakeRequest(t0, None), eng, key, r2)
            out["r2"] = "ok"

        async with anyio.create_task_group() as group:
            group.start_soon(first)
            group.start_soon(second)

    anyio.run(main)
    assert out["r2"] == "ok"
    assert out["r1"] in ("cancelled", "superseded")


def test_stale_request_never_supersedes_a_newer_one(tmp_path: Path) -> None:
    # A query_timeout_s well above what the probe query could plausibly take, even on a slow or
    # oversubscribed CI runner: the probe must finish normally, not be cut off by the engine's own
    # timeout (that would masquerade as the behavior this test is checking against).
    eng = Engine(temp_dir=tmp_path / "t", max_concurrent_queries=4, query_timeout_s=60.0)
    try:
        with request_scope() as old:
            pass
        newer_started = threading.Event()

        def newer() -> object:
            with request_scope(), eng.cursor("chart:9") as cur:
                newer_started.set()
                return cur.sql(
                    "SELECT count(*) FROM range(300000000) t(x) WHERE x % 7 = 3"
                ).fetchall()

        thread, errors, results = run_in_thread(newer)
        assert newer_started.wait(5)
        with request_scope(old), pytest.raises(QueryInterruptedError) as info:
            eng.query_arrow("SELECT 1", key="chart:9")
        assert info.value.reason == "superseded"
        thread.join(60)
        assert not errors and results
    finally:
        eng.close()


def test_cancelled_owner_cannot_start_new_cursors(eng: Engine) -> None:
    with request_scope() as owner:
        eng.cancel_owner(owner)
        with pytest.raises(QueryInterruptedError):
            eng.query_arrow("SELECT 1")


def test_supersede_happens_at_arrival_even_when_slots_are_full(tmp_path: Path) -> None:
    eng = Engine(temp_dir=tmp_path / "t", max_concurrent_queries=1, query_timeout_s=30)
    try:
        started = time.monotonic()
        thread, errors, _ = run_in_thread(
            lambda: eng.query_arrow(SLOW_SQL, key="chart:1", generation=1)
        )
        assert wait_for(lambda: "chart:1" in eng._by_key)
        time.sleep(0.2)
        assert eng.query_arrow("SELECT 1 AS a", key="chart:1", generation=2).num_rows == 1
        thread.join(10)
        assert isinstance(errors[0], QueryInterruptedError)
        assert errors[0].reason == "superseded"
        assert time.monotonic() - started < 5
    finally:
        eng.close()


def test_interrupt_between_statements_is_not_lost(eng: Engine) -> None:
    """DuckDB drops an interrupt that arrives between statements; ours is sticky."""

    def run() -> None:
        with eng.cursor("job-like") as cur:
            cur.sql("SELECT 1").fetchall()
            time.sleep(0.3)  # the interrupt lands now, between statements
            cur.sql(SLOW_SQL).fetchall()

    thread, errors, _ = run_in_thread(run)
    time.sleep(0.1)
    eng.interrupt("job-like")
    thread.join(10)
    assert not thread.is_alive()
    assert isinstance(errors[0], QueryInterruptedError)


def test_timeout_between_statements_is_enforced(tmp_path: Path) -> None:
    eng = Engine(temp_dir=tmp_path / "t", query_timeout_s=0.3)
    try:

        def run() -> None:
            with eng.cursor() as cur:
                time.sleep(0.5)  # past the deadline before the slow statement starts
                cur.sql(SLOW_SQL).fetchall()

        thread, errors, _ = run_in_thread(run)
        thread.join(10)
        assert isinstance(errors[0], QueryInterruptedError)
        assert errors[0].reason == "timed out"
    finally:
        eng.close()


# -- fatal recovery --------------------


def test_fatal_exception_recreates_the_instance(eng: Engine) -> None:
    before = eng._main
    eng.query_arrow("SELECT 1")
    with pytest.raises(EngineBusyError), eng.cursor():
        raise duckdb.FatalException("simulated: database invalidated")
    assert eng._main is not before and eng.restarts == 1
    assert eng.query_arrow("SELECT 41 + 1 AS x").to_pylist() == [{"x": 42}]
    assert eng.query_arrow("SELECT current_setting('memory_limit') AS m").num_rows == 1


def test_in_flight_query_on_a_replaced_instance_finishes(eng: Engine) -> None:
    """Another request's fatal error replaces the instance; work already running on the old
    (still healthy) one completes, new cursors use the new one, and the old one is closed
    after its last cursor is released."""
    old = eng._main
    with eng.cursor() as cur:
        with eng._lock:
            eng._recover(old)
        assert eng._main is not old
        assert cur.sql("SELECT 7").fetchall() == [(7,)]
        with eng.cursor() as fresh:  # resolved under the lock: the new instance
            assert fresh.sql("SELECT 1").fetchall() == [(1,)]
    assert old.active == 0 and old.retired
    with pytest.raises(duckdb.Error):
        old.con.sql("SELECT 1").fetchall()  # closed once released
    assert eng.query_arrow("SELECT 1 AS x").num_rows == 1


def test_background_instance_recovers_too(tmp_path: Path) -> None:
    eng = Engine(temp_dir=tmp_path / "t", job_threads=1)
    try:
        bg = eng._bg
        with pytest.raises(EngineBusyError), eng.background_cursor():
            raise duckdb.FatalException("simulated")
        assert eng._bg is not bg
        with eng.background_cursor() as cur:
            assert cur.sql("SELECT 1").fetchall() == [(1,)]
    finally:
        eng.close()


# -- process pool --------------------


def test_pool_close_kills_running_workers_quickly() -> None:
    pool = ProcessPool(1, warm_modules=())
    pool.prewarm()[0].result(60)
    pool.submit(time.sleep, 30)
    time.sleep(0.5)
    assert pool._executor is not None
    processes = list(pool._executor._processes.values())  # type: ignore[attr-defined]
    started = time.monotonic()
    pool.close()
    assert time.monotonic() - started < 5
    assert all(not p.is_alive() for p in processes)


def test_pool_restart_is_ignored_for_a_stale_executor() -> None:
    pool = ProcessPool(1, warm_modules=())
    try:
        pool.prewarm()[0].result(60)
        old = pool._executor
        pool.restart()
        assert pool.run(abs, -1, timeout_s=60) == 1
        current = pool._executor
        pool.restart(old)  # a late caller holding the old executor must not kill the new one
        assert pool._executor is current
    finally:
        pool.close()


def test_pool_broken_worker_is_a_clean_error() -> None:
    import os

    pool = ProcessPool(1, warm_modules=())
    try:
        with pytest.raises(EngineBusyError):
            pool.run(os._exit, 3, timeout_s=60)
        assert pool.run(abs, -2, timeout_s=60) == 2
    finally:
        pool.close()


# -- time zone, conditional cancel --------------------


def test_sessions_are_utc(tmp_path: Path) -> None:
    eng = Engine(temp_dir=tmp_path / "t", job_threads=1)
    try:
        with eng.cursor() as cur:
            assert cur.sql("SELECT current_setting('TimeZone')").fetchall() == [("UTC",)]
        with eng.background_cursor() as cur:
            assert cur.sql("SELECT current_setting('TimeZone')").fetchall() == [("UTC",)]
        with pytest.raises(EngineBusyError), eng.cursor():
            raise duckdb.FatalException("simulated")
        with eng.cursor() as cur:  # a recreated instance keeps the setting
            assert cur.sql("SELECT current_setting('TimeZone')").fetchall() == [("UTC",)]
    finally:
        eng.close()


def test_conditional_cancel_only_hits_the_latest_generation(tmp_path: Path) -> None:
    # A query_timeout_s well above what the probe query could plausibly take, even on a slow or
    # oversubscribed CI runner: the probe must finish normally, not be cut off by the engine's own
    # timeout (that would masquerade as the behavior this test is checking against).
    eng = Engine(temp_dir=tmp_path / "t", max_concurrent_queries=4, query_timeout_s=60.0)
    try:
        gen_a = next_generation()
        gen_b = next_generation()
        assert gen_b > gen_a
        started = threading.Event()

        def run_b() -> object:
            with eng.cursor("chart:c", generation=gen_b) as cur:
                started.set()
                sql = "SELECT count(*) FROM range(300000000) t(x) WHERE x % 7 = 3"
                return cur.sql(sql).fetchall()

        thread, errors, results = run_in_thread(run_b)
        assert started.wait(5)
        # Client A aborted after B was sent: cancelling A's generation is a no-op.
        assert eng.cancel("chart:c", gen_a) is False
        thread.join(60)
        assert not errors and results

        thread, errors, _ = run_in_thread(
            lambda: eng.query_arrow(SLOW_SQL, key="chart:c", generation=gen_b)
        )
        assert wait_for(lambda: "chart:c" in eng._by_key)
        assert eng.cancel("chart:c", gen_b) is True
        thread.join(10)
        assert isinstance(errors[0], QueryInterruptedError)
    finally:
        eng.close()
