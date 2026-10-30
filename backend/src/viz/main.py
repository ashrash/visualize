"""FastAPI app factory: API under /api, the built frontend everywhere else.

Run with: uvicorn viz.main:create_app --factory   (or `uv run viz serve` / `uv run viz dev`)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic.json_schema import models_json_schema

from viz import __version__
from viz.api import api_router
from viz.chartdata.reduce import warm_up as warm_up_downsampling
from viz.config import Settings, get_settings
from viz.contracts import EXPORTED_MODELS
from viz.engine import Engine
from viz.errors import VizError
from viz.ingest.catalog import sweep_orphans
from viz.ingest.views import RowViews
from viz.jobs import InProcessJobRunner
from viz.meta.db import create_db_engine, make_sessionmaker, run_migrations
from viz.storage import LocalStorage

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        storage = LocalStorage(settings.data_dir)
        storage.ensure()
        db_engine = create_db_engine(settings.db_url)
        run_migrations(db_engine)
        engine = Engine.from_settings(settings, temp_dir=storage.temp_dir)
        warm_up_downsampling()  # transform: avoids tsdownsample's ~0.25 s first-call cost
        app.state.settings = settings
        app.state.storage = storage
        app.state.sessionmaker = make_sessionmaker(db_engine)
        app.state.engine = engine
        # data-core: background jobs (imports, materialisation, exports).
        jobs = InProcessJobRunner(
            engine=engine,
            storage=storage,
            sessionmaker=app.state.sessionmaker,
            settings=settings,
        )
        app.state.jobs = jobs
        app.state.row_views = RowViews(engine, storage, max_bytes=settings.grid_views_mb * 2**20)
        # Expired uploads/exports and files of imports that never reached the catalog.
        # The job runner repeats the expiry sweep hourly.
        storage.sweep(
            staged_max_age_s=settings.staged_retention_s,
            exports_max_age_s=settings.export_retention_s,
        )
        with app.state.sessionmaker() as db:
            sweep_orphans(db, storage)
        try:
            yield
        finally:
            jobs.close()
            app.state.row_views.close()
            engine.close()
            db_engine.dispose()

    app = FastAPI(
        title="Visualize",
        version=__version__,
        lifespan=lifespan,
        separate_input_output_schemas=False,
    )

    @app.exception_handler(VizError)
    async def _viz_error(_request: Request, exc: VizError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    app.include_router(api_router, prefix="/api")
    _mount_frontend(app)

    def _openapi() -> dict[str, Any]:
        return build_openapi(app)

    app.openapi = _openapi  # type: ignore[method-assign]
    return app


def build_openapi(app: FastAPI) -> dict[str, Any]:
    """OpenAPI schema plus every exported contract, so TS types exist before endpoints do."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
        separate_input_output_schemas=False,
    )
    _, defs = models_json_schema(
        [(model, "validation") for model in EXPORTED_MODELS],
        ref_template="#/components/schemas/{model}",
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for name, definition in defs.get("$defs", {}).items():
        components.setdefault(name, definition)
    app.openapi_schema = schema
    return schema


def _mount_frontend(app: FastAPI) -> None:
    index = STATIC_DIR / "index.html"
    if not index.is_file():

        @app.get("/", include_in_schema=False)
        def _not_built() -> HTMLResponse:
            return HTMLResponse(
                "<p>The frontend isn't built. Run <code>uv run viz build</code>, "
                "or use <code>uv run viz dev</code> for development.</p>"
            )

        return

    static_root = STATIC_DIR.resolve()
    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def _spa(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (STATIC_DIR / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(static_root):
            return FileResponse(candidate)
        return FileResponse(index)
