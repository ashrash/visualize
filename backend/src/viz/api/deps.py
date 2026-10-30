"""FastAPI dependencies for shared app state."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from viz.config import Settings
from viz.engine import Engine
from viz.storage import LocalStorage


def get_settings_dep(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def get_storage(request: Request) -> LocalStorage:
    return cast(LocalStorage, request.app.state.storage)


def get_db(request: Request) -> Iterator[Session]:
    factory = cast(sessionmaker[Session], request.app.state.sessionmaker)
    with factory() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
EngineDep = Annotated[Engine, Depends(get_engine)]
StorageDep = Annotated[LocalStorage, Depends(get_storage)]
DbDep = Annotated[Session, Depends(get_db)]
