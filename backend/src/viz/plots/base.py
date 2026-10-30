"""The plot-module seam: how a plot family's server module plugs into chart data.

A family module (`plots/<family>.py`, owned by its plot agent) registers one stat function
per plot type:

    class HistogramParams(PlotParams):
        bins: int | None = Field(default=None, ge=1, le=1000)

    @register_plot(
        PlotType.HISTOGRAM,
        channels=[Channel("x", QUANT), Channel("color", CATEGORICAL, min=0)],
        params=HistogramParams,
    )
    def histogram(req: PlotRequest) -> PlotOutput: ...

chartdata calls `viz.plots.run_plot_stat`, which validates the spec's channels and params
before calling the function. A TableOutput becomes an Arrow response with ChartDataMeta; a
StatResult becomes a ChartStatResponse. Plot types without a server module are not
registered and use chartdata's generic aggregate path.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass, field

import pyarrow as pa
from pydantic import JsonValue

from viz.contracts import (
    PLOT_FAMILY,
    ChannelInfo,
    ChartSpec,
    ColumnSemantic,
    Contract,
    PlotInfo,
    PlotType,
    Reduction,
    StatResult,
    Viewport,
)
from viz.stats.relation import Relation

# Column semantics a channel accepts.
QUANT = frozenset({ColumnSemantic.QUANTITATIVE})
TEMPORAL = frozenset({ColumnSemantic.TEMPORAL})
CATEGORICAL = frozenset({ColumnSemantic.NOMINAL, ColumnSemantic.ORDINAL})
NUMERIC_OR_TIME = QUANT | TEMPORAL
GEO = frozenset({ColumnSemantic.GEO})


class PlotRegistrationError(RuntimeError):
    """A plot module registered something invalid. Raised at import time."""


class PlotParams(Contract):
    """Base of every plot's params model. camelCase keys on the wire; unknown keys rejected."""


@dataclass(frozen=True)
class Channel:
    """An encoding channel the plot's stat reads, e.g. Channel("x", QUANT)."""

    name: str
    # Column semantics the channel accepts; None = any.
    accepts: frozenset[ColumnSemantic] | None = None
    # Fewest fields; 0 makes the channel optional.
    min: int = 1
    # Most fields; None = unbounded.
    max: int | None = 1

    def __post_init__(self) -> None:
        if self.min < 0:
            raise PlotRegistrationError(f"channel {self.name!r}: min must be >= 0")
        if self.max is not None and self.max < max(self.min, 1):
            raise PlotRegistrationError(f"channel {self.name!r}: max must be >= max(min, 1)")

    def info(self) -> ChannelInfo:
        accepts = None
        if self.accepts is not None:
            accepts = [s for s in ColumnSemantic if s in self.accepts]
        return ChannelInfo(name=self.name, accepts=accepts, min=self.min, max=self.max)


@dataclass(frozen=True)
class PlotRequest:
    spec: ChartSpec
    # spec.params, validated by the plot's params model.
    params: PlotParams
    # The chart's source with every filter applied.
    relation: Relation
    viewport: Viewport
    # Most marks the response may carry; above it, reduce (bin, sample, aggregate).
    mark_cap: int
    # Process pool for pure-Python-heavy work; None = run inline.
    executor: Executor | None = None

    def fields(self, channel: str) -> list[str]:
        """Field names bound to a channel, in order."""
        return [e.field for e in self.spec.encodings.get(channel, []) if e.field]

    def field(self, channel: str) -> str | None:
        """The first field bound to a channel, or None."""
        names = self.fields(channel)
        return names[0] if names else None


@dataclass(frozen=True)
class TableOutput:
    """Tabular plot data. chartdata sends it as Arrow with a ChartDataMeta built from this."""

    table: pa.Table
    reduction: Reduction
    # e.g. "kde", "reservoir", "fd_bins".
    method: str | None
    # Result column -> display label (ChartDataMeta.fields).
    fields: dict[str, str]
    # Resolved parameters copied to ChartDataMeta.params, e.g. {"binWidth": 2.5}.
    params: dict[str, JsonValue] = field(default_factory=dict)
    # Rows read; None lets chartdata fill it.
    rows_scanned: int | None = None
    # Rows after filters, before reduction; None lets chartdata fill it.
    rows_matched: int | None = None
    warnings: list[str] = field(default_factory=list)


PlotOutput = TableOutput | StatResult
StatFn = Callable[[PlotRequest], PlotOutput]


@dataclass(frozen=True)
class PlotDef:
    plot_type: PlotType
    stat: StatFn
    channels: tuple[Channel, ...]
    params: type[PlotParams]

    def info(self) -> PlotInfo:
        return PlotInfo(
            type=self.plot_type,
            family=PLOT_FAMILY[self.plot_type],
            channels=[c.info() for c in self.channels],
            params_schema=self.params.model_json_schema(by_alias=True),
        )


class PlotRegistry:
    def __init__(self) -> None:
        self._defs: dict[PlotType, PlotDef] = {}
        self._lock = threading.Lock()

    def add(self, definition: PlotDef) -> None:
        with self._lock:
            existing = self._defs.get(definition.plot_type)
            if existing is not None and _identity(existing.stat) != _identity(definition.stat):
                raise PlotRegistrationError(
                    f"plot type {definition.plot_type.value!r} is already registered by "
                    f"{_identity(existing.stat)}"
                )
            self._defs[definition.plot_type] = definition

    def get(self, plot_type: PlotType) -> PlotDef | None:
        return self._defs.get(plot_type)

    def all(self) -> Mapping[PlotType, PlotDef]:
        return dict(self._defs)


def _identity(fn: StatFn) -> str:
    return f"{fn.__module__}.{fn.__qualname__}"


_current = PlotRegistry()


def current_registry() -> PlotRegistry:
    return _current


def swap_registry(registry: PlotRegistry) -> PlotRegistry:
    """Install `registry` and return the previous one. Tests use viz.plots.scoped_registry."""
    global _current
    previous, _current = _current, registry
    return previous


def register_plot(
    plot_type: PlotType,
    *,
    channels: Sequence[Channel],
    params: type[PlotParams] = PlotParams,
) -> Callable[[StatFn], StatFn]:
    """Register `fn` as the server stat transform for `plot_type`."""
    if not (isinstance(params, type) and issubclass(params, PlotParams)):
        raise PlotRegistrationError(f"{plot_type.value}: params must subclass PlotParams")
    names = [c.name for c in channels]
    if len(set(names)) != len(names):
        raise PlotRegistrationError(f"{plot_type.value}: duplicate channel names {names}")

    def decorate(fn: StatFn) -> StatFn:
        module = fn.__module__
        family_module = f"viz.plots.{PLOT_FAMILY[plot_type].value}"
        if module.startswith("viz.plots.") and module != family_module:
            raise PlotRegistrationError(
                f"{module} can't register {plot_type.value!r}; it belongs in {family_module}"
            )
        _current.add(PlotDef(plot_type, fn, tuple(channels), params))
        return fn

    return decorate
