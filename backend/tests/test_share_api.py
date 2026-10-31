from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update

from viz.meta.models import ShareTokenRow, utcnow

CHART = {
    "plotType": "bar",
    "source": {"kind": "dataset", "id": "d1"},
    "encodings": {"x": [{"field": "region"}], "y": [{"field": "sales", "agg": "sum"}]},
}


def _project(client: TestClient) -> dict[str, Any]:
    created = client.post("/api/projects", json={"name": "Shared"}).json()
    project = dict(created)
    project["charts"] = [{"id": "c1", **CHART}, {"id": "c2", **CHART}]
    project["dashboards"] = [
        {
            "id": "dash1",
            "name": "Overview",
            "tabs": [
                {
                    "id": "t1",
                    "items": [
                        {
                            "i": "a",
                            "x": 0,
                            "y": 0,
                            "w": 6,
                            "h": 4,
                            "widget": {"kind": "chart", "chartId": "c1"},
                        },
                        {
                            "i": "b",
                            "x": 6,
                            "y": 0,
                            "w": 6,
                            "h": 4,
                            "widget": {"kind": "text", "markdown": "hi"},
                        },
                    ],
                }
            ],
        }
    ]
    saved = client.put(f"/api/projects/{created['id']}", json=project)
    assert saved.status_code == 200, saved.text
    out: dict[str, Any] = saved.json()
    return out


def _mint(client: TestClient, project: dict[str, Any], **extra: Any) -> dict[str, Any]:
    body = {"projectId": project["id"], "kind": "dashboard", "itemId": "dash1", **extra}
    response = client.post("/api/share", json=body)
    assert response.status_code == 201, response.text
    out: dict[str, Any] = response.json()
    return out


def test_share_dashboard_round_trip(client: TestClient) -> None:
    project = _project(client)
    link = _mint(client, project)
    assert link["sharePath"] == f"/share/{link['token']}"
    assert len(link["token"]) >= 32

    view = client.get(f"/api/share/{link['token']}")
    assert view.status_code == 200
    body = view.json()
    assert body["kind"] == "dashboard"
    assert body["dashboard"]["id"] == "dash1"
    # Only the chart the dashboard references is bundled.
    assert [c["id"] for c in body["charts"]] == ["c1"]
    assert client.get(f"/api/embed/{link['token']}").json()["projectName"] == "Shared"


def test_share_single_chart(client: TestClient) -> None:
    project = _project(client)
    link = client.post(
        "/api/share", json={"projectId": project["id"], "kind": "chart", "itemId": "c2"}
    ).json()
    body = client.get(f"/api/embed/{link['token']}").json()
    assert body["dashboard"] is None
    assert [c["id"] for c in body["charts"]] == ["c2"]


def test_resolve_unknown_revoked_expired_are_404(client: TestClient) -> None:
    project = _project(client)
    assert client.get("/api/share/nope").status_code == 404

    link = _mint(client, project)
    assert client.delete(f"/api/share/{link['token']}").status_code == 204
    assert client.get(f"/api/share/{link['token']}").status_code == 404
    listed = client.get("/api/share", params={"projectId": project["id"]}).json()
    assert listed[0]["revoked"] is True

    expiring = _mint(client, project, expiresInDays=1)
    assert client.get(f"/api/share/{expiring['token']}").status_code == 200
    sessions = client.app.state.sessionmaker  # type: ignore[attr-defined]
    with sessions() as db:
        db.execute(
            update(ShareTokenRow)
            .where(ShareTokenRow.token == expiring["token"])
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
        db.commit()
    assert client.get(f"/api/share/{expiring['token']}").status_code == 404


def test_cannot_share_missing_items(client: TestClient) -> None:
    project = _project(client)
    bad = {"projectId": project["id"], "kind": "dashboard", "itemId": "zzz"}
    assert client.post("/api/share", json=bad).status_code == 404
    bad = {"projectId": "missing", "kind": "chart", "itemId": "c1"}
    assert client.post("/api/share", json=bad).status_code == 404


def test_link_follows_live_edits_and_dies_with_item(client: TestClient) -> None:
    project = _project(client)
    link = _mint(client, project)
    project["dashboards"] = []
    assert client.put(f"/api/projects/{project['id']}", json=project).status_code == 200
    assert client.get(f"/api/share/{link['token']}").status_code == 404


def test_delete_project_removes_tokens(client: TestClient) -> None:
    project = _project(client)
    link = _mint(client, project)
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.get(f"/api/share/{link['token']}").status_code == 404


def test_reports(client: TestClient) -> None:
    project = _project(client)
    dash = client.get(f"/api/reports/{project['id']}/dashboards/dash1").json()
    assert dash["dashboard"]["name"] == "Overview"
    assert [c["id"] for c in dash["charts"]] == ["c1"]
    every = client.get(f"/api/reports/{project['id']}").json()
    assert [c["id"] for c in every["charts"]] == ["c1", "c2"]
    assert client.get(f"/api/reports/{project['id']}/dashboards/x").status_code == 404
