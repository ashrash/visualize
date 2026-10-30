"""Known-answer tests for the geo family (Plots.md Section 7)."""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from viz.chartdata.sql import parquet_source
from viz.contracts import (
    Column,
    ColumnSemantic,
    Encoding,
    PlotType,
    Reduction,
    SourceRef,
)
from viz.contracts.chart import ChartSpec
from viz.contracts.chartdata import Viewport
from viz.engine import Engine
from viz.plots import run_plot_stat
from viz.plots.base import TableOutput
from viz.plots.geo import dorling_layout
from viz.stats.relation import Relation


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    eng = Engine(temp_dir=tmp_path_factory.mktemp("t"), max_concurrent_queries=4)
    yield eng
    eng.close()


_SEMANTIC_BY_NAME = {
    "region": ColumnSemantic.GEO,
    "lat": ColumnSemantic.GEO,
    "lon": ColumnSemantic.GEO,
    "value": ColumnSemantic.QUANTITATIVE,
    "color": ColumnSemantic.NOMINAL,
}


def relation_of(engine: Engine, table: pa.Table, path: Path) -> Relation:
    pq.write_table(table, path)
    columns = {
        n: Column(
            name=n,
            type="DOUBLE" if _SEMANTIC_BY_NAME.get(n) != ColumnSemantic.NOMINAL else "VARCHAR",
            semantic=_SEMANTIC_BY_NAME.get(n, ColumnSemantic.QUANTITATIVE),
            null_count=0,
            approx_distinct=table.column(n).to_pylist().__len__(),
        )
        for n in table.column_names
    }
    return Relation(engine, parquet_source(path), None, columns, table.num_rows, "test@v1")


def _spec(
    plot_type: PlotType, params: dict[str, object] | None = None, **channels: list[str | None]
) -> ChartSpec:
    return ChartSpec.model_validate(
        {
            "id": "c1",
            "plotType": plot_type.value,
            "source": SourceRef(kind="dataset", id="d1").model_dump(),
            "encodings": {
                name: [Encoding(field=f).model_dump(by_alias=True) for f in fields]
                for name, fields in channels.items()
            },
            "params": params or {},
        }
    )


def _run(spec: ChartSpec, relation: Relation, mark_cap: int = 1000, executor: object = None):
    return run_plot_stat(spec, relation, viewport=Viewport(), mark_cap=mark_cap, executor=executor)


# -- choropleth -------------------------------------------------------------------------------


def test_choropleth_aggregates_value_by_region(engine: Engine, tmp_path: Path) -> None:
    table = pa.table(
        {
            "region": ["US", "US", "CA", "FR"],
            "value": [10.0, 20.0, 5.0, 1.0],
        }
    )
    rel = relation_of(engine, table, tmp_path / "choropleth.parquet")
    spec = _spec(PlotType.CHOROPLETH, region=["region"], value=["value"])
    out = _run(spec, rel)
    assert isinstance(out, TableOutput)
    assert out.reduction == Reduction.AGGREGATED
    assert out.method == "group_by"
    regions = out.table.column("region").to_pylist()
    values = out.table.column("y0").to_pylist()
    result = dict(zip(regions, values, strict=True))
    assert result == {"US": 30.0, "CA": 5.0, "FR": 1.0}
    assert out.fields == {"region": "region", "y0": "Sum of value"}


def test_choropleth_counts_rows_when_no_value_is_bound(engine: Engine, tmp_path: Path) -> None:
    table = pa.table({"region": ["US", "US", "CA"], "value": [1.0, 2.0, 3.0]})
    rel = relation_of(engine, table, tmp_path / "choropleth_count.parquet")
    spec = _spec(PlotType.CHOROPLETH, region=["region"])
    out = _run(spec, rel)
    assert isinstance(out, TableOutput)
    regions = out.table.column("region").to_pylist()
    values = out.table.column("y0").to_pylist()
    result = dict(zip(regions, values, strict=True))
    assert result == {"US": 2.0, "CA": 1.0}
    assert out.fields["y0"] == "Count"


def test_choropleth_returns_every_region_even_above_mark_cap(
    engine: Engine, tmp_path: Path
) -> None:
    # The number of distinct regions is bounded by the data (never above the region column's
    # cardinality), so choropleth does no truncation of its own: the generic post-cap guard in
    # chartdata (`_finish_table`) is what enforces `mark_cap`, same as every other plot module.
    regions = [f"R{i}" for i in range(20)]
    table = pa.table({"region": regions, "value": [1.0] * 20})
    rel = relation_of(engine, table, tmp_path / "choropleth_many.parquet")
    spec = _spec(PlotType.CHOROPLETH, region=["region"], value=["value"])
    out = _run(spec, rel, mark_cap=5)
    assert isinstance(out, TableOutput)
    assert out.table.num_rows == 20


# -- bubble map ---------------------------------------------------------------------------------


def test_bubble_map_aggregates_by_lat_lon_and_color(engine: Engine, tmp_path: Path) -> None:
    table = pa.table(
        {
            "lat": [1.0, 1.0, 2.0, 2.0],
            "lon": [1.0, 1.0, 2.0, 2.0],
            "color": ["a", "b", "a", "a"],
            "value": [10.0, 5.0, 3.0, 7.0],
        }
    )
    rel = relation_of(engine, table, tmp_path / "bubble.parquet")
    spec = _spec(PlotType.BUBBLE_MAP, lat=["lat"], lon=["lon"], size=["value"], color=["color"])
    out = _run(spec, rel)
    assert isinstance(out, TableOutput)
    rows = out.table.to_pylist()
    by_key = {(r["lat"], r["lon"], r["color"]): r["y0"] for r in rows}
    assert by_key == {(1.0, 1.0, "a"): 10.0, (1.0, 1.0, "b"): 5.0, (2.0, 2.0, "a"): 10.0}


def test_bubble_map_without_size_counts_points(engine: Engine, tmp_path: Path) -> None:
    table = pa.table({"lat": [1.0, 1.0, 2.0], "lon": [1.0, 1.0, 2.0], "value": [1.0, 1.0, 1.0]})
    rel = relation_of(engine, table, tmp_path / "bubble_count.parquet")
    spec = _spec(PlotType.BUBBLE_MAP, lat=["lat"], lon=["lon"])
    out = _run(spec, rel)
    assert isinstance(out, TableOutput)
    rows = {(r["lat"], r["lon"]): r["y0"] for r in out.table.to_pylist()}
    assert rows == {(1.0, 1.0): 2.0, (2.0, 2.0): 1.0}


def test_bubble_map_falls_back_to_grid_binning_over_the_cap(engine: Engine, tmp_path: Path) -> None:
    # Every point sits at a distinct (lat, lon), so GROUP BY is a no-op and the raw group-by
    # path would hand back one row per point: with n well above mark_cap, that must not happen
    # (it would silently drop whole regions once the generic cap truncation cuts it down).
    n = 400
    table = pa.table({"lat": [i * 0.01 for i in range(n)], "lon": [i * 0.01 for i in range(n)]})
    rel = relation_of(engine, table, tmp_path / "bubble_dense.parquet")
    spec = _spec(PlotType.BUBBLE_MAP, lat=["lat"], lon=["lon"])
    out = _run(spec, rel, mark_cap=50)
    assert isinstance(out, TableOutput)
    assert out.reduction == Reduction.BINNED
    assert out.method == "geo_bin2d"
    assert out.table.num_rows <= 50
    # Every original point is accounted for in the binned counts.
    assert sum(out.table.column("y0").to_pylist()) == n
    assert any("grid" in w.lower() for w in out.warnings)


# -- dot density --------------------------------------------------------------------------------


def test_dot_density_keeps_all_points_under_the_cap(engine: Engine, tmp_path: Path) -> None:
    n = 20
    table = pa.table(
        {
            "lat": [float(i) for i in range(n)],
            "lon": [float(i) for i in range(n)],
            "color": ["a" if i % 2 == 0 else "b" for i in range(n)],
        }
    )
    rel = relation_of(engine, table, tmp_path / "dots_small.parquet")
    spec = _spec(PlotType.DOT_DENSITY, lat=["lat"], lon=["lon"], color=["color"])
    out = _run(spec, rel, mark_cap=1000)
    assert isinstance(out, TableOutput)
    assert out.table.num_rows == n
    assert out.reduction == Reduction.NONE
    assert set(out.table.column_names) >= {"lat", "lon", "color"}


def test_dot_density_samples_and_reports_reduction_over_the_cap(
    engine: Engine, tmp_path: Path
) -> None:
    n = 500
    table = pa.table({"lat": [float(i) for i in range(n)], "lon": [float(i) for i in range(n)]})
    rel = relation_of(engine, table, tmp_path / "dots_big.parquet")
    spec = _spec(PlotType.DOT_DENSITY, lat=["lat"], lon=["lon"])
    out = _run(spec, rel, mark_cap=50)
    assert isinstance(out, TableOutput)
    assert out.table.num_rows == 50
    assert out.reduction == Reduction.SAMPLED
    assert out.method == "reservoir"
    assert out.rows_matched == n
    assert any("sample" in w.lower() or "reservoir" in w.lower() for w in out.warnings)


# -- geographic heatmap -------------------------------------------------------------------------


def test_geo_heatmap_bins_points_into_a_grid(engine: Engine, tmp_path: Path) -> None:
    # Four clusters near the four corners of a 10x10 degree box: a 2x2 bin grid should put
    # exactly one cluster's worth of points in each cell.
    lats, lons = [], []
    for base_lat, base_lon, n in ((0.1, 0.1, 3), (0.1, 9.9, 1), (9.9, 0.1, 2), (9.9, 9.9, 4)):
        lats += [base_lat] * n
        lons += [base_lon] * n
    table = pa.table({"lat": lats, "lon": lons})
    rel = relation_of(engine, table, tmp_path / "heatmap.parquet")
    spec = _spec(
        PlotType.GEO_HEATMAP, lat=["lat"], lon=["lon"], params={"binsLon": 2, "binsLat": 2}
    )
    out = _run(spec, rel)
    assert isinstance(out, TableOutput)
    assert out.reduction == Reduction.BINNED
    assert out.method == "geo_bin2d"
    assert out.table.num_rows == 4
    assert sorted(out.table.column("y0").to_pylist()) == [1.0, 2.0, 3.0, 4.0]
    assert out.rows_matched == 10


def test_geo_heatmap_weights_by_value(engine: Engine, tmp_path: Path) -> None:
    table = pa.table({"lat": [0.0, 0.0], "lon": [0.0, 0.0], "value": [3.0, 4.0]})
    rel = relation_of(engine, table, tmp_path / "heatmap_weighted.parquet")
    spec = _spec(
        PlotType.GEO_HEATMAP,
        lat=["lat"],
        lon=["lon"],
        value=["value"],
        params={"binsLon": 2, "binsLat": 2},
    )
    out = _run(spec, rel)
    assert isinstance(out, TableOutput)
    assert out.table.column("y0").to_pylist() == [7.0]


def test_geo_heatmap_distinct_count_is_really_distinct(engine: Engine, tmp_path: Path) -> None:
    # Regression: the weighted measure used to go through a hand-rolled SQL-function table
    # that mapped count_distinct to plain `count`, so a repeated value was over-counted.
    table = pa.table({"lat": [0.0, 0.0, 0.0], "lon": [0.0, 0.0, 0.0], "value": [1.0, 1.0, 2.0]})
    rel = relation_of(engine, table, tmp_path / "heatmap_distinct.parquet")
    spec = ChartSpec.model_validate(
        {
            "id": "c1",
            "plotType": PlotType.GEO_HEATMAP.value,
            "source": SourceRef(kind="dataset", id="d1").model_dump(),
            "encodings": {
                "lat": [Encoding(field="lat").model_dump(by_alias=True)],
                "lon": [Encoding(field="lon").model_dump(by_alias=True)],
                "value": [Encoding(field="value", agg="count_distinct").model_dump(by_alias=True)],
            },
            "params": {"binsLon": 2, "binsLat": 2},
        }
    )
    out = _run(spec, rel)
    assert isinstance(out, TableOutput)
    assert out.table.column("y0").to_pylist() == [2.0]


def test_geo_heatmap_empty_input(engine: Engine, tmp_path: Path) -> None:
    table = pa.table({"lat": pa.array([], pa.float64()), "lon": pa.array([], pa.float64())})
    rel = relation_of(engine, table, tmp_path / "heatmap_empty.parquet")
    spec = _spec(PlotType.GEO_HEATMAP, lat=["lat"], lon=["lon"])
    out = _run(spec, rel)
    assert isinstance(out, TableOutput)
    assert out.table.num_rows == 0
    assert out.rows_matched == 0


# -- cartogram ------------------------------------------------------------------------------------


def test_cartogram_aggregates_region_value_and_centroid(engine: Engine, tmp_path: Path) -> None:
    table = pa.table(
        {
            "region": ["US", "US", "CA"],
            "lat": [10.0, 12.0, 50.0],
            "lon": [-100.0, -98.0, -90.0],
            "value": [5.0, 5.0, 2.0],
        }
    )
    rel = relation_of(engine, table, tmp_path / "cartogram.parquet")
    spec = _spec(PlotType.CARTOGRAM, region=["region"], value=["value"], lat=["lat"], lon=["lon"])
    out = _run(spec, rel, executor=None)
    assert isinstance(out, TableOutput)
    rows = {r["region"]: r for r in out.table.to_pylist()}
    assert set(rows) == {"US", "CA"}
    assert rows["US"]["y0"] == 10.0
    assert rows["CA"]["y0"] == 2.0
    for r in rows.values():
        assert r["radius"] > 0
        assert math.isfinite(r["lon"]) and math.isfinite(r["lat"])


def test_cartogram_runs_through_a_given_executor(engine: Engine, tmp_path: Path) -> None:
    calls: list[tuple[object, ...]] = []

    class RecordingExecutor:
        def submit(self, fn, *args):
            calls.append(args)

            class _Future:
                def result(self, timeout=None):
                    return fn(*args)

            return _Future()

    table = pa.table(
        {"region": ["US", "CA"], "lat": [10.0, 50.0], "lon": [-100.0, -90.0], "value": [5.0, 2.0]}
    )
    rel = relation_of(engine, table, tmp_path / "cartogram_exec.parquet")
    spec = _spec(PlotType.CARTOGRAM, region=["region"], value=["value"], lat=["lat"], lon=["lon"])
    out = _run(spec, rel, executor=RecordingExecutor())
    assert isinstance(out, TableOutput)
    assert len(calls) == 1  # the heavy layout ran through the executor, not inline


def test_cartogram_drops_rows_with_non_finite_coordinates(engine: Engine, tmp_path: Path) -> None:
    # A region whose every row has a non-finite lat/lon used to make avg() return NULL, and
    # the pure-Python layout (not NaN-aware) then raised TypeError: an unhandled 500.
    table = pa.table(
        {
            "region": ["US", "US", "ZZ", "ZZ"],
            "lat": [10.0, 12.0, float("nan"), None],
            "lon": [-100.0, -98.0, float("inf"), None],
            "value": [5.0, 5.0, 9.0, 9.0],
        }
    )
    rel = relation_of(engine, table, tmp_path / "cartogram_nan.parquet")
    spec = _spec(PlotType.CARTOGRAM, region=["region"], value=["value"], lat=["lat"], lon=["lon"])
    out = _run(spec, rel)
    assert isinstance(out, TableOutput)
    regions = out.table.column("region").to_pylist()
    assert regions == ["US"]  # ZZ had no finite coordinate anywhere and is dropped, not crashed
    assert math.isfinite(out.table.column("lat").to_pylist()[0])
    assert math.isfinite(out.table.column("lon").to_pylist()[0])


def test_cartogram_caps_region_count_and_warns(engine: Engine, tmp_path: Path) -> None:
    n = 10
    table = pa.table(
        {
            "region": [f"R{i}" for i in range(n)],
            "lat": [float(i) for i in range(n)],
            "lon": [float(i) for i in range(n)],
            # Distinct values so the top-N-by-|value| cap has an unambiguous answer.
            "value": [float(i + 1) for i in range(n)],
        }
    )
    rel = relation_of(engine, table, tmp_path / "cartogram_many.parquet")
    spec = _spec(PlotType.CARTOGRAM, region=["region"], value=["value"], lat=["lat"], lon=["lon"])
    out = _run(spec, rel, mark_cap=4)
    assert isinstance(out, TableOutput)
    assert out.table.num_rows == 4
    # The largest-value regions (R9, R8, R7, R6) are kept.
    assert set(out.table.column("region").to_pylist()) == {"R9", "R8", "R7", "R6"}
    assert any("largest" in w.lower() for w in out.warnings)


def test_cartogram_maps_a_worker_timeout_to_query_interrupted(
    engine: Engine, tmp_path: Path
) -> None:
    import concurrent.futures

    from viz.errors import QueryInterruptedError

    class TimingOutExecutor:
        def submit(self, fn, *args):
            class _Future:
                def result(self, timeout=None):
                    raise concurrent.futures.TimeoutError

            return _Future()

    table = pa.table(
        {"region": ["US", "CA"], "lat": [10.0, 50.0], "lon": [-100.0, -90.0], "value": [5.0, 2.0]}
    )
    rel = relation_of(engine, table, tmp_path / "cartogram_timeout.parquet")
    spec = _spec(PlotType.CARTOGRAM, region=["region"], value=["value"], lat=["lat"], lon=["lon"])
    with pytest.raises(QueryInterruptedError):
        _run(spec, rel, executor=TimingOutExecutor())


def test_cartogram_maps_a_broken_pool_to_engine_busy(engine: Engine, tmp_path: Path) -> None:
    from concurrent.futures.process import BrokenProcessPool

    from viz.errors import EngineBusyError

    class CrashingExecutor:
        def submit(self, fn, *args):
            class _Future:
                def result(self, timeout=None):
                    raise BrokenProcessPool("worker died")

            return _Future()

    table = pa.table(
        {"region": ["US", "CA"], "lat": [10.0, 50.0], "lon": [-100.0, -90.0], "value": [5.0, 2.0]}
    )
    rel = relation_of(engine, table, tmp_path / "cartogram_broken.parquet")
    spec = _spec(PlotType.CARTOGRAM, region=["region"], value=["value"], lat=["lat"], lon=["lon"])
    with pytest.raises(EngineBusyError):
        _run(spec, rel, executor=CrashingExecutor())


def test_cartogram_empty_input(engine: Engine, tmp_path: Path) -> None:
    table = pa.table(
        {
            "region": pa.array([], pa.string()),
            "lat": pa.array([], pa.float64()),
            "lon": pa.array([], pa.float64()),
            "value": pa.array([], pa.float64()),
        }
    )
    rel = relation_of(engine, table, tmp_path / "cartogram_empty.parquet")
    spec = _spec(PlotType.CARTOGRAM, region=["region"], value=["value"], lat=["lat"], lon=["lon"])
    out = _run(spec, rel)
    assert isinstance(out, TableOutput)
    assert out.table.num_rows == 0


# -- dorling_layout (pure function) ---------------------------------------------------------------


def test_dorling_layout_separates_overlapping_circles() -> None:
    # Two equal-valued circles at the same point must end up apart by at least their radii sum.
    lons = [0.0, 0.0]
    lats = [0.0, 0.0]
    values = [1.0, 1.0]
    new_lon, new_lat, radius = dorling_layout(lons, lats, values, iterations=300, padding=0.0)
    cos_lat0 = 1.0
    dx = (new_lon[0] - new_lon[1]) * cos_lat0
    dy = new_lat[0] - new_lat[1]
    dist = math.hypot(dx, dy)
    assert dist >= (radius[0] + radius[1]) * 0.9  # a little slack for the iterative solver
    assert radius[0] == pytest.approx(radius[1])


def test_dorling_layout_bigger_value_gets_bigger_radius() -> None:
    lons = [0.0, 1.0, 2.0]
    lats = [0.0, 0.0, 0.0]
    values = [1.0, 4.0, 9.0]
    _, _, radius = dorling_layout(lons, lats, values, iterations=50, padding=0.02)
    assert radius[0] < radius[1] < radius[2]


def test_dorling_layout_handles_a_single_point() -> None:
    new_lon, new_lat, radius = dorling_layout([5.0], [10.0], [3.0], iterations=10, padding=0.02)
    assert new_lon == pytest.approx([5.0])
    assert new_lat == pytest.approx([10.0])
    assert radius[0] > 0


def test_dorling_layout_handles_no_points() -> None:
    assert dorling_layout([], [], [], iterations=10, padding=0.02) == ([], [], [])


def test_dorling_layout_has_no_overlaps_at_moderate_scale() -> None:
    # n=2 (the old test coverage) can't show whether the force balance actually converges: at
    # n~100, the previous push-divided-by-(n-1) implementation left the large majority of
    # pairs still overlapping even after 1000 iterations. Benchmarked fix: zero overlapping
    # pairs at n in {100, 177, 250} using the default iteration count.
    import numpy as np

    rng = np.random.default_rng(0)
    n = 120
    lons = (rng.random(n) * 360 - 180).tolist()
    lats = (rng.random(n) * 140 - 70).tolist()
    values = (rng.random(n) ** 2 * 1000 + 1).tolist()
    padding = 0.04
    new_lon, new_lat, radius = dorling_layout(lons, lats, values, iterations=400, padding=padding)

    lat0 = math.radians(sum(lats) / n)
    cos_lat0 = max(0.15, math.cos(lat0))
    x = [lo * cos_lat0 for lo in new_lon]
    y = new_lat
    overlapping = 0
    for i in range(n):
        for j in range(i + 1, n):
            dist = math.hypot(x[i] - x[j], y[i] - y[j])
            if dist < (radius[i] + radius[j]) * (1 + padding) - 1e-6:
                overlapping += 1
    assert overlapping == 0
