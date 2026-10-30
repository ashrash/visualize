"""Per-request state for chart data (internal to chartdata; plot modules see PlotRequest)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pyarrow as pa
from pydantic import JsonValue
from sqlglot import exp

from viz.chartdata.slots import SlotRegistry, Ticket
from viz.chartdata.sql import to_sql
from viz.contracts import ChartSpec, Column, Encoding, Reduction, Viewport
from viz.errors import InvalidSpecError
from viz.stats.relation import Relation
from viz.transform import runtime
from viz.transform.source import resolve_field


@dataclass
class ChartContext:
    spec: ChartSpec
    relation: Relation
    viewport: Viewport
    mark_cap: int
    ticket: Ticket
    slots: SlotRegistry

    def check_current(self) -> None:
        self.slots.check(self.ticket)

    def query(self, query: exp.Expr) -> pa.Table:
        """Run `query` in this request's cancellation slot (stale requests stop here)."""
        self.check_current()
        return runtime.query_arrow(
            self.relation.engine,
            to_sql(query),
            key=self.ticket.key,
            generation=self.ticket.generation,
        )

    def column(self, name: str) -> Column:
        return self.relation.columns[resolve_field(self.relation.columns, name)]

    def encodings(self, channel: str) -> list[Encoding]:
        return list(self.spec.encodings.get(channel, []))

    def one(self, channel: str, *, required: bool) -> Encoding | None:
        found = self.encodings(channel)
        if len(found) > 1:
            raise InvalidSpecError(f"'{channel}' takes one field")
        if not found:
            if required:
                raise InvalidSpecError(f"Choose a field for '{channel}'.")
            return None
        encoding = found[0]
        if required and encoding.field is None:
            raise InvalidSpecError(f"Choose a field for '{channel}'.")
        return encoding


@dataclass
class ChartOutput:
    table: pa.Table
    reduction: Reduction
    method: str | None
    fields: dict[str, str]
    params: dict[str, JsonValue] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    rows_matched: int | None = None
    x_domain: tuple[float | str, float | str] | None = None
