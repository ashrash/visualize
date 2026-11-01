"""apply.start_apply reinstalls the uv tool from a release's wheel in a background thread, and
refuses to do so for a dev checkout (editable install) or while one is already running."""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from viz.contracts.update import UpdateStatus
from viz.update import apply
from viz.update.check import ReleaseInfo


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    apply.reset_state()
    yield
    apply.reset_state()


def _release(version: str = "9.9.9", wheel_url: str | None = "https://x/v.whl") -> ReleaseInfo:
    return ReleaseInfo(version=version, html_url="https://x/release", wheel_url=wheel_url)


def _wait_until_terminal(timeout: float = 2.0) -> apply.ApplyState:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = apply.current()
        if state.status in (UpdateStatus.DONE, UpdateStatus.FAILED):
            return state
        time.sleep(0.01)
    raise AssertionError("apply did not reach a terminal status in time")


class _FakeDistribution:
    def __init__(self, direct_url: dict[str, Any] | None) -> None:
        self._direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        if name == "direct_url.json" and self._direct_url is not None:
            return json.dumps(self._direct_url)
        return None


def test_is_editable_install_true_for_an_editable_direct_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dist = _FakeDistribution({"url": "file:///repo", "dir_info": {"editable": True}})
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: dist)
    assert apply.is_editable_install() is True


def test_is_editable_install_false_for_a_normal_install(monkeypatch: pytest.MonkeyPatch) -> None:
    dist = _FakeDistribution({"url": "https://files.pythonhosted.org/x.whl"})
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: dist)
    assert apply.is_editable_install() is False


def test_is_editable_install_false_when_there_is_no_direct_url_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No direct_url.json at all is normal for an ordinary (non-editable) install."""
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: _FakeDistribution(None))
    assert apply.is_editable_install() is False


def test_is_editable_install_true_when_distribution_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_missing(name: str) -> Any:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", raise_missing)
    assert apply.is_editable_install() is True


def test_start_apply_refuses_an_editable_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply, "is_editable_install", lambda: True)
    assert apply.start_apply(_release()) is False
    assert apply.current().status == UpdateStatus.IDLE


def test_start_apply_refuses_a_release_with_no_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply, "is_editable_install", lambda: False)
    assert apply.start_apply(_release(wheel_url=None)) is False


def test_start_apply_runs_uv_tool_install_and_reaches_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(apply, "is_editable_install", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    release = _release(version="2.0.0", wheel_url="https://x/visualize-2.0.0.whl")
    assert apply.start_apply(release) is True
    state = _wait_until_terminal()
    assert state.status == UpdateStatus.DONE
    assert state.message is not None and "2.0.0" in state.message
    assert len(calls) == 1
    assert calls[0][:3] == ["/usr/bin/uv", "tool", "install"]
    assert release.wheel_url in calls[0]


def test_start_apply_returns_false_while_already_applying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(apply, "is_editable_install", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")
    started = threading.Event()
    release_second = _release()

    def slow_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        started.set()
        time.sleep(0.2)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", slow_run)
    assert apply.start_apply(_release()) is True
    started.wait(timeout=1.0)
    assert apply.start_apply(release_second) is False
    _wait_until_terminal()


def test_a_failed_install_sets_failed_status_with_a_readable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(apply, "is_editable_install", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")

    def failing_run(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(1, cmd, output="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", failing_run)
    assert apply.start_apply(_release()) is True
    state = _wait_until_terminal()
    assert state.status == UpdateStatus.FAILED
    assert state.error is not None


def test_a_missing_uv_binary_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(apply, "is_editable_install", lambda: False)
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert apply.start_apply(_release()) is True
    state = _wait_until_terminal()
    assert state.status == UpdateStatus.FAILED
