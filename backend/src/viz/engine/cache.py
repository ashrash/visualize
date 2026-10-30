"""In-process LRU for query results, bounded by bytes (CLAUDE.md §6).

Every key includes version tokens (see `version_token`), so a refresh, which creates a new
dataset version, misses the cache automatically. `evict(prefix)` frees a deleted dataset's
entries early. Two identical concurrent misses both compute; the second write wins.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

import pyarrow as pa
from cachetools import LRUCache

T = TypeVar("T")


def version_token(dataset_id: str, n: int) -> str:
    """Identifies one immutable dataset version. Pipelines combine these with a spec hash."""
    return f"ds:{dataset_id}@v{n}"


@dataclass(frozen=True)
class CachedTable:
    table: pa.Table
    cached: bool


@dataclass(frozen=True)
class _Entry:
    versions: tuple[str, ...]
    value: Any
    nbytes: int


def cache_key(versions: Sequence[str], key: str | bytes, params: Sequence[object] | None) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(sorted(versions)).encode())
    digest.update(b"\x00")
    digest.update(key if isinstance(key, bytes) else key.encode())
    digest.update(b"\x00")
    digest.update(json.dumps(list(params or []), default=str, sort_keys=True).encode())
    return digest.hexdigest()


def _entry_size(entry: _Entry) -> int:
    return max(1, entry.nbytes)


class ResultCache:
    """Thread-safe LRU keyed by (versions, key, params). `max_bytes=0` disables caching."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._lru: LRUCache[str, _Entry] = LRUCache(maxsize=max(1, max_bytes), getsizeof=_entry_size)
        self._lock = threading.Lock()

    def get(
        self, versions: Sequence[str], key: str | bytes, params: Sequence[object] | None = None
    ) -> Any | None:
        k = cache_key(versions, key, params)
        with self._lock:
            entry = self._lru.get(k)
        return None if entry is None else entry.value

    def put(
        self,
        versions: Sequence[str],
        key: str | bytes,
        value: object,
        *,
        nbytes: int,
        params: Sequence[object] | None = None,
    ) -> None:
        if self.max_bytes <= 0 or nbytes > self.max_bytes:
            return
        entry = _Entry(tuple(versions), value, nbytes)
        with self._lock:
            self._lru[cache_key(versions, key, params)] = entry

    def get_or_compute(
        self,
        versions: Sequence[str],
        key: str | bytes,
        compute: Callable[[], T],
        *,
        nbytes: Callable[[T], int],
        params: Sequence[object] | None = None,
    ) -> tuple[T, bool]:
        """Return `(value, hit)`. `nbytes` sizes a computed value for the byte budget."""
        found = self.get(versions, key, params)
        if found is not None:
            return found, True  # type: ignore[no-any-return]
        value = compute()
        self.put(versions, key, value, nbytes=nbytes(value), params=params)
        return value, False

    def evict(self, prefix: str) -> int:
        """Drop entries with any version token starting with `prefix` (e.g. "ds:<id>@")."""
        with self._lock:
            doomed = [k for k, e in self._lru.items() if any(v.startswith(prefix) for v in e.versions)]
            for k in doomed:
                del self._lru[k]
        return len(doomed)

    @property
    def current_bytes(self) -> int:
        with self._lock:
            return int(self._lru.currsize)

    def clear(self) -> None:
        with self._lock:
            self._lru.clear()
