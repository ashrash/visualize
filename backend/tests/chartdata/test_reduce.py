"""Reduction kernels: M4, MinMaxLTTB, 2-D and hexagonal binning."""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlglot import exp

from viz.chartdata.reduce import (
    bin2d,
    bucket_position,
    hexbin,
    m4_extent,
    m4_extent_query,
    m4_query,
    minmaxlttb,
    numeric_x,
)
from viz.chartdata.sql import alias, col, fn, parquet_source, to_sql
from viz.contracts import Column, ColumnSemantic
from viz.engine import Engine
from viz.stats.relation import Relation


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    eng = Engine(temp_dir=tmp_path_factory.mktemp("t"), max_concurrent_queries=4)
    yield eng
    eng.close()


def relation_of(engine: Engine, table: pa.Table, path: Path) -> Relation:
    pq.write_table(table, path)
    columns = {
        n: Column(
            name=n,
            type="DOUBLE",
            semantic=ColumnSemantic.QUANTITATIVE,
            null_count=0,
            approx_distinct=0,
        )
        for n in table.column_names
    }
    return Relation(engine, parquet_source(path), None, columns, table.num_rows, "test@v1")


def test_m4_keeps_extremes_per_pixel_column(engine: Engine, tmp_path: Path) -> None:
    n = 10_000
    x = np.arange(n, dtype=np.float64)
    y = np.sin(x / 300.0)
    y[1234] = 50.0
    y[8765] = -50.0
    rel = relation_of(engine, pa.table({"x": x, "y0": y}), tmp_path / "a.parquet")
    source = rel.select(exp.Star())
    query = m4_query(
        source,
        x="x",
        x_position=numeric_x(col("x"), ColumnSemantic.QUANTITATIVE),
        ys=["y0"],
        series=None,
        buckets=50,
    )
    out = rel.arrow(query)
    assert out.num_rows <= 4 * 50
    xs = out.column("x").to_pylist()
    assert xs == sorted(xs) and xs[0] == 0 and xs[-1] == n - 1
    assert {1234.0, 8765.0} <= set(xs)
    # Every bucket's max and min survive.
    for b in range(50):
        lo, hi = b * n / 50, (b + 1) * n / 50
        in_bucket = (x >= lo) & (x < hi)
        assert y[in_bucket].max() in out.column("y0").to_pylist()
        assert y[in_bucket].min() in out.column("y0").to_pylist()


def test_m4_per_series_with_null_series(engine: Engine, tmp_path: Path) -> None:
    x = np.tile(np.arange(1000, dtype=np.float64), 2)
    color = pa.array(["a"] * 1000 + [None] * 1000)
    y = np.concatenate([np.zeros(1000), np.ones(1000)])
    y[10] = 9.0
    rel = relation_of(engine, pa.table({"x": x, "color": color, "y0": y}), tmp_path / "b.parquet")
    query = m4_query(
        rel.select(exp.Star()),
        x="x",
        x_position=numeric_x(col("x"), ColumnSemantic.QUANTITATIVE),
        ys=["y0"],
        series="color",
        buckets=10,
    )
    out = rel.arrow(query).to_pylist()
    assert {r["color"] for r in out} == {"a", None}
    assert len([r for r in out if r["color"] is None]) <= 40
    assert any(r["y0"] == 9.0 for r in out)


def _m4(
    rel: Relation,
    source: exp.Select,
    semantic: ColumnSemantic,
    *,
    ys: list[str],
    series: str | None = None,
    buckets: int,
    with_extent: bool = True,
) -> list[dict[str, object]]:
    """M4 of `source` (columns x[, color], ys) the way the chart path runs it."""
    extent = None
    if with_extent:
        row = rel.arrow(m4_extent_query(rel, col("x"), semantic)).to_pylist()[0]
        extent = m4_extent(row)
    query = m4_query(
        source,
        x="x",
        x_position=bucket_position(col("x"), semantic),
        ys=ys,
        series=series,
        buckets=buckets,
        extent=extent,
    )
    rows: list[dict[str, object]] = rel.arrow(query).to_pylist()
    return rows


def test_m4_with_extent_known_answer(engine: Engine, tmp_path: Path) -> None:
    # Unique y values, so every pick is unambiguous; buckets computed exactly as the SQL does.
    n, buckets = 1000, 10
    x = np.arange(n, dtype=np.float64)
    y = np.random.default_rng(7).permutation(n).astype(np.float64)
    rel = relation_of(engine, pa.table({"x": x, "y0": y}), tmp_path / "k.parquet")
    out = _m4(
        rel,
        rel.select(col("x"), col("y0")),
        ColumnSemantic.QUANTITATIVE,
        ys=["y0"],
        buckets=buckets,
    )
    bucket = np.minimum(np.floor((x - 0.0) * (buckets / (n - 1.0))), buckets - 1)
    expected: set[float] = set()
    for b in range(buckets):
        idx = np.flatnonzero(bucket == b)
        expected |= {x[idx[0]], x[idx[-1]], x[idx[np.argmin(y[idx])]], x[idx[np.argmax(y[idx])]]}
    assert [r["x"] for r in out] == sorted(expected)
    assert all(r["y0"] == y[int(r["x"])] for r in out)  # type: ignore[call-overload]
    assert {r["__viz_groups"] for r in out} == {n}


def test_m4_with_extent_matches_fallback(engine: Engine, tmp_path: Path) -> None:
    # The single-pass (extent given) and computed-extent shapes keep the same points when no
    # x sits on a bucket boundary (the span, n - 1 = 997, is prime), including a NULL y at
    # the first x and per-series picks.
    n = 998
    x = np.tile(np.arange(n, dtype=np.float64) + 0.5, 2)
    y = np.sin(x / 40.0) + np.repeat([0.0, 3.0], n)
    y_arr = pa.array(y, mask=np.isin(np.arange(2 * n), [0, n + 400]))
    color = pa.array(["a"] * n + ["b"] * n)
    rel = relation_of(
        engine, pa.table({"x": x, "color": color, "y0": y_arr}), tmp_path / "f.parquet"
    )
    source = rel.select(col("x"), col("color"), col("y0"))
    args: dict[str, Any] = {"ys": ["y0"], "series": "color", "buckets": 16}
    fast = _m4(rel, source, ColumnSemantic.QUANTITATIVE, **args)
    slow = _m4(rel, source, ColumnSemantic.QUANTITATIVE, with_extent=False, **args)
    assert fast == slow
    first_a = fast[0]
    assert first_a["color"] == "a" and first_a["x"] == 0.5 and first_a["y0"] is None
    assert list(fast[0]) == ["x", "color", "y0", "__viz_groups"]  # source's column order


def test_m4_several_measures_pick_whole_rows(engine: Engine, tmp_path: Path) -> None:
    # Heavy ties in y0: each output row must be a real source row (no pairing of y0's x with
    # a y1 from another tied row), and every column's extremes per bucket must survive.
    rng = np.random.default_rng(3)
    n, buckets = 2000, 8
    x = np.arange(n, dtype=np.float64)
    y0 = rng.integers(0, 3, n).astype(np.float64)
    y1 = rng.normal(size=n)
    extra = rng.normal(size=n)
    table = pa.table({"x": x, "y0": y0, "y1": y1, "extra": extra})
    rel = relation_of(engine, table, tmp_path / "w.parquet")
    source = rel.select(col("x"), col("y0"), col("y1"), col("extra"))
    out = _m4(rel, source, ColumnSemantic.QUANTITATIVE, ys=["y0", "y1"], buckets=buckets)
    for r in out:
        i = int(r["x"])  # type: ignore[call-overload]
        assert (r["y0"], r["y1"], r["extra"]) == (y0[i], y1[i], extra[i])
    xs = [r["x"] for r in out]
    assert xs == sorted(xs) and xs[0] == 0 and xs[-1] == n - 1
    bucket = np.minimum(np.floor(x * (buckets / (n - 1.0))), buckets - 1)
    for b in range(buckets):
        idx = bucket == b
        kept = [r for r in out if bucket[int(r["x"])] == b]  # type: ignore[call-overload]
        for name, values in (("y0", y0), ("y1", y1)):
            got = [r[name] for r in kept]
            assert values[idx].min() in got and values[idx].max() in got


def _temporal_relation(engine: Engine, path: Path, select: str) -> Relation:
    with engine.cursor() as cur:
        cur.sql(f"COPY ({select}) TO '{path.as_posix()}' (FORMAT parquet)")
    columns = {
        "x": Column(
            name="x",
            type="TIMESTAMP",
            semantic=ColumnSemantic.TEMPORAL,
            null_count=0,
            approx_distinct=0,
        ),
        "y0": Column(
            name="y0",
            type="DOUBLE",
            semantic=ColumnSemantic.QUANTITATIVE,
            null_count=0,
            approx_distinct=0,
        ),
    }
    return Relation(engine, parquet_source(path), None, columns, None, "test@v1")


@pytest.mark.parametrize("ys", [["y0"], ["y0", "y1"]])
def test_m4_integer_path_drops_null_x(engine: Engine, tmp_path: Path, ys: list[str]) -> None:
    # Regression: DuckDB's least() skips NULLs, so an unguarded integer bucket put the NULL-x
    # group (here 40 empty date cells summing to 4000, far above every real y) into the last
    # pixel column as a fake spike, and in the whole-row (several measures) shape it also
    # displaced that column's real extremes before being filtered out.
    rel = _temporal_relation(
        engine,
        tmp_path / "nullx.parquet",
        "SELECT TIMESTAMP '2024-01-01' + INTERVAL (i) SECOND AS x, (i % 97)::DOUBLE AS y0,"
        " (i % 89)::DOUBLE AS y1 FROM range(5000) t(i)"
        " UNION ALL SELECT NULL, 100.0, 100.0 FROM range(40)",
    )
    grouped = rel.select(col("x"), *(alias(fn("sum", col(y)), y) for y in ys)).group_by(col("x"))
    extent_row = rel.arrow(m4_extent_query(rel, col("x"), ColumnSemantic.TEMPORAL)).to_pylist()[0]
    assert extent_row["n"] == 5040
    extent = m4_extent(extent_row)
    assert extent is not None and all(isinstance(v, int) for v in extent)  # integer path
    buckets = 50
    out = rel.arrow(
        m4_query(
            grouped,
            x="x",
            x_position=bucket_position(col("x"), ColumnSemantic.TEMPORAL),
            ys=ys,
            series=None,
            buckets=buckets,
            extent=extent,
        )
    ).to_pylist()
    assert {r["__viz_groups"] for r in out} == {5001}  # the NULL-x group still counts
    assert all(r["x"] is not None for r in out)
    assert all(r[y] != 4000.0 for r in out for y in ys)
    seconds = np.arange(5000)
    values = {"y0": seconds % 97, "y1": seconds % 89}
    lo, hi = extent
    bucket = np.minimum(seconds * 1_000_000 * buckets // (hi - lo), buckets - 1)
    picked = {int((r["x"] - datetime(2024, 1, 1)).total_seconds()): r for r in out}  # type: ignore[operator]
    for s, r in picked.items():  # every point is a real row
        assert all(r[y] == values[y][s] for y in ys)
    for b in (0, buckets - 1):  # every column's real extremes survive, the last one included
        idx = np.flatnonzero(bucket == b)
        assert {int(idx[0]), int(idx[-1])} <= set(picked)
        for y in ys:
            got = [picked[s][y] for s in picked if bucket[s] == b]
            assert values[y][idx].max() in got and values[y][idx].min() in got


def test_m4_temporal_integer_buckets(engine: Engine, tmp_path: Path) -> None:
    # Epoch-microsecond integer buckets; a NULL x and an infinite timestamp are left out (and
    # don't break the extent), but still count as groups.
    rel = _temporal_relation(
        engine,
        tmp_path / "t.parquet",
        "SELECT TIMESTAMP '2024-01-01' + INTERVAL (i) SECOND AS x, (i % 97)::DOUBLE AS y0"
        " FROM range(5000) t(i)"
        " UNION ALL SELECT NULL, 1000.0 UNION ALL SELECT 'infinity'::TIMESTAMP, -1000.0",
    )
    source = rel.select(col("x"), col("y0"))
    extent_row = rel.arrow(m4_extent_query(rel, col("x"), ColumnSemantic.TEMPORAL)).to_pylist()[0]
    assert extent_row["n"] == 5002
    # The infinite timestamp makes max(x) unusable, so the computed-extent shape takes over.
    assert m4_extent(extent_row) is None
    out = _m4(rel, source, ColumnSemantic.TEMPORAL, ys=["y0"], buckets=50)
    assert {r["__viz_groups"] for r in out} == {5002}
    ys = [r["y0"] for r in out]
    assert 1000.0 not in ys and -1000.0 not in ys
    # Now a finite extent: integer bucket math, same points as the computed-extent shape.
    rel = rel.narrow(fn("isfinite", col("x")))
    fast = _m4(rel, rel.select(col("x"), col("y0")), ColumnSemantic.TEMPORAL, ys=["y0"], buckets=50)
    lo, hi = m4_extent(
        rel.arrow(m4_extent_query(rel, col("x"), ColumnSemantic.TEMPORAL)).to_pylist()[0]
    ) or (0, 0)
    assert isinstance(lo, int) and isinstance(hi, int)
    seconds = np.arange(5000)
    bucket = np.minimum(seconds * 1_000_000 * 50 // (hi - lo), 49)
    expected: set[int] = set()
    for b in range(50):
        idx = np.flatnonzero(bucket == b)
        vals = seconds[idx] % 97
        expected |= {int(idx[0]), int(idx[-1])}
        assert vals.min() in [r["y0"] for r in fast] and vals.max() in [r["y0"] for r in fast]
    got = {int((r["x"] - datetime(2024, 1, 1)).total_seconds()) for r in fast}  # type: ignore[operator]
    assert expected <= got
    assert len(fast) <= 4 * 50


def test_m4_temporal_wide_span_and_single_value(engine: Engine, tmp_path: Path) -> None:
    # 400 years in microseconds x 2000 buckets overflows BIGINT: DOUBLE bucket math instead.
    rel = _temporal_relation(
        engine,
        tmp_path / "wide.parquet",
        "SELECT TIMESTAMP '1800-01-01' + INTERVAL (i * 30) DAY AS x, (i % 11)::DOUBLE AS y0"
        " FROM range(4870) t(i)",
    )
    out = _m4(
        rel, rel.select(col("x"), col("y0")), ColumnSemantic.TEMPORAL, ys=["y0"], buckets=2000
    )
    assert out[0]["x"] == datetime(1800, 1, 1) and len(out) <= 4870
    one = _temporal_relation(
        engine, tmp_path / "one.parquet", "SELECT TIMESTAMP '2024-01-01' AS x, 5.0 AS y0"
    )
    out = _m4(one, one.select(col("x"), col("y0")), ColumnSemantic.TEMPORAL, ys=["y0"], buckets=10)
    assert [(r["x"], r["y0"]) for r in out] == [(datetime(2024, 1, 1), 5.0)]


def test_m4_nan_x_uses_finite_extent(engine: Engine, tmp_path: Path) -> None:
    x = np.arange(100, dtype=np.float64)
    x[[3, 50]] = [math.nan, math.inf]
    rel = relation_of(engine, pa.table({"x": x, "y0": np.ones(100)}), tmp_path / "nan.parquet")
    row = rel.arrow(m4_extent_query(rel, col("x"), ColumnSemantic.QUANTITATIVE)).to_pylist()[0]
    assert m4_extent(row) is None  # max(x) is NaN
    out = _m4(
        rel, rel.select(col("x"), col("y0")), ColumnSemantic.QUANTITATIVE, ys=["y0"], buckets=5
    )
    xs = [r["x"] for r in out]
    assert all(math.isfinite(v) for v in xs)  # type: ignore[arg-type]
    assert xs[0] == 0 and xs[-1] == 99


def test_m4_with_extent_is_one_pipeline(engine: Engine, tmp_path: Path) -> None:
    # Perf regression guard (raw-timestamp line at 10M rows: ~230 ms -> ~140 ms). With a known
    # extent the M4 query must not materialize the grouped source (CTE) or join back to it.
    rel = relation_of(
        engine, pa.table({"x": np.arange(100.0), "y0": np.arange(100.0)}), tmp_path / "p.parquet"
    )
    grouped = rel.select(col("x"), alias(fn("sum", col("y0")), "y0")).group_by(col("x"))
    for ys, extra in ((["y0"], []), (["y0"], [alias(fn("count", col("y0")), "n")])):
        source = grouped.copy().select(*extra) if extra else grouped.copy()
        query = m4_query(
            source,
            x="x",
            x_position=bucket_position(col("x"), ColumnSemantic.QUANTITATIVE),
            ys=ys,
            series=None,
            buckets=10,
            extent=(0.0, 99.0),
        )
        plan = "\n".join(str(r[1]) for r in rel.rows("EXPLAIN " + to_sql(query)))
        assert "CTE" not in plan and "SEMI" not in plan.upper()
        assert plan.count("READ_PARQUET") == 1
        assert len(rel.arrow(query)) <= 4 * 10


@settings(deadline=None, max_examples=60)
@given(
    st.lists(st.floats(-1e6, 1e6, allow_nan=False), min_size=1, max_size=3000),
    st.integers(3, 400),
)
def test_minmaxlttb_bounds_and_order(values: list[float], n_out: int) -> None:
    table = pa.table({"x": np.arange(len(values), dtype=np.int64), "y0": values})
    out, reduced = minmaxlttb(table, x="x", ys=["y0"], series=None, n_out=n_out)
    assert out.num_rows <= max(n_out, 3) or not reduced
    assert reduced == (len(values) > max(n_out, 3))
    xs = out.column("x").to_pylist()
    assert xs == sorted(xs)
    if reduced:
        assert xs[0] == 0 and xs[-1] == len(values) - 1


def test_minmaxlttb_handles_nan_and_null_x() -> None:
    y = [float(i % 7) for i in range(1000)]
    y[5] = math.nan
    x = pa.array([*range(999), None], pa.int64())
    out, reduced = minmaxlttb(pa.table({"x": x, "y0": y}), x="x", ys=["y0"], series=None, n_out=50)
    assert reduced and out.num_rows <= 50
    assert None not in out.column("x").to_pylist()


def test_bin2d_counts_and_edges(engine: Engine, tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    x, y = rng.uniform(0, 10, 5000), rng.uniform(-5, 5, 5000)
    x[:3] = [math.nan, math.inf, 1.0]
    y[2] = math.nan
    rel = relation_of(engine, pa.table({"x": x, "y": y}), tmp_path / "c.parquet")
    out, params = bin2d(rel, col("x"), col("y"), bins_x=10, bins_y=5)
    assert sum(out.column("count").to_pylist()) == 4997  # NaN/inf pairs dropped
    assert params["n"] == 4997
    assert out.num_rows <= 50
    lo, hi = min(out.column("x_lo").to_pylist()), max(out.column("x_hi").to_pylist())
    assert lo == pytest.approx(float(np.min(x[3:])))
    assert hi == pytest.approx(float(np.max(x[3:])))
    # Brute-force check of one bin.
    widths = (hi - lo) / 10
    first = out.to_pylist()[0]
    finite = np.isfinite(x) & np.isfinite(y)
    xs, ys = x[finite], y[finite]
    ylo = float(np.min(ys))
    wy = (float(np.max(ys)) - ylo) / 5
    i = np.minimum(np.floor((xs - lo) / widths), 9)
    j = np.minimum(np.floor((ys - ylo) / wy), 4)
    bi, bj = round((first["x_lo"] - lo) / widths), round((first["y_lo"] - ylo) / wy)
    assert first["count"] == int(np.sum((i == bi) & (j == bj)))


def test_hexbin_counts_and_nearest_centres(engine: Engine, tmp_path: Path) -> None:
    rng = np.random.default_rng(2)
    x, y = rng.normal(size=4000), rng.normal(size=4000)
    rel = relation_of(engine, pa.table({"x": x, "y": y}), tmp_path / "d.parquet")
    out, params = hexbin(rel, col("x"), col("y"), width_px=400, height_px=300, radius_px=10)
    assert sum(out.column("count").to_pylist()) == 4000
    assert params["radiusPx"] == 10
    # Each point's cell centre is the nearest hex-lattice centre (checked in pixel space).
    x0, x1, y0, y1 = x.min(), x.max(), y.min(), y.max()
    px, py = (x - x0) / (x1 - x0) * 400, (y - y0) / (y1 - y0) * 300
    dx, dy = math.sqrt(3) * 10, 30.0
    centres = []
    for row in range(-1, 12):
        for column in range(-1, 25):
            centres.append((column * dx, row * dy))
            centres.append(((column + 0.5) * dx, (row + 0.5) * dy))
    centre_arr = np.array(centres)
    nearest = np.argmin(
        ((px[:, None] - centre_arr[:, 0]) ** 2 + (py[:, None] - centre_arr[:, 1]) ** 2), axis=1
    )
    expected_cells = len(set(nearest.tolist()))
    assert out.num_rows == expected_cells


def test_x_range_rejects_non_finite_bounds() -> None:
    from viz.chartdata.reduce import x_range_predicate
    from viz.errors import InvalidSpecError

    for bounds in ((math.nan, 1.0), (0.0, math.inf)):
        with pytest.raises(InvalidSpecError):
            x_range_predicate(col("t"), ColumnSemantic.TEMPORAL, bounds)
    sql = to_sql(x_range_predicate(col("t"), ColumnSemantic.TEMPORAL, ("x'); DROP", "2024")))
    assert "CAST('x''); DROP' AS TIMESTAMP)" in sql


@pytest.mark.parametrize(
    "literal",
    ["DATE '1970-01-02'", "TIMESTAMP '1970-01-02'", "TIMESTAMPTZ '1970-01-02 00:00:00+00'"],
)
def test_numeric_x_temporal_is_utc_epoch_ms(literal: str) -> None:
    import duckdb
    import sqlglot

    con = duckdb.connect()
    con.execute("SET TimeZone = 'Asia/Kolkata'")  # a non-UTC session must not shift values
    value = sqlglot.parse_one(literal, read="duckdb")
    query = exp.select(numeric_x(value, ColumnSemantic.TEMPORAL))
    assert con.sql(to_sql(query)).fetchall()[0][0] == 86_400_000.0


def test_epoch_ms_values_handles_zones_units_and_dates() -> None:
    from datetime import date, datetime, timezone

    from viz.chartdata.reduce import epoch_ms_values

    day = 86_400_000.0
    utc = datetime(1970, 1, 2, tzinfo=UTC)
    ist = utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
    cases = [
        pa.chunked_array([pa.array([utc, None], pa.timestamp("us", tz="UTC"))]),
        pa.chunked_array([pa.array([ist, None], pa.timestamp("ms", tz="Asia/Kolkata"))]),
        pa.chunked_array([pa.array([datetime(1970, 1, 2), None], pa.timestamp("ns"))]),
        pa.chunked_array([pa.array([date(1970, 1, 2), None], pa.date32())]),
    ]
    for column in cases:
        values = epoch_ms_values(column)
        assert values[0] == day and math.isnan(values[1])
