"""Plots.md §3 Relationship & Correlation: scatter, bubble, pair, correlation heatmap,
correlogram, hexbin, contour, regression, residual.

Raw-point plots (scatter, bubble, regression) return the filtered rows as-is up to the mark
cap; over the cap, scatter/bubble switch to a 2-D binned density (`chartdata.reduce.bin2d`,
columns `x_lo`/`x_hi`/`y_lo`/`y_hi`/`count`) while regression falls back to a reservoir sample
(so the fitted line in the frontend's trendline overlay still sees a representative scatter).
Contour always returns that same `bin2d` shape (see contour()'s docstring for why).
"""

from __future__ import annotations

import math

import pyarrow as pa
from pydantic import Field
from scipy import stats as sps
from sqlglot import exp

from viz.chartdata.reduce import bin2d, hexbin, numeric_x
from viz.contracts import PlotType, Reduction
from viz.errors import InvalidSpecError
from viz.plots.base import (
    CATEGORICAL,
    NUMERIC_OR_TIME,
    QUANT,
    Channel,
    PlotParams,
    PlotRequest,
    TableOutput,
    register_plot,
)
from viz.stats.errors import StatsInputError
from viz.stats.regression import ols_curve
from viz.stats.relation import col
from viz.stats.sampling import MODEL_CAP, reservoir_sample
from viz.stats.sql import alias, as_double, count_if, fn, valid_rows


def _label(req: PlotRequest, channel: str, index: int = 0) -> str:
    """Display label for the `index`-th field bound to `channel`."""
    encodings = req.spec.encodings.get(channel, [])
    if index < len(encodings) and encodings[index].label:
        label = encodings[index].label
        assert label is not None
        return label
    fields = req.fields(channel)
    return fields[index] if index < len(fields) else channel.capitalize()


# ---------------------------------------------------------------- scatter / bubble


class ScatterParams(PlotParams):
    bins: int = Field(default=60, ge=10, le=300)


def _points_or_bins(req: PlotRequest) -> TableOutput:
    x_field = req.field("x")
    y_field = req.field("y")
    assert x_field is not None and y_field is not None
    size_field = req.field("size")
    color_field = req.field("color")
    params = req.params
    assert isinstance(params, ScatterParams)
    validity_fields = list(dict.fromkeys([x_field, y_field, *([size_field] if size_field else [])]))
    valid_target = valid_rows(req.relation, validity_fields)
    cap = req.mark_cap

    fields = {"x": _label(req, "x"), "y": _label(req, "y")}
    if size_field:
        fields["size"] = _label(req, "size")
    if color_field:
        fields["color"] = _label(req, "color")

    # Explicit aliases, not a rename-by-source-name: the same field can be bound to two
    # channels (e.g. bubble's size=y, or x=y), and renaming columns by their *source* name would
    # silently collapse two output columns into one, dropping a channel from the chart entirely.
    select_cols: list[tuple[str, exp.Expr]] = [("x", col(x_field)), ("y", col(y_field))]
    if size_field:
        select_cols.append(("size", col(size_field)))
    if color_field:
        select_cols.append(("color", col(color_field)))
    probe_query = valid_target.select(*(alias(e, n) for n, e in select_cols)).limit(cap + 1)
    probe = valid_target.arrow(probe_query)

    if probe.num_rows <= cap:
        return TableOutput(
            table=probe, reduction=Reduction.NONE, method=None, fields=fields, params={}
        )

    x_column = req.relation.columns[x_field]
    x_expr = numeric_x(col(x_field), x_column.semantic)
    y_expr = as_double(col(y_field))
    binned, bin_params = bin2d(valid_target, x_expr, y_expr, bins_x=params.bins, bins_y=params.bins)
    return TableOutput(
        table=binned,
        reduction=Reduction.BINNED,
        method="bin2d",
        fields={
            "x_lo": fields["x"],
            "x_hi": fields["x"],
            "y_lo": fields["y"],
            "y_hi": fields["y"],
            "count": "Count",
        },
        params=bin_params,
        warnings=["Showing a 2-D density of points; zoom in or filter to see individual points."],
    )


@register_plot(
    PlotType.SCATTER,
    channels=[
        Channel("x", NUMERIC_OR_TIME),
        Channel("y", QUANT),
        Channel("color", CATEGORICAL, min=0),
        Channel("size", QUANT, min=0),
    ],
    params=ScatterParams,
)
def scatter(req: PlotRequest) -> TableOutput:
    return _points_or_bins(req)


@register_plot(
    PlotType.BUBBLE,
    channels=[
        Channel("x", NUMERIC_OR_TIME),
        Channel("y", QUANT),
        Channel("size", QUANT),
        Channel("color", CATEGORICAL, min=0),
    ],
    params=ScatterParams,
)
def bubble(req: PlotRequest) -> TableOutput:
    return _points_or_bins(req)


# ---------------------------------------------------------------- pair plot


class PairParams(PlotParams):
    max_points: int = Field(default=2000, ge=50, le=20000)


@register_plot(
    PlotType.PAIR,
    channels=[Channel("dimensions", QUANT, min=2, max=8), Channel("color", CATEGORICAL, min=0)],
    params=PairParams,
)
def pair(req: PlotRequest) -> TableOutput:
    dims = req.fields("dimensions")
    color_field = req.field("color")
    params = req.params
    assert isinstance(params, PairParams)
    # Marks drawn = sampled rows x off-diagonal cells (n*(n-1)): at 8 dimensions that is 56x the
    # row count, so the per-row cap must shrink with n or the chart badly overshoots mark_cap
    # (e.g. 20,000 rows x 56 cells = 1.12M marks against a 50k budget).
    off_diagonal_cells = max(1, len(dims) * (len(dims) - 1))
    cap = max(50, min(params.max_points, req.mark_cap // off_diagonal_cells))
    sample = reservoir_sample(
        req.relation,
        dims,
        cap=cap,
        by=(color_field,) if color_field else (),
        total_cap=cap,
    )
    columns: dict[str, object] = {
        f"dimension{i}": sample.column(d).tolist() for i, d in enumerate(dims)
    }
    fields = {f"dimension{i}": _label(req, "dimensions", i) for i in range(len(dims))}
    if color_field:
        columns["color"] = sample.values(color_field)
        fields["color"] = _label(req, "color")
    table = pa.table(columns)
    return TableOutput(
        table=table,
        reduction=Reduction.SAMPLED if sample.sampled else Reduction.NONE,
        method="reservoir",
        fields=fields,
        params={"dimensions": list(dims)},
        warnings=sample.notes(),
        rows_scanned=sample.n_total,
        rows_matched=sample.n_total,
    )


# ---------------------------------------------------------------- correlation heatmap / correlogram


def _pvalue(r: float | None, n: int | None) -> float | None:
    if r is None or n is None or n < 3:
        return None
    clipped = max(min(r, 1.0 - 1e-15), -1.0 + 1e-15)
    t = clipped * math.sqrt((n - 2) / (1 - clipped * clipped))
    return float(2 * sps.t.sf(abs(t), n - 2))


def _finite_or_null(expr: exp.Expr) -> exp.Expr:
    """`expr`, or NULL when it is NaN/+-inf: `corr()` (like most aggregates) otherwise returns
    NaN for the whole pair the moment one input value is non-finite (CLAUDE.md §12 gotcha)."""
    return exp.Case(ifs=[exp.If(this=fn("isfinite", expr), true=expr)])


def _corr_matrix(req: PlotRequest, *, with_p: bool) -> TableOutput:
    dims = req.fields("dimensions")
    exprs = []
    for i, a in enumerate(dims):
        for j, b in enumerate(dims):
            a_expr = _finite_or_null(as_double(col(a)))
            b_expr = _finite_or_null(as_double(col(b)))
            exprs.append(alias(fn("corr", a_expr, b_expr), f"c_{i}_{j}"))
            if with_p:
                both_finite = exp.and_(
                    fn("isfinite", as_double(col(a))), fn("isfinite", as_double(col(b)))
                )
                exprs.append(alias(count_if(both_finite), f"n_{i}_{j}"))
    query = req.relation.select(*exprs)
    row = req.relation.arrow(query).to_pylist()[0]

    xs: list[object] = []
    ys: list[object] = []
    values: list[float | None] = []
    ns: list[int | None] = []
    ps: list[float | None] = []
    for i, a in enumerate(dims):
        for j, b in enumerate(dims):
            xs.append(a)
            ys.append(b)
            r = row[f"c_{i}_{j}"]
            # A constant column (zero variance) gives 0/0 = NaN, not NULL; treat it the same way
            # as a missing value so the frontend (which only checks for `null`) doesn't show the
            # literal text "NaN" or feed NaN into a symbolSize computation.
            if r is not None and not math.isfinite(r):
                r = None
            values.append(r)
            if with_p:
                n_ij = row[f"n_{i}_{j}"]
                ns.append(n_ij)
                ps.append(_pvalue(r, n_ij))
    columns: dict[str, object] = {"x": xs, "y": ys, "value": values}
    fields = {"x": "Field", "y": "Field", "value": "Correlation (Pearson r)"}
    if with_p:
        columns["n"] = ns
        columns["p"] = ps
        fields["n"] = "N"
        fields["p"] = "P-value"
    table = pa.table(columns)
    return TableOutput(
        table=table,
        reduction=Reduction.AGGREGATED,
        method="pearson_corr",
        fields=fields,
        params={"dimensions": list(dims)},
    )


@register_plot(
    PlotType.CORRELATION_HEATMAP,
    channels=[Channel("dimensions", QUANT, min=2, max=20)],
    params=PlotParams,
)
def correlation_heatmap(req: PlotRequest) -> TableOutput:
    return _corr_matrix(req, with_p=False)


@register_plot(
    PlotType.CORRELOGRAM,
    channels=[Channel("dimensions", QUANT, min=2, max=20)],
    params=PlotParams,
)
def correlogram(req: PlotRequest) -> TableOutput:
    return _corr_matrix(req, with_p=True)


# ---------------------------------------------------------------- hexbin


class HexbinParams(PlotParams):
    radius_px: float = Field(default=18.0, gt=2, le=100)


@register_plot(
    PlotType.HEXBIN,
    channels=[Channel("x", QUANT), Channel("y", QUANT)],
    params=HexbinParams,
)
def hexbin_plot(req: PlotRequest) -> TableOutput:
    x_field = req.field("x")
    y_field = req.field("y")
    assert x_field is not None and y_field is not None
    params = req.params
    assert isinstance(params, HexbinParams)
    target = valid_rows(req.relation, [x_field, y_field])
    x_expr = as_double(col(x_field))
    y_expr = as_double(col(y_field))
    width_px = max(100, req.viewport.width_px)
    height_px = max(100, req.viewport.height_px)
    table, bin_params = hexbin(
        target, x_expr, y_expr, width_px=width_px, height_px=height_px, radius_px=params.radius_px
    )
    return TableOutput(
        table=table,
        reduction=Reduction.BINNED,
        method="hexbin",
        fields={"x": _label(req, "x"), "y": _label(req, "y"), "count": "Count"},
        params=bin_params,
    )


# ---------------------------------------------------------------- contour


class ContourParams(PlotParams):
    grid: int = Field(default=40, ge=8, le=200)


@register_plot(
    PlotType.CONTOUR,
    channels=[Channel("x", QUANT), Channel("y", QUANT)],
    params=ContourParams,
)
def contour(req: PlotRequest) -> TableOutput:
    """A 2-D binned density over the *full* filtered data (CLAUDE.md §1: compute in Python).

    Not the official `@echarts-x/custom-contour` series: its compiled output imports the full
    (non-tree-shaken) `echarts` package as a side effect of `import { zrUtil } from 'echarts'`,
    which self-registers every chart type/component (confirmed to break an unrelated ui-charts
    test, `charts/core/render.test.ts`'s unregistered-component check) and defeats the
    tree-shaken bundle CLAUDE.md §1/§2 require. The frontend renders this same `bin2d` shape
    (`x_lo`/`x_hi`/`y_lo`/`y_hi`/`count`) as a density heatmap via the shared `densityFallback`
    primitive, same as scatter/bubble's over-cap fallback; it is not a true isoline contour, so
    this plot stays `partial` in STATUS.md pending a real marching-squares renderer.
    """
    x_field = req.field("x")
    y_field = req.field("y")
    assert x_field is not None and y_field is not None
    params = req.params
    assert isinstance(params, ContourParams)
    target = valid_rows(req.relation, [x_field, y_field])
    # Bound total cells by the mark cap regardless of the requested grid resolution.
    grid = max(4, min(params.grid, math.isqrt(max(1, req.mark_cap))))
    x_expr = as_double(col(x_field))
    y_expr = as_double(col(y_field))
    table, bin_params = bin2d(target, x_expr, y_expr, bins_x=grid, bins_y=grid)
    return TableOutput(
        table=table,
        reduction=Reduction.BINNED,
        method="bin2d",
        fields={
            "x_lo": _label(req, "x"),
            "x_hi": _label(req, "x"),
            "y_lo": _label(req, "y"),
            "y_hi": _label(req, "y"),
            "count": "Count",
        },
        params=bin_params,
    )


# ---------------------------------------------------------------- regression


class RegressionParams(PlotParams):
    """Raw/sampled points for a scatter + fitted line. The fit itself comes from the generic
    overlays endpoint (`style.trendlines`); this module only supplies the underlying points."""


@register_plot(
    PlotType.REGRESSION,
    channels=[
        Channel("x", NUMERIC_OR_TIME),
        Channel("y", QUANT),
        Channel("color", CATEGORICAL, min=0),
    ],
    params=RegressionParams,
)
def regression(req: PlotRequest) -> TableOutput:
    x_field = req.field("x")
    y_field = req.field("y")
    assert x_field is not None and y_field is not None
    color_field = req.field("color")
    needed = list(dict.fromkeys([x_field, y_field, *([color_field] if color_field else [])]))
    valid_target = valid_rows(req.relation, [x_field, y_field])
    cap = req.mark_cap
    fields = {"x": _label(req, "x"), "y": _label(req, "y")}
    if color_field:
        fields["color"] = _label(req, "color")

    # Explicit aliases (not rename-by-source-name): x and y can be bound to the same field.
    select_cols: list[tuple[str, exp.Expr]] = [("x", col(x_field)), ("y", col(y_field))]
    if color_field:
        select_cols.append(("color", col(color_field)))
    probe = valid_target.arrow(
        valid_target.select(*(alias(e, n) for n, e in select_cols)).limit(cap + 1)
    )
    if probe.num_rows <= cap:
        return TableOutput(
            table=probe, reduction=Reduction.NONE, method=None, fields=fields, params={}
        )
    sample = reservoir_sample(valid_target, needed, cap=cap, total_cap=cap)
    columns: dict[str, object] = {
        "x": sample.column(x_field).tolist(),
        "y": sample.column(y_field).tolist(),
    }
    if color_field:
        columns["color"] = sample.values(color_field)
    table = pa.table(columns)
    return TableOutput(
        table=table,
        reduction=Reduction.SAMPLED,
        method="reservoir",
        fields=fields,
        params={},
        warnings=sample.notes(),
        rows_scanned=sample.n_total,
        rows_matched=sample.n_total,
    )


# ---------------------------------------------------------------- residual


class ResidualParams(PlotParams):
    """Degree-1 (linear) OLS residuals; higher-degree fits are a possible follow-up."""


@register_plot(
    PlotType.RESIDUAL,
    channels=[Channel("x", QUANT), Channel("y", QUANT), Channel("color", CATEGORICAL, min=0)],
    params=ResidualParams,
)
def residual(req: PlotRequest) -> TableOutput:
    """Fit on up to `MODEL_CAP` points (for a good fit), but never *display* more than the
    chart's mark cap: the fit sample used to go straight into the output table, so a dataset
    between mark_cap and MODEL_CAP rows silently fell back to chartdata's blunt "first N rows"
    safety truncation (biased toward however the data happens to be ordered) while still
    reporting `reduction: none`. Residuals for the displayed points are evaluated from the fit's
    own (intercept, slope) rather than refitting, so the fit quality is unaffected by the cap.
    """
    x_field = req.field("x")
    y_field = req.field("y")
    assert x_field is not None and y_field is not None
    color_field = req.field("color")
    fit_fields = list(dict.fromkeys([x_field, y_field]))
    fit_sample = reservoir_sample(req.relation, fit_fields, cap=MODEL_CAP)
    try:
        fit = ols_curve(
            fit_sample.column(x_field), fit_sample.column(y_field), degree=1, ci_level=None
        )
    except StatsInputError as exc:
        raise InvalidSpecError(str(exc)) from exc
    intercept = fit.coef[0] if fit.coef else 0.0
    slope = fit.coef[1] if len(fit.coef) > 1 else 0.0

    display_fields = list(
        dict.fromkeys([x_field, y_field, *([color_field] if color_field else [])])
    )
    display_cap = req.mark_cap
    if fit_sample.n <= display_cap and set(display_fields) == set(fit_fields):
        display = fit_sample
    else:
        display = reservoir_sample(
            req.relation, display_fields, cap=display_cap, total_cap=display_cap
        )
    x_d = display.column(x_field)
    y_d = display.column(y_field)
    fitted = intercept + slope * x_d
    residuals = y_d - fitted
    columns: dict[str, object] = {"x": fitted.tolist(), "y": residuals.tolist()}
    fields = {"x": "Fitted value", "y": "Residual"}
    if color_field:
        columns["color"] = display.values(color_field)
        fields["color"] = _label(req, "color")
    table = pa.table(columns)
    sampled = fit_sample.sampled or display.n < display.n_total
    return TableOutput(
        table=table,
        reduction=Reduction.SAMPLED if sampled else Reduction.NONE,
        method="ols_residuals",
        fields=fields,
        params={"intercept": intercept, "slope": slope, "r2": fit.r2},
        warnings=[*fit.warnings, *display.notes()],
        rows_scanned=display.n_total,
        rows_matched=display.n_total,
    )
