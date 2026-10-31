"""Project persistence with optimistic concurrency, snapshots and restore.

A save must carry the revision it was based on. Explicit saves always write a snapshot;
every Nth autosave does too, and older autosave snapshots are pruned. Restoring a version
writes it as a new revision, so history is never rewritten.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from viz.config import Settings
from viz.contracts import Project, ProjectSummary, ProjectVersion, migrations, new_id
from viz.contracts.migrations import NewerSchemaError, upgrade_project
from viz.errors import ConflictError, InvalidSpecError, NotFoundError
from viz.meta.models import ProjectRow, ProjectVersionRow, ShareTokenRow, as_utc, utcnow
from viz.projects.validate import validate_project

# Fields the server owns; ignored when deciding whether an autosave changed anything.
_SERVER_FIELDS = {"revision", "updated_at", "created_at", "schema_version"}


def load_doc(doc: dict[str, Any]) -> Project:
    """Upgrade a stored or imported document to the current schema and validate it."""
    try:
        upgraded = upgrade_project(dict(doc))
    except NewerSchemaError as exc:
        raise ConflictError(str(exc)) from exc
    return Project.model_validate(upgraded)


def _row(db: Session, project_id: str) -> ProjectRow:
    row = db.get(ProjectRow, project_id)
    if row is None:
        raise NotFoundError(f"project {project_id} not found")
    return row


def _stale(project_revision: int, row: ProjectRow) -> ConflictError:
    return ConflictError(
        f"project changed since revision {project_revision} (now {row.revision}); "
        "reload to continue"
    )


MAX_NAME = 200


def check_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise InvalidSpecError("name must not be empty")
    if len(cleaned) > MAX_NAME:
        raise InvalidSpecError(f"name must be at most {MAX_NAME} characters")
    return cleaned


def _cas_update(db: Session, row: ProjectRow, values: dict[str, Any]) -> None:
    """Update the project only if nobody changed it since we read `row.revision`.

    The revision check in the WHERE clause makes check-and-write atomic, so two sessions
    that both read the same revision cannot both write.
    """
    expected, row_id = row.revision, row.id
    result = db.execute(
        update(ProjectRow)
        .where(ProjectRow.id == row_id, ProjectRow.revision == expected)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        db.rollback()
        db.expire_all()
        if db.get(ProjectRow, row_id) is None:
            raise NotFoundError(f"project {row_id} not found")
        raise ConflictError(f"project changed since revision {expected}; reload to continue")


def list_projects(db: Session) -> list[ProjectSummary]:
    rows = db.scalars(select(ProjectRow).order_by(ProjectRow.updated_at.desc())).all()
    return [
        ProjectSummary(id=r.id, name=r.name, revision=r.revision, updated_at=as_utc(r.updated_at))
        for r in rows
    ]


def _add_row(db: Session, project: Project) -> None:
    db.add(
        ProjectRow(
            id=project.id,
            name=project.name,
            revision=project.revision,
            doc=project.model_dump(mode="json"),
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
    )


def create_project(db: Session, name: str) -> Project:
    now = utcnow()
    project = Project(id=new_id(), name=check_name(name), created_at=now, updated_at=now)
    _add_row(db, project)
    db.commit()
    return project


def create_from_doc(db: Session, project: Project) -> Project:
    """Store a validated project under a fresh id at revision 0 (duplicate and import)."""
    validate_project(project)
    now = utcnow()
    fresh = project.model_copy(
        update={
            "id": new_id(),
            "revision": 0,
            "created_at": now,
            "updated_at": now,
        }
    )
    _add_row(db, fresh)
    db.commit()
    return fresh


def get_project(db: Session, project_id: str) -> Project:
    return load_doc(_row(db, project_id).doc)


def delete_project(db: Session, project_id: str) -> None:
    row = _row(db, project_id)
    db.execute(delete(ProjectVersionRow).where(ProjectVersionRow.project_id == project_id))
    db.execute(delete(ShareTokenRow).where(ShareTokenRow.project_id == project_id))
    db.delete(row)
    db.commit()


def duplicate_project(db: Session, project_id: str, name: str | None) -> Project:
    source = get_project(db, project_id)
    if name is not None:
        new_name = check_name(name)
    else:
        suffix = " copy"
        new_name = f"{source.name[: MAX_NAME - len(suffix)]}{suffix}".strip()
    return create_from_doc(db, source.model_copy(update={"name": new_name}))


def _snapshot(
    db: Session, project_id: str, revision: int, kind: str, doc: dict[str, Any], label: str | None
) -> None:
    db.add(
        ProjectVersionRow(
            project_id=project_id,
            revision=revision,
            kind=kind,
            label=label,
            doc=doc,
            created_at=utcnow(),
        )
    )


def _prune_autosaves(db: Session, project_id: str, keep: int) -> None:
    stmt = (
        select(ProjectVersionRow.id)
        .where(
            ProjectVersionRow.project_id == project_id,
            ProjectVersionRow.kind == "autosave",
            ProjectVersionRow.label.is_(None),
        )
        .order_by(ProjectVersionRow.revision.desc(), ProjectVersionRow.id.desc())
    )
    old = list(db.scalars(stmt))[max(keep, 0) :]
    if old:
        db.execute(delete(ProjectVersionRow).where(ProjectVersionRow.id.in_(old)))


def _write(
    db: Session,
    row: ProjectRow,
    project: Project,
    *,
    kind: Literal["save", "autosave"],
    label: str | None,
    settings: Settings,
) -> Project:
    now = utcnow()
    saved = project.model_copy(
        update={
            "revision": row.revision + 1,
            "schema_version": migrations.CURRENT_SCHEMA_VERSION,
            "updated_at": now,
            "created_at": project.created_at or as_utc(row.created_at),
        }
    )
    doc = saved.model_dump(mode="json")
    counter = row.autosaves_since_snapshot + 1
    snapshot_kind: str | None = None
    if kind == "save":
        snapshot_kind, counter = "save", 0
    else:
        every = settings.autosave_snapshot_every
        if every > 0 and counter >= every:
            snapshot_kind, counter = "autosave", 0
    _cas_update(
        db,
        row,
        {
            "name": saved.name,
            "revision": saved.revision,
            "doc": doc,
            "updated_at": now,
            "autosaves_since_snapshot": counter,
        },
    )
    if snapshot_kind is not None:
        _snapshot(db, row.id, saved.revision, snapshot_kind, doc, label if kind == "save" else None)
        if snapshot_kind == "autosave":
            db.flush()
            _prune_autosaves(db, row.id, settings.autosave_snapshots_keep)
    db.commit()
    return saved


def _content(project: Project) -> dict[str, Any]:
    return project.model_dump(mode="json", exclude=_SERVER_FIELDS)


def save_project(
    db: Session,
    project_id: str,
    project: Project,
    *,
    autosave: bool,
    settings: Settings,
    label: str | None = None,
) -> Project:
    if project.id != project_id:
        raise InvalidSpecError("project id in the body does not match the URL")
    row = _row(db, project_id)
    if project.revision != row.revision:
        raise _stale(project.revision, row)
    validate_project(project)
    if autosave:
        stored = load_doc(row.doc)
        if _content(stored) == _content(project):
            return stored  # nothing changed: no revision bump, no snapshot
    return _write(
        db,
        row,
        project,
        kind="autosave" if autosave else "save",
        label=None if autosave else label,
        settings=settings,
    )


def rename_project(db: Session, project_id: str, name: str, revision: int) -> Project:
    row = _row(db, project_id)
    if revision != row.revision:
        raise _stale(revision, row)
    project = load_doc(row.doc).model_copy(update={"name": check_name(name)})
    now = utcnow()
    saved = project.model_copy(
        update={
            "revision": row.revision + 1,
            "schema_version": migrations.CURRENT_SCHEMA_VERSION,
            "updated_at": now,
        }
    )
    _cas_update(
        db,
        row,
        {
            "name": saved.name,
            "revision": saved.revision,
            "doc": saved.model_dump(mode="json"),
            "updated_at": now,
        },
    )
    db.commit()
    return saved


def _version_row(db: Session, project_id: str, version_id: int) -> ProjectVersionRow:
    _row(db, project_id)
    version = db.get(ProjectVersionRow, version_id)
    if version is None or version.project_id != project_id:
        raise NotFoundError(f"version {version_id} not found")
    return version


def get_version(db: Session, project_id: str, version_id: int) -> Project:
    return load_doc(_version_row(db, project_id, version_id).doc)


def restore_version(
    db: Session, project_id: str, version_id: int, revision: int, settings: Settings
) -> Project:
    row = _row(db, project_id)
    if revision != row.revision:
        raise _stale(revision, row)
    version = _version_row(db, project_id, version_id)
    snapshot = load_doc(version.doc).model_copy(update={"id": project_id})
    validate_project(snapshot)
    return _write(
        db,
        row,
        snapshot.model_copy(update={"revision": row.revision}),
        kind="save",
        label=f"Restored from r{version.revision}",
        settings=settings,
    )


def list_versions(
    db: Session,
    project_id: str,
    *,
    kind: Literal["save", "autosave"] | None = None,
    limit: int | None = None,
) -> list[ProjectVersion]:
    _row(db, project_id)
    # Select only the light columns: the snapshot docs can be large.
    stmt = select(
        ProjectVersionRow.id,
        ProjectVersionRow.project_id,
        ProjectVersionRow.revision,
        ProjectVersionRow.kind,
        ProjectVersionRow.label,
        ProjectVersionRow.created_at,
    ).where(ProjectVersionRow.project_id == project_id)
    if kind is not None:
        stmt = stmt.where(ProjectVersionRow.kind == kind)
    stmt = stmt.order_by(ProjectVersionRow.revision.desc(), ProjectVersionRow.id.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return [
        ProjectVersion(
            id=r.id,
            project_id=r.project_id,
            revision=r.revision,
            kind="autosave" if r.kind == "autosave" else "save",
            label=r.label,
            created_at=_aware(r.created_at),
        )
        for r in db.execute(stmt)
    ]


def _aware(value: datetime) -> datetime:
    return as_utc(value)
