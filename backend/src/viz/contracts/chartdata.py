"""Chart-data protocol: what a chart asks for and what comes back.

Tabular responses are Arrow IPC streams. Their metadata (ChartDataMeta / RowsPageMeta)
travels as JSON under the `viz.meta` key of the Arrow schema metadata.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, JsonValue

from viz.contracts.base import Contract
from viz.contracts.chart import ChartSpec, TrendlineKind
from viz.contracts.pipeline import Pipeline
from viz.contracts.predicate import Predicate
from viz.contracts.stats import StatResult

ARROW_STREAM_MEDIA_TYPE = "application/vnd.apache.arrow.stream"
ARROW_META_KEY = "viz.meta"


class Viewport(Contract):
    width_px: int = 800
    height_px: int = 400
    # Visible x range after zoom, for detail-on-demand re-queries.
    x_range: tuple[float | str, float | str] | None = None


class ChartDataRequest(Contract):
    spec: ChartSpec
    # Filters from the dashboard, cross-filtering and drill-down (on top of spec.filters).
    filters: list[Predicate] = Field(default_factory=list)
    viewport: Viewport = Field(default_factory=Viewport)
    # Pipelines the source references (unsaved edits included); the server never loads them.
    pipelines: list[Pipeline] = Field(default_factory=list)
    # Cancellation slot; defaults to spec.id. Two widgets showing one chart use different slots.
    slot: str | None = None


class Reduction(StrEnum):
    NONE = "none"
    AGGREGATED = "aggregated"
    BINNED = "binned"
    DOWNSAMPLED = "downsampled"
    SAMPLED = "sampled"


class ChartDataMeta(Contract):
    """Server-produced only, so every field is required (and non-optional in TypeScript)."""

    rows_scanned: int
    rows_returned: int
    reduction: Reduction
    # e.g. "group_by", "m4", "lttb", "reservoir", "hexbin".
    method: str | None
    # Result column name → display label, e.g. {"y0": "Sum of sales"}.
    fields: dict[str, str]
    cached: bool
    ms: float
    warnings: list[str]
    # Rows after filters, before reduction; None when not counted.
    rows_matched: int | None
    # Resolved reduction parameters, e.g. binWidth, hexRadius, nOut, truncatedGroups.
    params: dict[str, JsonValue]
    # Full x extent ignoring viewport.xRange, so a zoom slider keeps its scale.
    x_domain: tuple[float | str, float | str] | None


class RowsPageMeta(Contract):
    offset: int
    limit: int
    # Rows after filters and search.
    total_rows: int
    # Rows in the dataset version, before filters.
    dataset_rows: int | None = None
    version: int | None = None


class ChartStatResponse(Contract):
    """JSON body of /api/charts/data for plots that use a stat transform. Server-produced."""

    result: StatResult
    meta: ChartDataMeta


class OverlayResult(Contract):
    """A server-computed trendline or regression fit, with an optional band. Server-produced."""

    kind: TrendlineKind
    # Index into the y channel (ChartStyle.trendlines[].series).
    series: int
    # The color/facet value this overlay belongs to, when the base series is split by a color or
    # facet encoding (e.g. one regression line per group, one KDE ridge per category). None when
    # the overlay applies to the whole (ungrouped) series.
    group: str | None = None
    # Temporal x travels as epoch milliseconds.
    x: list[float]
    y: list[float]
    lo: list[float] | None
    hi: list[float] | None
    equation: str | None
    r2: float | None
    n: int
    sampled: bool
    warnings: list[str]
