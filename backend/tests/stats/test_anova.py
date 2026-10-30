from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
import statsmodels.api as sm
import statsmodels.formula.api as smf
from scipy import stats as sps

from viz.contracts import SourceRef
from viz.stats.anova import anova
from viz.stats.errors import StatsInputError
from viz.stats.models import AnovaRequest, AnovaValues

SOURCE = SourceRef(kind="dataset", id="d")


def _run(rel: Any, **kwargs: Any) -> tuple[Any, AnovaValues]:
    result = anova(rel, AnovaRequest(source=SOURCE, **kwargs))
    return result, AnovaValues.model_validate(result.values)


def test_plant_growth_one_way(table_relation: Any, plant_growth: pa.Table) -> None:
    result, v = _run(table_relation(plant_growth), field="weight", factors=["group"])
    groups = [np.array(plant_growth["weight"][i * 10 : (i + 1) * 10]) for i in range(3)]
    ref = sps.f_oneway(*groups)
    effect, residual = v.table
    assert effect.term == "group" and residual.term == "Residuals"
    assert effect.statistic == pytest.approx(ref.statistic, rel=1e-10)
    assert effect.p_value == pytest.approx(ref.pvalue, rel=1e-9)
    # R: summary(aov(weight ~ group, data = PlantGrowth))
    assert (effect.df, residual.df) == (2, 27)
    assert effect.sum_sq == pytest.approx(3.76634, abs=5e-6)
    assert residual.sum_sq == pytest.approx(10.49209, abs=5e-6)
    assert effect.statistic == pytest.approx(4.846, abs=5e-4)
    assert effect.p_value == pytest.approx(0.01591, abs=5e-6)
    assert v.eta_sq == pytest.approx(3.76634 / (3.76634 + 10.49209), rel=1e-5)
    assert result.method == "One-way ANOVA (exact)" and result.n == 30
    assert [c.group for c in v.cells] == [["ctrl"], ["trt1"], ["trt2"]]
    assert [c.mean for c in v.cells] == pytest.approx([5.032, 4.661, 5.526], rel=1e-12)

    # R: oneway.test(weight ~ group, data = PlantGrowth)
    assert v.welch is not None
    assert v.welch.statistic == pytest.approx(5.181, abs=5e-4)
    assert v.welch.df2 == pytest.approx(17.128, abs=5e-4)
    assert v.welch.p_value == pytest.approx(0.01739, abs=5e-6)

    levene = next(c for c in v.checks if c.name.startswith("Levene"))
    bf = sps.levene(*groups, center="median")
    assert levene.statistic == pytest.approx(bf.statistic, rel=1e-10)
    assert levene.p_value == pytest.approx(bf.pvalue, rel=1e-10)
    normal = next(c for c in v.checks if c.name.startswith("Shapiro"))
    residuals = np.concatenate([g - g.mean() for g in groups])
    assert normal.p_value == pytest.approx(sps.shapiro(residuals).pvalue, rel=1e-6)


def test_plant_growth_tukey_matches_r_and_scipy(
    table_relation: Any, plant_growth: pa.Table
) -> None:
    _, v = _run(table_relation(plant_growth), field="weight", factors=["group"])
    assert v.post_hoc is not None
    pairs = {(c.a, c.b): c for c in v.post_hoc}
    # R: TukeyHSD(aov(weight ~ group, data = PlantGrowth))
    expected = {
        ("ctrl", "trt1"): (-0.371, -1.0622161, 0.3202161, 0.3908711),
        ("ctrl", "trt2"): (0.494, -0.1972161, 1.1852161, 0.1979960),
        ("trt1", "trt2"): (0.865, 0.1737839, 1.5562161, 0.0120064),
    }
    for key, (diff, low, high, p) in expected.items():
        row = pairs[key]
        assert row.diff == pytest.approx(diff, abs=1e-9)
        assert row.ci_low == pytest.approx(low, abs=1e-6)
        assert row.ci_high == pytest.approx(high, abs=1e-6)
        assert row.p_value == pytest.approx(p, abs=1e-6)
    groups = [np.array(plant_growth["weight"][i * 10 : (i + 1) * 10]) for i in range(3)]
    ref = sps.tukey_hsd(*groups)
    ci = ref.confidence_interval(0.95)
    assert pairs[("ctrl", "trt2")].p_value == pytest.approx(ref.pvalue[0, 2], rel=1e-6)
    # SciPy's statistic[i, j] is mean_i - mean_j; ours is mean_b - mean_a.
    assert pairs[("ctrl", "trt2")].ci_low == pytest.approx(-ci.high[0, 2], rel=1e-6)


def test_two_groups_equal_t_tests(table_relation: Any, sleep_long: pa.Table) -> None:
    _, v = _run(table_relation(sleep_long), field="extra", factors=["group"])
    a, b = np.array(sleep_long["extra"][:10]), np.array(sleep_long["extra"][10:])
    student = sps.ttest_ind(a, b)
    welch = sps.ttest_ind(a, b, equal_var=False)
    assert v.table[0].statistic == pytest.approx(student.statistic**2, rel=1e-10)
    assert v.welch is not None
    assert v.welch.statistic == pytest.approx(welch.statistic**2, rel=1e-10)
    assert v.welch.df2 == pytest.approx(welch.df, rel=1e-10)
    assert v.welch.p_value == pytest.approx(welch.pvalue, rel=1e-9)


def _unbalanced(seed: int = 1, n: int = 240) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    a = rng.choice(["a1", "a2", "a3"], n, p=[0.5, 0.3, 0.2])
    b = rng.choice(["b1", "b2"], n, p=[0.65, 0.35])
    effect = {"a1": 0.0, "a2": 0.6, "a3": -0.4}
    y = (
        np.array([effect[x] for x in a])
        + (b == "b2") * 0.5
        + ((a == "a3") & (b == "b2")) * 0.9
        + rng.normal(0, 1, n)
    )
    return pd.DataFrame({"y": y, "a": a, "b": b})


@pytest.mark.parametrize("ss_type", [1, 2, 3])
@pytest.mark.parametrize("interaction", [True, False])
def test_two_way_matches_statsmodels(table_relation: Any, ss_type: int, interaction: bool) -> None:
    frame = _unbalanced()
    rel = table_relation(pa.Table.from_pandas(frame, preserve_index=False))
    result, v = _run(rel, field="y", factors=["a", "b"], ss_type=ss_type, interaction=interaction)
    op = "*" if interaction else "+"
    formula = f"y ~ C(a, Sum) {op} C(b, Sum)" if ss_type == 3 else f"y ~ C(a) {op} C(b)"
    model = smf.ols(formula, data=frame).fit()
    ref = sm.stats.anova_lm(model, typ=ss_type)
    ref = ref.drop(index=[i for i in ref.index if i == "Intercept"])
    assert [r.term for r in v.table] == (
        ["a", "b", "a:b", "Residuals"] if interaction else ["a", "b", "Residuals"]
    )
    for ours, (_, theirs) in zip(v.table, ref.iterrows(), strict=True):
        assert ours.df == pytest.approx(theirs["df"])
        assert ours.sum_sq == pytest.approx(theirs["sum_sq"], rel=1e-9)
        if ours.term != "Residuals":
            assert ours.statistic == pytest.approx(theirs["F"], rel=1e-9)
            assert ours.p_value == pytest.approx(theirs["PR(>F)"], rel=1e-8)
    kind = {1: "I", 2: "II", 3: "III"}[ss_type]
    assert result.method == f"Two-way ANOVA, Type {kind} sums of squares (exact)"
    assert len(v.cells) == 6 and v.empty_cells == 0
    assert v.welch is None and v.post_hoc is None


def test_two_way_with_an_empty_cell(table_relation: Any) -> None:
    frame = _unbalanced(seed=4)
    frame = frame[~((frame["a"] == "a3") & (frame["b"] == "b2"))]
    rel = table_relation(pa.Table.from_pandas(frame, preserve_index=False))
    result, v = _run(rel, field="y", factors=["a", "b"], ss_type=2)
    assert v.empty_cells == 1
    interaction = v.table[2]
    assert interaction.df == 1  # (3 - 1) * (2 - 1) = 2, less the empty cell
    assert any("1 of 6 combinations" in w for w in result.warnings)
    # The interaction model is the cell-means model: residual SS = within-cell SS.
    within = sum(((g - g.mean()) ** 2).sum() for _, g in frame.groupby(["a", "b"])["y"])
    assert v.table[-1].sum_sq == pytest.approx(within, rel=1e-10)
    with pytest.raises(StatsInputError, match=r"Type III .* 1 are empty"):
        _run(rel, field="y", factors=["a", "b"], ss_type=3)


def test_large_offset_keeps_precision(table_relation: Any) -> None:
    rng = np.random.default_rng(2)
    groups = [rng.normal(mu, 0.1, 50) for mu in (0.0, 0.03, 0.06)]
    values = 1e12 + np.concatenate(groups)
    rel = table_relation({"y": values, "g": ["a"] * 50 + ["b"] * 50 + ["c"] * 50})
    _, v = _run(rel, field="y", factors=["g"])
    centred = values - 1e12  # exact: the reference sees the stored doubles
    ref = sps.f_oneway(centred[:50], centred[50:100], centred[100:])
    assert v.table[0].statistic == pytest.approx(ref.statistic, rel=1e-6)


def test_degenerate_inputs(table_relation: Any) -> None:
    rel = table_relation(
        {
            "y": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "one": ["x"] * 6,
            "g": ["a", "b", "c", "d", "e", "f"],
            "h": ["a", "a", "b", "b", "c", "c"],
            "k": ["p", "q", "p", "q", "p", "q"],
            "const": [2.0, 2.0, 5.0, 5.0, 7.0, 7.0],
        }
    )
    with pytest.raises(StatsInputError, match="'one' needs at least 2 levels"):
        _run(rel, field="y", factors=["one"])
    with pytest.raises(StatsInputError, match="more values than groups"):
        _run(rel, field="y", factors=["g"])
    with pytest.raises(StatsInputError, match="'g' has 6 levels; ANOVA supports at most 5"):
        _run(rel, field="y", factors=["g"], max_levels=5)
    with pytest.raises(StatsInputError, match="No residual degrees of freedom"):
        _run(rel, field="y", factors=["h", "k"])
    with pytest.raises(StatsInputError, match="different fields"):
        _run(rel, field="y", factors=["h", "h"])
    result, v = _run(rel, field="const", factors=["h"])
    assert v.table[0].statistic is None and v.table[0].p_value is None
    assert "Every group is constant" in result.warnings[0]
    additive = _run(rel, field="y", factors=["h", "k"], interaction=False)[1]
    assert [r.df for r in additive.table] == [2, 1, 2]


def test_missing_values_and_factors_are_dropped(table_relation: Any) -> None:
    rel = table_relation(
        {
            "y": pa.array([1.0, 2.0, 3.0, None, float("nan"), 5.0, 6.0, 9.0]),
            "g": pa.array(["a", "a", "a", "b", "b", None, "b", "b"]),
        }
    )
    result, v = _run(rel, field="y", factors=["g"])
    assert result.n == 5 and result.n_missing == 3
    ref = sps.f_oneway([1.0, 2.0, 3.0], [6.0, 9.0])
    assert v.table[0].statistic == pytest.approx(ref.statistic, rel=1e-12)


def test_small_skewed_groups_warn(table_relation: Any) -> None:
    y = [1.0, 1.1, 1.0, 1.2, 9.0, 1.1, 1.0, 14.0, 1.3, 1.2, 1.1, 20.0]
    result, _ = _run(table_relation({"y": y, "g": ["a", "b"] * 6}), field="y", factors=["g"])
    assert any("Kruskal-Wallis" in w for w in result.warnings)
