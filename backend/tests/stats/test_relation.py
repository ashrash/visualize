from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import duckdb
import pytest
from sqlglot import exp

from viz.contracts import ColumnSemantic
from viz.engine import Engine, next_generation
from viz.errors import QueryInterruptedError
from viz.stats.errors import StatsInputError
from viz.stats.relation import Relation, col

pytestmark = pytest.mark.usefixtures("engine")

_DATA = """
SELECT * FROM (VALUES
    (1, 'a', 10.0), (2, 'a', 20.0), (3, 'b', 30.0), (4, 'b', NULL), (5, 'c', 50.0)
) t(id, grp, "odd ""name""; drop")
"""


def test_select_applies_the_filters(make_relation: Any) -> None:
    rel: Relation = make_relation(
        _DATA, where=exp.GT(this=col("id"), expression=exp.Literal.number(2))
    )
    table = rel.arrow(rel.select(col("id")).order_by(col("id")))
    assert table.column("id").to_pylist() == [3, 4, 5]
    assert rel.count() == 3


def test_narrow_ands_another_filter(make_relation: Any) -> None:
    rel: Relation = make_relation(
        _DATA, where=exp.GT(this=col("id"), expression=exp.Literal.number(1))
    )
    narrowed = rel.narrow(exp.EQ(this=col("grp"), expression=exp.Literal.string("b")))
    assert narrowed.count() == 2
    assert rel.count() == 4  # the original is unchanged


def test_narrow_without_existing_filter(make_relation: Any) -> None:
    rel: Relation = make_relation(_DATA)
    assert rel.narrow(exp.EQ(this=col("grp"), expression=exp.Literal.string("a"))).count() == 2


def test_identifiers_are_quoted(make_relation: Any) -> None:
    rel: Relation = make_relation(_DATA)
    odd = 'odd "name"; drop'
    table = rel.arrow(rel.filtered(odd))
    assert table.column_names == [odd]
    assert table.num_rows == 5


def test_query_source_is_wrapped_as_subquery(make_relation: Any) -> None:
    base: Relation = make_relation(_DATA)
    pipeline_sql = (
        exp.select(col("id"), col("grp"))
        .from_(base.source)
        .where(exp.LT(this=col("id"), expression=exp.Literal.number(5)))
    )
    rel = Relation(
        engine=base.engine,
        source=pipeline_sql,
        where=exp.EQ(this=col("grp"), expression=exp.Literal.string("b")),
        columns={k: v for k, v in base.columns.items() if k in ("id", "grp")},
        row_count=None,
        version_key="p@1",
    )
    assert rel.arrow(rel.filtered("id")).column("id").to_pylist() == [3, 4]
    assert rel.rows(rel.select(exp.Count(this=exp.Star()))) == [(2,)]


def test_require_checks_field_and_semantic(make_relation: Any) -> None:
    rel: Relation = make_relation(_DATA)
    assert rel.require("id").name == "id"
    assert rel.require("id", frozenset({ColumnSemantic.QUANTITATIVE})).semantic == "quantitative"
    with pytest.raises(StatsInputError, match="Unknown field 'nope'"):
        rel.require("nope")
    with pytest.raises(StatsInputError, match="'grp' must be a numeric field; it is categorical"):
        rel.require("grp", frozenset({ColumnSemantic.QUANTITATIVE}))
    assert StatsInputError("x").status_code == 422


class _RecordingEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self._con = duckdb.connect()

    @contextmanager
    def cursor(self, *args: object, **kwargs: object) -> Iterator[duckdb.DuckDBPyConnection]:
        self.calls.append((args, kwargs))
        cur = self._con.cursor()
        try:
            yield cur
        finally:
            cur.close()


@pytest.mark.parametrize(
    ("generation", "expected_kwargs"), [(None, {"generation": None}), (7, {"generation": 7})]
)
def test_cursor_passes_key_and_generation(
    generation: int | None, expected_kwargs: dict[str, object]
) -> None:
    engine = _RecordingEngine()
    rel = Relation(
        engine=engine,  # type: ignore[arg-type]
        source=exp.Anonymous(this="range", expressions=[exp.Literal.number(3)]),
        where=None,
        columns={},
        row_count=3,
        version_key="r@1",
        query_key="chart:1",
        generation=generation,
    )
    assert rel.count() == 3
    assert engine.calls == [(("chart:1",), expected_kwargs)]


def test_stale_generations_are_refused(engine: Engine) -> None:
    def relation(generation: int) -> Relation:
        return Relation(
            engine=engine,
            source=exp.Anonymous(this="range", expressions=[exp.Literal.number(3)]),
            where=None,
            columns={},
            row_count=3,
            version_key="r@1",
            query_key="chart:gen",
            generation=generation,
        )

    older, newer = next_generation(), next_generation()
    assert relation(newer).count() == 3
    assert relation(newer).count() == 3  # several queries of one request share a generation
    with pytest.raises(QueryInterruptedError, match="superseded"):
        relation(older).count()
