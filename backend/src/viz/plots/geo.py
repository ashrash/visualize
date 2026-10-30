"""Geospatial plot stat transforms (Plots.md Section 7).

All five plot types are server-aggregated or server-sampled per CLAUDE.md Section 8:
- Choropleth: GROUP BY a region key, one measure.
- Bubble map: GROUP BY (lat, lon[, color]), one measure.
- Dot density: a reservoir sample of raw (lat, lon[, color]) points, capped at mark_cap.
- Geographic heatmap: a lat/lon rectangular grid, binned in SQL.
- Cartogram: GROUP BY region for a value and a centroid, then a Dorling (non-overlapping
  circle) force layout, run in the engine's process pool (CLAUDE.md Section 6 concurrency
  model: "pure-Python-heavy work" goes to a worker, never inline in the request handler).

No basemap tiles and no GeoJSON live here: this module only produces rows. Matching those
rows to the bundled Natural Earth GeoJSON (ISO codes first, then normalized names) and
reporting unmatched regions happens in the frontend renderer, which is the only side that
has the map geometry (CLAUDE.md principle 6: local-first, cloud-ready; and the family notes
in .claude/agents/plots-geo.md: maps are a bundled offline asset, never sent to the server).
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
import pyarrow as pa
from pydantic import Field
from sqlglot import exp

from viz.chartdata.sql import alias, cast, col, default_agg, fn, lit, measure_sql
from viz.contracts import AggFn, Encoding, PlotType, Reduction
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

_SQL_AGG = {
    AggFn.SUM: "sum",
    AggFn.MEAN: "avg",
    AggFn.MEDIAN: "median",
    AggFn.MIN: "min",
    AggFn.MAX: "max",
    AggFn.COUNT: "count",
    AggFn.COUNT_DISTINCT: "count",
    AggFn.STD: "stddev_samp",
    AggFn.VAR: "var_samp",
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


def _aggregate(
    relation: Relation, dims: list[tuple[str, exp.Expr]], measures: list[tuple[str, exp.Expr]]
) -> pa.Table:
    query = relation.select(
        *(alias(e, n) for n, e in dims), *(alias(e, n) for n, e in measures)
    ).group_by(*(e.copy() for _, e in dims))
    return relation.arrow(query)


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
    dims: list[tuple[str, exp.Expr]] = [("lat", col(lat_field)), ("lon", col(lon_field))]
    fields = {"lat": lat_field, "lon": lon_field}
    if color is not None:
        assert color.field is not None
        dims.append(("color", col(color.field)))
        fields["color"] = color.label or color.field
    measures: list[tuple[str, exp.Expr]] = [("y0", measure_sql(size or Encoding(agg=AggFn.COUNT)))]
    fields["y0"] = _measure_label(size)
    table = _aggregate(req.relation, dims, measures)
    return TableOutput(
        table=table, reduction=Reduction.AGGREGATED, method="group_by", fields=fields, params={}
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
    table, extent_n = _lat_lon_bins(
        req.relation, lon_field, lat_field, value, bins_lon=bins_lon, bins_lat=bins_lat
    )
    return TableOutput(
        table=table,
        reduction=Reduction.BINNED,
        method="geo_bin2d",
        fields={"lon": lon_field, "lat": lat_field, "y0": _measure_label(value)},
        params={"binsLon": bins_lon, "binsLat": bins_lat},
        rows_matched=extent_n,
    )


def _auto_bins(size_px: int) -> int:
    # About one bin per 12 px, clamped to a sane range.
    return max(8, min(60, round(max(1, size_px) / 12)))


def _bin_index(value: exp.Expr, lo: float, width: float, bins: int) -> exp.Expr:
    shifted = exp.Paren(this=exp.Sub(this=value, expression=lit(lo)))
    scaled = exp.Div(this=shifted, expression=lit(width))
    return fn("least", fn("floor", scaled), lit(bins - 1))


def _lat_lon_bins(
    relation: Relation,
    lon_field: str,
    lat_field: str,
    value: Encoding | None,
    *,
    bins_lon: int,
    bins_lat: int,
) -> tuple[pa.Table, int]:
    lon = cast(col(lon_field), "DOUBLE")
    lat = cast(col(lat_field), "DOUBLE")
    finite = exp.and_(fn("isfinite", lon.copy()), fn("isfinite", lat.copy()))
    weighted = value is not None and value.field is not None
    selects = [alias(lon, "__lon"), alias(lat, "__lat")]
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
            {
                "lon": pa.array([], pa.float64()),
                "lat": pa.array([], pa.float64()),
                "y0": pa.array([], pa.float64()),
            }
        )
        return empty, 0
    x0, x1, y0, y1 = (float(ext[k]) for k in ("x0", "x1", "y0", "y1"))
    wx = max((x1 - x0) / bins_lon, 1e-12)
    wy = max((y1 - y0) / bins_lat, 1e-12)
    i = _bin_index(col("__lon"), x0, wx, bins_lon)
    j = _bin_index(col("__lat"), y0, wy, bins_lat)
    measure = (
        exp.Count(this=exp.Star()) if not weighted else fn(_SQL_AGG[default_agg(value)], col("__v"))  # type: ignore[arg-type]
    )
    grouped = (
        exp.select(
            alias(cast(i, "BIGINT"), "i"),
            alias(cast(j, "BIGINT"), "j"),
            alias(cast(measure, "DOUBLE"), "y0"),
        )
        .from_(points.subquery("__pts"))
        .group_by(col("i"), col("j"))
    )
    raw = relation.arrow(grouped)
    ii = raw.column("i").to_numpy(zero_copy_only=False).astype(np.float64)
    jj = raw.column("j").to_numpy(zero_copy_only=False).astype(np.float64)
    lon_centers = x0 + (ii + 0.5) * wx
    lat_centers = y0 + (jj + 0.5) * wy
    out = pa.table({"lon": lon_centers, "lat": lat_centers, "y0": raw.column("y0")})
    return out, n


# -- Cartogram --------------------------------------------------------------------------------


class CartogramParams(PlotParams):
    iterations: int = Field(default=200, ge=10, le=1000)
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
    dims: list[tuple[str, exp.Expr]] = [("region", col(region_field))]
    measures: list[tuple[str, exp.Expr]] = [
        ("lat", fn("avg", cast(col(lat_field), "DOUBLE"))),
        ("lon", fn("avg", cast(col(lon_field), "DOUBLE"))),
        ("y0", measure_sql(value)),
    ]
    table = _aggregate(req.relation, dims, measures)
    fields = {"region": region_field, "y0": _measure_label(value)}
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
            table=empty, reduction=Reduction.AGGREGATED, method="dorling", fields=fields, params={}
        )
    lons = table.column("lon").to_pylist()
    lats = table.column("lat").to_pylist()
    values = table.column("y0").to_pylist()
    args = (lons, lats, values, req.params.iterations, req.params.padding)
    if req.executor is not None:
        future = req.executor.submit(dorling_layout, *args)
        new_lon, new_lat, radius = future.result(timeout=30.0)
    else:
        new_lon, new_lat, radius = dorling_layout(*args)
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
    )


def dorling_layout(
    lons: list[float], lats: list[float], values: list[float], iterations: int, padding: float
) -> tuple[list[float], list[float], list[float]]:
    """Non-overlapping circles (a Dorling cartogram), relaxed by pairwise repulsion plus a
    weak pull back to each circle's true position. Top-level and picklable (runs in the
    engine's process pool): CLAUDE.md Section 6 ("pure-Python-heavy work" goes to a worker;
    workers start with `spawn`, so this function must be importable at module scope).

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
    target_area = math.pi * (span / 2) ** 2 * 0.5
    k = math.sqrt(target_area / (math.pi * float(share.sum())))
    radius = k * np.sqrt(share)
    x, y = x0.copy(), y0.copy()
    if n > 1:
        for _ in range(max(1, iterations)):
            dx = x[:, None] - x[None, :]
            dy = y[:, None] - y[None, :]
            dist = np.sqrt(dx * dx + dy * dy)
            np.fill_diagonal(dist, np.inf)
            min_dist = (radius[:, None] + radius[None, :]) * (1 + padding)
            overlap = np.maximum(min_dist - dist, 0.0)
            # Each pair pushes both circles apart by half the overlap, along their axis.
            safe_dist = np.where(dist > 1e-9, dist, 1.0)
            push_x = (dx / safe_dist) * overlap * 0.5
            push_y = (dy / safe_dist) * overlap * 0.5
            x = x + push_x.sum(axis=1) / max(1, n - 1)
            y = y + push_y.sum(axis=1) / max(1, n - 1)
            # A gentle pull back toward the true centroid keeps the layout recognizable.
            x += (x0 - x) * 0.02
            y += (y0 - y) * 0.02
    new_lon = (x / cos_lat0).tolist()
    new_lat = y.tolist()
    return new_lon, new_lat, radius.tolist()
