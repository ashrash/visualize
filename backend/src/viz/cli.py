"""Command line: `viz dev`, `viz build`, `viz serve`, `viz openapi`.

Orchestration lives here (not in shell scripts) so it behaves the same on Windows and macOS.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Annotated, Any

import typer

app = typer.Typer(help="Visualize: local-first data visualization.", no_args_is_help=True)

# backend/src/viz/cli.py → repo root (source checkouts only; dev and build need it).
REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIR = REPO_ROOT / "frontend"
STATIC_DIR = Path(__file__).parent / "static"


def _npm() -> str:
    npm = shutil.which("npm")
    if npm is None:
        typer.secho("npm not found. Install Node.js LTS (22+) first.", fg="red", err=True)
        raise typer.Exit(1)
    if not (FRONTEND_DIR / "node_modules").is_dir():
        typer.secho(
            "Frontend dependencies missing. Run: npm --prefix frontend install", fg="red", err=True
        )
        raise typer.Exit(1)
    return npm


_BLAS_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")


def _cap_blas_env(env: dict[str, str] | os._Environ[str]) -> None:
    """data-core: cap BLAS/OpenMP threads before numpy/scipy load (threadpoolctl can only cap
    libraries that are already loaded). Explicit user settings win."""
    from viz.config import get_settings

    settings = get_settings()
    threads = settings.blas_threads or max(1, (os.cpu_count() or 2) // settings.process_workers)
    for name in _BLAS_ENV:
        env.setdefault(name, str(threads))


def _process_group_kwargs() -> dict[str, Any]:
    # POSIX: own process group so we can stop the whole tree. Windows: Ctrl+C already reaches
    # every process in the console, and _stop() uses taskkill /T for the tree.
    return {} if os.name == "nt" else {"start_new_session": True}


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        os.killpg(proc.pid, signal.SIGTERM)


@app.command()
def dev(
    port: Annotated[int, typer.Option(help="API port")] = 8000,
    frontend_port: Annotated[int, typer.Option(help="Vite dev server port")] = 5173,
) -> None:
    """Run the API with auto-reload and the Vite dev server. Open the Vite URL."""
    npm = _npm()
    env = os.environ.copy()
    env.setdefault("VIZ_DATA_DIR", str(REPO_ROOT / ".data"))
    env["VIZ_API_PORT"] = str(port)
    _cap_blas_env(env)
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "viz.main:create_app",
            "--factory",
            "--reload",
            "--reload-dir",
            str(REPO_ROOT / "backend" / "src"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        **_process_group_kwargs(),
    )
    web = subprocess.Popen(
        [npm, "--prefix", str(FRONTEND_DIR), "run", "dev", "--", "--port", str(frontend_port)],
        env=env,
        **_process_group_kwargs(),
    )
    typer.secho(
        f"Visualize dev: http://127.0.0.1:{frontend_port}  "
        f"(API :{port}, data in {env['VIZ_DATA_DIR']}). Ctrl+C to stop.",
        fg="green",
    )
    procs = [api, web]
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for proc in procs:
            _stop(proc)


@app.command()
def build() -> None:
    """Build the frontend into backend/src/viz/static (served by `viz serve`)."""
    result = subprocess.run([_npm(), "--prefix", str(FRONTEND_DIR), "run", "build"])
    raise typer.Exit(result.returncode)


@app.command()
def serve(
    host: Annotated[str | None, typer.Option(help="Bind address (default 127.0.0.1)")] = None,
    port: Annotated[int | None, typer.Option(help="Port (default 8000)")] = None,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
) -> None:
    """Serve the app on one port and open it in the browser."""
    _cap_blas_env(os.environ)
    import uvicorn

    from viz.config import get_settings

    settings = get_settings()
    host = host or settings.host
    port = port or settings.port
    if not (STATIC_DIR / "index.html").is_file():
        typer.secho("Frontend not built; run `uv run viz build` first.", fg="yellow", err=True)
    if open_browser:
        timer = threading.Timer(1.5, webbrowser.open, args=[f"http://{host}:{port}"])
        timer.daemon = True
        timer.start()
    uvicorn.run("viz.main:create_app", factory=True, host=host, port=port)


@app.command()
def openapi(
    out: Annotated[Path, typer.Option(help="Where to write the OpenAPI JSON")],
) -> None:
    """Write the OpenAPI schema (used by `npm run gen:api`)."""
    from viz.main import create_app

    schema = create_app().openapi()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    typer.echo(f"wrote {out}")


if __name__ == "__main__":
    app()
