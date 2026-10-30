"""Perf budgets at 10M rows (CLAUDE.md §6). Opt in with VIZ_PERF=1; qa wires these into
scripts/bench.py. Budget: aggregated chart query p50 <= 300 ms."""

from __future__ import annotations

import os
import statistics
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from viz.chartdata.service import chart_data
from viz.contracts import ChartDataRequest, Dataset, DatasetSource, DatasetVersion, SourceKind
from viz.engine import Engine
from viz.ingest.importer import profile_parquet
from viz.storage import LocalStorage

pytestmark = pytest.mark.skipif(os.environ.get("VIZ_PERF") != "1", reason="set VIZ_PERF=1")
ROWS = int(os.environ.get("VIZ_PERF_ROWS", "10000000"))


class FakeDb:
    """Stands in for the metadata session: chart_data only looks the dataset up."""

    def __init__(self, dataset: Dataset) -> None:
        self.dataset = dataset


@pytest.fixture(scope="module")
def big(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Engine, LocalStorage, Dataset]]:
    root: Path = tmp_path_factory.mktemp("perf")
    storage = LocalStorage(root / "data")
    storage.ensure()
    engine = Engine(temp_dir=storage.temp_dir)
    rel = storage.parquet_rel("big", 1)
    path = storage.resolve(rel)
    path.parent.mkdir(parents=True)
    with engine.cursor() as cur:
        cur.sql(
            f"""
            SELECT TIMESTAMP '2020-01-01' + INTERVAL (i) SECOND AS ts,
                   'region_' || (i % 20) AS region, 'cat_' || (i % 500) AS category,
                   (i % 1000) / 10.0 AS price, (hash(i) % 100)::DOUBLE AS units
            FROM range({ROWS}) t(i)
            """
        ).write_parquet(str(path))
    columns, count = profile_parquet(engine, path)
    dataset = Dataset(
        id="big",
        name="big",
        source=DatasetSource(kind=SourceKind.CSV, file_name="big.csv"),
        versions=[
            DatasetVersion(
                n=1,
                parquet_path=rel,
                row_count=count,
                columns=columns,
                imported_at=datetime.now(UTC),
            )
        ],
    )
    yield engine, storage, dataset
    engine.close()


def _time(big: Any, body: dict[str, Any], runs: int = 7) -> float:
    engine, storage, dataset = big
    import viz.transform.source as source_module

    original = source_module.get_dataset
    source_module.get_dataset = lambda _db, _id: dataset  # type: ignore[assignment]
    try:
        samples = []
        for i in range(runs):
            # A new filter value each run defeats the response cache.
            request = ChartDataRequest.model_validate(
                {**body, "filters": [{"op": "range", "field": "price", "min": -1 - i}]}
            )
            started = time.perf_counter()
            chart_data(engine, storage, None, request, mark_cap=50_000)  # type: ignore[arg-type]
            samples.append((time.perf_counter() - started) * 1000)
    finally:
        source_module.get_dataset = original
    p50 = statistics.median(samples)
    print(f"\n{body['spec']['plotType']}: p50 {p50:.0f} ms, samples {[round(s) for s in samples]}")
    return p50


def _spec(plot: str, encodings: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "spec": {
            "id": plot,
            "plotType": plot,
            "source": {"kind": "dataset", "id": "big"},
            "encodings": encodings,
        },
        **extra,
    }


def test_aggregated_bar_p50_within_budget(big: Any) -> None:
    body = _spec(
        "bar",
        {
            "x": [{"field": "category"}],
            "y": [{"field": "units", "agg": "mean"}],
            "color": [{"field": "region"}],
        },
    )
    assert _time(big, body) <= 300


def test_monthly_line_p50_within_budget(big: Any) -> None:
    body = _spec("line", {"x": [{"field": "ts", "timeUnit": "month"}], "y": [{"field": "price"}]})
    assert _time(big, body) <= 300


def test_raw_timestamp_line_m4(big: Any) -> None:
    # 10M distinct x values: group_by, then M4 in SQL, then MinMaxLTTB.
    body = _spec(
        "line", {"x": [{"field": "ts"}], "y": [{"field": "price"}]}, viewport={"widthPx": 1200}
    )
    _time(big, body, runs=3)


def test_pipeline_source_aggregated(big: Any) -> None:
    pipeline = {
        "id": "p",
        "source": {"kind": "dataset", "id": "big"},
        "steps": [
            {"id": "f", "op": "filter", "expression": "units > 10"},
            {"id": "c", "op": "calc", "name": "revenue", "expression": "price * units"},
        ],
    }
    body = {
        "spec": {
            "id": "pp",
            "plotType": "bar",
            "source": {"kind": "pipeline", "id": "p"},
            "encodings": {"x": [{"field": "region"}], "y": [{"field": "revenue"}]},
        },
        "pipelines": [pipeline],
    }
    assert _time(big, body) <= 300
