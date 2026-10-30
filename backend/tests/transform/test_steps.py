"""Known-answer tests for every pipeline step on small Parquet fixtures."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import pytest

from viz.contracts import Dataset


def ids(rows: list[dict[str, Any]]) -> list[Any]:
    return [r["id"] for r in rows]


def test_no_steps_returns_source_in_file_order(world: Any, people: Dataset) -> None:
    compiled, rows = world.run([])
    assert ids(rows) == [1, 2, 3, 4, 5, 6]
    assert [c.name for c in compiled.source.columns] == [
        "id",
        "name",
        "region",
        "score",
        "joined",
        "tag",
    ]


def test_filter_predicates_and_formula(world: Any, people: Dataset) -> None:
    _, rows = world.run(
        [
            {
                "op": "filter",
                "predicates": [{"op": "in", "field": "region", "values": ["North", None]}],
                "expression": "score >= 20 OR score IS NULL",
            }
        ]
    )
    assert ids(rows) == [3, 4]


def test_filter_formula_must_be_boolean(world: Any, people: Dataset, pipe: Any) -> None:
    compiled = world.compile(pipe([{"op": "filter", "expression": "score + 1"}]))
    assert compiled.steps[0].status == "error"
    assert "true or false" in compiled.steps[0].error.message


def test_sort_reassigns_row_order(world: Any, people: Dataset) -> None:
    _, rows = world.run(
        [{"op": "sort", "keys": [{"field": "score", "descending": True, "nullsLast": True}]}]
    )
    assert ids(rows) == [5, 4, 3, 1, 6, 2]
    _, rows = world.run(
        [{"op": "sort", "keys": [{"field": "score", "descending": False, "nullsLast": False}]}]
    )
    assert ids(rows) == [2, 1, 6, 3, 4, 5]  # ties keep file order


def test_select_and_rename(world: Any, people: Dataset) -> None:
    compiled, rows = world.run(
        [
            {"op": "select", "fields": ["NAME", "id"]},
            {"op": "rename", "mapping": {"name": "Full name"}},
        ]
    )
    assert list(rows[0]) == ["Full name", "id"]
    assert rows[0] == {"Full name": "Ann", "id": 1}
    assert [c.name for c in compiled.steps[1].columns] == ["Full name", "id"]


def test_rename_collision_is_an_error(world: Any, people: Dataset, pipe: Any) -> None:
    compiled = world.compile(pipe([{"op": "rename", "mapping": {"name": "ID"}}]))
    assert compiled.steps[0].status == "error"
    assert "twice" in compiled.steps[0].error.message


def test_cast_with_failures_and_format(world: Any) -> None:
    world.dataset(
        "t",
        "SELECT * FROM (VALUES ('1', '05/01/2024', 'true'), ('x', 'bad', 'no'), (NULL, NULL, NULL))"
        " AS v(n, d, b)",
    )
    compiled, rows = world.run(
        [
            {
                "op": "cast",
                "casts": [
                    {"field": "n", "to": "integer"},
                    {"field": "d", "to": "date", "format": "%d/%m/%Y"},
                    {"field": "b", "to": "boolean"},
                ],
            }
        ]
    )
    assert [r["n"] for r in rows] == [1, None, None]
    assert [r["d"] for r in rows] == [date(2024, 1, 5), None, None]
    types = {c.name: c.type for c in compiled.steps[0].columns}
    assert types == {"n": "BIGINT", "d": "DATE", "b": "BOOLEAN"}


def test_cast_bad_format_is_a_step_error(world: Any, people: Dataset, pipe: Any) -> None:
    compiled = world.compile(
        pipe([{"op": "cast", "casts": [{"field": "name", "to": "date", "format": "%Q"}]}])
    )
    assert compiled.steps[0].status == "error"
    assert "format" in compiled.steps[0].error.message


def test_fill_missing_strategies(world: Any) -> None:
    world.dataset(
        "t",
        "SELECT * FROM (VALUES (1, NULL, NULL, 'a'), (2, 2.0, 'k', NULL), (3, NULL, 'k', 'b'),"
        " (4, 6.0, 'j', NULL), (5, 'NaN'::DOUBLE, NULL, NULL)) AS v(id, x, c, s)",
    )
    _, rows = world.run(
        [
            {
                "op": "fill_missing",
                "fills": [
                    {"field": "x", "strategy": "mean"},
                    {"field": "c", "strategy": "mode"},
                    {"field": "s", "strategy": "forward"},
                ],
            }
        ]
    )
    # NaN is ignored when computing the mean (mean of 2 and 6), but isn't NULL, so it stays.
    assert [r["x"] for r in rows][:4] == [4.0, 2.0, 4.0, 6.0]
    assert math.isnan(rows[4]["x"])
    assert [r["c"] for r in rows] == ["k", "k", "k", "j", "k"]
    assert [r["s"] for r in rows] == ["a", "a", "b", "b", "b"]
    _, rows = world.run(
        [
            {
                "op": "fill_missing",
                "fills": [
                    {"field": "s", "strategy": "backward"},
                    {"field": "x", "strategy": "value", "value": 0},
                    {"field": "c", "strategy": "value", "value": "none"},
                ],
            }
        ]
    )
    assert [r["s"] for r in rows] == ["a", "b", "b", None, None]
    assert [r["x"] for r in rows][:4] == [0.0, 2.0, 0.0, 6.0]
    assert rows[0]["c"] == "none"


def test_fill_median(world: Any) -> None:
    world.dataset("t", "SELECT * FROM (VALUES (1.0), (NULL), (3.0), (10.0)) AS v(x)")
    _, rows = world.run([{"op": "fill_missing", "fills": [{"field": "x", "strategy": "median"}]}])
    assert [r["x"] for r in rows] == [1.0, 3.0, 3.0, 10.0]


def test_drop_missing(world: Any, people: Dataset) -> None:
    _, rows = world.run([{"op": "drop_missing"}])
    assert ids(rows) == [1, 5, 6]
    _, rows = world.run([{"op": "drop_missing", "fields": ["score", "joined"], "how": "all"}])
    assert ids(rows) == [1, 2, 3, 4, 5, 6]
    _, rows = world.run([{"op": "drop_missing", "fields": ["score", "joined"], "how": "any"}])
    assert ids(rows) == [1, 4, 5, 6]


def test_dedupe_first_and_last(world: Any, people: Dataset) -> None:
    _, rows = world.run([{"op": "dedupe", "fields": ["name"]}])
    assert ids(rows) == [1, 2, 3, 4, 5]
    _, rows = world.run([{"op": "dedupe", "fields": ["name"], "keep": "last"}])
    assert ids(rows) == [2, 3, 4, 5, 6]
    # Whole rows: 1 and 6 differ only in id.
    _, rows = world.run([{"op": "select", "fields": ["name", "score"]}, {"op": "dedupe"}])
    assert len(rows) == 5


def test_dedupe_treats_nulls_as_equal(world: Any) -> None:
    world.dataset("t", "SELECT * FROM (VALUES (NULL, 1), (NULL, 2), ('a', 3)) AS v(k, id)")
    _, rows = world.run([{"op": "dedupe", "fields": ["k"]}])
    assert ids(rows) == [1, 3]


def test_calc_adds_and_replaces(world: Any, people: Dataset) -> None:
    compiled, rows = world.run(
        [
            {"op": "calc", "name": "double", "expression": "score * 2"},
            {"op": "calc", "name": "Score", "expression": "coalesce(score, 0) + 1"},
            {"op": "calc", "name": "initial", "expression": "upper(left(name, 1))"},
        ]
    )
    assert [c.name for c in compiled.steps[-1].columns] == [
        "id",
        "name",
        "region",
        "Score",
        "joined",
        "tag",
        "double",
        "initial",
    ]
    assert rows[1]["Score"] == 1.0 and rows[1]["double"] is None
    assert rows[0]["initial"] == "A"


def test_calc_errors_carry_offsets(world: Any, people: Dataset, pipe: Any) -> None:
    compiled = world.compile(pipe([{"op": "calc", "name": "x", "expression": "score + scor"}]))
    error = compiled.steps[0].error
    assert compiled.steps[0].status == "error"
    assert "did you mean 'score'" in error.message
    assert (error.start, error.end) == (8, 12)


def test_group_aggregate(world: Any, people: Dataset) -> None:
    compiled, rows = world.run(
        [
            {
                "op": "group_aggregate",
                "by": ["region"],
                "aggregations": [
                    {"fn": "count", "asName": "n"},
                    {"field": "score", "fn": "sum", "asName": "total"},
                    {"field": "score", "fn": "mean", "asName": "avg"},
                    {"field": "name", "fn": "count_distinct", "asName": "people"},
                ],
            }
        ]
    )
    assert rows == [
        {"region": "North", "n": 3, "total": 50.0, "avg": 50.0 / 3, "people": 2},
        {"region": "South", "n": 2, "total": 50.0, "avg": 50.0, "people": 2},
        {"region": None, "n": 1, "total": 40.0, "avg": 40.0, "people": 1},
    ]
    assert compiled.steps[0].columns[1].type == "BIGINT"


def test_group_aggregate_without_groups(world: Any, people: Dataset) -> None:
    _, rows = world.run(
        [{"op": "group_aggregate", "by": [], "aggregations": [{"fn": "count", "asName": "n"}]}]
    )
    assert rows == [{"n": 6}]


def test_join_left_with_suffix_and_shared_key(world: Any, people: Dataset) -> None:
    world.dataset(
        "r",
        "SELECT * FROM (VALUES ('North', 'N', 1), ('South', 'S', 2), ('West', 'W', 3))"
        " AS v(region, code, id)",
    )
    compiled, rows = world.run(
        [
            {
                "op": "join",
                "right": {"kind": "dataset", "id": "r"},
                "how": "left",
                "on": [{"left": "region", "right": "REGION"}],
            }
        ]
    )
    names = [c.name for c in compiled.steps[0].columns]
    assert names == ["id", "name", "region", "score", "joined", "tag", "code", "id_right"]
    assert [(r["id"], r["code"], r["id_right"]) for r in rows] == [
        (1, "N", 1),
        (2, "S", 2),
        (3, "N", 1),
        (4, None, None),
        (5, "S", 2),
        (6, "N", 1),
    ]


def test_join_full_coalesces_keys_and_chains_suffixes(world: Any) -> None:
    world.dataset("t", "SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS v(k, x)")
    world.dataset("r", "SELECT * FROM (VALUES (2, 'B', 'q'), (3, 'C', 'r')) AS v(k, x, x_right)")
    compiled, rows = world.run(
        [
            {
                "op": "join",
                "right": {"kind": "dataset", "id": "r"},
                "how": "full",
                "on": [{"left": "k", "right": "k"}],
            }
        ]
    )
    assert [c.name for c in compiled.steps[0].columns] == ["k", "x", "x_right", "x_right_right"]
    assert rows == [
        {"k": 1, "x": "a", "x_right": None, "x_right_right": None},
        {"k": 2, "x": "b", "x_right": "B", "x_right_right": "q"},
        {"k": 3, "x": None, "x_right": "C", "x_right_right": "r"},
    ]


def test_join_inner_and_unknown_key(world: Any, people: Dataset, pipe: Any) -> None:
    world.dataset("r", "SELECT * FROM (VALUES ('North', 1)) AS v(region, flag)")
    _, rows = world.run(
        [
            {
                "op": "join",
                "right": {"kind": "dataset", "id": "r"},
                "how": "inner",
                "on": [{"left": "region", "right": "region"}],
            }
        ]
    )
    assert ids(rows) == [1, 3, 6]
    compiled = world.compile(
        pipe(
            [
                {
                    "op": "join",
                    "right": {"kind": "dataset", "id": "r"},
                    "on": [{"left": "region", "right": "nope"}],
                }
            ]
        )
    )
    assert compiled.steps[0].error.field == "nope"


def test_join_key_type_mismatch_is_a_clear_error(world: Any, pipe: Any) -> None:
    world.dataset("t", "SELECT '1' AS k")
    world.dataset("r", "SELECT 1 AS k")
    compiled = world.compile(
        pipe(
            [
                {
                    "op": "join",
                    "right": {"kind": "dataset", "id": "r"},
                    "how": "full",
                    "on": [{"left": "k", "right": "k"}],
                }
            ]
        )
    )
    assert "cast step" in compiled.steps[0].error.message


def test_union_by_name_and_position(world: Any) -> None:
    world.dataset("t", "SELECT * FROM (VALUES (1, 'a')) AS v(id, x)")
    world.dataset("u", "SELECT * FROM (VALUES ('b', 2, true)) AS v(x, id, extra)")
    _, rows = world.run([{"op": "union", "others": [{"kind": "dataset", "id": "u"}]}])
    assert rows == [{"id": 1, "x": "a", "extra": None}, {"id": 2, "x": "b", "extra": True}]
    world.dataset("w", "SELECT * FROM (VALUES (9, 'z')) AS v(a, b)")
    _, rows = world.run(
        [{"op": "union", "byName": False, "others": [{"kind": "dataset", "id": "w"}]}]
    )
    assert rows == [{"id": 1, "x": "a"}, {"id": 9, "x": "z"}]


def test_union_by_position_needs_same_width(world: Any, pipe: Any) -> None:
    world.dataset("t", "SELECT 1 AS a")
    world.dataset("u", "SELECT 1 AS a, 2 AS b")
    compiled = world.compile(
        pipe([{"op": "union", "byName": False, "others": [{"kind": "dataset", "id": "u"}]}])
    )
    assert compiled.steps[0].status == "error"
    assert "2" in compiled.steps[0].error.message


def test_pivot(world: Any) -> None:
    world.dataset(
        "t",
        "SELECT * FROM (VALUES ('a', 'x', 1), ('a', 'y', 2), ('b', 'x', 3), ('b', 'x', 4),"
        " ('b', NULL, 5), ('c', 'a', 6)) AS v(g, k, v)",
    )
    compiled, rows = world.run(
        [{"op": "pivot", "index": ["g"], "columns": "k", "values": "v", "fn": "sum"}]
    )
    names = [c.name for c in compiled.steps[0].columns]
    # "a" collides with nothing; "null" is the NULL category.
    assert names == ["g", "a", "x", "y", "null"]
    assert rows == [
        {"g": "a", "a": None, "x": 1, "y": 2, "null": None},
        {"g": "b", "a": None, "x": 7, "y": None, "null": 5},
        {"g": "c", "a": 6, "x": None, "y": None, "null": None},
    ]


def test_pivot_name_collision_and_count(world: Any) -> None:
    world.dataset("t", "SELECT * FROM (VALUES ('G', 'g', 1), ('H', 'g', 2)) AS v(g, k, v)")
    compiled, rows = world.run(
        [{"op": "pivot", "index": ["g"], "columns": "k", "values": "v", "fn": "count"}]
    )
    assert [c.name for c in compiled.steps[0].columns] == ["g", "g_2"]
    assert rows == [{"g": "G", "g_2": 1}, {"g": "H", "g_2": 1}]


def test_pivot_too_many_columns(world: Any, pipe: Any) -> None:
    world.dataset("t", "SELECT range AS k, 1 AS v FROM range(1001)")
    compiled = world.compile(pipe([{"op": "pivot", "index": [], "columns": "k", "values": "v"}]))
    assert compiled.steps[0].status == "error"
    assert "1000" in compiled.steps[0].error.message


def test_unpivot_keeps_nulls_and_unifies_types(world: Any) -> None:
    world.dataset("t", "SELECT * FROM (VALUES (1, 10, 'u'), (2, NULL, 'v')) AS v(id, a, b)")
    compiled, rows = world.run([{"op": "unpivot", "idFields": ["id"], "valueFields": ["a", "b"]}])
    assert rows == [
        {"id": 1, "variable": "a", "value": "10"},
        {"id": 1, "variable": "b", "value": "u"},
        {"id": 2, "variable": "a", "value": None},
        {"id": 2, "variable": "b", "value": "v"},
    ]
    assert compiled.steps[0].columns[2].type == "VARCHAR"


def test_unpivot_numeric_values(world: Any) -> None:
    world.dataset("t", "SELECT * FROM (VALUES (1, 10, 2.5)) AS v(id, a, b)")
    _, rows = world.run(
        [
            {
                "op": "unpivot",
                "idFields": ["id"],
                "valueFields": ["a", "b"],
                "nameField": "metric",
                "valueField": "amount",
            }
        ]
    )
    assert rows == [
        {"id": 1, "metric": "a", "amount": 10.0},
        {"id": 1, "metric": "b", "amount": 2.5},
    ]


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("zscore", [-1.0, 0.0, 1.0, None]),
        ("minmax", [0.0, 0.5, 1.0, None]),
        ("robust", [-1.0, 0.0, 1.0, None]),
    ],
)
def test_normalize(world: Any, method: str, expected: list[float | None]) -> None:
    world.dataset("t", "SELECT * FROM (VALUES (1.0, 5), (2.0, 5), (3.0, 5), (NULL, 5)) AS v(x, c)")
    _, rows = world.run([{"op": "normalize", "fields": ["x", "c"], "method": method}])
    assert [None if r["x"] is None else round(r["x"], 9) for r in rows] == expected
    # A constant column has zero spread, so it normalizes to NULL rather than dividing by 0.
    assert [r["c"] for r in rows] == [None, None, None, None]


def test_normalize_rejects_text(world: Any, people: Dataset, pipe: Any) -> None:
    compiled = world.compile(pipe([{"op": "normalize", "fields": ["name"]}]))
    assert compiled.steps[0].error.field == "name"


def test_disabled_step_is_identity(world: Any, people: Dataset, pipe: Any) -> None:
    pipeline = pipe(
        [
            {"op": "select", "fields": ["id"], "enabled": False},
            {"op": "filter", "predicates": [{"op": "eq", "field": "region", "value": "South"}]},
        ]
    )
    compiled = world.compile(pipeline)
    assert [s.status for s in compiled.steps] == ["disabled", "ok"]
    assert len(compiled.steps[0].columns) == 6
    assert ids(world.rows(compiled)) == [2, 5]
    assert ids(world.rows(compiled, compiled.steps[0].step_id)) == [1, 2, 3, 4, 5, 6]


def test_error_blocks_later_steps(world: Any, people: Dataset, pipe: Any) -> None:
    pipeline = pipe(
        [
            {"op": "select", "fields": ["id", "name"]},
            {"op": "sort", "keys": [{"field": "region"}]},
            {"op": "calc", "name": "x", "expression": "1"},
        ]
    )
    compiled = world.compile(pipeline)
    assert [s.status for s in compiled.steps] == ["ok", "error", "blocked"]
    assert compiled.steps[1].error.field == "region"
    assert not compiled.ok


def test_pipeline_of_pipeline_and_cycles(world: Any, people: Dataset, pipe: Any) -> None:
    inner = pipe([{"op": "filter", "expression": "score > 20"}], pipeline_id="inner")
    outer = pipe(
        [{"op": "sort", "keys": [{"field": "score", "descending": True}]}],
        "inner",
        pipeline_id="outer",
        kind="pipeline",
    )
    compiled = world.compile(outer, [inner])
    assert ids(world.rows(compiled)) == [5, 4, 3]

    a = pipe([], "b", pipeline_id="a", kind="pipeline")
    b = pipe([], "a", pipeline_id="b", kind="pipeline")
    from viz.errors import InvalidSpecError

    with pytest.raises(InvalidSpecError, match="cycle"):
        world.compile(a, [b])


def test_reserved_names_rejected(world: Any, people: Dataset, pipe: Any) -> None:
    compiled = world.compile(pipe([{"op": "calc", "name": "__viz_rn", "expression": "1"}]))
    assert compiled.steps[0].status == "error"
    assert "reserved" in compiled.steps[0].error.message


def test_dataset_with_file_row_number_column(world: Any) -> None:
    world.dataset("t", "SELECT * FROM (VALUES (7, 'a'), (8, 'b')) AS v(file_row_number, x)")
    _, rows = world.run([{"op": "sort", "keys": [{"field": "x", "descending": True}]}])
    assert rows == [{"file_row_number": 8, "x": "b"}, {"file_row_number": 7, "x": "a"}]


@pytest.mark.parametrize("keep", ["first", "last"])
def test_dedupe_on_pyarrow_parquet_regression(world: Any, keep: str) -> None:
    """DuckDB 2.0.0: QUALIFY over the *virtual* file_row_number column of a pyarrow-written
    file with 3+ columns hit an internal error that invalidated the database (found by the
    compiler fuzz test). The compiler now uses read_parquet(..., file_row_number = true)."""
    import pyarrow as pa

    table = pa.table({"k": ["a", "b", None, "a"], "f": [0.5, 1.5, 2.5, 3.5], "i": [1, 2, 3, 4]})
    world.arrow_dataset("t", table)
    _, rows = world.run([{"op": "dedupe", "fields": ["k"], "keep": keep}])
    assert [r["i"] for r in rows] == ([1, 2, 3] if keep == "first" else [2, 3, 4])


def test_timestamps_survive(world: Any) -> None:
    world.dataset("t", "SELECT TIMESTAMP '2024-01-01 10:00:00' AS ts")
    _, rows = world.run([{"op": "calc", "name": "d", "expression": "date_trunc('day', ts)"}])
    assert rows[0]["ts"] == datetime(2024, 1, 1, 10)


# --- review fixes ----------------------------------------------------------------------------


def test_pivot_value_with_reserved_prefix_terminates(world: Any) -> None:
    """Review blocker 1: a data value starting with __viz_ looped forever."""
    world.dataset(
        "t",
        "SELECT * FROM (VALUES ('__viz_a', 1), ('__VIZ_A', 2), ('___viz_a', 3), ('b', 4))"
        " AS v(k, v)",
    )
    compiled, rows = world.run(
        [{"op": "pivot", "index": [], "columns": "k", "values": "v", "fn": "sum"}]
    )
    names = [c.name for c in compiled.steps[0].columns]
    assert not any(n.lower().startswith("__viz_") for n in names)
    assert len({n.lower() for n in names}) == 4
    assert sorted(rows[0].values()) == [1, 2, 3, 4]


def test_pivot_column_name_rules() -> None:
    from viz.transform.steps import MAX_PIVOT_NAME, pivot_column_name

    assert pivot_column_name("__viz_rn", set()) == "___viz_rn"
    assert pivot_column_name(None, set()) == "null"
    assert pivot_column_name("  ", set()) == "(empty)"
    assert pivot_column_name("a", {"a", "a_2"}) == "a_3"
    long = "z" * 5000
    first = pivot_column_name(long, set())
    second = pivot_column_name(long, {first.lower()})
    assert len(first) == MAX_PIVOT_NAME and len(second) == MAX_PIVOT_NAME
    assert second.endswith("_2")
    # Bounded even when every candidate is taken: n names need at most n + 1 tries.
    used = {"x"} | {f"x_{n}" for n in range(2, 500)}
    assert pivot_column_name("x", used) == "x_500"


def test_dedupe_with_empty_fields_compares_whole_rows(world: Any) -> None:
    """Review should-fix 8: fields=[] collapsed everything into one row."""
    world.dataset("t", "SELECT * FROM (VALUES (1, 2), (3, 4), (1, 2)) AS v(a, b)")
    for fields in (None, []):
        _, rows = world.run([{"op": "dedupe", "fields": fields}])
        assert [(r["a"], r["b"]) for r in rows] == [(1, 2), (3, 4)]


def test_in_filter_with_numbers_on_a_text_column(world: Any) -> None:
    world.dataset("t", "SELECT * FROM (VALUES ('1', 'a'), ('2', 'b'), ('x', 'c')) AS v(code, n)")
    _, rows = world.run(
        [{"op": "filter", "predicates": [{"op": "in", "field": "code", "values": [1, 2.0]}]}]
    )
    assert [r["n"] for r in rows] == ["a", "b"]


def test_materialization_key_with_a_quote_in_the_data_dir(
    world: Any, pipe: Any, tmp_path: Any
) -> None:
    """The version stamp must be found even when the path is escaped in the SQL ('')."""
    from datetime import timedelta

    from viz.storage import LocalStorage

    storage = LocalStorage(tmp_path / "o'brien data")
    storage.ensure()
    quoted = type(world)(world.engine, storage)
    quoted.dataset("t", "SELECT 1 AS a")
    pipeline = pipe([{"op": "select", "fields": ["a"], "materialize": True}])
    first = quoted.compile(pipeline).pending[0].key
    version = quoted.datasets["t"].current()
    later = version.model_copy(update={"imported_at": version.imported_at + timedelta(seconds=1)})
    quoted.datasets["t"] = quoted.datasets["t"].model_copy(update={"versions": [later]})
    assert quoted.compile(pipeline).pending[0].key != first


def test_materialization_key_changes_with_the_import(world: Any, pipe: Any) -> None:
    """Re-importing a file at the same path (same id and version) must not reuse a stale
    materialized prefix: the key includes the version's import time and row count."""
    from datetime import timedelta

    world.dataset("t", "SELECT 1 AS a")
    pipeline = pipe([{"op": "select", "fields": ["a"], "materialize": True}])
    first = world.compile(pipeline).pending[0].key
    assert world.compile(pipeline).pending[0].key == first  # deterministic
    version = world.datasets["t"].current()
    later = version.model_copy(update={"imported_at": version.imported_at + timedelta(seconds=1)})
    world.datasets["t"] = world.datasets["t"].model_copy(update={"versions": [later]})
    assert world.compile(pipeline).pending[0].key != first


@pytest.mark.parametrize(
    ("expression", "steps", "crossing"),
    [
        # Review B4 residual: a calc step overwriting its own column compounds growth.
        # Ids start at s0: 20 x 10^4 bytes > 64 KiB at the 4th step (s3).
        ("replace(s, 'a', 'aaaaaaaaaa')", 8, 3),
        # 20 x 2^12 bytes > 64 KiB at the 12th step (s11).
        ("s || s", 26, 11),
    ],
)
def test_chained_calc_growth_stops_at_one_megabyte(
    world: Any, pipe: Any, expression: str, steps: int, crossing: int
) -> None:
    import time

    import duckdb

    world.dataset("t", "SELECT 'aaaaaaaaaaaaaaaaaaaa' AS s")
    pipeline = pipe(
        [{"id": f"s{i}", "op": "calc", "name": "s", "expression": expression} for i in range(steps)]
    )
    compiled = world.compile(pipeline)
    assert compiled.ok  # binding is fine; the guard fires when the data is read
    started = time.perf_counter()
    with pytest.raises(duckdb.InvalidInputException, match=rf"step 's{crossing}'.*64 KB"):
        world.rows(compiled)
    assert time.perf_counter() - started < 5


def test_calc_text_guard_allows_large_legitimate_text(world: Any) -> None:
    world.dataset("t", "SELECT repeat('a', 60000) AS s, 1.5 AS n")
    compiled, rows = world.run(
        [
            {"op": "calc", "name": "t", "expression": "s || 'b'"},
            {"op": "calc", "name": "m", "expression": "n * 2"},
            {"op": "calc", "name": "flag", "expression": "length(t) > 5"},
        ]
    )
    assert len(rows[0]["t"]) == 60_001 and rows[0]["m"] == 3.0 and rows[0]["flag"] is True
    types = {c.name: c.type for c in compiled.steps[-1].columns}
    # The guard keeps each calc column's natural type: the same as DuckDB gives unguarded.
    with world.engine.cursor() as cur:
        plain = cur.sql(
            "SELECT s || 'b' AS t, n * 2 AS m, length(s || 'b') > 5 AS flag"
            " FROM (SELECT repeat('a', 60000) AS s, 1.5 AS n)"
        )
        expected = dict(zip(plain.columns, (str(t) for t in plain.types), strict=True))
    assert {k: types[k] for k in expected} == expected


def test_formula_column_reference_cap(world: Any, pipe: Any) -> None:
    world.dataset("t", "SELECT 'a' AS s")
    compiled = world.compile(
        pipe([{"op": "calc", "name": "x", "expression": f"concat({', '.join(['s'] * 101)})"}])
    )
    assert "at most 100" in compiled.steps[0].error.message
    _, rows = world.run(
        [{"op": "calc", "name": "x", "expression": f"concat({', '.join(['s'] * 100)})"}]
    )
    assert rows[0]["x"] == "a" * 100
