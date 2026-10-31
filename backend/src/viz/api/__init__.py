"""All API routes, mounted under /api. Every route depends on the current user, except the two
deliberate public exceptions documented in `viz.auth`: share/embed resolution and `/health`."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from viz import __version__
from viz.api import assets, charts, datasets, exports, jobs, pipelines, projects, share, stats
from viz.api.deps import DbDep
from viz.auth import current_user

api_router = APIRouter(dependencies=[Depends(current_user)])


class Health(BaseModel):
    status: str
    version: str


# Deliberately unauthenticated (no `current_user` dependency), unlike every other route: a load
# balancer's or container's health probe can't carry credentials. A trivial query proves the
# metadata DB connection works (useful for the Docker healthcheck and AWS's RDS-backed deploys);
# the response discloses only a static status and version, nothing from the DB or data dir, so
# this doesn't weaken the "every router depends on auth.current_user" rule in practice.
health_router = APIRouter(tags=["system"])


@health_router.get("/health", response_model=Health)
def health(db: DbDep) -> Health:
    db.execute(text("SELECT 1"))
    return Health(status="ok", version=__version__)


api_router.include_router(datasets.router)
api_router.include_router(jobs.router)
api_router.include_router(exports.router)
api_router.include_router(charts.router)
api_router.include_router(pipelines.router)
api_router.include_router(projects.router)
api_router.include_router(stats.router)
api_router.include_router(share.router)
api_router.include_router(assets.router)

# Token holders resolve share/embed links without a user; the app mounts this router bare.
public_router = share.public_router
