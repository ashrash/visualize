"""Chart specs and the plot catalogue (every chart type in Plots.md).

Encodings and params are maps keyed by name: each plot declares its channels and params in
its registry entry, so adding a plot type never requires a contract change.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue

from viz.contracts.base import AggFn, Contract, SourceRef
from viz.contracts.dataset import ColumnSemantic
from viz.contracts.predicate import Predicate


class PlotFamily(StrEnum):
    BASIC = "basic"
    DISTRIBUTION = "distribution"
    RELATIONSHIP = "relationship"
    TIMESERIES = "timeseries"
    COMPOSITION = "composition"
    MULTIVARIATE = "multivariate"
    GEO = "geo"
    ECONOMETRIC = "econometric"
    SPECIALIZED = "specialized"


class PlotType(StrEnum):
    # Plots.md §1 Basic
    BAR = "bar"
    HORIZONTAL_BAR = "horizontal_bar"
    GROUPED_BAR = "grouped_bar"
    STACKED_BAR = "stacked_bar"
    LINE = "line"
    AREA = "area"
    PIE = "pie"
    DONUT = "donut"
    # §2 Statistical distribution
    HISTOGRAM = "histogram"
    DENSITY = "density"
    BOX = "box"
    VIOLIN = "violin"
    STRIP = "strip"
    SWARM = "swarm"
    QQ = "qq"
    ECDF = "ecdf"
    FREQUENCY_POLYGON = "frequency_polygon"
    # §3 Relationship & correlation
    SCATTER = "scatter"
    BUBBLE = "bubble"
    PAIR = "pair"
    CORRELATION_HEATMAP = "correlation_heatmap"
    CORRELOGRAM = "correlogram"
    HEXBIN = "hexbin"
    CONTOUR = "contour"
    REGRESSION = "regression"
    RESIDUAL = "residual"
    # §4 Time series
    TIMESERIES_LINE = "timeseries_line"
    SEASONAL = "seasonal"
    LAG = "lag"
    ACF = "acf"
    PACF = "pacf"
    MOVING_AVERAGE = "moving_average"
    CANDLESTICK = "candlestick"
    CALENDAR_HEATMAP = "calendar_heatmap"
    # §5 Comparison & composition
    STACKED_AREA = "stacked_area"
    PERCENT_STACKED_BAR = "percent_stacked_bar"
    WATERFALL = "waterfall"
    FUNNEL = "funnel"
    TREEMAP = "treemap"
    SUNBURST = "sunburst"
    MOSAIC = "mosaic"
    MARIMEKKO = "marimekko"
    # §6 Multivariate
    RADAR = "radar"
    PARALLEL_COORDINATES = "parallel_coordinates"
    PCA_BIPLOT = "pca_biplot"
    FACTOR_LOADING = "factor_loading"
    CLUSTER = "cluster"
    DENDROGRAM = "dendrogram"
    ANDREWS_CURVES = "andrews_curves"
    # §7 Geospatial
    CHOROPLETH = "choropleth"
    BUBBLE_MAP = "bubble_map"
    DOT_DENSITY = "dot_density"
    CARTOGRAM = "cartogram"
    GEO_HEATMAP = "geo_heatmap"
    # §8 Statistical & econometric
    KAPLAN_MEIER = "kaplan_meier"
    FOREST = "forest"
    ROC = "roc"
    PRECISION_RECALL = "precision_recall"
    LORENZ = "lorenz"
    QUANTILE = "quantile"
    MARGINAL_EFFECTS = "marginal_effects"
    COEFFICIENT = "coefficient"
    IMPULSE_RESPONSE = "impulse_response"
    EVENT_STUDY = "event_study"
    # §9 Specialized
    SANKEY = "sankey"
    NETWORK = "network"
    ALLUVIAL = "alluvial"
    GANTT = "gantt"
    PARETO = "pareto"
    ERROR_BAR = "error_bar"
    LOLLIPOP = "lollipop"
    DUMBBELL = "dumbbell"
    RIDGELINE = "ridgeline"
    RAINCLOUD = "raincloud"


_FAMILY_MEMBERS: dict[PlotFamily, tuple[PlotType, ...]] = {
    PlotFamily.BASIC: (
        PlotType.BAR,
        PlotType.HORIZONTAL_BAR,
        PlotType.GROUPED_BAR,
        PlotType.STACKED_BAR,
        PlotType.LINE,
        PlotType.AREA,
        PlotType.PIE,
        PlotType.DONUT,
    ),
    PlotFamily.DISTRIBUTION: (
        PlotType.HISTOGRAM,
        PlotType.DENSITY,
        PlotType.BOX,
        PlotType.VIOLIN,
        PlotType.STRIP,
        PlotType.SWARM,
        PlotType.QQ,
        PlotType.ECDF,
        PlotType.FREQUENCY_POLYGON,
    ),
    PlotFamily.RELATIONSHIP: (
        PlotType.SCATTER,
        PlotType.BUBBLE,
        PlotType.PAIR,
        PlotType.CORRELATION_HEATMAP,
        PlotType.CORRELOGRAM,
        PlotType.HEXBIN,
        PlotType.CONTOUR,
        PlotType.REGRESSION,
        PlotType.RESIDUAL,
    ),
    PlotFamily.TIMESERIES: (
        PlotType.TIMESERIES_LINE,
        PlotType.SEASONAL,
        PlotType.LAG,
        PlotType.ACF,
        PlotType.PACF,
        PlotType.MOVING_AVERAGE,
        PlotType.CANDLESTICK,
        PlotType.CALENDAR_HEATMAP,
    ),
    PlotFamily.COMPOSITION: (
        PlotType.STACKED_AREA,
        PlotType.PERCENT_STACKED_BAR,
        PlotType.WATERFALL,
        PlotType.FUNNEL,
        PlotType.TREEMAP,
        PlotType.SUNBURST,
        PlotType.MOSAIC,
        PlotType.MARIMEKKO,
    ),
    PlotFamily.MULTIVARIATE: (
        PlotType.RADAR,
        PlotType.PARALLEL_COORDINATES,
        PlotType.PCA_BIPLOT,
        PlotType.FACTOR_LOADING,
        PlotType.CLUSTER,
        PlotType.DENDROGRAM,
        PlotType.ANDREWS_CURVES,
    ),
    PlotFamily.GEO: (
        PlotType.CHOROPLETH,
        PlotType.BUBBLE_MAP,
        PlotType.DOT_DENSITY,
        PlotType.CARTOGRAM,
        PlotType.GEO_HEATMAP,
    ),
    PlotFamily.ECONOMETRIC: (
        PlotType.KAPLAN_MEIER,
        PlotType.FOREST,
        PlotType.ROC,
        PlotType.PRECISION_RECALL,
        PlotType.LORENZ,
        PlotType.QUANTILE,
        PlotType.MARGINAL_EFFECTS,
        PlotType.COEFFICIENT,
        PlotType.IMPULSE_RESPONSE,
        PlotType.EVENT_STUDY,
    ),
    PlotFamily.SPECIALIZED: (
        PlotType.SANKEY,
        PlotType.NETWORK,
        PlotType.ALLUVIAL,
        PlotType.GANTT,
        PlotType.PARETO,
        PlotType.ERROR_BAR,
        PlotType.LOLLIPOP,
        PlotType.DUMBBELL,
        PlotType.RIDGELINE,
        PlotType.RAINCLOUD,
    ),
}

PLOT_FAMILY: dict[PlotType, PlotFamily] = {
    plot: family for family, plots in _FAMILY_MEMBERS.items() for plot in plots
}


class ChannelInfo(Contract):
    """A channel a plot's server module validates. Server-produced."""

    name: str
    # None = any column type.
    accepts: list[ColumnSemantic] | None
    # 0 = optional.
    min: int
    # None = unbounded.
    max: int | None


class PlotInfo(Contract):
    type: PlotType
    family: PlotFamily
    # From the plot's server module; None when it uses the generic aggregate path.
    channels: list[ChannelInfo] | None = None
    # JSON schema of the plot's params model; None when it has no server module.
    params_schema: dict[str, JsonValue] | None = None


class TimeUnit(StrEnum):
    YEAR = "year"
    QUARTER = "quarter"
    MONTH = "month"
    WEEK = "week"
    DAY = "day"
    HOUR = "hour"
    MINUTE = "minute"


class Encoding(Contract):
    """One field bound to a channel. `field` may be None only for count(*)."""

    field: str | None = None
    agg: AggFn | None = None
    time_unit: TimeUnit | None = None
    sort: Literal["asc", "desc"] | None = None
    label: str | None = None
    format: str | None = None


class AxisStyle(Contract):
    # None means pick automatically from the field's semantic.
    scale: Literal["linear", "log", "time", "category"] | None = None
    title: str | None = None
    format: str | None = None
    min: float | str | None = None
    max: float | str | None = None
    grid: bool = True
    # Where the axis sits: "start" = bottom/left, "end" = top/right. None = automatic.
    position: Literal["start", "end"] | None = None
    inverse: bool | None = None
    tick_rotate: float | None = None
    # Log scale base; None = 10.
    log_base: float | None = None


class LegendStyle(Contract):
    show: bool = True
    position: Literal["top", "bottom", "left", "right"] = "top"


class Annotation(Contract):
    kind: Literal["point", "area", "text"]
    x: float | str | None = None
    y: float | str | None = None
    x2: float | str | None = None
    y2: float | str | None = None
    text: str | None = None


class RefLine(Contract):
    axis: Literal["x", "y"]
    value: float | str
    label: str | None = None
    line_style: Literal["solid", "dashed", "dotted"] = "dashed"


class TrendlineKind(StrEnum):
    LINEAR = "linear"
    POLYNOMIAL = "polynomial"
    EXPONENTIAL = "exponential"
    LOGARITHMIC = "logarithmic"
    POWER = "power"
    LOESS = "loess"
    MOVING_AVERAGE = "moving_average"


class Trendline(Contract):
    """A server-computed overlay fitted to one y series."""

    kind: TrendlineKind = TrendlineKind.LINEAR
    # Index into the y channel.
    series: int = 0
    # Polynomial degree.
    degree: int = 2
    # Moving-average window, in points.
    window: int | None = None
    # Confidence band level, e.g. 0.95; None = no band.
    ci_level: float | None = None
    show_equation: bool = False
    show_r2: bool = False


class ErrorBars(Contract):
    kind: Literal["sd", "se", "ci", "fixed"] = "sd"
    # Confidence level when kind is "ci".
    level: float = 0.95
    # Half-width when kind is "fixed".
    value: float | None = None


class SeriesStyle(Contract):
    """Per-series overrides, keyed by index into the y channel."""

    index: int
    # Which entry of ChartStyle.y_axes this series uses.
    y_axis: int = 0
    color: str | None = None
    # Combo charts: draw this series as a different mark.
    kind: Literal["bar", "line", "area", "scatter"] | None = None
    marker: bool | None = None
    line_width: float | None = None
    line_style: Literal["solid", "dashed", "dotted"] | None = None


class TooltipStyle(Contract):
    show: bool = True
    # None = the plot's default trigger.
    trigger: Literal["axis", "item"] | None = None
    format: str | None = None


class ChartStyle(Contract):
    title: str | None = None
    subtitle: str | None = None
    title_position: Literal["top", "bottom"] = "top"
    x_axis: AxisStyle = Field(default_factory=AxisStyle)
    # More than one entry gives multiple Y axes; series pick one by index.
    y_axes: list[AxisStyle] = Field(default_factory=lambda: [AxisStyle()])
    legend: LegendStyle = Field(default_factory=LegendStyle)
    palette: str | None = None
    orientation: Literal["vertical", "horizontal"] = "vertical"
    stack: Literal["none", "stack", "percent"] = "none"
    show_labels: bool = False
    markers: bool | None = None
    line_width: float | None = None
    line_style: Literal["solid", "dashed", "dotted"] | None = None
    number_format: str | None = None
    annotations: list[Annotation] = Field(default_factory=list)
    ref_lines: list[RefLine] = Field(default_factory=list)
    template_id: str | None = None
    series: list[SeriesStyle] = Field(default_factory=list)
    trendlines: list[Trendline] = Field(default_factory=list)
    error_bars: ErrorBars | None = None
    tooltip: TooltipStyle = Field(default_factory=TooltipStyle)
    font_family: str | None = None
    font_size: float | None = None
    smooth: bool | None = None
    area_opacity: float | None = None
    label_position: Literal["auto", "inside", "outside", "top", "end"] | None = None


class Interactions(Contract):
    zoom: bool = True
    brush: bool = False
    click_filter: bool = True
    drill_hierarchy: list[str] = Field(default_factory=list)
    link_group: str | None = None


class ChartSpec(Contract):
    id: str
    name: str = "Untitled chart"
    plot_type: PlotType
    source: SourceRef
    # Channel name → encodings, as declared by the plot's registry entry (x, y, color, size, …).
    encodings: dict[str, list[Encoding]] = Field(default_factory=dict)
    params: dict[str, JsonValue] = Field(default_factory=dict)
    style: ChartStyle = Field(default_factory=ChartStyle)
    interactions: Interactions = Field(default_factory=Interactions)
    filters: list[Predicate] = Field(default_factory=list)
