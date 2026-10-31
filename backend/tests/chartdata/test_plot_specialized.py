"""End-to-end chart-data tests for the specialized family, through POST /api/charts/data."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

FLOWS = """source,target,value
Ads,Home,10
Search,Home,20
Ads,Home,5
"""

DEFECTS = """defect,count
Scratch,10
Dent,50
Crack,40
"""


def test_sankey_over_http_returns_json_stat_result(client: TestClient, upload: Any) -> None:
    ds = upload(FLOWS)
    body = {
        "spec": {
            "id": "s1",
            "plotType": "sankey",
            "source": {"kind": "dataset", "id": ds["id"]},
            "encodings": {
                "source": [{"field": "source"}],
                "target": [{"field": "target"}],
                "size": [{"field": "value"}],
            },
        }
    }
    response = client.post("/api/charts/data", json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    links = payload["result"]["values"]["links"]
    by_source = {link["source"]: link["value"] for link in links}
    assert by_source["Ads"] == pytest.approx(15.0)
    assert payload["meta"]["reduction"] in ("aggregated", "sampled")


def test_pareto_over_http(upload: Any, chart: Any, make_spec: Any) -> None:
    ds = upload(DEFECTS)
    body = {
        "spec": make_spec(
            ds["id"],
            plot="pareto",
            encodings={"x": [{"field": "defect"}], "y": [{"field": "count"}]},
        )
    }
    table, meta = chart(body)
    rows = table.to_pylist()
    assert [r["x"] for r in rows] == ["Dent", "Crack", "Scratch"]
    assert rows[-1]["cumPercent"] == pytest.approx(100.0)
    assert meta["fields"]["cumPercent"] == "Cumulative %"


def test_error_bar_over_http(upload: Any, chart: Any, make_spec: Any) -> None:
    csv = "species,mass\nA,10\nA,20\nA,30\nB,5\nB,7\n"
    ds = upload(csv)
    body = {
        "spec": make_spec(
            ds["id"],
            plot="error_bar",
            encodings={"x": [{"field": "species"}], "y": [{"field": "mass"}]},
            params={"kind": "se"},
        )
    }
    table, meta = chart(body)
    rows = {r["x"]: r for r in table.to_pylist()}
    assert rows["A"]["y0"] == pytest.approx(20.0)
    assert rows["A"]["y0_lo"] < rows["A"]["y0"] < rows["A"]["y0_hi"]
    assert meta["params"]["errorKind"] == "se"


def test_lollipop_rejects_unknown_params(upload: Any, client: TestClient, make_spec: Any) -> None:
    ds = upload(DEFECTS)
    body = {
        "spec": make_spec(
            ds["id"],
            plot="lollipop",
            encodings={"x": [{"field": "defect"}], "y": [{"field": "count"}]},
            params={"notAThing": 1},
        )
    }
    response = client.post("/api/charts/data", json=body)
    assert response.status_code == 422
