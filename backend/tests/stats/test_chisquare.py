from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from scipy import stats as sps

from viz.contracts import SourceRef
from viz.stats.chisquare import chi_square
from viz.stats.errors import StatsInputError
from viz.stats.models import ChiSquareRequest, ChiSquareValues

SOURCE = SourceRef(kind="dataset", id="d")


def _run(rel: Any, **kwargs: Any) -> tuple[Any, ChiSquareValues]:
    result = chi_square(rel, ChiSquareRequest(source=SOURCE, **kwargs))
    return result, ChiSquareValues.model_validate(result.values)


def _expand(table: list[list[int]], rows: list[str], cols: list[str]) -> pa.Table:
    r: list[str] = []
    c: list[str] = []
    for i, counts in enumerate(table):
        for j, count in enumerate(counts):
            r += [rows[i]] * count
            c += [cols[j]] * count
    return pa.table({"r": r, "c": c})


def test_agresti_party_by_gender(table_relation: Any) -> None:
    # Agresti (2007) p. 39; R: chisq.test(M) gives X-squared = 30.07, df = 2, p = 2.954e-07.
    table = [[762, 327, 468], [484, 239, 477]]
    rel = table_relation(_expand(table, ["F", "M"], ["Democrat", "Independent", "Republican"]))
    result, v = _run(rel, field="r", column_field="c")
    assert v.statistic == pytest.approx(30.07, abs=5e-3)
    assert v.df == 2 and v.p_value == pytest.approx(2.954e-07, rel=1e-3)
    ref = sps.chi2_contingency(np.array(table))
    assert v.statistic == pytest.approx(ref.statistic, rel=1e-12)
    assert v.p_value == pytest.approx(ref.pvalue, rel=1e-10)
    np.testing.assert_allclose(v.expected, ref.expected_freq, rtol=1e-12)
    assert v.observed == table
    assert v.rows == ["F", "M"] and v.columns == ["Democrat", "Independent", "Republican"]
    assert not v.yates and v.fisher_p_value is None
    # Agresti (2007) Table 2.5: standardized residuals 4.50, 0.70, -5.32 for females.
    assert v.residuals[0] == pytest.approx([4.50, 0.70, -5.32], abs=5e-3)
    assert v.residuals[1] == pytest.approx([-4.50, -0.70, 5.32], abs=5e-3)
    assert v.cramers_v == pytest.approx(
        sps.contingency.association(np.array(table), method="cramer"), rel=1e-12
    )
    assert result.n == sum(map(sum, table)) and result.method.startswith("Pearson's chi-square")


def test_two_by_two_uses_yates_and_fisher(table_relation: Any) -> None:
    # Fisher's tea-tasting experiment: two-sided p = 0.4857.
    table = [[3, 1], [1, 3]]
    rel = table_relation(_expand(table, ["milk", "tea"], ["milk", "tea"]))
    result, v = _run(rel, field="r", column_field="c")
    ref = sps.chi2_contingency(np.array(table), correction=True)
    assert v.yates and v.statistic == pytest.approx(ref.statistic, rel=1e-12)
    assert v.fisher_p_value == pytest.approx(0.4857, abs=5e-5)
    assert v.odds_ratio == pytest.approx(9.0)
    assert "Yates" in result.method
    assert any("Fisher's exact test" in w for w in result.warnings)
    uncorrected = _run(rel, field="r", column_field="c", correction=False)[1]
    assert not uncorrected.yates
    assert uncorrected.statistic == pytest.approx(
        sps.chi2_contingency(np.array(table), correction=False).statistic, rel=1e-12
    )


def test_missing_categories_are_dropped_and_types_kept(table_relation: Any) -> None:
    rel = table_relation(
        {
            "a": pa.array([1, 1, 2, 2, 2, None, 1, 2]),
            "b": pa.array([True, False, True, True, False, True, None, False]),
        },
    )
    result, v = _run(rel, field="a", column_field="b", correction=False)
    assert result.n == 6 and result.n_missing == 2
    assert v.rows == [1, 2] and v.columns == [False, True]
    assert v.observed == [[1, 1], [2, 2]]


def test_goodness_of_fit_uniform_and_given(table_relation: Any) -> None:
    faces = [1] * 8 + [2] * 12 + [3] * 9 + [4] * 11 + [5] * 6 + [6] * 14
    rel = table_relation({"face": [str(f) for f in faces]})
    result, v = _run(rel, kind="goodness_of_fit", field="face")
    ref = sps.chisquare([8, 12, 9, 11, 6, 14])
    assert v.statistic == pytest.approx(ref.statistic, rel=1e-12)
    assert v.p_value == pytest.approx(ref.pvalue, rel=1e-12)
    assert v.df == 5 and v.rows == ["1", "2", "3", "4", "5", "6"]
    assert "uniform" in result.method

    shares = [1, 1, 1, 1, 1, 3]
    expected = [{"value": str(f), "proportion": p} for f, p in enumerate(shares, start=1)]
    result, v = _run(rel, kind="goodness_of_fit", field="face", expected=expected)
    observed = np.array([8, 12, 9, 11, 6, 14])
    ref = sps.chisquare(observed, f_exp=np.array([1, 1, 1, 1, 1, 3]) / 8 * observed.sum())
    assert v.statistic == pytest.approx(ref.statistic, rel=1e-12)
    assert "rescaled" in result.warnings[0]

    partial = [{"value": "1", "proportion": 0.5}, {"value": "2", "proportion": 0.5}]
    with pytest.raises(StatsInputError, match="40 rows have a value of 'face'"):
        _run(rel, kind="goodness_of_fit", field="face", expected=partial)


def test_goodness_of_fit_counts_categories_without_rows(table_relation: Any) -> None:
    rel = table_relation({"x": ["a", "a", "b", "a"]})
    expected = [
        {"value": "a", "proportion": 0.5},
        {"value": "b", "proportion": 0.25},
        {"value": "c", "proportion": 0.25},
    ]
    _, v = _run(rel, kind="goodness_of_fit", field="x", expected=expected)
    assert v.observed == [[3], [1], [0]]
    assert v.statistic == pytest.approx(sps.chisquare([3, 1, 0], f_exp=[2, 1, 1]).statistic)


def test_degenerate_inputs(table_relation: Any) -> None:
    rel = table_relation({"x": ["a", "a", "a"], "y": ["p", "q", "p"], "z": ["1", "2", "3"]})
    with pytest.raises(StatsInputError, match="'x' needs at least 2 categories"):
        _run(rel, field="x", column_field="y")
    with pytest.raises(StatsInputError, match=r"'z' has 3 categories; .* at most 2"):
        _run(rel, field="y", column_field="z", max_levels=2)
    with pytest.raises(StatsInputError, match="two categorical fields"):
        _run(rel, field="x")
    with pytest.raises(StatsInputError, match="different fields"):
        _run(rel, field="y", column_field="y")
    with pytest.raises(StatsInputError, match="'x' needs at least 2 categories"):
        _run(rel, kind="goodness_of_fit", field="x")
    zero = [{"value": "a", "proportion": 1.0}, {"value": "b", "proportion": 0.0}]
    with pytest.raises(StatsInputError, match="greater than 0"):
        _run(rel, kind="goodness_of_fit", field="x", expected=zero)
