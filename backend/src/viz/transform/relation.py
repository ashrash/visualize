"""The filtered-source type shared by chartdata, stats and exports.

TODO(stats): this mirrors the agreed interface of `viz.stats.relation.Relation` until stats'
seam commit lands on main; then this module becomes `from viz.stats.relation import Relation`.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

import pyarrow as pa
from sqlglot import exp

from viz.chartdata.sql import to_sql
from viz.contracts import Column, ColumnSemantic
from viz.engine import Engine
from viz.errors import InvalidSpecError
from viz.transform import runtime


@dataclass(frozen=True)
class Relation:
    engine: Engine
    # FROM item: read_parquet(...) or an aliased compiled-pipeline subquery.
    source: exp.Expr
    where: exp.Expr | None
    # Exact names as they appear in the source.
    columns: Mapping[str, Column]
    # Rows in the unfiltered source, when known without scanning.
    row_count: int | None
    # Changes whenever the source's data can change (dataset version, compiled SQL hash).
    version_key: str
    # Engine cancellation key and generation for queries run through `arrow`.
    query_key: str | None = None
    generation: int | None = None

    def select(self, *expressions: exp.Expr) -> exp.Select:
        query = exp.select(*(expressions or (exp.Star(),))).from_(self.source.copy())
        if self.where is not None:
            query = query.where(self.where.copy())
        return query

    def arrow(self, query: exp.Expr) -> pa.Table:
        return runtime.query_arrow(self.engine, to_sql(query), key=self.query_key)

    def field(self, name: str) -> Column:
        """Look a column up by name (case-insensitively, like DuckDB)."""
        found = self.columns.get(name)
        if found is not None:
            return found
        lowered = name.lower()
        for column in self.columns.values():
            if column.name.lower() == lowered:
                return column
        raise InvalidSpecError(f"unknown field {name!r}")

    def require(
        self, field: str, accepts: Collection[ColumnSemantic] | None = None
    ) -> Column:
        column = self.field(field)
        if accepts is not None and column.semantic not in accepts:
            allowed = ", ".join(sorted(s.value for s in accepts))
            raise InvalidSpecError(
                f"field {column.name!r} is {column.semantic.value}; this needs {allowed}"
            )
        return column
