"""Applies an update by reinstalling the uv tool from the latest release's wheel, in a
background thread: the request that triggers it returns immediately, and the frontend polls
GET /api/update/status for progress. Self-update only makes sense for an install made with
`uv tool install`; a dev checkout (editable install, e.g. `uv sync`) is left alone, since
reinstalling over a live editable source tree makes no sense and `git pull` is the right tool
there. uv reports no install progress, so this is stage-based ("applying" / "done" / "failed"),
not a true percentage.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass, field

from viz import __version__
from viz.contracts.update import UpdateStatus
from viz.update.check import ReleaseInfo

logger = logging.getLogger(__name__)

_DIST_NAME = "visualize"
_INSTALL_TIMEOUT_S = 300.0


def is_editable_install() -> bool:
    """True for a dev checkout that must not be self-upgraded (an editable install, or no
    installed distribution at all, e.g. running straight from source without even `uv sync`)."""
    try:
        dist = importlib.metadata.distribution(_DIST_NAME)
    except importlib.metadata.PackageNotFoundError:
        return True
    try:
        raw = dist.read_text("direct_url.json")
    except OSError:
        return False
    if not raw:
        return False
    try:
        info = json.loads(raw)
    except ValueError:
        return False
    return bool(info.get("dir_info", {}).get("editable"))


@dataclass
class _State:
    lock: threading.Lock = field(default_factory=threading.Lock)
    status: UpdateStatus = UpdateStatus.IDLE
    message: str | None = None
    error: str | None = None


_state = _State()


@dataclass(frozen=True)
class ApplyState:
    status: UpdateStatus
    message: str | None
    error: str | None


def current() -> ApplyState:
    with _state.lock:
        return ApplyState(status=_state.status, message=_state.message, error=_state.error)


def start_apply(release: ReleaseInfo) -> bool:
    """Kicks off the reinstall in a background thread. Returns False (and starts nothing) if
    one is already running, this install can't self-update, or the release has no wheel."""
    if is_editable_install() or release.wheel_url is None:
        return False
    with _state.lock:
        if _state.status == UpdateStatus.APPLYING:
            return False
        _state.status = UpdateStatus.APPLYING
        _state.message = "Downloading and installing the update"
        _state.error = None
    threading.Thread(target=_run_apply, args=(release,), daemon=True).start()
    return True


def _run_apply(release: ReleaseInfo) -> None:
    uv = shutil.which("uv")
    try:
        if uv is None:
            raise RuntimeError("`uv` is not on PATH")
        if release.wheel_url is None:
            raise RuntimeError("the release has no wheel to install")
        subprocess.run(
            [uv, "tool", "install", "--reinstall", "--force", release.wheel_url],
            check=True,
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT_S,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning("self-update to %s failed: %s", release.version, exc.stderr)
        _fail("The update failed. Try again, or update manually from the release page.")
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        logger.warning("self-update to %s failed: %s", release.version, exc)
        _fail("The update failed. Try again, or update manually from the release page.")
    else:
        with _state.lock:
            _state.status = UpdateStatus.DONE
            _state.message = f"Updated to {release.version}. Restart the app to finish."


def _fail(message: str) -> None:
    with _state.lock:
        _state.status = UpdateStatus.FAILED
        _state.error = message


def reset_state() -> None:
    """Test hook."""
    global _state
    _state = _State()


def installed_version() -> str:
    return __version__
