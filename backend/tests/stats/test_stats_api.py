from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from viz.stats.descriptive import clear_cache


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    clear_cache()


def _describe(client: TestClient, body: dict[str, Any]) -> Any:
    return client.post("/api/stats/describe", json=body)


def _summary(result: dict[str, Any], field: str, group: list[Any] | None = None) -> dict[str, Any]:
    matches = [
        s for s in result["values"]["summaries"] if s["field"] == field and s["group"] == group
    ]
    assert len(matches) == 1
    return matches[0]


def test_describe_sales(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    source = {"kind": "dataset", "id": sales_dataset["id"]}
    response = _describe(
        client, {"source": source, "fields": ["sales", "units"], "groupBy": ["region"]}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["n"] == 6 and result["sampled"] is False and result["nMissing"] == 0
    sales = _summary(result, "sales")
    assert (sales["count"], sales["missing"]) == (5, 1)
    assert sales["mean"] == pytest.approx(93.15)
    assert sales["median"] == 80.0 and sales["min"] == 10.0 and sales["max"] == 200.0
    assert _summary(result, "sales", ["North"])["mean"] == pytest.approx((120.5 + 200 + 10) / 3)
    assert _summary(result, "sales", ["South"])["mean"] == pytest.approx(67.625)
    east = _summary(result, "sales", ["East"])
    assert east["count"] == 0 and east["mean"] is None
    units = _summary(result, "units")
    assert units["modes"] == [1] and units["modeCount"] == 3 and units["distinct"] == 4
    assert [q["p"] for q in units["quantiles"]] == [0.05, 0.95]
    assert "'sales': no values (region=East)." in result["warnings"]


def test_describe_with_filters(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    body = {
        "source": {"kind": "dataset", "id": sales_dataset["id"]},
        "fields": ["sales"],
        "filters": [{"op": "eq", "field": "product", "value": "Widget"}],
    }
    result = _describe(client, body).json()
    assert result["n"] == 2
    assert _summary(result, "sales")["mean"] == pytest.approx(100.25)


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        ({"fields": ["nope"]}, 422, "Unknown field 'nope'."),
        (
            {"fields": ["sales"], "filters": [{"op": "eq", "field": "zzz", "value": 1}]},
            422,
            "Unknown filter field(s): zzz.",
        ),
    ],
)
def test_describe_errors(
    client: TestClient,
    sales_dataset: dict[str, Any],
    body: dict[str, Any],
    status: int,
    detail: str,
) -> None:
    body = {"source": {"kind": "dataset", "id": sales_dataset["id"]}, **body}
    response = _describe(client, body)
    assert response.status_code == status
    assert response.json()["detail"] == detail


def test_describe_unknown_dataset_and_pipeline(client: TestClient) -> None:
    missing = _describe(client, {"source": {"kind": "dataset", "id": "nope"}, "fields": ["x"]})
    assert missing.status_code == 404
    pipeline = _describe(client, {"source": {"kind": "pipeline", "id": "p"}, "fields": ["x"]})
    assert pipeline.status_code == 501
