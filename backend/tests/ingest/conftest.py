from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from viz.contracts import ARROW_META_KEY


def read_arrow(content: bytes) -> tuple[pa.Table, dict[str, Any]]:
    table = pa.ipc.open_stream(content).read_all()
    meta = json.loads(table.schema.metadata[ARROW_META_KEY.encode()])
    return table, meta


def wait_job(client: TestClient, job: dict[str, Any], timeout_s: float = 60) -> dict[str, Any]:
    done = client.app.state.jobs.wait(job["id"], timeout_s)  # type: ignore[attr-defined]
    return dict(done.model_dump(mode="json"))


@pytest.fixture
def stage(client: TestClient) -> Callable[[str, bytes], dict[str, Any]]:
    def _stage(name: str, content: bytes) -> dict[str, Any]:
        response = client.post("/api/datasets/staged", files={"file": (name, content)})
        assert response.status_code == 201, response.text
        return dict(response.json())

    return _stage


@pytest.fixture
def run_import(client: TestClient) -> Callable[..., dict[str, Any]]:
    """POST /datasets/import, wait for the job, return the dataset (asserts success)."""

    def _run(**body: Any) -> dict[str, Any]:
        response = client.post("/api/datasets/import", json=body)
        assert response.status_code == 202, response.text
        job = wait_job(client, response.json())
        assert job["status"] == "succeeded", job
        dataset = client.get(f"/api/datasets/{job['result']['datasetId']}").json()
        return dict(dataset)

    return _run


@pytest.fixture
def write(tmp_path: Path) -> Callable[[str, bytes | str], Path]:
    def _write(name: str, content: bytes | str) -> Path:
        path = tmp_path / "src files" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)
        return path

    return _write
