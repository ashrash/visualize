"""Predicate: the single filter type.

Dashboard filters, chart filters, click-to-filter, brush selections and drill-down all
produce Predicates. Values are JSON scalars; dates and timestamps are ISO-8601 strings.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from viz.contracts.base import Contract, Scalar


class InPredicate(Contract):
    """Membership. `None` in `values` matches NULL. `not_in` keeps NULL rows unless `None` is
    in `values`, so excluding "North" never silently drops rows with a missing region."""

    op: Literal["in", "not_in"]
    field: str
    values: list[Scalar]


class EqPredicate(Contract):
    op: Literal["eq"]
    field: str
    value: Scalar


class RangePredicate(Contract):
    """Numeric range, or date range when op is `between_dates`. A missing bound is open."""

    op: Literal["range", "between_dates"]
    field: str
    min: float | str | None = None
    max: float | str | None = None
    include_min: bool = True
    include_max: bool = True


class IsNullPredicate(Contract):
    op: Literal["is_null"]
    field: str
    is_null: bool = True


Predicate = Annotated[
    InPredicate | EqPredicate | RangePredicate | IsNullPredicate,
    Field(discriminator="op"),
]
