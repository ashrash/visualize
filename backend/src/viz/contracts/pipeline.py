"""Data-preparation pipelines: an ordered list of steps applied to a source.

A pipeline compiles to SQL over the source's Parquet version (transform/). Its output is a
derived dataset that charts can read via SourceRef(kind="pipeline").
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from viz.contracts.base import AggFn, Contract, Scalar, SortKey, SourceRef
from viz.contracts.dataset import ColumnSemantic
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


class SchemaField(Contract):
    name: str
    # DuckDB type name.
    type: str
    semantic: ColumnSemantic


class StepError(Contract):
    message: str
    field: str | None = None
    # Character offsets into the step's formula.
    start: int | None = None
    end: int | None = None


class StepSchema(Contract):
    """Server-produced."""

    step_id: str
    status: Literal["ok", "error", "blocked", "disabled"]
    columns: list[SchemaField]
    error: StepError | None
    materialized: Literal["none", "ready", "pending"]


class PipelineInspectRequest(Contract):
    pipeline: Pipeline
    # Pipelines referenced by the source, joins or unions (unsaved edits included).
    pipelines: list[Pipeline] = Field(default_factory=list)
    include_sql: bool = False


class PipelineInspectResult(Contract):
    """Server-produced."""

    ok: bool
    source_columns: list[SchemaField]
    steps: list[StepSchema]
    columns: list[SchemaField]
    sql: str | None


class PipelinePreviewRequest(Contract):
    pipeline: Pipeline
    pipelines: list[Pipeline] = Field(default_factory=list)
    # Data after this step; None = the final output.
    step_id: str | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1)
    sort: list[SortKey] = Field(default_factory=list)
    filters: list[Predicate] = Field(default_factory=list)


class PipelinePreviewMeta(Contract):
    """Server-produced; travels as viz.meta in the Arrow schema."""

    step_id: str | None
    offset: int
    limit: int
    total_rows: int
    columns: list[SchemaField]
    ms: float
    warnings: list[str]


class FormulaCheckRequest(Contract):
    expression: str
    pipeline: Pipeline
    pipelines: list[Pipeline] = Field(default_factory=list)
    # Validate against this step's input; None = the final output.
    before_step_id: str | None = None


class FormulaCheckResult(Contract):
    """Server-produced."""

    ok: bool
    error: StepError | None
    output_type: str | None
    fields: list[str]


class FormulaFunction(Contract):
    """Server-produced; feeds formula autocomplete."""

    name: str
    category: Literal["math", "string", "date", "conditional", "null", "type"]
    signature: str
    description: str


class MaterializeRequest(Contract):
    pipeline: Pipeline
    pipelines: list[Pipeline] = Field(default_factory=list)


class MaterializeResult(Contract):
    """Server-produced."""

    # Step id -> job id (started, or already running).
    jobs: dict[str, str]
    # Step ids already materialized.
    ready: list[str]
