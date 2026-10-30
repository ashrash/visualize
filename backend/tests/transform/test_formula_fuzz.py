"""Grammar-based fuzzing of the formula validator.

Formulas are generated from allowlisted pieces, optionally with one forbidden piece spliced
in. The validator must accept exactly the clean ones, and what it accepts must run.
"""

from __future__ import annotations

import contextlib

import duckdb
from hypothesis import given, settings
from hypothesis import strategies as st

from viz.chartdata.sql import to_sql
from viz.transform.formula import FormulaError, validate_formula

COLUMNS = ["n", "m", "s", "d", "b"]
SAMPLE = (
    "SELECT 3.5::DOUBLE AS n, -2::BIGINT AS m, 'ab' AS s, TIMESTAMP '2024-02-29 08:00:00' AS d,"
    " false AS b"
)

numeric_atoms = st.sampled_from(["n", "m", "1", "2.5", "0", '"n"', "(n)", "NULL"])
forbidden = st.sampled_from(
    [
        "(SELECT 1)",
        "getenv('X')",
        "read_csv('x')",
        "random()",
        "now()",
        "'x'.read_csv()",
        "$1",
        "current_setting('threads')",
        "list_value(1)",
        "sum(n)",
        "row_number() OVER ()",
        "repeat('a', 5)",
        "t.n",
    ]
)


def numeric(atoms: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
    def extend(inner: st.SearchStrategy[str]) -> st.SearchStrategy[str]:
        return st.one_of(
            st.tuples(inner, st.sampled_from(["+", "-", "*", "/", "%"]), inner).map(
                lambda t: f"({t[0]} {t[1]} {t[2]})"
            ),
            st.tuples(
                st.sampled_from(["abs", "round", "floor", "ceil", "sign", "sqrt"]), inner
            ).map(lambda t: f"{t[0]}({t[1]})"),
            st.tuples(inner, inner).map(lambda t: f"coalesce({t[0]}, {t[1]})"),
            st.tuples(inner, inner).map(lambda t: f"greatest({t[0]}, {t[1]})"),
            st.tuples(inner, inner, inner).map(
                lambda t: f"CASE WHEN {t[0]} > {t[1]} THEN {t[2]} ELSE 0 END"
            ),
            st.tuples(inner).map(lambda t: f"CAST({t[0]} AS DOUBLE)"),
            st.tuples(inner).map(lambda t: f"length(s) + {t[0]}"),
            st.tuples(inner).map(lambda t: f"year(d) + {t[0]}"),
        )

    return st.recursive(atoms, extend, max_leaves=12)


@settings(deadline=None, max_examples=400)
@given(numeric(numeric_atoms))
def test_clean_formulas_validate_and_run(text: str) -> None:
    validated = validate_formula(text, COLUMNS)
    sql = f"SELECT {to_sql(validated.expression)} FROM ({SAMPLE}) AS t"
    # e.g. sqrt of a negative number is a data error, not a validation gap.
    data_errors = (
        duckdb.OutOfRangeException,
        duckdb.InvalidInputException,
        duckdb.ConversionException,
    )
    with contextlib.suppress(*data_errors):
        duckdb.sql(sql).fetchall()


@settings(deadline=None, max_examples=400)
@given(numeric(st.one_of(numeric_atoms, forbidden)), forbidden)
def test_any_forbidden_piece_is_rejected(text: str, bad: str) -> None:
    candidate = f"({text}) + {bad}"
    try:
        validate_formula(candidate, COLUMNS)
    except FormulaError:
        return
    raise AssertionError(f"accepted a formula containing a forbidden piece: {candidate}")
