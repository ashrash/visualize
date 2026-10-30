"""Follow-up fixes: explicit generations (S2), sticky conditional cancel (S3), request
scope + middleware (S4), memory/disk split (S7), same-owner cursors."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import anyio
import httpx
import pytest
from fastapi import FastAPI

from viz.engine import (
    Engine,
    RequestScopeMiddleware,
    current_owner,
    next_generation,
    request_scope,
)
from viz.errors import QueryInterruptedError

SLOW_SQL = "SELECT count(*) FROM range(600000000) t(x) WHERE x % 7 = 3"


@pytest.fixture
def eng(tmp_path: Path) -> Iterator[Engine]:
    engine = Engine(temp_dir=tmp_path / "t", max_concurrent_queries=4, query_timeout_s=30)
    yield engine
    engine.close()


def test_explicit_generation_beyond_the_counter_does_not_lock_the_key(eng: Engine) -> None:
    huge = next_generation() + 1_700_000_000_000  # e.g. a client's Date.now()
    eng.query_arrow("SELECT 1", key="chart:2", generation=huge)
    for _ in range(5):
        with request_scope():
            assert eng.query_arrow("SELECT 1 AS x", key="chart:2").num_rows == 1
    assert next_generation() > huge


def test_conditional_cancel_is_sticky_between_statements(eng: Engine) -> None:
    gen = next_generation()
    result: dict[str, object] = {}

    def request() -> None:
        eng.query_arrow("SELECT 1", key="chart:9", generation=gen)
        time.sleep(0.4)  # the cancel arrives now, between statements
        try:
            eng.query_arrow(SLOW_SQL, key="chart:9", generation=gen)
            result["q2"] = "ran"
        except QueryInterruptedError as exc:
            result["q2"] = exc.reason

    thread = threading.Thread(target=request)
    thread.start()
    time.sleep(0.2)
    assert eng.cancel("chart:9", gen) is True
    thread.join(30)
    assert result["q2"] == "cancelled"
    # A newer generation on the key is unaffected.
    assert eng.query_arrow("SELECT 1 AS x", key="chart:9", generation=next_generation()).num_rows
    assert eng.cancel("chart:9", gen) is False  # stale: a newer generation exists


def test_two_cursors_of_one_request_on_a_key_do_not_supersede(eng: Engine) -> None:
    with request_scope(), eng.cursor("chart:1") as first:
        with eng.cursor("chart:1") as second:
            assert second.sql("SELECT 2").fetchall() == [(2,)]
        assert first.sql("SELECT 1").fetchall() == [(1,)]


def test_request_scope_restores_the_previous_owner() -> None:
    with request_scope() as outer:
        with request_scope() as inner:
            assert current_owner() is inner
        assert current_owner() is outer
    assert current_owner() is None


def test_request_scope_exit_in_another_context_does_not_raise() -> None:
    import contextvars

    scope = request_scope()
    scope.__enter__()
    contextvars.copy_context().run(scope.__exit__, None, None, None)


def test_middleware_gives_sync_endpoints_an_owner_and_orders_requests(tmp_path: Path) -> None:
    eng = Engine(temp_dir=tmp_path / "t", max_concurrent_queries=4, query_timeout_s=30)
    app = FastAPI()
    app.add_middleware(RequestScopeMiddleware)
    log: dict[str, str] = {}

    @app.get("/owner")
    def owner() -> dict[str, bool]:
        return {"owner": current_owner() is not None}

    @app.get("/slow")
    def slow() -> dict[str, str]:  # a multi-query request
        try:
            eng.query_arrow("SELECT 1", key="chart:m")
            time.sleep(0.5)
            eng.query_arrow("SELECT 2", key="chart:m")
            log["slow"] = "ok"
        except QueryInterruptedError as exc:
            log["slow"] = exc.reason
        return log

    @app.get("/fast")
    def fast() -> dict[str, str]:
        eng.query_arrow("SELECT 3", key="chart:m")
        log["fast"] = "ok"
        return log

    async def main() -> bool:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            seen = (await client.get("/owner")).json()["owner"]

            async def later() -> None:
                await anyio.sleep(0.2)
                await client.get("/fast")

            async with anyio.create_task_group() as group:
                group.start_soon(client.get, "/slow")
                group.start_soon(later)
        return bool(seen)

    try:
        assert anyio.run(main) is True
        assert log == {"fast": "ok", "slow": "superseded"}
    finally:
        eng.close()


def _settings(eng: Engine, background: bool) -> tuple[str, str]:
    cm = eng.background_cursor() if background else eng.cursor()
    with cm as cur:
        row = cur.sql(
            "SELECT current_setting('memory_limit'), current_setting('max_temp_directory_size')"
        ).fetchall()[0]
    return str(row[0]), str(row[1])


def _gib(text: str) -> float:
    amount, unit = text.split(" ")
    return float(amount) * {"MiB": 1 / 1024, "GiB": 1.0, "TiB": 1024.0}[unit]


def test_explicit_memory_limit_and_temp_disk_are_split(tmp_path: Path) -> None:
    eng = Engine(temp_dir=tmp_path / "t", memory_limit="9GB", job_threads=2)
    try:
        main_mem, main_tmp = _settings(eng, False)
        bg_mem, bg_tmp = _settings(eng, True)
        total = _gib(main_mem) + _gib(bg_mem)
        assert total == pytest.approx(9e9 / 2**30, rel=0.05)  # DuckDB shows 1 decimal
        assert _gib(main_mem) == pytest.approx(2 * _gib(bg_mem), rel=0.06)
        assert "%" not in main_tmp and "%" not in bg_tmp
        assert _gib(main_tmp) == pytest.approx(2 * _gib(bg_tmp), rel=0.06)
    finally:
        eng.close()
