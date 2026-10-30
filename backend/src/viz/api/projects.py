"""Project endpoints: CRUD, autosave, versions, restore, duplicate, .vizproj export/import."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Query, Response, UploadFile
from starlette.background import BackgroundTask
from starlette.responses import FileResponse

from viz.api.deps import DbDep, SettingsDep, StorageDep
from viz.contracts import (
    Project,
    ProjectCreate,
    ProjectDuplicate,
    ProjectImportResult,
    ProjectRename,
    ProjectRestore,
    ProjectSummary,
    ProjectVersion,
)
from viz.projects import service, vizproj

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectSummary])
def list_projects(db: DbDep) -> list[ProjectSummary]:
    return service.list_projects(db)


@router.post("", response_model=Project, status_code=201)
def create_project(body: ProjectCreate, db: DbDep) -> Project:
    return service.create_project(db, body.name)


@router.post("/import", response_model=ProjectImportResult, status_code=201)
def import_project(
    db: DbDep, storage: StorageDep, settings: SettingsDep, file: Annotated[UploadFile, File()]
) -> ProjectImportResult:
    return vizproj.import_project(db, storage, settings, file.file)


@router.get("/{project_id}", response_model=Project)
def get_project(project_id: str, db: DbDep) -> Project:
    return service.get_project(db, project_id)


@router.put("/{project_id}", response_model=Project)
def save_project(
    project_id: str,
    body: Project,
    db: DbDep,
    settings: SettingsDep,
    autosave: Annotated[bool, Query()] = False,
    label: Annotated[str | None, Query(max_length=255)] = None,
) -> Project:
    return service.save_project(
        db, project_id, body, autosave=autosave, settings=settings, label=label
    )


@router.patch("/{project_id}", response_model=Project)
def rename_project(project_id: str, body: ProjectRename, db: DbDep) -> Project:
    return service.rename_project(db, project_id, body.name, body.revision)


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, db: DbDep) -> Response:
    service.delete_project(db, project_id)
    return Response(status_code=204)


@router.post("/{project_id}/duplicate", response_model=Project, status_code=201)
def duplicate_project(project_id: str, body: ProjectDuplicate, db: DbDep) -> Project:
    return service.duplicate_project(db, project_id, body.name)


@router.get("/{project_id}/versions", response_model=list[ProjectVersion])
def list_versions(
    project_id: str,
    db: DbDep,
    kind: Annotated[Literal["save", "autosave"] | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
) -> list[ProjectVersion]:
    return service.list_versions(db, project_id, kind=kind, limit=limit)


@router.get("/{project_id}/versions/{version_id}", response_model=Project)
def get_version(project_id: str, version_id: int, db: DbDep) -> Project:
    return service.get_version(db, project_id, version_id)


@router.post("/{project_id}/versions/{version_id}/restore", response_model=Project)
def restore_version(
    project_id: str, version_id: int, body: ProjectRestore, db: DbDep, settings: SettingsDep
) -> Project:
    return service.restore_version(db, project_id, version_id, body.revision, settings)


@router.get("/{project_id}/export", response_class=FileResponse)
def export_project(
    project_id: str,
    db: DbDep,
    storage: StorageDep,
    include_data: Annotated[bool, Query(alias="includeData")] = False,
) -> FileResponse:
    path, name = vizproj.export_project(db, storage, project_id, include_data=include_data)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=name,
        background=BackgroundTask(_remove, path),
    )


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)
