"""Chart-data test fixtures. Parametrize `cap` in a test to change the mark cap."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from viz.config import Settings
from viz.contracts import ARROW_META_KEY


@pytest.fixture
def cap() -> int:
    return 50_000


@pytest.fixture
def settings(tmp_path: Path, cap: int) -> Settings:
    # Overrides the root fixture so the app (and `client`) uses this mark cap.
    return Settings(data_dir=tmp_path / "data", query_timeout_s=10.0, mark_cap=cap)


UploadFn = Callable[..., dict[str, Any]]


@pytest.fixture
def upload(client: TestClient) -> UploadFn:
    def _upload(csv_text: str, name: str = "data.csv") -> dict[str, Any]:
        files = {"file": (name, io.BytesIO(csv_text.encode("utf-8")), "text/csv")}
        response = client.post("/api/datasets/upload", files=files)
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    return _upload


ChartFn = Callable[..., tuple[pa.Table, dict[str, Any]]]


@pytest.fixture
def chart(client: TestClient) -> ChartFn:
    def _chart(body: dict[str, Any], status: int = 200) -> tuple[pa.Table, dict[str, Any]]:
        response = client.post("/api/charts/data", json=body)
        assert response.status_code == status, response.text
        table = pa.ipc.open_stream(response.content).read_all()
        return table, json.loads(table.schema.metadata[ARROW_META_KEY.encode()])

    return _chart


def spec(dataset_id: str, plot: str = "bar", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chart-1",
        "plotType": plot,
        "source": {"kind": "dataset", "id": dataset_id},
        "encodings": {},
    }
    body.update(extra)
    return body


@pytest.fixture
def make_spec() -> Callable[..., dict[str, Any]]:
    return spec
