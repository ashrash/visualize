"""Self-update endpoints: check the project's GitHub Releases for a newer version, and (for an
install made with `uv tool install`) apply it. Disabled in a hosted deployment via
`VIZ_UPDATE_ENABLED=false`, where a container image is redeployed, never self-upgraded in place.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from viz.api.deps import SettingsDep
from viz.contracts.update import UpdateInfo, UpdateStatus
from viz.errors import ConflictError, InvalidSpecError
from viz.update import apply, check

router = APIRouter(prefix="/update", tags=["update"])


def _info(settings: SettingsDep, *, force: bool = False) -> UpdateInfo:
    current_version = apply.installed_version()
    if not settings.update_enabled:
        return UpdateInfo(status=UpdateStatus.IDLE, current_version=current_version)

    release = check.latest_release(settings, force=force)
    applying = apply.current()
    editable = apply.is_editable_install()
    has_update = release is not None and release.version != current_version
    can_apply = (
        has_update and not editable and release is not None and release.wheel_url is not None
    )

    status: UpdateStatus
    if applying.status in (UpdateStatus.APPLYING, UpdateStatus.DONE, UpdateStatus.FAILED):
        status = applying.status
        message = applying.message
        error = applying.error
    elif has_update:
        status = UpdateStatus.AVAILABLE
        message = None
        error = check.last_error()
    else:
        status = UpdateStatus.IDLE
        message = None
        error = check.last_error()

    if status == UpdateStatus.DONE:
        progress = 1.0
    elif status == UpdateStatus.APPLYING:
        progress = 0.5
    else:
        progress = None

    return UpdateInfo(
        status=status,
        current_version=current_version,
        latest_version=release.version if release else None,
        release_url=release.html_url if release else None,
        can_apply=can_apply,
        progress=progress,
        message=message,
        error=error,
        checked_at=datetime.now(UTC),
    )


@router.get("/status", response_model=UpdateInfo)
def get_status(settings: SettingsDep) -> UpdateInfo:
    """The last-known update status; cheap, since it reuses the cached GitHub check."""
    return _info(settings)


@router.post("/check", response_model=UpdateInfo)
def check_now(settings: SettingsDep) -> UpdateInfo:
    """Forces an immediate GitHub check, bypassing the cache."""
    return _info(settings, force=True)


@router.post("/apply", response_model=UpdateInfo, status_code=202)
def apply_update(settings: SettingsDep) -> UpdateInfo:
    if not settings.update_enabled:
        raise InvalidSpecError("self-update is disabled on this server")
    release = check.latest_release(settings)
    current_version = apply.installed_version()
    if release is None or release.version == current_version:
        raise InvalidSpecError("no update is available")
    if not apply.start_apply(release):
        if apply.is_editable_install():
            raise InvalidSpecError(
                "this install can't self-update (it's a dev checkout); use git pull instead"
            )
        if release.wheel_url is None:
            raise InvalidSpecError("the latest release has no installable wheel")
        raise ConflictError("an update is already in progress")
    return _info(settings)
