"""The wheel must carry the built frontend and the Alembic migrations.

hatch's `artifacts` setting is what pulls the git-ignored `static/` into the wheel; this test
builds a real wheel and looks inside, so a config regression can't ship a UI-less package.
Skipped when the frontend isn't built (`uv run viz build`) or uv isn't on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "backend" / "src" / "viz" / "static"


@pytest.mark.skipif(not (STATIC / "index.html").is_file(), reason="frontend not built")
@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_wheel_contains_static_and_migrations(tmp_path: Path) -> None:
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    wheel = next(tmp_path.glob("visualize-*.whl"))
    names = set(zipfile.ZipFile(wheel).namelist())
    assert "viz/static/index.html" in names
    built_assets = {p.name for p in (STATIC / "assets").iterdir()}
    packed_assets = {n.rsplit("/", 1)[-1] for n in names if n.startswith("viz/static/assets/")}
    assert built_assets <= packed_assets
    assert "viz/meta/migrations/env.py" in names
    assert any(n.startswith("viz/meta/migrations/versions/0001") for n in names)
