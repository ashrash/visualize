"""Background jobs (imports, materialisation, exports) with progress and cancellation.

Owner: data-core. Register a handler with `@job_handler(JobKind.X, ParamsModel)`; submit with
`runner.submit(JobKind.X, ParamsModel(...), title=..., dedupe_key=...)`. Inside a handler use
`ctx.cursor()` for DuckDB, `ctx.progress()` for progress and `ctx.check_cancelled()` between
chunks of Python work.
"""

from viz.jobs.runner import (
    TERMINAL,
    InProcessJobRunner,
    JobCancelledError,
    JobContext,
    JobHandler,
    JobRunner,
    job_handler,
    job_result,
    registered_kinds,
)

__all__ = [
    "TERMINAL",
    "InProcessJobRunner",
    "JobCancelledError",
    "JobContext",
    "JobHandler",
    "JobRunner",
    "job_handler",
    "job_result",
    "registered_kinds",
]
