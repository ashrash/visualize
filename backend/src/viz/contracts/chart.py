"""Chart specs and the plot catalogue (every chart type in Plots.md).

Encodings and params are maps keyed by name: each plot declares its channels and params in
its registry entry, so adding a plot type never requires a contract change.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue

from viz.contracts.base import AggFn, Contract, SourceRef
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


class PlotInfo(Contract):
    type: PlotType
    family: PlotFamily


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


class ChartStyle(Contract):
    title: str | None = None
    subtitle: str | None = None
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
