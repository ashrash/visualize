"""resolve_source: a SourceRef (dataset or pipeline) -> a filtered Relation.

Used by chartdata, stats (describe) and data-core (exports).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from sqlalchemy.orm import Session
from sqlglot import exp

from viz.chartdata.sql import RN, col, parquet_source, predicate_sql, star_except, subquery, to_sql
from viz.contracts import Column, Pipeline, Predicate, SourceRef
from viz.engine import Engine
from viz.errors import InvalidSpecError
from viz.ingest.catalog import get_dataset
from viz.stats.relation import Relation
from viz.storage import LocalStorage
from viz.transform.compiler import CompiledPipeline, Compiler, sha


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
    - Pipelines compile to a query (`pipelines` holds every pipeline `ref` reaches, including
      unsaved edits). `ordered=True` keeps the pipeline's row order (sort steps, then file
      order), for previews and exports; leave it off for aggregations.
    - Filter field names match case-insensitively, as in DuckDB.
    - Raises NotFoundError for a missing dataset or pipeline, and InvalidSpecError for a
      pipeline step error (naming the step), an unknown filter field or a reference cycle.
    """
    compiler = make_compiler(engine, db, storage, pipelines)
    return relation_for(
        compiler, ref, filters, query_key=query_key, generation=generation, ordered=ordered
    )


def relation_for(
    compiler: Compiler,
    ref: SourceRef,
    filters: Sequence[Predicate] = (),
    *,
    query_key: str | None = None,
    generation: int | None = None,
    ordered: bool = False,
) -> Relation:
    source: exp.Expr
    if ref.kind == "dataset":
        dataset = compiler.dataset(ref.id)
        version = dataset.current()
        source = parquet_source(compiler.storage.resolve(version.parquet_path))
        columns: dict[str, Column] = {c.name: c for c in version.columns}
        row_count: int | None = version.row_count
        version_key = f"{dataset.id}@v{version.n}"
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
        # None inside a request scope: the engine uses the request's arrival ticket.
        generation=generation,
    )
    return with_filters(relation, filters)


def pipeline_source(
    compiled: CompiledPipeline, *, ordered: bool, step_id: str | None = None
) -> tuple[exp.Query, dict[str, Column], str]:
    """The pipeline's output after `step_id` (None = final) without the hidden RN column.

    Pipeline columns carry no profile, so null_count and approx_distinct are 0 (unknown).
    """
    _, fields = compiled.output_cte(step_id)
    body = compiled.body(step_id)
    inner = "__viz_body"
    query = exp.select(star_except(RN)).from_(subquery(body, inner))
    if ordered:
        query = query.order_by(col(RN, inner))
    columns = {
        f.name: Column(
            name=f.name, type=f.type, semantic=f.semantic, null_count=0, approx_distinct=0
        )
        for f in fields
    }
    return query, columns, f"pipeline:{compiled.pipeline.id}:{sha(to_sql(body))[:32]}"


def resolve_field(columns: Mapping[str, Column], name: str) -> str:
    """The relation's spelling of `name` (case-insensitive, like DuckDB)."""
    if name in columns:
        return name
    lowered = name.lower()
    for actual in columns:
        if actual.lower() == lowered:
            return actual
    raise InvalidSpecError(f"unknown field {name!r}")


def filter_condition(
    columns: Mapping[str, Column], filters: Sequence[Predicate]
) -> exp.Expr | None:
    if not filters:
        return None
    parts: list[exp.Expr] = []
    for predicate in filters:
        column = columns[resolve_field(columns, predicate.field)]
        condition = predicate_sql(predicate, col(column.name), duck_type=column.type)
        parts.append(exp.Paren(this=condition))
    return exp.and_(*parts)


def with_filters(relation: Relation, filters: Sequence[Predicate]) -> Relation:
    """`relation` with `filters` ANDed into its WHERE (field names checked and resolved)."""
    condition = filter_condition(relation.columns, filters)
    return relation if condition is None else relation.narrow(condition)
