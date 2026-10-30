"""Formula allowlist: every documented function works; every attack is rejected."""

from __future__ import annotations

import duckdb
import pytest
import sqlglot
from sqlglot import exp

from viz.chartdata.sql import to_sql
from viz.transform import formula as fm
from viz.transform.formula import FUNCTIONS, FormulaError, validate_formula

COLUMNS = ["n", "s", "d", "b", "Mixed Case", 'quo"te']
_SAMPLE = (
    "SELECT 42.5::DOUBLE AS n, 'a-b 1' AS s, TIMESTAMP '2024-03-15 10:30:00' AS d, true AS b,"
    ' 1 AS "Mixed Case", 2 AS "quo""te"'
)


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    yield connection
    connection.close()


def run(con: duckdb.DuckDBPyConnection, expression: exp.Expr | str) -> object:
    text = expression if isinstance(expression, str) else to_sql(expression)
    # Compare as text: TIMESTAMPTZ results would need pytz to come back as Python objects.
    sql = f"SELECT CAST(({text}) AS VARCHAR) FROM ({_SAMPLE}) AS t"
    return con.sql(sql).fetchall()[0][0]


@pytest.mark.parametrize("doc", FUNCTIONS, ids=lambda d: d.name)
def test_every_documented_function_validates_and_matches_duckdb(
    con: duckdb.DuckDBPyConnection, doc: fm.FunctionDoc
) -> None:
    validated = validate_formula(doc.example, COLUMNS)
    # The regenerated SQL must compute exactly what the user's text computes in DuckDB.
    assert run(con, validated.expression) == run(con, doc.example)


@pytest.mark.parametrize(
    "text",
    [
        "n * 2 + 1",
        "CASE WHEN n > 10 THEN 'big' ELSE 'small' END",
        "s || '!'",
        "n BETWEEN 1 AND 100",
        "s IN ('a', 'b') OR s LIKE 'a%'",
        "s ILIKE 'A%'",
        "CAST(n AS INTEGER)",
        "TRY_CAST(s AS DOUBLE)",
        "n::BIGINT",
        "d + INTERVAL 1 DAY",
        "NOT b AND n IS NOT NULL",
        "n // 3 + n % 3 + n ^ 2",
        "-n",
        '"Mixed Case" + 1',
        '"mixed case" + 1',
        '"quo""te" * 2',
        "DATE '2024-01-01'",
        "n IS DISTINCT FROM 3",
    ],
)
def test_valid_formulas(con: duckdb.DuckDBPyConnection, text: str) -> None:
    validated = validate_formula(text, COLUMNS)
    run(con, validated.expression)  # binds and runs


def test_columns_resolve_case_insensitively_and_are_quoted() -> None:
    validated = validate_formula('"mixed case" + N', COLUMNS)
    assert validated.fields == ("Mixed Case", "n")
    assert to_sql(validated.expression) == '"Mixed Case" + "n"'


def test_unknown_column_suggests_and_points_at_it() -> None:
    with pytest.raises(FormulaError) as info:
        validate_formula("n + sss", ["n", "ss"])
    assert "did you mean 'ss'" in info.value.message
    assert (info.value.start, info.value.end) == (4, 7)


ATTACKS = [
    # table functions and file access
    "read_csv('C:/Windows/win.ini')",
    "read_csv_auto('x.csv')",
    "read_parquet('x.parquet')",
    "read_json('x.json')",
    "read_text('x')",
    "read_blob('x')",
    "glob('*')",
    "'f.csv'.read_csv()",
    "s.read_csv()",
    "n.abs()",
    # system / settings / environment
    "getenv('HOME')",
    "current_setting('memory_limit')",
    "duckdb_settings()",
    "duckdb_extensions()",
    "version()",
    "query('SELECT 1')",
    "query_table('t')",
    "sql_auto_complete('SEL')",
    "load_extension('httpfs')",
    "current_user",
    # statements and injection
    "1; ATTACH 'x.db' AS x",
    "1; DROP TABLE t",
    "n); DROP TABLE t; --",
    "1 /* */; COPY t TO 'x'",
    "PRAGMA version",
    "SET threads = 1",
    "INSTALL httpfs",
    "LOAD httpfs",
    "ATTACH 'x.db'",
    "COPY (SELECT 1) TO 'x.csv'",
    "SELECT 1",
    "(SELECT 1)",
    "n IN (SELECT 1)",
    "EXISTS (SELECT 1)",
    "(SELECT * FROM read_csv('x'))",
    # placeholders, lambdas, json arrows, windows, stars
    "$1",
    "?",
    "x -> x + 1",
    "s ->> '$.a'",
    "list_transform([1, 2], x -> x + 1)",
    "row_number() OVER ()",
    "sum(n) OVER (PARTITION BY s)",
    "*",
    "t.n",
    "main.t.n",
    # nondeterministic and unbounded functions
    "now()",
    "current_date",
    "random()",
    "uuid()",
    "gen_random_uuid()",
    "repeat('a', 1000000000)",
    "range(1000000000)",
    "generate_series(1, 10)",
    "lpad(s, 1000000000, 'x')",
    "list_value(1, 2)",
    "[1, 2, 3]",
    "{'a': 1}",
    "struct_pack(a := 1)",
    # aggregate functions don't belong in row formulas
    "sum(n)",
    "count(*)",
    # casts to odd types and unknown date parts
    "CAST(n AS BLOB)",
    "CAST(s AS STRUCT(a INTEGER))",
    "date_trunc('fortnight', d)",
    # empty and oversized
    "",
    "   ",
    "n +",
    "'unterminated",
    "(" * 3000 + "1" + ")" * 3000,
    "1" + " + 1" * 1500,
    "abs(" * 70 + "n" + ")" * 70,
]


@pytest.mark.parametrize("text", ATTACKS)
def test_attacks_are_rejected(text: str) -> None:
    with pytest.raises(FormulaError):
        validate_formula(text, COLUMNS)


def test_comments_are_stripped() -> None:
    validated = validate_formula("n /* '; DROP TABLE t; -- */ + 1 -- trailing", COLUMNS)
    assert to_sql(validated.expression) == '"n" + 1'


def test_quoted_identifier_tricks_stay_identifiers() -> None:
    names = ['a"; DROP TABLE t; --', "select", "x y"]
    validated = validate_formula('"a""; DROP TABLE t; --" || "select" || "x y"', names)
    sql = to_sql(validated.expression)
    assert sql == '"a""; DROP TABLE t; --" || "select" || "x y"'
    reparsed = sqlglot.parse_one(sql, read="duckdb")
    assert {c.name for c in reparsed.find_all(exp.Column)} == set(names)


def test_accepted_output_reparses_to_allowlisted_nodes_only() -> None:
    for doc in FUNCTIONS:
        sql = to_sql(validate_formula(doc.example, COLUMNS).expression)
        # Re-validating our own output must succeed: it contains only allowlisted nodes.
        validate_formula(sql, COLUMNS)


def test_function_docs_are_unique_and_categorized() -> None:
    names = [d.name for d in FUNCTIONS]
    assert len(names) == len(set(names))
    assert {d.category for d in FUNCTIONS} == {
        "math",
        "string",
        "date",
        "conditional",
        "null",
        "type",
    }
