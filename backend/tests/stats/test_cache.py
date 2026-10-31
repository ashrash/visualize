from __future__ import annotations

from typing import Any

import pytest

from viz.contracts import SourceRef, StatResult
from viz.stats import cache
from viz.stats.models import CorrelationRequest


@pytest.fixture(autouse=True)
def _fresh() -> None:
    cache.clear_cache()


def _result(payload: str = "") -> StatResult:
    return StatResult(values={"x": payload}, method="m", n=1)


def test_hits_are_keyed_by_data_version_and_request(table_relation: Any) -> None:
    rel = table_relation({"x": [1.0, 2.0]})
    request = CorrelationRequest(source=SourceRef(kind="dataset", id="d"), fields=["x", "y"])
    calls: list[int] = []

    def compute() -> StatResult:
        calls.append(1)
        return _result()

    first = cache.cached("a", rel, request, compute)
    assert cache.cached("a", rel, request, compute) is first
    cache.cached("b", rel, request, compute)
    other = request.model_copy(update={"method": "kendall"})
    cache.cached("a", rel, other, compute)
    assert len(calls) == 3


def test_entries_are_weighed_in_bytes(table_relation: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "MAX_ENTRY_BYTES", 1000)
    rel = table_relation({"x": [1.0]})
    request = CorrelationRequest(source=SourceRef(kind="dataset", id="d"), fields=["x", "y"])
    calls: list[int] = []

    def big() -> StatResult:
        calls.append(1)
        return _result("z" * 5000)

    cache.cached("big", rel, request, big)
    cache.cached("big", rel, request, big)
    assert len(calls) == 2  # too large to keep
