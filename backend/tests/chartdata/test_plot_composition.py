"""End-to-end chart-data tests for the composition family, through POST /api/charts/data."""

from __future__ import annotations

from typing import Any

import pytest

SALES = """quarter,product,units
Q1,A,10
Q1,B,30
Q1,C,10
Q2,A,5
Q2,B,5
"""

HIERARCHY = """region,product,revenue
N,Widget,100
N,Gadget,50
N,Gizmo,1
S,Widget,20
"""


@pytest.fixture
def cap(request: pytest.FixtureRequest) -> int:
    """Overrides the chartdata conftest's `cap` fixture so one test can shrink the mark cap."""
    return getattr(request, "param", 50_000)


def test_percent_stacked_bar_over_http(upload: Any, chart: Any, make_spec: Any) -> None:
    ds = upload(SALES)
    body = {
        "spec": make_spec(
            ds["id"],
            plot="percent_stacked_bar",
            encodings={
                "x": [{"field": "quarter"}],
                "y": [{"field": "units"}],
                "color": [{"field": "product"}],
            },
        )
    }
    table, meta = chart(body)
    rows = table.to_pylist()
    q1_total = sum(r["y0"] for r in rows if r["x"] == "Q1")
    assert q1_total == pytest.approx(100.0)
    assert meta["reduction"] == "aggregated"
    assert meta["fields"]["y0"].startswith("Share of")


@pytest.mark.parametrize("cap", [2], indirect=True)
def test_treemap_leaves_are_capped_above_the_mark_cap(
    upload: Any, chart: Any, make_spec: Any
) -> None:
    ds = upload(HIERARCHY)
    body = {
        "spec": make_spec(
            ds["id"],
            plot="treemap",
            encodings={
                "label": [{"field": "region"}, {"field": "product"}],
                "size": [{"field": "revenue"}],
            },
        )
    }
    table, meta = chart(body)
    # 4 leaf rows over a mark cap of 2: chartdata's generic payload guard (not the plot's own
    # `maxLeaves` rollup, since 4 < the default 50) truncates the response and warns.
    assert table.num_rows <= 2
    assert meta["warnings"]
    assert meta["params"].get("truncatedGroups") is True


def test_treemap_rolls_up_small_leaves_via_its_own_param(
    upload: Any, chart: Any, make_spec: Any
) -> None:
    ds = upload(HIERARCHY)
    body = {
        "spec": make_spec(
            ds["id"],
            plot="treemap",
            encodings={
                "label": [{"field": "region"}, {"field": "product"}],
                "size": [{"field": "revenue"}],
            },
            params={"maxLeaves": 2},
        )
    }
    table, meta = chart(body)
    rows = table.to_pylist()
    assert any(r["label1"] == "(Other)" for r in rows)
    assert any("Other" in w for w in meta["warnings"])


def test_waterfall_over_http(upload: Any, chart: Any, make_spec: Any) -> None:
    csv = "item,amount\nStart,100\nGrowth,20\nEnd,120\n"
    ds = upload(csv)
    body = {
        "spec": make_spec(
            ds["id"],
            plot="waterfall",
            encodings={"x": [{"field": "item"}], "y": [{"field": "amount"}]},
        )
    }
    table, meta = chart(body)
    rows = table.to_pylist()
    assert rows[0]["direction"] == "total"
    # last_is_total defaults to False: the real running total (100 + 20 + 120 = 240) must not be
    # overwritten by resetting the last bar to its own value (120).
    assert rows[-1]["direction"] == "increase"
    assert rows[-1]["end"] == 240.0
    assert meta["method"] == "running_sum"
