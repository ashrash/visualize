"""Fixtures for compiler tests: small Parquet datasets built from test-controlled SQL."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from viz.contracts import Dataset, DatasetSource, DatasetVersion, Pipeline, SourceKind
from viz.engine import Engine
from viz.ingest.importer import profile_parquet
from viz.storage import LocalStorage
from viz.transform.compiler import CompiledPipeline, Compiler
from viz.transform.source import pipeline_source


class World:
    """A data dir with datasets registered in memory (no metadata DB needed)."""

    def __init__(self, engine: Engine, storage: LocalStorage) -> None:
        self.engine = engine
        self.storage = storage
        self.datasets: dict[str, Dataset] = {}

    def dataset(self, dataset_id: str, select_sql: str) -> Dataset:
        rel = self.storage.parquet_rel(dataset_id, 1)
        path = self.storage.resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.engine.cursor() as cur:
            cur.sql(select_sql).write_parquet(str(path))
        columns, rows = profile_parquet(self.engine, path)
        dataset = Dataset(
            id=dataset_id,
            name=dataset_id,
            source=DatasetSource(kind=SourceKind.CSV, file_name=f"{dataset_id}.csv"),
            versions=[
                DatasetVersion(
                    n=1,
                    parquet_path=rel,
                    row_count=rows,
                    columns=columns,
                    imported_at=datetime.now(UTC),
                )
            ],
        )
        self.datasets[dataset_id] = dataset
        return dataset

    def lookup(self, dataset_id: str) -> Dataset:
        from viz.errors import NotFoundError

        if dataset_id not in self.datasets:
            raise NotFoundError(f"dataset {dataset_id} not found")
        return self.datasets[dataset_id]

    def compiler(self, pipelines: list[Pipeline] | None = None) -> Compiler:
        return Compiler(self.engine, self.storage, self.lookup, pipelines or [])

    def compile(self, pipeline: Pipeline, others: list[Pipeline] | None = None) -> CompiledPipeline:
        return self.compiler([pipeline, *(others or [])]).compile(pipeline)

    def rows(self, compiled: CompiledPipeline, step_id: str | None = None) -> list[dict[str, Any]]:
        from sqlglot import exp

        from viz.chartdata.sql import to_sql

        query, _, _ = pipeline_source(compiled, ordered=True, step_id=step_id)
        assert isinstance(query, exp.Select)
        with self.engine.cursor() as cur:
            table = cur.sql(to_sql(query)).to_arrow_table()
        result: list[dict[str, Any]] = table.to_pylist()
        return result

    def run(
        self, steps: list[dict[str, Any]], source: str = "t", others: list[Pipeline] | None = None
    ) -> tuple[CompiledPipeline, list[dict[str, Any]]]:
        pipeline = make_pipeline(steps, source)
        compiled = self.compile(pipeline, others)
        assert compiled.ok, [s.error for s in compiled.steps]
        return compiled, self.rows(compiled)


_counter = iter(range(1, 1_000_000))


def make_pipeline(
    steps: list[dict[str, Any]], source: str = "t", *, pipeline_id: str = "p", kind: str = "dataset"
) -> Pipeline:
    full = []
    for step in steps:
        item = dict(step)
        item.setdefault("id", f"step{next(_counter)}")
        full.append(item)
    return Pipeline.model_validate(
        {"id": pipeline_id, "source": {"kind": kind, "id": source}, "steps": full}
    )


@pytest.fixture
def world(engine: Engine, tmp_path: Path) -> Iterator[World]:
    storage = LocalStorage(tmp_path / "data")
    storage.ensure()
    yield World(engine, storage)


@pytest.fixture
def people(world: World) -> Dataset:
    return world.dataset(
        "t",
        """
        SELECT * FROM (VALUES
            (1, 'Ann',  'North', 10.0, DATE '2024-01-05', 'x'),
            (2, 'Bob',  'South', NULL, DATE '2024-01-17', 'y'),
            (3, 'Cid',  'North', 30.0, NULL,              'x'),
            (4, 'Dee',  NULL,    40.0, DATE '2024-02-20', 'z'),
            (5, 'Eve',  'South', 50.0, DATE '2024-03-03', 'x'),
            (6, 'Ann',  'North', 10.0, DATE '2024-01-05', 'x')
        ) AS v(id, name, region, score, joined, tag)
        """,
    )


PipeFn = Callable[..., Pipeline]


@pytest.fixture
def pipe() -> PipeFn:
    """Build a Pipeline from step dicts (ids are filled in)."""
    return make_pipeline
