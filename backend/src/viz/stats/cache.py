"""LRU cache of StatResults keyed by (analysis, data version, request).

The data version (Relation.version_key) includes the dataset version and the compiled
pipeline's hash, so a refresh or a pipeline edit never serves a stale result. Sampled
analyses are seeded, so a cached result is also what a recomputation would give.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from cachetools import LRUCache
from pydantic import BaseModel

from viz.contracts import StatResult
from viz.stats.relation import Relation

_cache: LRUCache[tuple[str, str, str], StatResult] = LRUCache(maxsize=256)
_lock = threading.Lock()


def cached(
    analysis: str, rel: Relation, request: BaseModel, compute: Callable[[], StatResult]
) -> StatResult:
    key = (analysis, rel.version_key, request.model_dump_json())
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit
    result = compute()
    with _lock:
        _cache[key] = result
    return result


def clear_cache() -> None:
    with _lock:
        _cache.clear()
