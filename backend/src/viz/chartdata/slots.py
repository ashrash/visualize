"""Cancellation slots: a newer chart request supersedes older ones in the same slot.

A slot is `chart:<request.slot or spec.id>`. The endpoint takes a Ticket (slot key plus a
generation from one monotonic counter that never resets) before any work starts. Before every
query the request checks its ticket is still the newest in its slot; if not, it stops with
"superseded" (409). The engine also interrupts the older query running under the same key
when the newer one starts.

A client disconnect cancels only its own ticket, and only while that ticket is still the
newest: an aborted older request can never fail the newer one that replaced it.

TODO(data-core): data-core's engine rework adds per-request owner tokens and a monotonic
arrival ticket. When it lands, `Ticket` maps onto it, `cancel(ticket)` becomes the engine's
conditional cancel, and `TicketEngine` goes away.
"""

from __future__ import annotations

import itertools
import threading
import weakref
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import duckdb
import pyarrow as pa

from viz.engine import Engine
from viz.errors import QueryInterruptedError
from viz.transform.runtime import open_cursor


@dataclass(frozen=True)
class Ticket:
    key: str
    generation: int


class SlotRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counter: Iterator[int] = itertools.count(1)
        self._latest: dict[str, int] = {}
        self._active: dict[str, int] = {}
        self._cancelled: set[int] = set()

    def begin(self, key: str) -> Ticket:
        with self._lock:
            generation = next(self._counter)
            self._latest[key] = generation
            self._active[key] = self._active.get(key, 0) + 1
            return Ticket(key, generation)

    def end(self, ticket: Ticket) -> None:
        with self._lock:
            self._cancelled.discard(ticket.generation)
            remaining = self._active.get(ticket.key, 1) - 1
            if remaining <= 0:
                # Forget idle slots; generations still never repeat (one global counter).
                self._active.pop(ticket.key, None)
                self._latest.pop(ticket.key, None)
            else:
                self._active[ticket.key] = remaining

    def cancel(self, ticket: Ticket) -> bool:
        """Cancel `ticket` if it is still the newest in its slot. Returns whether it was, i.e.
        whether the query running under the slot's key belongs to this ticket."""
        with self._lock:
            if self._latest.get(ticket.key) != ticket.generation:
                return False
            self._cancelled.add(ticket.generation)
            return True

    def is_current(self, ticket: Ticket) -> bool:
        with self._lock:
            return (
                self._latest.get(ticket.key) == ticket.generation
                and ticket.generation not in self._cancelled
            )

    def check(self, ticket: Ticket) -> None:
        if not self.is_current(ticket):
            raise QueryInterruptedError("superseded")


_registries: weakref.WeakKeyDictionary[Engine, SlotRegistry] = weakref.WeakKeyDictionary()
_registries_lock = threading.Lock()


def slots_for(engine: Engine) -> SlotRegistry:
    with _registries_lock:
        registry = _registries.get(engine)
        if registry is None:
            registry = SlotRegistry()
            _registries[engine] = registry
        return registry


class TicketEngine:
    """The engine as one chart request sees it: every cursor first checks that the request's
    ticket is still current, so plot modules' own queries (through Relation) stop too.

    TODO(data-core): remove once Engine.cursor(key, generation=) enforces this itself.
    """

    def __init__(self, engine: Engine, slots: SlotRegistry, ticket: Ticket) -> None:
        self._engine = engine
        self._slots = slots
        self._ticket = ticket

    def cursor(
        self, key: str | None = None, generation: int | None = None
    ) -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        self._slots.check(self._ticket)
        # Forwarded to the engine once it accepts generations (see runtime.open_cursor).
        return open_cursor(self._engine, key, generation)

    def query_arrow(
        self, sql: str, params: Sequence[object] | None = None, *, key: str | None = None
    ) -> pa.Table:
        self._slots.check(self._ticket)
        return self._engine.query_arrow(sql, params, key=key)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._engine, name)
