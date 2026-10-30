"""Job endpoints: list, inspect, cancel, and follow progress over SSE."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from viz.contracts import Job
from viz.jobs import JobRunner

router = APIRouter(prefix="/jobs", tags=["jobs"])


def get_jobs(request: Request) -> JobRunner:
    return cast(JobRunner, request.app.state.jobs)


JobsDep = Annotated[JobRunner, Depends(get_jobs)]


@router.get("", response_model=list[Job])
def list_jobs(jobs: JobsDep, active: Annotated[bool, Query()] = False) -> list[Job]:
    return jobs.list(active=active)


@router.get("/{job_id}", response_model=Job)
def get_job(job_id: str, jobs: JobsDep) -> Job:
    return jobs.get(job_id)


@router.post("/{job_id}/cancel", response_model=Job, status_code=202)
def cancel_job(job_id: str, jobs: JobsDep) -> Job:
    return jobs.cancel(job_id)


def _existing_job(job_id: str, jobs: JobsDep) -> str:
    """Runs before the stream starts, so an unknown id is a plain 404."""
    jobs.get(job_id)
    return job_id


@router.get("/{job_id}/events", response_class=EventSourceResponse)
async def job_events(
    job_id: Annotated[str, Depends(_existing_job)], jobs: JobsDep
) -> AsyncIterator[ServerSentEvent]:
    """`event: job` with the full Job JSON on every change; closes after the final state."""
    async for job in jobs.watch(job_id):
        if job is None:
            yield ServerSentEvent(comment="ping")
        else:
            yield ServerSentEvent(event="job", id=str(job.seq), raw_data=job.model_dump_json())
