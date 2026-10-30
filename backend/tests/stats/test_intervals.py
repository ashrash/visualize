from __future__ import annotations

import math
from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from scipy import stats as sps

from viz.contracts import SourceRef
from viz.stats import intervals as intervals_module
from viz.stats.bootstrap import bootstrap_ci, quantile
from viz.stats.errors import StatsInputError
from viz.stats.intervals import confidence_intervals
from viz.stats.models import ConfidenceIntervalRequest, ConfidenceIntervalValues

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
    assert result.method == "t confidence intervals for the mean (exact)" and not result.sampled


def test_grouped_t_intervals(table_relation: Any, plant_growth: pa.Table) -> None:
    _, v = _run(table_relation(plant_growth), fields=["weight"], group_by="group", ci_level=0.9)
    assert [i.group for i in v.intervals] == [["ctrl"], ["trt1"], ["trt2"]]
    for k, interval in enumerate(v.intervals):
        x = np.array(plant_growth["weight"][k * 10 : (k + 1) * 10])
        low, high = sps.t.interval(0.9, 9, loc=x.mean(), scale=sps.sem(x))
        assert (interval.ci_low, interval.ci_high) == pytest.approx((low, high), rel=1e-12)


@pytest.mark.parametrize(("statistic", "q"), [("median", None), ("std", None), ("quantile", 0.9)])
def test_bootstrap_on_all_values(table_relation: Any, statistic: str, q: float | None) -> None:
    rng = np.random.default_rng(4)
    x = rng.lognormal(0, 0.7, 300)
    result, v = _run(
        table_relation({"x": x}), fields=["x"], statistic=statistic, quantile=q, n_resamples=1000
    )
    interval = v.intervals[0]
    named = quantile(q) if statistic == "quantile" else statistic
    ref = bootstrap_ci(x, named, n_resamples=1000, seed=42)
    exact = {
        "median": np.median(x),
        "std": x.std(ddof=1),
        "quantile": np.quantile(x, q or 0.5),
    }[statistic]
    assert interval.estimate == pytest.approx(exact, rel=1e-12)
    assert (interval.ci_low, interval.ci_high) == pytest.approx((ref.low, ref.high), rel=1e-12)
    assert interval.method == "bca bootstrap" and not interval.sampled
    assert result.method.startswith("Bootstrap confidence intervals")


def test_bootstrap_of_large_groups_is_rescaled(
    table_relation: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(intervals_module, "BOOTSTRAP_CAP", 800)
    rng = np.random.default_rng(9)
    n = 20_000
    x = rng.normal(10, 2, n)
    result, v = _run(table_relation({"x": x}), fields=["x"], statistic="median")
    interval = v.intervals[0]
    assert interval.sampled and result.sampled
    assert interval.estimate == pytest.approx(np.median(x), rel=1e-12)
    assert interval.ci_low is not None and interval.ci_high is not None
    # Asymptotic SE of a normal median: sqrt(pi / 2) * sigma / sqrt(n).
    width = 2 * 1.959964 * math.sqrt(math.pi / 2) * 2 / math.sqrt(n)
    assert interval.ci_high - interval.ci_low == pytest.approx(width, rel=0.25)
    assert interval.ci_low < np.median(x) < interval.ci_high
    assert any("m-out-of-n" in w for w in result.warnings)
    _, mean = _run(table_relation({"x": x}), fields=["x"], method="bootstrap")
    t_width = 2 * 1.959964 * x.std(ddof=1) / math.sqrt(n)
    m = mean.intervals[0]
    assert m.ci_high - m.ci_low == pytest.approx(t_width, rel=0.15)  # type: ignore[operator]


def test_degenerate_inputs(table_relation: Any) -> None:
    rel = table_relation({"x": [1.0, 2.0, 3.0, 9.0], "g": ["a", "a", "a", "b"]})
    with pytest.raises(StatsInputError, match="t interval is for the mean"):
        _run(rel, fields=["x"], statistic="median", method="t")
    with pytest.raises(StatsInputError, match="Choose the quantile"):
        _run(rel, fields=["x"], statistic="quantile")
    result, v = _run(rel, fields=["x"], group_by="g")
    assert v.intervals[1].ci_low is None and v.intervals[1].n == 1
    assert any("'x' (b): an interval needs at least 2 values." in w for w in result.warnings)
    _, constant = _run(table_relation({"x": [4.0] * 5}), fields=["x"], statistic="median")
    assert constant.intervals[0].ci_low == constant.intervals[0].ci_high == 4.0
