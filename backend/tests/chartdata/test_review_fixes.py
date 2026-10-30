"""Regression tests for the xhigh review of the chart-data path."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import pytest

SALES = """region,product,sales,units,order_date
North,Widget,120.5,3,2024-01-05
South,Widget,80,2,2024-01-17
North,Gadget,200,5,2024-02-02
East,Gadget,,1,2024-02-20
South,Gizmo,55.25,1,2024-03-03
North,Gizmo,10,1,2024-03-15
"""

COLLIDING = """x,color,y0,y,count,x_lo,region,sales,day
q,red,1,5,9,0,North,10,2024-01-05
r,blue,2,6,9,0,South,20,2024-02-06
s,red,3,7,9,0,North,30,2024-02-07
"""


def series(n: int, *, colors: int = 1) -> str:
    start = datetime(2024, 1, 1)
    lines = ["ts,g,v"]
    for i in range(n):
        for c in range(colors):
            value = math.sin(i / 50.0) * 100 + c * 10
            lines.append(f"{(start + timedelta(minutes=i)).isoformat()},g{c:02d},{value:.6f}")
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("source", ["dataset", "pipeline"])
def test_source_columns_named_like_output_columns(
    upload: Any, chart: Any, make_spec: Any, source: str
) -> None:
    """Blocker 2: GROUP BY "x" bound to a source column named x instead of the alias."""
    ds = upload(COLLIDING)
    extra: dict[str, Any] = {}
    ref: dict[str, Any] = {"kind": "dataset", "id": ds["id"]}
    if source == "pipeline":
        steps = [{"id": "s", "op": "sort", "keys": [{"field": "sales"}]}]
        extra = {"pipelines": [{"id": "p", "source": ref, "steps": steps}]}
        ref = {"kind": "pipeline", "id": "p"}

    def run(plot: str, encodings: dict[str, Any]) -> list[dict[str, Any]]:
        spec = make_spec(ds["id"], plot, encodings=encodings)
        spec["source"] = ref
        table, _ = chart({"spec": spec, **extra})
        rows: list[dict[str, Any]] = table.to_pylist()
        return rows

    assert run("bar", {"x": [{"field": "region"}], "y": [{"field": "sales"}]}) == [
        {"x": "North", "y0": 40.0},
        {"x": "South", "y0": 20.0},
    ]
    by_x = run(
        "bar", {"x": [{"field": "region"}], "y": [{"field": "sales"}], "color": [{"field": "x"}]}
    )
    assert sorted((r["x"], r["color"], r["y0"]) for r in by_x) == [
        ("North", "q", 10.0),
        ("North", "s", 30.0),
        ("South", "r", 20.0),
    ]
    swapped = run(
        "bar", {"x": [{"field": "x"}], "y": [{"field": "y0"}], "color": [{"field": "region"}]}
    )
    assert sorted((r["x"], r["color"], r["y0"]) for r in swapped) == [
        ("q", "North", 1.0),
        ("r", "South", 2.0),
        ("s", "North", 3.0),
    ]
    monthly = run("line", {"x": [{"field": "day", "timeUnit": "month"}], "y": [{"field": "count"}]})
    assert [r["y0"] for r in monthly] == [9.0, 18.0]
    by_month = run(
        "bar",
        {
            "x": [{"field": "region"}],
            "y": [{"field": "y"}],
            "color": [{"field": "day", "timeUnit": "month"}],
        },
    )
    assert len(by_month) == 3
    with_bars = run(
        "bar",
        {"x": [{"field": "color"}], "y": [{"field": "count", "agg": "mean"}]},
    )
    assert {r["x"] for r in with_bars} == {"red", "blue"}


@pytest.mark.parametrize("cap", [2000])
def test_many_series_share_the_cap_without_dropping_any(
    upload: Any, chart: Any, make_spec: Any
) -> None:
    """Should-fix 5: n_out was per series, so 6 of 20 series vanished silently."""
    ds = upload(series(400, colors=20))
    body = {
        "spec": make_spec(
            ds["id"],
            "line",
            encodings={"x": [{"field": "ts"}], "y": [{"field": "v"}], "color": [{"field": "g"}]},
        ),
        "viewport": {"widthPx": 1920},
    }
    table, meta = chart(body)
    assert len(set(table.column("color").to_pylist())) == 20
    assert table.num_rows <= 2000
    assert meta["params"]["nOut"] == 100  # 2000 marks shared by 20 series
    assert "truncatedGroups" not in meta["params"]


@pytest.mark.parametrize("cap", [10])
def test_truncation_warning_suggests_a_fix(upload: Any, chart: Any, make_spec: Any) -> None:
    temporal = upload(series(20))
    spec = make_spec(temporal["id"], encodings={"x": [{"field": "ts"}], "y": [{"field": "v"}]})
    _, meta = chart({"spec": spec})
    assert any("set a time unit" in w for w in meta["warnings"])
    numeric = upload("n\n" + "".join(f"{i}\n" for i in range(20)), "nums.csv")
    spec = make_spec(numeric["id"], encodings={"x": [{"field": "n"}], "y": [{"agg": "count"}]})
    _, meta = chart({"spec": spec})
    assert any("histogram" in w for w in meta["warnings"])


def test_error_bars_warn_for_non_mean_aggregates(upload: Any, chart: Any, make_spec: Any) -> None:
    ds = upload(SALES)
    spec = make_spec(
        ds["id"],
        encodings={"x": [{"field": "region"}], "y": [{"field": "sales", "agg": "sum"}]},
        style={"errorBars": {"kind": "sd"}},
    )
    _, meta = chart({"spec": spec})
    assert any("around a mean" in w for w in meta["warnings"])


@pytest.mark.parametrize("cap", [1000])
def test_profile_skip_with_filters_falls_back_when_the_data_fits(
    upload: Any, chart: Any, make_spec: Any
) -> None:
    """The import profile says x has more distinct values than the cap, so the cap+1 probe
    is skipped (with or without filters). When filters leave fewer groups than the cap, M4
    reports that and the exact (unreduced) result is returned instead."""
    ds = upload(series(3000))
    spec = make_spec(ds["id"], "line", encodings={"x": [{"field": "ts"}], "y": [{"field": "v"}]})
    big, meta = chart({"spec": spec, "viewport": {"widthPx": 100}})
    assert meta["method"] == "m4+minmaxlttb" and meta["rowsMatched"] == 3000
    filtered, meta = chart(
        {
            "spec": spec,
            "viewport": {"widthPx": 100},
            "filters": [{"op": "is_null", "field": "ts", "isNull": False}],
        }
    )
    assert meta["method"] == "m4+minmaxlttb" and filtered.to_pylist() == big.to_pylist()
    # 150 rows pass this filter: fewer than the cap and under 2 x widthPx, so every point.
    end = (datetime(2024, 1, 1) + timedelta(minutes=149)).isoformat()
    small, meta = chart(
        {
            "spec": spec,
            "viewport": {"widthPx": 100},
            "filters": [{"op": "range", "field": "ts", "max": end}],
        }
    )
    assert small.num_rows == 150 and meta["reduction"] == "aggregated"
    assert meta["rowsMatched"] == 150


def test_timestamptz_domain_and_zoom_are_utc(upload: Any, chart: Any, make_spec: Any) -> None:
    """Should-fix 6: TIMESTAMPTZ was shifted by the server's UTC offset."""
    ds = upload(
        "ts,v\n2024-01-01 00:00:00+00,1\n2024-01-02 00:00:00+00,2\n2024-01-03 00:00:00+00,3\n"
    )
    tz_type = ds["versions"][-1]["columns"][0]["type"]
    assert tz_type.startswith("TIMESTAMP WITH TIME ZONE")
    jan1, day = 1704067200000.0, 86_400_000.0
    spec = make_spec(ds["id"], "line", encodings={"x": [{"field": "ts"}], "y": [{"field": "v"}]})
    _, meta = chart({"spec": spec})
    assert meta["xDomain"] == [jan1, jan1 + 2 * day]
    table, meta = chart({"spec": spec, "viewport": {"xRange": [jan1, jan1 + day]}})
    assert table.column("y0").to_pylist() == [1.0, 2.0]
    assert meta["xDomain"] == [jan1, jan1 + 2 * day]
    for bound in ("2024-01-02T00:00:00Z", "2024-01-02T05:30:00+05:30", "2024-01-02"):
        filters = [{"op": "between_dates", "field": "ts", "min": bound}]
        table, _ = chart({"spec": spec, "filters": filters})
        assert table.column("y0").to_pylist() == [2.0, 3.0], bound


def test_naive_timestamp_honours_offsets_in_filters(
    upload: Any, chart: Any, make_spec: Any
) -> None:
    ds = upload("ts,v\n2024-01-01 00:00:00,1\n2024-01-02 00:00:00,2\n", "naive.csv")
    assert ds["versions"][-1]["columns"][0]["type"] == "TIMESTAMP"
    spec = make_spec(ds["id"], "line", encodings={"x": [{"field": "ts"}], "y": [{"field": "v"}]})
    # 05:30+05:30 is midnight UTC: the Jan 2 row matches. A plain CAST would drop the
    # offset, read 05:30, and miss it.
    filters = [{"op": "between_dates", "field": "ts", "min": "2024-01-02T05:30:00+05:30"}]
    table, _ = chart({"spec": spec, "filters": filters})
    assert table.column("y0").to_pylist() == [2.0]


def test_in_filter_numbers_on_text_column(upload: Any, chart: Any, make_spec: Any) -> None:
    ds = upload("code,v\n1,10\n2,20\nx,30\n")
    pipeline = {
        "id": "p",
        "source": {"kind": "dataset", "id": ds["id"]},
        "steps": [{"id": "c", "op": "cast", "casts": [{"field": "code", "to": "string"}]}],
    }
    spec = make_spec(ds["id"], encodings={"x": [{"field": "code"}], "y": [{"field": "v"}]})
    spec["source"] = {"kind": "pipeline", "id": "p"}
    body = {
        "spec": spec,
        "pipelines": [pipeline],
        "filters": [{"op": "in", "field": "code", "values": [1, 2]}],
    }
    table, _ = chart(body)
    assert sorted(table.column("x").to_pylist()) == ["1", "2"]


def test_overlay_and_chart_slots_are_independent(client: Any, upload: Any) -> None:
    from viz.chartdata.overlays import overlay_key
    from viz.chartdata.service import slot_key
    from viz.contracts import ChartDataRequest

    request = ChartDataRequest.model_validate(
        {"spec": {"id": "c", "plotType": "bar", "source": {"kind": "dataset", "id": "d"}}}
    )
    assert overlay_key(request) == "chart:overlay:c"
    assert slot_key(request) == "chart:c"
