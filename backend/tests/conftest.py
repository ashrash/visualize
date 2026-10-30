from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from viz.config import Settings
from viz.engine import Engine
from viz.main import create_app

SALES_CSV = """region,product,sales,units,order_date
North,Widget,120.5,3,2024-01-05
South,Widget,80,2,2024-01-17
North,Gadget,200,5,2024-02-02
East,Gadget,,1,2024-02-20
South,Gizmo,55.25,1,2024-03-03
North,Gizmo,10,1,2024-03-15
"""


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", query_timeout_s=10.0)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = Engine(temp_dir=tmp_path / "duck-temp", max_concurrent_queries=4, query_timeout_s=5.0)
    yield eng
    eng.close()


@pytest.fixture
def sales_csv(tmp_path: Path) -> Path:
    path = tmp_path / "sales data.csv"
    path.write_text(SALES_CSV, encoding="utf-8")
    return path


@pytest.fixture
def sales_dataset(client: TestClient, sales_csv: Path) -> dict[str, object]:
    with sales_csv.open("rb") as fh:
        files = {"file": ("sales.csv", fh, "text/csv")}
        response = client.post("/api/datasets/upload", files=files)
    assert response.status_code == 201, response.text
    data: dict[str, object] = response.json()
    return data
