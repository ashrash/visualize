"""Plots.md §2 Statistical Distribution: histogram, density, box, violin, strip, swarm, Q-Q,
ECDF, frequency polygon.

Column conventions (see docs/plans/transform.md): bin edges are `x_lo`/`x_hi`; a channel bound
to a single field keeps its bare name (`x`, `y`, `color`). Violin and box use their own small
shapes (documented on each function) since they don't fit the generic bar/line convention.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pyarrow as pa
from pydantic import Field, JsonValue
from sqlglot import exp

from viz.contracts import PlotType, Reduction
from viz.plots.base import (
    CATEGORICAL,
    QUANT,
    Channel,
    PlotParams,
    PlotRequest,
    TableOutput,
    register_plot,
)
from viz.stats.groups import cap_groups
from viz.stats.kde import Bandwidth, kde, kde_by_group
from viz.stats.quantiles import (
    BinRule,
    Histogram,
)
from viz.stats.quantiles import (
    ecdf as ecdf_kernel,
)
from viz.stats.quantiles import (
    histogram as histogram_kernel,
)
from viz.stats.quantiles import (
    qq as qq_kernel,
)
from viz.stats.relation import col
from viz.stats.sampling import KDE_CAP, reservoir_sample
from viz.stats.sql import alias, as_double, valid_rows


def _label(req: PlotRequest, channel: str, index: int = 0) -> str:
    """Display label for the `index`-th field bound to `channel`."""
    encodings = req.spec.encodings.get(channel, [])
    if index < len(encodings) and encodings[index].label:
        label = encodings[index].label
        assert label is not None
        return label
    fields = req.fields(channel)
    return fields[index] if index < len(fields) else channel.capitalize()


def _sql(expr: exp.Expr) -> str:
    return expr.sql(dialect="duckdb")


# ---------------------------------------------------------------- histogram / frequency polygon


class BinParams(PlotParams):
    bins: int | None = Field(default=None, ge=1, le=1000)
    bin_width: float | None = Field(default=None, gt=0)
    rule: BinRule = "fd"
    max_groups: int = Field(default=20, ge=1, le=100)


def _histogram(req: PlotRequest) -> tuple[Histogram, str]:
    x_field = req.field("x")
    assert x_field is not None
    color_field = req.field("color")
    params = req.params
    assert isinstance(params, BinParams)
    by = [color_field] if color_field else []
    hist = histogram_kernel(
        req.relation,
        x_field,
        rule=params.rule,
        bins=params.bins,
        width=params.bin_width,
        by=by,
        max_groups=params.max_groups,
    )
    return hist, color_field or ""


@register_plot(
    PlotType.HISTOGRAM,
    channels=[Channel("x", QUANT), Channel("color", CATEGORICAL, min=0)],
    params=BinParams,
)
def histogram(req: PlotRequest) -> TableOutput:
    hist, color_field = _histogram(req)
    names = hist.table.column_names
    rename = {"binStart": "x_lo", "binEnd": "x_hi"}
    if color_field:
        rename[color_field] = "color"
    table = hist.table.rename_columns([rename.get(n, n) for n in names])
    fields = {"x_lo": _label(req, "x"), "x_hi": _label(req, "x"), "count": "Count"}
    if color_field:
        fields["color"] = _label(req, "color")
    return TableOutput(
        table=table,
        reduction=Reduction.BINNED,
        method=hist.rule,
        fields=fields,
        params={"binWidth": hist.width, "rule": hist.rule},
        warnings=list(hist.warnings),
    )


@register_plot(
    PlotType.FREQUENCY_POLYGON,
    channels=[Channel("x", QUANT), Channel("color", CATEGORICAL, min=0)],
    params=BinParams,
)
def frequency_polygon(req: PlotRequest) -> TableOutput:
    hist, color_field = _histogram(req)
    lo = np.asarray(hist.table.column("binStart").to_numpy())
    hi = np.asarray(hist.table.column("binEnd").to_numpy())
    mids = (lo + hi) / 2
    columns: dict[str, object] = {"x": mids.tolist(), "y0": hist.table.column("count").to_pylist()}
    if color_field:
        columns["color"] = hist.table.column(color_field).to_pylist()
    table = pa.table(columns)
    fields = {"x": _label(req, "x"), "y0": "Count"}
    if color_field:
        fields["color"] = _label(req, "color")
    return TableOutput(
        table=table,
        reduction=Reduction.BINNED,
        method=hist.rule,
        fields=fields,
        params={"binWidth": hist.width, "rule": hist.rule},
        warnings=list(hist.warnings),
    )


# ---------------------------------------------------------------- density (KDE)


class DensityParams(PlotParams):
    bandwidth: Bandwidth = "silverman"
    adjust: float = Field(default=1.0, gt=0)
    grid_size: int = Field(default=512, ge=16, le=4096)
    max_groups: int = Field(default=20, ge=1, le=100)


@register_plot(
    PlotType.DENSITY,
    channels=[Channel("x", QUANT), Channel("color", CATEGORICAL, min=0)],
    params=DensityParams,
)
def density(req: PlotRequest) -> TableOutput:
    x_field = req.field("x")
    assert x_field is not None
    color_field = req.field("color")
    params = req.params
    assert isinstance(params, DensityParams)
    by = (color_field,) if color_field else ()
    sample = reservoir_sample(
        req.relation,
        [x_field],
        cap=KDE_CAP,
        by=by,
        total_cap=KDE_CAP,
        max_groups=params.max_groups,
    )
    values = sample.column(x_field)
    fields = {"x": _label(req, "x"), "y0": "Density"}
    if color_field:
        groups = np.asarray(sample.values(color_field), dtype=object)
        grouped = kde_by_group(
            values, groups, bw=params.bandwidth, adjust=params.adjust, grid_size=params.grid_size
        )
        xs: list[float] = []
        ys: list[float] = []
        colors: list[object] = []
        bandwidths: dict[str, JsonValue] = {}
        for group_curve in grouped.curves:
            xs.extend(group_curve.curve.x.tolist())
            ys.extend(group_curve.curve.density.tolist())
            colors.extend([group_curve.key] * len(group_curve.curve.x))
            bandwidths[str(group_curve.key)] = group_curve.curve.bandwidth
        table = pa.table({"x": xs, "y0": ys, "color": colors})
        fields["color"] = _label(req, "color")
        warnings = list(grouped.warnings)
        bw_param: JsonValue = bandwidths
    else:
        single = kde(values, bw=params.bandwidth, adjust=params.adjust, grid_size=params.grid_size)
        table = pa.table({"x": single.x.tolist(), "y0": single.density.tolist()})
        warnings = list(single.warnings)
        bw_param = single.bandwidth
    return TableOutput(
        table=table,
        reduction=Reduction.SAMPLED if sample.sampled else Reduction.NONE,
        method="kde",
        fields=fields,
        params={"bandwidth": bw_param},
        warnings=[*warnings, *sample.notes()],
        rows_scanned=sample.n_total,
        rows_matched=sample.n_total,
    )


# ---------------------------------------------------------------- box


class BoxParams(PlotParams):
    max_groups: int = Field(default=50, ge=1, le=200)
    max_outliers: int = Field(default=1000, ge=0, le=10000)


@register_plot(
    PlotType.BOX,
    channels=[Channel("y", QUANT), Channel("x", CATEGORICAL, min=0)],
    params=BoxParams,
)
def box(req: PlotRequest) -> TableOutput:
    y_field = req.field("y")
    assert y_field is not None
    x_field = req.field("x")
    params = req.params
    assert isinstance(params, BoxParams)

    target = valid_rows(req.relation, [y_field])
    warnings: list[str] = []
    if x_field:
        capped = cap_groups(target, [x_field], params.max_groups)
        target = capped.relation
        if capped.warning:
            warnings.append(capped.warning)

    base_cols = [col(x_field)] if x_field else []
    base = target.select(*base_cols, alias(as_double(col(y_field)), "_v"))
    base_sql = _sql(base)
    partition = f"PARTITION BY {_sql(col(x_field))}" if x_field else ""
    quant_sql = (
        f"SELECT *, "
        f"quantile_cont(_v, 0.25) OVER ({partition}) AS _q1, "
        f"quantile_cont(_v, 0.5) OVER ({partition}) AS _q2, "
        f"quantile_cont(_v, 0.75) OVER ({partition}) AS _q3 "
        f"FROM ({base_sql}) AS _b"
    )
    group_select = f"{_sql(col(x_field))} AS x, " if x_field else ""
    group_by = f"GROUP BY {_sql(col(x_field))}" if x_field else ""
    summary_sql = (
        f"SELECT {group_select}"
        "min(_q1) AS q1, min(_q2) AS median, min(_q3) AS q3, "
        "min(_v) FILTER (WHERE _v >= _q1 - 1.5 * (_q3 - _q1)) AS lo, "
        "max(_v) FILTER (WHERE _v <= _q3 + 1.5 * (_q3 - _q1)) AS hi, "
        f"count(*) AS n FROM ({quant_sql}) AS _q {group_by}"
    )
    summary = target.arrow(summary_sql)
    if x_field:
        table = summary
    else:
        parts = summary.to_pydict()
        table = pa.table({"x": ["All"] * summary.num_rows, **parts})

    order_sql = f"{_sql(col(x_field))}, _v" if x_field else "_v"
    outlier_sql = (
        f"SELECT {group_select}_v AS value FROM ({quant_sql}) AS _o "
        "WHERE _v < _q1 - 1.5 * (_q3 - _q1) OR _v > _q3 + 1.5 * (_q3 - _q1) "
        f"ORDER BY {order_sql} LIMIT {params.max_outliers + 1}"
    )
    outliers = target.arrow(outlier_sql)
    truncated = outliers.num_rows > params.max_outliers
    if truncated:
        outliers = outliers.slice(0, params.max_outliers)
        warnings.append(f"Showing the first {params.max_outliers:,} outliers.")
    outlier_rows = outliers.to_pylist()

    fields = {
        "x": _label(req, "x") if x_field else "Category",
        "q1": "Q1",
        "median": "Median",
        "q3": "Q3",
        "lo": "Lower whisker",
        "hi": "Upper whisker",
        "n": "Count",
    }
    return TableOutput(
        table=table,
        reduction=Reduction.AGGREGATED,
        method="exact_quartiles",
        fields=fields,
        params={"outliers": outlier_rows, "truncatedOutliers": truncated},
        warnings=warnings,
    )


# ---------------------------------------------------------------- violin / strip / swarm


class PointsParams(PlotParams):
    max_groups: int = Field(default=50, ge=1, le=200)


def _points_table(req: PlotRequest) -> TableOutput:
    """Raw/reservoir-sampled (x, y) points, up to the chart's mark cap.

    Shared by violin, strip and swarm: all three draw individual values per category, so none
    of them need a server-side density or layout. Violin's renderer (the official
    `@echarts-x/custom-violin` custom series) bins and shapes the violin from these raw points
    itself; strip/swarm use ECharts' native scatter `jitter`.
    """
    y_field = req.field("y")
    assert y_field is not None
    x_field = req.field("x")
    params = req.params
    assert isinstance(params, PointsParams)
    by = (x_field,) if x_field else ()
    sample = reservoir_sample(
        req.relation,
        [y_field],
        cap=req.mark_cap,
        by=by,
        total_cap=req.mark_cap,
        max_groups=params.max_groups,
    )
    columns: dict[str, object] = {"y": sample.column(y_field).tolist()}
    columns["x"] = sample.values(x_field) if x_field else ["All"] * sample.n
    table = pa.table(columns)
    return TableOutput(
        table=table,
        reduction=Reduction.SAMPLED if sample.sampled else Reduction.NONE,
        method="reservoir",
        fields={"x": _label(req, "x") if x_field else "Category", "y": _label(req, "y")},
        params={},
        warnings=sample.notes(),
        rows_scanned=sample.n_total,
        rows_matched=sample.n_total,
    )


@register_plot(
    PlotType.VIOLIN,
    channels=[Channel("x", CATEGORICAL, min=0), Channel("y", QUANT)],
    params=PointsParams,
)
def violin(req: PlotRequest) -> TableOutput:
    return _points_table(req)


@register_plot(
    PlotType.STRIP,
    channels=[Channel("y", QUANT), Channel("x", CATEGORICAL, min=0)],
    params=PointsParams,
)
def strip(req: PlotRequest) -> TableOutput:
    return _points_table(req)


@register_plot(
    PlotType.SWARM,
    channels=[Channel("y", QUANT), Channel("x", CATEGORICAL, min=0)],
    params=PointsParams,
)
def swarm(req: PlotRequest) -> TableOutput:
    return _points_table(req)


# ---------------------------------------------------------------- Q-Q


class QqParams(PlotParams):
    distribution: Literal["norm", "expon", "uniform", "logistic", "laplace"] = "norm"
    max_points: int = Field(default=2000, ge=10, le=20000)
    line: Literal["quartiles", "fit"] = "quartiles"


@register_plot(PlotType.QQ, channels=[Channel("x", QUANT)], params=QqParams)
def qq(req: PlotRequest) -> TableOutput:
    x_field = req.field("x")
    assert x_field is not None
    params = req.params
    assert isinstance(params, QqParams)
    result = qq_kernel(
        req.relation,
        x_field,
        dist=params.distribution,
        max_points=params.max_points,
        line=params.line,
    )
    table = pa.table({"x": result.theoretical.tolist(), "y": result.sample.tolist()})
    return TableOutput(
        table=table,
        reduction=Reduction.NONE if result.exact else Reduction.AGGREGATED,
        method="qq" if result.exact else "qq_ranks",
        fields={"x": "Theoretical quantile", "y": _label(req, "x")},
        params={
            "slope": result.slope,
            "intercept": result.intercept,
            "r": result.r,
            "distribution": result.dist,
            "exact": result.exact,
        },
        rows_scanned=result.n,
        rows_matched=result.n,
    )


# ---------------------------------------------------------------- ECDF


class EcdfParams(PlotParams):
    max_points: int = Field(default=2000, ge=10, le=20000)
    max_groups: int = Field(default=20, ge=1, le=100)


@register_plot(
    PlotType.ECDF,
    channels=[Channel("x", QUANT), Channel("color", CATEGORICAL, min=0)],
    params=EcdfParams,
)
def ecdf(req: PlotRequest) -> TableOutput:
    x_field = req.field("x")
    assert x_field is not None
    color_field = req.field("color")
    params = req.params
    assert isinstance(params, EcdfParams)
    by = (color_field,) if color_field else ()
    result = ecdf_kernel(
        req.relation, x_field, max_points=params.max_points, by=by, max_groups=params.max_groups
    )
    rename = {"p": "y"}
    if color_field:
        rename[color_field] = "color"
    table = result.table.rename_columns([rename.get(n, n) for n in result.table.column_names])
    fields = {"x": _label(req, "x"), "y": "Cumulative probability"}
    if color_field:
        fields["color"] = _label(req, "color")
    return TableOutput(
        table=table,
        reduction=Reduction.NONE if result.exact else Reduction.BINNED,
        method="ecdf" if result.exact else "ecdf_quantile_grid",
        fields=fields,
        params={},
        warnings=list(result.warnings),
        rows_scanned=result.n,
        rows_matched=result.n,
    )
