""".vizproj archives: a zip of manifest.json, project.json and optionally Parquet files.

Layout (formatVersion 1):
    manifest.json                 format, versions, dataset list, warnings
    project.json                  the Project document
    datasets/<id>/dataset.json    catalog entry (local paths scrubbed)
    datasets/<id>/v<n>.parquet    latest version, only when exported with data

An archive is untrusted input. Its dataset ids are never used as path components or catalog
keys: every dataset that is not an exact, byte-identical match of a catalog entry is registered
under a fresh id, its catalog entry is rebuilt from an allowlist, and paths are never copied.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from viz import __version__
from viz.config import Settings
from viz.contracts import (
    Dataset,
    DatasetSource,
    DatasetVersion,
    Project,
    ProjectImportResult,
    new_id,
)
from viz.contracts.migrations import CURRENT_SCHEMA_VERSION
from viz.errors import ConflictError, InvalidSpecError, NotFoundError, PayloadTooLargeError
from viz.ingest import catalog
from viz.meta.models import DatasetRow, utcnow
from viz.projects import service
from viz.projects.validate import validate_project
from viz.storage import LocalStorage
from viz.storage.local import safe_file_name

FORMAT_VERSION = 1
_MAX_ENTRIES = 10_000
_MAX_JSON_BYTES = 64 * 1024 * 1024
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class _ManifestVersion(BaseModel):
    n: int = Field(ge=1)
    file: str


class _ManifestDataset(BaseModel):
    id: str
    name: str = ""
    versions: list[_ManifestVersion] = Field(default_factory=list)


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: str
    formatVersion: int
    datasets: list[_ManifestDataset] = Field(default_factory=list)


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
    """Rewrite dataset ids in refs and in source-keyed maps (dashboard filter fieldMap)."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "fieldMap" and isinstance(value, dict):
                out[key] = {mapping.get(k, k): v for k, v in value.items()}
            else:
                out[key] = _remap(value, mapping)
        if out.get("kind") == "dataset" and out.get("id") in mapping:
            out["id"] = mapping[out["id"]]
        return out
    if isinstance(node, list):
        return [_remap(v, mapping) for v in node]
    return node


def _project_dataset_ids(doc: dict[str, Any]) -> list[str]:
    ids = list(doc.get("datasetIds", [])) + list(_dataset_refs(doc))
    return list(dict.fromkeys(ids))


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- export


def export_project(
    db: Session, storage: LocalStorage, project_id: str, *, include_data: bool
) -> tuple[Path, str]:
    """Write the archive to a temp file; returns (path, suggested file name)."""
    project = service.get_project(db, project_id)
    doc = project.model_dump(mode="json")
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    storage.temp_dir.mkdir(parents=True, exist_ok=True)
    target = storage.temp_dir / f"export-{uuid4().hex}.vizproj"
    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for dataset_id in _project_dataset_ids(doc):
                try:
                    dataset = catalog.get_dataset(db, dataset_id)
                except NotFoundError:
                    warnings.append(f"dataset {dataset_id} is not in the catalog; not exported")
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
                    else:
                        warnings.append(f"data file of dataset {dataset.name} is missing")
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
                "warnings": warnings,
            }
            zf.writestr("manifest.json", json.dumps(manifest))
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in project.name).strip()
    return target, f"{safe or 'project'}.vizproj"


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
        info = zf.getinfo(name)
    except KeyError as exc:
        raise InvalidSpecError(f"not a .vizproj archive: {name} is missing") from exc
    if info.file_size > _MAX_JSON_BYTES:
        raise PayloadTooLargeError(f"{name} is larger than {_MAX_JSON_BYTES // 2**20} MB")
    try:
        return json.loads(zf.read(info))
    except json.JSONDecodeError as exc:
        raise InvalidSpecError(f"{name} is not valid JSON") from exc


def _arrow_family(t: pa.DataType) -> str | None:
    checks = (
        ("bool", pa.types.is_boolean),
        ("int", pa.types.is_integer),
        ("float", pa.types.is_floating),
        ("decimal", pa.types.is_decimal),
        ("string", lambda x: pa.types.is_string(x) or pa.types.is_large_string(x)),
        ("date", pa.types.is_date),
        ("timestamp", pa.types.is_timestamp),
        ("time", pa.types.is_time),
    )
    return next((name for name, test in checks if test(t)), None)


def _duckdb_family(type_name: str) -> str | None:
    t = type_name.upper().split("(")[0].strip()
    if t in {"BOOLEAN", "BOOL"}:
        return "bool"
    if t.endswith("INT") or t in {"BIGINT", "SMALLINT", "TINYINT", "HUGEINT", "INTEGER"}:
        return "int"
    if t in {"DOUBLE", "FLOAT", "REAL"}:
        return "float"
    if t in {"DECIMAL", "NUMERIC"}:
        return "decimal"
    if t in {"VARCHAR", "TEXT", "STRING", "UUID"} or t.startswith("ENUM"):
        return "string"
    if t == "DATE":
        return "date"
    if t.startswith("TIMESTAMP"):
        return "timestamp"
    if t.startswith("TIME"):
        return "time"
    return None


def _schema_matches(schema: pa.Schema, version: DatasetVersion) -> bool:
    """Column names and (where both sides are a known family) column types must agree."""
    if list(schema.names) != [c.name for c in version.columns]:
        return False
    for field, column in zip(schema, version.columns, strict=True):
        actual, claimed = _arrow_family(field.type), _duckdb_family(column.type)
        if actual is not None and claimed is not None and actual != claimed:
            return False
    return True


def _sanitize(dataset: Dataset, version: DatasetVersion, new_dataset_id: str, rel: str) -> Dataset:
    """Rebuild a catalog entry from an allowlist of fields; paths are never carried over."""
    name = dataset.name.strip()[:200] or "Imported dataset"
    return Dataset(
        id=new_dataset_id,
        name=name,
        source=DatasetSource(
            kind=dataset.source.kind,
            file_name=safe_file_name(dataset.source.file_name),
            original_path=None,
            options=dataset.source.options,
        ),
        versions=[
            DatasetVersion(
                n=version.n,
                parquet_path=rel,
                row_count=version.row_count,
                columns=version.columns,
                imported_at=version.imported_at,
                source_path=None,
                options=None,
                size_bytes=version.size_bytes,
            )
        ],
    )


def _register_dataset(db: Session, storage: LocalStorage, dataset: Dataset, staged: Path) -> None:
    """Move the staged Parquet file into place and add the catalog entry.

    `dataset` already has a fresh id and scrubbed paths.
    TODO(data-core): replace the body with catalog.register_dataset_bundle(...) once it exists.
    """
    dest = storage.resolve(dataset.current().parquet_path)
    os.replace(staged, dest)
    catalog.save_dataset(db, dataset)


def _rollback_registered(db: Session, storage: LocalStorage, ids: list[str]) -> None:
    db.rollback()
    if ids:
        db.execute(delete(DatasetRow).where(DatasetRow.id.in_(ids)))
        db.commit()
    for dataset_id in ids:
        shutil.rmtree(storage.datasets_dir / dataset_id, ignore_errors=True)


def _same_as_catalog(
    db: Session, storage: LocalStorage, dataset_id: str, size: int, sha: str
) -> bool:
    """True when the catalog holds this exact id with a byte-identical latest Parquet file."""
    if db.get(DatasetRow, dataset_id) is None:
        return False
    existing = catalog.get_dataset(db, dataset_id)
    if not existing.versions:
        return False
    path = storage.resolve(existing.current().parquet_path)
    return path.is_file() and path.stat().st_size == size and _sha256_of(path) == sha


def import_project(
    db: Session, storage: LocalStorage, settings: Settings, fileobj: BinaryIO
) -> ProjectImportResult:
    registered: list[str] = []
    try:
        return _import(db, storage, settings, fileobj, registered)
    except BaseException:
        _rollback_registered(db, storage, registered)
        raise


def _import(
    db: Session,
    storage: LocalStorage,
    settings: Settings,
    fileobj: BinaryIO,
    registered: list[str],
) -> ProjectImportResult:
    entry = "archive"
    try:
        try:
            zf = zipfile.ZipFile(fileobj)
        except zipfile.BadZipFile as exc:
            raise InvalidSpecError("file is not a .vizproj archive") from exc
        with zf:
            _check_names(zf, settings)
            entry = "manifest.json"
            raw_manifest = _read_json(zf, "manifest.json")
            if not isinstance(raw_manifest, dict) or raw_manifest.get("format") != "vizproj":
                raise InvalidSpecError("file is not a .vizproj archive")
            if int(raw_manifest.get("formatVersion", 0)) > FORMAT_VERSION:
                raise ConflictError(
                    "archive was written by a newer version of Visualize "
                    f"(format {raw_manifest['formatVersion']}, this build reads {FORMAT_VERSION})"
                )
            manifest = _Manifest.model_validate(raw_manifest)
            entry = "project.json"
            # Upgrade and validate before touching the catalog or the data dir.
            project = service.load_doc(_read_json(zf, "project.json"))
            validate_project(project)
            doc = project.model_dump(mode="json")

            mapping: dict[str, str] = {}
            imported: list[str] = []
            warnings: list[str] = []
            seen_ids: set[str] = set()
            for item in manifest.datasets:
                if item.id in seen_ids:
                    raise InvalidSpecError(f"manifest: dataset id {item.id} is listed twice")
                seen_ids.add(item.id)
                if not _UUID.fullmatch(item.id):
                    raise InvalidSpecError(f"manifest: invalid dataset id {item.id!r}")
                if not item.versions:
                    continue  # exported without data; resolved against the catalog below
                entry = f"datasets/{item.id}"
                data = item.versions[-1]
                if data.file != f"datasets/{item.id}/v{data.n}.parquet":
                    raise InvalidSpecError(f"manifest: unexpected file name {data.file!r}")
                dataset = Dataset.model_validate(_read_json(zf, f"datasets/{item.id}/dataset.json"))
                version = next((v for v in dataset.versions if v.n == data.n), None)
                if version is None:
                    raise InvalidSpecError(f"{entry}/dataset.json does not describe v{data.n}")

                target_id = new_id()
                registered.append(target_id)
                rel = storage.parquet_rel(target_id, data.n)
                dest = storage.resolve(rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                staged = dest.with_name(dest.name + ".tmp")
                entry = data.file
                sha = hashlib.sha256()
                size = 0
                with zf.open(data.file) as src, staged.open("wb") as out:
                    for chunk in iter(lambda: src.read(1024 * 1024), b""):
                        sha.update(chunk)
                        size += len(chunk)
                        out.write(chunk)
                try:
                    meta = pq.read_metadata(staged)
                except (pa.ArrowException, OSError) as exc:
                    raise InvalidSpecError(f"{data.file} is not a Parquet file") from exc
                if meta.num_rows != version.row_count or not _schema_matches(
                    meta.schema.to_arrow_schema(), version
                ):
                    raise InvalidSpecError(f"{data.file} does not match its dataset.json")

                if _same_as_catalog(db, storage, item.id, size, sha.hexdigest()):
                    shutil.rmtree(dest.parent, ignore_errors=True)
                    registered.remove(target_id)
                    warnings.append(
                        f"dataset {dataset.name} is already in the catalog with identical "
                        "data; using the existing copy"
                    )
                    continue
                clean = _sanitize(
                    dataset, version.model_copy(update={"size_bytes": size}), target_id, rel
                )
                _register_dataset(db, storage, clean, staged)
                mapping[item.id] = target_id
                imported.append(target_id)
    except (ValidationError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        raise InvalidSpecError(f"malformed archive ({entry}): {exc}") from exc

    if mapping:
        doc = _remap(doc, mapping)
        doc["datasetIds"] = [mapping.get(i, i) for i in doc.get("datasetIds", [])]
    wanted = list(dict.fromkeys(_project_dataset_ids(doc)))
    in_catalog = set(db.scalars(select(DatasetRow.id).where(DatasetRow.id.in_(wanted))))
    missing = [d for d in wanted if d not in in_catalog]
    for dataset_id in missing:
        warnings.append(f"dataset {dataset_id} is not in the archive or the catalog")
    created = service.create_from_doc(db, Project.model_validate(doc))
    return ProjectImportResult(
        project=created,
        datasets_imported=imported,
        datasets_remapped=mapping,
        datasets_missing=missing,
        warnings=warnings,
    )
