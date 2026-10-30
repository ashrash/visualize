"""Fail when the committed OpenAPI JSON or generated TypeScript types are out of date.

    uv run python scripts/check_contract_drift.py

Regenerates both into a temp dir exactly as `npm --prefix frontend run gen:api` does
(`viz openapi --out ...`, then openapi-typescript) and compares them with the committed
frontend/src/api/openapi.json and schema.d.ts, ignoring line-ending differences. Exit code 1 on
drift (with a diff excerpt), 2 when a tool could not run. Works the same on Windows and macOS;
no shell is involved.
"""

from __future__ import annotations

import difflib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND = REPO_ROOT / "frontend"
API_DIR = FRONTEND / "src" / "api"
OPENAPI_TS_CLI = FRONTEND / "node_modules" / "openapi-typescript" / "bin" / "cli.js"
MAX_DIFF_LINES = 60


def run(args: list[str], what: str) -> None:
    result = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"ERROR: {what} failed (exit {result.returncode})")
        print(result.stdout[-2000:])
        print(result.stderr[-2000:])
        raise SystemExit(2)


def normalized(path: Path) -> list[str]:
    """File lines with CRLF folded to LF (git may check files out with either on Windows)."""
    text = path.read_text(encoding="utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n").splitlines()


def compare(name: str, committed: Path, fresh: Path) -> bool:
    if not committed.is_file():
        print(f"DRIFT: {committed.relative_to(REPO_ROOT)} is missing")
        return False
    old, new = normalized(committed), normalized(fresh)
    if old == new:
        print(f"ok: {name} is up to date")
        return True
    print(f"DRIFT: {name} differs from the generated output")
    diff = list(
        difflib.unified_diff(old, new, f"committed/{name}", f"generated/{name}", lineterm="", n=1)
    )
    for line in diff[:MAX_DIFF_LINES]:
        print(line)
    if len(diff) > MAX_DIFF_LINES:
        print(f"... {len(diff) - MAX_DIFF_LINES} more diff lines")
    return False


def main() -> int:
    node = shutil.which("node")
    if node is None:
        print("ERROR: node not found on PATH (Node LTS 22+ is required)")
        return 2
    if not OPENAPI_TS_CLI.is_file():
        print("ERROR: openapi-typescript missing; run `npm --prefix frontend ci` first")
        return 2

    with tempfile.TemporaryDirectory(prefix="viz-contract-drift-") as tmp:
        tmp_dir = Path(tmp)
        openapi = tmp_dir / "openapi.json"
        schema = tmp_dir / "schema.d.ts"
        # Same interpreter as this script: `uv run python ...` already put the venv on top.
        run([sys.executable, "-m", "viz.cli", "openapi", "--out", str(openapi)], "viz openapi")
        run([node, str(OPENAPI_TS_CLI), str(openapi), "-o", str(schema)], "openapi-typescript")
        results = [
            compare("openapi.json", API_DIR / "openapi.json", openapi),
            compare("schema.d.ts", API_DIR / "schema.d.ts", schema),
        ]

    if all(results):
        return 0
    print("\nRegenerate and commit: npm --prefix frontend run gen:api")
    return 1


if __name__ == "__main__":
    sys.exit(main())
