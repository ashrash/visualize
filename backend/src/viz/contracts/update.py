"""Self-update check and apply status. Local mode only: checks the project's GitHub Releases
for a newer version. Disabled or always "unsupported" in a hosted deployment, since there a
container image is rebuilt and redeployed, never self-upgraded in place."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from viz.contracts.base import Contract


class UpdateStatus(StrEnum):
    # No newer release is known, or this install can't self-update (e.g. a dev checkout): see
    # `canApply` to tell the two apart.
    IDLE = "idle"
    AVAILABLE = "available"
    APPLYING = "applying"
    # Installed; the app must be restarted (a new process) to run the new code.
    DONE = "done"
    FAILED = "failed"


class UpdateInfo(Contract):
    status: UpdateStatus
    current_version: str
    latest_version: str | None = None
    release_url: str | None = None
    # True once an update is known and this install was made with `uv tool install` (so
    # POST /api/update/apply can self-upgrade it). False for a dev checkout: the user updates
    # that with `git pull`.
    can_apply: bool = False
    # 0-1 while applying; None while indeterminate (uv reports no install progress, so this is
    # stage-based, not a true percentage).
    progress: float | None = None
    message: str | None = None
    error: str | None = None
    checked_at: datetime | None = None
