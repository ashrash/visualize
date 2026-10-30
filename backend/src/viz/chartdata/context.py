"""Per-request state for chart data (internal to chartdata; plot modules see PlotRequest)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pyarrow as pa
from pydantic import JsonValue
from sqlglot import exp

from viz.chartdata.sql import to_sql
from viz.contracts import ChartSpec, Column, Encoding, Reduction, Viewport
from viz.engine import current_owner
from viz.errors import InvalidSpecError, QueryInterruptedError
from viz.stats.relation import Relation
from viz.transform import runtime
from viz.transform.source import resolve_field


@dataclass
class ChartContext:
    spec: ChartSpec
    relation: Relation
    viewport: Viewport
    mark_cap: int
    # Cancellation slot, e.g. "chart:<slot or spec id>". Inside a request scope the engine
    # orders queries on it by the request's arrival ticket: a newer request supersedes this
    # one, and this one's next query is refused with "superseded" (409).
    key: str

    def check_current(self) -> None:
        """Stop between Python-side steps once this request's client has gone away."""
        owner = current_owner()
        if owner is not None and owner.cancelled:
            raise QueryInterruptedError("cancelled")

    def query(self, query: exp.Expr) -> pa.Table:
        """Run `query` in this request's cancellation slot."""
        self.check_current()
        return runtime.query_arrow(self.relation.engine, to_sql(query), key=self.key)

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
