"""Fixtures for stats tests: Parquet-backed Relations built from a SQL SELECT."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlglot import exp

from viz.contracts import Column, ColumnSemantic
from viz.engine import Engine
from viz.stats.relation import Relation

MakeRelation = Callable[..., Relation]

_NUMERIC = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT")
_NUMERIC += ("UINTEGER", "UBIGINT", "FLOAT", "DOUBLE", "DECIMAL")


def _semantic(duck_type: str) -> ColumnSemantic:
    upper = duck_type.upper()
    if upper.startswith(("DATE", "TIMESTAMP", "TIME")):
        return ColumnSemantic.TEMPORAL
    if upper.startswith(_NUMERIC):
        return ColumnSemantic.QUANTITATIVE
    return ColumnSemantic.NOMINAL


def build_relation(
    engine: Engine,
    directory: Path,
    select_sql: str,
    *,
    where: exp.Expr | None = None,
    semantics: dict[str, ColumnSemantic] | None = None,
) -> Relation:
    """Write `select_sql` to Parquet (path with a space and non-ASCII) and wrap it."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"data é {next(_counter)}.parquet"
    literal = str(path).replace("'", "''")
    with engine.cursor() as cur:
        cur.execute(f"COPY ({select_sql}) TO '{literal}' (FORMAT parquet)")
    with engine.cursor() as cur:
        described = cur.sql(f"DESCRIBE SELECT * FROM read_parquet('{literal}')").fetchall()
    with engine.cursor() as cur:
        rows = cur.sql(f"SELECT count(*) FROM read_parquet('{literal}')").fetchall()
    columns = {}
    for name, duck_type, *_ in described:
        semantic = (semantics or {}).get(name, _semantic(duck_type))
        columns[name] = Column(
            name=name, type=duck_type, semantic=semantic, null_count=0, approx_distinct=0
        )
    source = exp.Anonymous(this="read_parquet", expressions=[exp.Literal.string(str(path))])
    return Relation(
        engine=engine,
        source=source,
        where=where,
        columns=columns,
        row_count=int(rows[0][0]),
        version_key=f"test-{path.stem}@v1",
    )


_counter = itertools.count()


@pytest.fixture
def make_relation(engine: Engine, tmp_path: Path) -> MakeRelation:
    def make(
        select_sql: str,
        *,
        where: exp.Expr | None = None,
        semantics: dict[str, ColumnSemantic] | None = None,
    ) -> Relation:
        return build_relation(
            engine, tmp_path / "rel", select_sql, where=where, semantics=semantics
        )

    return make
