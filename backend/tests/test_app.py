from __future__ import annotations

from fastapi.testclient import TestClient

from viz.main import create_app


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"


def test_unknown_api_route_is_404(client: TestClient) -> None:
    assert client.get("/api/nope").status_code == 404


def test_openapi_publishes_every_contract() -> None:
    schemas = create_app().openapi()["components"]["schemas"]
    for name in ("ChartSpec", "ChartDataMeta", "Dashboard", "Pipeline", "Project", "StatResult"):
        assert name in schemas, name
    assert len(schemas["PlotType"]["enum"]) == 74
    assert "plotType" in schemas["ChartSpec"]["properties"]
