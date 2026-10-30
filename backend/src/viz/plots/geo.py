"""Geospatial plot stat transforms (Plots.md Section 7).

All five plot types are server-aggregated or server-sampled per CLAUDE.md Section 8:
- Choropleth: GROUP BY a region key, one measure. The number of distinct regions is bounded
  by the data itself (never more than the cardinality of the region column), so it has no
  cap of its own here; an unusually high-cardinality region column still goes through
  chartdata's generic post-cap truncation (`_finish_table`) like every other plot module.
- Bubble map: GROUP BY (lat, lon[, color]), one measure. When the grouped result would still
  be over `mark_cap` (distinct coordinates, e.g. raw GPS points with no shared locations),
  it falls back to a lat/lon grid (same binning as the heatmap) so whole regions of the map
  are never silently dropped by an arbitrary row cut.
- Dot density: a reservoir sample of raw (lat, lon[, color]) points, capped at mark_cap.
- Geographic heatmap: a lat/lon rectangular grid, binned in SQL.
- Cartogram: GROUP BY region for a value and a centroid, then a Dorling (non-overlapping
  circle) force layout, run in the engine's process pool (CLAUDE.md Section 6 concurrency
  model: "pure-Python-heavy work" goes to a worker, never inline in the request handler).
  The layout is O(n^2) per iteration, so the region count is capped before it ever reaches
  the worker (CARTOGRAM_MAX_REGIONS).

No basemap tiles and no GeoJSON live here: this module only produces rows. Matching those
rows to the bundled Natural Earth GeoJSON (ISO codes first, then normalized names) and
reporting unmatched regions happens in the frontend renderer, which is the only side that
has the map geometry (CLAUDE.md principle 6: local-first, cloud-ready; and the family notes
in .claude/agents/plots-geo.md: maps are a bundled offline asset, never sent to the server).
"""

from __future__ import annotations

import math
from concurrent.futures import Executor
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures.process import BrokenProcessPool
from typing import Literal

import numpy as np
import pyarrow as pa
from pydantic import Field
from sqlglot import exp

from viz.chartdata.sql import agg_sql, alias, cast, col, default_agg, fn, lit, measure_sql, ordered
from viz.contracts import AggFn, Encoding, PlotType, Reduction
from viz.engine.pool import ProcessPool
from viz.errors import EngineBusyError, QueryInterruptedError
from viz.plots.base import (
    CATEGORICAL,
    GEO,
    QUANT,
    Channel,
    PlotOutput,
    PlotParams,
    PlotRequest,
    TableOutput,
    register_plot,
)
from viz.stats.relation import Relation
from viz.stats.sampling import reservoir_sample

# Cartogram layout is O(n^2) per iteration; beyond this many regions it is both slow and
# memory-hungry (CLAUDE.md Section 6), so the server keeps only the largest-by-|value| this
# many and warns, the same pattern the generic aggregate path uses for top-N series.
CARTOGRAM_MAX_REGIONS = 1000
# A generous ceiling for the layout itself. The region cap above bounds the worst case to a
# few seconds (benchmarked: ~6.5s for 1000 regions at the default 400 iterations), so a
# timeout this size only ever fires for a genuinely stuck or overloaded worker.
CARTOGRAM_LAYOUT_TIMEOUT_S = 60.0

# Region keys: an ISO code or name column (tagged `geo` by the importer) or a plain
# categorical column (e.g. "state" without a recognized geo name).
REGION = GEO | CATEGORICAL
# lat/lon: tagged `geo` by the importer's name list, but accept plain numeric columns too
# (a differently named latitude/longitude pair still profiles as quantitative).
COORD = GEO | QUANT

_AGG_LABELS = {
    AggFn.COUNT: "Count",
    AggFn.COUNT_DISTINCT: "Distinct count",
    AggFn.SUM: "Sum",
    AggFn.MEAN: "Mean",
    AggFn.MEDIAN: "Median",
    AggFn.MIN: "Min",
    AggFn.MAX: "Max",
    AggFn.STD: "Std dev",
    AggFn.VAR: "Variance",
}


def _measure_label(encoding: Encoding | None) -> str:
    if encoding is None or encoding.field is None:
        return "Count"
    if encoding.label:
        return encoding.label
    return f"{_AGG_LABELS[default_agg(encoding)]} of {encoding.field}"


def _one(req: PlotRequest, channel: str) -> Encoding | None:
    items = req.spec.encodings.get(channel, [])
    bound = [e for e in items if e.field is not None]
    return bound[0] if bound else None


def _finite(*exprs: exp.Expr) -> exp.Expr:
    """`isfinite(a) AND isfinite(b) AND ...`: drop NULL, NaN and +/-inf coordinates before
    they reach either SQL aggregates (a single NULL poisons `avg`) or the Python layout."""
    conditions = [fn("isfinite", e) for e in exprs]
    result = conditions[0]
    for c in conditions[1:]:
        result = exp.and_(result, c)
    return result


def _aggregate_query(
    relation: Relation, dims: list[tuple[str, exp.Expr]], measures: list[tuple[str, exp.Expr]]
) -> exp.Select:
    return relation.select(
        *(alias(e, n) for n, e in dims), *(alias(e, n) for n, e in measures)
    ).group_by(*(e.copy() for _, e in dims))


def _aggregate(
    relation: Relation, dims: list[tuple[str, exp.Expr]], measures: list[tuple[str, exp.Expr]]
) -> pa.Table:
    return relation.arrow(_aggregate_query(relation, dims, measures))


def _auto_bins(size_px: int) -> int:
    # About one bin per 12 px, clamped to a sane range.
    return max(8, min(60, round(max(1, size_px) / 12)))


def _bin_index(value: exp.Expr, lo: float, width: float, bins: int) -> exp.Expr:
    shifted = exp.Paren(this=exp.Sub(this=value, expression=lit(lo)))
    scaled = exp.Div(this=shifted, expression=lit(width))
    return fn("least", fn("floor", scaled), lit(bins - 1))


def _grid_bin(
    relation: Relation,
    lon_expr: exp.Expr,
    lat_expr: exp.Expr,
    extra_dims: list[tuple[str, exp.Expr]],
    value: Encoding | None,
    *,
    bins_lon: int,
    bins_lat: int,
) -> tuple[pa.Table, int]:
    """Rows binned onto a lat/lon grid, optionally also split by `extra_dims` (e.g. color).
    Columns: `lon`, `lat` (cell centers), one column per extra dim, and `y0` (count, or the
    chosen aggregate of `value` when given). Shared by the geo heatmap and, when a bubble map
    has too many distinct coordinates to plot as-is, the bubble map's own fallback."""
    weighted = value is not None and value.field is not None
    finite = _finite(lon_expr.copy(), lat_expr.copy())
    selects = [alias(lon_expr, "__lon"), alias(lat_expr, "__lat")]
    selects += [alias(e, f"__{name}") for name, e in extra_dims]
    if weighted:
        assert value is not None and value.field is not None
        selects.append(alias(cast(col(value.field), "DOUBLE"), "__v"))
    points = relation.narrow(finite).select(*selects)
    extent = exp.select(
        alias(fn("min", col("__lon")), "x0"),
        alias(fn("max", col("__lon")), "x1"),
        alias(fn("min", col("__lat")), "y0"),
        alias(fn("max", col("__lat")), "y1"),
        alias(exp.Count(this=exp.Star()), "n"),
    ).from_(points.subquery("__pts"))
    ext = relation.arrow(extent).to_pylist()[0]
    n = int(ext["n"] or 0)
    if not n:
        empty = pa.table(
            {"lon": pa.array([], pa.float64()), "lat": pa.array([], pa.float64())}
            | {name: pa.array([], pa.string()) for name, _ in extra_dims}
            | {"y0": pa.array([], pa.float64())}
        )
        return empty, 0
    x0, x1, y0, y1 = (float(ext[k]) for k in ("x0", "x1", "y0", "y1"))
    wx = max((x1 - x0) / bins_lon, 1e-12)
    wy = max((y1 - y0) / bins_lat, 1e-12)
    i = _bin_index(col("__lon"), x0, wx, bins_lon)
    j = _bin_index(col("__lat"), y0, wy, bins_lat)
    measure = (
        exp.Count(this=exp.Star()) if not weighted else agg_sql(default_agg(value), col("__v"))  # type: ignore[arg-type]
    )
    group_cols = [alias(cast(i, "BIGINT"), "i"), alias(cast(j, "BIGINT"), "j")]
    group_cols += [alias(col(f"__{name}"), name) for name, _ in extra_dims]
    grouped = (
        exp.select(*group_cols, alias(cast(measure, "DOUBLE"), "y0"))
        .from_(points.subquery("__pts"))
        .group_by(col("i"), col("j"), *(col(name) for name, _ in extra_dims))
    )
    raw = relation.arrow(grouped)
    ii = raw.column("i").to_numpy(zero_copy_only=False).astype(np.float64)
    jj = raw.column("j").to_numpy(zero_copy_only=False).astype(np.float64)
    lon_centers = x0 + (ii + 0.5) * wx
    lat_centers = y0 + (jj + 0.5) * wy
    columns: dict[str, object] = {"lon": lon_centers, "lat": lat_centers}
    for name, _ in extra_dims:
        columns[name] = raw.column(name)
    columns["y0"] = raw.column("y0")
    return pa.table(columns), n


# -- Choropleth -------------------------------------------------------------------------------


class ChoroplethParams(PlotParams):
    # Which bundled map to join against. Purely a frontend/rendering choice: the backend
    # only groups by the region key, so this never changes the SQL.
    level: Literal["country", "us_state"] = "country"
    resolution: Literal["110m", "50m"] = "110m"


@register_plot(
    PlotType.CHOROPLETH,
    channels=[Channel("region", REGION), Channel("value", QUANT, min=0)],
    params=ChoroplethParams,
)
def choropleth(req: PlotRequest) -> PlotOutput:
    region_field = req.field("region")
    assert region_field is not None
    value = _one(req, "value")
    dims: list[tuple[str, exp.Expr]] = [("region", col(region_field))]
    measures: list[tuple[str, exp.Expr]] = [("y0", measure_sql(value or Encoding(agg=AggFn.COUNT)))]
    table = _aggregate(req.relation, dims, measures)
    return TableOutput(
        table=table,
        reduction=Reduction.AGGREGATED,
        method="group_by",
        fields={"region": region_field, "y0": _measure_label(value)},
        params={},
    )


# -- Bubble map -------------------------------------------------------------------------------


class BubbleMapParams(PlotParams):
    # The backdrop map behind the points. Purely a frontend/rendering choice.
    resolution: Literal["110m", "50m"] = "110m"


@register_plot(
    PlotType.BUBBLE_MAP,
    channels=[
        Channel("lat", COORD),
        Channel("lon", COORD),
        Channel("size", QUANT, min=0),
        Channel("color", CATEGORICAL, min=0),
    ],
    params=BubbleMapParams,
)
def bubble_map(req: PlotRequest) -> PlotOutput:
    lat_field = req.field("lat")
    lon_field = req.field("lon")
    assert lat_field is not None and lon_field is not None
    size = _one(req, "size")
    color = _one(req, "color")
    lat_expr = cast(col(lat_field), "DOUBLE")
    lon_expr = cast(col(lon_field), "DOUBLE")
    relation = req.relation.narrow(_finite(lat_expr.copy(), lon_expr.copy()))
    dims: list[tuple[str, exp.Expr]] = [("lat", lat_expr), ("lon", lon_expr)]
    fields = {"lat": lat_field, "lon": lon_field}
    color_dims: list[tuple[str, exp.Expr]] = []
    if color is not None:
        assert color.field is not None
        color_dims = [("color", col(color.field))]
        dims = dims + color_dims
        fields["color"] = color.label or color.field
    measures: list[tuple[str, exp.Expr]] = [("y0", measure_sql(size or Encoding(agg=AggFn.COUNT)))]
    fields["y0"] = _measure_label(size)
    # A cap+1 probe (CLAUDE.md's chartdata protocol pattern): most bubble maps aggregate a
    # modest, already-small set of shared locations (a handful of cities, say), so the common
    # case never pays for the grid-binning path below.
    probe = relation.arrow(_aggregate_query(relation, dims, measures).limit(req.mark_cap + 1))
    if probe.num_rows <= req.mark_cap:
        return TableOutput(
            table=probe, reduction=Reduction.AGGREGATED, method="group_by", fields=fields, params={}
        )
    # Distinct (lat, lon[, color]) tuples outnumber the cap (e.g. raw, mostly-unique GPS
    # points): a plain row cut would silently drop whole regions of the map. Snap to a grid
    # sized to the cap instead, same as the geographic heatmap.
    bins = max(2, int(math.sqrt(req.mark_cap)))
    table, n = _grid_bin(
        relation, lon_expr, lat_expr, color_dims, size, bins_lon=bins, bins_lat=bins
    )
    return TableOutput(
        table=table,
        reduction=Reduction.BINNED,
        method="geo_bin2d",
        fields=fields,
        params={"binsLon": bins, "binsLat": bins},
        rows_matched=n,
        warnings=[
            f"Too many distinct points to plot individually; grouped into a {bins}x{bins} grid."
        ],
    )


# -- Dot density ------------------------------------------------------------------------------


class DotDensityParams(PlotParams):
    # The backdrop map behind the points. Purely a frontend/rendering choice.
    resolution: Literal["110m", "50m"] = "110m"


@register_plot(
    PlotType.DOT_DENSITY,
    channels=[Channel("lat", COORD), Channel("lon", COORD), Channel("color", CATEGORICAL, min=0)],
    params=DotDensityParams,
)
def dot_density(req: PlotRequest) -> PlotOutput:
    lat_field = req.field("lat")
    lon_field = req.field("lon")
    assert lat_field is not None and lon_field is not None
    color_field = req.field("color")
    fields_to_sample = [lat_field, lon_field, *([color_field] if color_field else [])]
    sample = reservoir_sample(req.relation, fields_to_sample, cap=req.mark_cap)
    rename = {lat_field: "lat", lon_field: "lon"}
    if color_field:
        rename[color_field] = "color"
    table = sample.table.rename_columns([rename.get(n, n) for n in sample.table.column_names])
    fields = {"lat": lat_field, "lon": lon_field}
    if color_field:
        fields["color"] = color_field
    return TableOutput(
        table=table,
        reduction=Reduction.SAMPLED if sample.sampled else Reduction.NONE,
        method="reservoir" if sample.sampled else None,
        fields=fields,
        params={},
        rows_scanned=sample.n_total,
        rows_matched=sample.n_total,
        warnings=sample.notes(),
    )


# -- Geographic heatmap -------------------------------------------------------------------------


class GeoHeatmapParams(PlotParams):
    bins_lon: int | None = Field(default=None, ge=2, le=200)
    bins_lat: int | None = Field(default=None, ge=2, le=200)
    # The backdrop map behind the heat. Purely a frontend/rendering choice.
    resolution: Literal["110m", "50m"] = "110m"


@register_plot(
    PlotType.GEO_HEATMAP,
    channels=[Channel("lat", COORD), Channel("lon", COORD), Channel("value", QUANT, min=0)],
    params=GeoHeatmapParams,
)
def geo_heatmap(req: PlotRequest) -> PlotOutput:
    assert isinstance(req.params, GeoHeatmapParams)
    lat_field = req.field("lat")
    lon_field = req.field("lon")
    assert lat_field is not None and lon_field is not None
    value = _one(req, "value")
    bins_lon = req.params.bins_lon or _auto_bins(req.viewport.width_px)
    bins_lat = req.params.bins_lat or _auto_bins(req.viewport.height_px)
    table, extent_n = _grid_bin(
        req.relation,
        cast(col(lon_field), "DOUBLE"),
        cast(col(lat_field), "DOUBLE"),
        [],
        value,
        bins_lon=bins_lon,
        bins_lat=bins_lat,
    )
    return TableOutput(
        table=table,
        reduction=Reduction.BINNED,
        method="geo_bin2d",
        fields={"lon": lon_field, "lat": lat_field, "y0": _measure_label(value)},
        params={"binsLon": bins_lon, "binsLat": bins_lat},
        rows_matched=extent_n,
    )


# -- Cartogram --------------------------------------------------------------------------------


class CartogramParams(PlotParams):
    iterations: int = Field(default=400, ge=10, le=1000)
    padding: float = Field(default=0.04, ge=0.0, le=0.5)
    # The faint backdrop map behind the circles. Purely a frontend/rendering choice.
    resolution: Literal["110m", "50m"] = "110m"


@register_plot(
    PlotType.CARTOGRAM,
    channels=[
        Channel("region", REGION),
        Channel("value", QUANT),
        Channel("lat", COORD),
        Channel("lon", COORD),
    ],
    params=CartogramParams,
)
def cartogram(req: PlotRequest) -> PlotOutput:
    assert isinstance(req.params, CartogramParams)
    region_field = req.field("region")
    lat_field = req.field("lat")
    lon_field = req.field("lon")
    value = _one(req, "value")
    assert region_field is not None and lat_field is not None and lon_field is not None
    assert value is not None and value.field is not None
    lat_expr = cast(col(lat_field), "DOUBLE")
    lon_expr = cast(col(lon_field), "DOUBLE")
    # A region with no finite lat/lon anywhere in its rows would otherwise make `avg()`
    # return NULL, which crashes the (pure Python, NaN-unaware) layout below.
    relation = req.relation.narrow(_finite(lat_expr.copy(), lon_expr.copy()))
    dims: list[tuple[str, exp.Expr]] = [("region", col(region_field))]
    measures: list[tuple[str, exp.Expr]] = [
        ("lat", fn("avg", lat_expr)),
        ("lon", fn("avg", lon_expr)),
        ("y0", measure_sql(value)),
    ]
    fields = {"region": region_field, "y0": _measure_label(value)}
    warnings: list[str] = []
    cap = min(req.mark_cap, CARTOGRAM_MAX_REGIONS)
    # Rank and limit in SQL (not after fetching everything into Arrow): a high-cardinality
    # region column must never hand the O(n^2) layout below an unbounded number of regions.
    grouped = _aggregate_query(relation, dims, measures)
    ranked = (
        exp.select(exp.Star())
        .from_(grouped.subquery("__viz_g"))
        .order_by(ordered(fn("abs", col("y0")), desc=True))
        .limit(cap + 1)
    )
    table = relation.arrow(ranked)
    if table.num_rows > cap:
        table = table.slice(0, cap)
        warnings.append(
            f"Showing the {cap:,} largest of the {fields['region']} regions by "
            f"{fields['y0'].lower()}; filter to see the rest."
        )
    if table.num_rows == 0:
        empty = pa.table(
            {
                "region": pa.array([], pa.string()),
                "lon": pa.array([], pa.float64()),
                "lat": pa.array([], pa.float64()),
                "y0": pa.array([], pa.float64()),
                "radius": pa.array([], pa.float64()),
            }
        )
        return TableOutput(
            table=empty,
            reduction=Reduction.AGGREGATED,
            method="dorling",
            fields=fields,
            params={},
            warnings=warnings,
        )
    lons = table.column("lon").to_pylist()
    lats = table.column("lat").to_pylist()
    values = table.column("y0").to_pylist()
    new_lon, new_lat, radius = _run_dorling(
        req.executor, lons, lats, values, req.params.iterations, req.params.padding
    )
    out = pa.table(
        {
            "region": table.column("region"),
            "lon": pa.array(new_lon, pa.float64()),
            "lat": pa.array(new_lat, pa.float64()),
            "y0": table.column("y0"),
            "radius": pa.array(radius, pa.float64()),
        }
    )
    return TableOutput(
        table=out,
        reduction=Reduction.AGGREGATED,
        method="dorling",
        fields=fields,
        params={"iterations": req.params.iterations},
        warnings=warnings,
    )


def _run_dorling(
    executor: Executor | None,
    lons: list[float],
    lats: list[float],
    values: list[float],
    iterations: int,
    padding: float,
) -> tuple[list[float], list[float], list[float]]:
    """Run `dorling_layout` through `executor` when one is given (chartdata always passes the
    engine's `ProcessPool`; tests may pass a plain `Executor` or None). A worker that times out
    or dies raises `QueryInterruptedError`/`EngineBusyError` (clean 504/503s), never an
    unhandled 500, and `ProcessPool.run` also restarts the pool so a stuck layout never keeps
    starving the pool's other callers (bootstrap, regression) for good.
    """
    args = (lons, lats, values, iterations, padding)
    if executor is None:
        return dorling_layout(*args)
    if isinstance(executor, ProcessPool):
        return executor.run(dorling_layout, *args, timeout_s=CARTOGRAM_LAYOUT_TIMEOUT_S)
    future = executor.submit(dorling_layout, *args)
    try:
        return future.result(timeout=CARTOGRAM_LAYOUT_TIMEOUT_S)
    except FutureTimeoutError as exc:
        raise QueryInterruptedError("timed out") from exc
    except BrokenProcessPool as exc:
        raise EngineBusyError(
            "a worker process stopped while laying out the cartogram; try again"
        ) from exc


# Fraction of the layout's iterations spent purely resolving overlaps before the circles are
# pulled back toward their true position (see `dorling_layout`).
_DORLING_PHASE1_FRACTION = 0.6
# How far back toward the true centroid the pull moves each circle, in one step, between the
# two repulsion phases.
_DORLING_PULL_FRACTION = 0.5
# Target total circle area as a fraction of the bounding box area of the original centroids.
# Benchmarked (177/250/1000 uniformly scattered regions): 0.25 reliably resolves to zero
# overlapping pairs within a few hundred iterations; larger fractions pack too tightly to
# fully separate in a bounded iteration count.
_DORLING_AREA_FRACTION = 0.25


def _resolve_overlaps(
    x: np.ndarray, y: np.ndarray, radius: np.ndarray, padding: float, iterations: int
) -> tuple[np.ndarray, np.ndarray]:
    """Push overlapping circles apart until none overlap (or `iterations` runs out).

    Each pair's push is proportional only to its own overlap and is *summed*, not averaged,
    across neighbors: a circle moves only because of the (few) circles it actually overlaps,
    so dividing by the total circle count would needlessly weaken the repulsion as the map
    grows, which is what previously left most pairs still overlapping at n ~ 100-250.
    """
    for _ in range(max(1, iterations)):
        dx = x[:, None] - x[None, :]
        dy = y[:, None] - y[None, :]
        dist = np.sqrt(dx * dx + dy * dy)
        np.fill_diagonal(dist, np.inf)
        min_dist = (radius[:, None] + radius[None, :]) * (1 + padding)
        overlap = np.maximum(min_dist - dist, 0.0)
        if float(overlap.max()) < 1e-9:
            break
        safe_dist = np.where(dist > 1e-9, dist, 1.0)
        push_x = (dx / safe_dist) * overlap * 0.5
        push_y = (dy / safe_dist) * overlap * 0.5
        x = x + push_x.sum(axis=1)
        y = y + push_y.sum(axis=1)
    return x, y


def dorling_layout(
    lons: list[float], lats: list[float], values: list[float], iterations: int, padding: float
) -> tuple[list[float], list[float], list[float]]:
    """Non-overlapping circles (a Dorling cartogram): resolve overlaps by pairwise repulsion,
    pull the result partway back toward each circle's true position, then resolve overlaps
    again (the pull alone can reintroduce some). Top-level and picklable (runs in the engine's
    process pool): CLAUDE.md Section 6 ("pure-Python-heavy work" goes to a worker; workers
    start with `spawn`, so this function must be importable at module scope).

    Coordinates are an equirectangular approximation (longitude scaled by cos(mean latitude))
    so the layout is not badly distorted near the poles; it is illustrative, not a real
    projection (samples/README.md: the reference data here is for illustration, not GIS use).
    """
    n = len(lons)
    if n == 0:
        return [], [], []
    lat0 = math.radians(sum(lats) / n)
    cos_lat0 = max(0.15, math.cos(lat0))
    x0 = np.array([lo * cos_lat0 for lo in lons], dtype=np.float64)
    y0 = np.array(lats, dtype=np.float64)
    # Two circles starting at the exact same point have no direction to push apart along
    # (their difference vector is zero). A deterministic, negligible jitter breaks the tie
    # without noticeably moving any circle that didn't start exactly on top of another.
    jitter = np.arange(n, dtype=np.float64) * 1e-7
    x0 = x0 + jitter
    y0 = y0 + jitter[::-1]
    vals = np.array(
        [abs(v) if v is not None and math.isfinite(v) else 0.0 for v in values], dtype=np.float64
    )
    total_val = float(vals.sum()) or 1.0
    share = np.clip(vals / total_val * n, 1e-9, None)
    # Spread of the original centroids, so circle sizes are in the same units as x0/y0.
    x_span = float(x0.max() - x0.min()) if n > 1 else 1.0
    y_span = float(y0.max() - y0.min()) if n > 1 else 1.0
    span = max(x_span, y_span, 1e-6)
    target_area = math.pi * (span / 2) ** 2 * _DORLING_AREA_FRACTION
    k = math.sqrt(target_area / (math.pi * float(share.sum())))
    radius = k * np.sqrt(share)
    x, y = x0.copy(), y0.copy()
    if n > 1:
        phase1 = max(1, int(iterations * _DORLING_PHASE1_FRACTION))
        phase2 = max(1, iterations - phase1)
        x, y = _resolve_overlaps(x, y, radius, padding, phase1)
        x = x + (x0 - x) * _DORLING_PULL_FRACTION
        y = y + (y0 - y) * _DORLING_PULL_FRACTION
        x, y = _resolve_overlaps(x, y, radius, padding, phase2)
    new_lon = (x / cos_lat0).tolist()
    new_lat = y.tolist()
    return new_lon, new_lat, radius.tolist()
