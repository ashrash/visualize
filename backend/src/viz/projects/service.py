"""Project persistence with optimistic concurrency.

A save must carry the revision it was based on. Explicit saves also write a snapshot to
project_versions; autosaves don't (snapshot policy for autosaves arrives with version history).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from viz.contracts import Project, ProjectSummary, ProjectVersion, new_id
from viz.contracts.migrations import CURRENT_SCHEMA_VERSION, NewerSchemaError, upgrade_project
from viz.errors import ConflictError, InvalidSpecError, NotFoundError
from viz.meta.models import ProjectRow, ProjectVersionRow, as_utc, utcnow


def _load(row: ProjectRow) -> Project:
    try:
        doc = upgrade_project(dict(row.doc))
    except NewerSchemaError as exc:
        raise ConflictError(str(exc)) from exc
    return Project.model_validate(doc)


def list_projects(db: Session) -> list[ProjectSummary]:
    rows = db.scalars(select(ProjectRow).order_by(ProjectRow.updated_at.desc())).all()
    return [
        ProjectSummary(id=r.id, name=r.name, revision=r.revision, updated_at=as_utc(r.updated_at))
        for r in rows
    ]


def create_project(db: Session, name: str) -> Project:
    now = utcnow()
    project = Project(id=new_id(), name=name, created_at=now, updated_at=now)
    db.add(
        ProjectRow(
            id=project.id,
            name=project.name,
            revision=project.revision,
            doc=project.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )
    )
    db.commit()
    return project


def get_project(db: Session, project_id: str) -> Project:
    row = db.get(ProjectRow, project_id)
    if row is None:
        raise NotFoundError(f"project {project_id} not found")
    return _load(row)


def save_project(db: Session, project_id: str, project: Project, *, autosave: bool) -> Project:
    if project.id != project_id:
        raise InvalidSpecError("project id in the body does not match the URL")
    row = db.get(ProjectRow, project_id)
    if row is None:
        raise NotFoundError(f"project {project_id} not found")
    if project.revision != row.revision:
        raise ConflictError(
            f"project changed since revision {project.revision} (now {row.revision}); "
            "reload to continue"
        )
    now = utcnow()
    saved = project.model_copy(
        update={
            "revision": row.revision + 1,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "updated_at": now,
            "created_at": project.created_at or as_utc(row.created_at),
        }
    )
    doc = saved.model_dump(mode="json")
    row.name = saved.name
    row.revision = saved.revision
    row.doc = doc
    row.updated_at = now
    if not autosave:
        db.add(
            ProjectVersionRow(
                project_id=project_id, revision=saved.revision, kind="save", doc=doc, created_at=now
            )
        )
    db.commit()
    return saved


def list_versions(db: Session, project_id: str) -> list[ProjectVersion]:
    if db.get(ProjectRow, project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    rows = db.scalars(
        select(ProjectVersionRow)
        .where(ProjectVersionRow.project_id == project_id)
        .order_by(ProjectVersionRow.revision.desc())
    ).all()
    return [
        ProjectVersion(
            id=r.id,
            project_id=r.project_id,
            revision=r.revision,
            kind="autosave" if r.kind == "autosave" else "save",
            label=r.label,
            created_at=as_utc(r.created_at),
        )
        for r in rows
    ]
