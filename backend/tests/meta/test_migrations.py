from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from viz.config import Settings
from viz.meta.db import _MIGRATIONS_DIR


def _config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return config


def test_0002_up_and_down(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    monkeypatch.setenv("VIZ_DATABASE_URL", url)
    from viz.config import get_settings

    get_settings.cache_clear()
    try:
        config = _config(url)
        command.upgrade(config, "0001")
        engine = create_engine(url)
        assert "autosaves_since_snapshot" not in {
            c["name"] for c in inspect(engine).get_columns("projects")
        }
        command.upgrade(config, "head")
        insp = inspect(engine)
        assert "autosaves_since_snapshot" in {c["name"] for c in insp.get_columns("projects")}
        assert "ix_project_versions_project_kind_rev" in {
            i["name"] for i in insp.get_indexes("project_versions")
        }
        command.downgrade(config, "0001")
        assert "autosaves_since_snapshot" not in {
            c["name"] for c in inspect(engine).get_columns("projects")
        }
        engine.dispose()
    finally:
        get_settings.cache_clear()


def test_settings_env_override(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VIZ_AUTOSAVE_SNAPSHOT_EVERY", "4")
    monkeypatch.setenv("VIZ_AUTOSAVE_SNAPSHOTS_KEEP", "7")
    s = Settings()
    assert (s.autosave_snapshot_every, s.autosave_snapshots_keep) == (4, 7)
