"""Statistics endpoints. Sync handlers, so DuckDB work runs in the threadpool."""

from __future__ import annotations

from fastapi import APIRouter

from viz.api.deps import DbDep, EngineDep, StorageDep
from viz.contracts import DescribeRequest, StatResult
from viz.stats.descriptive import describe_cached
from viz.stats.source import relation_for

router = APIRouter(prefix="/stats", tags=["stats"])


@router.post("/describe", response_model=StatResult)
def describe_fields(
    body: DescribeRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> StatResult:
    """Exact descriptive statistics; `values.summaries` is a list of FieldSummary."""
    relation = relation_for(
        db, storage, engine, body.source, pipelines=body.pipelines, filters=body.filters
    )
    return describe_cached(relation, body)
