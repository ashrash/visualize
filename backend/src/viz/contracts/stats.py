"""Uniform result shape for statistics and plot stat transforms."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from viz.contracts.base import Contract, Scalar, SourceRef
from viz.contracts.pipeline import Pipeline
from viz.contracts.predicate import Predicate


class StatResult(Contract):
    values: dict[str, JsonValue]
    # Human-readable method, e.g. "Welch two-sample t-test".
    method: str
    n: int
    # True when computed on a reservoir sample rather than the full data.
    sampled: bool = False
    # Assumption checks and caveats (non-normality, small expected counts, …).
    warnings: list[str] = Field(default_factory=list)
    # Eligible rows before sampling (after filters and missing-value removal). None = same as n.
    n_total: int | None = None
    # Rows dropped because a required field was null, NaN or +/-inf.
    n_missing: int = 0


class Quantile(Contract):
    p: float
    # str = ISO date/timestamp for temporal fields.
    value: float | str | None


class FieldSummary(Contract):
    """One field within one group of a descriptive-statistics table. Server-produced."""

    field: str
    # Aligned with DescribeRequest.group_by; None = the overall row.
    group: list[Scalar] | None
    kind: Literal["numeric", "temporal", "categorical", "boolean"]
    # Non-missing values.
    count: int
    # Null, plus NaN/+-inf for float columns.
    missing: int
    # None when include_mode is false.
    distinct: int | None
    mean: float | None
    std: float | None
    variance: float | None
    sem: float | None
    sum: float | None
    iqr: float | None
    # str = ISO date/timestamp.
    min: float | str | None
    max: float | str | None
    median: float | str | None
    q1: float | str | None
    q3: float | str | None
    # Adjusted Fisher-Pearson G1.
    skewness: float | None
    # Excess, bias-corrected G2.
    kurtosis: float | None
    # The requested percentiles.
    quantiles: list[Quantile]
    modes: list[Scalar]
    mode_count: int | None


class DescribeRequest(Contract):
    source: SourceRef
    # Pipelines the source references, as for chart data.
    pipelines: list[Pipeline] = Field(default_factory=list)
    fields: list[str] = Field(min_length=1, max_length=50)
    group_by: list[str] = Field(default_factory=list, max_length=2)
    filters: list[Predicate] = Field(default_factory=list)
    percentiles: list[Annotated[float, Field(ge=0, le=1)]] = Field(
        default_factory=lambda: [0.05, 0.95], max_length=20
    )
    include_overall: bool = True
    include_mode: bool = True
    max_groups: int = Field(default=50, ge=1, le=500)
