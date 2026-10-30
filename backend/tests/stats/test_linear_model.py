from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
import statsmodels.api as sm
import statsmodels.formula.api as smf
from statsmodels.stats.diagnostic import het_breuschpagan
from statsmodels.stats.outliers_influence import variance_inflation_factor

from viz.contracts import SourceRef
from viz.stats import linear_model
from viz.stats.errors import StatsInputError
from viz.stats.linear_model import linear_regression
from viz.stats.models import RegressionRequest, RegressionValues

SOURCE = SourceRef(kind="dataset", id="d")
NIST = Path(__file__).parents[1] / "data" / "stats" / "nist"


def _run(rel: Any, **kwargs: Any) -> tuple[Any, RegressionValues]:
    result = linear_regression(rel, RegressionRequest(source=SOURCE, **kwargs))
    return result, RegressionValues.model_validate(result.values)


def _nist(name: str) -> dict[str, Any]:
    lines = (NIST / f"{name}.dat").read_text(encoding="ascii").splitlines()
    coef, se = [], []
    sigma = r2 = None
    start = None
    for i, line in enumerate(lines):
        if m := re.match(r"^\s+B(\d+)\s+(\S+)\s+(\S+)", line):
            coef.append(float(m.group(2)))
            se.append(float(m.group(3)))
        elif line.strip() == "Residual" and "Standard Deviation" in lines[i + 1]:
            sigma = float(lines[i + 1].split()[-1])
        elif m := re.search(r"R-Squared\s+(\S+)", line):
            r2 = float(m.group(1))
        elif re.match(r"^Data:\s+y\s+x\s*$", line):
            start = i + 1
    assert start is not None
    data = np.array([list(map(float, r.split())) for r in lines[start:] if r.strip()])
    return {"coef": coef, "se": se, "sigma": sigma, "r2": r2, "y": data[:, 0], "x": data[:, 1]}


def test_nist_norris(table_relation: Any) -> None:
    cert = _nist("Norris")
    result, v = _run(table_relation({"x": cert["x"], "y": cert["y"]}), y="y", x=["x"])
    assert [t.term for t in v.terms] == ["(Intercept)", "x"]
    np.testing.assert_allclose([t.estimate for t in v.terms], cert["coef"], rtol=1e-9)
    np.testing.assert_allclose([t.std_error for t in v.terms], cert["se"], rtol=1e-7)
    assert v.sigma == pytest.approx(cert["sigma"], rel=1e-9)
    assert v.r2 == pytest.approx(cert["r2"], rel=1e-12)
    assert result.method == "OLS linear regression" and not result.sampled


def test_nist_pontius_as_multiple_regression(table_relation: Any) -> None:
    cert = _nist("Pontius")
    rel = table_relation({"x": cert["x"], "x2": cert["x"] ** 2, "y": cert["y"]})
    result, v = _run(rel, y="y", x=["x", "x2"])
    np.testing.assert_allclose([t.estimate for t in v.terms], cert["coef"], rtol=1e-8)
    np.testing.assert_allclose([t.std_error for t in v.terms], cert["se"], rtol=1e-6)
    assert v.sigma == pytest.approx(cert["sigma"], rel=1e-8)
    assert v.r2 == pytest.approx(cert["r2"], rel=1e-12)
    assert result.method == "OLS multiple regression"


@pytest.fixture
def frame() -> pd.DataFrame:
    rng = np.random.default_rng(21)
    n = 400
    region = rng.choice(["West", "East", "North"], n)
    x1 = rng.normal(10, 3, n)
    x2 = rng.uniform(0, 5, n) + 0.3 * x1
    effect = {"East": 0.0, "North": 1.5, "West": -2.0}
    y = 3 + 0.8 * x1 - 1.2 * x2 + np.array([effect[r] for r in region]) + rng.normal(0, 2, n)
    return pd.DataFrame({"y": y, "x1": x1, "x2": x2, "region": region})


@pytest.mark.parametrize("robust", [False, True])
def test_matches_statsmodels_with_a_categorical(
    table_relation: Any, frame: pd.DataFrame, robust: bool
) -> None:
    rel = table_relation(pa.Table.from_pandas(frame, preserve_index=False))
    _, v = _run(rel, y="y", x=["x1", "region", "x2"], robust=robust, ci_level=0.9)
    model = smf.ols("y ~ x1 + C(region) + x2", data=frame).fit(
        cov_type="HC3" if robust else "nonrobust"
    )
    names = ["(Intercept)", "x1", "region[North]", "region[West]", "x2"]
    theirs = ["Intercept", "x1", "C(region)[T.North]", "C(region)[T.West]", "x2"]
    assert [t.term for t in v.terms] == names
    assert v.reference_levels[0].field == "region" and v.reference_levels[0].level == "East"
    conf = model.conf_int(alpha=0.1)
    for term, key in zip(v.terms, theirs, strict=True):
        assert term.estimate == pytest.approx(model.params[key], rel=1e-9)
        assert term.std_error == pytest.approx(model.bse[key], rel=1e-9)
        assert term.statistic == pytest.approx(model.tvalues[key], rel=1e-9)
        assert term.p_value == pytest.approx(model.pvalues[key], rel=1e-7)
        assert (term.ci_low, term.ci_high) == pytest.approx(tuple(conf.loc[key]), rel=1e-9)
    assert v.terms[2].field == "region" and v.terms[2].level == "North"
    assert v.r2 == pytest.approx(model.rsquared, rel=1e-12)
    assert v.adj_r2 == pytest.approx(model.rsquared_adj, rel=1e-12)
    assert v.f_statistic == pytest.approx(model.fvalue, rel=1e-9)
    assert v.f_p_value == pytest.approx(model.f_pvalue, rel=1e-6, abs=1e-300)
    assert v.aic == pytest.approx(model.aic, rel=1e-12)
    assert v.bic == pytest.approx(model.bic, rel=1e-12)
    assert v.log_likelihood == pytest.approx(model.llf, rel=1e-12)
    assert (v.df_model, v.df_resid) == (4, 395)


def test_vif_and_breusch_pagan(table_relation: Any) -> None:
    rng = np.random.default_rng(5)
    n = 300
    a = rng.normal(0, 1, n)
    b = a + rng.normal(0, 0.2, n)  # strongly collinear with a
    c = rng.uniform(1, 4, n)
    y = 1 + a + b + c + rng.normal(0, 1, n) * c  # variance grows with c
    rel = table_relation({"y": y, "a": a, "b": b, "c": c})
    result, v = _run(rel, y="y", x=["a", "b", "c"])
    exog = sm.add_constant(np.column_stack([a, b, c]))
    for j, term in enumerate(v.terms):
        if j == 0:
            assert term.vif is None
        else:
            assert term.vif == pytest.approx(variance_inflation_factor(exog, j), rel=1e-9)
    assert any("VIF > 10" in w and "'a'" in w and "'b'" in w for w in result.warnings)
    resid = sm.OLS(y, exog).fit().resid
    lm, lm_p, _, _ = het_breuschpagan(resid, exog)
    bp = next(c for c in v.checks if c.name.startswith("Breusch"))
    assert bp.statistic == pytest.approx(lm, rel=1e-9) and bp.p_value == pytest.approx(
        lm_p, rel=1e-6
    )
    assert bp.passed is False
    assert any("robust standard errors" in w for w in result.warnings)
    shapiro = next(c for c in v.checks if c.name.startswith("Shapiro"))
    assert shapiro.p_value is not None


def test_no_intercept_matches_statsmodels(table_relation: Any) -> None:
    rng = np.random.default_rng(6)
    x = rng.uniform(1, 10, 80)
    y = 2.5 * x + rng.normal(0, 1, 80)
    result, v = _run(table_relation({"x": x, "y": y}), y="y", x=["x"], intercept=False)
    model = sm.OLS(y, x[:, None]).fit()
    assert [t.term for t in v.terms] == ["x"]
    assert v.terms[0].estimate == pytest.approx(model.params[0], rel=1e-12)
    assert v.r2 == pytest.approx(model.rsquared, rel=1e-12)
    assert any("uncentred" in w for w in result.warnings)
    assert v.terms[0].vif is None


def test_boolean_and_date_predictors(table_relation: Any) -> None:
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(10)]
    flag = [i % 3 == 0 for i in range(10)]
    y = [2.0 * i + 5.0 * f + (0.1 if i % 2 else -0.1) for i, f in enumerate(flag)]
    result, v = _run(table_relation({"y": y, "day": days, "flag": flag}), y="y", x=["day", "flag"])
    epoch_days = np.array([(d - dt.date(1970, 1, 1)).days for d in days], dtype=float)
    design = np.column_stack([np.ones(10), epoch_days, np.array(flag, dtype=float)])
    expected = np.linalg.lstsq(design, np.array(y), rcond=None)[0]
    assert [t.estimate for t in v.terms] == pytest.approx(expected.tolist(), rel=1e-9)
    assert v.terms[1].estimate == pytest.approx(2.0, abs=0.01)  # per day
    assert "'day' is measured in days since 1970-01-01." in result.warnings


def test_missing_rows_and_sampling(
    table_relation: Any, frame: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
) -> None:
    frame = frame.copy()
    frame.loc[[0, 1], "x1"] = np.nan
    frame.loc[2, "region"] = None
    rel = table_relation(pa.Table.from_pandas(frame, preserve_index=False))
    result, _ = _run(rel, y="y", x=["x1", "region"])
    assert result.n == 397 and result.n_missing == 3
    monkeypatch.setattr(linear_model, "MODEL_CAP", 100)
    sampled, _ = _run(rel, y="y", x=["x1", "region"])
    assert sampled.sampled and sampled.n == 100 and sampled.n_total == 397


def test_exact_fit(table_relation: Any) -> None:
    x = np.arange(10.0)
    result, v = _run(table_relation({"x": x, "y": 3 * x + 1}), y="y", x=["x"])
    assert [t.estimate for t in v.terms] == pytest.approx([1.0, 3.0])
    assert all(t.std_error is None and t.p_value is None for t in v.terms)
    assert v.f_statistic is None and v.r2 == pytest.approx(1.0)
    assert any("fit y exactly" in w for w in result.warnings)


def test_degenerate_designs(table_relation: Any) -> None:
    rng = np.random.default_rng(2)
    x1 = rng.normal(0, 1, 20)
    rel = table_relation(
        {
            "y": rng.normal(0, 1, 20),
            "x1": x1,
            "x2": 2 * x1 + 3,
            "const": np.full(20, 4.0),
            "one": ["a"] * 20,
            "many": [f"l{i}" for i in range(20)],
            "flag": [True] * 20,
        }
    )
    with pytest.raises(StatsInputError, match="'x2' is a linear combination"):
        _run(rel, y="y", x=["x1", "x2"])
    with pytest.raises(StatsInputError, match="'const' is a linear combination"):
        _run(rel, y="y", x=["x1", "const"])
    with pytest.raises(StatsInputError, match="'one' has a single level"):
        _run(rel, y="y", x=["one"])
    with pytest.raises(StatsInputError, match="'many' has 20 levels"):
        _run(rel, y="y", x=["many"], max_levels=10)
    with pytest.raises(StatsInputError, match="needs more than 20 complete rows"):
        _run(rel, y="y", x=["many"])
    with pytest.raises(StatsInputError, match="both the response and a predictor"):
        _run(rel, y="y", x=["y"])
    with pytest.raises(StatsInputError, match="must be a numeric or boolean field"):
        _run(rel, y="one", x=["x1"])
    with pytest.raises(StatsInputError, match="Unknown field 'nope'"):
        _run(rel, y="y", x=["nope"])
    _, constant_y = _run(
        table_relation({"y": np.full(20, 2.0), "x": rng.normal(0, 1, 20)}), y="y", x=["x"]
    )
    assert constant_y.r2 is None
