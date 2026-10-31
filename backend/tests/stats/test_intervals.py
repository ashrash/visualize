from __future__ import annotations

import math
from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from scipy import stats as sps

from viz.contracts import ConfidenceIntervalRequest, ConfidenceIntervalValues, SourceRef
from viz.stats import intervals as intervals_module
from viz.stats.bootstrap import bootstrap_ci, quantile
from viz.stats.errors import StatsInputError
from viz.stats.intervals import asymptotic_sd, confidence_intervals, order_ranks

SOURCE = SourceRef(kind="dataset", id="d")


def _run(rel: Any, **kwargs: Any) -> tuple[Any, ConfidenceIntervalValues]:
    result = confidence_intervals(rel, ConfidenceIntervalRequest(source=SOURCE, **kwargs))
    return result, ConfidenceIntervalValues.model_validate(result.values)


def test_t_interval_matches_r(table_relation: Any, sleep_wide: pa.Table) -> None:
    result, v = _run(table_relation(sleep_wide), fields=["drug1", "drug2"])
    one, two = v.intervals
    # R: t.test(sleep$extra[1:10])$conf.int = -0.5297804 2.0297804
    assert (one.ci_low, one.ci_high) == pytest.approx((-0.5297804, 2.0297804), abs=1e-7)
    assert one.estimate == pytest.approx(0.75) and one.n == 10 and one.method == "t"
    x = np.array(sleep_wide["drug2"])
    low, high = sps.t.interval(0.95, 9, loc=x.mean(), scale=sps.sem(x))
    assert (two.ci_low, two.ci_high) == pytest.approx((low, high), rel=1e-12)
    assert two.std_error == pytest.approx(sps.sem(x), rel=1e-12)
    assert result.method == "Confidence intervals for the mean (t)" and not result.sampled


def test_grouped_t_intervals(table_relation: Any, plant_growth: pa.Table) -> None:
    _, v = _run(table_relation(plant_growth), fields=["weight"], group_by="group", ci_level=0.9)
    assert [i.group for i in v.intervals] == [["ctrl"], ["trt1"], ["trt2"]]
    for k, interval in enumerate(v.intervals):
        x = np.array(plant_growth["weight"][k * 10 : (k + 1) * 10])
        low, high = sps.t.interval(0.9, 9, loc=x.mean(), scale=sps.sem(x))
        assert (interval.ci_low, interval.ci_high) == pytest.approx((low, high), rel=1e-12)


def test_order_ranks_known_answers() -> None:
    # Median of 10 values at 95%: (x_(2), x_(9)), coverage B(8) - B(1) = 0.978516.
    low, high, coverage = order_ranks(10, 0.5, 0.95)
    assert (low, high) == (2, 9)
    assert coverage == pytest.approx(sps.binom.cdf(8, 10, 0.5) - sps.binom.cdf(1, 10, 0.5))
    assert coverage == pytest.approx(0.978516, abs=1e-6)
    assert order_ranks(5, 0.5, 0.95)[:2] == (None, None)  # 2 * 0.5^5 = 6.25% > 5%
    assert order_ranks(30, 0.95, 0.95)[1] is None  # no rank above the 0.95 quantile
    for n, p in [(57, 0.5), (1000, 0.9), (20000, 0.99)]:
        low, high, coverage = order_ranks(n, p, 0.95)
        assert low is not None and high is not None and coverage >= 0.95
        assert sps.binom.cdf(low - 1, n, p) <= 0.025 < sps.binom.cdf(low, n, p)
        assert sps.binom.cdf(high - 2, n, p) < 0.975 <= sps.binom.cdf(high - 1, n, p)


@pytest.mark.parametrize(("p", "n"), [(0.5, 2000), (0.99, 5000)])
def test_order_statistic_intervals_cover(p: float, n: int) -> None:
    """Coverage for skewed data, including the extreme quantile the m-out-of-n bootstrap missed."""
    rng = np.random.default_rng(5)
    truth = math.exp(sps.norm.ppf(p))  # lognormal(0, 1) quantile
    low, high, _ = order_ranks(n, p, 0.95)
    assert low is not None and high is not None
    hits = 0
    reps = 400
    for _ in range(reps):
        x = np.sort(rng.lognormal(0, 1, n))
        hits += x[low - 1] <= truth <= x[high - 1]
    assert 0.93 <= hits / reps <= 0.99


@pytest.mark.parametrize(("draw", "truth"), [("exponential", 1.0), ("lognormal", None)])
def test_asymptotic_sd_covers(draw: str, truth: float | None) -> None:
    rng = np.random.default_rng(12)
    if truth is None:  # lognormal(0, 0.5)
        truth = math.sqrt((math.exp(0.25) - 1) * math.exp(0.25))
    hits, reps, n = 0, 300, 20_000
    for _ in range(reps):
        x = rng.exponential(1.0, n) if draw == "exponential" else rng.lognormal(0, 0.5, n)
        result = asymptotic_sd(n, float(x.var(ddof=1)), float(sps.kurtosis(x, bias=False)), 0.95)
        assert result is not None
        hits += result[0] <= truth <= result[1]
    assert 0.92 <= hits / reps <= 0.98


@pytest.mark.parametrize("statistic", ["median", "quantile"])
def test_order_statistic_intervals_are_exact(table_relation: Any, statistic: str) -> None:
    rng = np.random.default_rng(4)
    x = rng.lognormal(0, 0.8, 12_000)
    q = 0.5 if statistic == "median" else 0.9
    extra = {"quantile": 0.9} if statistic == "quantile" else {}
    result, v = _run(table_relation({"x": x}), fields=["x"], statistic=statistic, **extra)
    interval = v.intervals[0]
    low, high, _ = order_ranks(len(x), q, 0.95)
    ordered = np.sort(x)
    assert interval.method == "order statistic" and interval.std_error is None
    assert interval.estimate == pytest.approx(np.quantile(x, q), rel=1e-12)
    assert (interval.ci_low, interval.ci_high) == (ordered[low - 1], ordered[high - 1])
    assert "order statistic" in result.method


def test_small_order_statistic_intervals_note_their_coverage(table_relation: Any) -> None:
    rel = table_relation(
        {"x": [5.0, 1.0, 9.0, 3.0, 7.0, 2.0, 8.0, 4.0, 6.0, 10.0], "y": [1.0] * 10}
    )
    result, v = _run(rel, fields=["x"], statistic="median")
    assert (v.intervals[0].ci_low, v.intervals[0].ci_high) == (2.0, 9.0)
    assert any("covers 97.9%" in w for w in result.warnings)
    short, s = _run(table_relation({"x": [1.0, 2.0, 3.0, 4.0]}), fields=["x"], statistic="median")
    assert s.intervals[0].ci_low is None and s.intervals[0].ci_high is None
    assert any("no order statistic gives the lower or upper bound" in w for w in short.warnings)


def test_large_sd_uses_the_asymptotic_interval(table_relation: Any) -> None:
    rng = np.random.default_rng(6)
    x = rng.lognormal(0, 0.5, 15_000)
    _, v = _run(table_relation({"x": x}), fields=["x"], statistic="std")
    interval = v.intervals[0]
    expected = asymptotic_sd(len(x), x.var(ddof=1), sps.kurtosis(x, bias=False), 0.95)
    assert expected is not None
    assert interval.method == "asymptotic"
    assert interval.estimate == pytest.approx(x.std(ddof=1), rel=1e-10)
    assert (interval.ci_low, interval.ci_high) == pytest.approx(expected[:2], rel=1e-8)
    heavy, _ = _run(
        table_relation({"x": rng.lognormal(0, 1.5, 15_000)}), fields=["x"], statistic="std"
    )
    assert any("heavy tails" in w for w in heavy.warnings)


@pytest.mark.parametrize(("statistic", "q"), [("median", None), ("std", None), ("quantile", 0.9)])
def test_bootstrap_on_all_values(table_relation: Any, statistic: str, q: float | None) -> None:
    rng = np.random.default_rng(4)
    x = rng.lognormal(0, 0.7, 300)
    result, v = _run(
        table_relation({"x": x}),
        fields=["x"],
        statistic=statistic,
        quantile=q,
        method="bootstrap",
        n_resamples=1000,
    )
    interval = v.intervals[0]
    named = quantile(q) if statistic == "quantile" else statistic
    ref = bootstrap_ci(x, named, n_resamples=1000, method="bca", seed=42)
    exact = {"median": np.median(x), "std": x.std(ddof=1), "quantile": np.quantile(x, q or 0.5)}
    assert interval.estimate == pytest.approx(exact[statistic], rel=1e-12)
    assert (interval.ci_low, interval.ci_high) == pytest.approx((ref.low, ref.high), rel=1e-12)
    assert interval.method == "bca bootstrap"
    assert "bca bootstrap" in result.method


def test_bootstrap_falls_back_above_the_size_and_work_limits(
    table_relation: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = np.random.default_rng(9)
    big = rng.normal(10, 2, 12_000)
    result, v = _run(
        table_relation({"x": big}), fields=["x"], statistic="median", method="bootstrap"
    )
    assert v.intervals[0].method == "order statistic"
    assert any("above 10,000 values the order-statistic interval" in w for w in result.warnings)

    monkeypatch.setattr(intervals_module, "BOOTSTRAP_WORK", 3_000_000)
    groups = np.repeat(["a", "b", "c"], [500, 1000, 2000])
    rel = table_relation({"x": rng.normal(0, 1, 3500), "g": groups})
    limited, lv = _run(rel, fields=["x"], statistic="std", group_by="g", n_resamples=1000)
    methods = {i.group[0] if i.group else None: i.method for i in lv.intervals}
    assert methods == {"a": "bca bootstrap", "b": "bca bootstrap", "c": "asymptotic"}
    assert any("1 group(s) got the asymptotic interval" in w for w in limited.warnings)


def test_many_groups_all_get_intervals(table_relation: Any) -> None:
    rng = np.random.default_rng(2)
    groups = [f"g{i:03d}" for i in range(150) for _ in range(20)]
    rel = table_relation({"x": rng.normal(0, 1, 3000), "g": groups})
    result, v = _run(
        rel, fields=["x"], statistic="std", group_by="g", max_groups=200, n_resamples=200
    )
    assert len(v.intervals) == 150
    assert all(i.ci_low is not None and i.ci_high is not None for i in v.intervals)
    assert not any("largest" in w for w in result.warnings)


def test_degenerate_inputs(table_relation: Any) -> None:
    rel = table_relation({"x": [1.0, 2.0, 3.0, 9.0], "g": ["a", "a", "a", "b"]})
    with pytest.raises(StatsInputError, match="t interval is for the mean"):
        _run(rel, fields=["x"], statistic="median", method="t")
    with pytest.raises(StatsInputError, match="Choose the quantile"):
        _run(rel, fields=["x"], statistic="quantile")
    result, v = _run(rel, fields=["x"], group_by="g")
    assert v.intervals[1].ci_low is None and v.intervals[1].n == 1
    assert any("'x' (b): an interval needs at least 2 values." in w for w in result.warnings)
    _, constant = _run(table_relation({"x": [4.0] * 5}), fields=["x"], statistic="std")
    assert constant.intervals[0].ci_low == constant.intervals[0].ci_high == 0.0
