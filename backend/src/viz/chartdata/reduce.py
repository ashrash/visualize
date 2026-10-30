"""Data-reduction kernels shared by the generic chart path and plot modules.

All of them work on a `Relation` (filters already applied) and run in DuckDB, except the
final LTTB pass, which runs in Python on an already-small table (tsdownsample, Rust).

- `aggregate_query`: GROUP BY dimensions with measures.
- `m4_query`: per-series, per-pixel-column M4 (first/last x, min/max of every y), done in SQL
  so a 10M-point series is cut to ~4 points per pixel column before it leaves DuckDB.
- `minmaxlttb`: MinMaxLTTB to `n_out` points per series (tsdownsample).
- `bin2d`, `hexbin`: 2-D rectangular and hexagonal binning for over-cap scatters.
- `x_range_predicate`: the visible-range filter for detail-on-demand zoom.

stats owns 1-D histograms (`histogram`/`bin_edges`) and `reservoir_sample`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import JsonValue
from sqlglot import exp
from tsdownsample import MinMaxLTTBDownsampler, NaNMinMaxLTTBDownsampler

from viz.chartdata.sql import (
    agg_sql,
    alias,
    cast,
    col,
    fn,
    lit,
    subquery,
    table,
    temporal_literal,
    with_ctes,
)
from viz.contracts import ColumnSemantic, TimeUnit
from viz.errors import InvalidSpecError
from viz.stats.relation import Relation

__all__ = [
    "agg_sql",
    "aggregate_query",
    "bin2d",
    "epoch_ms_values",
    "hexbin",
    "m4_query",
    "minmaxlttb",
    "numeric_x",
    "over_groups",
    "warm_up",
    "x_range_predicate",
]


def numeric_x(expression: exp.Expr, semantic: ColumnSemantic) -> exp.Expr:
    """A DOUBLE position for an x value: epoch milliseconds (UTC) for temporal values.

    epoch_ms works on DATE, TIMESTAMP and TIMESTAMPTZ directly. Casting a TIMESTAMPTZ to
    TIMESTAMP first would shift it by the session's UTC offset.
    """
    if semantic == ColumnSemantic.TEMPORAL:
        return cast(fn("epoch_ms", expression), "DOUBLE")
    return cast(expression, "DOUBLE")


def epoch_ms_values(column: pa.ChunkedArray) -> np.ndarray[Any, np.dtype[np.float64]]:
    """An Arrow column as float64 positions: epoch milliseconds (UTC) for dates and
    timestamps (timezone-aware or not), the values themselves for numbers; NaN for nulls."""
    kind = column.type
    if pa.types.is_timestamp(kind):
        per_ms = {"s": 1e-3, "ms": 1.0, "us": 1e3, "ns": 1e6}[kind.unit]
        ints = pc.cast(column, pa.int64())  # the stored UTC instant, whatever the zone
        values = ints.to_numpy(zero_copy_only=False).astype(np.float64) / per_ms
    elif pa.types.is_date32(kind):
        days = pc.cast(column, pa.int32()).to_numpy(zero_copy_only=False).astype(np.float64)
        values = days * 86_400_000.0
    elif pa.types.is_date64(kind):
        values = pc.cast(column, pa.int64()).to_numpy(zero_copy_only=False).astype(np.float64)
    else:
        values = pc.cast(column, pa.float64()).to_numpy(zero_copy_only=False)
    valid = pc.is_valid(column).to_numpy(zero_copy_only=False)
    return np.where(valid, np.asarray(values, dtype=np.float64), np.nan)


def _range_bound(value: float | str, semantic: ColumnSemantic, duck_type: str | None) -> exp.Expr:
    if isinstance(value, int | float) and not math.isfinite(value):
        raise InvalidSpecError("the zoom range must be finite")
    if semantic == ColumnSemantic.TEMPORAL:
        # Numbers are epoch milliseconds (what ECharts time axes report).
        return temporal_literal(value, duck_type)
    return lit(float(value)) if isinstance(value, int | float) else lit(value)


def x_range_predicate(
    expression: exp.Expr,
    semantic: ColumnSemantic,
    x_range: tuple[float | str, float | str],
    time_unit: TimeUnit | None = None,
    duck_type: str | None = None,
) -> exp.Expr:
    """`expression` within the visible range, inclusive.

    `duck_type` is the x column's DuckDB type; TIMESTAMPTZ columns get absolute (UTC) bounds.
    With a time unit the expression is the truncated bucket, so the lower bound is truncated
    too: a bucket that starts before the visible range but overlaps it stays in.
    """
    lo, hi = x_range
    if type(lo) is type(hi) and lo > hi:  # type: ignore[operator]
        lo, hi = hi, lo
    lower = _range_bound(lo, semantic, duck_type)
    if time_unit is not None:
        lower = fn("date_trunc", lit(time_unit.value), lower)
    upper = _range_bound(hi, semantic, duck_type)
    return exp.Between(this=expression, low=lower, high=upper)


def aggregate_query(
    relation: Relation,
    dimensions: Sequence[tuple[str, exp.Expr]],
    measures: Sequence[tuple[str, exp.Expr]],
) -> exp.Select:
    """SELECT dims, measures FROM relation WHERE filters GROUP BY dims.

    Groups by the dimension *expressions*, not their aliases: in DuckDB `GROUP BY "x"` binds
    to a source column named x before the alias, so a source with columns called x or color
    would be grouped by the wrong thing. Reference the output aliases only from an outer
    query (see `over_groups`).
    """
    query = relation.select(
        *(alias(e, n) for n, e in dimensions), *(alias(e, n) for n, e in measures)
    )
    if dimensions:
        query = query.group_by(*(e.copy() for _, e in dimensions))
    return query


def over_groups(grouped: exp.Select) -> exp.Select:
    """SELECT * FROM (grouped): a scope where x, color, y0, ... mean the output columns."""
    return exp.select(exp.Star()).from_(subquery(grouped, "__viz_g"))


def m4_query(
    source: exp.Select,
    *,
    x: str,
    x_position: exp.Expr,
    ys: Sequence[str],
    series: str | None,
    buckets: int,
) -> exp.Select:
    """Rows of `source` that are M4 extremes (first/last x, min/max of each y) of a pixel
    column within their series. `source` must have unique (series, x); `x_position` is a
    DOUBLE expression over `source`'s columns giving x's position."""
    buckets = max(1, int(buckets))
    extent = exp.select(
        alias(fn("min", x_position.copy()), "lo"), alias(fn("max", x_position.copy()), "hi")
    ).from_(table("__viz_a"))
    span = exp.Nullif(
        this=exp.Paren(this=exp.Sub(this=col("hi", "__viz_e"), expression=col("lo", "__viz_e"))),
        expression=lit(0),
    )
    scaled = exp.Mul(
        this=exp.Div(
            this=exp.Paren(this=exp.Sub(this=x_position.copy(), expression=col("lo", "__viz_e"))),
            expression=span,
        ),
        expression=lit(buckets),
    )
    bucket = exp.Coalesce(
        this=fn("least", fn("floor", scaled), lit(buckets - 1)), expressions=[lit(0)]
    )
    bucketed = (
        exp.select(exp.Star(), alias(bucket, "__viz_bucket"))
        .from_(table("__viz_a"))
        .join(subquery(extent, "__viz_e"), join_type="cross")
    )
    keys = [col(series)] if series else []
    picks: list[exp.Expr] = [
        alias(fn("min", col(x)), "__viz_p0"),
        alias(fn("max", col(x)), "__viz_p1"),
    ]
    for i, y in enumerate(ys):
        # (y, x) breaks ties deterministically; the FILTER keeps NULL y out (a row value
        # with a NULL sorts as the largest).
        by = exp.Tuple(expressions=[col(y), col(x)])
        has_y = exp.Where(this=exp.Not(this=exp.Is(this=col(y), expression=exp.Null())))
        for j, name in enumerate(("arg_min", "arg_max")):
            pick = exp.Filter(this=fn(name, col(x), by.copy()), expression=has_y.copy())
            picks.append(alias(pick, f"__viz_p{2 + 2 * i + j}"))
    pick_query = (
        exp.select(*keys, *picks)
        .from_(table("__viz_b"))
        .group_by(*[col(series)] if series else [], col("__viz_bucket"))
    )
    point_list = exp.Array(expressions=[col(f"__viz_p{i}") for i in range(2 + 2 * len(ys))])
    xs = (
        exp.select(*keys, alias(fn("unnest", point_list), x)).from_(table("__viz_picks")).distinct()
    )
    condition: exp.Expr = exp.EQ(this=col(x, "__viz_a"), expression=col(x, "__viz_xs"))
    if series:
        condition = exp.and_(
            condition,
            exp.NullSafeEQ(this=col(series, "__viz_a"), expression=col(series, "__viz_xs")),
        )
    order = [col(series, "__viz_a")] if series else []
    final = (
        exp.select(exp.Star())
        .from_(table("__viz_a"))
        .join(table("__viz_xs"), on=condition, join_type="semi")
        .order_by(*order, col(x, "__viz_a"))
    )
    return with_ctes(
        [
            ("__viz_a", source),
            ("__viz_b", bucketed),
            ("__viz_picks", pick_query),
            ("__viz_xs", xs),
        ],
        final,
    )


def warm_up() -> None:
    """Run the downsampling path once at startup, so the first chart doesn't pay ~0.25 s of
    one-time initialization (tsdownsample, and pyarrow's timestamp cast/compute kernels)."""
    n = 4096
    values = np.sin(np.arange(n) / 10.0)
    values[7] = np.nan
    start = np.datetime64("2020-01-01T00:00:00", "us")
    for x in (
        pa.array(start + np.arange(n).astype("timedelta64[s]"), pa.timestamp("us")),
        pa.array(start + np.arange(n).astype("timedelta64[s]"), pa.timestamp("us", tz="UTC")),
        pa.array(np.arange(n, dtype=np.float64)),
    ):
        table = pa.table({"x": x, "y0": values, "color": pa.array(["a", "b"] * (n // 2))})
        minmaxlttb(table, x="x", ys=["y0"], series="color", n_out=256)
        minmaxlttb(table.drop_columns(["color"]), x="x", ys=["y0"], series=None, n_out=256)


def minmaxlttb(
    data: pa.Table, *, x: str, ys: Sequence[str], series: str | None, n_out: int
) -> tuple[pa.Table, bool]:
    """Keep at most ~n_out points per series and y (union over ys). Rows with a null x are
    dropped, as they can't be placed on a continuous axis. Returns (table, reduced)."""
    n_out = max(3, int(n_out))
    keep: list[int] = []
    reduced = False
    groups: dict[Any, list[int]] = {}
    series_values = data.column(series).to_pylist() if series else [None] * data.num_rows
    x_valid = pc.is_valid(data.column(x)).to_pylist()
    for i, key in enumerate(series_values):
        if x_valid[i]:
            groups.setdefault(key, []).append(i)
    positions_all = epoch_ms_values(data.column(x))
    for indices in groups.values():
        idx = np.asarray(indices, dtype=np.int64)
        if len(idx) <= n_out:
            keep.extend(indices)
            continue
        reduced = True
        order = idx[np.argsort(positions_all[idx], kind="stable")]
        xs = positions_all[order]
        chosen: set[int] = set()
        for y in ys:
            values = pc.cast(data.column(y), pa.float64()).to_numpy(zero_copy_only=False)[order]
            values = np.where(np.isfinite(values), values, np.nan)
            if np.isnan(values).all():
                continue
            # tsdownsample ships no type hints.
            sampler: Any = (
                NaNMinMaxLTTBDownsampler()  # type: ignore[no-untyped-call]
                if np.isnan(values).any()
                else MinMaxLTTBDownsampler()  # type: ignore[no-untyped-call]
            )
            picked = sampler.downsample(xs, values, n_out=n_out)
            chosen.update(int(order[j]) for j in picked)
        keep.extend(sorted(chosen, key=lambda j: positions_all[j]))
    if not reduced and len(keep) == data.num_rows:
        return data, False
    return data.take(pa.array(keep, pa.int64())), reduced


def _finite_pair(relation: Relation, x: exp.Expr, y: exp.Expr) -> exp.Select:
    """(x, y) as __viz_x/__viz_y; NULL, NaN and +/-inf pairs are dropped."""
    finite = exp.and_(fn("isfinite", x.copy()), fn("isfinite", y.copy()))
    return relation.narrow(finite).select(alias(x, "__viz_x"), alias(y, "__viz_y"))


def _extent_query(points: exp.Select) -> exp.Select:
    return with_ctes(
        [("__viz_pts", points)],
        exp.select(
            alias(fn("min", col("__viz_x")), "x0"),
            alias(fn("max", col("__viz_x")), "x1"),
            alias(fn("min", col("__viz_y")), "y0"),
            alias(fn("max", col("__viz_y")), "y1"),
            alias(exp.Count(this=exp.Star()), "n"),
        ).from_(table("__viz_pts")),
    )


def _scaled(value: str, lo: float, hi: float, size: float) -> exp.Expr:
    span = hi - lo
    if span <= 0 or not math.isfinite(span):
        return lit(0.0)
    return exp.Mul(
        this=exp.Div(
            this=exp.Paren(this=exp.Sub(this=col(value), expression=lit(lo))), expression=lit(span)
        ),
        expression=lit(float(size)),
    )


def bin2d(
    relation: Relation, x: exp.Expr, y: exp.Expr, *, bins_x: int, bins_y: int
) -> tuple[pa.Table, dict[str, JsonValue]]:
    """Rectangular 2-D bins of the finite (x, y) pairs. Columns x_lo, x_hi, y_lo, y_hi, count
    (empty bins omitted). `x` and `y` must be DOUBLE expressions (see numeric_x)."""
    points = _finite_pair(relation, x, y)
    ext = relation.arrow(_extent_query(points)).to_pylist()[0]
    params: dict[str, JsonValue] = {"binsX": bins_x, "binsY": bins_y, "n": ext["n"]}
    empty = pa.table(
        {k: pa.array([], pa.float64()) for k in ("x_lo", "x_hi", "y_lo", "y_hi")}
        | {"count": pa.array([], pa.int64())}
    )
    if not ext["n"]:
        return empty, params
    x0, x1, y0, y1 = (float(ext[k]) for k in ("x0", "x1", "y0", "y1"))
    i = fn("least", fn("floor", _scaled("__viz_x", x0, x1, bins_x)), lit(bins_x - 1))
    j = fn("least", fn("floor", _scaled("__viz_y", y0, y1, bins_y)), lit(bins_y - 1))
    counted = with_ctes(
        [("__viz_pts", points)],
        exp.select(
            alias(cast(i, "BIGINT"), "i"),
            alias(cast(j, "BIGINT"), "j"),
            alias(exp.Count(this=exp.Star()), "count"),
        )
        .from_(table("__viz_pts"))
        .group_by(col("i"), col("j"))
        .order_by(col("i"), col("j")),
    )
    raw = relation.arrow(counted)
    ii = raw.column("i").to_numpy(zero_copy_only=False).astype(np.float64)
    jj = raw.column("j").to_numpy(zero_copy_only=False).astype(np.float64)
    wx = (x1 - x0) / bins_x
    wy = (y1 - y0) / bins_y
    params |= {"xMin": x0, "xMax": x1, "yMin": y0, "yMax": y1, "binWidthX": wx, "binWidthY": wy}
    out = pa.table(
        {
            "x_lo": x0 + ii * wx,
            "x_hi": x0 + (ii + 1) * wx,
            "y_lo": y0 + jj * wy,
            "y_hi": y0 + (jj + 1) * wy,
            "count": raw.column("count"),
        }
    )
    return out, params


def hexbin(
    relation: Relation,
    x: exp.Expr,
    y: exp.Expr,
    *,
    width_px: int,
    height_px: int,
    radius_px: float,
) -> tuple[pa.Table, dict[str, JsonValue]]:
    """Pointy-top hexagonal bins in screen space. Columns x, y (cell centre, data units) and
    count. A point's cell is the nearer of its two candidate centres on the two offset
    rectangular lattices that together form the hexagonal lattice."""
    points = _finite_pair(relation, x, y)
    ext = relation.arrow(_extent_query(points)).to_pylist()[0]
    params: dict[str, JsonValue] = {"radiusPx": radius_px, "n": ext["n"]}
    if not ext["n"]:
        empty = {k: pa.array([], pa.float64()) for k in ("x", "y")}
        return pa.table(empty | {"count": pa.array([], pa.int64())}), params
    x0, x1, y0, y1 = (float(ext[k]) for k in ("x0", "x1", "y0", "y1"))
    dx = math.sqrt(3) * radius_px
    dy = 3 * radius_px  # vertical spacing of each rectangular lattice
    px, py = _scaled("__viz_x", x0, x1, width_px), _scaled("__viz_y", y0, y1, height_px)
    c1x = exp.Mul(this=fn("round", exp.Div(this=px.copy(), expression=lit(dx))), expression=lit(dx))
    c1y = exp.Mul(this=fn("round", exp.Div(this=py.copy(), expression=lit(dy))), expression=lit(dy))
    c2x = exp.Mul(
        this=exp.Paren(
            this=exp.Add(
                this=fn("floor", exp.Div(this=px.copy(), expression=lit(dx))), expression=lit(0.5)
            )
        ),
        expression=lit(dx),
    )
    c2y = exp.Mul(
        this=exp.Paren(
            this=exp.Add(
                this=fn("floor", exp.Div(this=py.copy(), expression=lit(dy))), expression=lit(0.5)
            )
        ),
        expression=lit(dy),
    )
    candidates = exp.select(
        alias(px, "px"),
        alias(py, "py"),
        alias(c1x, "c1x"),
        alias(c1y, "c1y"),
        alias(c2x, "c2x"),
        alias(c2y, "c2y"),
    ).from_(table("__viz_pts"))

    def dist(cx: str, cy: str) -> exp.Expr:
        return exp.Add(
            this=fn("pow", exp.Sub(this=col("px"), expression=col(cx)), lit(2)),
            expression=fn("pow", exp.Sub(this=col("py"), expression=col(cy)), lit(2)),
        )

    first = exp.LTE(this=dist("c1x", "c1y"), expression=dist("c2x", "c2y"))
    cx = exp.Case(ifs=[exp.If(this=first, true=col("c1x"))], default=col("c2x"))
    cy = exp.Case(ifs=[exp.If(this=first.copy(), true=col("c1y"))], default=col("c2y"))
    counted = with_ctes(
        [("__viz_pts", points), ("__viz_c", candidates)],
        exp.select(alias(cx, "cx"), alias(cy, "cy"), alias(exp.Count(this=exp.Star()), "count"))
        .from_(table("__viz_c"))
        .group_by(col("cx"), col("cy"))
        .order_by(col("cx"), col("cy")),
    )
    raw = relation.arrow(counted)
    cxs = raw.column("cx").to_numpy(zero_copy_only=False)
    cys = raw.column("cy").to_numpy(zero_copy_only=False)
    sx = (x1 - x0) / width_px if x1 > x0 else 0.0
    sy = (y1 - y0) / height_px if y1 > y0 else 0.0
    params |= {
        "xMin": x0,
        "xMax": x1,
        "yMin": y0,
        "yMax": y1,
        "widthPx": width_px,
        "heightPx": height_px,
        # Data units per pixel, so the renderer can draw cells at the right size.
        "xPerPx": sx,
        "yPerPx": sy,
    }
    out = pa.table({"x": x0 + cxs * sx, "y": y0 + cys * sy, "count": raw.column("count")})
    return out, params
