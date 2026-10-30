"""Pipeline compiler: Pipeline spec -> one SQL query (a chain of CTEs) over Parquet.

- The output is an inlined query, not a CREATE VIEW: nothing is registered in the shared
  DuckDB, so there is no view lifecycle and no process-global state.
- CTEs are named by enabled-step position ("s0" is the source), never by user ids.
- The hidden row-order column RN keeps row order deterministic through every step.
- After each step, a DESCRIBE (bind only, cached) gives that step's output schema, so the next
  step validates field references against real names and types, and bind errors are reported
  against the step that caused them. The first failing step is "error"; later ones "blocked".
- A step with `materialize` reads a content-addressed Parquet file when one exists for its
  prefix SQL; otherwise it runs inline and is listed in `pending` (correctness never depends
  on materialization).
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import duckdb
import pyarrow as pa
from cachetools import LRUCache
from sqlglot import exp

from viz.chartdata.sql import (
    RN,
    alias,
    col,
    fn,
    lit,
    parquet_source,
    star_except,
    table,
    to_sql,
    with_ctes,
)
from viz.contracts import Dataset, Pipeline, SchemaField, SourceRef, StepError, StepSchema
from viz.engine import Engine
from viz.errors import InvalidSpecError, NotFoundError
from viz.ingest.importer import infer_semantic
from viz.storage import LocalStorage
from viz.transform import runtime
from viz.transform.steps import RESERVED_PREFIX, StepInput, StepProblem, build_step

StepStatus = Literal["ok", "error", "blocked", "disabled"]
MaterializedState = Literal["none", "ready", "pending"]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SourceBody:
    """A resolved source as a query that includes the hidden RN column."""

    body: exp.Select
    columns: tuple[SchemaField, ...]
    # sha256 of the body SQL. The SQL embeds versioned or content-addressed Parquet paths, so
    # the key changes whenever the data can.
    key: str
    # Rows without scanning (dataset versions only).
    row_count: int | None
    label: str


@dataclass(frozen=True)
class Materialization:
    step_id: str
    key: str
    sql: str
    path: Path


@dataclass
class CompiledStep:
    step_id: str
    status: StepStatus
    columns: list[SchemaField]
    error: StepError | None = None
    materialized: MaterializedState = "none"
    # CTE holding this step's output (the previous one for a disabled step); None on failure.
    cte: str | None = None


@dataclass
class CompiledPipeline:
    pipeline: Pipeline
    source: SourceBody
    ctes: list[tuple[str, exp.Query]]
    steps: list[CompiledStep]
    pending: list[Materialization] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(s.status in ("ok", "disabled") for s in self.steps)

    def _step(self, step_id: str) -> CompiledStep:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise InvalidSpecError(f"pipeline {self.pipeline.name!r} has no step {step_id!r}")

    def output_cte(self, step_id: str | None = None) -> tuple[str, list[SchemaField]]:
        """CTE name and visible columns after `step_id` (None = the final output)."""
        if step_id is None:
            failed = next((s for s in self.steps if s.status in ("error", "blocked")), None)
            if failed is not None:
                self._raise(failed)
            if not self.steps:
                return "s0", list(self.source.columns)
            last = self.steps[-1]
            return last.cte or "s0", last.columns
        step = self._step(step_id)
        if step.cte is None:
            self._raise(step)
        return step.cte, step.columns  # type: ignore[return-value]

    def input_cte(self, step_id: str | None) -> tuple[str, list[SchemaField]]:
        """CTE name and columns a step reads (None = the final output)."""
        if step_id is None:
            return self.output_cte(None)
        index = next((i for i, s in enumerate(self.steps) if s.step_id == step_id), None)
        if index is None:
            raise InvalidSpecError(f"pipeline {self.pipeline.name!r} has no step {step_id!r}")
        if index == 0:
            return "s0", list(self.source.columns)
        return self.output_cte(self.steps[index - 1].step_id)

    def _raise(self, step: CompiledStep) -> None:
        failed = step
        if step.status == "blocked":
            failed = next(s for s in self.steps if s.status == "error")
        message = failed.error.message if failed.error else "invalid step"
        raise InvalidSpecError(f"step {failed.step_id!r}: {message}")

    def query(self, cte: str, final: exp.Select) -> exp.Select:
        """`final` (which reads `cte`) with the CTEs it needs attached."""
        names = [n for n, _ in self.ctes]
        upto = names.index(cte) + 1
        return with_ctes(self.ctes[:upto], final)

    def body(self, step_id: str | None = None) -> exp.Select:
        cte, _ = self.output_cte(step_id)
        return self.query(cte, exp.select(exp.Star()).from_(table(cte)))

    def source_body(self, step_id: str | None = None) -> SourceBody:
        _, columns = self.output_cte(step_id)
        body = self.body(step_id)
        return SourceBody(
            body=body,
            columns=tuple(columns),
            key=sha(to_sql(body)),
            row_count=None,
            label=self.pipeline.name,
        )

    def step_schemas(self) -> list[StepSchema]:
        return [
            StepSchema(
                step_id=s.step_id,
                status=s.status,
                columns=s.columns,
                error=s.error,
                materialized=s.materialized,
            )
            for s in self.steps
        ]


DatasetLookup = Callable[[str], Dataset]

_STEP_ERRORS: tuple[type[Exception], ...] = (InvalidSpecError, NotFoundError, *runtime.USER_ERRORS)

_value_cache: LRUCache[str, list[str | None]] = LRUCache(maxsize=512)
_value_lock = threading.Lock()


class Compiler:
    """Compiles pipelines and resolves sources for one request."""

    def __init__(
        self,
        engine: Engine,
        storage: LocalStorage,
        datasets: DatasetLookup,
        pipelines: Iterable[Pipeline] = (),
    ) -> None:
        self.engine = engine
        self.storage = storage
        self._datasets = datasets
        self._pipelines = {p.id: p for p in pipelines}
        self._compiled: dict[str, CompiledPipeline] = {}
        self._visiting: list[str] = []

    # --- sources -------------------------------------------------------------------------

    def dataset(self, dataset_id: str) -> Dataset:
        return self._datasets(dataset_id)

    def dataset_body(self, dataset_id: str) -> SourceBody:
        dataset = self.dataset(dataset_id)
        version = dataset.current()
        path = self.storage.resolve(version.parquet_path)
        names = [c.name for c in version.columns]
        if any(n.lower() == "file_row_number" for n in names):
            # The data has its own file_row_number column, so DuckDB can't add one; fall back
            # to scan order (preserve_insertion_order keeps it stable for a single file).
            body = exp.select(exp.Star(), alias(row_order_fallback(), RN)).from_(
                parquet_source(path)
            )
        else:
            # Use the read_parquet option, not the virtual column: in DuckDB 2.0.0 filtering on
            # a window over the virtual file_row_number (QUALIFY, as dedupe does) hits an
            # internal error that invalidates the whole database.
            source = exp.Anonymous(
                this="read_parquet",
                expressions=[
                    exp.Literal.string(str(path)),
                    exp.EQ(this=exp.column("file_row_number"), expression=exp.true()),
                ],
            )
            body = exp.select(
                star_except("file_row_number"), alias(col("file_row_number"), RN)
            ).from_(source)
        columns = tuple(
            SchemaField(name=c.name, type=c.type, semantic=c.semantic) for c in version.columns
        )
        return SourceBody(
            body=body,
            columns=columns,
            key=sha(to_sql(body)),
            row_count=version.row_count,
            label=dataset.name,
        )

    def pipeline(self, pipeline_id: str) -> Pipeline:
        found = self._pipelines.get(pipeline_id)
        if found is None:
            raise NotFoundError(
                f"pipeline {pipeline_id} wasn't sent with the request; include it in `pipelines`"
            )
        return found

    def source_body(self, ref: SourceRef) -> SourceBody:
        if ref.kind == "dataset":
            return self.dataset_body(ref.id)
        compiled = self.compile(self.pipeline(ref.id))
        return compiled.source_body()

    # --- compilation ---------------------------------------------------------------------

    def compile(self, pipeline: Pipeline) -> CompiledPipeline:
        cached = self._compiled.get(pipeline.id)
        if cached is not None and cached.pipeline is pipeline:
            return cached
        if pipeline.id in self._visiting:
            chain = " -> ".join([*self._visiting, pipeline.id])
            raise InvalidSpecError(f"pipelines reference each other in a cycle: {chain}")
        self._visiting.append(pipeline.id)
        try:
            compiled = self._compile(pipeline)
        finally:
            self._visiting.pop()
        self._compiled[pipeline.id] = compiled
        return compiled

    def _compile(self, pipeline: Pipeline) -> CompiledPipeline:
        source = self.source_body(pipeline.source)
        ctes: list[tuple[str, exp.Query]] = [("s0", source.body)]
        current, columns = "s0", list(source.columns)
        steps: list[CompiledStep] = []
        pending: list[Materialization] = []
        failed = False
        for step in pipeline.steps:
            if failed:
                steps.append(CompiledStep(step.id, "blocked", []))
                continue
            if not step.enabled:
                steps.append(CompiledStep(step.id, "disabled", list(columns), cte=current))
                continue
            name = f"s{len(ctes)}"
            try:
                query = build_step(step, StepInput(self, current, list(columns), list(ctes)))
                trial = [*ctes, (name, query)]
                sql = to_sql(with_ctes(trial, exp.select(exp.Star()).from_(table(name))))
                out = self.describe_sql(sql)
                state: MaterializedState = "none"
                if step.materialize:
                    key = sha(sql)[:40]
                    path = runtime.derived_path(self.storage, key)
                    if path.is_file():
                        trial[-1] = (name, exp.select(exp.Star()).from_(parquet_source(path)))
                        state = "ready"
                    else:
                        pending.append(Materialization(step.id, key, sql, path))
                        state = "pending"
            except StepProblem as problem:
                error = StepError(
                    message=problem.message,
                    field=problem.field,
                    start=problem.start,
                    end=problem.end,
                )
                steps.append(CompiledStep(step.id, "error", [], error=error))
                failed = True
                continue
            except _STEP_ERRORS as exc:
                # A missing join/union source or a nested pipeline problem belongs to this step.
                viz_error = isinstance(exc, InvalidSpecError | NotFoundError)
                message = str(exc) if viz_error else runtime.clean_error(exc)
                steps.append(CompiledStep(step.id, "error", [], error=StepError(message=message)))
                failed = True
                continue
            ctes, current, columns = trial, name, out
            steps.append(CompiledStep(step.id, "ok", list(out), materialized=state, cte=name))
        return CompiledPipeline(pipeline, source, ctes, steps, pending)

    # --- engine helpers used by step builders --------------------------------------------

    def describe_sql(self, sql: str) -> list[SchemaField]:
        """Visible output columns of a query that carries RN."""
        described = runtime.describe(self.engine, sql)
        names = [name for name, _ in described]
        if RN not in names:  # pragma: no cover - a builder bug, not a user error
            raise AssertionError("step output lost the row-order column")
        fields: list[SchemaField] = []
        seen: set[str] = set()
        for name, duck_type in described:
            if name == RN:
                continue
            if name.lower().startswith(RESERVED_PREFIX):
                raise StepProblem(f"names starting with {RESERVED_PREFIX!r} are reserved")
            if name.lower() in seen:
                raise StepProblem(f"the output has two columns named {name!r}", field=name)
            seen.add(name.lower())
            fields.append(
                SchemaField(name=name, type=duck_type, semantic=infer_semantic(name, duck_type))
            )
        if not fields:
            # e.g. a pivot with no index over data with no values in the pivot column.
            raise StepProblem("this step leaves no columns")
        return fields

    def describe(self, query: exp.Expr) -> tuple[tuple[str, str], ...]:
        return runtime.describe(self.engine, to_sql(query))

    def distinct_values(self, query: exp.Expr) -> list[str | None]:
        sql = to_sql(query)
        with _value_lock:
            hit = _value_cache.get(sql)
        if hit is not None:
            return hit
        result: pa.Table = runtime.query_arrow(self.engine, sql)
        values = [None if v is None else str(v) for v in result.column(0).to_pylist()]
        with _value_lock:
            _value_cache[sql] = values
        return values

    def check_strptime_format(self, fmt: str) -> None:
        probe = exp.select(fn("try_strptime", lit("2000-01-01"), lit(fmt)))
        try:
            with self.engine.cursor() as cur:
                cur.sql(to_sql(probe)).fetchall()
        except duckdb.InvalidInputException as exc:
            raise StepProblem(f"invalid date format {fmt!r}: {runtime.clean_error(exc)}") from exc


def row_order_fallback() -> exp.Expr:
    return exp.Sub(this=exp.Window(this=exp.RowNumber(), over="OVER"), expression=lit(1))
