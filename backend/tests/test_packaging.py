"""The wheel must carry the built frontend and the Alembic migrations.

hatch's `artifacts` setting is what pulls the git-ignored `static/` into the wheel; this test
builds a real wheel and looks inside, so a config regression can't ship a UI-less package.
Skipped when the frontend isn't built (`uv run viz build`) or uv isn't on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = REPO_ROOT / "backend" / "src" / "viz" / "static"


def _assert_wheel_complete(wheel: Path) -> None:
    names = set(zipfile.ZipFile(wheel).namelist())
    assert "viz/static/index.html" in names
    built_assets = {p.name for p in (STATIC / "assets").iterdir()}
    packed_assets = {n.rsplit("/", 1)[-1] for n in names if n.startswith("viz/static/assets/")}
    assert built_assets <= packed_assets
    assert "viz/meta/migrations/env.py" in names
    assert any(n.startswith("viz/meta/migrations/versions/0001") for n in names)


@pytest.mark.skipif(not (STATIC / "index.html").is_file(), reason="frontend not built")
@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_wheel_contains_static_and_migrations(tmp_path: Path) -> None:
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    _assert_wheel_complete(next(tmp_path.glob("visualize-*.whl")))


@pytest.mark.skipif(not (STATIC / "index.html").is_file(), reason="frontend not built")
@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_full_build_contains_static_and_migrations(tmp_path: Path) -> None:
    """`uv build` (no `--wheel`) builds an sdist first and the wheel FROM that sdist, so the
    static dir must be force-included in the sdist too, not just the wheel target - otherwise
    this two-step path silently ships a UI-less package even though test_wheel_contains_static_
    and_migrations above passes. This is the release workflow's actual build command."""
    subprocess.run(
        ["uv", "build", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    _assert_wheel_complete(next(tmp_path.glob("visualize-*.whl")))
    assert next(tmp_path.glob("visualize-*.tar.gz"), None) is not None


@pytest.mark.skipif(not (STATIC / "index.html").is_file(), reason="frontend not built")
@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_installed_wheel_imports_with_only_its_declared_dependencies(tmp_path: Path) -> None:
    """`uv tool install` (what end users and the release doc run) resolves only
    [project.dependencies], never [dependency-groups].dev - so a package imported only from
    production code that happens to already be installed via the dev group (httpx, pulled in by
    FastAPI's TestClient) can work in every `uv run`/`uv sync` in this repo's own venv while
    still being missing from the wheel's own Requires-Dist, and only blow up as a
    ModuleNotFoundError for someone who actually installs the released wheel. Installs the built
    wheel into a fresh, isolated venv (full dependency resolution, not --no-deps) and imports
    viz.main there to catch that gap before it ships.

    Installed against uv.lock's own versions as a constraint file, not a fresh unconstrained
    resolution: the wheel's Requires-Dist ranges (e.g. `scipy>=1.14`) would otherwise let uv
    resolve a brand-new release of a heavy scientific dependency that may not ship a macOS
    x86_64 wheel yet (CLAUDE.md section 2 notes exactly this risk for DuckDB 2.1), which can
    hang this test on a source build instead of testing the dependency gap it exists to catch.
    The lockfile's versions are already proven to have wheels on every CI platform.
    """
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    wheel = next(tmp_path.glob("visualize-*.whl"))
    constraints = tmp_path / "constraints.txt"
    subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "-o",
            str(constraints),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", str(venv)], cwd=REPO_ROOT, check=True, capture_output=True)
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--constraint",
            str(constraints),
            str(wheel),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [str(python), "-c", "import viz.main"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
