"""Dataset catalog, stored in the metadata DB."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from viz.contracts import Dataset
from viz.errors import ConflictError, InvalidSpecError, NotFoundError
from viz.meta.models import DatasetRow, ProjectRow, utcnow
from viz.storage import LocalStorage

log = logging.getLogger(__name__)

__all__ = [
    "DatasetRow",
    "dataset_refs",
    "delete_dataset_row",
    "get_dataset",
    "list_datasets",
    "projects_using",
    "referenced_dataset_ids",
    "register_dataset_bundle",
    "save_dataset",
    "sweep_orphans",
]


def list_datasets(db: Session) -> list[Dataset]:
    rows = db.scalars(select(DatasetRow).order_by(DatasetRow.created_at)).all()
    return [Dataset.model_validate(row.doc) for row in rows]


def get_dataset(db: Session, dataset_id: str) -> Dataset:
    row = db.get(DatasetRow, dataset_id)
    if row is None:
        raise NotFoundError(f"dataset {dataset_id} not found")
    return Dataset.model_validate(row.doc)


def save_dataset(db: Session, dataset: Dataset) -> None:
    doc = dataset.model_dump(mode="json")
    row = db.get(DatasetRow, dataset.id)
    if row is None:
        db.add(DatasetRow(id=dataset.id, name=dataset.name, doc=doc))
    else:
        row.name = dataset.name
        row.doc = doc
        row.updated_at = utcnow()
    db.commit()


def delete_dataset_row(db: Session, dataset_id: str) -> None:
    row = db.get(DatasetRow, dataset_id)
    if row is not None:
        db.delete(row)
        db.commit()


# -- references -------------------------------------------------------------------------------


def dataset_refs(node: Any) -> Iterator[str]:
    """Every dataset id referenced by a `SourceRef` (`{kind: "dataset", id}`) anywhere in a
    JSON document: chart sources, pipeline sources, join `right`, union `others`, widgets."""
    if isinstance(node, dict):
        if node.get("kind") == "dataset" and isinstance(node.get("id"), str):
            yield node["id"]
        for value in node.values():
            yield from dataset_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from dataset_refs(item)


def referenced_dataset_ids(doc: dict[str, Any]) -> list[str]:
    """A project document's datasets: `datasetIds` plus every `SourceRef`, de-duplicated.
    Shared helper; platform's vizproj export can use it too."""
    ids = list(doc.get("datasetIds") or []) + list(dataset_refs(doc))
    return list(dict.fromkeys(ids))


def projects_using(db: Session, dataset_id: str) -> list[str]:
    """Names of projects that reference `dataset_id` anywhere."""
    names: list[str] = []
    for row in db.scalars(select(ProjectRow)).all():
        if dataset_id in referenced_dataset_ids(row.doc or {}):
            names.append(row.name)
    return names


# -- bundles and orphans ----------------------------------------------------------------------


def register_dataset_bundle(
    db: Session,
    storage: LocalStorage,
    dataset: Dataset,
    parquet_files: dict[int, Path],
    *,
    new_id: str | None = None,
    move: bool = False,
) -> Dataset:
    """Register a dataset from a project bundle (.vizproj import).

    Copies (or with `move=True`, moves) each `parquet_files[n]` to
    `datasets/<id>/v<n>.parquet`, keeps only versions that came with a file, rewrites their
    `parquetPath`, and saves the catalog row under `new_id` when given. Without `new_id` the
    dataset keeps its id, and an existing dataset with that id is never overwritten
    (`ConflictError`). Source copies aren't bundled, so `sourcePath` is cleared.
    """
    dataset_id = new_id or dataset.id
    if db.get(DatasetRow, dataset_id) is not None:
        raise ConflictError(f"a dataset with id {dataset_id} already exists")
    versions = []
    written: list[str] = []
    try:
        for version in dataset.versions:
            source = parquet_files.get(version.n)
            if source is None:
                continue
            rel = storage.parquet_rel(dataset_id, version.n)
            with storage.atomic_write(rel) as tmp:
                if move:
                    shutil.move(source, tmp)  # a rename on the same volume
                else:
                    shutil.copyfile(source, tmp)
            written.append(rel)
            versions.append(
                version.model_copy(
                    update={
                        "parquet_path": rel,
                        "source_path": None,
                        "size_bytes": storage.size(rel),
                    }
                )
            )
        if not versions:
            raise InvalidSpecError(f"dataset {dataset.name!r} has no Parquet files in the bundle")
        registered = dataset.model_copy(update={"id": dataset_id, "versions": versions})
        save_dataset(db, registered)
    except BaseException:
        for rel in written:
            storage.resolve(rel).unlink(missing_ok=True)
        raise
    return registered


def sweep_orphans(db: Session, storage: LocalStorage) -> list[str]:
    """Delete `datasets/<id>` and `sources/<id>` directories with no catalog row (left by an
    import that crashed or a process killed mid-import). Run at startup, before jobs."""
    known = set(db.scalars(select(DatasetRow.id)).all())
    removed: list[str] = []
    for parent in (storage.datasets_dir, storage.sources_dir):
        if not parent.is_dir():
            continue
        for child in parent.iterdir():
            if not child.is_dir() or child.name.startswith("_") or child.name in known:
                continue
            shutil.rmtree(child, ignore_errors=True)
            removed.append(storage.rel(child) if child.exists() else child.name)
    if removed:
        log.info("removed %d orphaned dataset directories", len(removed))
    storage.empty_trash()
    return removed
