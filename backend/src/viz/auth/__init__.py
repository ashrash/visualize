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


def share_access() -> None:
    """Marks a route as intentionally public: the holder of a share/embed token needs no user.

    Use this only on token-gated, read-only routes. It exists so that "public" is spelled out
    at the call site instead of being the absence of `current_user`; a test pins the full list.
    """
