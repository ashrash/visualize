"""StatResult helpers and conventions.

Conventions for every StatResult (plan section 2):
- `values` keys are camelCase. Curves are column-oriented ({"x": [...], "y": [...],
  "lower": [...], "upper": [...]}); intervals use estimate/ciLow/ciHigh/ciLevel; tests use
  statistic/pValue/df; groups are [{"key": [...], "n": ..., ...}]; probabilities are in [0, 1].
- `n` is the number of observations the statistic used (the sample size when sampled);
  `nTotal` is the eligible rows before sampling; `nMissing` the rows dropped as null/non-finite.
- Undefined parts are null plus one warning saying why. The output never contains NaN.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Mapping
from decimal import Decimal

import numpy as np
from pydantic import JsonValue

from viz.contracts import StatResult


def json_values(value: object) -> JsonValue:
    """Convert numpy/arrow-ish values to JSON-safe Python. NaN and +/-inf become None."""
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, int | np.integer):
        return int(value)
    if isinstance(value, float | np.floating | Decimal):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.ndarray):
        if value.dtype.kind == "f":
            cleaned = np.where(np.isfinite(value), value, np.nan)
            return [json_values(v) for v in cleaned.tolist()]
        return [json_values(v) for v in value.tolist()]
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): json_values(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_values(v) for v in value]
    raise TypeError(f"can't convert {type(value).__name__} to JSON")


def finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def dedupe(messages: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(m for m in messages if m))


def sample_note(n: int, n_total: int) -> str:
    return f"Computed on a random sample of {n:,} of {n_total:,} rows."


def stat_result(
    values: Mapping[str, object],
    *,
    method: str,
    n: int,
    n_total: int | None = None,
    n_missing: int = 0,
    sampled: bool | None = None,
    warnings: Iterable[str] = (),
) -> StatResult:
    """Build a StatResult with JSON-safe values and the sampling note when sampled."""
    is_sampled = sampled if sampled is not None else (n_total is not None and n_total > n)
    notes = list(warnings)
    if is_sampled and n_total is not None:
        notes.append(sample_note(n, n_total))
    converted = json_values(dict(values))
    assert isinstance(converted, dict)
    return StatResult(
        values=converted,
        method=method,
        n=n,
        sampled=is_sampled,
        warnings=dedupe(notes),
        n_total=n_total,
        n_missing=n_missing,
    )
