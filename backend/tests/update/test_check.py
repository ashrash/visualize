"""check.latest_release polls GitHub, caches the result, and never surfaces a transient failure
as fatal: the last known-good release keeps being served until a check succeeds again."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from viz.config import Settings
from viz.update import check


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    check.reset_cache()
    yield
    check.reset_cache()


def _settings(**kw: Any) -> Settings:
    return Settings(update_repo="owner/repo", **kw)


def _response(payload: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "https://example"))


def _release_payload(
    tag: str = "v1.2.3", whl_name: str = "visualize-1.2.3-py3-none-any.whl"
) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/owner/repo/releases/tag/{tag}",
        "full_name": "owner/repo",
        "assets": [
            {"name": "visualize-1.2.3.tar.gz", "browser_download_url": "https://x/sdist.tar.gz"},
            {"name": whl_name, "browser_download_url": f"https://x/{whl_name}"},
        ],
    }


def test_parses_latest_release_and_finds_the_wheel_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _response(_release_payload()))
    release = check.latest_release(_settings())
    assert release is not None
    assert release.version == "1.2.3"
    assert release.wheel_url == "https://x/visualize-1.2.3-py3-none-any.whl"
    assert release.html_url.endswith("/v1.2.3")


def test_strips_leading_v_from_the_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _response(_release_payload(tag="v0.9.0")))
    release = check.latest_release(_settings())
    assert release is not None
    assert release.version == "0.9.0"


def test_release_with_no_wheel_asset_has_none_wheel_url(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _release_payload()
    payload["assets"] = [a for a in payload["assets"] if not a["name"].endswith(".whl")]
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _response(payload))
    release = check.latest_release(_settings())
    assert release is not None
    assert release.wheel_url is None


def test_caches_within_the_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_get(*a: Any, **kw: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(_release_payload())

    monkeypatch.setattr(httpx, "get", fake_get)
    settings = _settings(update_check_interval_s=3600)
    check.latest_release(settings)
    check.latest_release(settings)
    assert calls == 1


def test_force_bypasses_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_get(*a: Any, **kw: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(_release_payload())

    monkeypatch.setattr(httpx, "get", fake_get)
    settings = _settings(update_check_interval_s=3600)
    check.latest_release(settings)
    check.latest_release(settings, force=True)
    assert calls == 2


def test_network_failure_keeps_the_last_known_good_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _response(_release_payload()))
    settings = _settings(update_check_interval_s=3600)
    first = check.latest_release(settings)
    assert first is not None

    def fail(*a: Any, **kw: Any) -> httpx.Response:
        raise httpx.ConnectError("offline", request=httpx.Request("GET", "https://example"))

    monkeypatch.setattr(httpx, "get", fail)
    second = check.latest_release(settings, force=True)
    assert second == first
    assert check.last_error() is not None


def test_a_failure_with_no_prior_success_returns_none_and_records_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*a: Any, **kw: Any) -> httpx.Response:
        raise httpx.ConnectError("offline", request=httpx.Request("GET", "https://example"))

    monkeypatch.setattr(httpx, "get", fail)
    assert check.latest_release(_settings()) is None
    assert check.last_error() is not None


def test_an_http_error_status_is_treated_as_a_failed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _response({}, status=404))
    assert check.latest_release(_settings()) is None
    assert check.last_error() is not None


def test_a_malformed_payload_without_a_tag_is_treated_as_no_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _response({"no_tag": True}))
    assert check.latest_release(_settings()) is None
