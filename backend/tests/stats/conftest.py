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


# ---------------------------------------------------------------- published datasets

# R `sleep` (Student 1908): extra hours of sleep for 10 patients under two drugs.
SLEEP_1 = [0.7, -1.6, -0.2, -1.2, -0.1, 3.4, 3.7, 0.8, 0.0, 2.0]
SLEEP_2 = [1.9, 0.8, 1.1, 0.1, -0.1, 4.4, 5.5, 1.6, 4.6, 3.4]

# R `PlantGrowth`: dried plant weight under a control and two treatments.
PLANT_CTRL = [4.17, 5.58, 5.18, 6.11, 4.50, 4.61, 5.17, 4.53, 5.33, 5.14]
PLANT_TRT1 = [4.81, 4.17, 4.41, 3.59, 5.87, 3.83, 6.03, 4.89, 4.32, 4.69]
PLANT_TRT2 = [6.31, 5.12, 5.54, 5.50, 5.37, 5.29, 4.92, 6.15, 5.80, 5.26]


@pytest.fixture
def sleep_long() -> pa.Table:
    return pa.table(
        {
            "extra": SLEEP_1 + SLEEP_2,
            "group": ["1"] * 10 + ["2"] * 10,
            "drug": [1] * 10 + [2] * 10,
            "id": list(range(1, 11)) * 2,
        }
    )


@pytest.fixture
def sleep_wide() -> pa.Table:
    return pa.table({"drug1": SLEEP_1, "drug2": SLEEP_2})


@pytest.fixture
def plant_growth() -> pa.Table:
    return pa.table(
        {
            "weight": PLANT_CTRL + PLANT_TRT1 + PLANT_TRT2,
            "group": ["ctrl"] * 10 + ["trt1"] * 10 + ["trt2"] * 10,
        }
    )
