"""Data-preparation pipelines: an ordered list of steps applied to a source.

A pipeline compiles to SQL over the source's Parquet version (transform/). Its output is a
derived dataset that charts can read via SourceRef(kind="pipeline").
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from viz.contracts.base import AggFn, Contract, Scalar, SourceRef
from viz.contracts.predicate import Predicate


class StepBase(Contract):
    id: str
    enabled: bool = True
    # Write this step's output to Parquet (as a job) instead of keeping it a view.
    materialize: bool = False


class FilterStep(StepBase):
    op: Literal["filter"]
    predicates: list[Predicate] = Field(default_factory=list)
    # Optional formula (validated DuckDB expression subset), ANDed with the predicates.
    expression: str | None = None


class SortKey(Contract):
    field: str
    descending: bool = False
    nulls_last: bool = True


class SortStep(StepBase):
    op: Literal["sort"]
    keys: list[SortKey]


class SelectStep(StepBase):
    op: Literal["select"]
    fields: list[str]


class RenameStep(StepBase):
    op: Literal["rename"]
    mapping: dict[str, str]


class DataType(StrEnum):
    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"


class Cast(Contract):
    field: str
    to: DataType
    # strptime format for string → date/timestamp, e.g. "%d/%m/%Y".
    format: str | None = None


class CastStep(StepBase):
    op: Literal["cast"]
    casts: list[Cast]


class FillStrategy(StrEnum):
    VALUE = "value"
    MEAN = "mean"
    MEDIAN = "median"
    MODE = "mode"
    FORWARD = "forward"
    BACKWARD = "backward"


class Fill(Contract):
    field: str
    strategy: FillStrategy
    value: Scalar = None


class FillMissingStep(StepBase):
    op: Literal["fill_missing"]
    fills: list[Fill]


class DropMissingStep(StepBase):
    op: Literal["drop_missing"]
    # None means all columns.
    fields: list[str] | None = None
    how: Literal["any", "all"] = "any"


class DedupeStep(StepBase):
    op: Literal["dedupe"]
    # None means compare whole rows.
    fields: list[str] | None = None
    keep: Literal["first", "last"] = "first"


class CalcStep(StepBase):
    op: Literal["calc"]
    name: str
    # Validated DuckDB expression subset (see transform/ formula allowlist).
    expression: str


class Aggregation(Contract):
    field: str | None = None
    fn: AggFn
    as_name: str


class GroupAggregateStep(StepBase):
    op: Literal["group_aggregate"]
    by: list[str]
    aggregations: list[Aggregation]


class JoinKey(Contract):
    left: str
    right: str


class JoinStep(StepBase):
    op: Literal["join"]
    right: SourceRef
    how: Literal["inner", "left", "right", "full"] = "left"
    on: list[JoinKey]
    suffix: str = "_right"


class UnionStep(StepBase):
    op: Literal["union"]
    others: list[SourceRef]
    by_name: bool = True


class PivotStep(StepBase):
    op: Literal["pivot"]
    index: list[str]
    columns: str
    values: str
    fn: AggFn = AggFn.SUM


class UnpivotStep(StepBase):
    op: Literal["unpivot"]
    id_fields: list[str]
    value_fields: list[str]
    name_field: str = "variable"
    value_field: str = "value"


class NormalizeStep(StepBase):
    op: Literal["normalize"]
    fields: list[str]
    method: Literal["zscore", "minmax", "robust"] = "zscore"


Step = Annotated[
    FilterStep
    | SortStep
    | SelectStep
    | RenameStep
    | CastStep
    | FillMissingStep
    | DropMissingStep
    | DedupeStep
    | CalcStep
    | GroupAggregateStep
    | JoinStep
    | UnionStep
    | PivotStep
    | UnpivotStep
    | NormalizeStep,
    Field(discriminator="op"),
]


class Pipeline(Contract):
    id: str
    name: str = "Untitled pipeline"
    source: SourceRef
    steps: list[Step] = Field(default_factory=list)
