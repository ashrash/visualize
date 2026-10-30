"""Materialization as a background job (JobKind.MATERIALIZE).

A pipeline step with `materialize: true` reads a content-addressed Parquet file when one
exists for its prefix (see compiler.py). This job writes the missing files: it compiles the
pipeline, writes the first pending prefix, and compiles again, so each later step's prefix
reads the earlier file instead of recomputing it. Files are named by a hash of the prefix SQL
plus the dataset versions it reads, so a finished file is never rewritten or stale.

The params carry the pipeline JSON (no live objects), so the handler can run in a queue
worker later.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from viz.contracts import JobKind, JobResult, Pipeline
from viz.errors import InvalidSpecError
from viz.jobs import JobContext, job_handler, job_result
from viz.transform import runtime
from viz.transform.source import make_compiler


class MaterializeParams(BaseModel):
    pipeline: Pipeline
    # Pipelines the source, joins or unions reference (as sent with the request).
    pipelines: list[Pipeline] = Field(default_factory=list)


def dedupe_key(first_pending_key: str) -> str:
    return f"materialize:{first_pending_key}"


@job_handler(JobKind.MATERIALIZE, MaterializeParams)
def run_materialize(ctx: JobContext, params: MaterializeParams) -> JobResult:
    pipelines = [params.pipeline, *params.pipelines]
    total = sum(1 for s in params.pipeline.steps if s.materialize and s.enabled)
    done = 0
    with ctx.session() as db:
        # At most one write per materialize step, plus the final check.
        for _ in range(len(params.pipeline.steps) + 1):
            compiler = make_compiler(ctx.engine, db, ctx.storage, pipelines)
            compiled = compiler.compile(params.pipeline)
            if not compiled.pending:
                break
            item = compiled.pending[0]
            ctx.progress(done / max(1, total), f"Materializing step {item.step_id}")
            if not item.path.is_file():
                try:
                    with ctx.cursor() as cur:
                        runtime.write_parquet(cur, ctx.storage, item.sql, item.rel)
                except runtime.USER_ERRORS as exc:
                    # e.g. a text-length guard: the job fails with the readable message,
                    # not as a crash with a traceback.
                    raise InvalidSpecError(runtime.clean_error(exc)) from exc
            done += 1
    return job_result(pipeline_id=params.pipeline.id)
