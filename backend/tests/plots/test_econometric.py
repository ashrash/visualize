"""Plots.md §8 Statistical & econometric stat transforms: published known answers and
degenerate inputs."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlglot import exp

import viz.plots as plots
from viz.contracts import ChartSpec, Column, ColumnSemantic, PlotType, StatResult, Viewport
from viz.engine import Engine
from viz.errors import InvalidSpecError
from viz.plots import econometric as ec
from viz.stats.relation import Relation

_counter = itertools.count()
Rel = Callable[..., Relation]


def _semantic(arrow_type: pa.DataType) -> ColumnSemantic:
    if pa.types.is_integer(arrow_type) or pa.types.is_floating(arrow_type):
        return ColumnSemantic.QUANTITATIVE
    if pa.types.is_temporal(arrow_type):
        return ColumnSemantic.TEMPORAL
    return ColumnSemantic.NOMINAL


@pytest.fixture
def rel(engine: Engine, tmp_path: Path) -> Rel:
    def make(data: pa.Table | dict[str, Any]) -> Relation:
        table = data if isinstance(data, pa.Table) else pa.table(data)
        path = tmp_path / f"ec é {next(_counter)}.parquet"
        pq.write_table(table, path)
        columns = {
            f.name: Column(
                name=f.name,
                type=str(f.type).upper(),
                semantic=_semantic(f.type),
                null_count=0,
                approx_distinct=0,
            )
            for f in table.schema
        }
        source = exp.Anonymous(this="read_parquet", expressions=[exp.Literal.string(str(path))])
        return Relation(
            engine=engine,
            source=source,
            where=None,
            columns=columns,
            row_count=table.num_rows,
            version_key=f"e{next(_counter)}@v1",
        )

    return make


def run(relation: Relation, plot: PlotType, params: dict[str, Any] | None = None, **ch: Any) -> Any:
    spec = ChartSpec.model_validate(
        {
            "id": "c",
            "plotType": plot.value,
            "source": {"kind": "dataset", "id": "d"},
            "encodings": {
                k: [{"field": f} for f in (v if isinstance(v, list) else [v])]
                for k, v in ch.items()
            },
            "params": params or {},
        }
    )
    return plots.run_plot_stat(spec, relation, viewport=Viewport(), mark_cap=50_000)


def values_of(out: Any) -> dict[str, Any]:
    assert isinstance(out, StatResult)
    assert "nan" not in repr(out.values).lower()
    return out.values


def test_every_econometric_plot_is_registered() -> None:
    for plot in (
        PlotType.KAPLAN_MEIER,
        PlotType.FOREST,
        PlotType.ROC,
        PlotType.PRECISION_RECALL,
        PlotType.LORENZ,
        PlotType.QUANTILE,
        PlotType.MARGINAL_EFFECTS,
        PlotType.COEFFICIENT,
        PlotType.IMPULSE_RESPONSE,
        PlotType.EVENT_STUDY,
    ):
        assert plots.get_plot(plot) is not None, plot


# --- Kaplan-Meier: Freireich et al. (1963) leukemia remission data (Gehan 1965) -----------------

MP6_TIME = [6, 6, 6, 6, 7, 9, 10, 10, 11, 13, 16, 17, 19, 20, 22, 23, 25, 32, 32, 34, 35]
MP6_EVENT = [1, 1, 1, 0, 1, 0, 1, 0, 0, 1, 1, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0]
PLACEBO = [1, 1, 2, 2, 3, 4, 4, 5, 5, 8, 8, 8, 8, 11, 11, 12, 12, 15, 17, 22, 23]


def leukemia() -> dict[str, Any]:
    return {
        "weeks": [float(t) for t in MP6_TIME + PLACEBO],
        "relapse": MP6_EVENT + [1] * 21,
        "arm": ["6-MP"] * 21 + ["Placebo"] * 21,
    }


def test_kaplan_meier_matches_published_survfit(rel: Rel) -> None:
    r = rel(leukemia())
    out = run(
        r, PlotType.KAPLAN_MEIER, {"ciType": "log"}, time="weeks", event="relapse", color="arm"
    )
    values = values_of(out)
    mp = next(g for g in values["groups"] if g["key"] == "6-MP")
    # R: summary(survfit(Surv(time, cens) ~ 1, data = drug6mp)), conf.type = "log".
    assert mp["time"] == [0, 6, 7, 10, 13, 16, 22, 23]
    assert mp["survival"][1:] == pytest.approx(
        [0.857, 0.807, 0.753, 0.690, 0.627, 0.538, 0.448], abs=6e-4
    )
    assert mp["lower"][1:] == pytest.approx(
        [0.720, 0.653, 0.586, 0.510, 0.439, 0.337, 0.249], abs=1e-3
    )
    assert mp["upper"][1:] == pytest.approx(
        [1.000, 0.996, 0.968, 0.935, 0.896, 0.858, 0.807], abs=1e-3
    )
    assert mp["atRisk"] == [21, 21, 17, 15, 12, 11, 7, 6]
    assert mp["median"] == 23
    placebo = next(g for g in values["groups"] if g["key"] == "Placebo")
    assert placebo["median"] == 8
    # Log-rank: chi-square 16.79 on 1 df, p = 4.17e-5 (R survdiff).
    assert values["logRank"]["statistic"] == pytest.approx(16.7929, abs=1e-3)
    assert values["logRank"]["pValue"] == pytest.approx(4.169e-5, rel=1e-3)
    # Censoring marks sit on the curve: 6+ at S(6) = 0.857, 35+ at S(23) = 0.448.
    assert mp["censorTime"][0] == 6 and mp["censorSurvival"][0] == pytest.approx(0.8571, abs=1e-4)
    assert mp["censorSurvival"][-1] == pytest.approx(0.4482, abs=1e-4)


def test_kaplan_meier_loglog_band_is_inside_unit_interval(rel: Rel) -> None:
    values = values_of(run(rel(leukemia()), PlotType.KAPLAN_MEIER, time="weeks", event="relapse"))
    group = values["groups"][0]
    for lo, s, hi in zip(group["lower"], group["survival"], group["upper"], strict=True):
        assert 0 <= lo <= s <= hi <= 1
    # R log-log at the first step for the pooled 42 patients: S = 38/42.
    assert group["survival"][1] == pytest.approx(40 / 42)


def test_kaplan_meier_all_censored_and_bad_events(rel: Rel) -> None:
    out = run(
        rel({"t": [1.0, 2, 3], "e": [0, 0, 0], "g": ["a", "a", "a"]}),
        PlotType.KAPLAN_MEIER,
        time="t",
        event="e",
        color="g",
    )
    values = values_of(out)
    assert values["groups"][0]["survival"] == [1, 1] and values["groups"][0]["median"] is None
    assert any("no events" in w for w in out.warnings)
    assert values["logRank"] is None
    with pytest.raises(InvalidSpecError, match="exactly two values"):
        run(rel({"t": [1.0, 2, 3], "e": [0, 1, 2]}), PlotType.KAPLAN_MEIER, time="t", event="e")
    with pytest.raises(InvalidSpecError, match="negative"):
        run(rel({"t": [-1.0, 2, 3], "e": [0, 1, 1]}), PlotType.KAPLAN_MEIER, time="t", event="e")
    # Boolean events work.
    booleans = run(
        rel({"t": [1.0, 2, 3, 4], "e": [True, False, True, True]}),
        PlotType.KAPLAN_MEIER,
        time="t",
        event="e",
    )
    assert values_of(booleans)["groups"][0]["events"] == 3


def test_kaplan_meier_event_coding(rel: Rel) -> None:
    # R's Surv convention: 1 = censored, 2 = event. Deaths at 1 and 2 of 6 patients, the
    # others censored later: S stays at 4/6 after t = 2, it must not fall to 0.
    times = [1.0, 2, 3, 4, 5, 6]
    r_style = rel({"t": times, "status": [2, 2, 1, 1, 1, 1]})
    out = run(r_style, PlotType.KAPLAN_MEIER, time="t", event="status")
    group = values_of(out)["groups"][0]
    assert group["events"] == 2
    assert group["survival"] == pytest.approx([1, 5 / 6, 4 / 6])
    assert any("1/2" in w for w in out.warnings)
    # Words: "died" is the event whether it sorts first or last.
    words = rel({"t": times, "status": ["died", "died", *["survived"] * 4]})
    group = values_of(run(words, PlotType.KAPLAN_MEIER, time="t", event="status"))["groups"][0]
    assert group["events"] == 2
    alive = rel({"t": times, "status": ["dead", "dead", *["alive"] * 4]})
    assert (
        values_of(run(alive, PlotType.KAPLAN_MEIER, time="t", event="status"))["groups"][0][
            "events"
        ]
        == 2
    )
    # One recognizable "no" word: the other value is the event.
    censored = rel({"t": times, "status": ["relapse_x", "relapse_x", *["censored"] * 4]})
    assert (
        values_of(run(censored, PlotType.KAPLAN_MEIER, time="t", event="status"))["groups"][0][
            "events"
        ]
        == 2
    )
    # An explicit event value always wins (here: 1 is the event, as in 0/1-free coding).
    explicit = run(r_style, PlotType.KAPLAN_MEIER, {"eventValue": "1"}, time="t", event="status")
    assert values_of(explicit)["groups"][0]["events"] == 4
    with pytest.raises(InvalidSpecError, match="not a value"):
        run(r_style, PlotType.KAPLAN_MEIER, {"eventValue": "dead"}, time="t", event="status")


def test_km_bands_degenerate() -> None:
    lo, hi = ec.km_bands(np.array([1.0, 0.5, 0.0]), np.array([0.0, 0.1, 0.0]), 0.95, "log-log")
    assert lo[0] == hi[0] == 1 and lo[2] == hi[2] == 0 and lo[1] < 0.5 < hi[1]


# --- ROC and precision-recall ---------------------------------------------------------------


def test_roc_and_pr_match_scikit_learn_documentation(rel: Rel) -> None:
    r = rel({"y": [0, 0, 1, 1], "s": [0.1, 0.4, 0.35, 0.8]})
    roc = values_of(run(r, PlotType.ROC, truth="y", score="s"))
    assert roc["curves"][0]["auc"] == pytest.approx(0.75)
    assert roc["curves"][0]["fpr"][0] == 0 and roc["curves"][0]["tpr"][-1] == 1
    assert roc["curves"][0]["threshold"][0] is None  # sklearn's +inf first threshold
    pr = values_of(run(r, PlotType.PRECISION_RECALL, truth="y", score="s"))
    assert pr["curves"][0]["averagePrecision"] == pytest.approx(0.8333333, abs=1e-6)
    assert pr["prevalence"] == 0.5


def test_delong_variance_matches_brute_force_structural_components() -> None:
    rng = np.random.default_rng(7)
    y = (rng.random(80) < 0.4).astype(float)
    s = np.round(rng.normal(size=80) + y, 1)  # rounded: ties
    pos, neg = s[y == 1], s[y == 0]
    psi = (pos[:, None] > neg[None, :]) + 0.5 * (pos[:, None] == neg[None, :])
    v10, v01 = psi.mean(axis=1), psi.mean(axis=0)
    expected = v10.var(ddof=1) / len(pos) + v01.var(ddof=1) / len(neg)
    auc, variance = ec.delong(s, y)
    assert auc == pytest.approx(psi.mean())
    assert variance == pytest.approx(expected, rel=1e-12)


def test_roc_degenerate_inputs(rel: Rel) -> None:
    with pytest.raises(InvalidSpecError, match="both outcomes"):
        run(rel({"y": [1, 1, 1], "s": [0.1, 0.2, 0.3]}), PlotType.ROC, truth="y", score="s")
    out = run(
        rel({"y": [0, 0, 1, 1], "s": [0.1, 0.2, 0.8, 0.9]}), PlotType.ROC, truth="y", score="s"
    )
    curve = values_of(out)["curves"][0]
    assert curve["auc"] == 1 and curve["aucLow"] == curve["aucHigh"] == 1
    assert any("perfectly separated" in w for w in out.warnings)
    # A constant score: AUC 0.5 with zero SE is not "perfect separation".
    flat = run(rel({"y": [0, 0, 1, 1], "s": [0.5] * 4}), PlotType.ROC, truth="y", score="s")
    assert values_of(flat)["curves"][0]["auc"] == 0.5
    assert not any("perfectly separated" in w for w in flat.warnings)
    labels = run(
        rel({"y": ["no", "yes", "no", "yes"], "s": [0.1, 0.9, 0.3, 0.2]}),
        PlotType.ROC,
        truth="y",
        score="s",
    )
    assert values_of(labels)["positive"] == "yes"
    chosen = run(
        rel({"y": ["a", "b", "a", "b"], "s": [0.1, 0.9, 0.3, 0.2]}),
        PlotType.ROC,
        {"positive": "a"},
        truth="y",
        score="s",
    )
    assert values_of(chosen)["curves"][0]["auc"] == pytest.approx(0.25)


# --- Lorenz and quantile plot ---------------------------------------------------------------


def test_lorenz_and_gini_known_values(rel: Rel) -> None:
    values = values_of(run(rel({"x": [3.0, 1, 5, 2, 4]}), PlotType.LORENZ, y="x"))
    group = values["groups"][0]
    # G = 2 * sum(i x_(i)) / (n sum x) - (n + 1) / n = 110 / 75 - 6 / 5.
    assert group["gini"] == pytest.approx(110 / 75 - 1.2)
    assert group["x"] == pytest.approx([0, 0.2, 0.4, 0.6, 0.8, 1])
    assert group["y"] == pytest.approx([0, 1 / 15, 3 / 15, 6 / 15, 10 / 15, 1])
    equal = values_of(run(rel({"x": [2.0] * 4}), PlotType.LORENZ, y="x"))
    assert equal["groups"][0]["gini"] == pytest.approx(0)
    one = values_of(run(rel({"x": [0.0, 0, 0, 9]}), PlotType.LORENZ, y="x"))
    assert one["groups"][0]["gini"] == pytest.approx(3 / 4)


def test_lorenz_groups_negatives_and_thinning(rel: Rel) -> None:
    rng = np.random.default_rng(1)
    x = rng.lognormal(size=5000)
    out = run(
        rel({"x": np.r_[x, -1.0], "g": ["a"] * 2500 + ["b"] * 2501}),
        PlotType.LORENZ,
        {"points": 100},
        y="x",
        color="g",
    )
    values = values_of(out)
    assert [g["key"] for g in values["groups"]] == ["a", "b"]
    assert any("negative" in w for w in out.warnings)
    a = values["groups"][0]
    assert len(a["x"]) <= 102 and a["x"][-1] == 1 and a["y"][-1] == pytest.approx(1)
    s = np.sort(x[:2500])
    expected = 2 * (np.arange(1, 2501) * s).sum() / (2500 * s.sum()) - 2501 / 2500
    assert a["gini"] == pytest.approx(expected, rel=1e-10)


def test_quantile_plot_order_statistics(rel: Rel) -> None:
    values = values_of(run(rel({"v": [3.0, 1, 2, None]}), PlotType.QUANTILE, y="v"))
    group = values["groups"][0]
    assert group["f"] == pytest.approx([1 / 6, 1 / 2, 5 / 6])
    assert group["value"] == [1, 2, 3]
    assert group["median"] == 2 and values["exact"] is True
    rng = np.random.default_rng(2)
    big = rng.normal(size=5000)
    out = run(rel({"v": big}), PlotType.QUANTILE, {"maxPoints": 50}, y="v")
    group = values_of(out)["groups"][0]
    assert 45 <= len(group["f"]) <= 52
    ranks = np.round(np.array(group["f"]) * 5000 + 0.5).astype(int)
    assert group["value"] == pytest.approx(np.sort(big)[ranks - 1])
    assert group["value"][0] == pytest.approx(big.min()) and group["value"][-1] == pytest.approx(
        big.max()
    )


# --- Coefficient plot: NIST StRD Longley (certified values) ---------------------------------

LONGLEY_COEF = [
    -3482258.63459582,
    15.0618722713733,
    -0.358191792925910e-01,
    -2.02022980381683,
    -1.03322686717359,
    -0.511041056535807e-01,
    1829.15146461355,
]
LONGLEY_SE = [
    890420.383607373,
    84.9149257747669,
    0.334910077722432e-01,
    0.488399681651699,
    0.214274163161675,
    0.226073200069370,
    455.478499142212,
]


def longley(rel: Rel) -> Relation:
    import statsmodels.api as sm

    data = sm.datasets.longley.load_pandas().data
    return rel(pa.Table.from_pandas(data, preserve_index=False))


def test_coefficient_ols_matches_nist_longley(rel: Rel) -> None:
    xs = ["GNPDEFL", "GNP", "UNEMP", "ARMED", "POP", "YEAR"]
    out = run(longley(rel), PlotType.COEFFICIENT, y="TOTEMP", x=xs)
    values = values_of(out)
    terms = values["terms"]
    assert [t["term"] for t in terms] == ["Intercept", *xs]
    assert [t["estimate"] for t in terms] == pytest.approx(LONGLEY_COEF, rel=1e-7)
    assert [t["se"] for t in terms] == pytest.approx(LONGLEY_SE, rel=1e-7)
    assert values["r2"] == pytest.approx(0.995479004577296, rel=1e-9)
    assert terms[0]["intercept"] is True
    # 95% t interval on 9 df.
    t = 2.2621571627409915
    assert terms[1]["ciLow"] == pytest.approx(LONGLEY_COEF[1] - t * LONGLEY_SE[1], rel=1e-7)


def test_coefficient_cluster_and_robust_match_statsmodels(rel: Rel) -> None:
    import statsmodels.api as sm

    rng = np.random.default_rng(3)
    g = np.repeat(np.arange(40), 10)
    x = rng.normal(size=400) + rng.normal(size=40)[g]
    y = 1 + 2 * x + rng.normal(size=400) + rng.normal(size=40)[g]
    r = rel({"y": y, "x": x, "g": [f"c{i}" for i in g]})
    clustered = values_of(run(r, PlotType.COEFFICIENT, y="y", x="x", cluster="g"))
    ref = sm.OLS(y, sm.add_constant(x)).fit(cov_type="cluster", cov_kwds={"groups": g})
    assert [t["se"] for t in clustered["terms"]] == pytest.approx(list(ref.bse), rel=1e-10)
    robust = values_of(run(r, PlotType.COEFFICIENT, {"robust": True}, y="y", x="x"))
    hc1 = sm.OLS(y, sm.add_constant(x)).fit(cov_type="HC1")
    assert [t["se"] for t in robust["terms"]] == pytest.approx(list(hc1.bse), rel=1e-10)


def test_coefficient_categorical_terms_and_collinearity(rel: Rel) -> None:
    r = rel(
        {
            "y": [1.0, 2, 3, 4, 5, 7, 6, 8],
            "a": [1.0, 2, 3, 4, 5, 6, 7, 8],
            "b": [2.0, 4, 6, 8, 10, 12, 14, 16],
            "k": ["x", "y", "z", "x", "y", "z", "x", "y"],
        }
    )
    values = values_of(run(r, PlotType.COEFFICIENT, y="y", x=["a", "k"]))
    assert [t["term"] for t in values["terms"]] == ["Intercept", "a", "k: y", "k: z"]
    with pytest.raises(InvalidSpecError, match="exact combinations"):
        run(r, PlotType.COEFFICIENT, y="y", x=["a", "b"])
    with pytest.raises(InvalidSpecError, match="also be a predictor"):
        run(r, PlotType.COEFFICIENT, y="y", x=["y"])
    with pytest.raises(InvalidSpecError, match=r"only 2 complete rows|at least 3"):
        run(rel({"y": [1.0, 2], "a": [1.0, 3]}), PlotType.COEFFICIENT, y="y", x="a")


# --- Marginal effects: Spector & Mazzeo (1980), as in the statsmodels documentation ------------


def spector(rel: Rel) -> Relation:
    import statsmodels.api as sm

    data = sm.datasets.spector.load_pandas().data
    return rel(pa.Table.from_pandas(data, preserve_index=False))


def test_marginal_effects_match_published_spector_logit(rel: Rel) -> None:
    out = run(
        spector(rel),
        PlotType.MARGINAL_EFFECTS,
        {"discrete": False},
        y="GRADE",
        x=["GPA", "TUCE", "PSI"],
    )
    values = values_of(out)
    effects = values["effects"]
    # Published (statsmodels docs, Logit get_margeff()): GPA 0.3626 (SE 0.109), PSI 0.3052
    # (SE 0.092). Average marginal effects of a logit are b_k times the mean density, so TUCE's
    # follows from Greene's coefficients (GPA 2.8261, TUCE 0.0952, PSI 2.3787).
    gpa, tuce, psi = (e["estimate"] for e in effects)
    assert gpa == pytest.approx(0.3626, abs=1e-4)
    assert psi == pytest.approx(0.3052, abs=1e-4)
    assert tuce / gpa == pytest.approx(0.0952 / 2.8261, rel=2e-3)
    assert effects[0]["se"] == pytest.approx(0.109, abs=1e-3)
    assert effects[2]["se"] == pytest.approx(0.092, abs=1e-3)
    assert values["pseudoR2"] == pytest.approx(0.3740, abs=1e-4)
    discrete = values_of(
        run(spector(rel), PlotType.MARGINAL_EFFECTS, y="GRADE", x=["GPA", "TUCE", "PSI"])
    )["effects"]
    # A discrete 0 -> 1 change for PSI differs from the derivative.
    assert discrete[2]["estimate"] != pytest.approx(0.3052, abs=1e-3)
    assert discrete[0]["estimate"] == pytest.approx(0.3626, abs=1e-4)


def test_marginal_effects_ols_is_the_slope(rel: Rel) -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=100)
    y = 3 * x + rng.normal(size=100)
    r = rel({"y": y, "x": x})
    me = values_of(run(r, PlotType.MARGINAL_EFFECTS, {"model": "ols"}, y="y", x="x"))["effects"]
    coef = values_of(run(r, PlotType.COEFFICIENT, y="y", x="x"))["terms"][1]
    assert me[0]["estimate"] == pytest.approx(coef["estimate"])
    assert me[0]["se"] == pytest.approx(coef["se"])


def test_perfect_separation_is_a_clear_error(rel: Rel) -> None:
    r = rel({"y": [0, 0, 0, 1, 1, 1], "x": [1.0, 2, 3, 4, 5, 6]})
    with pytest.raises(InvalidSpecError, match="Perfect separation"):
        run(r, PlotType.MARGINAL_EFFECTS, y="y", x="x")
    with pytest.raises(InvalidSpecError, match="exactly two values"):
        run(rel({"y": [0, 1, 2, 1], "x": [1.0, 2, 3, 4]}), PlotType.MARGINAL_EFFECTS, y="y", x="x")
    with pytest.raises(InvalidSpecError, match="negative"):
        run(
            rel({"y": [0.0, -1, 2, 1], "x": [1.0, 2, 3, 4]}),
            PlotType.MARGINAL_EFFECTS,
            {"model": "poisson"},
            y="y",
            x="x",
        )


# --- Forest plot: BCG vaccine trials (Colditz et al. 1994; metafor's dat.bcg) ---------------

BCG = [
    (4, 119, 11, 128),
    (6, 300, 29, 274),
    (3, 228, 11, 209),
    (62, 13536, 248, 12619),
    (33, 5036, 47, 5761),
    (180, 1361, 372, 1079),
    (8, 2537, 10, 619),
    (505, 87886, 499, 87892),
    (29, 7470, 45, 7232),
    (17, 1699, 65, 1600),
    (186, 50448, 141, 27197),
    (5, 2493, 3, 2338),
    (27, 16886, 29, 17825),
]


def bcg() -> tuple[np.ndarray, np.ndarray]:
    a = np.array(BCG, dtype=float)
    yi = np.log((a[:, 0] / (a[:, 0] + a[:, 1])) / (a[:, 2] / (a[:, 2] + a[:, 3])))
    vi = 1 / a[:, 0] - 1 / (a[:, 0] + a[:, 1]) + 1 / a[:, 2] - 1 / (a[:, 2] + a[:, 3])
    return yi, vi


def test_forest_pooling_matches_metafor_bcg(rel: Rel) -> None:
    yi, vi = bcg()
    r = rel(
        {
            "trial": [f"T{i + 1}" for i in range(13)],
            "rr": np.exp(yi),
            "se": np.sqrt(vi),
        }
    )
    out = run(r, PlotType.FOREST, {"measure": "ratio"}, label="trial", estimate="rr", se="se")
    pooled = values_of(out)["pooled"]
    # metafor rma(yi, vi, method = "DL"): -0.7141 (SE 0.1787), tau^2 0.3088, I^2 92.12%,
    # Q(df = 12) = 152.2330.
    assert math.log(pooled["estimate"]) == pytest.approx(-0.7141, abs=1e-4)
    assert pooled["se"] == pytest.approx(0.1787, abs=1e-4)
    assert pooled["tau2"] == pytest.approx(0.3088, abs=1e-4)
    assert pooled["i2"] == pytest.approx(0.9212, abs=1e-4)
    assert pooled["q"] == pytest.approx(152.2330, abs=1e-3) and pooled["qDf"] == 12
    fixed = values_of(
        run(
            r,
            PlotType.FOREST,
            {"measure": "ratio", "pooled": "fixed"},
            label="trial",
            estimate="rr",
            se="se",
        )
    )["pooled"]
    # metafor method = "FE": -0.4303 (SE 0.0405).
    assert math.log(fixed["estimate"]) == pytest.approx(-0.4303, abs=1e-4)
    assert fixed["se"] == pytest.approx(0.0405, abs=1e-4)
    weights = [row["weight"] for row in values_of(out)["rows"]]
    assert sum(weights) == pytest.approx(100)


def test_forest_matches_statsmodels_combine_effects(rel: Rel) -> None:
    from statsmodels.stats.meta_analysis import combine_effects

    yi, vi = bcg()
    ref = combine_effects(yi, vi, method_re="chi2")
    pooled = ec.pool_effects(yi, vi, "random")
    assert pooled.estimate == pytest.approx(ref.mean_effect_re, rel=1e-10)
    assert pooled.tau2 == pytest.approx(ref.tau2, rel=1e-10)


def test_forest_from_ci_limits_and_subgroups(rel: Rel) -> None:
    r = rel(
        {
            "s": ["A", "B", "C", "D"],
            "d": [0.1, 0.3, 0.2, 0.5],
            "lo": [-0.096, 0.104, 0.004, 0.304],
            "hi": [0.296, 0.496, 0.396, 0.696],
            "g": ["x", "x", "y", "y"],
        }
    )
    values = values_of(
        run(r, PlotType.FOREST, label="s", estimate="d", lower="lo", upper="hi", color="g")
    )
    se = values["rows"][0]["se"]
    assert se == pytest.approx(0.392 / (2 * 1.959963985), rel=1e-6)
    assert [s["key"] for s in values["subgroups"]] == ["x", "y"]
    with pytest.raises(InvalidSpecError, match="standard error"):
        run(r, PlotType.FOREST, label="s", estimate="d", lower="lo")
    single = values_of(
        run(
            rel({"s": ["A"], "d": [0.2], "e": [0.1]}),
            PlotType.FOREST,
            label="s",
            estimate="d",
            se="e",
        )
    )
    assert single["pooled"]["estimate"] == pytest.approx(0.2) and single["pooled"]["i2"] is None


# --- Impulse responses ------------------------------------------------------------------------


def _var1(n: int = 400, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    a = np.array([[0.5, 0.1], [0.2, 0.3]])
    x = np.zeros((n, 2))
    for t in range(1, n):
        x[t] = a @ x[t - 1] + rng.normal(size=2)
    return x


def test_irf_matches_closed_form_var1(rel: Rel) -> None:
    import statsmodels.api as sm

    x = _var1()
    r = rel({"t": np.arange(len(x)), "a": x[:, 0], "b": x[:, 1]})
    raw = values_of(
        run(
            r,
            PlotType.IMPULSE_RESPONSE,
            {"lags": 1, "periods": 6, "orthogonalized": False},
            y=["a", "b"],
            time="t",
        )
    )
    # Independent OLS estimate of the VAR(1): y_t = c + A y_{t-1}.
    design = sm.add_constant(x[:-1])
    coef = np.linalg.lstsq(design, x[1:], rcond=None)[0]
    a = coef[1:].T
    responses = {(r_["impulse"], r_["response"]): r_ for r_ in raw["responses"]}
    for h in range(7):
        power = np.linalg.matrix_power(a, h)
        for j, imp in enumerate(["a", "b"]):
            for i, resp in enumerate(["a", "b"]):
                assert responses[(imp, resp)]["irf"][h] == pytest.approx(power[i, j], abs=1e-10)
    # At h = 1 the response is the coefficient, so its SE is the coefficient's OLS SE.
    ols = sm.OLS(x[1:, 0], design).fit()
    z = 1.959963984540054
    band = responses[("b", "a")]
    assert (band["upper"][1] - band["irf"][1]) / z == pytest.approx(ols.bse[2], rel=1e-8)
    # Orthogonalized: A^h P with P the Cholesky factor of the residual covariance (df-adjusted).
    orth = values_of(
        run(r, PlotType.IMPULSE_RESPONSE, {"lags": 1, "periods": 2}, y=["a", "b"], time="t")
    )
    resid = x[1:] - design @ coef
    sigma = resid.T @ resid / (len(resid) - 3)
    p = np.linalg.cholesky(sigma)
    first = {(r_["impulse"], r_["response"]): r_["irf"][0] for r_ in orth["responses"]}
    assert first[("a", "a")] == pytest.approx(p[0, 0], rel=1e-8)
    assert first[("a", "b")] == pytest.approx(p[1, 0], rel=1e-8)
    assert first[("b", "a")] == pytest.approx(0, abs=1e-12)
    assert orth["stable"] is True


def test_irf_degenerate_inputs(rel: Rel) -> None:
    x = _var1(30)
    with pytest.raises(InvalidSpecError, match="repeats"):
        run(
            rel({"t": [1] * 30, "a": x[:, 0], "b": x[:, 1]}),
            PlotType.IMPULSE_RESPONSE,
            y=["a", "b"],
            time="t",
        )
    with pytest.raises(InvalidSpecError, match="too few"):
        run(
            rel({"t": np.arange(6), "a": x[:6, 0], "b": x[:6, 1]}),
            PlotType.IMPULSE_RESPONSE,
            {"lags": 3},
            y=["a", "b"],
            time="t",
        )
    # Automatic lags with too few periods: the same clear message, not "collinear".
    with pytest.raises(InvalidSpecError, match="too few for 2 series with 1 lag"):
        run(
            rel({"t": np.arange(4), "a": x[:4, 0], "b": x[:4, 1]}),
            PlotType.IMPULSE_RESPONSE,
            y=["a", "b"],
            time="t",
        )
    short = values_of(
        run(
            rel({"t": np.arange(9), "a": x[:9, 0], "b": x[:9, 1]}),
            PlotType.IMPULSE_RESPONSE,
            y=["a", "b"],
            time="t",
        )
    )
    assert short["lags"] >= 1
    auto = values_of(
        run(
            rel({"t": np.arange(30), "a": x[:, 0], "b": x[:, 1]}),
            PlotType.IMPULSE_RESPONSE,
            y=["a", "b"],
            time="t",
        )
    )
    assert auto["lags"] >= 1


# --- Event study ------------------------------------------------------------------------------


def panel(noise: float, seed: int = 0, never_treated: int = 10) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    units, years = 40, list(range(2000, 2012))
    effects = {k: 0.5 * k for k in range(0, 6)}  # dynamic effect from treatment on
    rows: dict[str, list[Any]] = {"unit": [], "year": [], "y": [], "treat": []}
    alpha = rng.normal(size=units)
    gamma = {yr: rng.normal() for yr in years}
    for u in range(units):
        start = None if u < never_treated else 2004 + u % 5
        for yr in years:
            rel_t = None if start is None else yr - start
            effect = effects.get(min(rel_t, 5), 0.0) if rel_t is not None and rel_t >= 0 else 0.0
            rows["unit"].append(f"u{u}")
            rows["year"].append(yr)
            rows["treat"].append(start)
            rows["y"].append(alpha[u] + gamma[yr] + effect + noise * rng.normal())
    return rows


def test_event_study_recovers_noiseless_effects(rel: Rel) -> None:
    values = values_of(
        run(
            rel(panel(0.0)),
            PlotType.EVENT_STUDY,
            y="y",
            unit="unit",
            time="year",
            treatTime="treat",
        )
    )
    by_k = {e["relTime"]: e for e in values["estimates"]}
    assert by_k[-1]["reference"] is True and by_k[-1]["estimate"] == 0
    for k in range(-5, 6):
        if k == -1:
            continue
        expected = 0.5 * k if k >= 0 else 0.0
        assert by_k[k]["estimate"] == pytest.approx(expected, abs=1e-8)
    assert values["nUnits"] == 40 and values["nTreatedUnits"] == 30


def test_event_study_equals_dummy_regression_with_clustered_se(rel: Rel) -> None:
    import statsmodels.api as sm

    data = panel(1.0, seed=3)
    values = values_of(
        run(
            rel(data),
            PlotType.EVENT_STUDY,
            {"windowStart": -3, "windowEnd": 3},
            y="y",
            unit="unit",
            time="year",
            treatTime="treat",
        )
    )
    unit = np.array(data["unit"])
    year = np.array(data["year"])
    treat = np.array([np.nan if t is None else t for t in data["treat"]], dtype=float)
    rel_t = np.clip(year - treat, -3, 3)
    ks = [k for k in range(-3, 4) if k != -1]
    d = np.column_stack([(rel_t == k).astype(float) for k in ks])
    u_d = (unit[:, None] == np.unique(unit)[None, 1:]).astype(float)
    y_d = (year[:, None] == np.unique(year)[None, 1:]).astype(float)
    x = np.column_stack([np.ones(len(year)), d, u_d, y_d])
    _, groups = np.unique(unit, return_inverse=True)
    ref = sm.OLS(np.array(data["y"]), x).fit(cov_type="cluster", cov_kwds={"groups": groups})
    ours = {e["relTime"]: e for e in values["estimates"]}
    n, k_full = len(year), x.shape[1]
    # Same coefficients (Frisch-Waugh-Lovell); SEs differ only by the small-sample factor:
    # unit effects nested in the clusters are not counted and the period effects count T - 1
    # (fixest's ssc(fixef.K = "nested")).
    k_ours = len(ks) + (len(np.unique(year)) - 1)
    factor = math.sqrt((n - k_full) / (n - k_ours))
    for j, k in enumerate(ks):
        assert ours[k]["estimate"] == pytest.approx(ref.params[1 + j], abs=1e-8)
        assert ours[k]["se"] == pytest.approx(ref.bse[1 + j] * factor, rel=1e-6)
    assert values["preTrend"]["df"][0] == 2


def test_event_study_degenerate_inputs(rel: Rel) -> None:
    data = panel(0.0)
    r = rel(data)
    with pytest.raises(InvalidSpecError, match="either"):
        run(r, PlotType.EVENT_STUDY, y="y", unit="unit", time="year")
    never = {**data, "treat": [None] * len(data["treat"])}
    never["treat"] = pa.array(never["treat"], pa.float64())
    with pytest.raises(InvalidSpecError, match="No row is treated"):
        run(rel(never), PlotType.EVENT_STUDY, y="y", unit="unit", time="year", treatTime="treat")
    with pytest.raises(InvalidSpecError, match="reference"):
        run(
            r,
            PlotType.EVENT_STUDY,
            {"reference": -9},
            y="y",
            unit="unit",
            time="year",
            treatTime="treat",
        )
    event = [
        None if t is None else yr - t for t, yr in zip(data["treat"], data["year"], strict=True)
    ]
    via_event = values_of(
        run(
            rel({**data, "et": pa.array(event, pa.float64())}),
            PlotType.EVENT_STUDY,
            y="y",
            unit="unit",
            time="year",
            eventTime="et",
        )
    )
    assert {e["relTime"]: e["estimate"] for e in via_event["estimates"]}[3] == pytest.approx(1.5)


def test_demean_two_way_balanced_is_exact() -> None:
    rng = np.random.default_rng(0)
    u = np.repeat(np.arange(5), 4)
    t = np.tile(np.arange(4), 5)
    x = rng.normal(size=(20, 1))
    out = ec.demean(x, [u, t])
    grid = x[:, 0].reshape(5, 4)
    expected = grid - grid.mean(axis=1, keepdims=True) - grid.mean(axis=0) + grid.mean()
    assert out[:, 0] == pytest.approx(expected.ravel(), abs=1e-12)
