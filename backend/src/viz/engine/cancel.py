"""Stop a request's queries when the browser gives up on it (CLAUDE.md §6, "Cancel stale work").

The frontend aborts superseded fetches. A sync endpoint can't notice that, so endpoints that
run DuckDB work call `run_cancellable` from an `async def` handler. It opens a request scope
(an owner with an arrival ticket, see `viz.engine.duckdb_engine`), runs the work in the
threadpool, and polls the request for a disconnect. On disconnect it cancels *this request's*
cursors only (`Engine.cancel_owner`), never whatever currently holds the key, so a newer
request on the same key is unaffected.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import anyio
from anyio.to_thread import run_sync

from viz.engine.duckdb_engine import Engine, new_owner, request_scope

POLL_INTERVAL_S = 0.1


class _Disconnectable(Protocol):
    async def is_disconnected(self) -> bool: ...


async def run_cancellable[T](
    request: _Disconnectable, engine: Engine, key: str | None, fn: Callable[[], T]
) -> T:
    """Run `fn` in a thread as one request; cancel its queries if the client leaves.

    `key` is unused (kept for callers written against the first version); queries inside
    `fn` pass their own keys, and the request's ticket orders them against newer requests.
    """
    del key
    owner = new_owner()  # the ticket is taken at arrival
    result: list[T] = []
    error: list[BaseException] = []

    async def work(scope: anyio.CancelScope) -> None:
        try:
            with request_scope(owner):  # anyio copies the context into the worker thread
                result.append(await run_sync(fn))
        except Exception as exc:  # re-raised below, unwrapped from the task group
            error.append(exc)
        scope.cancel()

    async def watch() -> None:
        while True:
            await anyio.sleep(POLL_INTERVAL_S)
            if await request.is_disconnected():
                engine.cancel_owner(owner)
                return

    async with anyio.create_task_group() as group:
        group.start_soon(work, group.cancel_scope)
        group.start_soon(watch)
    if error:
        raise error[0]
    return result[0]
