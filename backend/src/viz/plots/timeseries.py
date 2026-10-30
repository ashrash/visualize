"""Plots.md Sec4 Time series: line, seasonal, lag, ACF, PACF, moving average, candlestick,
calendar heatmap.

Shared idea for the long-series plots (timeseries_line, moving_average, candlestick): group by
the raw x value first (matching the generic line chart). Only when that would exceed the mark
cap does a calendar bucket get picked automatically from the data's span and the viewport width
(`_auto_time_unit`), which is friendlier for a time axis than the pixel-column M4 bucketing the
generic aggregate path uses. If the bucketed series is still over the cap (many color groups),
MinMaxLTTB trims each series to the viewport's point budget, same as the generic line chart.

ACF/PACF run on the series after it has been aggregated to a *regular* calendar grid (gaps are
carried forward from the previous bucket, and the fill count is reported as a warning): both
assume evenly spaced observations.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field, JsonValue
from sqlglot import exp
from statsmodels.tsa.stattools import acf as sm_acf
from statsmodels.tsa.stattools import pacf as sm_pacf

from viz.chartdata.reduce import (
    aggregate_query,
    m4_query,
    minmaxlttb,
    numeric_x,
    over_groups,
)
from viz.chartdata.sql import (
    agg_sql,
    alias,
    cast,
    col,
    default_agg,
    fn,
    lit,
    measure_sql,
    ordered,
    subquery,
    table,
    with_ctes,
)
from viz.contracts import (
    AggFn,
    ColumnSemantic,
    Encoding,
    PlotType,
    Reduction,
    TimeUnit,
)
from viz.errors import InvalidSpecError
from viz.plots.base import (
    CATEGORICAL,
    QUANT,
    TEMPORAL,
    Channel,
    PlotParams,
    PlotRequest,
    TableOutput,
    register_plot,
)
from viz.stats.relation import Relation

DEFAULT_MAX_SERIES = 50
DEFAULT_SEED = 42

# ---------------------------------------------------------------------------------------------
# Shared time-bucketing helpers
# ---------------------------------------------------------------------------------------------

_UNIT_ORDER: tuple[TimeUnit, ...] = (
    TimeUnit.MINUTE,
    TimeUnit.HOUR,
    TimeUnit.DAY,
    TimeUnit.WEEK,
    TimeUnit.MONTH,
    TimeUnit.QUARTER,
    TimeUnit.YEAR,
)
_UNIT_MS: dict[TimeUnit, float] = {
    TimeUnit.MINUTE: 60_000.0,
    TimeUnit.HOUR: 3_600_000.0,
    TimeUnit.DAY: 86_400_000.0,
    TimeUnit.WEEK: 7 * 86_400_000.0,
    TimeUnit.MONTH: 30.4375 * 86_400_000.0,
    TimeUnit.QUARTER: 91.3125 * 86_400_000.0,
    TimeUnit.YEAR: 365.25 * 86_400_000.0,
}
# DuckDB's to_X(n) builders make an INTERVAL from a plain integer, so the unit never has to be
# spliced into SQL as a keyword.
_TO_INTERVAL_FN: dict[TimeUnit, str] = {
    TimeUnit.MINUTE: "to_minutes",
    TimeUnit.HOUR: "to_hours",
    TimeUnit.DAY: "to_days",
    TimeUnit.WEEK: "to_weeks",
    TimeUnit.MONTH: "to_months",
    TimeUnit.QUARTER: "to_quarters",
    TimeUnit.YEAR: "to_years",
}


def _auto_time_unit(span_ms: float, target_buckets: int) -> TimeUnit:
    """The finest unit that keeps the bucket count at or under `target_buckets`."""
    target = max(1, target_buckets)
    if span_ms <= 0:
        return TimeUnit.DAY
    for unit in _UNIT_ORDER:
        if span_ms / _UNIT_MS[unit] <= target:
            return unit
    return TimeUnit.YEAR


def _target_buckets(width_px: int, mark_cap: int, *, px_per_bucket: int = 3) -> int:
    """About one bucket every `px_per_bucket` pixels, but never more than the cap allows."""
    return max(10, min(max(1, width_px) // px_per_bucket, mark_cap))


def _x_span_ms(relation: Relation, x: exp.Expr) -> tuple[float, float] | None:
    position = numeric_x(x, ColumnSemantic.TEMPORAL)
    row = relation.rows(
        relation.select(alias(fn("min", position), "lo"), alias(fn("max", position.copy()), "hi"))
    )
    lo, hi = row[0]
    if lo is None:
        return None
    return float(lo), float(hi)


def _measure_label(encoding: Encoding, default_fn: AggFn = AggFn.SUM) -> str:
    if encoding.label:
        return encoding.label
    if encoding.field is None:
        return "Count"
    agg = encoding.agg or default_fn
    return f"{agg.value.replace('_', ' ').capitalize()} of {encoding.field}"


def _bucket_dimension(x_field: str, unit: TimeUnit | None) -> exp.Expr:
    if unit is None:
        return col(x_field)
    return fn("date_trunc", lit(unit.value), col(x_field))


def _drop_helpers(data: pa.Table) -> pa.Table:
    return data.drop_columns([n for n in data.column_names if n.startswith("__viz_")])


def _top_series(
    relation: Relation, color_dim: exp.Expr, y0: Encoding, limit: int
) -> tuple[list[object], bool]:
    """The colors (at most `limit`, largest |y0| first) and whether there were more."""
    grouped = aggregate_query(
        relation, [("color", color_dim)], [("__viz_size", fn("abs", measure_sql(y0)))]
    )
    query = (
        over_groups(grouped)
        .order_by(ordered(col("__viz_size"), desc=True), ordered(col("color")))
        .limit(limit + 1)
    )
    values = [row[0] for row in relation.rows(query)]
    return values[:limit], len(values) > limit


def _series_condition(color_dim: exp.Expr, keep: list[object]) -> exp.Expr:
    present = [lit(v) for v in keep if v is not None]  # type: ignore[arg-type]
    parts: list[exp.Expr] = []
    if present:
        parts.append(exp.In(this=color_dim.copy(), expressions=present))
    if any(v is None for v in keep):
        parts.append(exp.Is(this=color_dim.copy(), expression=exp.Null()))
    return exp.or_(*parts) if parts else exp.false()


@dataclass
class _Aggregated:
    table: pa.Table
    unit: TimeUnit | None
    auto_bucketed: bool


def _aggregate_series(
    relation: Relation,
    x_field: str,
    time_unit: TimeUnit | None,
    measures: list[tuple[str, exp.Expr]],
    color_dim: exp.Expr | None,
    *,
    mark_cap: int,
    width_px: int,
) -> _Aggregated:
    """GROUP BY x[, color], ordered. Buckets by a calendar unit only when needed.

    `time_unit` set by the caller (or the x encoding) always wins. Otherwise groups by the raw
    x value first; if that overflows the cap, a unit is auto-picked from the data's span and the
    viewport width (`_auto_time_unit`) and the grouping is redone.
    """
    dims: list[tuple[str, exp.Expr]] = [("x", _bucket_dimension(x_field, time_unit))]
    if color_dim is not None:
        dims.append(("color", color_dim))
    order = [ordered(col("x"))] + ([ordered(col("color"))] if color_dim is not None else [])

    grouped = aggregate_query(relation, dims, measures)
    probe = over_groups(grouped).order_by(*order).limit(mark_cap + 1)
    data = relation.arrow(probe)
    if time_unit is not None or data.num_rows <= mark_cap:
        return _Aggregated(data, time_unit, False)

    span = _x_span_ms(relation, col(x_field))
    unit = _auto_time_unit(span[1] - span[0] if span else 0.0, _target_buckets(width_px, mark_cap))
    dims[0] = ("x", _bucket_dimension(x_field, unit))
    grouped = aggregate_query(relation, dims, measures)
    data = relation.arrow(over_groups(grouped).order_by(*order))
    return _Aggregated(data, unit, True)


def _reduce_to_viewport(
    data: pa.Table,
    *,
    relation: Relation,
    dims: list[tuple[str, exp.Expr]],
    lean_measures: list[tuple[str, exp.Expr]],
    y_names: list[str],
    series_col: str | None,
    mark_cap: int,
    width_px: int,
) -> tuple[pa.Table, bool, int]:
    """If `data` is still over the cap, M4-bucket in SQL then MinMaxLTTB in Python.

    Returns (table, was_reduced, n_out).
    """
    n_series = len(set(data.column(series_col).to_pylist())) if series_col else 1
    n_out = max(3, min(2 * max(1, width_px), mark_cap // max(1, n_series)))
    if data.num_rows <= mark_cap:
        reduced, was_reduced = minmaxlttb(data, x="x", ys=y_names, series=series_col, n_out=n_out)
        return reduced, was_reduced, n_out
    source = aggregate_query(relation, dims, lean_measures)
    m4 = m4_query(
        source,
        x="x",
        x_position=numeric_x(col("x"), ColumnSemantic.TEMPORAL),
        ys=y_names,
        series=series_col,
        buckets=max(1, min(width_px, n_out)),
    )
    table_ = relation.arrow(m4)
    reduced, _ = minmaxlttb(table_, x="x", ys=y_names, series=series_col, n_out=n_out)
    return reduced, True, n_out


def _trailing_mean(values: np.ndarray, window: int) -> np.ndarray:
    """Mean of up to the trailing `window` finite values (inclusive of the current one).

    Vectorized with two cumulative sums so a capped series (at most `mark_cap` points) costs
    O(n) instead of an O(n * window) Python loop.
    """
    n = len(values)
    finite = np.isfinite(values)
    zeroed = np.where(finite, values, 0.0)
    csum = np.concatenate(([0.0], np.cumsum(zeroed)))
    ccount = np.concatenate(([0.0], np.cumsum(finite.astype(np.float64))))
    hi = np.arange(1, n + 1)
    lo = np.maximum(0, hi - window)
    window_sum = csum[hi] - csum[lo]
    window_count = ccount[hi] - ccount[lo]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(window_count > 0, window_sum / window_count, np.nan)


def _moving_average(data: pa.Table, window: int, series_col: str | None) -> pa.Table:
    """Append `y1`: the trailing moving average of `y0`, per `series_col` group when given.

    Assumes `data` is already ordered by x ascending (with x as the primary sort key, so each
    group's rows appear in x order even though they are interleaved with other groups).
    """
    y0 = pc.cast(data.column("y0"), pa.float64()).to_numpy(zero_copy_only=False)
    out = np.full(len(y0), np.nan, dtype=np.float64)
    groups = data.column(series_col).to_pylist() if series_col else [None] * data.num_rows
    by_group: dict[object, list[int]] = {}
    for i, g in enumerate(groups):
        by_group.setdefault(g, []).append(i)
    for indices in by_group.values():
        idx = np.asarray(indices, dtype=np.int64)
        out[idx] = _trailing_mean(y0[idx], window)
    return data.append_column("y1", pa.array(out, pa.float64()))


# ---------------------------------------------------------------------------------------------
# Time Series Line Plot
# ---------------------------------------------------------------------------------------------


class TimeSeriesLineParams(PlotParams):
    max_series: int = Field(default=DEFAULT_MAX_SERIES, ge=1, le=500)


@register_plot(
    PlotType.TIMESERIES_LINE,
    channels=[
        Channel("x", TEMPORAL, min=1, max=1),
        Channel("y", QUANT, min=0, max=None),
        Channel("color", CATEGORICAL, min=0, max=1),
    ],
    params=TimeSeriesLineParams,
)
def timeseries_line(req: PlotRequest) -> TableOutput:
    params = req.params
    assert isinstance(params, TimeSeriesLineParams)
    relation = req.relation
    x_field = req.field("x")
    assert x_field is not None
    x_enc = req.spec.encodings["x"][0]
    ys = list(req.spec.encodings.get("y", [])) or [Encoding(agg=AggFn.COUNT)]
    for y in ys:
        if y.field is None and default_agg(y) != AggFn.COUNT:
            raise InvalidSpecError(f"{default_agg(y).value} needs a field")
    color_encodings = req.spec.encodings.get("color", [])
    color_enc = color_encodings[0] if color_encodings and color_encodings[0].field else None

    warnings: list[str] = []
    out_params: dict[str, JsonValue] = {}
    fields = {"x": x_enc.label or x_field}
    color_dim: exp.Expr | None = None
    if color_enc is not None:
        assert color_enc.field is not None
        color_dim = col(color_enc.field)
        fields["color"] = color_enc.label or color_enc.field
        keep, more = _top_series(relation, color_dim, ys[0], params.max_series)
        if more:
            relation = relation.narrow(_series_condition(color_dim, keep))
            out_params["truncatedSeries"] = True
            warnings.append(
                f"Showing the {params.max_series} largest of the {fields['color']} series; "
                "filter to see the rest."
            )

    per_measure_cap = max(1, req.mark_cap // len(ys))
    measures: list[tuple[str, exp.Expr]] = []
    for i, y in enumerate(ys):
        measures.append((f"y{i}", measure_sql(y)))
        fields[f"y{i}"] = _measure_label(y)
    measures.append(("__viz_n", exp.Count(this=exp.Star())))

    agg = _aggregate_series(
        relation,
        x_field,
        x_enc.time_unit,
        measures,
        color_dim,
        mark_cap=per_measure_cap,
        width_px=req.viewport.width_px,
    )
    if agg.auto_bucketed:
        assert agg.unit is not None
        out_params["timeUnit"] = agg.unit.value
        warnings.append(
            f"Bucketed to {agg.unit.value} intervals to keep the chart under "
            f"{per_measure_cap:,} points per series."
        )

    y_names = [f"y{i}" for i in range(len(ys))]
    dims: list[tuple[str, exp.Expr]] = [("x", _bucket_dimension(x_field, agg.unit))]
    if color_dim is not None:
        dims.append(("color", color_dim))
    lean = [(n, e) for n, e in measures if n != "__viz_n"]
    rows_matched = int(pc.sum(agg.table.column("__viz_n")).as_py() or 0)
    data = _drop_helpers(agg.table)
    before_rows = data.num_rows
    reduced, was_reduced, n_out = _reduce_to_viewport(
        data,
        relation=relation,
        dims=dims,
        lean_measures=lean,
        y_names=y_names,
        series_col="color" if color_dim is not None else None,
        mark_cap=per_measure_cap,
        width_px=req.viewport.width_px,
    )
    if was_reduced:
        out_params["nOut"] = n_out
        method = "m4+minmaxlttb" if reduced.num_rows < before_rows else "group_by+minmaxlttb"
        reduction = Reduction.DOWNSAMPLED
    else:
        method = "date_trunc" if agg.unit is not None else "group_by"
        reduction = Reduction.AGGREGATED

    return TableOutput(
        table=reduced,
        reduction=reduction,
        method=method,
        fields=fields,
        params=out_params,
        warnings=warnings,
        rows_matched=rows_matched,
    )


# ---------------------------------------------------------------------------------------------
# Seasonal Plot
# ---------------------------------------------------------------------------------------------

_PERIOD_PART = {
    "month": "month",
    "weekday": "dow",
    "hour": "hour",
    "quarter": "quarter",
    "day_of_month": "day",
    "week": "week",
}
_PERIOD_LABEL = {
    "month": "Month",
    "weekday": "Day of week",
    "hour": "Hour of day",
    "quarter": "Quarter",
    "day_of_month": "Day of month",
    "week": "Week of year",
}
_CYCLE_PART = {"year": "year", "month": "month"}


class SeasonalParams(PlotParams):
    period: Literal["month", "weekday", "hour", "quarter", "day_of_month", "week"] = "month"
    cycle: Literal["year", "month", "none"] = "year"


@register_plot(
    PlotType.SEASONAL,
    channels=[Channel("x", TEMPORAL, min=1, max=1), Channel("y", QUANT, min=0, max=1)],
    params=SeasonalParams,
)
def seasonal(req: PlotRequest) -> TableOutput:
    params = req.params
    assert isinstance(params, SeasonalParams)
    relation = req.relation
    x_field = req.field("x")
    assert x_field is not None
    y_encodings = req.spec.encodings.get("y", [])
    y_enc = y_encodings[0] if y_encodings else Encoding(agg=AggFn.MEAN)
    if y_enc.field is None and default_agg(y_enc) not in (AggFn.COUNT, AggFn.MEAN):
        raise InvalidSpecError(f"{default_agg(y_enc).value} needs a field")
    if params.cycle != "none" and _CYCLE_PART[params.cycle] == _PERIOD_PART[params.period]:
        raise InvalidSpecError("the cycle and the period must be different units of time")

    period_expr = cast(fn("date_part", lit(_PERIOD_PART[params.period]), col(x_field)), "BIGINT")
    dims: list[tuple[str, exp.Expr]] = [("x", period_expr)]
    if params.cycle != "none":
        cycle_expr = cast(fn("date_part", lit(_CYCLE_PART[params.cycle]), col(x_field)), "BIGINT")
        dims.append(("color", cycle_expr))

    agg = y_enc.agg or AggFn.MEAN
    target = col(y_enc.field) if y_enc.field else None
    measure = cast(agg_sql(agg, target), "DOUBLE")
    measures: list[tuple[str, exp.Expr]] = [
        ("y0", measure),
        ("__viz_n", exp.Count(this=exp.Star())),
    ]

    grouped = aggregate_query(relation, dims, measures)
    # Ordered by cycle (series) first, then period, so each series' points are contiguous.
    order = ([ordered(col("color"))] if params.cycle != "none" else []) + [ordered(col("x"))]
    data = relation.arrow(over_groups(grouped).order_by(*order))
    rows_matched = int(pc.sum(data.column("__viz_n")).as_py() or 0)
    data = _drop_helpers(data)

    fields = {"x": _PERIOD_LABEL[params.period], "y0": _measure_label(y_enc, AggFn.MEAN)}
    if params.cycle != "none":
        fields["color"] = params.cycle.capitalize()

    return TableOutput(
        table=data,
        reduction=Reduction.AGGREGATED,
        method="date_part",
        fields=fields,
        params={"period": params.period, "cycle": params.cycle},
        rows_matched=rows_matched,
    )


# ---------------------------------------------------------------------------------------------
# Lag Plot
# ---------------------------------------------------------------------------------------------


class LagParams(PlotParams):
    lag: int = Field(default=1, ge=1, le=366)


def _seed_for(spec_id: str) -> int:
    # A stable seed per chart so repeated requests return the same sampled points.
    return (hash(spec_id) ^ DEFAULT_SEED) & 0xFFFFFFFF


@register_plot(
    PlotType.LAG,
    channels=[
        Channel("x", TEMPORAL, min=1, max=1),
        Channel("y", QUANT, min=1, max=1),
        Channel("color", CATEGORICAL, min=0, max=1),
    ],
    params=LagParams,
)
def lag(req: PlotRequest) -> TableOutput:
    params = req.params
    assert isinstance(params, LagParams)
    relation = req.relation
    time_field = req.field("x")
    value_field = req.field("y")
    assert time_field is not None and value_field is not None
    x_enc = req.spec.encodings["x"][0]
    y_enc = req.spec.encodings["y"][0]
    color_encodings = req.spec.encodings.get("color", [])
    color_field = color_encodings[0].field if color_encodings and color_encodings[0].field else None

    value = cast(col(value_field), "DOUBLE")
    finite = exp.Case(ifs=[exp.If(this=fn("isfinite", value), true=value.copy())])
    partition = [col(color_field)] if color_field else []
    lagged = exp.Window(
        this=fn("lag", finite.copy(), lit(params.lag)),
        partition_by=partition,
        order=exp.Order(expressions=[ordered(col(time_field))]),
        over="OVER",
    )
    projection: list[exp.Expr] = [alias(lagged, "x"), alias(finite.copy(), "y0")]
    if color_field:
        projection.append(alias(col(color_field), "color"))
    inner = relation.select(*projection)
    outer = (
        exp.select(exp.Star())
        .from_(subquery(inner, "__viz_lag"))
        .where(exp.and_(fn("isfinite", col("x")), fn("isfinite", col("y0"))))
    )
    n_total = int(
        relation.rows(exp.select(exp.Count(this=exp.Star())).from_(subquery(outer, "__viz_c")))[0][
            0
        ]
    )
    if n_total <= req.mark_cap:
        data = relation.arrow(outer)
        sampled = False
    else:
        seed = _seed_for(req.spec.id)
        sampled_sql = (
            f"SELECT * FROM ({outer.sql(dialect='duckdb')}) AS __viz_s "
            f"USING SAMPLE reservoir({int(req.mark_cap)} ROWS) REPEATABLE ({seed})"
        )
        data = relation.arrow(sampled_sql)
        sampled = True

    fields = {
        "x": f"{x_enc.label or time_field} (lag {params.lag})",
        "y0": y_enc.label or value_field,
    }
    if color_field:
        fields["color"] = color_encodings[0].label or color_field
    return TableOutput(
        table=data,
        reduction=Reduction.SAMPLED if sampled else Reduction.NONE,
        method="reservoir" if sampled else "lag",
        fields=fields,
        params={"lag": params.lag},
        warnings=[f"Showing a random sample of {req.mark_cap:,} of {n_total:,} points."]
        if sampled
        else [],
        rows_matched=n_total,
    )


# ---------------------------------------------------------------------------------------------
# ACF / PACF
# ---------------------------------------------------------------------------------------------


class AcfParams(PlotParams):
    lags: int | None = Field(default=None, ge=1, le=1000)
    ci_level: float = Field(default=0.95, gt=0, lt=1)
    time_unit: TimeUnit | None = None


def _regular_series(
    relation: Relation, time_field: str, value_field: str, unit: TimeUnit
) -> pa.Table:
    """Mean of `value_field` on a complete, evenly spaced calendar grid of `unit` buckets.

    Buckets with no rows come back as NULL (filled by the caller); the grid itself has no gaps.
    """
    bucket = cast(fn("date_trunc", lit(unit.value), col(time_field)), "TIMESTAMP")
    grouped = aggregate_query(
        relation, [("b", bucket)], [("val", fn("avg", cast(col(value_field), "DOUBLE")))]
    )
    bounds = exp.select(alias(fn("min", col("b")), "lo"), alias(fn("max", col("b")), "hi")).from_(
        table("__viz_g")
    )
    step = fn(_TO_INTERVAL_FN[unit], lit(1))
    grid = exp.select(
        alias(fn("unnest", fn("generate_series", col("lo"), col("hi"), step)), "b")
    ).from_(table("__viz_bounds"))
    final = (
        exp.select(col("b", "__viz_grid"), col("val", "__viz_g"))
        .from_(table("__viz_grid"))
        .join(
            table("__viz_g"),
            on=exp.EQ(this=col("b", "__viz_grid"), expression=col("b", "__viz_g")),
            join_type="left",
        )
        .order_by(col("b", "__viz_grid"))
    )
    query = with_ctes([("__viz_g", grouped), ("__viz_bounds", bounds), ("__viz_grid", grid)], final)
    return relation.arrow(query)


def _fill_gaps(values: list[float | None]) -> tuple[list[float], int]:
    """Forward-fill, then back-fill, missing (None/NaN) values. Returns (filled, n_filled)."""
    n = len(values)
    clean: list[float | None] = [v if v is not None and v == v else None for v in values]
    n_filled = sum(1 for v in clean if v is None)
    filled = list(clean)
    last: float | None = None
    for i in range(n):
        if filled[i] is None:
            filled[i] = last
        else:
            last = filled[i]
    nxt: float | None = None
    for i in range(n - 1, -1, -1):
        if filled[i] is None:
            filled[i] = nxt
        else:
            nxt = filled[i]
    return [v if v is not None else 0.0 for v in filled], n_filled


def _default_lags(n: int) -> int:
    return max(1, min(40, n // 2 - 1))


def _native_gap_ms(relation: Relation, time_field: str) -> float | None:
    """The smallest positive gap (ms) between consecutive distinct values of `time_field`.

    This is the data's own cadence (daily, hourly, ...), unlike `_auto_time_unit` which only
    picks a *coarser* bucket to keep a group count under a cap.
    """
    position = numeric_x(col(time_field), ColumnSemantic.TEMPORAL)
    distinct_ms = relation.select(alias(position, "ms")).distinct()
    gap = exp.Sub(
        this=col("ms"),
        expression=exp.Window(
            this=fn("lag", col("ms")),
            order=exp.Order(expressions=[ordered(col("ms"))]),
            over="OVER",
        ),
    )
    gaps = with_ctes(
        [("__viz_d", distinct_ms)],
        exp.select(alias(gap, "gap")).from_(table("__viz_d")),
    )
    positive = (
        exp.select(col("gap"))
        .from_(subquery(gaps, "__viz_g"))
        .where(exp.GT(this=col("gap"), expression=lit(0)))
    )
    result = relation.rows(
        exp.select(alias(fn("min", col("gap")), "min_gap")).from_(subquery(positive, "__viz_p"))
    )
    value = result[0][0] if result else None
    return float(value) if value is not None else None


def _pick_unit(relation: Relation, time_field: str, explicit: TimeUnit | None) -> TimeUnit:
    """The data's own calendar grain, rounded up to the nearest unit `date_trunc` supports."""
    if explicit is not None:
        return explicit
    gap = _native_gap_ms(relation, time_field)
    if gap is None:
        return TimeUnit.DAY
    for unit in _UNIT_ORDER:
        if _UNIT_MS[unit] >= gap - 1.0:  # a millisecond of slack for float rounding
            return unit
    return TimeUnit.YEAR


_AcfFn = Callable[[np.ndarray, int, float], tuple[np.ndarray, np.ndarray]]


def _acf_like(req: PlotRequest, *, label: str, method_name: str, run: _AcfFn) -> TableOutput:
    params = req.params
    assert isinstance(params, AcfParams)
    time_field = req.field("x")
    value_field = req.field("y")
    assert time_field is not None and value_field is not None
    unit = _pick_unit(req.relation, time_field, params.time_unit)
    series = _regular_series(req.relation, time_field, value_field, unit)
    n = series.num_rows
    if n < 4:
        raise InvalidSpecError(
            "Not enough points to compute autocorrelation; widen the date range or pick a "
            "coarser time unit."
        )
    raw = series.column("val").to_pylist()
    filled, n_filled = _fill_gaps(raw)
    lags = min(params.lags or _default_lags(n), n - 1)
    if lags < 1:
        raise InvalidSpecError("Not enough points for the requested number of lags.")
    alpha = 1 - params.ci_level
    values_arr, confint = run(np.asarray(filled, dtype=np.float64), lags, alpha)
    out = pa.table(
        {
            "x": pa.array(range(lags + 1), pa.int64()),
            "y0": pa.array(values_arr, pa.float64()),
            "y0_lo": pa.array(confint[:, 0], pa.float64()),
            "y0_hi": pa.array(confint[:, 1], pa.float64()),
        }
    )
    warnings: list[str] = []
    if n_filled:
        warnings.append(
            f"{n_filled} empty {unit.value} bucket(s) were carried forward from the previous "
            "value to keep the series evenly spaced."
        )
    return TableOutput(
        table=out,
        reduction=Reduction.AGGREGATED,
        method=method_name,
        fields={"x": "Lag", "y0": label, "y0_lo": f"{label} (low)", "y0_hi": f"{label} (high)"},
        params={"lags": lags, "timeUnit": unit.value, "ciLevel": params.ci_level},
        warnings=warnings,
    )


@register_plot(
    PlotType.ACF,
    channels=[Channel("x", TEMPORAL, min=1, max=1), Channel("y", QUANT, min=1, max=1)],
    params=AcfParams,
)
def acf_plot(req: PlotRequest) -> TableOutput:
    def run(values: np.ndarray, lags: int, alpha: float) -> tuple[np.ndarray, np.ndarray]:
        result = sm_acf(
            values,
            nlags=lags,
            alpha=alpha,
            fft=True,
            bartlett_confint=True,
            result_object=False,
        )
        assert isinstance(result, tuple)
        return result

    return _acf_like(req, label="Autocorrelation", method_name="statsmodels_acf", run=run)


@register_plot(
    PlotType.PACF,
    channels=[Channel("x", TEMPORAL, min=1, max=1), Channel("y", QUANT, min=1, max=1)],
    params=AcfParams,
)
def pacf_plot(req: PlotRequest) -> TableOutput:
    def run(values: np.ndarray, lags: int, alpha: float) -> tuple[np.ndarray, np.ndarray]:
        result = sm_pacf(values, nlags=lags, alpha=alpha, result_object=False)
        assert isinstance(result, tuple)
        return result

    return _acf_like(req, label="Partial autocorrelation", method_name="statsmodels_pacf", run=run)


# ---------------------------------------------------------------------------------------------
# Moving Average Plot
# ---------------------------------------------------------------------------------------------


class MovingAverageParams(PlotParams):
    window: int = Field(default=7, ge=2, le=1000)


@register_plot(
    PlotType.MOVING_AVERAGE,
    channels=[
        Channel("x", TEMPORAL, min=1, max=1),
        Channel("y", QUANT, min=0, max=1),
        Channel("color", CATEGORICAL, min=0, max=1),
    ],
    params=MovingAverageParams,
)
def moving_average(req: PlotRequest) -> TableOutput:
    params = req.params
    assert isinstance(params, MovingAverageParams)
    relation = req.relation
    x_field = req.field("x")
    assert x_field is not None
    x_enc = req.spec.encodings["x"][0]
    y_encodings = req.spec.encodings.get("y", [])
    y_enc = y_encodings[0] if y_encodings else Encoding(agg=AggFn.COUNT)
    if y_enc.field is None and default_agg(y_enc) != AggFn.COUNT:
        raise InvalidSpecError(f"{default_agg(y_enc).value} needs a field")
    color_encodings = req.spec.encodings.get("color", [])
    color_enc = color_encodings[0] if color_encodings and color_encodings[0].field else None
    color_dim = col(color_enc.field) if color_enc and color_enc.field else None
    series_col = "color" if color_dim is not None else None

    fields = {"x": x_enc.label or x_field, "y0": _measure_label(y_enc)}
    if color_enc is not None and color_enc.field:
        fields["color"] = color_enc.label or color_enc.field
    measures: list[tuple[str, exp.Expr]] = [
        ("y0", measure_sql(y_enc)),
        ("__viz_n", exp.Count(this=exp.Star())),
    ]
    agg = _aggregate_series(
        relation,
        x_field,
        x_enc.time_unit,
        measures,
        color_dim,
        mark_cap=req.mark_cap,
        width_px=req.viewport.width_px,
    )
    out_params: dict[str, JsonValue] = {"window": params.window}
    warnings: list[str] = []
    if agg.auto_bucketed:
        assert agg.unit is not None
        out_params["timeUnit"] = agg.unit.value
        warnings.append(
            f"Bucketed to {agg.unit.value} intervals to keep the chart under "
            f"{req.mark_cap:,} points."
        )
    rows_matched = int(pc.sum(agg.table.column("__viz_n")).as_py() or 0)
    data = _drop_helpers(agg.table)
    data = _moving_average(data, params.window, series_col)
    fields["y1"] = f"{params.window}-point moving average"

    if data.num_rows > req.mark_cap:
        # Rare: even the coarsest calendar bucket didn't fit alongside this many color groups.
        data = data.slice(0, req.mark_cap)
        out_params["truncatedGroups"] = True
        warnings.append(
            f"Showing the first {req.mark_cap:,} points; narrow the data with a filter."
        )
        reduction, method = Reduction.AGGREGATED, "date_trunc" if agg.unit else "group_by"
    else:
        n_series = len(set(data.column("color").to_pylist())) if series_col else 1
        n_out = max(3, min(2 * max(1, req.viewport.width_px), req.mark_cap // max(1, n_series)))
        reduced, was_reduced = minmaxlttb(
            data, x="x", ys=["y0", "y1"], series=series_col, n_out=n_out
        )
        data = reduced
        if was_reduced:
            out_params["nOut"] = n_out
            reduction, method = Reduction.DOWNSAMPLED, "group_by+minmaxlttb"
        else:
            reduction = Reduction.AGGREGATED
            method = "date_trunc" if agg.unit is not None else "group_by"

    return TableOutput(
        table=data,
        reduction=reduction,
        method=method,
        fields=fields,
        params=out_params,
        warnings=warnings,
        rows_matched=rows_matched,
    )


# ---------------------------------------------------------------------------------------------
# Candlestick Chart
# ---------------------------------------------------------------------------------------------


@register_plot(
    PlotType.CANDLESTICK,
    channels=[
        Channel("x", TEMPORAL, min=1, max=1),
        Channel("open", QUANT, min=1, max=1),
        Channel("high", QUANT, min=1, max=1),
        Channel("low", QUANT, min=1, max=1),
        Channel("close", QUANT, min=1, max=1),
        Channel("volume", QUANT, min=0, max=1),
    ],
)
def candlestick(req: PlotRequest) -> TableOutput:
    relation = req.relation
    x_field = req.field("x")
    open_field, high_field, low_field, close_field = (
        req.field("open"),
        req.field("high"),
        req.field("low"),
        req.field("close"),
    )
    volume_field = req.field("volume")
    assert x_field and open_field and high_field and low_field and close_field
    x_enc = req.spec.encodings["x"][0]
    order_col = col(x_field)
    measures: list[tuple[str, exp.Expr]] = [
        ("open", cast(fn("arg_min", col(open_field), order_col.copy()), "DOUBLE")),
        ("high", cast(fn("max", col(high_field)), "DOUBLE")),
        ("low", cast(fn("min", col(low_field)), "DOUBLE")),
        ("close", cast(fn("arg_max", col(close_field), order_col.copy()), "DOUBLE")),
        ("__viz_n", exp.Count(this=exp.Star())),
    ]
    if volume_field:
        measures.append(("volume", cast(fn("sum", col(volume_field)), "DOUBLE")))

    agg = _aggregate_series(
        relation,
        x_field,
        x_enc.time_unit,
        measures,
        None,
        mark_cap=req.mark_cap,
        width_px=max(1, req.viewport.width_px // 6),
    )
    data = agg.table
    rows_matched = int(pc.sum(data.column("__viz_n")).as_py() or 0)
    max_per_bar = int(pc.max(data.column("__viz_n")).as_py() or 0)
    data = _drop_helpers(data)
    warnings: list[str] = []
    out_params: dict[str, JsonValue] = {}
    if agg.unit is None and max_per_bar > 1:
        warnings.append(
            "More than one row shares the same timestamp, so their open/high/low/close were "
            "combined. Filter to a single instrument, or pick a time bucket, for correct bars."
        )
    if agg.auto_bucketed:
        assert agg.unit is not None
        out_params["timeUnit"] = agg.unit.value
        warnings.append(
            f"Bucketed to {agg.unit.value} bars to keep the chart under {req.mark_cap:,} bars."
        )
    if data.num_rows > req.mark_cap:
        data = data.slice(0, req.mark_cap)
        out_params["truncatedGroups"] = True
        warnings.append(f"Showing the first {req.mark_cap:,} bars; narrow the data with a filter.")

    fields = {
        "x": x_enc.label or x_field,
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
    }
    if volume_field:
        fields["volume"] = "Volume"
    return TableOutput(
        table=data,
        reduction=Reduction.DOWNSAMPLED if agg.auto_bucketed else Reduction.AGGREGATED,
        method="arg_min_max" if agg.unit is None else "date_trunc+arg_min_max",
        fields=fields,
        params=out_params,
        warnings=warnings,
        rows_matched=rows_matched,
    )


# ---------------------------------------------------------------------------------------------
# Calendar Heatmap
# ---------------------------------------------------------------------------------------------


@register_plot(
    PlotType.CALENDAR_HEATMAP,
    channels=[Channel("x", TEMPORAL, min=1, max=1), Channel("y", QUANT, min=0, max=1)],
)
def calendar_heatmap(req: PlotRequest) -> TableOutput:
    relation = req.relation
    x_field = req.field("x")
    assert x_field is not None
    x_enc = req.spec.encodings["x"][0]
    y_encodings = req.spec.encodings.get("y", [])
    y_enc = y_encodings[0] if y_encodings else Encoding(agg=AggFn.COUNT)
    if y_enc.field is None and default_agg(y_enc) != AggFn.COUNT:
        raise InvalidSpecError(f"{default_agg(y_enc).value} needs a field")

    day = cast(fn("date_trunc", lit("day"), col(x_field)), "DATE")
    measures: list[tuple[str, exp.Expr]] = [
        ("y0", measure_sql(y_enc)),
        ("__viz_n", exp.Count(this=exp.Star())),
    ]
    grouped = aggregate_query(relation, [("x", day)], measures)
    data = relation.arrow(over_groups(grouped).order_by(ordered(col("x"))))
    rows_matched = int(pc.sum(data.column("__viz_n")).as_py() or 0)
    data = _drop_helpers(data)

    fields = {"x": x_enc.label or x_field, "y0": _measure_label(y_enc)}
    return TableOutput(
        table=data,
        reduction=Reduction.AGGREGATED,
        method="daily",
        fields=fields,
        params={},
        rows_matched=rows_matched,
    )
