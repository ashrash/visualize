"""/api/update/* ties check.py and apply.py together behind a settings.update_enabled switch."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from viz import __version__
from viz.update import apply, check
from viz.update.check import ReleaseInfo


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    check.reset_cache()
    apply.reset_state()
    yield
    check.reset_cache()
    apply.reset_state()


def _newer_release() -> ReleaseInfo:
    return ReleaseInfo(
        version="999.0.0",
        html_url="https://github.com/owner/repo/releases/tag/v999.0.0",
        wheel_url="https://x/visualize-999.0.0-py3-none-any.whl",
    )


def _same_version_release() -> ReleaseInfo:
    return ReleaseInfo(
        version=__version__,
        html_url="https://github.com/owner/repo/releases/tag/current",
        wheel_url="https://x/current.whl",
    )


def test_status_is_idle_with_no_release_known(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "latest_release", lambda settings, force=False: None)
    body = client.get("/api/update/status").json()
    assert body["status"] == "idle"
    assert body["currentVersion"] == __version__
    assert body["latestVersion"] is None
    assert body["canApply"] is False


def test_status_is_idle_when_already_on_the_latest_version(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        check, "latest_release", lambda settings, force=False: _same_version_release()
    )
    body = client.get("/api/update/status").json()
    assert body["status"] == "idle"
    assert body["latestVersion"] == __version__


def test_status_is_available_when_a_newer_release_exists(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "latest_release", lambda settings, force=False: _newer_release())
    monkeypatch.setattr(apply, "is_editable_install", lambda: False)
    body = client.get("/api/update/status").json()
    assert body["status"] == "available"
    assert body["latestVersion"] == "999.0.0"
    assert body["canApply"] is True


def test_can_apply_is_false_for_an_editable_install(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "latest_release", lambda settings, force=False: _newer_release())
    monkeypatch.setattr(apply, "is_editable_install", lambda: True)
    body = client.get("/api/update/status").json()
    assert body["status"] == "available"
    assert body["canApply"] is False


def test_status_is_idle_and_offline_when_update_is_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_be_called(*a: object, **kw: object) -> ReleaseInfo:
        raise AssertionError("should not check GitHub when update is disabled")

    monkeypatch.setattr(check, "latest_release", must_not_be_called)
    client.app.state.settings.update_enabled = False  # type: ignore[attr-defined]
    body = client.get("/api/update/status").json()
    assert body["status"] == "idle"
    assert body["currentVersion"] == __version__


def test_apply_returns_422_when_no_update_is_available(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "latest_release", lambda settings, force=False: None)
    response = client.post("/api/update/apply")
    assert response.status_code == 422


def test_apply_returns_422_for_an_editable_install(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "latest_release", lambda settings, force=False: _newer_release())
    monkeypatch.setattr(apply, "is_editable_install", lambda: True)
    response = client.post("/api/update/apply")
    assert response.status_code == 422


def test_apply_starts_the_update_and_status_reaches_done(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "latest_release", lambda settings, force=False: _newer_release())
    monkeypatch.setattr(apply, "is_editable_install", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),
    )

    response = client.post("/api/update/apply")
    assert response.status_code == 202
    assert response.json()["status"] in ("applying", "done")

    deadline = time.monotonic() + 2.0
    status = response.json()["status"]
    while status not in ("done", "failed") and time.monotonic() < deadline:
        time.sleep(0.01)
        status = client.get("/api/update/status").json()["status"]
    assert status == "done"


def test_apply_returns_409_when_already_applying(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check, "latest_release", lambda settings, force=False: _newer_release())
    monkeypatch.setattr(apply, "is_editable_install", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")
    started = threading.Event()

    def slow_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        started.set()
        time.sleep(0.3)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", slow_run)

    first = client.post("/api/update/apply")
    assert first.status_code == 202
    started.wait(timeout=1.0)
    second = client.post("/api/update/apply")
    assert second.status_code == 409
