"""Dataset catalog, stored in the metadata DB."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from viz.contracts import Dataset
from viz.errors import NotFoundError
from viz.meta.models import DatasetRow, utcnow


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
