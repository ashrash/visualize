"""Reduction kernels: M4, MinMaxLTTB, 2-D and hexagonal binning."""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlglot import exp

from viz.chartdata.reduce import bin2d, hexbin, m4_query, minmaxlttb, numeric_x
from viz.chartdata.sql import col, parquet_source, to_sql
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


def test_numeric_x_temporal_is_epoch_ms(engine: Engine) -> None:
    query = exp.select(numeric_x(exp.Literal.string("1970-01-02"), ColumnSemantic.TEMPORAL))
    with engine.cursor() as cur:
        assert cur.sql(to_sql(query)).fetchall()[0][0] == 86_400_000.0
