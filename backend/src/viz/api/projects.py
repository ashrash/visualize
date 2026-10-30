"""Project endpoints: create, list, load, save (with revision check) and versions."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from viz.api.deps import DbDep
from viz.contracts import Project, ProjectCreate, ProjectSummary, ProjectVersion
from viz.projects import service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectSummary])
def list_projects(db: DbDep) -> list[ProjectSummary]:
    return service.list_projects(db)


@router.post("", response_model=Project, status_code=201)
def create_project(body: ProjectCreate, db: DbDep) -> Project:
    return service.create_project(db, body.name)


@router.get("/{project_id}", response_model=Project)
def get_project(project_id: str, db: DbDep) -> Project:
    return service.get_project(db, project_id)


@router.put("/{project_id}", response_model=Project)
def save_project(
    project_id: str,
    body: Project,
    db: DbDep,
    autosave: Annotated[bool, Query()] = False,
) -> Project:
    return service.save_project(db, project_id, body, autosave=autosave)


@router.get("/{project_id}/versions", response_model=list[ProjectVersion])
def list_versions(project_id: str, db: DbDep) -> list[ProjectVersion]:
    return service.list_versions(db, project_id)
