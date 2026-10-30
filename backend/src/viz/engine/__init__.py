"""DuckDB lifecycle and query execution. Every backend module queries through Engine.

Public API (data-core; see docs/plans/data-core.md):
- `Engine.cursor(key, generation)` / `query_arrow(...)`: interactive queries (slot + timeout).
- `Engine.query_arrow_cached(..., versions=[version_token(...)])`: same, through the LRU.
- `Engine.background_cursor(key)`: job work (no slot, no timeout).
- `Engine.interrupt(key)`, `Engine.cancel_owner(owner)`, `Engine.progress(key)`.
- `request_scope()`: per-request owner + arrival ticket (implicit generation).
- `Engine.cache` (`ResultCache.get_or_compute`) and `Engine.pool` (`ProcessPool.run`).
- `run_cancellable(request, engine, key, fn)`: interrupt on client disconnect.
"""

from viz.engine.arrow import ipc_bytes, js_friendly
from viz.engine.cache import CachedTable, ResultCache, version_token
from viz.engine.cancel import run_cancellable
from viz.engine.duckdb_engine import (
    Engine,
    RequestOwner,
    current_owner,
    new_owner,
    request_scope,
)
from viz.engine.pool import ProcessPool

__all__ = [
    "CachedTable",
    "Engine",
    "ProcessPool",
    "RequestOwner",
    "ResultCache",
    "current_owner",
    "ipc_bytes",
    "js_friendly",
    "new_owner",
    "request_scope",
    "run_cancellable",
    "version_token",
]
