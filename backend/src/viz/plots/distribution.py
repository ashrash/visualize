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
    # NOTE: "scott" here is `viz.stats.kde`'s rule, which is R's bw.nrd (1.06 * spread *
    # n^(-1/5), spread = min(sd, IQR/1.34)), not SciPy's own Scott factor (n^(-1/5) * sd alone);
    # the two differ by a few percent. Both rules and "silverman" are documented in kde.py.
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

    # `base` carries the category as a fixed "_cat" alias (so it works whether or not x_field is
    # bound) and the value as "_v". Quartiles are computed once per *group*, in a small
    # `grouped` CTE (quantile_cont over one row per group), then joined back onto every raw row:
    # a GROUP BY + join is much cheaper than a window function recomputed over every row for both
    # the summary and the outlier query (measured ~357ms vs ~607ms at 10M rows, grouped).
    base_cols = [alias(col(x_field), "_cat")] if x_field else []
    base = target.select(*base_cols, alias(as_double(col(y_field)), "_v"))
    base_sql = _sql(base)
    cat_select = "_cat, " if x_field else ""
    group_by = "GROUP BY _cat" if x_field else ""
    grouped_sql = (
        f"SELECT {cat_select}quantile_cont(_v, [0.25, 0.5, 0.75]) AS _qs "
        f"FROM ({base_sql}) AS _b {group_by}"
    )
    # A plain CROSS JOIN takes no ON clause (there is exactly one group to join to); grouped
    # joins use IS NOT DISTINCT FROM so a NULL category still joins to its own group's row.
    join = (
        "JOIN ({grouped}) AS _g ON _t._cat IS NOT DISTINCT FROM _g._cat"
        if x_field
        else "CROSS JOIN ({grouped}) AS _g"
    ).format(grouped=grouped_sql)
    joined_sql = (
        f"SELECT {'_t._cat AS _cat, ' if x_field else ''}_t._v AS _v, "
        "_g._qs[1] AS _q1, _g._qs[2] AS _q2, _g._qs[3] AS _q3 "
        f"FROM ({base_sql}) AS _t {join}"
    )
    group_select = "_cat AS x, " if x_field else ""
    summary_sql = (
        f"SELECT {group_select}"
        "min(_q1) AS q1, min(_q2) AS median, min(_q3) AS q3, "
        "min(_v) FILTER (WHERE _v >= _q1 - 1.5 * (_q3 - _q1)) AS lo, "
        "max(_v) FILTER (WHERE _v <= _q3 + 1.5 * (_q3 - _q1)) AS hi, "
        f"count(*) AS n FROM ({joined_sql}) AS _j "
        f"{'GROUP BY _cat ORDER BY _cat' if x_field else ''}"
    )
    summary = target.arrow(summary_sql)
    if x_field:
        table = summary
    else:
        parts = summary.to_pydict()
        table = pa.table({"x": ["All"] * summary.num_rows, **parts})

    outlier_sql = (
        f"SELECT {group_select}_v AS value FROM ({joined_sql}) AS _o "
        "WHERE _v < _q1 - 1.5 * (_q3 - _q1) OR _v > _q3 + 1.5 * (_q3 - _q1) "
        f"ORDER BY {'_cat, ' if x_field else ''}_v LIMIT {params.max_outliers + 1}"
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


# ---------------------------------------------------------------- violin


class ViolinParams(PlotParams):
    bandwidth: Bandwidth = "silverman"
    adjust: float = Field(default=1.0, gt=0)
    grid_size: int = Field(default=200, ge=16, le=2048)
    max_groups: int = Field(default=20, ge=1, le=100)


@register_plot(
    PlotType.VIOLIN,
    channels=[Channel("x", CATEGORICAL, min=0), Channel("y", QUANT)],
    params=ViolinParams,
)
def violin(req: PlotRequest) -> TableOutput:
    """Per-group KDE (CLAUDE.md §8: "S KDE per group"), drawn by our own custom renderItem.

    Not the official `@echarts-x/custom-violin` series: that renderer computes its own KDE from
    raw points in the browser with a bandwidth hard-coded to ~10% of a fixed [0, 10] sweep, which
    draws a visually wrong shape (clipped tails, wrong relative width) for data outside that
    range and for narrow groups. Reusing the server's `kde_by_group` (same kernel as `density`)
    and a small custom renderItem (frontend) avoids that.
    """
    x_field = req.field("x")
    y_field = req.field("y")
    assert y_field is not None
    params = req.params
    assert isinstance(params, ViolinParams)
    by = (x_field,) if x_field else ()
    sample = reservoir_sample(
        req.relation,
        [y_field],
        cap=KDE_CAP,
        by=by,
        total_cap=KDE_CAP,
        max_groups=params.max_groups,
    )
    groups = (
        np.asarray(sample.values(x_field), dtype=object)
        if x_field
        else np.array(["All"] * sample.n, dtype=object)
    )
    grouped = kde_by_group(
        sample.column(y_field),
        groups,
        bw=params.bandwidth,
        adjust=params.adjust,
        grid_size=params.grid_size,
        common_grid=True,
    )
    xs: list[object] = []
    ys: list[float] = []
    densities: list[float] = []
    ns: list[int] = []
    for curve in grouped.curves:
        xs.extend([curve.key] * len(curve.curve.x))
        ys.extend(curve.curve.x.tolist())
        densities.extend(curve.curve.density.tolist())
        ns.extend([curve.n] * len(curve.curve.x))
    table = pa.table({"x": xs, "y": ys, "density": densities, "n": ns})
    return TableOutput(
        table=table,
        reduction=Reduction.SAMPLED if sample.sampled else Reduction.NONE,
        method="kde",
        fields={
            "x": _label(req, "x") if x_field else "Category",
            "y": _label(req, "y"),
            "density": "Density",
        },
        params={},
        warnings=[*grouped.warnings, *sample.notes()],
        rows_scanned=sample.n_total,
        rows_matched=sample.n_total,
    )


# ---------------------------------------------------------------- strip / swarm


class PointsParams(PlotParams):
    max_groups: int = Field(default=50, ge=1, le=200)


def _points_table(req: PlotRequest) -> TableOutput:
    """Raw/reservoir-sampled (x, y) points, up to the chart's mark cap.

    Shared by strip and swarm (both draw individual values per category with ECharts' native
    scatter `jitter`, so neither needs a server-side density or layout).
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
