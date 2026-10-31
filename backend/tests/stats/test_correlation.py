from __future__ import annotations

import datetime as dt
import math
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from scipy import stats as sps

from viz.contracts import CorrelationRequest, CorrelationValues, SourceRef
from viz.stats import correlation as corr_module
from viz.stats.correlation import correlation, fisher_interval
from viz.stats.errors import StatsInputError

SOURCE = SourceRef(kind="dataset", id="d")


def _run(rel: Any, **kwargs: Any) -> tuple[Any, CorrelationValues]:
    result = correlation(rel, CorrelationRequest(source=SOURCE, **kwargs))
    return result, CorrelationValues.model_validate(result.values)


@pytest.fixture
def frame() -> pd.DataFrame:
    rng = np.random.default_rng(12)
    n = 300
    a = rng.normal(0, 1, n)
    b = 0.6 * a + rng.normal(0, 0.8, n)
    c = np.exp(0.5 * b) + rng.normal(0, 0.3, n)
    b_obj: list[float | None] = b.tolist()
    c_obj: list[float | None] = c.tolist()
    for i in (3, 10, 50):
        b_obj[i] = None
    c_obj[7] = float("nan")
    c_obj[8] = float("inf")
    c_obj[50] = None
    return pd.DataFrame({"a": a, "b": b_obj, "c": c_obj}, dtype=float)


def _clean(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.replace([np.inf, -np.inf], np.nan)


def test_pearson_pairwise_matches_scipy(table_relation: Any, frame: pd.DataFrame) -> None:
    rel = table_relation(pa.Table.from_pandas(frame, preserve_index=False))
    result, v = _run(rel, fields=["a", "b", "c"], ci_level=0.9)
    clean = _clean(frame)
    expected = clean.corr(method="pearson")
    np.testing.assert_allclose(
        np.array(v.coefficient, dtype=float), expected.to_numpy(), rtol=1e-12
    )
    for i, j in [(0, 1), (0, 2), (1, 2)]:
        pair = clean.iloc[:, [i, j]].dropna()
        ref = sps.pearsonr(pair.iloc[:, 0], pair.iloc[:, 1])
        assert v.n[i][j] == len(pair) == v.n[j][i]
        assert v.p_value[i][j] == pytest.approx(ref.pvalue, rel=1e-9)
        ci = ref.confidence_interval(0.9)
        assert (v.ci_low[i][j], v.ci_high[i][j]) == pytest.approx((ci.low, ci.high), rel=1e-10)
        assert v.p_value[j][i] == v.p_value[i][j]
    assert [v.n[i][i] for i in range(3)] == [300, 297, 297]
    assert [v.coefficient[i][i] for i in range(3)] == [1.0, 1.0, 1.0]
    assert v.p_value[0][0] is None
    assert result.method == "Pearson correlation (exact), pairwise deletion"
    assert result.n == 300 and not result.sampled


def test_pearson_listwise(table_relation: Any, frame: pd.DataFrame) -> None:
    rel = table_relation(pa.Table.from_pandas(frame, preserve_index=False))
    result, v = _run(rel, fields=["a", "b", "c"], missing="listwise")
    complete = _clean(frame).dropna()
    np.testing.assert_allclose(
        np.array(v.coefficient, dtype=float), complete.corr().to_numpy(), rtol=1e-12
    )
    assert result.n == len(complete) == 295 and result.n_missing == 5
    assert all(n == 295 for row in v.n for n in row)


def test_pearson_precision_at_large_offsets_and_magnitudes(table_relation: Any) -> None:
    rng = np.random.default_rng(3)
    x = rng.normal(0, 1, 5000)
    y = 0.4 * x + rng.normal(0, 1, 5000)
    expected = float(np.corrcoef(x, y)[0, 1])
    rel = table_relation({"x": x + 1e9, "y": y + 1e9, "big": x * 1e200, "tiny": y * 1e-200})
    _, v = _run(rel, fields=["x", "y", "big", "tiny"])
    # x + 1e9 rounds the data itself; compare with the stored doubles.
    stored = float(np.corrcoef((x + 1e9) - 1e9, (y + 1e9) - 1e9)[0, 1])
    assert v.coefficient[0][1] == pytest.approx(stored, rel=1e-12)
    assert v.coefficient[2][3] == pytest.approx(expected, rel=1e-12)
    assert v.coefficient[0][2] == pytest.approx(float(np.corrcoef(x + 1e9, x)[0, 1]), rel=1e-9)


@pytest.mark.parametrize("method", ["spearman", "kendall"])
def test_rank_methods_match_scipy(table_relation: Any, frame: pd.DataFrame, method: str) -> None:
    rel = table_relation(pa.Table.from_pandas(frame, preserve_index=False))
    result, v = _run(rel, fields=["a", "b", "c"], method=method)
    clean = _clean(frame)
    for i, j in [(0, 1), (0, 2), (1, 2)]:
        pair = clean.iloc[:, [i, j]].dropna()
        ref = (sps.spearmanr if method == "spearman" else sps.kendalltau)(
            pair.iloc[:, 0], pair.iloc[:, 1]
        )
        assert v.coefficient[i][j] == pytest.approx(ref.statistic, rel=1e-12)
        assert v.p_value[i][j] == pytest.approx(ref.pvalue, rel=1e-9)
        low, high = fisher_interval(ref.statistic, len(pair), method, 0.95)  # type: ignore[arg-type]
        assert (v.ci_low[i][j], v.ci_high[i][j]) == pytest.approx((low, high), rel=1e-12)
    assert not result.sampled and result.n == 300
    assert result.method.startswith("Spearman" if method == "spearman" else "Kendall")


def test_rank_correlation_samples_large_inputs(
    table_relation: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(corr_module, "RANK_BUDGET", 600)
    monkeypatch.setattr(corr_module, "RANK_MIN_ROWS", 200)
    rng = np.random.default_rng(1)
    x = rng.normal(0, 1, 2000)
    rel = table_relation({"x": x, "y": x + rng.normal(0, 1, 2000), "z": rng.normal(0, 1, 2000)})
    result, v = _run(rel, fields=["x", "y", "z"], method="spearman")
    assert result.sampled and result.n == 200 and result.n_total == 2000
    assert v.n[0][1] == 200
    assert any("random sample of 200 of 2,000" in w for w in result.warnings)


def test_wide_pearson_samples_above_the_budget(
    table_relation: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(corr_module, "PEARSON_BUDGET", 3 * 1500)
    rng = np.random.default_rng(8)
    x = rng.normal(0, 1, 6000)
    y = 0.7 * x + rng.normal(0, 0.7, 6000)
    rel = table_relation({"x": x, "y": y, "z": rng.normal(0, 1, 6000)})
    result, v = _run(rel, fields=["x", "y", "z"])
    assert result.sampled and result.n_total == 6000
    assert 1200 < result.n < 1800 and v.n[0][1] == result.n == v.n[0][0]
    assert v.coefficient[0][1] == pytest.approx(np.corrcoef(x, y)[0, 1], abs=0.05)
    assert result.method == "Pearson correlation, pairwise deletion"
    assert any("random sample" in w for w in result.warnings)


@pytest.mark.parametrize("method", ["spearman", "kendall"])
def test_rank_intervals_cover(method: str) -> None:
    """Bonett-Wright / Fieller intervals cover the population value about 95% of the time."""
    rng = np.random.default_rng(2024)
    rho = 0.5
    truth = (
        6 / math.pi * math.asin(rho / 2) if method == "spearman" else 2 / math.pi * math.asin(rho)
    )
    covered = 0
    reps = 400
    for _ in range(reps):
        x = rng.normal(size=80)
        y = rho * x + math.sqrt(1 - rho**2) * rng.normal(size=80)
        r = (sps.spearmanr if method == "spearman" else sps.kendalltau)(x, y).statistic
        low, high = fisher_interval(float(r), 80, method, 0.95)  # type: ignore[arg-type]
        assert low is not None and high is not None
        covered += low <= truth <= high
    assert 0.92 <= covered / reps <= 0.98


def test_fisher_interval_edges() -> None:
    assert fisher_interval(0.5, 3, "pearson", 0.95) == (None, None)
    assert fisher_interval(1.0, 10, "pearson", 0.95) == (1.0, 1.0)
    assert fisher_interval(0.5, 4, "kendall", 0.95) == (None, None)


def test_booleans_and_dates_are_numeric(table_relation: Any) -> None:
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(6)]
    rel = table_relation(
        {"d": days, "flag": [False, False, True, False, True, True], "y": [1.0, 2, 2, 3, 5, 6]}
    )
    result, v = _run(rel, fields=["d", "flag", "y"])
    assert v.coefficient[0][2] == pytest.approx(np.corrcoef(range(6), [1, 2, 2, 3, 5, 6])[0, 1])
    assert v.coefficient[1][2] == pytest.approx(
        np.corrcoef([0, 0, 1, 0, 1, 1], [1, 2, 2, 3, 5, 6])[0, 1]
    )
    assert "'d' is measured in days since 1970-01-01." in result.warnings


def test_degenerate_inputs(table_relation: Any) -> None:
    rel = table_relation(
        {
            "x": [1.0, 2.0, 3.0, 4.0],
            "const": [2.0, 2.0, 2.0, 2.0],
            "two": pa.array([1.0, 5.0, None, None]),
            "cat": ["a", "b", "c", "d"],
        }
    )
    result, v = _run(rel, fields=["x", "const", "two"])
    assert v.coefficient[0][1] is None and v.coefficient[1][1] is None
    assert v.coefficient[0][2] == pytest.approx(1.0) and v.p_value[0][2] is None
    assert v.ci_low[0][2] is None
    assert any("No variation" in w and "x & const" in w for w in result.warnings)
    assert any("Too few pairs" in w and "x & two" in w for w in result.warnings)
    with pytest.raises(StatsInputError, match="must be a numeric, boolean or date field"):
        _run(rel, fields=["x", "cat"])
    with pytest.raises(StatsInputError, match="at least 2 different fields"):
        _run(rel, fields=["x", "x"])
