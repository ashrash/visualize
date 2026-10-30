"""Chart-data protocol: what a chart asks for and what comes back.

Tabular responses are Arrow IPC streams. Their metadata (ChartDataMeta / RowsPageMeta)
travels as JSON under the `viz.meta` key of the Arrow schema metadata.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from viz.contracts.base import Contract
from viz.contracts.chart import ChartSpec
from viz.contracts.predicate import Predicate

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


class RowsPageMeta(Contract):
    offset: int
    limit: int
    total_rows: int
