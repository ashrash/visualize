from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from viz.config import Settings
from viz.main import create_app


def _project_with_chart(client: TestClient, dataset_id: str) -> dict[str, Any]:
    p = client.post("/api/projects", json={"name": "Sales ünï"}).json()
    p["datasetIds"] = [dataset_id]
    p["charts"] = [
        {
            "id": "c1",
            "plotType": "bar",
            "source": {"kind": "dataset", "id": dataset_id},
            "encodings": {"x": [{"field": "region"}], "y": [{"field": "sales", "agg": "sum"}]},
        }
    ]
    p["folders"] = [{"id": "f", "name": "F", "items": [{"kind": "chart", "id": "c1"}]}]
    saved: dict[str, Any] = client.put(f"/api/projects/{p['id']}", json=p).json()
    return saved


def _upload(client: TestClient, data: bytes) -> Any:
    return client.post("/api/projects/import", files={"file": ("x.vizproj", data)})


def test_round_trip_without_data_reuses_catalog(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])
    r = client.get(f"/api/projects/{p['id']}/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert not any(n.endswith(".parquet") for n in names)

    res = _upload(client, r.content)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["project"]["id"] != p["id"] and body["project"]["revision"] == 0
    assert body["project"]["charts"][0]["source"]["id"] == sales_dataset["id"]
    assert body["datasetsMissing"] == [] and body["datasetsImported"] == []
    assert body["project"]["name"] == p["name"]


def test_round_trip_with_data_into_fresh_install(
    client: TestClient, sales_dataset: dict[str, Any], tmp_path: Path
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])
    r = client.get(f"/api/projects/{p['id']}/export?includeData=true")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    parquet = [n for n in z.namelist() if n.endswith(".parquet")]
    assert len(parquet) == 1
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["formatVersion"] == 1 and manifest["includesData"] is True

    other = Settings(data_dir=tmp_path / "other")
    with TestClient(create_app(other)) as fresh:
        body = _upload(fresh, r.content).json()
        assert body["datasetsImported"] == [sales_dataset["id"]]
        assert body["datasetsMissing"] == []
        got = fresh.get(f"/api/datasets/{sales_dataset['id']}").json()
        assert got["versions"][-1]["rowCount"] == 6
        rows = fresh.get(f"/api/datasets/{sales_dataset['id']}/rows?limit=5")
        assert rows.status_code == 200


def test_missing_dataset_is_reported(
    client: TestClient, sales_dataset: dict[str, Any], tmp_path: Path
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])
    data = client.get(f"/api/projects/{p['id']}/export").content
    with TestClient(create_app(Settings(data_dir=tmp_path / "empty"))) as fresh:
        body = _upload(fresh, data).json()
        assert body["datasetsMissing"] == [sales_dataset["id"]]
        assert body["warnings"]


def test_id_collision_remaps_references(
    client: TestClient, sales_dataset: dict[str, Any], tmp_path: Path
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])
    data = client.get(f"/api/projects/{p['id']}/export?includeData=true").content
    # Rewrite dataset.json so the archived dataset differs from the catalog's.
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            content = src.read(info.filename)
            if info.filename.endswith("dataset.json"):
                d = json.loads(content)
                d["versions"][-1]["rowCount"] = 999
                content = json.dumps(d).encode()
            dst.writestr(info.filename, content)
    body = _upload(client, out.getvalue()).json()
    new_id = body["datasetsRemapped"][sales_dataset["id"]]
    assert new_id != sales_dataset["id"]
    assert body["datasetsImported"] == [new_id]
    assert body["project"]["datasetIds"] == [new_id]
    assert body["project"]["charts"][0]["source"]["id"] == new_id
    assert body["project"]["folders"][0]["items"][0]["id"] == "c1"


def _zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for k, v in entries.items():
            z.writestr(k, v)
    return out.getvalue()


def test_rejects_bad_archives(client: TestClient) -> None:
    assert _upload(client, b"not a zip").status_code == 422
    assert _upload(client, _zip({"x.txt": b"1"})).status_code == 422
    manifest = json.dumps({"format": "vizproj", "formatVersion": 1}).encode()
    slip = _zip({"manifest.json": manifest, "../evil.txt": b"x"})
    assert _upload(client, slip).status_code == 422
    backslash = _zip({"manifest.json": manifest, "a\\b.txt": b"x"})
    assert _upload(client, backslash).status_code == 422
    newer = json.dumps({"format": "vizproj", "formatVersion": 99}).encode()
    assert _upload(client, _zip({"manifest.json": newer, "project.json": b"{}"})).status_code == 409


def test_oversize_archive_rejected(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "d", max_vizproj_mb=1)
    with TestClient(create_app(settings)) as c:
        big = _zip({"manifest.json": b"{}", "big.bin": b"\0" * (2 * 1024 * 1024)})
        assert _upload(c, big).status_code == 413


def test_old_schema_archive_is_upgraded(client: TestClient) -> None:
    """A project.json lacking newer optional fields still imports (additive defaults)."""
    doc = {"id": "old", "name": "Old", "schemaVersion": 1, "revision": 5}
    manifest = json.dumps({"format": "vizproj", "formatVersion": 1, "datasets": []}).encode()
    body = _upload(
        client, _zip({"manifest.json": manifest, "project.json": json.dumps(doc).encode()})
    )
    assert body.status_code == 201
    assert body.json()["project"]["revision"] == 0
