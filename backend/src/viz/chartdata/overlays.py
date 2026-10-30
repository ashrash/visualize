"""Trendline overlays: POST /api/charts/overlays -> one OverlayResult per style.trendlines[i].

What gets fitted:
- Aggregate charts (the basic family) fit the points the chart draws: the aggregated y{series}
  against x. Temporal x is epoch milliseconds; a categorical x is the category's position
  (0, 1, 2, ... in the chart's order).
- Charts with a plot module fit the raw (x, y[series]) rows, on stats' reservoir sample above
  `MODEL_CAP` rows (`sampled`, `n`).

Fits come from stats' kernels: `ols_curve` for linear, polynomial(degree), exponential
(ln y on x), logarithmic (y on ln x) and power (ln y on ln x), with r2 in the fitted (log)
space as spreadsheets report it; `lowess_curve` for LOESS, with a bootstrap band. A trailing
moving average is computed here. `ciLevel` adds a confidence band for the mean. Temporal x
has no equation (its coefficients would be per millisecond). A fit the data can't support
(too few points, no variation) returns an empty curve with a warning instead of failing the
whole request.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from concurrent.futures import Executor
from dataclasses import dataclass

import numpy as np
import pyarrow as pa
from sqlalchemy.orm import Session

from viz.chartdata.basic import AGGREGATE_PLOTS
from viz.chartdata.reduce import epoch_ms_values
from viz.chartdata.service import chart_data
from viz.contracts import (
    ChartDataRequest,
    ColumnSemantic,
    OverlayResult,
    Trendline,
    TrendlineKind,
)
from viz.engine import Engine
from viz.errors import InvalidSpecError
from viz.plots import get_plot
from viz.stats.errors import StatsInputError
from viz.stats.regression import FitCurve, lowess_curve, ols_curve
from viz.stats.relation import Relation
from viz.stats.sampling import MODEL_CAP, reservoir_sample
from viz.storage import LocalStorage
from viz.transform.source import resolve_field, resolve_source

GRID_POINTS = 200

Array = np.ndarray


@dataclass
class Points:
    x: Array
    y: Array
    temporal: bool
    sampled: bool
    n_total: int


def _empty(trend: Trendline, n: int, sampled: bool, warning: str) -> OverlayResult:
    return OverlayResult(
        kind=trend.kind,
        series=trend.series,
        x=[],
        y=[],
        lo=None,
        hi=None,
        equation=None,
        r2=None,
        n=n,
        sampled=sampled,
        warnings=[warning],
    )


def _clean(x: Array, y: Array) -> tuple[Array, Array]:
    keep = np.isfinite(x) & np.isfinite(y)
    return x[keep], y[keep]


def _floats(column: pa.ChunkedArray) -> Array:
    """Numbers as float64; dates and timestamps (with or without a zone) as UTC epoch ms."""
    return epoch_ms_values(column)


def overlay_key(request: ChartDataRequest) -> str:
    """Overlays get their own cancellation slot: an overlay request must not supersede the
    chart's data request (and vice versa)."""
    return f"chart:overlay:{request.slot or request.spec.id}"


def _aggregated_points(
    engine: Engine,
    storage: LocalStorage,
    db: Session,
    request: ChartDataRequest,
    series: int,
    mark_cap: int,
) -> Points:
    # The cache key ignores the slot, so this usually reuses the chart's cached response.
    result = chart_data(engine, storage, db, request, mark_cap=mark_cap)
    table = result.table
    if table is None or f"y{series}" not in table.column_names:
        raise InvalidSpecError(f"the chart has no y series {series}")
    x_column = table.column("x")
    temporal = pa.types.is_timestamp(x_column.type) or pa.types.is_date(x_column.type)
    numeric = temporal or pa.types.is_integer(x_column.type) or pa.types.is_floating(x_column.type)
    x = _floats(x_column) if numeric else np.arange(table.num_rows, dtype=np.float64)
    y = _floats(table.column(f"y{series}"))
    x, y = _clean(x, y)
    order = np.argsort(x, kind="stable")
    return Points(x[order], y[order], temporal, False, len(x))


def _raw_points(relation: Relation, request: ChartDataRequest, series: int) -> Points:
    spec = request.spec
    xs, ys = spec.encodings.get("x", []), spec.encodings.get("y", [])
    x_field = xs[0].field if xs else None
    y_field = ys[series].field if 0 <= series < len(ys) else None
    if x_field is None or y_field is None:
        raise InvalidSpecError("a trendline needs an x field and the y field it follows")
    x_col = relation.columns[resolve_field(relation.columns, x_field)]
    y_col = relation.columns[resolve_field(relation.columns, y_field)]
    if y_col.semantic != ColumnSemantic.QUANTITATIVE:
        raise InvalidSpecError("a trendline needs a numeric y field")
    if x_col.semantic not in (ColumnSemantic.QUANTITATIVE, ColumnSemantic.TEMPORAL):
        raise InvalidSpecError("a trendline needs a numeric or date x field")
    sample = reservoir_sample(relation, [x_col.name, y_col.name], cap=MODEL_CAP)
    x = _floats(sample.table.column(x_col.name))
    y = _floats(sample.table.column(y_col.name))
    x, y = _clean(x, y)
    order = np.argsort(x, kind="stable")
    temporal = x_col.semantic == ColumnSemantic.TEMPORAL
    return Points(x[order], y[order], temporal, sample.sampled, sample.n_total)


def _fmt(value: float) -> str:
    if value == 0 or not math.isfinite(value):
        return "0" if value == 0 else str(value)
    return f"{value:.4g}"


def _poly_equation(coefficients: Sequence[float]) -> str:
    terms: list[str] = []
    for power in range(len(coefficients) - 1, -1, -1):
        c = coefficients[power]
        if c == 0:
            continue
        magnitude = _fmt(abs(c))
        body = (
            magnitude
            if power == 0
            else (("" if magnitude == "1" else magnitude) + ("x" if power == 1 else f"x^{power}"))
        )
        sign = "-" if c < 0 else "+"
        terms.append(f"{sign} {body}" if terms else ("-" if c < 0 else "") + body)
    return "y = " + (" ".join(terms) if terms else "0")


def _equation(kind: TrendlineKind, coef: Sequence[float]) -> str | None:
    """Equation text from the fitted-space coefficients b0..bd (original units)."""
    if not coef:
        return None
    if kind in (TrendlineKind.LINEAR, TrendlineKind.POLYNOMIAL):
        return _poly_equation(coef)
    a, b = coef[0], coef[1]
    if kind == TrendlineKind.EXPONENTIAL:
        return f"y = {_fmt(math.exp(a))}·e^({_fmt(b)}x)"
    if kind == TrendlineKind.LOGARITHMIC:
        return f"y = {_fmt(a)} {'-' if b < 0 else '+'} {_fmt(abs(b))}·ln(x)"
    return f"y = {_fmt(math.exp(a))}·x^{_fmt(b)}"


def _result(
    trend: Trendline,
    points: Points,
    x: Array,
    y: Array,
    lo: Array | None,
    hi: Array | None,
    *,
    equation: str | None,
    r2: float | None,
    n: int,
    warnings: list[str],
) -> OverlayResult:
    keep = np.isfinite(x) & np.isfinite(y)
    if lo is not None and hi is not None:
        keep &= np.isfinite(lo) & np.isfinite(hi)
    return OverlayResult(
        kind=trend.kind,
        series=trend.series,
        x=x[keep].tolist(),
        y=y[keep].tolist(),
        lo=lo[keep].tolist() if lo is not None else None,
        hi=hi[keep].tolist() if hi is not None else None,
        equation=None if points.temporal else equation,
        r2=r2 if r2 is None or math.isfinite(r2) else None,
        n=n,
        sampled=points.sampled,
        warnings=warnings,
    )


def _moving_average(trend: Trendline, points: Points) -> OverlayResult:
    window = trend.window or 5
    if window < 1:
        raise InvalidSpecError("the moving-average window must be at least 1")
    if len(points.y) < window:
        return _empty(trend, len(points.y), points.sampled, f"Needs at least {window} points.")
    averaged = np.convolve(points.y, np.ones(window) / window, mode="valid")
    return _result(
        trend,
        points,
        points.x[window - 1 :],
        averaged,
        None,
        None,
        equation=None,
        r2=None,
        n=len(points.y),
        warnings=[],
    )


def fit_trendline(
    trend: Trendline, points: Points, *, executor: Executor | None = None
) -> OverlayResult:
    kind = trend.kind
    if kind == TrendlineKind.MOVING_AVERAGE:
        return _moving_average(trend, points)
    try:
        fit: FitCurve
        if kind == TrendlineKind.LOESS:
            fit = lowess_curve(
                points.x,
                points.y,
                ci_level=trend.ci_level,
                grid_size=GRID_POINTS,
                executor=executor,
            )
        else:
            fit = ols_curve(
                points.x,
                points.y,
                degree=trend.degree if kind == TrendlineKind.POLYNOMIAL else 1,
                log_x=kind in (TrendlineKind.LOGARITHMIC, TrendlineKind.POWER),
                log_y=kind in (TrendlineKind.EXPONENTIAL, TrendlineKind.POWER),
                ci_level=trend.ci_level,
                grid_size=GRID_POINTS,
            )
    except StatsInputError as exc:
        # Too few points, no variation, ...: an empty overlay, not a failed chart.
        return _empty(trend, len(points.x), points.sampled, str(exc))
    return _result(
        trend,
        points,
        np.asarray(fit.x, dtype=np.float64),
        np.asarray(fit.y, dtype=np.float64),
        None if fit.lower is None else np.asarray(fit.lower, dtype=np.float64),
        None if fit.upper is None else np.asarray(fit.upper, dtype=np.float64),
        equation=None if kind == TrendlineKind.LOESS else _equation(kind, fit.coef),
        r2=fit.r2,
        n=fit.n,
        warnings=list(fit.warnings),
    )


def overlays(
    engine: Engine,
    storage: LocalStorage,
    db: Session,
    request: ChartDataRequest,
    *,
    mark_cap: int,
) -> list[OverlayResult]:
    """One fit per spec.style.trendlines entry. On a chart split by color this is a single
    fit across all colors (Wave 1); per-group fits come with OverlayResult.group in Wave 2.

    Runs in the overlay slot (`overlay_key`), never the chart's own slot, so the overlay and
    data requests of one chart don't supersede each other.
    """
    spec = request.spec
    trendlines = spec.style.trendlines
    if not trendlines:
        return []
    key = overlay_key(request)
    # slot_key(request) == key from here on, for the aggregate path's chart_data call.
    request = request.model_copy(update={"slot": f"overlay:{request.slot or spec.id}"})
    results: list[OverlayResult] = []
    aggregate = get_plot(spec.plot_type) is None and spec.plot_type in AGGREGATE_PLOTS
    relation = None
    if not aggregate:
        relation = resolve_source(
            engine,
            db,
            storage,
            spec.source,
            request.pipelines,
            [*spec.filters, *request.filters],
            query_key=key,
        )
    cache: dict[int, Points] = {}
    for trend in trendlines:
        if trend.series not in cache:
            if aggregate:
                cache[trend.series] = _aggregated_points(
                    engine, storage, db, request, trend.series, mark_cap
                )
            else:
                assert relation is not None
                cache[trend.series] = _raw_points(relation, request, trend.series)
        result = fit_trendline(trend, cache[trend.series], executor=engine.pool)
        if aggregate and spec.encodings.get("color"):
            result.warnings.append("The trendline is fitted across all colors together.")
        results.append(result)
    return results
