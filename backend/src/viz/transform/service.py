"""Pipeline services behind /api/pipelines: inspect, preview, formula check, materialize."""

from __future__ import annotations

import time

import pyarrow as pa
from sqlalchemy.orm import Session
from sqlglot import exp

from viz.chartdata.arrow import browser_table
from viz.chartdata.sql import RN, alias, col, ordered, predicate_sql, star_except, table, to_sql
from viz.contracts import (
    FormulaCheckRequest,
    FormulaCheckResult,
    FormulaFunction,
    JobKind,
    MaterializeRequest,
    MaterializeResult,
    PipelineInspectRequest,
    PipelineInspectResult,
    PipelinePreviewMeta,
    PipelinePreviewRequest,
    SchemaField,
    StepError,
)
from viz.contracts.pipeline import CastStep
from viz.engine import Engine
from viz.errors import InvalidSpecError
from viz.jobs import JobRunner
from viz.storage import LocalStorage
from viz.transform import runtime
from viz.transform.compiler import CompiledPipeline
from viz.transform.formula import FormulaError, function_docs, validate_formula
from viz.transform.materialize import MaterializeParams, dedupe_key
from viz.transform.source import make_compiler
from viz.transform.steps import cast_expression


def _compile(
    engine: Engine, db: Session, storage: LocalStorage, request: PipelineInspectRequest
) -> CompiledPipeline:
    compiler = make_compiler(engine, db, storage, [request.pipeline, *request.pipelines])
    return compiler.compile(request.pipeline)


def inspect(
    engine: Engine, db: Session, storage: LocalStorage, request: PipelineInspectRequest
) -> PipelineInspectResult:
    compiled = _compile(engine, db, storage, request)
    final: list[SchemaField] = list(compiled.source.columns)
    for step in compiled.steps:
        if step.status in ("ok", "disabled"):
            final = step.columns
        else:
            break
    sql = None
    if request.include_sql and compiled.ok:
        sql = to_sql(compiled.body())
    return PipelineInspectResult(
        ok=compiled.ok,
        source_columns=list(compiled.source.columns),
        steps=compiled.step_schemas(),
        columns=final,
        sql=sql,
    )


def _resolve(columns: list[SchemaField], name: str) -> str:
    lowered = name.lower()
    for c in columns:
        if c.name.lower() == lowered:
            return c.name
    raise InvalidSpecError(f"unknown field {name!r}")


def preview(
    engine: Engine,
    db: Session,
    storage: LocalStorage,
    request: PipelinePreviewRequest,
    *,
    page_max: int,
) -> tuple[pa.Table, PipelinePreviewMeta]:
    started = time.perf_counter()
    compiled = _compile(
        engine,
        db,
        storage,
        PipelineInspectRequest(pipeline=request.pipeline, pipelines=request.pipelines),
    )
    cte, columns = compiled.output_cte(request.step_id)
    limit = min(request.limit, page_max)

    condition = None
    if request.filters:
        types = {c.name: c.type for c in columns}
        parts = []
        for p in request.filters:
            name = _resolve(columns, p.field)
            parts.append(exp.Paren(this=predicate_sql(p, col(name), duck_type=types[name])))
        condition = exp.and_(*parts)
    keys = [
        ordered(col(_resolve(columns, k.field)), desc=k.descending, nulls_last=k.nulls_last)
        for k in request.sort
    ]
    page = exp.select(star_except(RN)).from_(table(cte))
    count = exp.select(alias(exp.Count(this=exp.Star()), "n")).from_(table(cte))
    if condition is not None:
        page = page.where(condition)
        count = count.where(condition.copy())
    page = page.order_by(*keys, ordered(col(RN))).limit(limit).offset(request.offset)

    # One slot per pipeline step: previews of different steps must not cancel each other.
    key = f"preview:{request.pipeline.id}:{request.step_id or ''}"
    data = runtime.query_arrow(engine, to_sql(compiled.query(cte, page)), key=key)
    total = runtime.query_arrow(engine, to_sql(compiled.query(cte, count)))
    warnings = _cast_warnings(engine, compiled, request.step_id)
    meta = PipelinePreviewMeta(
        step_id=request.step_id,
        offset=request.offset,
        limit=limit,
        total_rows=int(total.column(0)[0].as_py()),
        columns=columns,
        ms=round((time.perf_counter() - started) * 1000, 2),
        warnings=warnings,
    )
    return browser_table(data), meta


def _cast_warnings(engine: Engine, compiled: CompiledPipeline, step_id: str | None) -> list[str]:
    """For a cast step, how many non-NULL values failed to convert (became NULL)."""
    if step_id is None:
        return []
    step = next((s for s in compiled.pipeline.steps if s.id == step_id), None)
    if not isinstance(step, CastStep) or not step.enabled or not step.casts:
        return []
    cte, columns = compiled.input_cte(step_id)
    items: list[exp.Expr] = []
    names: list[str] = []
    for i, item in enumerate(step.casts):
        name = _resolve(columns, item.field)
        converted = cast_expression(col(name), item.to, item.format)
        failed = exp.and_(
            exp.Not(this=exp.Is(this=col(name), expression=exp.Null())),
            exp.Is(this=converted, expression=exp.Null()),
        )
        items.append(
            alias(
                exp.Filter(this=exp.Count(this=exp.Star()), expression=exp.Where(this=failed)),
                f"c{i}",
            )
        )
        names.append(name)
    counts = runtime.query_arrow(
        engine, to_sql(compiled.query(cte, exp.select(*items).from_(table(cte))))
    ).to_pylist()[0]
    warnings = []
    for i, name in enumerate(names):
        failed_count = int(counts[f"c{i}"] or 0)
        if failed_count:
            warnings.append(
                f"{failed_count:,} value(s) in {name!r} couldn't be converted and are now empty"
            )
    return warnings


def check_formula(
    engine: Engine, db: Session, storage: LocalStorage, request: FormulaCheckRequest
) -> FormulaCheckResult:
    compiled = _compile(
        engine,
        db,
        storage,
        PipelineInspectRequest(pipeline=request.pipeline, pipelines=request.pipelines),
    )
    try:
        cte, columns = compiled.input_cte(request.before_step_id)
    except InvalidSpecError as exc:
        return FormulaCheckResult(
            ok=False, error=StepError(message=str(exc)), output_type=None, fields=[]
        )
    try:
        formula = validate_formula(request.expression, [c.name for c in columns])
    except FormulaError as exc:
        error = StepError(message=exc.message, start=exc.start, end=exc.end)
        return FormulaCheckResult(ok=False, error=error, output_type=None, fields=[])
    probe = compiled.query(cte, exp.select(alias(formula.expression, "v")).from_(table(cte)))
    try:
        described = runtime.describe(engine, to_sql(probe))
    except runtime.USER_ERRORS as exc:
        error = StepError(message=runtime.clean_error(exc))
        return FormulaCheckResult(
            ok=False, error=error, output_type=None, fields=list(formula.fields)
        )
    return FormulaCheckResult(
        ok=True, error=None, output_type=described[0][1], fields=list(formula.fields)
    )


def functions() -> list[FormulaFunction]:
    return [
        FormulaFunction(
            name=d.name, category=d.category, signature=d.signature, description=d.description
        )
        for d in function_docs()
    ]


def materialize(
    engine: Engine,
    db: Session,
    storage: LocalStorage,
    jobs: JobRunner,
    request: MaterializeRequest,
) -> MaterializeResult:
    """Start (or join) the job that writes every pending `materialize` step, in order.

    One job per pipeline does all its pending steps (each later prefix reads the earlier
    file), so every pending step maps to the same job id. The dedupe key is the first
    pending prefix's key: identical requests share one running job.
    """
    compiled = make_compiler(engine, db, storage, [request.pipeline, *request.pipelines]).compile(
        request.pipeline
    )
    ready = [s.step_id for s in compiled.steps if s.materialized == "ready"]
    if not compiled.pending:
        return MaterializeResult(jobs={}, ready=ready)
    params = MaterializeParams(pipeline=request.pipeline, pipelines=request.pipelines)
    job = jobs.submit(
        JobKind.MATERIALIZE,
        params,
        title=f"Materialize {request.pipeline.name}",
        dedupe_key=dedupe_key(compiled.pending[0].key),
    )
    return MaterializeResult(jobs={m.step_id: job.id for m in compiled.pending}, ready=ready)
