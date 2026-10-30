"""Authentication. Local mode has one user; on AWS this becomes OIDC (Cognito).

Every API router depends on `current_user`. Never add a route that skips it.
"""

from __future__ import annotations

from pydantic import BaseModel


class User(BaseModel):
    id: str
    name: str


LOCAL_USER = User(id="local", name="Local user")


def current_user() -> User:
    return LOCAL_USER
