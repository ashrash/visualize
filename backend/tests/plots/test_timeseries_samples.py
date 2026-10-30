"""Smoke tests against the real samples/ fixtures named for this family in samples/README.md:
stocks_ohlc.csv, monthly_demand.csv, weather_daily.csv.

These stand in for the brief's "visual check" (no browser is available in this environment):
each plot runs through the real import -> chart-data HTTP path on real data, checking the
response is well-formed and the values are sane, rather than only a hand-built fixture.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from viz.contracts import ARROW_META_KEY

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "samples"
pytestmark = pytest.mark.skipif(not SAMPLES_DIR.exists(), reason="samples/ not available")


def upload_path(client: TestClient, path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        files = {"file": (path.name, io.BytesIO(fh.read()), "text/csv")}
    response = client.post("/api/datasets/upload", files=files)
    assert response.status_code == 201, response.text
    data: dict[str, Any] = response.json()
    return data


def chart(client: TestClient, body: dict[str, Any]) -> tuple[pa.Table, dict[str, Any]]:
    response = client.post("/api/charts/data", json=body)
    assert response.status_code == 200, response.text
    table = pa.ipc.open_stream(response.content).read_all()
    return table, json.loads(table.schema.metadata[ARROW_META_KEY.encode()])


def spec(dataset_id: str, plot: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": "c1",
        "plotType": plot,
        "source": {"kind": "dataset", "id": dataset_id},
        "encodings": {},
    }
    body.update(extra)
    return body


def test_timeseries_line_on_monthly_demand(client: TestClient) -> None:
    ds = upload_path(client, SAMPLES_DIR / "monthly_demand.csv")
    body = {
        "spec": spec(
            ds["id"],
            "timeseries_line",
            encodings={"x": [{"field": "month"}], "y": [{"field": "demand", "agg": "sum"}]},
        )
    }
    table, meta = chart(client, body)
    assert table.num_rows == 168  # 14 years of months, no bucketing needed
    assert meta["reduction"] == "aggregated"
    assert all(v is not None and v > 0 for v in table.column("y0").to_pylist())


def test_seasonal_demand_by_month(client: TestClient) -> None:
    ds = upload_path(client, SAMPLES_DIR / "monthly_demand.csv")
    body = {
        "spec": spec(
            ds["id"],
            "seasonal",
            encodings={"x": [{"field": "month"}], "y": [{"field": "demand", "agg": "mean"}]},
        )
    }
    table, _ = chart(client, body)
    assert table.num_rows == 12 * 14  # 12 months x 14 years
    assert set(table.column("x").to_pylist()) == set(range(1, 13))


def test_lag_on_demand(client: TestClient) -> None:
    ds = upload_path(client, SAMPLES_DIR / "monthly_demand.csv")
    body = {
        "spec": spec(
            ds["id"],
            "lag",
            encodings={"x": [{"field": "month"}], "y": [{"field": "demand"}]},
            params={"lag": 12},
        )
    }
    table, meta = chart(client, body)
    assert table.num_rows == 168 - 12
    assert meta["reduction"] == "none"


def test_acf_and_pacf_on_demand(client: TestClient) -> None:
    ds = upload_path(client, SAMPLES_DIR / "monthly_demand.csv")
    for plot in ("acf", "pacf"):
        body = {
            "spec": spec(
                ds["id"],
                plot,
                encodings={"x": [{"field": "month"}], "y": [{"field": "demand"}]},
                params={"lags": 24},
            )
        }
        table, meta = chart(client, body)
        assert table.num_rows == 25
        assert meta["warnings"] == []  # monthly_demand has no gaps
        y0 = table.column("y0").to_pylist()
        assert y0[0] == pytest.approx(1.0)
        assert all(-1.0001 <= v <= 1.0001 for v in y0)


def test_moving_average_on_demand(client: TestClient) -> None:
    ds = upload_path(client, SAMPLES_DIR / "monthly_demand.csv")
    body = {
        "spec": spec(
            ds["id"],
            "moving_average",
            encodings={"x": [{"field": "month"}], "y": [{"field": "demand", "agg": "sum"}]},
            params={"window": 12},
        )
    }
    table, _ = chart(client, body)
    assert table.num_rows == 168
    # The 12-month trailing average should be far smoother than the raw series.
    y0 = table.column("y0").to_pylist()
    y1 = table.column("y1").to_pylist()

    def variance(values: list[float]) -> float:
        mean = sum(values) / len(values)
        return sum((v - mean) ** 2 for v in values) / len(values)

    assert variance(y1[11:]) < variance(y0[11:])


def test_candlestick_on_one_ticker(client: TestClient) -> None:
    ds = upload_path(client, SAMPLES_DIR / "stocks_ohlc.csv")
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
            filters=[{"op": "eq", "field": "ticker", "value": "NVLX"}],
        )
    }
    table, meta = chart(client, body)
    assert table.num_rows == 520  # one ticker's business days
    assert meta["warnings"] == []  # filtered to one ticker: no duplicate-timestamp warning
    lows = table.column("low").to_pylist()
    highs = table.column("high").to_pylist()
    opens = table.column("open").to_pylist()
    closes = table.column("close").to_pylist()
    assert all(
        lo <= o <= hi and lo <= c <= hi
        for lo, hi, o, c in zip(lows, highs, opens, closes, strict=True)
    )


def test_candlestick_warns_without_a_ticker_filter(client: TestClient) -> None:
    ds = upload_path(client, SAMPLES_DIR / "stocks_ohlc.csv")
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
            },
        )
    }
    table, meta = chart(client, body)
    assert table.num_rows == 520  # one bar per date, OHLC combined across the 4 tickers
    assert any("same timestamp" in w for w in meta["warnings"])


def test_calendar_heatmap_on_weather(client: TestClient) -> None:
    ds = upload_path(client, SAMPLES_DIR / "weather_daily.csv")
    body = {
        "spec": spec(
            ds["id"],
            "calendar_heatmap",
            encodings={"x": [{"field": "date"}], "y": [{"field": "temp_c", "agg": "mean"}]},
        )
    }
    table, meta = chart(client, body)
    assert table.num_rows == 1096  # 3 years x 3 cities averaged per day
    assert meta["rowsMatched"] == 3288
    temps = table.column("y0").to_pylist()
    assert all(-40 < t < 50 for t in temps)
