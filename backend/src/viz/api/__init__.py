"""All API routes, mounted under /api. Every route depends on the current user."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from viz import __version__
from viz.api import charts, datasets, exports, jobs, pipelines, projects, share, stats
from viz.auth import current_user

api_router = APIRouter(dependencies=[Depends(current_user)])


class Health(BaseModel):
    status: str
    version: str


@api_router.get("/health", response_model=Health, tags=["system"])
def health() -> Health:
    return Health(status="ok", version=__version__)


api_router.include_router(datasets.router)
api_router.include_router(jobs.router)
api_router.include_router(exports.router)
api_router.include_router(charts.router)
api_router.include_router(pipelines.router)
api_router.include_router(projects.router)
api_router.include_router(stats.router)
api_router.include_router(share.router)

# Token holders resolve share/embed links without a user; the app mounts this router bare.
public_router = share.public_router
