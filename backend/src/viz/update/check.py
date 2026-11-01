"""Polls GitHub's Releases API for the latest release, cached for `settings.update_check_interval_s`
to stay well under the unauthenticated rate limit (60 requests/hour) and keep every page load
cheap. A failed check (offline, rate-limited, GitHub down) is never surfaced as an error the user
must act on: it just means "no update known yet," and the last known-good release (if any) keeps
being served until a check succeeds again.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import httpx

from viz.config import Settings

logger = logging.getLogger(__name__)

_RELEASES_URL = "https://api.github.com/repos/{repo}/releases/latest"
_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    html_url: str
    # The .whl asset to reinstall from; None if the release has no wheel attached yet.
    wheel_url: str | None


class _Cache:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.release: ReleaseInfo | None = None
        # None = never checked. `time.monotonic()`'s epoch is implementation-defined (on Windows
        # it's time since boot), so a numeric sentinel like 0.0 can make the very first check
        # after a reboot look "already fresh" if uptime is under the cache interval - which, for
        # the default 6h interval, is true after nearly every normal restart.
        self.checked_at: float | None = None
        self.error: str | None = None


_cache = _Cache()


def _parse(payload: dict[str, object]) -> ReleaseInfo | None:
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag:
        return None
    version = tag.removeprefix("v")
    assets = payload.get("assets")
    wheel_url = next(
        (
            a.get("browser_download_url")
            for a in (assets if isinstance(assets, list) else [])
            if isinstance(a, dict) and str(a.get("name", "")).endswith(".whl")
        ),
        None,
    )
    html_url = payload.get("html_url")
    if not isinstance(html_url, str):
        html_url = f"https://github.com/{payload.get('full_name', '')}/releases/tag/{tag}"
    return ReleaseInfo(version=version, html_url=html_url, wheel_url=wheel_url)


def latest_release(settings: Settings, *, force: bool = False) -> ReleaseInfo | None:
    """The latest GitHub release, cached for `settings.update_check_interval_s`. Returns the
    last known-good release (possibly None) on failure; call `last_error()` for why."""
    now = time.monotonic()
    with _cache.lock:
        checked_at = _cache.checked_at
        fresh = checked_at is not None and now - checked_at < settings.update_check_interval_s
        if not force and fresh:
            return _cache.release
    try:
        response = httpx.get(
            _RELEASES_URL.format(repo=settings.update_repo),
            timeout=_TIMEOUT_S,
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        release = _parse(response.json())
        error = None
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("update check against %s failed: %s", settings.update_repo, exc)
        release = _cache.release  # keep serving the last known-good release
        error = str(exc)
    with _cache.lock:
        _cache.release = release
        _cache.error = error
        _cache.checked_at = now
        return _cache.release


def last_error() -> str | None:
    with _cache.lock:
        return _cache.error


def last_checked_monotonic() -> float | None:
    with _cache.lock:
        return _cache.checked_at


def reset_cache() -> None:
    """Test hook."""
    global _cache
    _cache = _Cache()
