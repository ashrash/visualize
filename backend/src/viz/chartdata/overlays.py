"""Trendline overlays: POST /api/charts/overlays -> one OverlayResult per style.trendlines[i].

What gets fitted:
- Aggregate charts (the basic family) fit the points the chart draws: the aggregated y{series}
  against x. Temporal x is epoch milliseconds; a categorical x is the category's position
  (0, 1, 2, ... in the chart's order).
- Charts with a plot module fit the raw (x, y[series]) rows, on a reservoir sample above
  STATS_SAMPLE_CAP rows (`sampled`, `n`).

Fits: linear and polynomial(degree) OLS; exponential (ln y on x), logarithmic (y on ln x) and
power (ln y on ln x), with r2 in the fitted (log) space as spreadsheets report it; LOESS; a
trailing moving average. `ciLevel` adds a confidence band for the mean (OLS-based fits only).
Temporal x has no equation (its coefficients would be per millisecond).

TODO(stats): swap the OLS and LOWESS math here for `viz.stats` `ols_curve(x, y, degree=,
log_x=, log_y=, ci_level=)` and `lowess_curve(...)` when they land (LOESS bands too).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast as type_cast

import numpy as np
import pyarrow as pa
from scipy import stats as sps
from sqlalchemy.orm import Session
from sqlglot import exp
from statsmodels.nonparametric.smoothers_lowess import lowess

from viz.chartdata.basic import AGGREGATE_PLOTS
from viz.chartdata.reduce import epoch_ms_values, numeric_x
from viz.chartdata.service import chart_data, slot_key
from viz.chartdata.slots import Ticket, TicketEngine, slots_for
from viz.chartdata.sql import alias, cast, col, fn, to_sql
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
from viz.stats.relation import Relation
from viz.storage import LocalStorage
from viz.transform import runtime
from viz.transform.source import resolve_field, resolve_source

STATS_SAMPLE_CAP = 1_000_000
LOESS_CAP = 5_000
GRID_POINTS = 200
SAMPLE_SEED = 42

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
    ticket: Ticket,
) -> Points:
    # The cache key ignores the slot, so this usually reuses the chart's cached response.
    result = chart_data(engine, storage, db, request, mark_cap=mark_cap, ticket=ticket)
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
    temporal = x_col.semantic == ColumnSemantic.TEMPORAL
    if x_col.semantic not in (ColumnSemantic.QUANTITATIVE, ColumnSemantic.TEMPORAL):
        raise InvalidSpecError("a trendline needs a numeric or date x field")
    x_expr = numeric_x(col(x_col.name), x_col.semantic)
    y_expr = cast(col(y_col.name), "DOUBLE")
    finite = exp.and_(fn("isfinite", x_expr.copy()), fn("isfinite", y_expr.copy()))
    narrowed = relation.narrow(finite)
    total = narrowed.count()
    points = narrowed.select(alias(x_expr, "x"), alias(y_expr, "y"))
    sampled = total > STATS_SAMPLE_CAP
    if sampled:
        points = _reservoir(points, STATS_SAMPLE_CAP)
    table = runtime.query_arrow(relation.engine, to_sql(points), key=relation.query_key)
    x, y = _clean(_floats(table.column("x")), _floats(table.column("y")))
    order = np.argsort(x, kind="stable")
    return Points(x[order], y[order], temporal, sampled, total)


def _reservoir(points: exp.Select, n: int) -> exp.Select:
    """A repeatable reservoir sample of the filtered rows (USING SAMPLE runs before WHERE in
    DuckDB 2.0, so sample the filtered subquery). TODO(stats): use stats' reservoir_sample."""
    import sqlglot

    template = sqlglot.parse_one(
        f"SELECT * FROM __viz_f USING SAMPLE reservoir({int(n)} ROWS) REPEATABLE ({SAMPLE_SEED})",
        read="duckdb",
    )
    assert isinstance(template, exp.Select)
    template.find(exp.Table).replace(points.subquery("__viz_f"))  # type: ignore[union-attr]
    return template


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


@dataclass
class Fit:
    y: Array
    lo: Array | None
    hi: Array | None
    r2: float | None
    coefficients: Array  # in the fitted space, original (unscaled) units, ascending powers


def _ols(u: Array, y: Array, degree: int, grid_u: Array, ci_level: float | None) -> Fit:
    """OLS of y on [1, t, ..., t^degree] with t = standardized u (well conditioned)."""
    center = float(np.mean(u))
    scale = float(np.std(u)) or 1.0
    t, grid_t = (u - center) / scale, (grid_u - center) / scale
    design = np.vander(t, degree + 1, increasing=True)
    grid_design = np.vander(grid_t, degree + 1, increasing=True)
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ beta
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None
    predicted = grid_design @ beta
    lo = hi = None
    dof = len(y) - (degree + 1)
    if ci_level is not None and dof > 0:
        if not 0 < ci_level < 1:
            raise InvalidSpecError("ciLevel must be between 0 and 1")
        cov = (ss_res / dof) * np.linalg.pinv(design.T @ design)
        se = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", grid_design, cov, grid_design), 0))
        half = sps.t.ppf((1 + ci_level) / 2, dof) * se
        lo, hi = predicted - half, predicted + half
    # Coefficients of the same polynomial in u: p(t) with t = (u - center) / scale.
    in_t = np.polynomial.Polynomial(beta)
    in_u = in_t(np.polynomial.Polynomial([-center / scale, 1 / scale]))
    coefficients = np.zeros(degree + 1)
    coefficients[: len(in_u.coef)] = in_u.coef
    return Fit(predicted, lo, hi, r2, coefficients)


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


def fit_trendline(trend: Trendline, points: Points) -> OverlayResult:
    x, y = points.x, points.y
    kind = trend.kind
    warnings: list[str] = []
    if kind == TrendlineKind.MOVING_AVERAGE:
        window = trend.window or 5
        if window < 1:
            raise InvalidSpecError("the moving-average window must be at least 1")
        if len(y) < window:
            return _empty(trend, len(y), points.sampled, f"Needs at least {window} points.")
        averaged = np.convolve(y, np.ones(window) / window, mode="valid")
        return _result(
            trend,
            points,
            x[window - 1 :],
            averaged,
            None,
            None,
            equation=None,
            r2=None,
            n=len(y),
            warnings=warnings,
        )

    if kind in (TrendlineKind.LOGARITHMIC, TrendlineKind.POWER):
        positive = x > 0
        if not positive.all():
            warnings.append("Points with x <= 0 were left out of the fit.")
        x, y = x[positive], y[positive]
    if kind in (TrendlineKind.EXPONENTIAL, TrendlineKind.POWER):
        positive = y > 0
        if not positive.all():
            warnings.append("Points with y <= 0 were left out of the fit.")
        x, y = x[positive], y[positive]
    degree = trend.degree if kind == TrendlineKind.POLYNOMIAL else 1
    if kind == TrendlineKind.POLYNOMIAL and not 1 <= degree <= 6:
        raise InvalidSpecError("the polynomial degree must be from 1 to 6")
    needed = degree + 2 if kind != TrendlineKind.LOESS else 3
    if len(np.unique(x)) < needed:
        return _empty(trend, len(x), points.sampled, f"Needs at least {needed} distinct x values.")
    grid = np.linspace(float(x.min()), float(x.max()), GRID_POINTS)

    if kind == TrendlineKind.LOESS:
        sampled = points.sampled
        fx, fy = x, y
        if len(x) > LOESS_CAP:
            rng = np.random.default_rng(SAMPLE_SEED)
            pick = np.sort(rng.choice(len(x), LOESS_CAP, replace=False))
            fx, fy, sampled = x[pick], y[pick], True
        smooth = lowess(fy, fx, frac=2 / 3, xvals=grid)
        if trend.ci_level is not None:
            warnings.append("Confidence bands for LOESS aren't available yet.")
        result = _result(
            trend,
            points,
            grid,
            np.asarray(smooth, dtype=np.float64),
            None,
            None,
            equation=None,
            r2=None,
            n=len(fx),
            warnings=warnings,
        )
        return result.model_copy(update={"sampled": sampled})

    log_x = kind in (TrendlineKind.LOGARITHMIC, TrendlineKind.POWER)
    log_y = kind in (TrendlineKind.EXPONENTIAL, TrendlineKind.POWER)
    u, grid_u = (np.log(x), np.log(grid)) if log_x else (x, grid)
    target = np.log(y) if log_y else y
    fit = _ols(u, target, degree, grid_u, trend.ci_level)
    out_y, lo, hi = fit.y, fit.lo, fit.hi
    if log_y:
        out_y = np.exp(out_y)
        lo = np.exp(lo) if lo is not None else None
        hi = np.exp(hi) if hi is not None else None
    a, b = (fit.coefficients[0], fit.coefficients[1]) if len(fit.coefficients) > 1 else (0.0, 0.0)
    if kind in (TrendlineKind.LINEAR, TrendlineKind.POLYNOMIAL):
        equation = _poly_equation(fit.coefficients.tolist())
    elif kind == TrendlineKind.EXPONENTIAL:
        equation = f"y = {_fmt(math.exp(a))}·e^({_fmt(b)}x)"
    elif kind == TrendlineKind.LOGARITHMIC:
        equation = f"y = {_fmt(a)} {'-' if b < 0 else '+'} {_fmt(abs(b))}·ln(x)"
    else:
        equation = f"y = {_fmt(math.exp(a))}·x^{_fmt(b)}"
    return _result(
        trend,
        points,
        grid,
        out_y,
        lo,
        hi,
        equation=equation,
        r2=fit.r2,
        n=len(x),
        warnings=warnings,
    )


def overlays(
    engine: Engine,
    storage: LocalStorage,
    db: Session,
    request: ChartDataRequest,
    *,
    mark_cap: int,
    ticket: Ticket | None = None,
) -> list[OverlayResult]:
    """One fit per spec.style.trendlines entry. On a chart split by color this is a single
    fit across all colors (Wave 1); per-group fits come with OverlayResult.group in Wave 2."""
    spec = request.spec
    trendlines = spec.style.trendlines
    if not trendlines:
        return []
    # Run in the overlay slot (see overlay_key), never in the chart's own slot.
    request = request.model_copy(update={"slot": f"overlay:{request.slot or spec.id}"})
    slots = slots_for(engine)
    own_ticket = ticket is None
    if ticket is None:
        ticket = slots.begin(slot_key(request))  # == overlay_key of the original request
    try:
        results: list[OverlayResult] = []
        aggregate = get_plot(spec.plot_type) is None and spec.plot_type in AGGREGATE_PLOTS
        relation = None
        if not aggregate:
            relation = resolve_source(
                type_cast(Engine, TicketEngine(engine, slots, ticket)),
                db,
                storage,
                spec.source,
                request.pipelines,
                [*spec.filters, *request.filters],
                query_key=ticket.key,
                generation=ticket.generation,
            )
        cache: dict[int, Points] = {}
        for trend in trendlines:
            if trend.series not in cache:
                if aggregate:
                    cache[trend.series] = _aggregated_points(
                        engine, storage, db, request, trend.series, mark_cap, ticket
                    )
                else:
                    assert relation is not None
                    cache[trend.series] = _raw_points(relation, request, trend.series)
            result = fit_trendline(trend, cache[trend.series])
            if aggregate and spec.encodings.get("color"):
                result.warnings.append("The trendline is fitted across all colors together.")
            results.append(result)
        return results
    finally:
        if own_ticket:
            slots.end(ticket)
