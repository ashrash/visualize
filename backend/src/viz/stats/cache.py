"""LRU cache of StatResults keyed by (analysis, data version, request), bounded in bytes.

The data version (Relation.version_key) includes the dataset version and the compiled
pipeline's hash, so a refresh or a pipeline edit never serves a stale result. Sampled
analyses are seeded where DuckDB allows it; either way a cached result is what the user saw.

Results vary from a few hundred bytes to tens of MB (outlier rows with included columns),
so entries are weighed by their JSON size, and one larger than MAX_ENTRY_BYTES isn't kept.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from cachetools import LRUCache
from pydantic import BaseModel

from viz.contracts import StatResult
from viz.stats.relation import Relation

MAX_BYTES = 64 * 1024 * 1024
MAX_ENTRY_BYTES = 8 * 1024 * 1024

Key = tuple[str, str, str]


def _weight(entry: tuple[StatResult, int]) -> int:
    return entry[1]


# Entries are (result, its JSON size in bytes).
_cache: LRUCache[Key, tuple[StatResult, int]] = LRUCache(maxsize=MAX_BYTES, getsizeof=_weight)
_lock = threading.Lock()


def cached(
    analysis: str, rel: Relation, request: BaseModel, compute: Callable[[], StatResult]
) -> StatResult:
    key = (analysis, rel.version_key, request.model_dump_json())
    with _lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit[0]
    result = compute()
    size = len(result.model_dump_json())
    if size <= MAX_ENTRY_BYTES:
        with _lock:
            _cache[key] = (result, size)
    return result


def clear_cache() -> None:
    with _lock:
        _cache.clear()
