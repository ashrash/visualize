"""Fixtures for stats tests: Parquet-backed Relations built from a SQL SELECT."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
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


def build_table_relation(
    engine: Engine,
    directory: Path,
    table: pa.Table,
    *,
    where: exp.Expr | None = None,
    semantics: dict[str, ColumnSemantic] | None = None,
) -> Relation:
    """Write an Arrow table to Parquet and wrap it (faster than SQL VALUES for big inputs)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"table {next(_counter)}.parquet"
    pq.write_table(table, path)
    literal = str(path).replace("'", "''")
    return build_relation(
        engine,
        directory,
        f"SELECT * FROM read_parquet('{literal}')",
        where=where,
        semantics=semantics,
    )


@pytest.fixture
def table_relation(engine: Engine, tmp_path: Path) -> Callable[..., Relation]:
    def make(
        table: pa.Table | dict[str, object],
        *,
        where: exp.Expr | None = None,
        semantics: dict[str, ColumnSemantic] | None = None,
    ) -> Relation:
        arrow = table if isinstance(table, pa.Table) else pa.table(table)
        return build_table_relation(
            engine, tmp_path / "tables", arrow, where=where, semantics=semantics
        )

    return make


@pytest.fixture
def relation_builder() -> Callable[..., Relation]:
    """build_relation itself, for tests that bring their own Engine."""
    return build_relation


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
