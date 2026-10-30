"""Job runner: lifecycle, progress, failure, cancellation, concurrency, dedupe, SSE."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from viz.contracts import JobKind, JobResult, JobStatus
from viz.errors import InvalidSpecError
from viz.jobs import InProcessJobRunner, JobContext, job_result, runner

SLOW_SQL = "SELECT count(*) FROM range(100000000000) t"


class Params(BaseModel):
    mode: str
    steps: int = 3


def handler(ctx: JobContext, params: Params) -> JobResult:
    if params.mode == "fail":
        raise InvalidSpecError("bad input")
    if params.mode == "crash":
        raise RuntimeError("boom")
    if params.mode == "slow_sql":
        with ctx.cursor() as cur:
            cur.sql(SLOW_SQL).fetchall()
    if params.mode == "sleep":
        for _ in range(200):
            ctx.check_cancelled()
            time.sleep(0.02)
    for i in range(params.steps):
        ctx.progress((i + 1) / params.steps, f"step {i + 1}")
    return job_result(warnings=[params.mode])


@pytest.fixture
def jobs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> Iterator[InProcessJobRunner]:
    monkeypatch.setitem(
        runner._HANDLERS, JobKind.MATERIALIZE, runner._Registration(Params, handler)
    )
    yield client.app.state.jobs  # type: ignore[attr-defined]


def submit(jobs: InProcessJobRunner, mode: str, **kwargs: Any) -> str:
    return jobs.submit(JobKind.MATERIALIZE, Params(mode=mode), title=mode, **kwargs).id


def test_success_records_progress_and_result(jobs: InProcessJobRunner) -> None:
    job_id = submit(jobs, "ok")
    done = jobs.wait(job_id, 10)
    assert done.status == JobStatus.SUCCEEDED
    assert done.progress == 1.0 and done.message == "Done"
    assert done.result is not None and done.result.warnings == ["ok"]
    assert done.started_at is not None and done.finished_at is not None
    assert done.seq >= 5  # running + 3 progress updates + finish


def test_failures_keep_a_readable_error(jobs: InProcessJobRunner) -> None:
    failed = jobs.wait(submit(jobs, "fail"), 10)
    assert failed.status == JobStatus.FAILED and failed.error == "bad input"
    crashed = jobs.wait(submit(jobs, "crash"), 10)
    assert crashed.status == JobStatus.FAILED and "boom" in (crashed.error or "")


def test_cancel_running_duckdb_query(jobs: InProcessJobRunner) -> None:
    job_id = submit(jobs, "slow_sql")
    deadline = time.monotonic() + 5
    while jobs.get(job_id).status != JobStatus.RUNNING and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    jobs.cancel(job_id)
    done = jobs.wait(job_id, 10)
    assert done.status == JobStatus.CANCELLED


def test_one_job_at_a_time_and_cancel_queued(jobs: InProcessJobRunner) -> None:
    first = submit(jobs, "sleep")
    second = submit(jobs, "ok")
    time.sleep(0.1)
    assert jobs.get(second).status == JobStatus.QUEUED  # max_concurrent_jobs = 1
    assert jobs.cancel(second).status == JobStatus.CANCELLED
    assert [j.id for j in jobs.list(active=True)] == [first]
    jobs.cancel(first)
    assert jobs.wait(first, 10).status == JobStatus.CANCELLED
    assert jobs.list(active=True) == []


def test_dedupe_key_returns_the_active_job(jobs: InProcessJobRunner) -> None:
    first = submit(jobs, "sleep", dedupe_key="step:1")
    assert submit(jobs, "ok", dedupe_key="step:1") == first
    assert jobs.find_active("step:1") is not None
    jobs.cancel(first)
    jobs.wait(first, 10)
    assert jobs.find_active("step:1") is None
    assert submit(jobs, "ok", dedupe_key="step:1") != first


def test_finished_jobs_are_pruned(jobs: InProcessJobRunner) -> None:
    job_id = submit(jobs, "ok")
    jobs.wait(job_id, 10)
    jobs._retention_s = 0
    time.sleep(0.01)
    assert all(j.id != job_id for j in jobs.list())


def test_http_endpoints_and_sse(client: TestClient, jobs: InProcessJobRunner) -> None:
    job_id = submit(jobs, "ok")
    events: list[dict[str, Any]] = []
    with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        current: dict[str, str] = {}
        for line in response.iter_lines():
            if line.startswith(("event:", "id:", "data:")):
                name, _, value = line.partition(":")
                current[name] = value.strip()
            elif not line and current:
                events.append(current)
                current = {}
    assert all(e["event"] == "job" for e in events)
    payloads = [json.loads(e["data"]) for e in events]
    assert payloads[-1]["status"] == "succeeded"
    seqs = [p["seq"] for p in payloads]
    assert seqs == sorted(seqs) and [int(e["id"]) for e in events] == seqs

    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "succeeded"
    assert any(j["id"] == job_id for j in client.get("/api/jobs").json())
    assert client.get("/api/jobs?active=true").json() == []
    assert client.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "succeeded"
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/events").status_code == 404


def test_close_cancels_running_jobs(jobs: InProcessJobRunner) -> None:
    job_id = submit(jobs, "slow_sql")
    time.sleep(0.3)
    closer = threading.Thread(target=jobs.close)
    closer.start()
    closer.join(15)
    assert not closer.is_alive()
    assert jobs.get(job_id).status == JobStatus.CANCELLED
