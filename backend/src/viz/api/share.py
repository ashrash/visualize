"""Share, embed and report endpoints.

Minting, listing and revoking go through `router` (mounted under the authenticated API router).
Resolving a token and the embed alias are intentionally public: possession of the token is the
credential. They live on `public_router`, which the app mounts without `current_user`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from viz.api.deps import DbDep
from viz.auth import User, current_user, share_access
from viz.projects import share as service
from viz.projects.share import ShareCreate, SharedView, ShareLink

router = APIRouter(tags=["share"])
# Intentionally public: `share_access` names that at the call site. Add routes here only if the
# token is the credential.
public_router = APIRouter(tags=["share"], dependencies=[Depends(share_access)])

UserDep = Annotated[User, Depends(current_user)]


@router.post("/share", response_model=ShareLink, status_code=201)
def create_share(body: ShareCreate, db: DbDep, user: UserDep) -> ShareLink:
    return service.create_share(db, user.id, body)


@router.get("/share", response_model=list[ShareLink])
def list_shares(project_id: Annotated[str, Query(alias="projectId")], db: DbDep) -> list[ShareLink]:
    return service.list_shares(db, project_id)


@router.delete("/share/{token}", status_code=204)
def revoke_share(token: str, db: DbDep) -> Response:
    service.revoke_share(db, token)
    return Response(status_code=204)


@router.get("/reports/{project_id}", response_model=SharedView)
def project_report(project_id: str, db: DbDep) -> SharedView:
    """Report bundle with every chart of the project."""
    return service.report_view(db, project_id, None)


@router.get("/reports/{project_id}/dashboards/{dashboard_id}", response_model=SharedView)
def dashboard_report(project_id: str, dashboard_id: str, db: DbDep) -> SharedView:
    return service.report_view(db, project_id, dashboard_id)


@public_router.get("/share/{token}", response_model=SharedView)
def resolve_share(token: str, db: DbDep) -> SharedView:
    return service.resolve_share(db, token)


@public_router.get("/embed/{token}", response_model=SharedView)
def resolve_embed(token: str, db: DbDep) -> SharedView:
    return service.resolve_share(db, token)
