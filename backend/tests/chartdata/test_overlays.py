"""Trendline overlays: known-answer fits and the /api/charts/overlays endpoint."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
import statsmodels.api as sm
from fastapi.testclient import TestClient

from viz.chartdata.overlays import Points, fit_trendline
from viz.contracts import Trendline


def points(x: Any, y: Any, temporal: bool = False) -> Points:
    xs, ys = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    return Points(xs, ys, temporal, False, len(xs))


def test_linear_exact() -> None:
    x = np.arange(10.0)
    result = fit_trendline(Trendline(kind="linear"), points(x, 2 * x + 1))
    assert result.equation == "y = 2x + 1"
    assert result.r2 == pytest.approx(1.0)
    assert result.y[0] == pytest.approx(1.0) and result.y[-1] == pytest.approx(19.0)
    assert len(result.x) == 200 and result.n == 10


def test_linear_ci_matches_statsmodels() -> None:
    rng = np.random.default_rng(3)
    x = rng.uniform(0, 10, 60)
    y = 3 * x - 2 + rng.normal(0, 2, 60)
    result = fit_trendline(Trendline(kind="linear", ci_level=0.9), points(x, y))
    model = sm.OLS(y, sm.add_constant(x)).fit()
    grid = np.asarray(result.x)
    frame = model.get_prediction(sm.add_constant(grid)).summary_frame(alpha=0.1)
    assert np.allclose(result.y, frame["mean"])
    assert np.allclose(result.lo, frame["mean_ci_lower"])
    assert np.allclose(result.hi, frame["mean_ci_upper"])
    assert result.r2 == pytest.approx(model.rsquared)


def test_polynomial_coefficients_in_original_units() -> None:
    x = np.linspace(-3, 7, 40)
    y = 0.5 * x**2 - 3 * x + 4
    result = fit_trendline(Trendline(kind="polynomial", degree=2), points(x, y))
    assert result.equation == "y = 0.5x^2 - 3x + 4"
    assert result.r2 == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("kind", "x", "y", "equation"),
    [
        (
            "exponential",
            np.arange(1.0, 10.0),
            2 * np.exp(0.3 * np.arange(1.0, 10.0)),
            "y = 2·e^(0.3x)",
        ),
        (
            "logarithmic",
            np.arange(1.0, 10.0),
            1 + 2 * np.log(np.arange(1.0, 10.0)),
            "y = 1 + 2·ln(x)",
        ),
        ("power", np.arange(1.0, 10.0), 3 * np.arange(1.0, 10.0) ** 1.5, "y = 3·x^1.5"),
    ],
)
def test_linearized_fits(kind: str, x: Any, y: Any, equation: str) -> None:
    result = fit_trendline(Trendline(kind=kind), points(x, y))
    assert result.equation == equation
    assert result.r2 == pytest.approx(1.0)


def test_power_drops_non_positive_points() -> None:
    x = np.array([-1.0, 0.0, 1.0, 2.0, 3.0, 4.0])
    result = fit_trendline(Trendline(kind="power"), points(x, x**2 + 1))
    assert result.n == 4 and result.warnings


def test_moving_average_and_loess() -> None:
    x = np.arange(20.0)
    result = fit_trendline(Trendline(kind="moving_average", window=4), points(x, x))
    assert result.x[0] == 3.0 and result.y[0] == pytest.approx(1.5)
    assert len(result.x) == 17 and result.equation is None
    smooth = fit_trendline(Trendline(kind="loess"), points(x, np.sin(x)))
    assert len(smooth.x) == 200 and all(math.isfinite(v) for v in smooth.y)


def test_temporal_x_has_no_equation_and_too_few_points() -> None:
    ms = np.array([0.0, 86_400_000.0, 172_800_000.0])
    result = fit_trendline(Trendline(kind="linear"), points(ms, [1.0, 2.0, 3.0], temporal=True))
    assert result.equation is None and result.r2 == pytest.approx(1.0)
    few = fit_trendline(Trendline(kind="polynomial", degree=3), points([1, 2, 3], [1, 2, 3]))
    assert few.x == [] and few.warnings


SALES = """month,units
2024-01-01,10
2024-02-01,12
2024-03-01,15
2024-04-01,15
2024-05-01,19
"""


def test_overlays_endpoint(client: TestClient, upload: Any) -> None:
    ds = upload(SALES)
    body = {
        "spec": {
            "id": "line",
            "plotType": "line",
            "source": {"kind": "dataset", "id": ds["id"]},
            "encodings": {"x": [{"field": "month"}], "y": [{"field": "units"}]},
            "style": {
                "trendlines": [
                    {"kind": "linear", "ciLevel": 0.95},
                    {"kind": "moving_average", "window": 2},
                ]
            },
        }
    }
    response = client.post("/api/charts/overlays", json=body)
    assert response.status_code == 200, response.text
    linear, average = response.json()
    assert linear["kind"] == "linear" and linear["equation"] is None  # temporal x
    assert linear["x"][0] == pytest.approx(1704067200000.0)  # epoch ms
    assert len(linear["lo"]) == len(linear["x"]) and linear["n"] == 5
    assert average["y"] == [11.0, 13.5, 15.0, 17.0]

    # A categorical x fits against the category position.
    body["spec"]["encodings"]["x"] = [{"field": "units"}]
    body["spec"]["plotType"] = "bar"
    body["spec"]["style"] = {"trendlines": [{"kind": "linear"}]}
    assert client.post("/api/charts/overlays", json=body).status_code == 200

    body["spec"]["style"] = {}
    assert client.post("/api/charts/overlays", json=body).json() == []
