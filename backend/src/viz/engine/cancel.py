"""Stop a query when the browser gives up on it (CLAUDE.md §6, "Cancel stale work").

The frontend aborts superseded fetches. A sync endpoint can't notice that, so endpoints that
run DuckDB work use `run_cancellable` from an `async def` handler: the work runs in the
threadpool while the request is polled for a disconnect, which interrupts the query by key.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import anyio
from anyio.to_thread import run_sync

from viz.engine.duckdb_engine import Engine

POLL_INTERVAL_S = 0.1


class _Disconnectable(Protocol):
    async def is_disconnected(self) -> bool: ...


async def run_cancellable[T](
    request: _Disconnectable, engine: Engine, key: str, fn: Callable[[], T]
) -> T:
    """Run `fn` (which queries under `key`) in a thread; interrupt `key` if the client leaves."""
    result: list[T] = []
    error: list[BaseException] = []

    async def work(scope: anyio.CancelScope) -> None:
        try:
            result.append(await run_sync(fn))
        except Exception as exc:  # re-raised below, unwrapped from the task group
            error.append(exc)
        scope.cancel()

    async def watch() -> None:
        while True:
            await anyio.sleep(POLL_INTERVAL_S)
            if await request.is_disconnected():
                engine.interrupt(key, "cancelled")
                return

    async with anyio.create_task_group() as group:
        group.start_soon(work, group.cancel_scope)
        group.start_soon(watch)
    if error:
        raise error[0]
    return result[0]
