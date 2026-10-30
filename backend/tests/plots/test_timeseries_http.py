"""End-to-end checks for the Sec4 time-series plots through POST /api/charts/data.

These complement test_timeseries.py's known-answer tests (built on a bare `Relation`) by
exercising the full stack: upload -> encoding resolution -> Arrow serialization -> meta.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from viz.config import Settings
from viz.contracts import ARROW_META_KEY


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # A tiny mark cap so a few hundred rows is already "over the cap".
    return Settings(data_dir=tmp_path / "data", query_timeout_s=10.0, mark_cap=40)


def upload(client: TestClient, csv_text: str, name: str = "data.csv") -> dict[str, Any]:
    files = {"file": (name, io.BytesIO(csv_text.encode("utf-8")), "text/csv")}
    response = client.post("/api/datasets/upload", files=files)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


def chart(
    client: TestClient, body: dict[str, Any], status: int = 200
) -> tuple[pa.Table, dict[str, Any]]:
    response = client.post("/api/charts/data", json=body)
    assert response.status_code == status, response.text
    table = pa.ipc.open_stream(response.content).read_all()
    return table, json.loads(table.schema.metadata[ARROW_META_KEY.encode()])


def spec(dataset_id: str, plot: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "chart-1",
        "plotType": plot,
        "source": {"kind": "dataset", "id": dataset_id},
        "encodings": {},
    }
    body.update(extra)
    return body


def _minute_series_csv(n: int) -> str:
    start = datetime(2024, 1, 1)
    lines = ["ts,v"]
    for i in range(n):
        lines.append(f"{(start + timedelta(minutes=i)).isoformat()},{float(i)}")
    return "\n".join(lines) + "\n"


def test_timeseries_line_over_cap_is_reduced(client: TestClient) -> None:
    ds = upload(client, _minute_series_csv(500))
    body = {
        "spec": spec(
            ds["id"],
            "timeseries_line",
            encodings={"x": [{"field": "ts"}], "y": [{"field": "v", "agg": "sum"}]},
        )
    }
    table, meta = chart(client, body)
    assert meta["reduction"] in ("downsampled", "aggregated")
    assert table.num_rows <= 40
    assert meta["rowsMatched"] == 500
    assert meta["fields"]["x"] == "ts"


def test_timeseries_line_under_cap_round_trips(client: TestClient) -> None:
    ds = upload(client, _minute_series_csv(10))
    body = {
        "spec": spec(
            ds["id"],
            "timeseries_line",
            encodings={"x": [{"field": "ts"}], "y": [{"field": "v", "agg": "sum"}]},
        )
    }
    table, meta = chart(client, body)
    assert meta["reduction"] == "aggregated"
    assert table.num_rows == 10
    assert meta["cached"] is False
    # A second, identical request hits the response cache.
    _, meta2 = chart(client, body)
    assert meta2["cached"] is True


CANDLES = """date,ticker,open,high,low,close,volume
2024-01-01,AAA,10,12,9,11,1000
2024-01-02,AAA,11,13,10,12,1100
2024-01-03,AAA,12,12.5,11,11.5,900
"""


def test_candlestick_round_trip(client: TestClient) -> None:
    ds = upload(client, CANDLES)
    body = {
        "spec": spec(
            ds["id"],
            "candlestick",
            encodings={
                "x": [{"field": "date"}],
                "open": [{"field": "open"}],
                "high": [{"field": "high"}],
                "low": [{"field": "low"}],
                "close": [{"field": "close"}],
                "volume": [{"field": "volume"}],
            },
        )
    }
    table, meta = chart(client, body)
    assert table.num_rows == 3
    assert table.column("close").to_pylist() == [11.0, 12.0, 11.5]
    assert meta["fields"]["volume"] == "Volume"


def test_acf_round_trip(client: TestClient) -> None:
    ds = upload(client, _minute_series_csv(30))
    body = {
        "spec": spec(
            ds["id"],
            "acf",
            encodings={"x": [{"field": "ts"}], "y": [{"field": "v"}]},
            params={"lags": 5},
        )
    }
    table, meta = chart(client, body)
    assert table.num_rows == 6  # lags 0..5
    assert meta["fields"]["x"] == "Lag"
    assert table.column("y0").to_pylist()[0] == pytest.approx(1.0)


def test_seasonal_bad_params_is_422(client: TestClient) -> None:
    ds = upload(client, _minute_series_csv(10))
    body = {
        "spec": spec(
            ds["id"],
            "seasonal",
            encodings={"x": [{"field": "ts"}], "y": [{"field": "v"}]},
            params={"period": "month", "cycle": "month"},
        )
    }
    response = client.post("/api/charts/data", json=body)
    assert response.status_code == 422, response.text
