"""Wave 4 grid perf pass: accent-insensitive search forms and late-materialized pages.

Both search forms (strip_accents for dictionary columns, an RE2 pattern for the rest) must match
exactly the rows `contains(strip_accents(lower(col)), strip_accents(lower(needle)))` matches,
and a late-materialized page must equal the direct `SELECT ... ORDER BY ... LIMIT` page.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from viz.contracts import Dataset, RowsRequest
from viz.ingest.rows import rows_page
from viz.ingest.search import accent_pattern, dictionary_columns, fold

WORDS = [
    "Zürich",
    "ZURICH",
    "Zürich",  # decomposed u + combining diaeresis
    "Montréal",
    "MONTREAL",
    "São Paulo",
    "Ångström",
    "Łódź",
    "Œuvre",
    "straße",
    "Oslo",
    "naïve café",
    "a.b(c)*d",
    "it's",
    "back\\slash",
    "Ελλάδα",
    "",
    "Crème brûlée",
]
NEEDLES = [
    "zur",
    "ZÜR",
    "montre",
    "MONTRÉ",
    "sao p",
    "angst",
    "lodz",
    "łódź",
    "œu",
    "ß",
    "ve ca",
    ".",
    "b(c)*",
    "'",
    "\\",
    "ελλαδα",
    "brulee",
    "x",
    "é",
]


def _con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.register("words", pa.table({"w": pa.array(WORDS)}))
    return con


def _matches(con: duckdb.DuckDBPyConnection, where: str, params: list[object]) -> list[str]:
    rows = con.execute(f"SELECT w FROM words WHERE {where} ORDER BY w", params).fetchall()
    return [r[0] for r in rows]


@pytest.mark.parametrize("needle", NEEDLES)
def test_pattern_matches_what_strip_accents_matches(needle: str) -> None:
    con = _con()
    expected = _matches(con, "contains(strip_accents(lower(w)), strip_accents(lower(?)))", [needle])
    got = _matches(con, "regexp_matches(w, ?)", [accent_pattern(needle)])
    assert got == expected


def test_pattern_finds_the_motivating_cases() -> None:
    con = _con()
    assert "Zürich" in _matches(con, "regexp_matches(w, ?)", [accent_pattern("zur")])
    assert "Montréal" in _matches(con, "regexp_matches(w, ?)", [accent_pattern("montre")])
    # Metacharacters are literal.
    assert _matches(con, "regexp_matches(w, ?)", [accent_pattern(".")]) == ["a.b(c)*d"]


def test_fold_mirrors_duckdb() -> None:
    con = _con()
    for word in WORDS:
        row = con.execute("SELECT strip_accents(lower(?))", [word]).fetchone()
        assert row is not None and fold(word) == row[0], word


def test_dictionary_columns_reads_the_layout(tmp_path: Path) -> None:
    path = tmp_path / "layout.parquet"
    con = duckdb.connect()
    con.sql(
        "SELECT 'cat_' || (i % 5) AS low, md5(i::VARCHAR) AS high FROM range(20000) t(i)"
    ).write_parquet(str(path))
    assert dictionary_columns(path) == {"low": True, "high": False}
    assert dictionary_columns(tmp_path / "missing.parquet") == {}


# -- late materialization ------------------------------------------------------------------


@pytest.fixture
def big(
    client: TestClient, write: Callable[[str, bytes | str], Path], run_import: Callable[..., Any]
) -> Dataset:
    """3,000 rows with ties, NULLs, accented text and a high-cardinality text column."""
    lines = ["id,grp,score,name,note"]
    cities = ["Zürich", "Oslo", "Montréal", "", "Bern"]
    for i in range(3000):
        score = "" if i % 17 == 0 else str((i * 7919) % 101)
        lines.append(f"{i},{cities[i % 5]},{score},n{i % 13},note é{(i * 31) % 2999}")
    path = write("big.csv", "\n".join(lines) + "\n")
    return Dataset.model_validate(run_import(path=str(path)))


REQUESTS: list[dict[str, Any]] = [
    {"sort": [{"field": "score", "descending": True}]},
    {"sort": [{"field": "score", "nullsLast": False}], "offset": 1500, "limit": 50},
    {"sort": [{"field": "grp"}, {"field": "score", "descending": True}], "offset": 7},
    {"sort": [{"field": "name", "descending": True}], "columns": ["note", "id"], "offset": 2990},
    {"filters": [{"op": "range", "field": "score", "min": 10, "max": 20}], "offset": 40},
    {"filters": [{"op": "is_null", "field": "score"}]},
    {"search": "zur", "limit": 30, "offset": 100},
    {"search": "E1", "sort": [{"field": "id", "descending": True}]},
    {"search": "nothing-matches"},
    {"search": "note e29", "filters": [{"op": "eq", "field": "grp", "value": "Oslo"}]},
    {"filters": [{"op": "eq", "field": "grp", "value": "Bern"}], "offset": 5000},
]


@pytest.mark.parametrize("body", REQUESTS)
def test_late_pages_equal_direct_pages(client: TestClient, big: Dataset, body: Any) -> None:
    engine = client.app.state.engine  # type: ignore[attr-defined]
    storage = client.app.state.storage  # type: ignore[attr-defined]
    request = RowsRequest.model_validate({"limit": 200, **body})
    direct, direct_meta = rows_page(
        engine, storage, big, request, max_limit=1000, late_min_rows=10**12
    )
    # Twice: the second late page takes the filtered count from the cache.
    for _ in range(2):
        late, late_meta = rows_page(engine, storage, big, request, max_limit=1000, late_min_rows=0)
        assert late.schema.equals(direct.schema)
        assert late.to_pylist() == direct.to_pylist()
        assert late_meta == direct_meta


def test_search_counts_are_accent_insensitive_on_both_paths(
    client: TestClient, big: Dataset
) -> None:
    engine = client.app.state.engine  # type: ignore[attr-defined]
    storage = client.app.state.storage  # type: ignore[attr-defined]
    for late_min_rows, needle in itertools.product((0, 10**12), ("ZUR", "zür", "NOTE E")):
        _, meta = rows_page(
            engine,
            storage,
            big,
            RowsRequest(search=needle, limit=5),
            max_limit=1000,
            late_min_rows=late_min_rows,
        )
        assert meta.total_rows == (600 if "z" in needle.lower() else 3000), needle


def test_high_cardinality_text_uses_the_pattern(client: TestClient, big: Dataset) -> None:
    storage = client.app.state.storage  # type: ignore[attr-defined]
    path = storage.resolve(big.current().parquet_path)
    layout = dictionary_columns(path)
    meta = pq.read_metadata(path)
    assert set(layout) == {meta.schema.column(i).name for i in range(meta.num_columns)}
    # So the tests above exercise both forms: strip_accents on grp, the pattern on note.
    assert layout["grp"] is True
    assert layout["note"] is False
