"""DuckDB lifecycle and query execution. Every backend module queries through Engine."""

from viz.engine.arrow import ipc_bytes, js_friendly
from viz.engine.duckdb_engine import Engine

__all__ = ["Engine", "ipc_bytes", "js_friendly"]
