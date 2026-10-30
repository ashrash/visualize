"""Size growth across pipeline steps (review B4, rounds 3-4): lists and structs, wide
replaces, the per-step growth bound, and every other step type."""

from __future__ import annotations

import io
import json
from typing import Any

import duckdb
import pytest
from fastapi.testclient import TestClient

from viz.chartdata.sql import to_sql
from viz.transform.formula import (
    MAX_STEP_GROWTH,
    FormulaError,
    validate_formula,
)
from viz.transform.steps import MAX_CALC_TEXT_BYTES


def upload_json(client: TestClient, rows: list[dict[str, Any]]) -> str:
    files = {"file": ("d.json", io.BytesIO(json.dumps(rows).encode()), "application/json")}
    response = client.post("/api/datasets/upload", files=files)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def upload_csv(client: TestClient, text: str) -> str:
    files = {"file": ("d.csv", io.BytesIO(text.encode()), "text/csv")}
    response = client.post("/api/datasets/upload", files=files)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def pipe(ds: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    full = [{"id": f"s{i}", **s} for i, s in enumerate(steps)]
    return {"id": "p1", "source": {"kind": "dataset", "id": ds}, "steps": full}


def inspect(client: TestClient, ds: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = client.post(
        "/api/pipelines/inspect", json={"pipeline": pipe(ds, steps)}
    ).json()
    return result


def preview(client: TestClient, ds: str, steps: list[dict[str, Any]]) -> Any:
    return client.post("/api/pipelines/preview", json={"pipeline": pipe(ds, steps)})


# --- probe3 "lists" ------------------------------------------------------------------------


def test_lists_and_structs_cannot_grow(client: TestClient) -> None:
    ds = upload_json(client, [{"l": [1, 2, 3], "t": ["ab", "cd"], "st": {"a": "x"}, "s": "aa"}])
    types = {
        c["name"]: c["type"]
        for c in client.get(f"/api/datasets/{ds}").json()["versions"][-1]["columns"]
    }
    assert (
        types["l"].endswith("[]") and types["t"] == "VARCHAR[]" and types["st"].startswith("STRUCT")
    )

    # Bare pass-through of a list or struct is fine (and VARCHAR[] isn't mistaken for text).
    for name, expression in (("t2", "t"), ("l2", "l"), ("z", "st"), ("t3", "(t)")):
        result = inspect(client, ds, [{"op": "calc", "name": name, "expression": expression}])
        assert result["ok"], result
        assert (
            preview(
                client, ds, [{"op": "calc", "name": name, "expression": expression}]
            ).status_code
            == 200
        )

    # Anything that builds a list or struct is refused at the step, so it can't compound.
    for expression in ("l || l", "concat(t, t)"):
        name = expression[0]
        steps = [{"op": "calc", "name": name, "expression": expression} for _ in range(24)]
        result = inspect(client, ds, steps)
        assert result["steps"][0]["status"] == "error"
        assert "list or struct" in result["steps"][0]["error"]["message"]
        assert {s["status"] for s in result["steps"][1:]} == {"blocked"}


def test_cast_to_text_is_guarded(client: TestClient) -> None:
    # A 20,000-element list renders to ~130 KB of text: over the guard at the cast step.
    ds = upload_json(client, [{"l": list(range(20_000)), "small": [1, 2]}])
    ok = preview(client, ds, [{"op": "cast", "casts": [{"field": "small", "to": "string"}]}])
    assert ok.status_code == 200
    response = preview(client, ds, [{"op": "cast", "casts": [{"field": "l", "to": "string"}]}])
    assert response.status_code == 422
    assert "column 'l' (step 's0') is longer than 64 KB" in response.json()["detail"]


# --- probe3 "wide_replace" -----------------------------------------------------------------


def test_wide_replace_is_refused(client: TestClient) -> None:
    ds = upload_csv(client, "s\naaaaaaaaaa\n")
    refs100 = "concat(" + ", ".join(["s"] * 100) + ")"
    b64 = "b" * 64
    steps = [
        {"op": "calc", "name": "s", "expression": f"replace({refs100}, 'a', '{b64}')"},
        {"op": "calc", "name": "s", "expression": "concat(" + ", ".join(["s"] * 16) + ")"},
        {"op": "calc", "name": "s", "expression": f"replace(concat(s, s), 'b', '{b64}')"},
    ]
    result = inspect(client, ds, steps)
    assert result["steps"][0]["status"] == "error"
    assert "at most one column" in result["steps"][0]["error"]["message"]
    # Without the wide first step, the chain stops at the guard well before 4 GiB.
    narrow = [
        {"op": "calc", "name": "s", "expression": f"replace(s, 'a', '{b64}')"},  # 640 B
        {"op": "calc", "name": "s", "expression": "concat(" + ", ".join(["s"] * 100) + ")"},
        {"op": "calc", "name": "s", "expression": f"replace(s, 'b', '{b64}')"},
    ]
    response = preview(client, ds, narrow)
    assert response.status_code == 422
    assert "(step 's2') is longer than 64 KB" in response.json()["detail"]


def test_replace_and_concat_ws_rules() -> None:
    columns = ["s", "t"]
    for text in (
        "replace(concat(s, t), 'a', 'b')",
        "regexp_replace(s || t, 'a', 'b')",
        "concat_ws(s, t, t)",
        "concat_ws(replace(s, '', 'bbbb', 'g'), t, t)",
        "concat_ws('" + "-" * 65 + "', s, t)",
    ):
        with pytest.raises(FormulaError):
            validate_formula(text, columns)
    for text in (
        "replace(upper(s), 'a', 'b')",
        "replace(s || '!', 'a', 'b')",
        "concat_ws('-', s, t)",
    ):
        validate_formula(text, columns)


def test_worst_case_formula_growth_is_bounded() -> None:
    """The largest formula the allowlist accepts grows its input at most MAX_STEP_GROWTH x
    (plus the formula's own literals), as the formula module documents."""
    b64 = "b" * 64
    grow = [f"regexp_replace(s, '', '{b64}', 'g')"] * 8
    plain = ["s"] * 92
    worst = f"upper(concat({', '.join(grow + plain)}))"
    validated = validate_formula(worst, ["s"])  # accepted: this is the worst allowed case
    con = duckdb.connect()
    for text in ("a" * 1000, "ɐ" * 1000):  # ɐ grows 2 -> 3 bytes under upper()
        size_in = len(text.encode())
        sql = f"SELECT strlen({to_sql(validated.expression)}) FROM (SELECT '{text}' AS s)"
        size_out = con.sql(sql).fetchall()[0][0]
        assert size_out <= MAX_STEP_GROWTH * size_in + 400_000, (size_in, size_out)
    # One step therefore peaks far below DuckDB's 4 GiB string limit.
    assert MAX_STEP_GROWTH * MAX_CALC_TEXT_BYTES + 400_000 < 2**32 // 64


# --- probe3 "other_steps" ------------------------------------------------------------------


def test_other_steps_do_not_grow_text(client: TestClient) -> None:
    ds = upload_csv(client, "g,s\n1,aaaaaaaaaa\n1,bbbbbbbbbb\n2,cccccccccc\n")
    grow = [{"op": "calc", "name": "s", "expression": "s || s"} for _ in range(3)]
    cases = [
        {
            "op": "group_aggregate",
            "by": ["g"],
            "aggregations": [{"field": "s", "fn": "max", "asName": "m"}],
        },
        {"op": "pivot", "index": ["g"], "columns": "s", "values": "s", "fn": "count"},
        {"op": "union", "others": [{"kind": "dataset", "id": ds}]},
        {"op": "fill_missing", "fills": [{"field": "s", "strategy": "mode"}]},
        {"op": "unpivot", "idFields": ["g"], "valueFields": ["s"]},
        {"op": "dedupe"},
        {"op": "sort", "keys": [{"field": "s"}]},
    ]
    for step in cases:
        response = preview(client, ds, [*grow, step])
        assert response.status_code == 200, (step, response.text)

    for steps in (
        [{"op": "rename", "mapping": {"s": "__viz_calc"}}],
        [{"op": "calc", "name": "__viz_calc", "expression": "s"}],
        [{"op": "calc", "name": "z", "expression": "__viz_calc"}],
    ):
        assert inspect(client, ds, steps)["steps"][0]["status"] == "error"

    long_fill = [
        {
            "op": "fill_missing",
            "fills": [{"field": "s", "strategy": "value", "value": "x" * 70_000}],
        }
    ]
    assert "64 KB" in inspect(client, ds, long_fill)["steps"][0]["error"]["message"]

    # Overwriting then sorting or filtering on the grown column stops at the guard.
    big = [{"op": "calc", "name": "s", "expression": "concat(" + ", ".join(["s"] * 100) + ")"}] * 2
    for after in (
        {"op": "sort", "keys": [{"field": "s"}]},
        {"op": "filter", "expression": "length(s) > 0"},
    ):
        response = preview(client, ds, [*big, after])
        assert response.status_code == 422 and "64 KB" in response.json()["detail"]


def test_normal_calc_types_and_values_are_unchanged(client: TestClient) -> None:
    ds = upload_csv(client, "g,s\n1,ab\n2,cd\n")
    con = duckdb.connect()
    for expression in (
        "upper(s)",
        "length(s)",
        "s || '!'",
        "g * 1.5",
        "CAST(g AS VARCHAR)",
        "if(g > 1, s, NULL)",
        "strptime('2024-01-02', '%Y-%m-%d')",
    ):
        steps = [{"op": "calc", "name": "z", "expression": expression}]
        result = inspect(client, ds, steps)
        z_type = next(c["type"] for c in result["columns"] if c["name"] == "z")
        unguarded = con.sql(
            f"SELECT {expression} AS z"
            " FROM (VALUES (1::BIGINT, 'ab'), (2::BIGINT, 'cd')) AS t(g, s)"
        )
        assert z_type == str(unguarded.types[0]), expression
        assert preview(client, ds, steps).status_code == 200


def test_materialize_job_reports_the_guard_cleanly(client: TestClient) -> None:
    ds = upload_csv(client, "x,s\n1,aaaaaaaaaa\n")
    steps = [
        {"op": "calc", "name": "s", "expression": "concat(" + ", ".join(["s"] * 100) + ")"}
    ] * 2
    pipeline = pipe(ds, [*steps[:1], {**steps[1], "materialize": True}])
    started = client.post("/api/pipelines/materialize", json={"pipeline": pipeline}).json()
    job = client.app.state.jobs.wait(next(iter(started["jobs"].values())), 30)  # type: ignore[attr-defined]
    assert job.status == "failed"
    assert job.error == "calculated column 's' (step 's1') is longer than 64 KB"
