"""End to end over HTTP: every §6 and §8 plot draws from the committed samples/ files."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from viz.contracts import ARROW_META_KEY

SAMPLES = Path(__file__).resolve().parents[3] / "samples"


class Datasets:
    """Uploads a sample file the first time a test asks for it."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.ids: dict[str, str] = {}

    def __getitem__(self, name: str) -> str:
        if name not in self.ids:
            path = SAMPLES / (f"{name}.tsv" if name == "customers" else f"{name}.csv")
            files = {"file": (path.name, io.BytesIO(path.read_bytes()), "text/csv")}
            response = self.client.post("/api/datasets/upload", files=files)
            assert response.status_code == 201, response.text
            self.ids[name] = response.json()["id"]
        return self.ids[name]


@pytest.fixture
def datasets(client: TestClient) -> Datasets:
    return Datasets(client)


def _enc(**channels: str | list[str]) -> dict[str, list[dict[str, str]]]:
    return {
        k: [{"field": f} for f in (v if isinstance(v, list) else [v])] for k, v in channels.items()
    }


MEASURES = ["bill_length_mm", "bill_depth_mm", "flipper_length_mm", "body_mass_g"]

CASES: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = [
    ("radar", "specimens", _enc(y=MEASURES, color="species"), {}),
    ("parallel_coordinates", "specimens", _enc(y=[*MEASURES, "island"], color="species"), {}),
    ("pca_biplot", "specimens", _enc(y=MEASURES, color="species"), {}),
    # Four measures identify one ML factor; principal components take two.
    ("factor_loading", "specimens", _enc(y=MEASURES), {"factors": 1}),
    ("factor_loading", "specimens", _enc(y=MEASURES), {"factors": 2, "method": "pca"}),
    (
        "factor_loading",
        "countries",
        _enc(
            y=[
                "gdp_per_capita",
                "life_exp_2000",
                "life_exp_2020",
                "co2_per_capita",
                "internet_pct",
                "population_m",
            ]
        ),
        {"factors": 2},
    ),
    ("cluster", "specimens", _enc(y=MEASURES), {"k": 3}),
    (
        "cluster",
        "countries",
        _enc(y=["gdp_per_capita", "life_exp_2020", "co2_per_capita"]),
        {"algorithm": "dbscan"},
    ),
    (
        "dendrogram",
        "countries",
        _enc(y=["gdp_per_capita", "life_exp_2020", "internet_pct"], label="country"),
        {},
    ),
    ("andrews_curves", "specimens", _enc(y=MEASURES, color="species"), {}),
    ("kaplan_meier", "survival", _enc(time="time_months", event="event", color="treatment"), {}),
    (
        "forest",
        "studies",
        _enc(label="study", estimate="effect", lower="ci_low", upper="ci_high", color="design"),
        {},
    ),
    ("roc", "churn", _enc(truth="churned", score=["score_model_a", "score_model_b"]), {}),
    ("precision_recall", "churn", _enc(truth="churned", score=["score_model_a"]), {}),
    ("lorenz", "customers", _enc(y="income", color="segment"), {}),
    ("quantile", "specimens", _enc(y="body_mass_g", color="species"), {}),
    (
        "marginal_effects",
        "churn",
        _enc(
            y="churned",
            x=["tenure_months", "monthly_charges", "support_calls", "contract", "autopay"],
        ),
        {},
    ),
    (
        "coefficient",
        "churn",
        _enc(y="churned", x=["tenure_months", "monthly_charges", "contract"]),
        {"model": "logit"},
    ),
    (
        "impulse_response",
        "macro_quarterly",
        _enc(y=["gdp_growth", "inflation", "policy_rate"], time="quarter"),
        {},
    ),
    (
        "event_study",
        "panel_event_study",
        _enc(y="outcome", unit="unit", time="year", treatTime="treat_year"),
        {},
    ),
]


@pytest.mark.parametrize(("plot", "dataset", "encodings", "params"), CASES)
def test_plot_draws_from_samples(
    client: TestClient,
    datasets: Datasets,
    plot: str,
    dataset: str,
    encodings: dict[str, Any],
    params: dict[str, Any],
) -> None:
    body = {
        "spec": {
            "id": f"chart-{plot}",
            "plotType": plot,
            "source": {"kind": "dataset", "id": datasets[dataset]},
            "encodings": encodings,
            "params": params,
        }
    }
    response = client.post("/api/charts/data", json=body)
    assert response.status_code == 200, response.text
    if response.headers["content-type"].startswith("application/json"):
        payload = response.json()
        assert "NaN" not in response.text
        assert payload["result"]["n"] > 0
    else:
        table = pa.ipc.open_stream(response.content).read_all()
        meta = json.loads(table.schema.metadata[ARROW_META_KEY.encode()])
        assert table.num_rows > 0 and meta["rowsReturned"] == table.num_rows


def test_sample_answers_are_sensible(client: TestClient, datasets: Datasets) -> None:
    def stat(plot: str, dataset: str, encodings: dict[str, Any], params: Any = None) -> Any:
        body = {
            "spec": {
                "id": plot,
                "plotType": plot,
                "source": {"kind": "dataset", "id": datasets[dataset]},
                "encodings": encodings,
                "params": params or {},
            }
        }
        response = client.post("/api/charts/data", json=body)
        assert response.status_code == 200, response.text
        return response.json()["result"]["values"]

    roc = stat("roc", "churn", _enc(truth="churned", score=["score_model_a", "score_model_b"]))
    a, b = roc["curves"]
    assert a["auc"] > b["auc"]  # model A is the stronger one (samples/README.md)
    km = stat(
        "kaplan_meier", "survival", _enc(time="time_months", event="event", color="treatment")
    )
    medians = {g["key"]: g["median"] for g in km["groups"]}
    assert km["logRank"]["pValue"] < 0.05
    assert all(medians[d] is None or medians[d] > medians["Control"] for d in ("Drug A", "Drug B"))
    es = stat(
        "event_study",
        "panel_event_study",
        _enc(y="outcome", unit="unit", time="year", treatTime="treat_year"),
    )
    after = [e["estimate"] for e in es["estimates"] if e["relTime"] >= 2]
    before = [e["estimate"] for e in es["estimates"] if e["relTime"] < -1]
    assert min(after) > max(abs(v) for v in before)  # the effect ramps up after treatment
