"""Differential tests: compiled pipeline steps vs pandas on random small frames."""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from viz.chartdata.sql import to_sql
from viz.contracts import Dataset, DatasetSource, DatasetVersion, Pipeline, SourceKind
from viz.engine import Engine
from viz.ingest.importer import profile_parquet
from viz.storage import LocalStorage
from viz.transform.compiler import Compiler
from viz.transform.source import pipeline_source

_ids = itertools.count()


class Env:
    def __init__(self, root: Path) -> None:
        self.engine = Engine(temp_dir=root / "tmp", max_concurrent_queries=4)
        self.storage = LocalStorage(root / "data")
        self.storage.ensure()
        self.datasets: dict[str, Dataset] = {}

    def run(self, frame: pd.DataFrame, steps: list[dict[str, Any]]) -> pd.DataFrame:
        dataset_id = f"d{next(_ids)}"
        rel = self.storage.parquet_rel(dataset_id, 1)
        path = self.storage.resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        schema = pa.schema([("k", pa.string()), ("x", pa.float64()), ("y", pa.int64())])
        pq.write_table(pa.Table.from_pandas(frame, schema=schema, preserve_index=False), path)
        with self.engine.cursor() as cur:
            columns, rows = profile_parquet(cur, path)
        self.datasets[dataset_id] = Dataset(
            id=dataset_id,
            name=dataset_id,
            source=DatasetSource(kind=SourceKind.CSV, file_name="x.csv"),
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
        for i, step in enumerate(steps):
            step.setdefault("id", f"s{i}")
        pipeline = Pipeline.model_validate(
            {"id": "p", "source": {"kind": "dataset", "id": dataset_id}, "steps": steps}
        )
        compiled = Compiler(self.engine, self.storage, self.datasets.__getitem__).compile(pipeline)
        assert compiled.ok, [s.error for s in compiled.steps]
        query, _, _ = pipeline_source(compiled, ordered=True)
        with self.engine.cursor() as cur:
            return cur.sql(to_sql(query)).to_arrow_table().to_pandas()


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Env]:
    environment = Env(tmp_path_factory.mktemp("diff"))
    yield environment
    environment.engine.close()


frames = st.lists(
    st.tuples(
        st.sampled_from(["a", "b", "c", None]),
        st.one_of(st.none(), st.floats(-1e6, 1e6, allow_nan=False).map(lambda v: round(v, 3))),
        st.integers(-5, 5),
    ),
    min_size=0,
    max_size=25,
).map(lambda rows: pd.DataFrame(rows, columns=["k", "x", "y"]).astype({"y": "int64"}))

SETTINGS = settings(
    max_examples=80,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def records(frame: pd.DataFrame) -> list[tuple[Any, ...]]:
    def norm(v: Any) -> Any:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        if isinstance(v, float):
            return round(v, 6)
        return v

    return [tuple(norm(v) for v in row) for row in frame.astype(object).itertuples(index=False)]


@SETTINGS
@given(frames, st.booleans())
def test_sort_matches_pandas_stable_sort(env: Env, frame: pd.DataFrame, desc: bool) -> None:
    out = env.run(frame, [{"op": "sort", "keys": [{"field": "y", "descending": desc}]}])
    expected = frame.sort_values("y", ascending=not desc, kind="stable", na_position="last")
    assert records(out) == records(expected)


@SETTINGS
@given(frames, st.sampled_from(["first", "last"]))
def test_dedupe_matches_pandas(env: Env, frame: pd.DataFrame, keep: str) -> None:
    out = env.run(frame, [{"op": "dedupe", "fields": ["k"], "keep": keep}])
    expected = frame.drop_duplicates(subset=["k"], keep=keep)  # type: ignore[call-overload]
    assert records(out) == records(expected)


@SETTINGS
@given(frames)
def test_group_aggregate_matches_pandas(env: Env, frame: pd.DataFrame) -> None:
    steps = [
        {
            "op": "group_aggregate",
            "by": ["k"],
            "aggregations": [
                {"fn": "count", "asName": "n"},
                {"field": "x", "fn": "sum", "asName": "sx"},
                {"field": "x", "fn": "min", "asName": "mn"},
                {"field": "y", "fn": "max", "asName": "my"},
                {"field": "x", "fn": "mean", "asName": "ax"},
            ],
        }
    ]
    out = env.run(frame, steps)
    grouped = frame.groupby("k", dropna=False, sort=True)
    expected = pd.DataFrame(
        {
            "n": grouped.size(),
            # SQL SUM of no values is NULL (pandas gives 0); min_count=1 matches SQL.
            "sx": grouped["x"].sum(min_count=1),
            "mn": grouped["x"].min(),
            "my": grouped["y"].max(),
            "ax": grouped["x"].mean(),
        }
    ).reset_index()
    assert records(out.sort_values("k", na_position="last")) == records(
        expected.sort_values("k", na_position="last")
    )


@SETTINGS
@given(frames, st.floats(-1e6, 1e6), st.floats(0, 1e6))
def test_range_filter_matches_pandas(
    env: Env, frame: pd.DataFrame, lo: float, width: float
) -> None:
    hi = lo + width
    out = env.run(
        frame,
        [{"op": "filter", "predicates": [{"op": "range", "field": "x", "min": lo, "max": hi}]}],
    )
    expected = frame[(frame["x"] >= lo) & (frame["x"] <= hi)]
    assert records(out) == records(expected)


@SETTINGS
@given(frames)
def test_zscore_matches_pandas(env: Env, frame: pd.DataFrame) -> None:
    out = env.run(frame, [{"op": "normalize", "fields": ["x"], "method": "zscore"}])
    std = frame["x"].std(ddof=1)
    mean = frame["x"].mean()
    # A constant column has zero spread; rounding can leave either side with ~1e-16 instead of
    # exactly 0 (x = [0.938] * 3: pandas ~1e-16, DuckDB 0 -> NULL). Treat that as zero.
    if pd.isna(std) or std <= 1e-12 * (abs(mean) if not pd.isna(mean) else 0.0):
        values = [v for v in out["x"].tolist() if v is not None and not math.isnan(v)]
        assert all(math.isfinite(v) for v in values)  # NULL, or noise-level but finite
        return
    expected = (frame["x"] - frame["x"].mean()) / std
    got = out["x"].tolist()
    for a, b in zip(got, expected.tolist(), strict=True):
        if b is None or (isinstance(b, float) and math.isnan(b)):
            assert a is None or math.isnan(a)
        else:
            assert a == pytest.approx(b, rel=1e-9, abs=1e-9)


@SETTINGS
@given(frames)
def test_forward_fill_matches_pandas(env: Env, frame: pd.DataFrame) -> None:
    out = env.run(frame, [{"op": "fill_missing", "fills": [{"field": "x", "strategy": "forward"}]}])
    assert records(out) == records(frame.assign(x=frame["x"].ffill()))
