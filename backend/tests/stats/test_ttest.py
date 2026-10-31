from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from scipy import stats as sps

from viz.contracts import SourceRef, TTestRequest, TTestValues
from viz.stats.errors import StatsInputError
from viz.stats.ttest import t_test

SOURCE = SourceRef(kind="dataset", id="d")


def _run(rel: Any, **kwargs: Any) -> tuple[Any, TTestValues]:
    result = t_test(rel, TTestRequest(source=SOURCE, **kwargs))
    return result, TTestValues.model_validate(result.values)


def test_sleep_welch_matches_r_and_scipy(table_relation: Any, sleep_long: pa.Table) -> None:
    rel = table_relation(sleep_long)
    result, v = _run(rel, kind="welch", field="extra", group_field="group", groups=["1", "2"])
    a, b = np.array(sleep_long["extra"][:10]), np.array(sleep_long["extra"][10:])
    ref = sps.ttest_ind(a, b, equal_var=False)
    assert v.statistic == pytest.approx(ref.statistic, rel=1e-12)
    assert v.df == pytest.approx(ref.df, rel=1e-12)
    assert v.p_value == pytest.approx(ref.pvalue, rel=1e-10)
    ci = ref.confidence_interval(0.95)
    assert (v.ci_low, v.ci_high) == pytest.approx((ci.low, ci.high), rel=1e-10)
    # R: t.test(extra ~ group, data = sleep)
    assert v.statistic == pytest.approx(-1.8608, abs=5e-5)
    assert v.df == pytest.approx(17.776, abs=5e-4)
    assert v.p_value == pytest.approx(0.07939, abs=5e-6)
    assert (v.ci_low, v.ci_high) == pytest.approx((-3.3654832, 0.2054832), abs=1e-7)
    assert v.estimate == pytest.approx(0.75 - 2.33, rel=1e-12)
    assert [s.mean for s in v.samples] == pytest.approx([0.75, 2.33], rel=1e-12)
    assert [s.n for s in v.samples] == [10, 10]
    assert result.n == 20 and result.method == "Welch two-sample t-test (exact)"
    assert not result.sampled
    # Cohen's d with the pooled SD.
    pooled = np.sqrt((9 * a.var(ddof=1) + 9 * b.var(ddof=1)) / 18)
    assert v.cohens_d == pytest.approx((a.mean() - b.mean()) / pooled, rel=1e-10)


def test_sleep_student_and_levene(table_relation: Any, sleep_long: pa.Table) -> None:
    rel = table_relation(sleep_long)
    _, v = _run(rel, kind="student", field="extra", group_field="group", groups=["1", "2"])
    a, b = np.array(sleep_long["extra"][:10]), np.array(sleep_long["extra"][10:])
    ref = sps.ttest_ind(a, b, equal_var=True)
    assert v.statistic == pytest.approx(ref.statistic, rel=1e-12)
    assert v.df == 18
    assert v.p_value == pytest.approx(ref.pvalue, rel=1e-10)  # R: 0.07919
    assert v.p_value == pytest.approx(0.07919, abs=5e-6)
    ci = ref.confidence_interval(0.95)
    assert (v.ci_low, v.ci_high) == pytest.approx((ci.low, ci.high), rel=1e-10)
    levene = next(c for c in v.checks if c.name.startswith("Levene"))
    bf = sps.levene(a, b, center="median")
    assert levene.statistic == pytest.approx(bf.statistic, rel=1e-10)
    assert levene.p_value == pytest.approx(bf.pvalue, rel=1e-10)


def test_sleep_paired_matches_r(table_relation: Any, sleep_wide: pa.Table) -> None:
    rel = table_relation(sleep_wide)
    result, v = _run(rel, kind="paired", field="drug1", paired_field="drug2")
    ref = sps.ttest_rel(np.array(sleep_wide["drug1"]), np.array(sleep_wide["drug2"]))
    assert v.statistic == pytest.approx(ref.statistic, rel=1e-12)
    assert v.p_value == pytest.approx(ref.pvalue, rel=1e-10)
    # R: t.test(sleep$extra[1:10], sleep$extra[11:20], paired = TRUE)
    assert v.statistic == pytest.approx(-4.0621, abs=5e-5)
    assert v.df == 9 and v.p_value == pytest.approx(0.002833, abs=5e-7)
    assert (v.ci_low, v.ci_high) == pytest.approx((-2.4598858, -0.7001142), abs=1e-7)
    assert v.estimate == pytest.approx(-1.58, rel=1e-12)
    assert [s.label for s in v.samples] == ["drug1", "drug2", "difference"]
    assert [s.mean for s in v.samples] == pytest.approx([0.75, 2.33, -1.58], rel=1e-12)
    assert result.method == "Paired t-test (exact)"


@pytest.mark.parametrize("alternative", ["two_sided", "less", "greater"])
def test_one_sample_alternatives_match_scipy(table_relation: Any, alternative: str) -> None:
    rng = np.random.default_rng(7)
    x = rng.normal(10.3, 2.0, 57)
    rel = table_relation({"x": x})
    _, v = _run(rel, kind="one_sample", field="x", mu=10.0, alternative=alternative, ci_level=0.9)
    scipy_alt = alternative.replace("_", "-")
    ref = sps.ttest_1samp(x, 10.0, alternative=scipy_alt)
    assert v.statistic == pytest.approx(ref.statistic, rel=1e-12)
    assert v.p_value == pytest.approx(ref.pvalue, rel=1e-10)
    ci = ref.confidence_interval(0.9)
    low = None if np.isinf(ci.low) else ci.low
    high = None if np.isinf(ci.high) else ci.high
    assert v.ci_low == (pytest.approx(low, rel=1e-10) if low is not None else None)
    assert v.ci_high == (pytest.approx(high, rel=1e-10) if high is not None else None)
    assert v.cohens_d == pytest.approx((x.mean() - 10.0) / x.std(ddof=1), rel=1e-10)


@pytest.mark.parametrize("alternative", ["less", "greater"])
def test_two_sample_one_sided(table_relation: Any, alternative: str) -> None:
    rng = np.random.default_rng(3)
    a, b = rng.normal(0, 1, 40), rng.normal(0.4, 2, 25)
    rel = table_relation({"y": np.concatenate([a, b]), "g": ["a"] * 40 + ["b"] * 25})
    _, v = _run(rel, field="y", group_field="g", groups=["a", "b"], alternative=alternative)
    ref = sps.ttest_ind(a, b, equal_var=False, alternative=alternative)
    assert v.p_value == pytest.approx(ref.pvalue, rel=1e-10)


def test_large_offset_keeps_precision(table_relation: Any) -> None:
    rng = np.random.default_rng(11)
    base = rng.normal(0, 1, 2000)
    shift = 0.05
    values = 1e9 + np.concatenate([base[:1000], base[1000:] + shift])
    rel = table_relation({"y": values, "g": ["a"] * 1000 + ["b"] * 1000})
    _, v = _run(rel, field="y", group_field="g", groups=["a", "b"])
    # The reference runs on the exactly representable centred values.
    centred = values - 1e9
    ref = sps.ttest_ind(centred[:1000], centred[1000:], equal_var=False)
    assert v.statistic == pytest.approx(ref.statistic, rel=1e-9)
    assert v.p_value == pytest.approx(ref.pvalue, rel=1e-8)


def test_missing_and_non_finite_values_are_dropped(table_relation: Any) -> None:
    x = [1.0, 2.0, None, float("nan"), float("inf"), 4.0, 7.0]
    rel = table_relation({"x": pa.array(x, type=pa.float64())})
    result, v = _run(rel, kind="one_sample", field="x")
    ref = sps.ttest_1samp([1.0, 2.0, 4.0, 7.0], 0.0)
    assert v.statistic == pytest.approx(ref.statistic, rel=1e-12)
    assert result.n == 4 and result.n_missing == 3


def test_group_values_match_like_filters(table_relation: Any) -> None:
    days = [dt.date(2024, 1, 1)] * 4 + [dt.date(2024, 1, 2)] * 4 + [dt.date(2024, 1, 3)] * 2
    y = [1.0, 2.0, 3.0, 4.0, 2.0, 4.0, 6.0, 9.0, 100.0, 200.0]
    rel = table_relation({"y": y, "day": days, "flag": [True, False] * 5})
    _, v = _run(rel, field="y", group_field="day", groups=["2024-01-01", "2024-01-02"])
    ref = sps.ttest_ind(y[:4], y[4:8], equal_var=False)
    assert v.statistic == pytest.approx(ref.statistic, rel=1e-12)
    assert [s.group for s in v.samples] == [["2024-01-01"], ["2024-01-02"]]
    _, booleans = _run(rel, field="y", group_field="flag", groups=[True, False])
    assert [s.n for s in booleans.samples] == [5, 5]


def test_integer_groups(table_relation: Any, sleep_long: pa.Table) -> None:
    _, v = _run(table_relation(sleep_long), field="extra", group_field="drug", groups=[1, 2])
    assert v.statistic == pytest.approx(-1.860813, abs=1e-6)


def test_normality_warning_for_small_skewed_samples(table_relation: Any) -> None:
    x = [1.0, 1.1, 1.2, 1.0, 1.3, 1.1, 9.0, 12.0, 1.05, 1.15, 1.2, 25.0]
    result, v = _run(table_relation({"x": x}), kind="one_sample", field="x")
    check = v.checks[0]
    assert check.name == "Shapiro-Wilk normality" and check.passed is False
    assert check.p_value == pytest.approx(sps.shapiro(x).pvalue, rel=1e-10)
    assert any("Wilcoxon signed-rank" in w for w in result.warnings)


def test_large_samples_skip_shapiro_without_warning(table_relation: Any) -> None:
    rng = np.random.default_rng(5)
    x = rng.exponential(1.0, 6000)
    result, v = _run(table_relation({"x": x}), kind="one_sample", field="x", mu=1.0)
    assert v.checks[0].passed is None and "Not run" in (v.checks[0].note or "")
    assert not any("normally" in w for w in result.warnings)


def test_unequal_variances_warn_for_student(table_relation: Any) -> None:
    rng = np.random.default_rng(9)
    a, b = rng.normal(0, 1, 200), rng.normal(0, 5, 200)
    rel = table_relation({"y": np.concatenate([a, b]), "g": ["a"] * 200 + ["b"] * 200})
    result, _ = _run(rel, kind="student", field="y", group_field="g", groups=["a", "b"])
    assert any("Welch" in w for w in result.warnings)


def test_degenerate_inputs(table_relation: Any) -> None:
    rel = table_relation({"x": [5.0, 5.0, 5.0], "g": ["a", "a", "b"], "y": [1.0, 2.0, 3.0]})
    result, v = _run(rel, kind="one_sample", field="x", mu=1.0)
    assert v.statistic is None and v.p_value is None and v.cohens_d is None
    assert "All values are equal" in result.warnings[0]
    with pytest.raises(StatsInputError, match="at least 2 values; there is 1"):
        _run(table_relation({"x": [1.0]}), kind="one_sample", field="x")
    with pytest.raises(StatsInputError, match="at least 2 values in each group"):
        _run(rel, field="y", group_field="g", groups=["a", "b"])
    with pytest.raises(StatsInputError, match="No rows with g = zzz"):
        _run(rel, field="y", group_field="g", groups=["a", "zzz"])
    with pytest.raises(StatsInputError, match="two different groups"):
        _run(rel, field="y", group_field="g", groups=["a", "a"])
    with pytest.raises(StatsInputError, match="needs a grouping field"):
        _run(rel, field="y")
    with pytest.raises(StatsInputError, match="must be a numeric or boolean field"):
        _run(rel, kind="one_sample", field="g")
    with pytest.raises(StatsInputError, match="Unknown field 'nope'"):
        _run(rel, kind="one_sample", field="nope")
    with pytest.raises(StatsInputError, match="second field"):
        _run(rel, kind="paired", field="x")
    # Student's t works with a single value in one group (pooled variance).
    _, student = _run(rel, kind="student", field="y", group_field="g", groups=["a", "b"])
    assert student.df == 1
    assert student.statistic == pytest.approx(
        sps.ttest_ind([1.0, 2.0], [3.0], equal_var=True).statistic, rel=1e-12
    )
