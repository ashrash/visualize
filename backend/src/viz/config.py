"""App configuration. Every field can be set with a VIZ_* environment variable."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import platformdirs
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    return Path(platformdirs.user_data_dir("visualize", appauthor=False))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VIZ_", env_file=".env", extra="ignore")

    data_dir: Path = Field(default_factory=_default_data_dir)
    # Metadata DB. Defaults to SQLite in the data dir; Postgres URL on AWS.
    database_url: str | None = None

    host: str = "127.0.0.1"
    port: int = 8000

    # DuckDB. None means: threads = all cores, memory_limit = ~60% of RAM.
    duckdb_threads: int | None = None
    duckdb_memory_limit: str | None = None
    max_concurrent_queries: int = 8
    query_timeout_s: float = 60.0

    # Most marks a chart response may carry before the server reduces the data.
    mark_cap: int = 50_000
    grid_page_max: int = 1_000
    max_upload_mb: int = 4_096
    # Image assets (dashboard Image widgets) are far smaller than dataset uploads.
    max_asset_mb: int = 10
    # Local mode reads files in place; hosted deployments turn this off.
    allow_path_import: bool = True
    # Path imports up to this size are copied into sources/ first; larger ones are read in place.
    copy_source_max_mb: int = 512

    # Query result cache (bytes budget), keyed by dataset version + SQL.
    cache_mb: int = 256
    # Process pool for pure-Python CPU work; BLAS threads per worker (None = cores / workers).
    process_workers: int = 2
    blas_threads: int | None = None
    prewarm_process_pool: bool = True
    # Background jobs (imports, materialisation, exports).
    max_concurrent_jobs: int = 1
    # Threads for the background DuckDB instance (jobs, grid-view builds): None = a quarter of
    # the cores (at least 2), 0 = share the main instance.
    job_duckdb_threads: int | None = None
    job_retention_s: int = 3600
    export_retention_s: int = 3600
    staged_retention_s: int = 86_400
    preview_rows: int = 1000
    # Disk budget for materialized grid views (sorted/filtered copies) in temp/views.
    grid_views_mb: int = 1024
    # How often the job runner deletes expired staged uploads and export files.
    sweep_interval_s: int = 3600

    # Projects: every Nth autosave also writes a snapshot; older autosave snapshots are pruned.
    autosave_snapshot_every: int = 10
    autosave_snapshots_keep: int = 20
    max_vizproj_mb: int = 8_192

    # Self-update (local mode only; set to false for a hosted deployment, which is redeployed
    # from a rebuilt image rather than self-upgraded in place).
    update_enabled: bool = True
    update_repo: str = "ashrash/visualize"
    update_check_interval_s: int = 21_600

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'meta.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
