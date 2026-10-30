from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from viz.config import Settings
from viz.contracts import Project
from viz.errors import ConflictError
from viz.main import create_app
from viz.projects import service


@pytest.mark.parametrize("op", ["save", "autosave", "rename", "restore"])
def test_interleaved_sessions_cannot_both_write(tmp_path: Path, op: str) -> None:
    """Two sessions read the same revision; only the first write may succeed."""
    settings = Settings(data_dir=tmp_path / "d")
    with TestClient(create_app(settings)) as c:
        p = c.post("/api/projects", json={"name": "P"}).json()
        saved = c.put(f"/api/projects/{p['id']}", json=p).json()  # revision 1 + a snapshot
        version_id = c.get(f"/api/projects/{p['id']}/versions").json()[0]["id"]
        factory = c.app.state.sessionmaker  # type: ignore[attr-defined]
        base = Project.model_validate(saved)

        def run(db: Any, name: str) -> None:
            changed = base.model_copy(update={"name": name})
            if op == "save":
                service.save_project(db, p["id"], changed, autosave=False, settings=settings)
            elif op == "autosave":
                service.save_project(db, p["id"], changed, autosave=True, settings=settings)
            elif op == "rename":
                service.rename_project(db, p["id"], name, 1)
            else:
                service.restore_version(db, p["id"], version_id, 1, settings)

        with factory() as s1, factory() as s2:
            # Both sessions load the row at revision 1; s2 keeps it in its identity map, so
            # its read-side check passes and only the conditional UPDATE can catch the race.
            service.get_project(s1, p["id"])
            service.get_project(s2, p["id"])
            run(s1, "first")
            with pytest.raises(ConflictError):
                run(s2, "second")
        final = c.get(f"/api/projects/{p['id']}").json()
        assert final["revision"] == 2
        if op != "restore":
            assert final["name"] == "first"
        # the losing write left no snapshot behind
        revisions = [v["revision"] for v in c.get(f"/api/projects/{p['id']}/versions").json()]
        assert revisions.count(2) <= 1


def test_name_validation(client: TestClient) -> None:
    assert client.post("/api/projects", json={"name": "  "}).status_code == 422
    assert client.post("/api/projects", json={"name": "x" * 201}).status_code == 422
    p = client.post("/api/projects", json={"name": "x" * 200}).json()
    url = f"/api/projects/{p['id']}"
    assert client.patch(url, json={"name": "", "revision": 0}).status_code == 422
    assert client.patch(url, json={"name": "y" * 201, "revision": 0}).status_code == 422
    assert client.post(f"{url}/duplicate", json={"name": ""}).status_code == 422
    dup = client.post(f"{url}/duplicate", json={}).json()
    assert len(dup["name"]) == 200 and dup["name"].endswith(" copy")
