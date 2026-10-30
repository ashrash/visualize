"""Fixtures for plot-module unit tests: Parquet-backed Relations built from an Arrow table.

A small, self-contained copy of `tests/stats/conftest.py`'s `table_relation` (that module is
owned by the stats agent and its conftest is scoped to `tests/stats/`, so it isn't visible here).
"""

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

_NUMERIC: tuple[str, ...] = (
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "FLOAT",
    "DOUBLE",
    "DECIMAL",
)
_counter = itertools.count()


def _semantic(duck_type: str) -> ColumnSemantic:
    upper = duck_type.upper()
    if upper.startswith(("DATE", "TIMESTAMP", "TIME")):
        return ColumnSemantic.TEMPORAL
    if upper.startswith(_NUMERIC):
        return ColumnSemantic.QUANTITATIVE
    return ColumnSemantic.NOMINAL


def build_table_relation(
    engine: Engine,
    directory: Path,
    table: pa.Table,
    *,
    where: exp.Expr | None = None,
    semantics: dict[str, ColumnSemantic] | None = None,
) -> Relation:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"table {next(_counter)}.parquet"
    pq.write_table(table, path)
    literal = str(path).replace("'", "''")
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
