"""Build a Relation for a stats request.

TODO(transform): replace with transform's resolve_source (SourceRef + pipelines + filters),
which also handles pipeline sources. Until then only dataset sources are supported.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from viz.chartdata.sql import parquet_source, where_sql
from viz.contracts import Pipeline, Predicate, SourceRef
from viz.engine import Engine
from viz.errors import NotImplementedYetError
from viz.ingest.catalog import get_dataset
from viz.stats.errors import StatsInputError
from viz.stats.relation import Relation
from viz.storage import LocalStorage


def relation_for(
    db: Session,
    storage: LocalStorage,
    engine: Engine,
    source: SourceRef,
    *,
    pipelines: Sequence[Pipeline] = (),
    filters: Sequence[Predicate] = (),
    query_key: str | None = None,
) -> Relation:
    if source.kind != "dataset":
        raise NotImplementedYetError("Statistics on pipeline outputs are not available yet.")
    dataset = get_dataset(db, source.id)
    version = dataset.current()
    columns = {c.name: c for c in version.columns}
    unknown = sorted({p.field for p in filters} - columns.keys())
    if unknown:
        raise StatsInputError(f"Unknown filter field(s): {', '.join(unknown)}.")
    return Relation(
        engine=engine,
        source=parquet_source(storage.resolve(version.parquet_path)),
        where=where_sql(list(filters)),
        columns=columns,
        row_count=version.row_count,
        version_key=f"{dataset.id}@v{version.n}",
        query_key=query_key,
    )
