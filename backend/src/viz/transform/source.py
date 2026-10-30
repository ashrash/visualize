"""resolve_source: a SourceRef (dataset or pipeline) -> a filtered Relation.

Used by chartdata, stats (describe) and data-core (exports).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy.orm import Session
from sqlglot import exp

from viz.chartdata.sql import col, parquet_source, predicate_sql, to_sql
from viz.contracts import Column, Pipeline, Predicate, SourceRef
from viz.engine import Engine
from viz.ingest.catalog import get_dataset
from viz.storage import LocalStorage
from viz.transform.compiler import CompiledPipeline, Compiler, sha, visible
from viz.transform.relation import Relation


def make_compiler(
    engine: Engine, db: Session, storage: LocalStorage, pipelines: Iterable[Pipeline] = ()
) -> Compiler:
    return Compiler(engine, storage, lambda dataset_id: get_dataset(db, dataset_id), pipelines)


def resolve_source(
    engine: Engine,
    db: Session,
    storage: LocalStorage,
    ref: SourceRef,
    pipelines: Sequence[Pipeline] = (),
    filters: Sequence[Predicate] = (),
    *,
    query_key: str | None = None,
    generation: int | None = None,
    ordered: bool = False,
) -> Relation:
    """Resolve `ref` and apply `filters`.

    - Datasets read their current Parquet version directly.
    - Pipelines compile to a subquery. `ordered=True` keeps the pipeline's row order (sort
      steps, file order), for previews and exports; leave it off for aggregations.
    - Raises NotFoundError for a missing dataset or pipeline, InvalidSpecError for a pipeline
      step error (naming the step), an unknown filter field, or a reference cycle.
    """
    compiler = make_compiler(engine, db, storage, pipelines)
    return relation_for(compiler, ref, filters, query_key=query_key, generation=generation,
                        ordered=ordered)


def relation_for(
    compiler: Compiler,
    ref: SourceRef,
    filters: Sequence[Predicate] = (),
    *,
    query_key: str | None = None,
    generation: int | None = None,
    ordered: bool = False,
) -> Relation:
    if ref.kind == "dataset":
        dataset = compiler.dataset(ref.id)
        version = dataset.current()
        source: exp.Expr = parquet_source(compiler.storage.resolve(version.parquet_path))
        columns = {c.name: c for c in version.columns}
        row_count: int | None = version.row_count
        version_key = f"dataset:{dataset.id}:v{version.n}"
    else:
        compiled = compiler.compile(compiler.pipeline(ref.id))
        source, columns, version_key = pipeline_source(compiled, ordered=ordered)
        row_count = None
    relation = Relation(
        engine=compiler.engine,
        source=source,
        where=None,
        columns=columns,
        row_count=row_count,
        version_key=version_key,
        query_key=query_key,
        generation=generation,
    )
    return with_filters(relation, filters)


def pipeline_source(
    compiled: CompiledPipeline, *, ordered: bool, step_id: str | None = None
) -> tuple[exp.Expr, dict[str, Column], str]:
    _, fields = compiled.output_cte(step_id)
    body = compiled.body(step_id)
    columns = {
        f.name: Column(name=f.name, type=f.type, semantic=f.semantic, null_count=0, approx_distinct=0)
        for f in fields
    }
    return visible(body, ordered_rows=ordered, alias_name="__viz_src"), columns, (
        f"pipeline:{sha(to_sql(body))}"
    )


def with_filters(relation: Relation, filters: Sequence[Predicate]) -> Relation:
    """`relation` with `filters` ANDed into its WHERE (field names checked and resolved)."""
    if not filters:
        return relation
    parts: list[exp.Expr] = []
    if relation.where is not None:
        parts.append(relation.where)
    for predicate in filters:
        column = relation.field(predicate.field)
        parts.append(exp.Paren(this=predicate_sql(predicate, col(column.name))))
    return Relation(
        engine=relation.engine,
        source=relation.source,
        where=exp.and_(*parts),
        columns=relation.columns,
        row_count=relation.row_count,
        version_key=relation.version_key,
        query_key=relation.query_key,
        generation=relation.generation,
    )
