"""Wave 4 grid perf pass: accent-insensitive search forms and late-materialized pages.

Both search forms (strip_accents for dictionary columns, an RE2 pattern for the rest) must match
exactly the rows `contains(strip_accents(lower(col)), strip_accents(lower(needle)))` matches,
and a late-materialized page must equal the direct `SELECT ... ORDER BY ... LIMIT` page.
"""

from __future__ import annotations

import itertools
import time
import unicodedata
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
from viz.ingest.search import (
    MAX_PATTERN_NEEDLE,
    accent_pattern,
    contains_folded,
    dictionary_columns,
    fold,
    pattern_supported,
)

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


def test_fold_mirrors_duckdb_on_every_pattern_safe_code_point() -> None:
    chars = [
        chr(cp)
        for cp in [*range(0x20, 0x530), *range(0x1E00, 0x2000)]
        if not unicodedata.category(chr(cp)).startswith("C")
    ]
    row = (
        duckdb.connect()
        .execute("SELECT list_transform(?, lambda x: strip_accents(lower(x)))", [chars])
        .fetchone()
    )
    assert row is not None
    wrong = [(hex(ord(c)), fold(c), d) for c, d in zip(chars, row[0], strict=True) if fold(c) != d]
    assert wrong == []


MIXED_WORDS = [
    "서울특별시 강남구",
    "부산광역시",
    "हिंदी भाषा",
    "தமிழ்நாடு",
    "ภาษาไทย",
    "العربية",
    "שָׁלוֹם",
    "Thành phố Hồ Chí Minh",
    "東京都",
    "とうきょう カタカナ",
    "Zürich",
]
MIXED_NEEDLES = [
    "서울",
    "부산광역시",
    "हिंदी",
    "भाषा",
    "தமிழ்",
    "ไทย",
    "عربية",
    "שלום",
    "שָׁלוֹם",
    "ho chi",
    "Hồ Chí",
    "東京",
    "とうきょう",
    "カタカナ",
    "zur",
]


def _contains_sql(needle: str, *, dictionary: bool) -> str:
    return contains_folded("w", needle, dictionary=dictionary).sql(dialect="duckdb")


@pytest.mark.parametrize("needle", MIXED_NEEDLES)
def test_other_scripts_route_to_strip_accents_and_match(needle: str) -> None:
    con = duckdb.connect()
    con.register("words", pa.table({"w": pa.array(MIXED_WORDS)}))
    latin_greek_cyrillic = all(ord(c) < 0x530 or 0x1E00 <= ord(c) <= 0x1FFF for c in needle)
    assert pattern_supported(needle) == latin_greek_cyrillic
    if not latin_greek_cyrillic:
        assert "regexp_matches" not in _contains_sql(needle, dictionary=False).lower()
    expected = _matches(con, _contains_sql(needle, dictionary=True), [])
    assert expected, needle  # every needle is a substring of some word
    assert _matches(con, _contains_sql(needle, dictionary=False), []) == expected


def test_pattern_matches_marks_beyond_combining_diacriticals() -> None:
    """`_MARKS` covers more than U+0300-U+036F (Combining Diacritical Marks): Cyrillic titlo
    and similar marks appear in real text (Church Slavonic) after a pattern-safe letter."""
    con = duckdb.connect()
    titlo = "҃"  # Cyrillic titlo, category Mn, outside U+0300-U+036F
    word = "а" + titlo + "б"  # Cyrillic "аб" with a titlo over the а
    con.register("words", pa.table({"w": pa.array([word])}))
    assert _matches(con, _contains_sql("аб", dictionary=True), []) == [word]
    assert _matches(con, _contains_sql("аб", dictionary=False), []) == [word]


def test_known_differences_of_the_pattern() -> None:
    """Documented in `accent_pattern`; accepted, not bugs."""
    con = duckdb.connect()
    long_s = chr(0x17F) + "un"
    con.register("words", pa.table({"w": pa.array([long_s, "µm", "1́2"])}))
    strip = _contains_sql
    assert _matches(con, strip("su", dictionary=True), []) == []
    assert _matches(con, strip("su", dictionary=False), []) == [long_s]
    assert _matches(con, strip("μm", dictionary=True), []) == []
    assert _matches(con, strip("μm", dictionary=False), []) == ["µm"]
    assert _matches(con, strip("12", dictionary=True), []) == ["1́2"]
    assert _matches(con, strip("12", dictionary=False), []) == []


def test_pattern_cost_is_bounded_at_the_needle_cap() -> None:
    """`\\p{Mn}*` after each letter pushed RE2 out of its DFA mode above ~40 letters (a
    256-letter needle took ~30-60 s per 1M rows). The narrow mark class keeps it near the
    strip_accents cost."""
    # "o" has the largest variant class (68 accented forms), so this is the actual worst case,
    # not just a representative one.
    needle = "o" * MAX_PATTERN_NEEDLE
    assert pattern_supported(needle)
    assert len(accent_pattern(needle)) <= 700 * MAX_PATTERN_NEEDLE
    con = duckdb.connect()
    con.sql(
        "CREATE TABLE words AS SELECT 'Crème ' || md5(i::VARCHAR) || ' brûlée' AS w "
        "FROM range(100000) t(i)"
    )

    def timed(sql: str) -> float:
        started = time.perf_counter()
        con.execute(f"SELECT count(*) FROM words WHERE {sql}").fetchall()
        return time.perf_counter() - started

    timed(_contains_sql(needle, dictionary=False))  # compile and warm up
    pattern_s = min(timed(_contains_sql(needle, dictionary=False)) for _ in range(3))
    strip_s = min(timed(_contains_sql(needle, dictionary=True)) for _ in range(3))
    assert pattern_s <= max(10 * strip_s, 0.5), (pattern_s, strip_s)


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


def test_a_file_row_number_column_skips_late_pages(
    client: TestClient, write: Callable[[str, bytes | str], Path], run_import: Callable[..., Any]
) -> None:
    """read_parquet(file_row_number := true) can't bind next to a real column of that name, so
    such datasets keep the direct path (filtered and searched pages; sorted ones are a
    pre-existing limitation)."""
    lines = ["File_Row_Number,city"] + [f"{i},{'Zürich' if i % 2 else 'Oslo'}" for i in range(50)]
    path = write("rownum.csv", "\n".join(lines) + "\n")
    dataset = Dataset.model_validate(run_import(path=str(path)))
    engine = client.app.state.engine  # type: ignore[attr-defined]
    storage = client.app.state.storage  # type: ignore[attr-defined]
    for body in (
        {"search": "zur"},
        {"filters": [{"op": "eq", "field": "city", "value": "Oslo"}], "offset": 3},
    ):
        request = RowsRequest.model_validate({"limit": 10, **body})
        table, meta = rows_page(engine, storage, dataset, request, max_limit=100, late_min_rows=0)
        assert meta.total_rows == 25 and table.num_rows == 10


def test_a_file_row_number_column_skips_materialized_views(
    client: TestClient, write: Callable[[str, bytes | str], Path], run_import: Callable[..., Any]
) -> None:
    """The same file_row_number collision breaks a materialized view's own
    read_parquet(file_row_number := true) read, once one is built. RowViews must not offer to
    build or serve a view for such a dataset; the direct path is always correct."""
    from viz.ingest.views import RowViews

    lines = ["file_row_number,city"] + [f"{i},{'Zürich' if i % 2 else 'Oslo'}" for i in range(50)]
    path = write("rownum_views.csv", "\n".join(lines) + "\n")
    dataset = Dataset.model_validate(run_import(path=str(path)))
    engine = client.app.state.engine  # type: ignore[attr-defined]
    storage = client.app.state.storage  # type: ignore[attr-defined]
    views = RowViews(engine, storage, min_rows=0)
    request = RowsRequest.model_validate({"search": "zur", "limit": 10})
    version = dataset.versions[-1]
    assert views._eligible(version, request) is False
    table, meta = rows_page(
        engine, storage, dataset, request, max_limit=100, late_min_rows=0, views=views
    )
    assert meta.total_rows == 25 and table.num_rows == 10
