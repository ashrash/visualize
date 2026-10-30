from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from viz.config import Settings
from viz.main import create_app


@pytest.fixture
def snap_client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        data_dir=tmp_path / "data", autosave_snapshot_every=3, autosave_snapshots_keep=2
    )
    with TestClient(create_app(settings)) as c:
        yield c


def _new(c: TestClient, name: str = "P") -> dict[str, Any]:
    r = c.post("/api/projects", json={"name": name})
    assert r.status_code == 201
    out: dict[str, Any] = r.json()
    return out


def _autosave(c: TestClient, p: dict[str, Any], name: str) -> dict[str, Any]:
    r = c.put(f"/api/projects/{p['id']}?autosave=true", json={**p, "name": name})
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def test_autosave_snapshot_cadence_and_pruning(snap_client: TestClient) -> None:
    p = _new(snap_client)
    for i in range(1, 13):
        p = _autosave(snap_client, p, f"n{i}")
    assert p["revision"] == 12
    versions = snap_client.get(f"/api/projects/{p['id']}/versions").json()
    # snapshots at revisions 3, 6, 9, 12; only the newest 2 are kept
    assert [v["revision"] for v in versions] == [12, 9]
    assert all(v["kind"] == "autosave" for v in versions)


def test_save_snapshots_are_never_pruned_and_labelled(snap_client: TestClient) -> None:
    p = _new(snap_client)
    saved = snap_client.put(
        f"/api/projects/{p['id']}?label=Milestone", json={**p, "name": "a"}
    ).json()
    for i in range(9):
        saved = _autosave(snap_client, saved, f"x{i}")
    versions = snap_client.get(f"/api/projects/{p['id']}/versions").json()
    kinds = [(v["revision"], v["kind"], v["label"]) for v in versions]
    assert (1, "save", "Milestone") in kinds
    assert len([k for k in kinds if k[1] == "autosave"]) == 2
    only_saves = snap_client.get(f"/api/projects/{p['id']}/versions?kind=save").json()
    assert [v["revision"] for v in only_saves] == [1]
    assert len(snap_client.get(f"/api/projects/{p['id']}/versions?limit=1").json()) == 1


def test_noop_autosave_does_not_bump_revision(snap_client: TestClient) -> None:
    p = _new(snap_client)
    r = snap_client.put(f"/api/projects/{p['id']}?autosave=true", json=p)
    assert r.status_code == 200
    assert r.json()["revision"] == 0
    p1 = _autosave(snap_client, p, "changed")
    again = snap_client.put(f"/api/projects/{p['id']}?autosave=true", json=p1).json()
    assert again["revision"] == 1


def test_restore_creates_new_revision_and_keeps_history(snap_client: TestClient) -> None:
    p = _new(snap_client, "v1")
    s1 = snap_client.put(f"/api/projects/{p['id']}", json=p).json()
    s2 = snap_client.put(f"/api/projects/{p['id']}", json={**s1, "name": "v2"}).json()
    assert s2["revision"] == 2
    versions = snap_client.get(f"/api/projects/{p['id']}/versions").json()
    first = next(v for v in versions if v["revision"] == 1)
    preview = snap_client.get(f"/api/projects/{p['id']}/versions/{first['id']}").json()
    assert preview["name"] == "v1"

    stale = snap_client.post(
        f"/api/projects/{p['id']}/versions/{first['id']}/restore", json={"revision": 1}
    )
    assert stale.status_code == 409
    restored = snap_client.post(
        f"/api/projects/{p['id']}/versions/{first['id']}/restore", json={"revision": 2}
    ).json()
    assert restored["name"] == "v1"
    assert restored["revision"] == 3
    after = snap_client.get(f"/api/projects/{p['id']}/versions").json()
    assert (after[0]["revision"], after[0]["label"]) == (3, "Restored from r1")
    assert {1, 2} <= {v["revision"] for v in after}
    assert snap_client.get(f"/api/projects/{p['id']}/versions/9999").status_code == 404


def test_delete_rename_duplicate(snap_client: TestClient) -> None:
    p = _new(snap_client, "orig")
    p = snap_client.put(f"/api/projects/{p['id']}", json=p).json()
    renamed = snap_client.patch(
        f"/api/projects/{p['id']}", json={"name": "new", "revision": 1}
    ).json()
    assert renamed["name"] == "new" and renamed["revision"] == 2
    assert (
        snap_client.patch(f"/api/projects/{p['id']}", json={"name": "z", "revision": 1}).status_code
        == 409
    )
    dup = snap_client.post(f"/api/projects/{p['id']}/duplicate", json={}).json()
    assert dup["id"] != p["id"] and dup["revision"] == 0 and dup["name"] == "new copy"
    assert snap_client.delete(f"/api/projects/{p['id']}").status_code == 204
    assert snap_client.get(f"/api/projects/{p['id']}").status_code == 404
    assert snap_client.get(f"/api/projects/{p['id']}/versions").status_code == 404
    assert snap_client.get(f"/api/projects/{dup['id']}").status_code == 200
    assert snap_client.delete("/api/projects/nope").status_code == 404


def test_folder_validation(snap_client: TestClient) -> None:
    p = _new(snap_client)
    url = f"/api/projects/{p['id']}"
    dangling = {
        **p,
        "folders": [{"id": "f1", "name": "F", "items": [{"kind": "chart", "id": "x"}]}],
    }
    assert snap_client.put(url, json=dangling).status_code == 422
    cycle = {
        **p,
        "folders": [
            {"id": "a", "name": "A", "parentId": "b", "items": []},
            {"id": "b", "name": "B", "parentId": "a", "items": []},
        ],
    }
    assert snap_client.put(url, json=cycle).status_code == 422
    orphan = {**p, "folders": [{"id": "a", "name": "A", "parentId": "zz", "items": []}]}
    assert snap_client.put(url, json=orphan).status_code == 422
    chart = {
        "id": "c1",
        "plotType": "bar",
        "source": {"kind": "dataset", "id": "d"},
        "encodings": {},
    }
    twice = {
        **p,
        "datasetIds": ["d"],
        "charts": [chart],
        "folders": [
            {"id": "a", "name": "A", "items": [{"kind": "chart", "id": "c1"}]},
            {"id": "b", "name": "B", "items": [{"kind": "chart", "id": "c1"}]},
        ],
    }
    assert snap_client.put(url, json=twice).status_code == 422
    ok = {**twice, "folders": twice["folders"][:1]}
    assert snap_client.put(url, json=ok).status_code == 200
    dup_chart = {**p, "revision": 1, "datasetIds": ["d"], "charts": [chart, chart]}
    assert snap_client.put(url, json=dup_chart).status_code == 422


def test_old_schema_project_loads_via_upgrader(
    snap_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from viz.contracts import migrations

    p = _new(snap_client)
    # Simulate: the stored doc is schema 1, the build is now schema 2 with a rename upgrader.
    monkeypatch.setattr(migrations, "CURRENT_SCHEMA_VERSION", 2)
    monkeypatch.setitem(migrations.UPGRADERS, 1, lambda d: {**d, "name": d["name"] + "!"})
    got = snap_client.get(f"/api/projects/{p['id']}").json()
    assert got["name"] == "P!" and got["schemaVersion"] == 2


def test_newer_schema_is_conflict(snap_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from viz.contracts import migrations

    p = _new(snap_client)
    monkeypatch.setattr(migrations, "CURRENT_SCHEMA_VERSION", 0)
    assert snap_client.get(f"/api/projects/{p['id']}").status_code == 409
