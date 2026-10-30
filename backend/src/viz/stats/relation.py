"""Relation: a filtered, read-only view of one source, plus the engine that queries it.

Every SQL kernel in stats/ and every plot stat transform reads data through a Relation.
transform builds them (`resolve_source`) from a SourceRef, its pipelines and the filters.

This is a leaf module: it imports only the engine, contracts, sqlglot and pyarrow, so
chartdata and transform can depend on it without cycles.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

import duckdb
import pyarrow as pa
from sqlglot import exp

from viz.contracts import Column, ColumnSemantic
from viz.engine import Engine
from viz.stats.errors import StatsInputError

_SEMANTIC_LABEL = {
    ColumnSemantic.QUANTITATIVE: "numeric",
    ColumnSemantic.TEMPORAL: "date/time",
    ColumnSemantic.NOMINAL: "categorical",
    ColumnSemantic.ORDINAL: "ordinal",
    ColumnSemantic.GEO: "geographic",
}


def col(name: str) -> exp.Column:
    """A quoted column reference. Never build SQL by formatting names into strings."""
    return exp.column(name, quoted=True)


def describe_accepts(accepts: frozenset[ColumnSemantic]) -> str:
    labels = sorted({_SEMANTIC_LABEL[s] for s in accepts})
    return " or ".join(labels)


@dataclass(frozen=True)
class Relation:
    engine: Engine
    # FROM-able source: read_parquet(...) or a compiled pipeline query (wrapped as a subquery).
    source: exp.Expr
    # All filters, already compiled (spec filters, dashboard filters, cross-filters).
    where: exp.Expr | None
    columns: Mapping[str, Column]
    # Rows in the source before filters, when known (dataset versions know it).
    row_count: int | None
    # Identifies the data: "<datasetId>@v<n>", plus a pipeline hash. Use in cache keys.
    version_key: str
    # Engine interrupt slot (e.g. "chart:<id>"): a newer query in the slot cancels older ones.
    query_key: str | None = None
    # Request generation within the slot (an older one never cancels a newer one). Take it
    # only from viz.engine.next_generation(), never from a client-supplied value. None uses
    # the request ticket from request_scope().
    generation: int | None = None

    def from_item(self) -> exp.Expr:
        if isinstance(self.source, exp.Query):
            return self.source.subquery("_src")
        return self.source

    def select(self, *exprs: exp.Expr | str) -> exp.Select:
        """SELECT exprs FROM source WHERE filters. Add GROUP BY / ORDER BY as needed."""
        query = exp.select(*exprs).from_(self.from_item())
        if self.where is not None:
            query = query.where(self.where)
        return query

    def filtered(self, *fields: str) -> exp.Select:
        """The filtered rows (only `fields` when given), for use as a subquery."""
        selects: list[exp.Expr] = [col(f) for f in fields] if fields else [exp.Star()]
        return self.select(*selects)

    def narrow(self, condition: exp.Expr) -> Relation:
        """The same relation with one more filter ANDed on."""
        where = condition if self.where is None else exp.and_(self.where, condition)
        return dataclasses.replace(self, where=where)

    def arrow(self, query: exp.Expr | str, params: list[object] | None = None) -> pa.Table:
        sql = query if isinstance(query, str) else query.sql(dialect="duckdb")
        with self._cursor() as cur:
            return cur.sql(sql, params=params).to_arrow_table()

    def rows(self, query: exp.Expr | str, params: list[object] | None = None) -> list[Any]:
        sql = query if isinstance(query, str) else query.sql(dialect="duckdb")
        with self._cursor() as cur:
            return cur.sql(sql, params=params).fetchall()

    def count(self) -> int:
        """Rows after filters."""
        result = self.rows(self.select(exp.Count(this=exp.Star())))
        return int(result[0][0])

    def require(self, field: str, accepts: frozenset[ColumnSemantic] | None = None) -> Column:
        column = self.columns.get(field)
        if column is None:
            raise StatsInputError(f"Unknown field '{field}'.")
        if accepts is not None and column.semantic not in accepts:
            raise StatsInputError(
                f"'{field}' must be a {describe_accepts(accepts)} field; "
                f"it is {_SEMANTIC_LABEL[column.semantic]}."
            )
        return column

    def _cursor(self) -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        return self.engine.cursor(self.query_key, generation=self.generation)
