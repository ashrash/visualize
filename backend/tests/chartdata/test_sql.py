"""Known answers for the SQL building blocks: predicates, literals, identifiers."""

from __future__ import annotations

import math
from typing import Any

import duckdb
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter
from sqlglot import exp

from viz.chartdata.sql import col, lit, predicate_sql, to_sql, where_sql
from viz.contracts import Predicate

_ROWS = (
    "SELECT * FROM (VALUES (1, 'a', 1.5, DATE '2024-01-01'), (2, 'b', NULL, DATE '2024-02-01'),"
    " (3, NULL, 3.0, NULL), (4, 'c', 4.5, DATE '2024-03-01')) AS t(id, k, x, d)"
)
_predicate = TypeAdapter(Predicate)


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    yield connection
    connection.close()


def matching(con: duckdb.DuckDBPyConnection, *raw: dict[str, Any]) -> list[int]:
    condition = where_sql([_predicate.validate_python(p) for p in raw])
    query = exp.select(col("id")).from_(exp.Subquery(this=exp.maybe_parse(_ROWS, dialect="duckdb")))
    if condition is not None:
        query = query.where(condition)
    return sorted(r[0] for r in con.sql(to_sql(query)).fetchall())


@pytest.mark.parametrize(
    ("predicate", "expected"),
    [
        ({"op": "in", "field": "k", "values": ["a", "c"]}, [1, 4]),
        ({"op": "in", "field": "k", "values": ["a", None]}, [1, 3]),
        ({"op": "in", "field": "k", "values": []}, []),
        # not_in keeps NULL rows unless NULL is listed.
        ({"op": "not_in", "field": "k", "values": ["a"]}, [2, 3, 4]),
        ({"op": "not_in", "field": "k", "values": ["a", None]}, [2, 4]),
        ({"op": "not_in", "field": "k", "values": []}, [1, 2, 3, 4]),
        ({"op": "eq", "field": "id", "value": 2}, [2]),
        ({"op": "eq", "field": "k", "value": None}, [3]),
        ({"op": "range", "field": "x", "min": 1.5, "max": 3.0}, [1, 3]),
        ({"op": "range", "field": "x", "min": 1.5, "includeMin": False}, [3, 4]),
        ({"op": "range", "field": "x", "max": 3.0, "includeMax": False}, [1]),
        ({"op": "range", "field": "x"}, [1, 2, 3, 4]),
        ({"op": "between_dates", "field": "d", "min": "2024-01-15", "max": "2024-03-01"}, [2, 4]),
        ({"op": "is_null", "field": "x"}, [2]),
        ({"op": "is_null", "field": "x", "isNull": False}, [1, 3, 4]),
    ],
)
def test_predicates(
    con: duckdb.DuckDBPyConnection, predicate: dict[str, Any], expected: list[int]
) -> None:
    assert matching(con, predicate) == expected


def test_predicates_and_together(con: duckdb.DuckDBPyConnection) -> None:
    assert matching(
        con,
        {"op": "not_in", "field": "k", "values": ["b"]},
        {"op": "range", "field": "id", "min": 2},
    ) == [3, 4]


def test_non_finite_floats_are_values_not_names(con: duckdb.DuckDBPyConnection) -> None:
    rendered = [to_sql(lit(v)) for v in (math.nan, math.inf, -math.inf)]
    assert all("CAST" in r for r in rendered)
    values = con.sql(f"SELECT {', '.join(rendered)}").fetchall()[0]
    assert math.isnan(values[0]) and values[1] == math.inf and values[2] == -math.inf


_SHARED = duckdb.connect()


@settings(deadline=None, max_examples=300)
@given(
    st.one_of(
        st.text(),
        st.integers(min_value=-(2**63), max_value=2**63 - 1),
        st.floats(),
        st.booleans(),
        st.none(),
    )
)
def test_literals_round_trip(value: Any) -> None:
    back = _SHARED.cursor().sql(f"SELECT {to_sql(lit(value))}").fetchall()[0][0]
    if isinstance(value, float) and math.isnan(value):
        assert math.isnan(back)
    else:
        assert back == value
        assert type(back) is type(value) or value is None


@settings(deadline=None, max_examples=300)
@given(st.text(min_size=1).filter(lambda s: "\x00" not in s))
def test_identifiers_round_trip(name: str) -> None:
    rel = _SHARED.cursor().sql(f"SELECT 1 AS {to_sql(col(name))}")
    assert rel.columns == [name]


def test_predicate_column_override() -> None:
    p = _predicate.validate_python({"op": "eq", "field": "K", "value": "a"})
    assert to_sql(predicate_sql(p, col("k"))) == "\"k\" = 'a'"
