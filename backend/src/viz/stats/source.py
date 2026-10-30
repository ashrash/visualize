"""Relations for stats requests: transform's resolve_source, and data-error mapping."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from sqlalchemy.orm import Session

from viz.contracts import Pipeline, Predicate, SourceRef
from viz.engine import Engine
from viz.errors import InvalidSpecError
from viz.stats.relation import Relation
from viz.storage import LocalStorage
from viz.transform import resolve_source
from viz.transform.runtime import USER_ERRORS, clean_error


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
    """A dataset or pipeline source with `filters` applied (unordered: stats aggregate).

    Raises NotFoundError (404) for a missing dataset or pipeline and InvalidSpecError (422)
    for a failing pipeline step, an unknown filter field or a reference cycle.
    """
    return resolve_source(engine, db, storage, source, pipelines, filters, query_key=query_key)


@contextmanager
def data_errors() -> Iterator[None]:
    """DuckDB errors caused by the spec or the data become a 422 with DuckDB's message.

    Pipeline guards fail at query time (e.g. a calc step's text over 64 KB), as do casts
    of bad values; they are the user's to fix, not server errors.
    """
    try:
        yield
    except USER_ERRORS as exc:
        raise InvalidSpecError(clean_error(exc)) from exc
