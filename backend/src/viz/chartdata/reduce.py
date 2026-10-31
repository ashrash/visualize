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
    ident,
    lit,
    ordered,
    subquery,
    table,
    temporal_literal,
    window,
    with_ctes,
)
from viz.contracts import ColumnSemantic, TimeUnit
from viz.errors import InvalidSpecError
from viz.stats.relation import Relation

__all__ = [
    "agg_sql",
    "aggregate_query",
    "bin2d",
    "bucket_position",
    "epoch_ms_values",
    "hexbin",
    "m4_extent",
    "m4_extent_query",
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


def bucket_position(expression: exp.Expr, semantic: ColumnSemantic) -> exp.Expr:
    """x's position for M4 pixel bucketing: epoch microseconds (BIGINT) for temporal values,
    DOUBLE otherwise. At 10M rows `epoch_us(x)` with integer bucket math is ~40 ms cheaper
    than `epoch_ms(x)::DOUBLE` with float math. Infinite timestamps give NULL."""
    if semantic == ColumnSemantic.TEMPORAL:
        return fn("epoch_us", expression)
    return cast(expression, "DOUBLE")


def m4_extent_query(relation: Relation, x_dim: exp.Expr, semantic: ColumnSemantic) -> exp.Select:
    """`lo`, `hi` (x's extent in `bucket_position` units) and `n` (rows) of `relation`.

    The aggregates take x itself, not its position, so for a raw column with no filter (or
    filters DuckDB can prune by row group) the answer comes from Parquet statistics. `lo`/`hi`
    may come back NULL or non-finite (all-NULL x, NaN, infinite timestamps); `m4_extent`
    turns that into None, and `m4_query` then computes the extent itself.
    """
    lo, hi = fn("min", x_dim.copy()), fn("max", x_dim.copy())
    return relation.select(
        alias(bucket_position(lo, semantic), "lo"),
        alias(bucket_position(hi, semantic), "hi"),
        alias(exp.Count(this=exp.Star()), "n"),
    )


def m4_extent(row: dict[str, Any]) -> tuple[float, float] | None:
    """The (lo, hi) of an `m4_extent_query` row, or None when it isn't usable."""
    lo, hi = row.get("lo"), row.get("hi")
    if lo is None or hi is None or not (math.isfinite(lo) and math.isfinite(hi)) or lo > hi:
        return None
    return lo, hi


# Integer bucket math keeps (position - lo) * buckets below this; above it, DOUBLE math.
_INT_BUCKET_LIMIT = 2**62


def _literal_bucket(position: exp.Expr, lo: float, hi: float, buckets: int) -> exp.Expr:
    """The pixel column (0..buckets-1) of `position` for a known extent; NULL for NULL or
    non-finite positions, so those rows land in a group of their own that is dropped.

    Every branch needs the explicit `isfinite` guard: DuckDB's `least()` skips NULL
    arguments (`least(NULL, 49)` is 49), so without it a NULL x (an empty date cell, an
    infinite timestamp) would be bucketed into the last pixel column as a fake point.
    """
    if hi <= lo:  # one distinct x: every finite position is in column 0
        index: exp.Expr = lit(0)
    elif isinstance(lo, int) and isinstance(hi, int) and (hi - lo) * buckets < _INT_BUCKET_LIMIT:
        # Epoch microseconds: exact integer math.
        offset = exp.Paren(this=exp.Sub(this=position.copy(), expression=lit(lo)))
        quotient = exp.IntDiv(
            this=exp.Mul(this=offset, expression=lit(buckets)), expression=lit(hi - lo)
        )
        index = fn("least", quotient, lit(buckets - 1))
    else:
        offset = exp.Paren(this=exp.Sub(this=position.copy(), expression=lit(float(lo))))
        scaled = exp.Mul(this=offset, expression=lit(buckets / (float(hi) - float(lo))))
        index = fn("least", fn("floor", scaled), lit(float(buckets - 1)))
    return exp.Case(ifs=[exp.If(this=fn("isfinite", position.copy()), true=index)])


def _computed_bucket(position: exp.Expr, extent: str, buckets: int) -> exp.Expr:
    """The pixel column of `position` against the extent CTE `extent` (columns lo, hi)."""
    lo, hi = col("lo", extent), col("hi", extent)
    span = exp.Nullif(this=exp.Paren(this=exp.Sub(this=hi, expression=lo)), expression=lit(0))
    scaled = exp.Mul(
        this=exp.Div(
            this=exp.Paren(
                this=exp.Sub(this=cast(position.copy(), "DOUBLE"), expression=lo.copy())
            ),
            expression=span,
        ),
        expression=lit(buckets),
    )
    index = exp.Coalesce(
        this=fn("least", fn("floor", scaled), lit(buckets - 1)), expressions=[lit(0)]
    )
    return exp.Case(ifs=[exp.If(this=fn("isfinite", position.copy()), true=index)])


def m4_query(
    source: exp.Select,
    *,
    x: str,
    x_position: exp.Expr,
    ys: Sequence[str],
    series: str | None,
    buckets: int,
    extent: tuple[float, float] | None = None,
) -> exp.Select:
    """Rows of `source` that are M4 extremes (first/last x, min/max of each y) of a pixel
    column within their series, ordered by series then x. Every row also carries
    `__viz_groups`, the number of rows in `source`.

    `source` must have unique (series, x), and no column named `__viz_r`. `x_position` is
    an expression over `source`'s columns giving x's position (`bucket_position`, or the
    older DOUBLE `numeric_x`). Rows whose position is NULL or non-finite are left out.

    `extent` is (min, max) of the finite positions in those same units, e.g. from
    `m4_extent_query`, which reads it from Parquet statistics where it can. With it the query
    is one pipeline: aggregate `source`, then GROUP BY (series, pixel column) picking the
    extreme rows directly, with no materialized copy of `source` and no join back to it
    (~60-90 ms less at 10M groups than the earlier semi-join shape). Without it the extent is
    computed in SQL over a materialized `source` (still no join back).
    """
    buckets = max(1, int(buckets))
    row = "__viz_r"
    names = source.named_selects
    # One measure and nothing else: pick (x, y) pairs with scalar aggregates (~60 ms faster at
    # 10M groups than whole-row struct picks). With several measures or extra columns, each
    # pick must be one whole row: scalar arg_min(y1, y0) and arg_min(x, y0) could each take a
    # different row among ties in y0 and pair values that never occurred together.
    scalar = len(ys) == 1 and sorted(names) == sorted([x, ys[0], *([series] if series else [])])
    ctes: list[tuple[str, exp.Query]] = []
    if extent is not None:
        bucket = _literal_bucket(x_position, extent[0], extent[1], buckets)
        from_: exp.Expr = subquery(source, row)
        joined: exp.Expr | None = None
    else:
        finite = exp.Case(
            ifs=[
                exp.If(
                    this=fn("isfinite", x_position.copy()), true=cast(x_position.copy(), "DOUBLE")
                )
            ]
        )
        extent_sql = exp.select(
            alias(fn("min", finite), "lo"), alias(fn("max", finite.copy()), "hi")
        ).from_(table("__viz_a"))
        ctes = [("__viz_a", source), ("__viz_e", extent_sql)]
        bucket = _computed_bucket(x_position, "__viz_e", buckets)
        from_ = exp.Table(this=ident("__viz_a"), alias=exp.TableAlias(this=ident(row)))
        joined = table("__viz_e")

    keys = [col(series)] if series else []
    picks: list[exp.Expr] = []
    if scalar:
        y = ys[0]
        # (x, y) per pick: first and last x (arg_*_null keeps a NULL y there), then the
        # lowest and highest y (arg_min/arg_max skip NULL y). Ties in y pick an arbitrary
        # row; the points are within a pixel of each other, and responses are cached per
        # data version, so a chart doesn't flicker.
        pairs = [
            (fn("min", col(x)), fn("arg_min_null", col(y), col(x))),
            (fn("max", col(x)), fn("arg_max_null", col(y), col(x))),
            (fn("arg_min", col(x), col(y)), fn("min", col(y))),
            (fn("arg_max", col(x), col(y)), fn("max", col(y))),
        ]
        for i, (px, py) in enumerate(pairs):
            picks += [alias(px, f"__viz_x{i}"), alias(py, f"__viz_y{i}")]
        n_picks = len(pairs)
    else:
        # `__viz_r` names the source row: as a column it is the whole row as a STRUCT.
        whole = col(row)
        keyed = [col(x), *(col(y) for y in ys)]
        n_picks = 0
        for key in keyed:
            for agg in ("arg_min", "arg_max"):
                picks.append(alias(fn(agg, whole.copy(), key.copy()), f"__viz_q{n_picks}"))
                n_picks += 1
    groups = window(fn("sum", exp.Count(this=exp.Star())))
    pick_query = exp.select(
        *keys,
        alias(bucket, "__viz_bucket"),
        *picks,
        alias(cast(groups, "BIGINT"), "__viz_groups"),
    ).from_(from_)
    if joined is not None:
        pick_query = pick_query.join(joined, join_type="cross")
    pick_query = pick_query.group_by(*(k.copy() for k in keys), bucket.copy())

    if scalar:
        unpivot: dict[str, exp.Expr] = {
            x: fn("unnest", exp.Array(expressions=[col(f"__viz_x{i}") for i in range(n_picks)])),
            ys[0]: fn(
                "unnest", exp.Array(expressions=[col(f"__viz_y{i}") for i in range(n_picks)])
            ),
        }
        if series:
            unpivot[series] = col(series)
        # In `source`'s column order.
        points: list[exp.Expr] = [alias(unpivot[n], n) for n in names]
    else:
        recursive = exp.PropertyEQ(this=exp.var("recursive"), expression=exp.true())
        rows = exp.Array(expressions=[col(f"__viz_q{i}") for i in range(n_picks)])
        points = [exp.Anonymous(this="unnest", expressions=[rows, recursive])]
    distinct = (
        exp.select(*points, col("__viz_groups"))
        .from_(subquery(pick_query, "__viz_p"))
        # The NULL pixel column holds rows with a NULL or non-finite x (and, per series, its
        # count still goes into __viz_groups above).
        .where(exp.Not(this=exp.Is(this=col("__viz_bucket"), expression=exp.Null())))
        .distinct()
    )
    final = (
        exp.select(exp.Star())
        .from_(subquery(distinct, "__viz_m"))
        # A pick is NULL where a pixel column has no non-NULL y.
        .where(exp.Not(this=exp.Is(this=col(x), expression=exp.Null())))
        .order_by(*(ordered(k.copy()) for k in keys), ordered(col(x)))
    )
    return with_ctes(ctes, final)


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
