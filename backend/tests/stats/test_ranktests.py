from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from scipy import stats as sps

from viz.contracts import SourceRef
from viz.stats import ranktests
from viz.stats.errors import StatsInputError
from viz.stats.models import RankTestRequest, RankTestValues
from viz.stats.ranktests import rank_test

SOURCE = SourceRef(kind="dataset", id="d")


def _run(rel: Any, **kwargs: Any) -> tuple[Any, RankTestValues]:
    result = rank_test(rel, RankTestRequest(source=SOURCE, **kwargs))
    return result, RankTestValues.model_validate(result.values)


def test_mann_whitney_sleep_matches_r(table_relation: Any, sleep_long: pa.Table) -> None:
    rel = table_relation(sleep_long)
    result, v = _run(rel, field="extra", group_field="group", groups=["1", "2"])
    a, b = np.array(sleep_long["extra"][:10]), np.array(sleep_long["extra"][10:])
    ref = sps.mannwhitneyu(a, b)
    # R: wilcox.test(extra ~ group, data = sleep) gives W = 25.5, p-value = 0.06933.
    assert v.statistic == 25.5 == ref.statistic
    assert v.p_value == pytest.approx(0.06933, abs=5e-6)
    assert v.p_value == pytest.approx(ref.pvalue, rel=1e-12)
    assert v.effect_size == pytest.approx(2 * 25.5 / 100 - 1)
    ranks = sps.rankdata(np.concatenate([a, b]))
    assert [s.mean_rank for s in v.samples] == pytest.approx([ranks[:10].mean(), ranks[10:].mean()])
    assert [s.median for s in v.samples] == pytest.approx([np.median(a), np.median(b)])
    assert result.method == "Mann-Whitney U test" and not result.sampled


@pytest.mark.parametrize("alternative", ["less", "greater"])
def test_mann_whitney_one_sided(table_relation: Any, alternative: str) -> None:
    rng = np.random.default_rng(4)
    a, b = rng.normal(0, 1, 60), rng.normal(0.5, 1, 45)
    rel = table_relation({"y": np.concatenate([a, b]), "g": ["a"] * 60 + ["b"] * 45})
    _, v = _run(rel, field="y", group_field="g", groups=["a", "b"], alternative=alternative)
    assert v.p_value == pytest.approx(sps.mannwhitneyu(a, b, alternative=alternative).pvalue)


def test_wilcoxon_paired_sleep(table_relation: Any, sleep_wide: pa.Table) -> None:
    result, v = _run(
        table_relation(sleep_wide), kind="wilcoxon", field="drug1", paired_field="drug2"
    )
    d = np.array(sleep_wide["drug1"]) - np.array(sleep_wide["drug2"])
    nonzero = d[d != 0]
    # R: wilcox.test(drug1, drug2, paired = TRUE) gives V = 0 (every difference is negative).
    assert v.statistic == 0.0
    assert v.p_value == pytest.approx(sps.wilcoxon(nonzero).pvalue, rel=1e-12)
    assert v.effect_size == pytest.approx(-1.0)
    assert any("1 zero differences" in w for w in result.warnings)
    assert result.n == 10


def test_wilcoxon_one_sample_vs_mu(table_relation: Any) -> None:
    rng = np.random.default_rng(8)
    x = rng.exponential(2.0, 40)
    _, v = _run(table_relation({"x": x}), kind="wilcoxon", field="x", mu=1.5)
    ref = sps.wilcoxon(x - 1.5)
    ranks = sps.rankdata(np.abs(x - 1.5))
    assert v.statistic == pytest.approx(ranks[x > 1.5].sum())
    assert v.p_value == pytest.approx(ref.pvalue, rel=1e-12)


def test_kruskal_plant_growth_matches_r(table_relation: Any, plant_growth: pa.Table) -> None:
    result, v = _run(
        table_relation(plant_growth), kind="kruskal", field="weight", group_field="group"
    )
    groups = [np.array(plant_growth["weight"][i * 10 : (i + 1) * 10]) for i in range(3)]
    ref = sps.kruskal(*groups)
    # R: kruskal.test(weight ~ group, data = PlantGrowth): chi-squared = 7.9882, p = 0.01842.
    assert v.statistic == pytest.approx(7.9882, abs=5e-5)
    assert v.statistic == pytest.approx(ref.statistic, rel=1e-12)
    assert v.p_value == pytest.approx(0.01842, abs=5e-6)
    assert v.df == 2 and v.effect_size == pytest.approx(ref.statistic / 29)
    assert [s.group for s in v.samples] == [["ctrl"], ["trt1"], ["trt2"]]
    subset = _run(
        table_relation(plant_growth),
        kind="kruskal",
        field="weight",
        group_field="group",
        groups=["trt2", "ctrl"],
    )[1]
    assert subset.statistic == pytest.approx(sps.kruskal(groups[2], groups[0]).statistic)
    assert [s.group for s in subset.samples] == [["trt2"], ["ctrl"]]
    assert result.n == 30


def test_large_inputs_are_sampled(table_relation: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ranktests, "MODEL_CAP", 300)
    rng = np.random.default_rng(1)
    rel = table_relation({"y": rng.normal(0, 1, 2000), "g": ["a", "b"] * 1000})
    result, v = _run(rel, field="y", group_field="g", groups=["a", "b"])
    assert result.sampled and result.n == 300 and result.n_total == 2000
    assert [s.n for s in v.samples] == [150, 150]


def test_degenerate_inputs(table_relation: Any) -> None:
    rel = table_relation({"y": [1.0, 1.0, 1.0, 1.0], "g": ["a", "a", "b", "b"], "z": [2.0] * 4})
    with pytest.raises(StatsInputError, match="All values are equal"):
        _run(rel, kind="kruskal", field="y", group_field="g")
    with pytest.raises(StatsInputError, match="No rows with g = c"):
        _run(rel, field="y", group_field="g", groups=["a", "c"])
    with pytest.raises(StatsInputError, match="two-sided only"):
        _run(rel, kind="kruskal", field="y", group_field="g", alternative="less")
    with pytest.raises(StatsInputError, match="Every difference is zero"):
        _run(rel, kind="wilcoxon", field="y", mu=1.0)
    with pytest.raises(StatsInputError, match="grouping field and two groups"):
        _run(rel, field="y")
    with pytest.raises(StatsInputError, match="at least 2 groups"):
        _run(
            table_relation({"y": [1.0, 2.0], "g": ["a", "a"]}),
            kind="kruskal",
            field="y",
            group_field="g",
        )
