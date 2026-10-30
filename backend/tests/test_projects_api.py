from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def test_save_and_reload_round_trip(client: TestClient, sales_dataset: dict[str, Any]) -> None:
    created = client.post("/api/projects", json={"name": "Sales"}).json()
    assert created["revision"] == 0

    project = dict(created)
    project["datasetIds"] = [sales_dataset["id"]]
    project["charts"] = [
        {
            "id": "c1",
            "plotType": "bar",
            "source": {"kind": "dataset", "id": sales_dataset["id"]},
            "encodings": {"x": [{"field": "region"}], "y": [{"field": "sales", "agg": "sum"}]},
        }
    ]
    saved = client.put(f"/api/projects/{created['id']}", json=project)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1

    loaded = client.get(f"/api/projects/{created['id']}").json()
    assert loaded["charts"][0]["encodings"]["x"][0]["field"] == "region"
    assert loaded["revision"] == 1

    versions = client.get(f"/api/projects/{created['id']}/versions").json()
    assert [(v["revision"], v["kind"]) for v in versions] == [(1, "save")]
    assert client.get("/api/projects").json()[0]["name"] == "Sales"


def test_stale_revision_is_rejected(client: TestClient) -> None:
    created = client.post("/api/projects", json={"name": "P"}).json()
    changed = {**created, "name": "P2"}
    autosaved = client.put(f"/api/projects/{created['id']}?autosave=true", json=changed)
    assert autosaved.status_code == 200
    stale = client.put(f"/api/projects/{created['id']}", json=created)
    assert stale.status_code == 409
    assert "reload" in stale.json()["detail"]
    # Autosaves don't create version snapshots.
    assert client.get(f"/api/projects/{created['id']}/versions").json() == []


def test_mismatched_id_and_missing_project(client: TestClient) -> None:
    created = client.post("/api/projects", json={"name": "P"}).json()
    assert client.put("/api/projects/other", json=created).status_code == 422
    assert client.get("/api/projects/missing").status_code == 404
