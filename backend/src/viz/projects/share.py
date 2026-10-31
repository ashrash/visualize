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
from viz.contracts.base import Contract
from viz.contracts.project import Theme
from viz.errors import InvalidSpecError, NotFoundError
from viz.meta.models import ProjectRow, ShareTokenRow, as_utc, utcnow
from viz.projects.service import get_project

ShareKind = Literal["dashboard", "chart"]
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

    kind: ShareKind
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


def build_view(project: Project, kind: ShareKind, item_id: str) -> SharedView:
    """Bundle one dashboard or chart with the specs needed to draw it."""
    charts_by_id = {c.id: c for c in project.charts}
    dashboard: Dashboard | None = None
    if kind == "dashboard":
        dashboard = next((d for d in project.dashboards if d.id == item_id), None)
        if dashboard is None:
            raise NotFoundError("shared item no longer exists")
        ids: list[str] = []
        for tab in dashboard.tabs:
            for item in tab.items:
                if item.widget.kind == "chart" and item.widget.chart_id not in ids:
                    ids.append(item.widget.chart_id)
        charts = [charts_by_id[i] for i in ids if i in charts_by_id]
    else:
        if item_id not in charts_by_id:
            raise NotFoundError("shared item no longer exists")
        charts = [charts_by_id[item_id]]
    return SharedView(
        kind=kind,
        project_id=project.id,
        project_name=project.name,
        dashboard=dashboard,
        charts=charts,
        pipelines=project.pipelines,
        dataset_ids=project.dataset_ids,
        themes=project.themes,
        generated_at=utcnow(),
    )


def resolve_share(db: Session, token: str) -> SharedView:
    """Resolve a token. Unknown, revoked and expired tokens all look the same: 404."""
    row = db.get(ShareTokenRow, token)
    if row is None or row.revoked_at is not None:
        raise NotFoundError("share link not found")
    if row.expires_at is not None and as_utc(row.expires_at) <= utcnow():
        raise NotFoundError("share link not found")
    project = get_project(db, row.project_id)
    return build_view(project, row.kind, row.item_id)  # type: ignore[arg-type]


def report_view(db: Session, project_id: str, dashboard_id: str | None) -> SharedView:
    """Report bundle for a dashboard, or for every chart of the project when no id is given."""
    project = get_project(db, project_id)
    if dashboard_id is not None:
        return build_view(project, "dashboard", dashboard_id)
    if not project.charts:
        raise InvalidSpecError("project has no charts to report on")
    return SharedView(
        kind="chart",
        project_id=project.id,
        project_name=project.name,
        dashboard=None,
        charts=project.charts,
        pipelines=project.pipelines,
        dataset_ids=project.dataset_ids,
        themes=project.themes,
        generated_at=utcnow(),
    )
