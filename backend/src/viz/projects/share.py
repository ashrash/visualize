"""Read-only share/embed tokens and report bundles.

A token is an unguessable random id pointing at one dashboard or chart of a project. Anyone
holding it can fetch the item plus the specs needed to draw it (the live project document, so
edits show up). Minting, listing and revoking require the current user; resolving does not.

Limitation (local-first): a token gates the *specs*, not the data. Chart-data requests still go
through the normal `/api/charts/data` route. Real access control arrives with AWS and auth.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from viz.contracts import ChartSpec, Dashboard, Pipeline, Project
from viz.contracts.base import Contract, SourceRef
from viz.contracts.dashboard import DashboardFilter
from viz.contracts.project import Theme
from viz.errors import NotFoundError, VizError
from viz.meta.models import ProjectRow, ShareTokenRow, as_utc, utcnow
from viz.projects.service import get_project

ShareKind = Literal["dashboard", "chart"]
# "project" is the whole-project report: every chart, no dashboard.
ViewKind = Literal["dashboard", "chart", "project"]
MAX_EXPIRY_DAYS = 3650


class ShareCreate(Contract):
    project_id: str
    kind: ShareKind
    item_id: str
    # None means the link never expires (it can still be revoked).
    expires_in_days: int | None = Field(default=None, ge=1, le=MAX_EXPIRY_DAYS)


class ShareLink(Contract):
    token: str
    project_id: str
    kind: ShareKind
    item_id: str
    created_at: datetime
    expires_at: datetime | None
    revoked: bool
    # Frontend routes; prefix with the page origin to get a full URL.
    share_path: str
    embed_path: str


class SharedView(Contract):
    """Everything the share/embed/report views need; chart data is fetched separately."""

    kind: ViewKind
    project_id: str
    project_name: str
    # Set when kind == "dashboard".
    dashboard: Dashboard | None
    # Chart(s) to draw: the one chart, or every chart the dashboard references.
    charts: list[ChartSpec]
    # Passed through to /api/charts/data, which never loads pipelines itself.
    pipelines: list[Pipeline]
    dataset_ids: list[str]
    themes: list[Theme]
    # Project-wide filters; viewers must see the same filtered data as the owner.
    global_filters: list[DashboardFilter]
    generated_at: datetime


def _link(row: ShareTokenRow) -> ShareLink:
    return ShareLink(
        token=row.token,
        project_id=row.project_id,
        kind=row.kind,
        item_id=row.item_id,
        created_at=as_utc(row.created_at),
        expires_at=as_utc(row.expires_at) if row.expires_at else None,
        revoked=row.revoked_at is not None,
        share_path=f"/share/{row.token}",
        embed_path=f"/embed/{row.token}",
    )


def _check_item(project: Project, kind: str, item_id: str) -> None:
    items = project.dashboards if kind == "dashboard" else project.charts
    if not any(i.id == item_id for i in items):
        raise NotFoundError(f"{kind} {item_id} not found in project {project.id}")


def create_share(db: Session, user_id: str, body: ShareCreate) -> ShareLink:
    project = get_project(db, body.project_id)
    _check_item(project, body.kind, body.item_id)
    now = utcnow()
    row = ShareTokenRow(
        token=secrets.token_urlsafe(32),
        project_id=body.project_id,
        kind=body.kind,
        item_id=body.item_id,
        created_by=user_id,
        created_at=now,
        expires_at=now + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    db.add(row)
    db.commit()
    return _link(row)


def list_shares(db: Session, project_id: str) -> list[ShareLink]:
    if db.get(ProjectRow, project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    rows = db.scalars(
        select(ShareTokenRow)
        .where(ShareTokenRow.project_id == project_id)
        .order_by(ShareTokenRow.created_at.desc())
    ).all()
    return [_link(r) for r in rows]


def revoke_share(db: Session, token: str) -> None:
    row = db.get(ShareTokenRow, token)
    if row is None:
        raise NotFoundError("share link not found")
    if row.revoked_at is None:
        row.revoked_at = utcnow()
        db.commit()


def _reachable(
    project: Project, charts: list[ChartSpec], extra: list[SourceRef]
) -> tuple[list[Pipeline], list[str]]:
    """Pipelines and dataset ids reachable from the given charts, following sources, joins
    and unions transitively. Nothing else in the project is exposed."""
    pipelines_by_id = {p.id: p for p in project.pipelines}
    pipeline_ids: list[str] = []
    dataset_ids: list[str] = []
    stack: list[SourceRef] = [c.source for c in charts] + extra
    while stack:
        ref = stack.pop()
        if ref.kind == "dataset":
            if ref.id not in dataset_ids:
                dataset_ids.append(ref.id)
            continue
        pipeline = pipelines_by_id.get(ref.id)
        if pipeline is None or ref.id in pipeline_ids:
            continue
        pipeline_ids.append(ref.id)
        stack.append(pipeline.source)
        for step in pipeline.steps:
            if step.op == "join":
                stack.append(step.right)
            elif step.op == "union":
                stack.extend(step.others)
    pipelines = [p for p in project.pipelines if p.id in pipeline_ids]
    return pipelines, dataset_ids


def _view(
    project: Project,
    kind: ViewKind,
    dashboard: Dashboard | None,
    charts: list[ChartSpec],
    extra_sources: list[SourceRef],
) -> SharedView:
    pipelines, dataset_ids = _reachable(project, charts, extra_sources)
    return SharedView(
        kind=kind,
        project_id=project.id,
        project_name=project.name,
        dashboard=dashboard,
        charts=charts,
        pipelines=pipelines,
        dataset_ids=dataset_ids,
        themes=project.themes,
        global_filters=project.global_filters,
        generated_at=utcnow(),
    )


# TODO(platform): a token only gates the specs. A viewer then draws charts through the normal
# /api/charts/data route, which works only because the local auth stub is a no-op. Hosted mode
# needs a token-scoped data route (e.g. /api/share/{token}/charts/{chartId}/data) that checks the
# chart belongs to the shared item, so a public token never grants general dataset access.
def build_view(project: Project, kind: ShareKind, item_id: str) -> SharedView:
    """Bundle one dashboard or chart with the specs needed to draw it."""
    charts_by_id = {c.id: c for c in project.charts}
    if kind == "chart":
        if item_id not in charts_by_id:
            raise NotFoundError("shared item no longer exists")
        return _view(project, "chart", None, [charts_by_id[item_id]], [])
    dashboard = next((d for d in project.dashboards if d.id == item_id), None)
    if dashboard is None:
        raise NotFoundError("shared item no longer exists")
    ids: list[str] = []
    kpi_sources: list[SourceRef] = []
    for tab in dashboard.tabs:
        for item in tab.items:
            widget = item.widget
            if widget.kind == "chart" and widget.chart_id not in ids:
                ids.append(widget.chart_id)
            elif widget.kind == "kpi":
                kpi_sources.append(widget.source)
    charts = [charts_by_id[i] for i in ids if i in charts_by_id]
    return _view(project, "dashboard", dashboard, charts, kpi_sources)


def resolve_share(db: Session, token: str) -> SharedView:
    """Resolve a token. Every failure, internal ones included, looks the same: 404."""
    row = db.get(ShareTokenRow, token)
    if row is None or row.revoked_at is not None:
        raise NotFoundError("share link not found")
    if row.expires_at is not None and as_utc(row.expires_at) <= utcnow():
        raise NotFoundError("share link not found")
    try:
        project = get_project(db, row.project_id)
        return build_view(project, row.kind, row.item_id)  # type: ignore[arg-type]
    except VizError as exc:
        raise NotFoundError("share link not found") from exc


def report_view(db: Session, project_id: str, dashboard_id: str | None) -> SharedView:
    """Report bundle for a dashboard, or for the whole project (possibly empty) without an id."""
    project = get_project(db, project_id)
    if dashboard_id is not None:
        return build_view(project, "dashboard", dashboard_id)
    dashboard_sources = [
        item.widget.source
        for d in project.dashboards
        for tab in d.tabs
        for item in tab.items
        if item.widget.kind == "kpi"
    ]
    return _view(project, "project", None, project.charts, dashboard_sources)
