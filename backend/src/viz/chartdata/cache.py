"""Response cache for chart data, keyed by data version plus everything that changes data.

The key covers the source's version key (dataset version, or the compiled pipeline's SQL
hash), the plot type, encodings, params, every filter, the zoom range, the viewport (bucketed
to 100 px, since downsampling depends on it) and style.errorBars. All other style fields,
the chart name and its interactions are cosmetic and excluded, so restyling never re-queries.

TODO(data-core): consider moving to the engine's query cache primitive once it lands; this
caches whole responses (including Python-side reductions), so it stays useful on top of it.
"""

from __future__ import annotations

import hashlib
import json
import threading
import weakref
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
from cachetools import LRUCache

from viz.contracts import ChartDataMeta, ChartDataRequest, StatResult
from viz.engine import Engine

MAX_BYTES = 256 * 2**20


@dataclass(frozen=True)
class CachedChart:
    table: pa.Table | None
    stat: StatResult | None
    meta: ChartDataMeta


def _size(entry: CachedChart) -> int:
    if entry.table is not None:
        return max(1, int(entry.table.nbytes))
    return len(entry.stat.model_dump_json()) if entry.stat is not None else 1


def _bucket(px: int) -> int:
    return max(100, -(-px // 100) * 100)


def chart_key(version_key: str, request: ChartDataRequest, mark_cap: int) -> str:
    spec = request.spec
    payload: dict[str, Any] = {
        "v": version_key,
        "plot": spec.plot_type.value,
        "encodings": {k: [e.model_dump(mode="json") for e in v] for k, v in spec.encodings.items()},
        "params": spec.params,
        "filters": [p.model_dump(mode="json") for p in spec.filters],
        "extra": [p.model_dump(mode="json") for p in request.filters],
        "xRange": request.viewport.x_range,
        "w": _bucket(request.viewport.width_px),
        "h": _bucket(request.viewport.height_px),
        "errorBars": spec.style.error_bars.model_dump(mode="json")
        if spec.style.error_bars
        else None,
        "cap": mark_cap,
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ChartCache:
    def __init__(self, max_bytes: int = MAX_BYTES) -> None:
        self._lock = threading.Lock()
        self._entries: LRUCache[str, CachedChart] = LRUCache(maxsize=max_bytes, getsizeof=_size)

    def get(self, key: str) -> CachedChart | None:
        with self._lock:
            return self._entries.get(key)

    def put(self, key: str, entry: CachedChart) -> None:
        if _size(entry) > MAX_BYTES // 8:
            return  # one huge response shouldn't evict everything else
        with self._lock:
            self._entries[key] = entry


_caches: weakref.WeakKeyDictionary[Engine, ChartCache] = weakref.WeakKeyDictionary()
_caches_lock = threading.Lock()


def cache_for(engine: Engine) -> ChartCache:
    with _caches_lock:
        cache = _caches.get(engine)
        if cache is None:
            cache = ChartCache()
            _caches[engine] = cache
        return cache
