"""Plot stat transforms: discovery, validation and dispatch.

Each `plots/<family>.py` module registers its plot types with `@register_plot` (see base.py)
and is found here with pkgutil, so plot agents never edit a shared file. Discovery is lazy
and happens once, on first use. Discovery and base: stats; family modules: plot agents.
"""

from __future__ import annotations

import importlib
import pkgutil
import threading
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import Executor
from contextlib import contextmanager

from pydantic import ValidationError

from viz.contracts import (
    PLOT_FAMILY,
    ChartSpec,
    Column,
    PlotInfo,
    PlotType,
    StatResult,
    Viewport,
)
from viz.errors import InvalidSpecError, NotImplementedYetError
from viz.plots.base import (
    PlotDef,
    PlotOutput,
    PlotParams,
    PlotRegistry,
    PlotRequest,
    TableOutput,
    current_registry,
    swap_registry,
)
from viz.stats.relation import Relation, describe_accepts

_SKIP_MODULES = frozenset({"base"})
_discover_lock = threading.RLock()
_discovered = False


def import_plot_modules(package: str, path: Iterable[str]) -> list[str]:
    """Import every module of `package` except base and private (_*) ones."""
    imported = []
    for info in sorted(pkgutil.iter_modules(list(path)), key=lambda m: m.name):
        if info.name in _SKIP_MODULES or info.name.startswith("_"):
            continue
        importlib.import_module(f"{package}.{info.name}")
        imported.append(info.name)
    return imported


def discover() -> None:
    """Import the family modules once. An import error propagates (and is retried next call)."""
    global _discovered
    if _discovered:
        return
    with _discover_lock:
        if _discovered:
            return
        import_plot_modules(__name__, __path__)
        _discovered = True


@contextmanager
def scoped_registry() -> Iterator[PlotRegistry]:
    """Tests only: run with an empty registry, then restore the real one.

    Discovery runs first, so family modules always register into the real registry.
    """
    discover()
    previous = swap_registry(PlotRegistry())
    try:
        yield current_registry()
    finally:
        swap_registry(previous)


def registered() -> Mapping[PlotType, PlotDef]:
    discover()
    return current_registry().all()


def get_plot(plot_type: PlotType) -> PlotDef | None:
    """The plot's server module, or None when it uses chartdata's generic aggregate path."""
    discover()
    return current_registry().get(plot_type)


def plot_infos() -> list[PlotInfo]:
    """Every plot type, with channels and params schema for those with a server module."""
    defs = registered()
    infos = []
    for plot_type, family in PLOT_FAMILY.items():
        definition = defs.get(plot_type)
        infos.append(definition.info() if definition else PlotInfo(type=plot_type, family=family))
    return infos


def channel_problems(
    definition: PlotDef, spec: ChartSpec, columns: Mapping[str, Column]
) -> list[str]:
    """Every problem with the spec's encodings, as sentences for the user."""
    problems: list[str] = []
    for channel in definition.channels:
        encodings = spec.encodings.get(channel.name, [])
        if any(e.field is None for e in encodings):
            problems.append(f"Choose a field for '{channel.name}'.")
        bound = [e.field for e in encodings if e.field is not None]
        if len(bound) < channel.min:
            if channel.min == 1:
                problems.append(f"Choose a field for '{channel.name}'.")
            else:
                problems.append(f"'{channel.name}' needs at least {channel.min} fields.")
        if channel.max is not None and len(bound) > channel.max:
            noun = "field" if channel.max == 1 else "fields"
            problems.append(f"'{channel.name}' takes at most {channel.max} {noun}.")
        for name in bound:
            column = columns.get(name)
            if column is None:
                problems.append(f"Unknown field '{name}' in '{channel.name}'.")
            elif channel.accepts is not None and column.semantic not in channel.accepts:
                problems.append(
                    f"'{channel.name}' needs a {describe_accepts(channel.accepts)} field; "
                    f"'{name}' is not."
                )
    return list(dict.fromkeys(problems))


def parse_params(definition: PlotDef, spec: ChartSpec) -> PlotParams:
    try:
        return definition.params.model_validate(spec.params)
    except ValidationError as exc:
        details = []
        for error in exc.errors():
            where = ".".join(str(part) for part in error["loc"]) or "params"
            details.append(f"'{where}': {error['msg']}")
        raise InvalidSpecError("Invalid chart settings: " + "; ".join(details)) from exc


def run_plot_stat(
    spec: ChartSpec,
    relation: Relation,
    *,
    viewport: Viewport,
    mark_cap: int,
    executor: Executor | None = None,
) -> PlotOutput:
    """Validate the spec against the plot's channels and params, then run its stat."""
    definition = get_plot(spec.plot_type)
    if definition is None:
        raise NotImplementedYetError(
            f"plot type {spec.plot_type.value!r} has no server stat transform"
        )
    problems = channel_problems(definition, spec, relation.columns)
    if problems:
        raise InvalidSpecError(" ".join(problems))
    params = parse_params(definition, spec)
    request = PlotRequest(
        spec=spec,
        params=params,
        relation=relation,
        viewport=viewport,
        mark_cap=mark_cap,
        executor=executor,
    )
    output = definition.stat(request)
    if not isinstance(output, TableOutput | StatResult):
        raise TypeError(
            f"{spec.plot_type.value}: stat returned {type(output).__name__}, "
            "expected TableOutput or StatResult"
        )
    return output


__all__ = [
    "channel_problems",
    "discover",
    "get_plot",
    "import_plot_modules",
    "plot_infos",
    "registered",
    "run_plot_stat",
    "scoped_registry",
]
