from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import update

from viz.meta.models import ProjectRow, ShareTokenRow, utcnow

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
    sessions = client.app.state.sessionmaker  # type: ignore[attr-defined]
    with sessions() as db:
        assert db.get(ShareTokenRow, link["token"]) is None


def test_reports(client: TestClient) -> None:
    project = _project(client)
    dash = client.get(f"/api/reports/{project['id']}/dashboards/dash1").json()
    assert dash["dashboard"]["name"] == "Overview"
    assert [c["id"] for c in dash["charts"]] == ["c1"]
    every = client.get(f"/api/reports/{project['id']}").json()
    assert [c["id"] for c in every["charts"]] == ["c1", "c2"]
    assert client.get(f"/api/reports/{project['id']}/dashboards/x").status_code == 404


def test_share_exposes_only_reachable_pipelines_and_datasets(client: TestClient) -> None:
    project = _project(client)
    project["datasetIds"] = ["d1", "d2", "secret-ds"]
    project["charts"][0]["source"] = {"kind": "pipeline", "id": "p1"}
    project["pipelines"] = [
        {
            "id": "p1",
            "source": {"kind": "dataset", "id": "d1"},
            "steps": [
                {"id": "s1", "op": "join", "right": {"kind": "pipeline", "id": "p2"}, "on": []}
            ],
        },
        {"id": "p2", "source": {"kind": "dataset", "id": "d2"}, "steps": []},
        {
            "id": "secret-pipe",
            "source": {"kind": "dataset", "id": "secret-ds"},
            "steps": [
                {
                    "id": "f",
                    "op": "filter",
                    "predicates": [{"field": "employee", "op": "eq", "value": "Alice Smith"}],
                }
            ],
        },
    ]
    saved = client.put(f"/api/projects/{project['id']}", json=project)
    assert saved.status_code == 200, saved.text
    link = client.post(
        "/api/share", json={"projectId": project["id"], "kind": "chart", "itemId": "c1"}
    ).json()
    response = client.get(f"/api/embed/{link['token']}")
    body = response.json()
    assert sorted(p["id"] for p in body["pipelines"]) == ["p1", "p2"]
    assert sorted(body["datasetIds"]) == ["d1", "d2"]
    assert "Alice" not in response.text


def test_share_carries_global_filters(client: TestClient) -> None:
    project = _project(client)
    project["globalFilters"] = [
        {
            "id": "g1",
            "label": "Region",
            "field": "region",
            "predicate": {"field": "region", "op": "in", "values": ["EU"]},
        }
    ]
    assert client.put(f"/api/projects/{project['id']}", json=project).status_code == 200
    link = _mint(client, project)
    assert client.get(f"/api/share/{link['token']}").json()["globalFilters"][0]["id"] == "g1"
    report = client.get(f"/api/reports/{project['id']}/dashboards/dash1").json()
    assert report["globalFilters"][0]["id"] == "g1"


def test_internal_errors_surface_as_plain_404(client: TestClient) -> None:
    project = _project(client)
    link = _mint(client, project)
    with client.app.state.sessionmaker() as db:  # type: ignore[attr-defined]
        row = db.get(ProjectRow, project["id"])
        row.doc = {**row.doc, "schemaVersion": 9999}
        db.commit()
    response = client.get(f"/api/share/{link['token']}")
    assert response.status_code == 404
    assert response.json() == {"detail": "share link not found"}


def test_empty_project_report_and_kind(client: TestClient) -> None:
    created = client.post("/api/projects", json={"name": "Empty"}).json()
    report = client.get(f"/api/reports/{created['id']}")
    assert report.status_code == 200
    assert report.json()["kind"] == "project"
    assert report.json()["charts"] == []


def test_only_share_and_embed_resolution_are_public(client: TestClient) -> None:
    from fastapi import HTTPException

    from viz.auth import current_user

    def deny() -> None:
        raise HTTPException(status_code=401)

    # With the user dependency failing, only routes that skip it can still answer.
    client.app.dependency_overrides[current_user] = deny  # type: ignore[attr-defined]
    schema = client.get("/openapi.json").json()
    public = set()
    for path, methods in schema["paths"].items():
        if not path.startswith("/api"):
            continue
        concrete = path.replace("{", "").replace("}", "")
        for method in methods:
            response = client.request(method.upper(), concrete)
            if response.status_code != 401:
                public.add((method.upper(), path))
    assert public == {("GET", "/api/share/{token}"), ("GET", "/api/embed/{token}")}
