"""Structural validation of a project document before it is stored."""

from __future__ import annotations

from collections.abc import Iterable

from viz.contracts import Project
from viz.errors import InvalidSpecError


def _first_dupe(ids: Iterable[str]) -> str | None:
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            return i
        seen.add(i)
    return None


def validate_project(project: Project) -> None:
    """Raise InvalidSpecError for clashing ids or folders that point at nothing."""
    for label, ids in (
        ("pipeline", [p.id for p in project.pipelines]),
        ("chart", [c.id for c in project.charts]),
        ("dashboard", [d.id for d in project.dashboards]),
        ("folder", [f.id for f in project.folders]),
    ):
        dupe = _first_dupe(ids)
        if dupe is not None:
            raise InvalidSpecError(f"duplicate {label} id: {dupe}")

    existing = {
        "dataset": set(project.dataset_ids),
        "pipeline": {p.id for p in project.pipelines},
        "chart": {c.id for c in project.charts},
        "dashboard": {d.id for d in project.dashboards},
    }
    folder_ids = {f.id for f in project.folders}
    parents = {f.id: f.parent_id for f in project.folders}
    placed: set[tuple[str, str]] = set()
    for folder in project.folders:
        if folder.parent_id is not None and folder.parent_id not in folder_ids:
            raise InvalidSpecError(f"folder '{folder.name}' has a missing parent folder")
        for ref in folder.items:
            if ref.id not in existing[ref.kind]:
                raise InvalidSpecError(f"folder '{folder.name}' refers to a missing {ref.kind}")
            key = (ref.kind, ref.id)
            if key in placed:
                raise InvalidSpecError(f"{ref.kind} {ref.id} is in more than one folder")
            placed.add(key)
    for start in parents:
        seen = {start}
        current = parents[start]
        while current is not None:
            if current in seen:
                raise InvalidSpecError("folders contain a cycle")
            seen.add(current)
            current = parents[current]
