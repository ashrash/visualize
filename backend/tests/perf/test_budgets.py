"""Perf budgets for the data path (CLAUDE.md §6). Opt-in: set VIZ_PERF=1 (and optionally
VIZ_PERF_ROWS, default 10,000,000). Generates a 20-column CSV in a temp dir, imports it as a
job, then times grid pages, including while a second import runs.

    VIZ_PERF=1 uv run pytest backend/tests/perf -s
"""

from __future__ import annotations

import os
import statistics
import threading
import time
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(os.environ.get("VIZ_PERF") != "1", reason="set VIZ_PERF=1")

ROWS = int(os.environ.get("VIZ_PERF_ROWS", "10000000"))
IMPORT_BUDGET_S = 60.0
PAGE_BUDGET_MS = 150.0


def generate_csv(path: Path, rows: int) -> None:
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
          SELECT
            i AS id,
            'cat_' || (i % 50) AS category,
            'region_' || (i % 7) AS region,
            'name_' || (hash(i) % 100000) AS name,
            (hash(i) % 1000000) / 100.0 AS amount,
            (hash(i * 3) % 10000) / 10.0 AS price,
            i % 1000 AS qty,
            (hash(i * 7) % 2000)::BIGINT - 1000 AS delta,
            DATE '2015-01-01' + (i % 3650)::INTEGER AS order_date,
            TIMESTAMP '2020-01-01' + to_seconds((i % 86400000)::BIGINT) AS ts,
            (i % 2 = 0) AS flag,
            (hash(i * 11) % 1000) / 1000.0 AS score,
            'sku_' || (i % 5000) AS sku,
            i % 13 AS bucket,
            (hash(i * 13) % 100000) / 7.0 AS metric_a,
            (hash(i * 17) % 100000) / 3.0 AS metric_b,
            CASE WHEN i % 10 = 0 THEN NULL ELSE i % 97 END AS maybe,
            'tag_' || (i % 3) AS tag,
            (i * 31) % 1000003 AS code,
            'x' || (i % 11) AS short
          FROM range({rows}) t(i)
        ) TO '{path.as_posix()}' (HEADER)
        """
    )
    con.close()


def timed_ms(fn: Any) -> float:
    started = time.perf_counter()
    fn()
    return (time.perf_counter() - started) * 1000


def test_import_and_grid_budgets(client: TestClient, tmp_path: Path) -> None:
    csv_path = tmp_path / "big data.csv"
    started = time.perf_counter()
    generate_csv(csv_path, ROWS)
    gen_s = time.perf_counter() - started
    size_mb = csv_path.stat().st_size / 2**20

    started = time.perf_counter()
    response = client.post("/api/datasets/import", json={"path": str(csv_path)})
    assert response.status_code == 202, response.text
    job = client.app.state.jobs.wait(response.json()["id"], 600)  # type: ignore[attr-defined]
    import_s = time.perf_counter() - started
    assert job.status == "succeeded", job.error
    dataset_id = job.result.dataset_id
    dataset = client.get(f"/api/datasets/{dataset_id}").json()
    assert dataset["versions"][0]["rowCount"] == ROWS

    def page(**body: Any) -> None:
        r = client.post(f"/api/datasets/{dataset_id}/rows", json={"limit": 200, **body})
        assert r.status_code == 200, r.text

    cases: dict[str, dict[str, Any]] = {
        "first page": {},
        "middle page": {"offset": ROWS // 2},
        "last page": {"offset": ROWS - 200},
        "sorted first page": {"sort": [{"field": "amount", "descending": True}]},
        "sorted deep page": {"sort": [{"field": "amount"}], "offset": ROWS // 2},
        "filtered page": {"filters": [{"op": "eq", "field": "category", "value": "cat_7"}]},
        "filtered deep page": {
            "filters": [{"op": "range", "field": "qty", "min": 100, "max": 500}],
            "offset": ROWS // 4,
        },
        "search page": {"search": "region_3"},
    }
    views = client.app.state.row_views  # type: ignore[attr-defined]
    engine = client.app.state.engine  # type: ignore[attr-defined]
    cold: dict[str, float] = {}
    scroll: dict[str, float] = {}
    build_ms: dict[str, float] = {}
    results: dict[str, float] = {}
    for name, body in cases.items():
        # First page: answered directly. The next page (the user scrolls) is direct too and
        # queues a view build for sorted/filtered views; after it, pages are range reads.
        first = {**body, "offset": 0}
        cold[name] = timed_ms(lambda b=first: page(**b))
        scroll[name] = timed_ms(lambda b=body: page(**{**b, "offset": b.get("offset", 0) + 200}))
        build_ms[name] = timed_ms(views.wait_idle)
        results[name] = statistics.median(timed_ms(lambda b=body: page(**b)) for _ in range(5))

    # A chart-style aggregate, alone and while a grid-view build runs in the background.
    parquet = client.app.state.storage.resolve(  # type: ignore[attr-defined]
        dataset["versions"][0]["parquetPath"]
    )
    agg_sql = (
        "SELECT category, sum(amount) AS s, count(*) AS n "
        f"FROM read_parquet('{parquet.as_posix()}')"
        " GROUP BY category"
    )
    alone = [timed_ms(lambda: engine.query_arrow(agg_sql)) for _ in range(10)]
    page(sort=[{"field": "price", "descending": True}], offset=200)  # queues a ~2 s build
    during_build: list[float] = []
    build_done = threading.Event()
    threading.Thread(target=lambda: (views.wait_idle(), build_done.set())).start()
    while not build_done.is_set() and len(during_build) < 100:
        during_build.append(timed_ms(lambda: engine.query_arrow(agg_sql)))

    # Pages while a second import of the same file runs as a job.
    response = client.post("/api/datasets/import", json={"path": str(csv_path)})
    second = response.json()["id"]
    during: list[float] = []
    stop = threading.Event()

    def watch() -> None:
        client.app.state.jobs.wait(second, 600)  # type: ignore[attr-defined]
        stop.set()

    watcher = threading.Thread(target=watch)
    watcher.start()
    time.sleep(0.2)
    while not stop.is_set() and len(during) < 200:
        during.append(timed_ms(lambda: page(offset=ROWS // 3)))
        during.append(
            timed_ms(lambda: page(filters=[{"op": "eq", "field": "tag", "value": "tag_1"}]))
        )
    watcher.join()

    print(f"\nrows={ROWS:,} csv={size_mb:,.0f} MB generated in {gen_s:.1f}s")
    print(f"import job (CSV -> Parquet + profile): {import_s:.1f}s (budget {IMPORT_BUDGET_S}s)")
    for name, ms in results.items():
        print(
            f"  {name:<20} first {cold[name]:7.1f} | next {scroll[name]:7.1f} | "
            f"view build {build_ms[name]:7.1f} | then p50 {ms:6.1f} ms"
        )
    print(
        f"  chart aggregate alone p50 {statistics.median(alone):.1f} ms; during a view build "
        f"n={len(during_build)} p50 {statistics.median(during_build or [0]):.1f} ms "
        f"max {max(during_build or [0]):.1f} ms"
    )
    if during:
        print(
            f"  during import: n={len(during)} p50 {statistics.median(during):.1f} ms "
            f"max {max(during):.1f} ms"
        )
    assert import_s <= IMPORT_BUDGET_S
    slow = {k: v for k, v in results.items() if v > PAGE_BUDGET_MS}
    assert not slow, slow
