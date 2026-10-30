"""Cancellation slots: a newer chart request supersedes older ones in the same slot.

A slot is `chart:<request.slot or spec.id>`. Each request takes the next generation of its
slot. Before every query, chartdata checks the request is still the newest; if not, it stops
with "superseded" (409). The engine also interrupts the older query running under the same
key when the newer one starts.

TODO(data-core): once `Engine.cursor(key, generation=n)` lands, pass the generation through
Relation so the engine itself never lets an older generation interrupt a newer one (today a
request that is between queries could still start one after a newer request began).
"""

from __future__ import annotations

import threading
import weakref

from viz.engine import Engine
from viz.errors import QueryInterruptedError


class SlotRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, int] = {}
        self._active: dict[str, int] = {}

    def begin(self, key: str) -> int:
        with self._lock:
            generation = self._latest.get(key, 0) + 1
            self._latest[key] = generation
            self._active[key] = self._active.get(key, 0) + 1
            return generation

    def end(self, key: str) -> None:
        with self._lock:
            remaining = self._active.get(key, 1) - 1
            if remaining <= 0:
                # Forget idle slots so the maps don't grow with every chart ever drawn.
                self._active.pop(key, None)
                self._latest.pop(key, None)
            else:
                self._active[key] = remaining

    def supersede(self, key: str) -> None:
        """Make every request currently in the slot stale (e.g. its client disconnected)."""
        with self._lock:
            if key in self._latest:
                self._latest[key] += 1

    def is_current(self, key: str, generation: int) -> bool:
        with self._lock:
            return self._latest.get(key) == generation

    def check(self, key: str, generation: int) -> None:
        if not self.is_current(key, generation):
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
