"""Data-preparation pipelines: Pipeline spec -> SQL (SQLGlot) -> DuckDB query. Owner: transform.

Public entry point for other modules: `resolve_source` (SourceRef -> filtered Relation).
"""

from viz.transform.source import resolve_source

__all__ = ["resolve_source"]
