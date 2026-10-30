"""Dataset catalog, stored in the metadata DB."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from viz.contracts import Dataset
from viz.errors import InvalidSpecError, NotFoundError
from viz.meta.models import DatasetRow, ProjectRow, utcnow
from viz.storage import LocalStorage

__all__ = [
    "DatasetRow",
    "delete_dataset_row",
    "get_dataset",
    "list_datasets",
    "projects_using",
    "register_dataset_bundle",
    "save_dataset",
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


def projects_using(db: Session, dataset_id: str) -> list[str]:
    """Names of projects whose `datasetIds` include `dataset_id`."""
    names: list[str] = []
    for row in db.scalars(select(ProjectRow)).all():
        ids = (row.doc or {}).get("datasetIds") or []
        if dataset_id in ids:
            names.append(row.name)
    return names


def register_dataset_bundle(
    db: Session,
    storage: LocalStorage,
    dataset: Dataset,
    parquet_files: dict[int, Path],
    *,
    new_id: str | None = None,
) -> Dataset:
    """Register a dataset from a project bundle (.vizproj import).

    Copies each `parquet_files[n]` to `datasets/<id>/v<n>.parquet`, keeps only versions that
    came with a file, rewrites their `parquetPath`, and saves the catalog row (under `new_id`
    when given). Source copies aren't bundled, so `sourcePath` is cleared.
    """
    dataset_id = new_id or dataset.id
    versions = []
    for version in dataset.versions:
        source = parquet_files.get(version.n)
        if source is None:
            continue
        rel = storage.parquet_rel(dataset_id, version.n)
        with storage.atomic_write(rel) as tmp:
            shutil.copyfile(source, tmp)
        versions.append(
            version.model_copy(
                update={"parquet_path": rel, "source_path": None, "size_bytes": storage.size(rel)}
            )
        )
    if not versions:
        raise InvalidSpecError(f"dataset {dataset.name!r} has no Parquet files in the bundle")
    registered = dataset.model_copy(update={"id": dataset_id, "versions": versions})
    save_dataset(db, registered)
    return registered
