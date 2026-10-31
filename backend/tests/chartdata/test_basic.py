"""The generic aggregate path (basic family): grouping, caps, downsampling, zoom, error bars."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

import pytest
from scipy import stats as sps

from viz.plots import scoped_registry

SALES = """region,product,sales,units,order_date
North,Widget,120.5,3,2024-01-05
South,Widget,80,2,2024-01-17
North,Gadget,200,5,2024-02-02
East,Gadget,,1,2024-02-20
South,Gizmo,55.25,1,2024-03-03
North,Gizmo,10,1,2024-03-15
"""


def categories(n: int) -> str:
    lines = ["cat,v"] + [f"c{i:04d},{i}" for i in range(n)]
    return "\n".join(lines) + "\n"


def series(n: int, *, colors: int = 1) -> str:
    start = datetime(2024, 1, 1)
    lines = ["ts,g,v"]
    for i in range(n):
        for c in range(colors):
            value = math.sin(i / 50.0) * 100 + c * 10 + (1000 if i == n // 3 else 0)
            lines.append(f"{(start + timedelta(minutes=i)).isoformat()},g{c},{value:.6f}")
    return "\n".join(lines) + "\n"


def test_bar_groups_sorted_by_total(upload: Any, chart: Any, make_spec: Any) -> None:
    ds = upload(SALES)
    body = {
        "spec": make_spec(
            ds["id"],
            encodings={"x": [{"field": "region"}], "y": [{"field": "sales", "agg": "sum"}]},
        )
    }
    table, meta = chart(body)
    assert table.to_pylist() == [
        {"x": "North", "y0": 330.5},
        {"x": "South", "y0": 135.25},
        {"x": "East", "y0": None},
    ]
    assert meta["reduction"] == "aggregated" and meta["method"] == "group_by"
    assert meta["rowsScanned"] == 6 and meta["rowsMatched"] == 6
    assert meta["fields"] == {"x": "region", "y0": "Sum of sales"}
    assert meta["xDomain"] is None


def test_stacked_bar_orders_x_by_total_across_colors(
    upload: Any, chart: Any, make_spec: Any
) -> None:
    ds = upload(SALES)
    body = {
        "spec": make_spec(
            ds["id"],
            "stacked_bar",
            encodings={
                "x": [{"field": "product"}],
                "y": [{"field": "units"}],
                "color": [{"field": "region"}],
            },
        )
    }
    table, _ = chart(body)
    rows = [(r["x"], r["color"], r["y0"]) for r in table.to_pylist()]
    # Gadget total 6, Widget 5, Gizmo 2; colors ascending within each x.
    assert rows == [
        ("Gadget", "East", 1.0),
        ("Gadget", "North", 5.0),
        ("Widget", "North", 3.0),
        ("Widget", "South", 2.0),
        ("Gizmo", "North", 1.0),
        ("Gizmo", "South", 1.0),
    ]


def test_field_names_are_case_insensitive(upload: Any, chart: Any, make_spec: Any) -> None:
    ds = upload(SALES)
    body = {
        "spec": make_spec(
            ds["id"], encodings={"x": [{"field": "REGION"}], "y": [{"agg": "count"}]}
        ),
        "filters": [{"op": "eq", "field": "Product", "value": "Widget"}],
    }
    table, meta = chart(body)
    assert {r["x"]: r["y0"] for r in table.to_pylist()} == {"North": 1.0, "South": 1.0}
    assert meta["rowsMatched"] == 2


@pytest.mark.parametrize("cap", [20])
def test_category_cap_exactly_at_and_above(upload: Any, chart: Any, make_spec: Any) -> None:
    at_cap = upload(categories(20))
    body = {
        "spec": make_spec(at_cap["id"], encodings={"x": [{"field": "cat"}], "y": [{"field": "v"}]})
    }
    table, meta = chart(body)
    assert table.num_rows == 20 and "truncatedGroups" not in meta["params"]
    assert meta["warnings"] == []

    above = upload(categories(21))
    body["spec"]["source"]["id"] = above["id"]
    table, meta = chart(body)
    assert table.num_rows == 20
    assert meta["params"]["truncatedGroups"] is True
    assert "c0000" not in table.column("x").to_pylist()  # the smallest group is dropped
    assert meta["warnings"]


@pytest.mark.parametrize("cap", [20])
def test_cap_is_shared_between_measures(upload: Any, chart: Any, make_spec: Any) -> None:
    ds = upload(categories(15))
    body = {
        "spec": make_spec(
            ds["id"],
            encodings={"x": [{"field": "cat"}], "y": [{"field": "v"}, {"agg": "count"}]},
        )
    }
    table, meta = chart(body)
    assert table.num_rows == 10  # 10 rows x 2 measures = 20 marks
    assert meta["fields"]["y1"] == "Count"


def test_line_downsamples_to_twice_viewport_width(upload: Any, chart: Any, make_spec: Any) -> None:
    ds = upload(series(3000))
    body = {
        "spec": make_spec(
            ds["id"], "line", encodings={"x": [{"field": "ts"}], "y": [{"field": "v"}]}
        ),
        "viewport": {"widthPx": 100},
    }
    table, meta = chart(body)
    assert meta["reduction"] == "downsampled" and meta["method"] == "group_by+minmaxlttb"
    assert table.num_rows <= 200 and meta["params"]["nOut"] == 200
    values = table.column("y0").to_pylist()
    assert max(values) > 1000  # the spike survives
    xs = table.column("x").to_pylist()
    assert xs == sorted(xs)
    assert xs[0] == datetime(2024, 1, 1) and xs[-1] == datetime(2024, 1, 1) + timedelta(
        minutes=2999
    )
    assert meta["rowsMatched"] == 3000
    # xDomain is epoch milliseconds (naive timestamps are treated as UTC).
    epoch = datetime(1970, 1, 1)
    first, last = datetime(2024, 1, 1), datetime(2024, 1, 1) + timedelta(minutes=2999)
    assert meta["xDomain"] == [
        (first - epoch).total_seconds() * 1000,
        (last - epoch).total_seconds() * 1000,
    ]


def test_line_below_threshold_is_not_downsampled(upload: Any, chart: Any, make_spec: Any) -> None:
    ds = upload(series(150))
    body = {
        "spec": make_spec(
            ds["id"], "area", encodings={"x": [{"field": "ts"}], "y": [{"field": "v"}]}
        ),
        "viewport": {"widthPx": 100},
    }
    table, meta = chart(body)
    assert table.num_rows == 150 and meta["reduction"] == "aggregated"


@pytest.mark.parametrize("cap", [1000])
def test_line_over_cap_uses_m4_then_lttb(upload: Any, chart: Any, make_spec: Any) -> None:
    ds = upload(series(2000, colors=2))
    body = {
        "spec": make_spec(
            ds["id"],
            "line",
            encodings={"x": [{"field": "ts"}], "y": [{"field": "v"}], "color": [{"field": "g"}]},
        ),
        "viewport": {"widthPx": 100},
    }
    table, meta = chart(body)
    assert meta["reduction"] == "downsampled" and meta["method"] == "m4+minmaxlttb"
    per_series: dict[str, list[Any]] = {}
    for row in table.to_pylist():
        per_series.setdefault(row["color"], []).append(row)
    assert set(per_series) == {"g0", "g1"}
    for rows in per_series.values():
        assert len(rows) <= 200
        assert max(r["y0"] for r in rows) > 1000  # M4 keeps extremes
    assert table.num_rows <= 1000


@pytest.mark.parametrize("cap", [1000])
def test_raw_line_over_cap_queries(
    upload: Any, chart: Any, make_spec: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Perf regression guard for the raw-timestamp line (10M rows: ~230 ms -> ~140 ms): one
    # extent+count scan, then M4 as a single pipeline (no CTE, no semi-join back to the
    # grouped rows), and no separate count query for rowsMatched.
    from viz.transform import runtime

    seen: list[str] = []
    original = runtime.query_arrow

    def recording(engine: Any, sql: str, **kwargs: Any) -> Any:
        seen.append(sql)
        return original(engine, sql, **kwargs)

    monkeypatch.setattr(runtime, "query_arrow", recording)
    ds = upload(series(3000))
    body = {
        "spec": make_spec(
            ds["id"], "line", encodings={"x": [{"field": "ts"}], "y": [{"field": "v"}]}
        ),
        "viewport": {"widthPx": 100},
    }
    table, meta = chart(body)
    assert meta["method"] == "m4+minmaxlttb" and meta["rowsMatched"] == 3000
    assert table.num_rows <= 200 and max(table.column("y0").to_pylist()) > 1000
    m4 = [s for s in seen if "__viz_groups" in s]
    assert len(m4) == 1 and "WITH" not in m4[0] and "SEMI" not in m4[0].upper()
    assert len(seen) == 2  # the extent (with the row count) and M4


def test_x_range_gives_detail_and_keeps_domain(upload: Any, chart: Any, make_spec: Any) -> None:
    ds = upload(series(3000))
    base = {
        "spec": make_spec(
            ds["id"], "line", encodings={"x": [{"field": "ts"}], "y": [{"field": "v"}]}
        ),
        "viewport": {"widthPx": 100},
    }
    _, full = chart(base)
    start = datetime(2024, 1, 1, 10, 0)
    end = start + timedelta(minutes=99)
    zoomed = dict(base, viewport={"widthPx": 100, "xRange": [start.isoformat(), end.isoformat()]})
    table, meta = chart(zoomed)
    xs = table.column("x").to_pylist()
    assert len(xs) == 100 and xs[0] == start and xs[-1] == end  # every point: full detail
    assert meta["reduction"] == "aggregated"
    assert meta["xDomain"] == full["xDomain"]
    # Epoch-millisecond bounds (what ECharts reports) select the same range.
    as_ms = [
        (start - datetime(1970, 1, 1)).total_seconds() * 1000,
        (end - datetime(1970, 1, 1)).total_seconds() * 1000,
    ]
    table_ms, _ = chart(dict(base, viewport={"widthPx": 100, "xRange": as_ms}))
    assert table_ms.column("x").to_pylist() == xs


def test_x_range_with_time_unit_keeps_partial_buckets(
    upload: Any, chart: Any, make_spec: Any
) -> None:
    ds = upload(SALES)
    body = {
        "spec": make_spec(
            ds["id"],
            "line",
            encodings={
                "x": [{"field": "order_date", "timeUnit": "month"}],
                "y": [{"field": "units"}],
            },
        ),
        "viewport": {"xRange": ["2024-01-20", "2024-02-10"]},
    }
    table, _ = chart(body)
    assert [r["x"].month for r in table.to_pylist()] == [1, 2]


@pytest.mark.parametrize(
    ("bars", "lo", "hi"),
    [
        ({"kind": "fixed", "value": 5}, 105.5, 115.5),
        ({"kind": "sd"}, 110.5 - 95.0, 110.5 + 95.0),
        ({"kind": "se"}, 110.5 - 95.0 / math.sqrt(3), 110.5 + 95.0 / math.sqrt(3)),
        (
            {"kind": "ci", "level": 0.95},
            110.5 - sps.t.ppf(0.975, 2) * 95.0 / math.sqrt(3),
            110.5 + sps.t.ppf(0.975, 2) * 95.0 / math.sqrt(3),
        ),
    ],
)
def test_error_bars(
    upload: Any, chart: Any, make_spec: Any, bars: Any, lo: float, hi: float
) -> None:
    ds = upload("g,v\na,15.5\na,110.5\na,205.5\nb,1\n")
    body = {
        "spec": make_spec(
            ds["id"],
            encodings={"x": [{"field": "g"}], "y": [{"field": "v", "agg": "mean"}]},
            style={"errorBars": bars},
        )
    }
    table, meta = chart(body)
    a = next(r for r in table.to_pylist() if r["x"] == "a")
    assert a["y0"] == pytest.approx(110.5)
    assert a["y0_lo"] == pytest.approx(lo) and a["y0_hi"] == pytest.approx(hi)
    b = next(r for r in table.to_pylist() if r["x"] == "b")
    if bars["kind"] == "fixed":
        assert (b["y0_lo"], b["y0_hi"]) == (-4.0, 6.0)
    else:
        assert b["y0_lo"] is None  # one value: no spread
    assert meta["fields"]["y0_lo"] == "Mean of v (low)"
    assert not any(n.startswith("__viz") for n in table.column_names)


def test_pie_ignores_color(upload: Any, chart: Any, make_spec: Any) -> None:
    ds = upload(SALES)
    body = {
        "spec": make_spec(
            ds["id"],
            "donut",
            encodings={
                "x": [{"field": "product"}],
                "y": [{"field": "units"}],
                "color": [{"field": "region"}],
            },
        )
    }
    table, _ = chart(body)
    assert table.column_names == ["x", "y0"]


def test_max_series(upload: Any, chart: Any, make_spec: Any) -> None:
    rows = ["x,g,v"] + [f"1,g{i},{i}" for i in range(10)]
    ds = upload("\n".join(rows) + "\n")
    body = {
        "spec": make_spec(
            ds["id"],
            "grouped_bar",
            encodings={"x": [{"field": "x"}], "y": [{"field": "v"}], "color": [{"field": "g"}]},
            params={"maxSeries": 3},
        )
    }
    table, meta = chart(body)
    assert sorted(table.column("color").to_pylist()) == ["g7", "g8", "g9"]
    assert meta["params"]["truncatedSeries"] is True


def test_errors(upload: Any, client: Any, make_spec: Any) -> None:
    ds = upload(SALES)
    bad = [
        {"x": [{"field": "nope"}]},
        {"x": [{"field": "region", "timeUnit": "month"}]},
        {"y": [{"field": "sales"}]},
        {"x": [{"field": "region"}], "y": [{"agg": "sum"}]},
    ]
    for encodings in bad:
        response = client.post(
            "/api/charts/data", json={"spec": make_spec(ds["id"], encodings=encodings)}
        )
        assert response.status_code == 422, (encodings, response.text)
    # Every Plots.md plot type is registered now (Wave 2 is complete), so there's no longer a
    # real PlotType to use as an "unimplemented" stand-in. An empty scoped registry makes any
    # otherwise-valid plot type unimplemented for the duration of the request. The default "bar"
    # doesn't exercise this: it's handled by the generic aggregate path (chartdata.basic), not
    # the viz.plots registry, so it ignores an empty scoped_registry() - use a plots/-family type.
    with scoped_registry():
        response = client.post(
            "/api/charts/data",
            json={"spec": make_spec(ds["id"], "histogram", encodings={"x": [{"field": "sales"}]})},
        )
        assert response.status_code == 501
