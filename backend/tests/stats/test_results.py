from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import numpy as np
import pytest

from viz.stats.results import json_values, stat_result


def test_json_values_cleans_numpy_and_non_finite() -> None:
    value = {
        "a": np.array([1.0, np.nan, np.inf, -2.5]),
        "b": np.int64(3),
        "c": np.float32(0.5),
        "d": [np.bool_(True), None, Decimal("1.5")],
        "e": dt.date(2024, 1, 2),
        "f": dt.datetime(2024, 1, 2, 3, 4, 5),
        "g": float("nan"),
        "h": np.array([1, 2], dtype=np.int32),
    }
    assert json_values(value) == {
        "a": [1.0, None, None, -2.5],
        "b": 3,
        "c": 0.5,
        "d": [True, None, 1.5],
        "e": "2024-01-02",
        "f": "2024-01-02T03:04:05",
        "g": None,
        "h": [1, 2],
    }
    with pytest.raises(TypeError):
        json_values(object())


def test_stat_result_adds_the_sampling_note_and_dedupes() -> None:
    result = stat_result(
        {"x": np.array([np.nan, 1.0])},
        method="m",
        n=100,
        n_total=1000,
        n_missing=3,
        warnings=["w", "w"],
    )
    assert result.sampled and result.n_total == 1000 and result.n_missing == 3
    assert result.warnings == ["w", "Computed on a random sample of 100 of 1,000 rows."]
    assert json.loads(result.model_dump_json(by_alias=True))["values"] == {"x": [None, 1.0]}
    exact = stat_result({}, method="m", n=5, n_total=5)
    assert not exact.sampled and exact.warnings == []
