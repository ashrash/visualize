"""Uniform result shape for statistics and plot stat transforms."""

from __future__ import annotations

from pydantic import Field, JsonValue

from viz.contracts.base import Contract


class StatResult(Contract):
    values: dict[str, JsonValue]
    # Human-readable method, e.g. "Welch two-sample t-test".
    method: str
    n: int
    # True when computed on a reservoir sample rather than the full data.
    sampled: bool = False
    # Assumption checks and caveats (non-normality, small expected counts, …).
    warnings: list[str] = Field(default_factory=list)
