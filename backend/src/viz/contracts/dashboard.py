"""Dashboards: tabs of grid-laid-out widgets, plus filters and cross-filter link groups."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from viz.contracts.base import Contract, SourceRef
from viz.contracts.chart import Encoding
from viz.contracts.predicate import Predicate


class ChartWidget(Contract):
    kind: Literal["chart"]
    chart_id: str


class KpiWidget(Contract):
    kind: Literal["kpi"]
    title: str
    source: SourceRef
    measure: Encoding
    # Time field used for the sparkline and period-over-period delta.
    time: Encoding | None = None
    comparison: Literal["none", "previous_period", "target"] = "none"
    target: float | None = None
    format: str | None = None


class TextWidget(Contract):
    kind: Literal["text"]
    # Rendered as Markdown and sanitized with DOMPurify.
    markdown: str


class ImageWidget(Contract):
    kind: Literal["image"]
    asset_id: str
    alt: str
    fit: Literal["contain", "cover"] = "contain"


class FilterWidget(Contract):
    kind: Literal["filter"]
    filter_id: str
    control: Literal["date_range", "dropdown", "slider"]


Widget = Annotated[
    ChartWidget | KpiWidget | TextWidget | ImageWidget | FilterWidget,
    Field(discriminator="kind"),
]


class LayoutItem(Contract):
    """One widget placed on the 12-column grid (react-grid-layout units)."""

    i: str
    x: int
    y: int
    w: int
    h: int
    widget: Widget


class DashboardTab(Contract):
    id: str
    name: str = "Tab 1"
    items: list[LayoutItem] = Field(default_factory=list)


class DashboardFilter(Contract):
    id: str
    label: str
    field: str
    # Source id → field name, for datasets that name the same field differently.
    field_map: dict[str, str] = Field(default_factory=dict)
    # Current selection; None means no filtering.
    predicate: Predicate | None = None


class LinkGroup(Contract):
    id: str
    chart_ids: list[str] = Field(default_factory=list)


class Dashboard(Contract):
    id: str
    name: str = "Untitled dashboard"
    tabs: list[DashboardTab] = Field(default_factory=list)
    filters: list[DashboardFilter] = Field(default_factory=list)
    link_groups: list[LinkGroup] = Field(default_factory=list)
    theme_id: str | None = None
