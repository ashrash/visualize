"""HTTP tests for the MVP §5 endpoints (the kernels have their own known-answer tests)."""

from __future__ import annotations

import io
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from scipy import stats as sps

from viz.main import create_app
from viz.stats.cache import clear_cache

N = 90


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    clear_cache()


@pytest.fixture
def data() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(31)
    group = np.array(["a", "b", "c"] * (N // 3))
    x = rng.normal(10, 2, N)
    y = 1.5 * x + (group == "b") * 2.0 + rng.normal(0, 1, N)
    side = np.array(["left", "right"])[rng.integers(0, 2, N)]
    return {"x": x, "y": y, "group": group, "side": side}


@pytest.fixture
def dataset(client: TestClient, data: dict[str, np.ndarray]) -> str:
    lines = ["x,y,group,side"] + [
        f"{float(x)!r},{float(y)!r},{g},{s}"
        for x, y, g, s in zip(data["x"], data["y"], data["group"], data["side"], strict=True)
    ]
    body = ("\n".join(lines) + "\n").encode()
    files = {"file": ("measures.csv", io.BytesIO(body), "text/csv")}
    response = client.post("/api/datasets/upload", files=files)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _post(client: TestClient, path: str, dataset: str, **body: Any) -> Any:
    payload = {"source": {"kind": "dataset", "id": dataset}, **body}
    return client.post(f"/api/stats/{path}", json=payload)


def _ok(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def test_correlation(client: TestClient, dataset: str, data: dict[str, np.ndarray]) -> None:
    result = _ok(_post(client, "correlation", dataset, fields=["x", "y"]))
    r = sps.pearsonr(data["x"], data["y"])
    assert result["values"]["coefficient"][0][1] == pytest.approx(r.statistic, rel=1e-12)
    assert result["values"]["pValue"][0][1] == pytest.approx(r.pvalue, rel=1e-6, abs=1e-300)
    assert result["n"] == N and result["sampled"] is False


def test_regression(client: TestClient, dataset: str) -> None:
    result = _ok(_post(client, "regression", dataset, y="y", x=["x", "group"]))
    terms = [t["term"] for t in result["values"]["terms"]]
    assert terms == ["(Intercept)", "x", "group[b]", "group[c]"]
    assert result["values"]["referenceLevels"] == [{"field": "group", "level": "a"}]


def test_ttest_and_ranktest(client: TestClient, dataset: str, data: dict[str, np.ndarray]) -> None:
    a, b = data["y"][data["group"] == "a"], data["y"][data["group"] == "b"]
    result = _ok(
        _post(
            client, "ttest", dataset, kind="welch", field="y", groupField="group", groups=["a", "b"]
        )
    )
    assert result["values"]["statistic"] == pytest.approx(
        sps.ttest_ind(a, b, equal_var=False).statistic, rel=1e-10
    )
    ranked = _ok(
        _post(client, "ranktest", dataset, field="y", groupField="group", groups=["a", "b"])
    )
    assert ranked["values"]["pValue"] == pytest.approx(sps.mannwhitneyu(a, b).pvalue, rel=1e-10)


def test_anova_and_chisquare(client: TestClient, dataset: str, data: dict[str, np.ndarray]) -> None:
    result = _ok(_post(client, "anova", dataset, field="y", factors=["group"]))
    groups = [data["y"][data["group"] == g] for g in ("a", "b", "c")]
    assert result["values"]["table"][0]["statistic"] == pytest.approx(
        sps.f_oneway(*groups).statistic, rel=1e-10
    )
    assert len(result["values"]["postHoc"]) == 3
    two_way = _ok(_post(client, "anova", dataset, field="y", factors=["group", "side"]))
    assert [r["term"] for r in two_way["values"]["table"]] == [
        "group",
        "side",
        "group:side",
        "Residuals",
    ]
    chi = _ok(_post(client, "chisquare", dataset, field="group", columnField="side"))
    assert chi["values"]["df"] == 2


def test_fit_outliers_and_ci(client: TestClient, dataset: str, data: dict[str, np.ndarray]) -> None:
    fit = _ok(_post(client, "fit", dataset, field="x", distributions=["norm", "logistic"]))
    assert {f["distribution"] for f in fit["values"]["fits"]} == {"norm", "logistic"}
    found = _ok(_post(client, "outliers", dataset, fields=["x", "y"], method="zscore"))
    assert [s["field"] for s in found["values"]["summaries"]] == ["x", "y"]
    ci = _ok(_post(client, "ci", dataset, fields=["x"], groupBy="group"))
    first = ci["values"]["intervals"][0]
    xa = data["x"][data["group"] == "a"]
    assert (first["ciLow"], first["ciHigh"]) == pytest.approx(
        sps.t.interval(0.95, len(xa) - 1, loc=xa.mean(), scale=sps.sem(xa)), rel=1e-10
    )
    boot = _ok(_post(client, "ci", dataset, fields=["x"], statistic="median", nResamples=500))
    assert boot["values"]["intervals"][0]["method"].endswith("bootstrap")


def test_pipeline_source(client: TestClient, dataset: str, data: dict[str, np.ndarray]) -> None:
    pipeline = {
        "id": "p1",
        "source": {"kind": "dataset", "id": dataset},
        "steps": [{"id": "s0", "op": "calc", "name": "diff", "expression": "y - x"}],
    }
    body = {
        "source": {"kind": "pipeline", "id": "p1"},
        "pipelines": [pipeline],
        "kind": "one_sample",
        "field": "diff",
        "filters": [{"op": "eq", "field": "group", "value": "c"}],
    }
    result = _ok(client.post("/api/stats/ttest", json=body))
    mask = data["group"] == "c"
    diff = data["y"][mask] - data["x"][mask]
    assert result["values"]["statistic"] == pytest.approx(
        sps.ttest_1samp(diff, 0.0).statistic, rel=1e-9
    )
    assert result["n"] == int(mask.sum())


@pytest.mark.parametrize(
    ("path", "body", "detail"),
    [
        ("correlation", {"fields": ["x", "nope"]}, "Unknown field 'nope'."),
        ("ttest", {"kind": "one_sample", "field": "group"}, "'group' must be a numeric"),
        ("anova", {"field": "y", "factors": ["x"]}, "'x' has 90 levels"),
        ("regression", {"y": "y", "x": ["x", "x2"]}, "Unknown field 'x2'."),
    ],
)
def test_user_errors_are_422(
    client: TestClient, dataset: str, path: str, body: dict[str, Any], detail: str
) -> None:
    response = _post(client, path, dataset, **body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"].startswith(detail)


def test_validation_and_missing_sources(client: TestClient, dataset: str) -> None:
    assert _post(client, "correlation", dataset, fields=["x"]).status_code == 422
    assert _post(client, "ci", dataset, fields=["x"], ciLevel=1.5).status_code == 422
    missing = client.post(
        "/api/stats/outliers", json={"source": {"kind": "dataset", "id": "nope"}, "fields": ["x"]}
    )
    assert missing.status_code == 404


def test_openapi_lists_the_endpoints() -> None:
    spec = create_app().openapi()
    for path in (
        "correlation",
        "regression",
        "ttest",
        "ranktest",
        "anova",
        "chisquare",
        "fit",
        "outliers",
        "ci",
    ):
        assert f"/api/stats/{path}" in spec["paths"], path
    schemas = spec["components"]["schemas"]
    for name in ("CorrelationRequest", "TTestRequest", "AnovaRequest", "OutlierRequest"):
        assert name in schemas
