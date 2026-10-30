"""`RequestScopeMiddleware`: one request owner (with an arrival ticket) per HTTP request.

Pure ASGI, so the contextvar set here is the context Starlette copies into the threadpool
for sync endpoints: every cursor a request opens carries the same owner, a stale request
can never supersede a newer one on the same key, and two cursors of one request on a key
don't supersede each other. Wire it with `app.add_middleware(RequestScopeMiddleware)`.

Don't open `request_scope()` in a FastAPI dependency instead: sync dependencies run in their
own copied context, so the owner never reaches the endpoint.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from viz.engine.duckdb_engine import request_scope


class RequestScopeMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        with request_scope():
            await self.app(scope, receive, send)
