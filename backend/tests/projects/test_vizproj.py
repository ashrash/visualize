from __future__ import annotations

import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from viz.config import Settings
from viz.contracts import migrations
from viz.main import create_app
from viz.projects import vizproj


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
    p["dashboards"] = [
        {
            "id": "d1",
            "filters": [
                {"id": "f1", "label": "R", "field": "region", "fieldMap": {dataset_id: "r"}}
            ],
        }
    ]
    p["folders"] = [{"id": "f", "name": "F", "items": [{"kind": "chart", "id": "c1"}]}]
    saved: dict[str, Any] = client.put(f"/api/projects/{p['id']}", json=p).json()
    return saved


def _upload(client: TestClient, data: bytes) -> Any:
    return client.post("/api/projects/import", files={"file": ("x.vizproj", data)})


def _zip(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for k, v in entries.items():
            z.writestr(k, v)
    return out.getvalue()


def _rewrite(data: bytes, fn: Any) -> bytes:
    """Rebuild an archive, letting fn(name, content) -> (name, content) change each entry."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            name, content = fn(info.filename, src.read(info.filename))
            dst.writestr(name, content)
    return out.getvalue()


def _rename_id(old: str, new: str) -> Any:
    def fn(name: str, content: bytes) -> tuple[str, bytes]:
        if name == "manifest.json" or name == "project.json":
            return name, content.replace(old.encode(), new.encode())
        return name.replace(old, new), content

    return fn


def _export(client: TestClient, pid: str, data: bool = True) -> bytes:
    r = client.get(f"/api/projects/{pid}/export?includeData={str(data).lower()}")
    assert r.status_code == 200
    return r.content


def test_round_trip_without_data_reuses_catalog(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])
    r = client.get(f"/api/projects/{p['id']}/export")
    assert "attachment" in r.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert not any(n.endswith(".parquet") for n in names)

    body = _upload(client, r.content).json()
    assert body["project"]["id"] != p["id"] and body["project"]["revision"] == 0
    assert body["project"]["charts"][0]["source"]["id"] == sales_dataset["id"]
    assert body["datasetsMissing"] == [] and body["datasetsImported"] == []
    assert body["project"]["name"] == p["name"]


def test_identical_dataset_is_reused_with_warning(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])
    body = _upload(client, _export(client, p["id"])).json()
    assert body["datasetsImported"] == [] and body["datasetsRemapped"] == {}
    assert any("already in the catalog" in w for w in body["warnings"])
    assert len(client.get("/api/datasets").json()) == 1


def test_round_trip_with_data_into_fresh_install(
    client: TestClient, sales_dataset: dict[str, Any], tmp_path: Path
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])
    data = _export(client, p["id"])
    z = zipfile.ZipFile(io.BytesIO(data))
    assert len([n for n in z.namelist() if n.endswith(".parquet")]) == 1
    manifest = json.loads(z.read("manifest.json"))
    assert manifest["formatVersion"] == 1 and manifest["includesData"] is True

    with TestClient(create_app(Settings(data_dir=tmp_path / "other"))) as fresh:
        body = _upload(fresh, data).json()
        (new,) = body["datasetsImported"]
        assert new != sales_dataset["id"]  # archive ids are never reused as catalog keys
        assert body["datasetsRemapped"] == {sales_dataset["id"]: new}
        assert body["datasetsMissing"] == []
        assert body["project"]["datasetIds"] == [new]
        got = fresh.get(f"/api/datasets/{new}").json()
        assert got["versions"][-1]["rowCount"] == 6
        assert got["versions"][-1]["sizeBytes"] > 0
        assert fresh.get(f"/api/datasets/{new}/rows?limit=5").status_code == 200
        # no leftover temp files
        assert not list((tmp_path / "other").rglob("*.tmp"))


def test_missing_dataset_is_reported(
    client: TestClient, sales_dataset: dict[str, Any], tmp_path: Path
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])
    data = _export(client, p["id"], data=False)
    with TestClient(create_app(Settings(data_dir=tmp_path / "empty"))) as fresh:
        body = _upload(fresh, data).json()
        assert body["datasetsMissing"] == [sales_dataset["id"]]
        assert body["warnings"]


def test_different_data_same_id_is_remapped_everywhere(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    did = sales_dataset["id"]
    p = _project_with_chart(client, did)

    def reorder(name: str, content: bytes) -> tuple[str, bytes]:
        if name.endswith(".parquet"):
            table = pq.read_table(io.BytesIO(content))
            out = io.BytesIO()
            pq.write_table(table.take(list(reversed(range(table.num_rows)))), out)
            return name, out.getvalue()
        return name, content

    body = _upload(client, _rewrite(_export(client, p["id"]), reorder)).json()
    new = body["datasetsRemapped"][did]
    assert new != did and body["datasetsImported"] == [new]
    proj = body["project"]
    assert proj["datasetIds"] == [new]
    assert proj["charts"][0]["source"]["id"] == new
    assert proj["dashboards"][0]["filters"][0]["fieldMap"] == {new: "r"}
    assert proj["folders"][0]["items"][0]["id"] == "c1"
    # the original dataset file is untouched and both datasets exist
    assert len(client.get("/api/datasets").json()) == 2


def test_uppercase_victim_id_cannot_overwrite_existing_file(
    client: TestClient, sales_dataset: dict[str, Any], settings: Settings
) -> None:
    did = sales_dataset["id"]
    victim = settings.data_dir / "datasets" / did / "v1.parquet"
    before = victim.read_bytes()
    p = _project_with_chart(client, did)
    data = _rewrite(_export(client, p["id"]), _rename_id(did, did.upper()))
    body = _upload(client, data).json()
    (new,) = body["datasetsImported"]
    assert new not in (did, did.upper())
    assert victim.read_bytes() == before


def test_dot_segment_and_non_uuid_ids_rejected(
    client: TestClient, sales_dataset: dict[str, Any], settings: Settings
) -> None:
    did = sales_dataset["id"]
    p = _project_with_chart(client, did)
    data = _export(client, p["id"])
    victim = settings.data_dir / "datasets" / did / "v1.parquet"
    before = victim.read_bytes()
    for bad in (f"{did}/.", "../x", "not-a-uuid"):
        r = _upload(client, _rewrite(data, _rename_id(did, bad)))
        assert r.status_code == 422, bad
    assert victim.read_bytes() == before


def test_same_archive_imported_concurrently(
    client: TestClient, sales_dataset: dict[str, Any], tmp_path: Path
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])
    data = _export(client, p["id"])
    with TestClient(create_app(Settings(data_dir=tmp_path / "fresh"))) as fresh:
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: _upload(fresh, data), range(2)))
        assert [r.status_code for r in results] == [201, 201]
        ids = [r.json()["datasetsImported"][0] for r in results]
        assert ids[0] != ids[1]
        assert len(fresh.get("/api/datasets").json()) == 2
        for i in ids:
            assert fresh.get(f"/api/datasets/{i}/rows?limit=1").status_code == 200


def test_paths_in_dataset_json_are_not_trusted(
    client: TestClient, sales_dataset: dict[str, Any], tmp_path: Path
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])

    def poison(name: str, content: bytes) -> tuple[str, bytes]:
        if name.endswith("dataset.json"):
            d = json.loads(content)
            d["source"]["originalPath"] = "\\\\evil\\share\\x.csv"
            d["source"]["fileName"] = "..\\..\\evil.csv"
            d["versions"][-1]["sourcePath"] = "../../etc/passwd"
            d["versions"][-1]["parquetPath"] = "../../escape.parquet"
            d["versions"][-1]["sizeBytes"] = 1
            return name, json.dumps(d).encode()
        if name.endswith(".parquet"):
            table = pq.read_table(io.BytesIO(content))
            out = io.BytesIO()
            pq.write_table(table.take(list(reversed(range(table.num_rows)))), out)
            return name, out.getvalue()
        return name, content

    with TestClient(create_app(Settings(data_dir=tmp_path / "fresh"))) as fresh:
        body = _upload(fresh, _rewrite(_export(client, p["id"]), poison)).json()
        (new,) = body["datasetsImported"]
        got = fresh.get(f"/api/datasets/{new}").json()
        assert got["source"]["originalPath"] is None
        assert got["source"]["fileName"] == "evil.csv"
        v = got["versions"][-1]
        assert v["sourcePath"] is None
        assert v["parquetPath"] == f"datasets/{new}/v1.parquet"
        assert v["sizeBytes"] > 1  # recomputed from the copied file
        assert not (tmp_path / "escape.parquet").exists()


def test_rejects_non_parquet_and_mismatched_data(
    client: TestClient, sales_dataset: dict[str, Any], settings: Settings
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])
    data = _export(client, p["id"])
    junk = _rewrite(data, lambda n, c: (n, b"junk" if n.endswith(".parquet") else c))
    assert _upload(client, junk).status_code == 422

    def wrong_rows(n: str, c: bytes) -> tuple[str, bytes]:
        if n.endswith("dataset.json"):
            d = json.loads(c)
            d["versions"][-1]["rowCount"] = 999
            return n, json.dumps(d).encode()
        return n, c

    assert _upload(client, _rewrite(data, wrong_rows)).status_code == 422
    # nothing registered by the failed imports
    assert len(client.get("/api/datasets").json()) == 1
    assert len(list((settings.data_dir / "datasets").iterdir())) == 1


def test_duplicate_manifest_id_and_wrong_column_type_rejected(
    client: TestClient, sales_dataset: dict[str, Any], settings: Settings
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])
    data = _export(client, p["id"])

    def dup_entry(name: str, content: bytes) -> tuple[str, bytes]:
        if name == "manifest.json":
            m = json.loads(content)
            m["datasets"].append(m["datasets"][0])
            return name, json.dumps(m).encode()
        return name, content

    assert _upload(client, _rewrite(data, dup_entry)).status_code == 422

    def wrong_type(name: str, content: bytes) -> tuple[str, bytes]:
        if name.endswith("dataset.json"):
            d = json.loads(content)
            for col in d["versions"][-1]["columns"]:
                if col["name"] == "region":
                    col["type"] = "BIGINT"  # the file holds strings
            return name, json.dumps(d).encode()
        return name, content

    assert _upload(client, _rewrite(data, wrong_type)).status_code == 422
    assert len(list((settings.data_dir / "datasets").iterdir())) == 1


def test_failed_import_registers_nothing(
    client: TestClient, sales_dataset: dict[str, Any], settings: Settings
) -> None:
    """Two archived datasets, the second corrupt: the first must be rolled back."""
    did = sales_dataset["id"]
    p = _project_with_chart(client, did)
    data = _export(client, p["id"])
    second = "11111111-1111-4111-8111-111111111111"
    src = zipfile.ZipFile(io.BytesIO(data))
    manifest = json.loads(src.read("manifest.json"))

    # first dataset differs from the catalog so it gets registered
    def reorder(name: str, content: bytes) -> bytes:
        table = pq.read_table(io.BytesIO(content))
        out = io.BytesIO()
        pq.write_table(table.take(list(reversed(range(table.num_rows)))), out)
        return out.getvalue()

    entries = {n: src.read(n) for n in src.namelist()}
    entries[f"datasets/{did}/v1.parquet"] = reorder("", entries[f"datasets/{did}/v1.parquet"])
    entries[f"datasets/{second}/dataset.json"] = entries[f"datasets/{did}/dataset.json"]
    entries[f"datasets/{second}/v1.parquet"] = b"corrupt"
    manifest["datasets"].append(
        {"id": second, "name": "b", "versions": [{"n": 1, "file": f"datasets/{second}/v1.parquet"}]}
    )
    entries["manifest.json"] = json.dumps(manifest).encode()
    assert _upload(client, _zip(entries)).status_code == 422
    assert len(client.get("/api/datasets").json()) == 1
    assert len(list((settings.data_dir / "datasets").iterdir())) == 1
    assert len(client.get("/api/projects").json()) == 1


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


@pytest.mark.parametrize(
    "manifest",
    [
        {"format": "vizproj"},  # no formatVersion
        {"format": "vizproj", "formatVersion": "x"},
        {"format": "vizproj", "formatVersion": 1, "datasets": "nope"},
        {"format": "vizproj", "formatVersion": 1, "datasets": [{"name": "x"}]},
        {"format": "vizproj", "formatVersion": 1, "datasets": [{"id": 5}]},
        {"format": "vizproj", "formatVersion": 1, "datasets": [{"id": "x", "versions": [1]}]},
    ],
)
def test_malformed_manifest_is_422(client: TestClient, manifest: dict[str, Any]) -> None:
    proj = json.dumps({"id": "p", "name": "P"}).encode()
    r = _upload(
        client, _zip({"manifest.json": json.dumps(manifest).encode(), "project.json": proj})
    )
    assert r.status_code == 422, r.text


def test_malformed_project_json_is_422(client: TestClient) -> None:
    manifest = json.dumps({"format": "vizproj", "formatVersion": 1}).encode()
    for proj in (b"[]", b'{"id": 1}', b"not json"):
        r = _upload(client, _zip({"manifest.json": manifest, "project.json": proj}))
        assert r.status_code == 422, proj


def test_oversize_archive_rejected(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "d", max_vizproj_mb=1)
    with TestClient(create_app(settings)) as c:
        big = _zip({"manifest.json": b"{}", "big.bin": b"\0" * (2 * 1024 * 1024)})
        assert _upload(c, big).status_code == 413
        # also rejected up front by upload size, before the zip is opened
        assert _upload(c, b"\0" * (2 * 1024 * 1024)).status_code == 413


def test_oversize_json_entry_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vizproj, "_MAX_JSON_BYTES", 10)
    manifest = json.dumps({"format": "vizproj", "formatVersion": 1}).encode()
    r = _upload(client, _zip({"manifest.json": manifest, "project.json": b"{" + b" " * 100 + b"}"}))
    assert r.status_code == 413


def test_old_schema_archive_is_upgraded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(migrations, "CURRENT_SCHEMA_VERSION", 2)
    monkeypatch.setitem(migrations.UPGRADERS, 1, lambda d: {**d, "name": d["name"] + "!"})
    doc = {"id": "old", "name": "Old", "schemaVersion": 1, "revision": 5}
    manifest = json.dumps({"format": "vizproj", "formatVersion": 1, "datasets": []}).encode()
    r = _upload(client, _zip({"manifest.json": manifest, "project.json": json.dumps(doc).encode()}))
    assert r.status_code == 201, r.text
    got = r.json()["project"]
    assert got["name"] == "Old!" and got["revision"] == 0 and got["schemaVersion"] == 2


def test_export_warns_when_data_file_missing(
    client: TestClient, sales_dataset: dict[str, Any], settings: Settings
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])
    (settings.data_dir / "datasets" / sales_dataset["id"] / "v1.parquet").unlink()
    data = _export(client, p["id"])
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(data)).read("manifest.json"))
    assert manifest["warnings"] and "missing" in manifest["warnings"][0]


def test_failed_export_leaves_no_partial_file(
    client: TestClient,
    sales_dataset: dict[str, Any],
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = _project_with_chart(client, sales_dataset["id"])

    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(zipfile.ZipFile, "writestr", boom)
    with pytest.raises(RuntimeError):
        client.get(f"/api/projects/{p['id']}/export")
    assert not list((settings.data_dir / "temp").glob("export-*"))
