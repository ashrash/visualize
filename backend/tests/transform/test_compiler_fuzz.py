"""Hypothesis fuzzing of the pipeline compiler.

Random column names (quotes, spaces, unicode, SQL keywords, comment markers) and random valid
step sequences, generated against a tracked schema. Every sequence must compile and run, the
executed result must have exactly the columns inspect predicted, and the names we can predict
by hand must match.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from viz.chartdata.sql import to_sql
from viz.contracts import Dataset, DatasetSource, DatasetVersion, Pipeline, SourceKind
from viz.engine import Engine
from viz.errors import NotFoundError
from viz.ingest.importer import profile_parquet
from viz.storage import LocalStorage
from viz.transform.compiler import Compiler
from viz.transform.source import pipeline_source

KEYWORDS = ["select", "from", "order", "group", "where", "NULL", "true", "file_row_number"]
NAME_CHARS = st.characters(
    codec="utf-8", exclude_categories=("Cs", "Cc"), include_characters=" \"'`;-*/\\()[]."
)
names = st.one_of(
    st.sampled_from(KEYWORDS),
    st.text(NAME_CHARS, min_size=1, max_size=10),
    st.sampled_from(['a"b', "x'; DROP TABLE t; --", "sp ace", "ünï", "/*c*/", "a.b"]),
).filter(lambda n: n.strip() and not n.lower().startswith("__viz_"))

KINDS = ("int", "float", "str")


class Env:
    def __init__(self, engine: Engine, storage: LocalStorage) -> None:
        self.engine = engine
        self.storage = storage
        self.datasets: dict[str, Dataset] = {}
        self.ids = itertools.count()

    def lookup(self, dataset_id: str) -> Dataset:
        if dataset_id not in self.datasets:
            raise NotFoundError(dataset_id)
        return self.datasets[dataset_id]

    def add(self, columns: list[tuple[str, str]], rows: int) -> str:
        dataset_id = f"d{next(self.ids)}"
        arrays = []
        for i, (_, kind) in enumerate(columns):
            if kind == "int":
                arrays.append(
                    pa.array(
                        [None if r % 4 == 3 else (r * (i + 1)) % 5 for r in range(rows)], pa.int64()
                    )
                )
            elif kind == "float":
                arrays.append(
                    pa.array(
                        [None if r % 5 == 4 else r / (i + 2) for r in range(rows)], pa.float64()
                    )
                )
            else:
                arrays.append(
                    pa.array(
                        [None if r % 3 == 2 else f"v{r % 3}'\"" for r in range(rows)], pa.string()
                    )
                )
        table = pa.Table.from_arrays(arrays, names=[n for n, _ in columns])
        rel = self.storage.parquet_rel(dataset_id, 1)
        path = self.storage.resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)
        profiled, count = profile_parquet(self.engine, path)
        self.datasets[dataset_id] = Dataset(
            id=dataset_id,
            name=dataset_id,
            source=DatasetSource(kind=SourceKind.CSV, file_name="x.csv"),
            versions=[
                DatasetVersion(
                    n=1,
                    parquet_path=rel,
                    row_count=count,
                    columns=profiled,
                    imported_at=datetime.now(UTC),
                )
            ],
        )
        return dataset_id


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Env]:
    root: Path = tmp_path_factory.mktemp("fuzz")
    engine = Engine(temp_dir=root / "tmp", max_concurrent_queries=4, query_timeout_s=30.0)
    storage = LocalStorage(root / "data")
    storage.ensure()
    yield Env(engine, storage)
    engine.close()


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@st.composite
def schemas(draw: st.DrawFn) -> list[tuple[str, str]]:
    picked = draw(st.lists(names, min_size=1, max_size=5, unique_by=str.lower))
    return [(n, draw(st.sampled_from(KINDS))) for n in picked]


def fresh(draw: st.DrawFn, taken: list[tuple[str, str]]) -> str:
    used = {n.lower() for n, _ in taken}
    return draw(names.filter(lambda n: n.lower() not in used))


@st.composite
def step_sequences(
    draw: st.DrawFn, schema: list[tuple[str, str]], rows: int
) -> tuple[list[dict[str, Any]], list[str] | None]:
    """Steps plus the output names we expect (None once a step's names aren't predictable)."""
    cols = list(schema)
    steps: list[dict[str, Any]] = []
    predictable = True
    for i in range(draw(st.integers(0, 6))):
        numeric = [c for c in cols if c[1] in ("int", "float")]
        options = [
            "filter",
            "sort",
            "select",
            "rename",
            "cast",
            "fill",
            "drop",
            "dedupe",
            "calc",
            "group",
            "pivot",
            "self_join",
            "toggle_off",
        ]
        if numeric:
            options += ["normalize", "unpivot"]
        op = draw(st.sampled_from(options))
        name, kind = draw(st.sampled_from(cols))
        sid = f"s{i}"
        if op == "filter":
            steps.append(
                {
                    "id": sid,
                    "op": "filter",
                    "predicates": [{"op": "is_null", "field": name, "isNull": False}],
                }
            )
        elif op == "sort":
            steps.append(
                {
                    "id": sid,
                    "op": "sort",
                    "keys": [{"field": name, "descending": draw(st.booleans())}],
                }
            )
        elif op == "select":
            keep = draw(st.lists(st.sampled_from(cols), min_size=1, unique_by=lambda c: c[0]))
            steps.append({"id": sid, "op": "select", "fields": [c[0] for c in keep]})
            cols = keep
        elif op == "rename":
            new = fresh(draw, cols)
            steps.append({"id": sid, "op": "rename", "mapping": {name: new}})
            cols = [(new, k) if n == name else (n, k) for n, k in cols]
        elif op == "cast":
            steps.append({"id": sid, "op": "cast", "casts": [{"field": name, "to": "string"}]})
            cols = [(n, "str") if n == name else (n, k) for n, k in cols]
        elif op == "fill":
            value: Any = "x" if kind == "str" else 0
            steps.append(
                {
                    "id": sid,
                    "op": "fill_missing",
                    "fills": [{"field": name, "strategy": "value", "value": value}],
                }
            )
        elif op == "drop":
            steps.append({"id": sid, "op": "drop_missing", "fields": [name]})
        elif op == "dedupe":
            steps.append({"id": sid, "op": "dedupe", "fields": [name], "keep": "last"})
        elif op == "calc":
            new = fresh(draw, cols)
            expr = f"length({quote(name)})" if kind == "str" else f"{quote(name)} + 1"
            steps.append({"id": sid, "op": "calc", "name": new, "expression": expr})
            cols = [*cols, (new, "int" if kind == "str" else "float")]
        elif op == "group":
            new = fresh(draw, [(name, kind)])
            steps.append(
                {
                    "id": sid,
                    "op": "group_aggregate",
                    "by": [name],
                    "aggregations": [{"fn": "count", "asName": new}],
                }
            )
            cols = [(name, kind), (new, "int")]
        elif op == "normalize":
            target = draw(st.sampled_from(numeric))
            steps.append({"id": sid, "op": "normalize", "fields": [target[0]]})
            cols = [(n, "float") if n == target[0] else (n, k) for n, k in cols]
        elif op == "unpivot":
            target = draw(st.sampled_from(numeric))
            ids = [c for c in cols if c[0] != target[0]][:1]
            name_out = fresh(draw, ids)
            value_out = fresh(draw, [*ids, (name_out, "str")])
            steps.append(
                {
                    "id": sid,
                    "op": "unpivot",
                    "idFields": [c[0] for c in ids],
                    "valueFields": [target[0]],
                    "nameField": name_out,
                    "valueField": value_out,
                }
            )
            cols = [*ids, (name_out, "str"), (value_out, target[1])]
        elif op == "pivot":
            others = [c for c in cols if c[0] != name]
            index = [c[0] for c in others[1:2]]
            # No index over empty data leaves no columns (a step error, tested elsewhere).
            if not others or (not index and rows == 0):
                continue
            steps.append(
                {
                    "id": sid,
                    "op": "pivot",
                    "index": index,
                    "columns": name,
                    "values": others[0][0],
                    "fn": "count",
                }
            )
            predictable = False
            break
        elif op == "self_join":
            # The joined pipeline has the original schema; join on a column both sides have.
            # Only keys whose type is unchanged: mismatched key types are a step error.
            original = {(n.lower(), k) for n, k in schema}
            keys = [c for c in cols if (c[0].lower(), c[1]) in original]
            if not keys:
                continue
            key = draw(st.sampled_from(keys))[0]
            right_key = next(n for n, _ in schema if n.lower() == key.lower())
            steps.append(
                {
                    "id": sid,
                    "op": "join",
                    "right": {"kind": "pipeline", "id": "base"},
                    "how": draw(st.sampled_from(["inner", "left", "right", "full"])),
                    "on": [{"left": key, "right": right_key}],
                }
            )
            predictable = False
            break
        elif op == "toggle_off":
            steps.append({"id": sid, "op": "select", "fields": [name], "enabled": False})
    return steps, ([n for n, _ in cols] if predictable else None)


@settings(
    max_examples=int(os.environ.get("VIZ_FUZZ_EXAMPLES", "60")),
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(data=st.data())
def test_random_pipelines_compile_run_and_match_inspect(env: Env, data: st.DataObject) -> None:
    schema = data.draw(schemas())
    rows = data.draw(st.integers(0, 12))
    dataset_id = env.add(schema, rows=rows)
    steps, expected = data.draw(step_sequences(schema, rows))
    base = Pipeline.model_validate(
        {"id": "base", "source": {"kind": "dataset", "id": dataset_id}, "steps": []}
    )
    pipeline = Pipeline.model_validate(
        {"id": "p", "source": {"kind": "dataset", "id": dataset_id}, "steps": steps}
    )
    compiled = Compiler(env.engine, env.storage, env.lookup, [pipeline, base]).compile(pipeline)
    assert compiled.ok, [(s.step_id, s.error) for s in compiled.steps if s.error]

    query, columns, _ = pipeline_source(compiled, ordered=True)
    with env.engine.cursor() as cur:
        result = cur.sql(to_sql(query)).to_arrow_table()
    predicted = list(columns)
    assert result.column_names == predicted
    if expected is not None:
        assert predicted == expected
    # Each step's predicted schema must match what DuckDB actually produces at that step.
    for step in compiled.steps:
        step_query, step_columns, _ = pipeline_source(compiled, ordered=True, step_id=step.step_id)
        with env.engine.cursor() as cur:
            names_out = cur.sql(to_sql(step_query)).limit(0).columns
        assert names_out == list(step_columns)
