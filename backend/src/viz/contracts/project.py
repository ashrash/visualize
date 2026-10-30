"""Projects: the saved document holding everything a user builds."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from viz.contracts.base import Contract
from viz.contracts.chart import ChartSpec, ChartStyle
from viz.contracts.dashboard import Dashboard
from viz.contracts.pipeline import Pipeline


class ItemRef(Contract):
    kind: Literal["dataset", "pipeline", "chart", "dashboard"]
    id: str


class Folder(Contract):
    id: str
    name: str
    parent_id: str | None = None
    items: list[ItemRef] = Field(default_factory=list)


class Theme(Contract):
    id: str
    name: str
    mode: Literal["light", "dark", "auto"] = "auto"
    palette: list[str] = Field(default_factory=list)
    font_family: str | None = None
    background: str | None = None


class ChartTemplate(Contract):
    """A reusable style preset applied to charts."""

    id: str
    name: str
    style: ChartStyle


class Project(Contract):
    id: str
    name: str
    schema_version: int = 1
    # Optimistic concurrency: a save must send the revision it was based on.
    revision: int = 0
    # Datasets live in the shared catalog; a project references them.
    dataset_ids: list[str] = Field(default_factory=list)
    pipelines: list[Pipeline] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)
    dashboards: list[Dashboard] = Field(default_factory=list)
    folders: list[Folder] = Field(default_factory=list)
    themes: list[Theme] = Field(default_factory=list)
    templates: list[ChartTemplate] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProjectCreate(Contract):
    name: str


class ProjectSummary(Contract):
    id: str
    name: str
    revision: int
    updated_at: datetime


class ProjectVersion(Contract):
    id: int
    project_id: str
    revision: int
    kind: Literal["save", "autosave"]
    label: str | None = None
    created_at: datetime


class ProjectRename(Contract):
    name: str
    # The revision the rename is based on (optimistic concurrency).
    revision: int


class ProjectDuplicate(Contract):
    # Defaults to "<name> copy".
    name: str | None = None


class ProjectRestore(Contract):
    # The live revision the restore is based on.
    revision: int


class ProjectImportResult(Contract):
    """Server-produced only, so every field is required."""

    project: Project
    # Dataset ids registered from the archive.
    datasets_imported: list[str]
    # Archived dataset id -> new id, when an id collided with different content.
    datasets_remapped: dict[str, str]
    # Referenced by the project but in neither the archive nor the catalog.
    datasets_missing: list[str]
    warnings: list[str]
