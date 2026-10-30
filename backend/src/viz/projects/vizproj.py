""".vizproj archives: a zip of manifest.json, project.json and optionally Parquet files.

Layout (formatVersion 1):
    manifest.json                 format, versions, dataset list
    project.json                  the Project document
    datasets/<id>/dataset.json    catalog entry (local paths scrubbed)
    datasets/<id>/v<n>.parquet    latest version, only when exported with data
"""

from __future__ import annotations

import json
import shutil
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from viz import __version__
from viz.config import Settings
from viz.contracts import Dataset, Project, ProjectImportResult, new_id
from viz.contracts.migrations import CURRENT_SCHEMA_VERSION
from viz.errors import ConflictError, InvalidSpecError, NotFoundError, PayloadTooLargeError
from viz.ingest import catalog
from viz.meta.models import DatasetRow, utcnow
from viz.projects import service
from viz.storage import LocalStorage

FORMAT_VERSION = 1
_MAX_ENTRIES = 10_000


def _dataset_refs(node: Any) -> Iterator[str]:
    """Yield every dataset id referenced anywhere in a raw project document."""
    if isinstance(node, dict):
        if node.get("kind") == "dataset" and isinstance(node.get("id"), str):
            yield node["id"]
        for value in node.values():
            yield from _dataset_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _dataset_refs(item)


def _remap(node: Any, mapping: dict[str, str]) -> Any:
    if isinstance(node, dict):
        out = {k: _remap(v, mapping) for k, v in node.items()}
        if out.get("kind") == "dataset" and out.get("id") in mapping:
            out["id"] = mapping[out["id"]]
        return out
    if isinstance(node, list):
        return [_remap(v, mapping) for v in node]
    return node


def _project_dataset_ids(doc: dict[str, Any]) -> list[str]:
    ids = list(doc.get("datasetIds", [])) + list(_dataset_refs(doc))
    return list(dict.fromkeys(ids))


# ---------------------------------------------------------------- export


def export_project(
    db: Session, storage: LocalStorage, project_id: str, *, include_data: bool
) -> tuple[Path, str]:
    """Write the archive to a temp file; returns (path, suggested file name)."""
    project = service.get_project(db, project_id)
    doc = project.model_dump(mode="json")
    entries: list[dict[str, Any]] = []
    storage.temp_dir.mkdir(parents=True, exist_ok=True)
    target = storage.temp_dir / f"export-{uuid4().hex}.vizproj"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for dataset_id in _project_dataset_ids(doc):
            try:
                dataset = catalog.get_dataset(db, dataset_id)
            except NotFoundError:
                continue
            scrubbed = dataset.model_copy(
                update={"source": dataset.source.model_copy(update={"original_path": None})}
            )
            files: list[dict[str, Any]] = []
            if include_data and dataset.versions:
                latest = dataset.current()
                src = storage.resolve(latest.parquet_path)
                if src.exists():
                    name = f"datasets/{dataset_id}/v{latest.n}.parquet"
                    zf.write(src, name, compress_type=zipfile.ZIP_STORED)
                    files.append({"n": latest.n, "file": name})
            zf.writestr(
                f"datasets/{dataset_id}/dataset.json", scrubbed.model_dump_json(by_alias=True)
            )
            entries.append({"id": dataset_id, "name": dataset.name, "versions": files})
        zf.writestr("project.json", json.dumps(doc))
        manifest = {
            "format": "vizproj",
            "formatVersion": FORMAT_VERSION,
            "appVersion": __version__,
            "schemaVersion": CURRENT_SCHEMA_VERSION,
            "exportedAt": utcnow().isoformat(),
            "includesData": include_data,
            "datasets": entries,
        }
        zf.writestr("manifest.json", json.dumps(manifest))
    safe = (
        "".join(c if c.isalnum() or c in " -_." else "_" for c in project.name).strip() or "project"
    )
    return target, f"{safe}.vizproj"


# ---------------------------------------------------------------- import


def _check_names(zf: zipfile.ZipFile, settings: Settings) -> None:
    infos = zf.infolist()
    if len(infos) > _MAX_ENTRIES:
        raise PayloadTooLargeError("archive has too many entries")
    if sum(i.file_size for i in infos) > settings.max_vizproj_mb * 1024 * 1024:
        raise PayloadTooLargeError(f"archive is larger than {settings.max_vizproj_mb} MB")
    for info in infos:
        name = info.filename
        path = PurePosixPath(name)
        mode = info.external_attr >> 16
        if (
            "\\" in name
            or path.is_absolute()
            or ".." in path.parts
            or (len(name) > 1 and name[1] == ":")
            or (mode & 0o170000) == 0o120000  # symlink
        ):
            raise InvalidSpecError(f"unsafe path in archive: {name}")


def _read_json(zf: zipfile.ZipFile, name: str) -> Any:
    try:
        return json.loads(zf.read(name))
    except KeyError as exc:
        raise InvalidSpecError(f"not a .vizproj archive: {name} is missing") from exc
    except json.JSONDecodeError as exc:
        raise InvalidSpecError(f"{name} is not valid JSON") from exc


def _same_content(a: Dataset, b: Dataset) -> bool:
    if not a.versions or not b.versions:
        return False
    x, y = a.current(), b.current()
    return x.row_count == y.row_count and [(c.name, c.type) for c in x.columns] == [
        (c.name, c.type) for c in y.columns
    ]


def _register_dataset(
    db: Session, storage: LocalStorage, dataset: Dataset, parquet: Path, new_dataset_id: str
) -> Dataset:
    """Copy one Parquet file into the data dir and add the catalog entry.

    TODO(data-core): replace the body with catalog.register_dataset_bundle(...) once it exists.
    """
    version = dataset.current()
    rel = storage.parquet_rel(new_dataset_id, version.n)
    dest = storage.resolve(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(parquet, dest)
    registered = dataset.model_copy(
        update={
            "id": new_dataset_id,
            "versions": [version.model_copy(update={"parquet_path": rel})],
        }
    )
    catalog.save_dataset(db, registered)
    return registered


def import_project(
    db: Session, storage: LocalStorage, settings: Settings, fileobj: BinaryIO
) -> ProjectImportResult:
    try:
        zf = zipfile.ZipFile(fileobj)
    except zipfile.BadZipFile as exc:
        raise InvalidSpecError("file is not a .vizproj archive") from exc
    with zf:
        _check_names(zf, settings)
        manifest = _read_json(zf, "manifest.json")
        if manifest.get("format") != "vizproj":
            raise InvalidSpecError("file is not a .vizproj archive")
        if int(manifest.get("formatVersion", 0)) > FORMAT_VERSION:
            raise ConflictError(
                "archive was written by a newer version of Visualize "
                f"(format {manifest['formatVersion']}, this build reads {FORMAT_VERSION})"
            )
        raw = _read_json(zf, "project.json")
        # Upgrade + validate before touching the catalog.
        project = service.load_doc(raw)
        doc = project.model_dump(mode="json")

        mapping: dict[str, str] = {}
        imported: list[str] = []
        warnings: list[str] = []
        temp = storage.temp_dir / f"import-{uuid4().hex}"
        temp.mkdir(parents=True, exist_ok=True)
        try:
            for entry in manifest.get("datasets", []):
                dataset_id = str(entry["id"])
                files = entry.get("versions", [])
                if not files:
                    continue  # exported without data; resolved against the catalog below
                dataset = Dataset.model_validate(
                    _read_json(zf, f"datasets/{dataset_id}/dataset.json")
                ).model_copy(update={"id": dataset_id})
                file = files[-1]
                version = next((v for v in dataset.versions if v.n == file["n"]), None)
                if version is None:
                    warnings.append(f"dataset {dataset.name}: version {file['n']} not described")
                    continue
                dataset = dataset.model_copy(update={"versions": [version]})
                existing = db.get(DatasetRow, dataset_id)
                if existing is not None:
                    if _same_content(catalog.get_dataset(db, dataset_id), dataset):
                        continue  # reuse the catalog's copy
                    target_id = new_id()
                    mapping[dataset_id] = target_id
                else:
                    target_id = dataset_id
                extracted = temp / f"{uuid4().hex}.parquet"
                with zf.open(file["file"]) as src, extracted.open("wb") as out:
                    shutil.copyfileobj(src, out)
                _register_dataset(db, storage, dataset, extracted, target_id)
                imported.append(target_id)
        finally:
            shutil.rmtree(temp, ignore_errors=True)

    if mapping:
        doc = _remap(doc, mapping)
        doc["datasetIds"] = [mapping.get(i, i) for i in doc.get("datasetIds", [])]
    wanted = list(dict.fromkeys(_project_dataset_ids(doc)))
    in_catalog = set(db.scalars(select(DatasetRow.id).where(DatasetRow.id.in_(wanted))))
    missing = [d for d in wanted if d not in in_catalog]
    for dataset_id in missing:
        warnings.append(f"dataset {dataset_id} is not in the archive or the catalog")
    doc["datasetIds"] = list(dict.fromkeys(doc.get("datasetIds", [])))
    created = service.create_from_doc(db, Project.model_validate(doc))
    return ProjectImportResult(
        project=created,
        datasets_imported=imported,
        datasets_remapped=mapping,
        datasets_missing=missing,
        warnings=warnings,
    )
