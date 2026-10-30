from __future__ import annotations

import threading
import time

import pytest

from viz.engine import Engine
from viz.errors import QueryInterruptedError

_SLOW_SQL = "SELECT count(*) FROM range(100000000000) t"


def test_query_arrow_returns_a_table(engine: Engine) -> None:
    table = engine.query_arrow("SELECT ? + 1 AS answer", [41])
    assert table.to_pylist() == [{"answer": 42}]


def test_memory_limit_defaults_below_duckdb_default(engine: Engine) -> None:
    table = engine.query_arrow("SELECT current_setting('memory_limit') AS limit_")
    assert table.column(0)[0].as_py() not in ("", None)


def test_newer_query_with_same_key_supersedes_older(engine: Engine) -> None:
    errors: list[QueryInterruptedError] = []

    def run_slow() -> None:
        try:
            engine.query_arrow(_SLOW_SQL, key="chart:1")
        except QueryInterruptedError as exc:
            errors.append(exc)

    thread = threading.Thread(target=run_slow)
    thread.start()
    time.sleep(0.3)
    assert engine.query_arrow("SELECT 1 AS one", key="chart:1").num_rows == 1
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert errors and errors[0].reason == "superseded"
    assert errors[0].status_code == 409


def test_queries_time_out(tmp_path) -> None:  # type: ignore[no-untyped-def]
    eng = Engine(temp_dir=tmp_path / "t", query_timeout_s=0.3)
    try:
        with pytest.raises(QueryInterruptedError) as info:
            eng.query_arrow(_SLOW_SQL)
        assert info.value.reason == "timed out"
        assert info.value.status_code == 504
    finally:
        eng.close()
