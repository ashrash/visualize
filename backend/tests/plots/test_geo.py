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
