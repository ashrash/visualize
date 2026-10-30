from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import statsmodels.api as sm

from viz.contracts import TrendlineKind
from viz.stats import regression
from viz.stats.errors import StatsInputError
from viz.stats.regression import fit_trend, lowess_curve, ols_curve

NIST = Path(__file__).parents[1] / "data" / "stats" / "nist"


def _nist(name: str) -> dict[str, Any]:
    """Certified values and data of an NIST StRD linear least-squares dataset."""
    lines = (NIST / f"{name}.dat").read_text(encoding="ascii").splitlines()
    coef, se = [], []
    sigma = r2 = None
    data_start = None
    for i, line in enumerate(lines):
        if m := re.match(r"^\s+B(\d+)\s+(\S+)\s+(\S+)", line):
            coef.append(float(m.group(2)))
            se.append(float(m.group(3)))
        elif line.strip() == "Residual" and "Standard Deviation" in lines[i + 1]:
            sigma = float(lines[i + 1].split()[-1])
        elif m := re.search(r"R-Squared\s+(\S+)", line):
            r2 = float(m.group(1))
        elif re.match(r"^Data:\s+y\s+x\s*$", line):
            data_start = i + 1
    assert data_start is not None
    rows = [list(map(float, row.split())) for row in lines[data_start:] if row.strip()]
    data = np.array(rows)
    return {"coef": coef, "se": se, "sigma": sigma, "r2": r2, "y": data[:, 0], "x": data[:, 1]}


@pytest.mark.parametrize(("name", "degree"), [("Norris", 1), ("Pontius", 2), ("Wampler1", 5)])
def test_ols_matches_nist_certified_values(name: str, degree: int) -> None:
    cert = _nist(name)
    fit = ols_curve(cert["x"], cert["y"], degree=degree)
    assert len(fit.coef) == degree + 1
    if name == "Wampler1":  # exact polynomial: y = 1 + x + ... + x^5
        np.testing.assert_allclose(fit.coef, cert["coef"], rtol=1e-9)
        assert fit.sigma == pytest.approx(0.0, abs=1e-6 * np.abs(cert["y"]).max())
        assert fit.r2 == pytest.approx(1.0, abs=1e-14)
        assert fit.lower is not None and fit.upper is not None
        assert np.max(fit.upper - fit.lower) < 1e-6 * np.abs(fit.y).max()
        return
    np.testing.assert_allclose(fit.coef, cert["coef"], rtol=1e-9)
    assert fit.coef_se is not None
    np.testing.assert_allclose(fit.coef_se, cert["se"], rtol=1e-7)
    assert fit.sigma == pytest.approx(cert["sigma"], rel=1e-9)
    assert fit.r2 == pytest.approx(cert["r2"], rel=1e-12)
    assert fit.n == len(cert["x"]) and fit.df_resid == len(cert["x"]) - degree - 1


@pytest.mark.parametrize("degree", [1, 2, 3])
def test_ols_bands_match_statsmodels(degree: int) -> None:
    rng = np.random.default_rng(degree)
    x = rng.uniform(-3, 5, 300)
    y = 1.5 - 0.7 * x + 0.2 * x**2 + rng.normal(0, 1.0, 300)
    fit = ols_curve(x, y, degree=degree, ci_level=0.9, prediction=True, grid_size=50)
    model = sm.OLS(y, np.vander(x, degree + 1, increasing=True)).fit()
    frame = model.get_prediction(np.vander(fit.x, degree + 1, increasing=True)).summary_frame(
        alpha=0.1
    )
    np.testing.assert_allclose(fit.y, frame["mean"], rtol=1e-9)
    for ours, theirs in (
        (fit.lower, "mean_ci_lower"),
        (fit.upper, "mean_ci_upper"),
        (fit.pred_lower, "obs_ci_lower"),
        (fit.pred_upper, "obs_ci_upper"),
    ):
        assert ours is not None
        np.testing.assert_allclose(ours, frame[theirs], rtol=1e-9, atol=1e-9)
    np.testing.assert_allclose(fit.coef, model.params, rtol=1e-9)
    assert fit.coef_se is not None
    np.testing.assert_allclose(fit.coef_se, model.bse, rtol=1e-8)
    assert fit.r2 == pytest.approx(model.rsquared, rel=1e-12)
    assert fit.adj_r2 == pytest.approx(model.rsquared_adj, rel=1e-12)


def test_log_forms_match_polyfit() -> None:
    rng = np.random.default_rng(4)
    x = rng.uniform(0.5, 10, 200)
    y = 2.0 * np.exp(0.3 * x) * np.exp(rng.normal(0, 0.1, 200))
    exp_fit = ols_curve(x, y, log_y=True)
    b1, b0 = np.polyfit(x, np.log(y), 1)
    np.testing.assert_allclose(exp_fit.coef, [b0, b1], rtol=1e-9)
    assert exp_fit.kind == "exponential"
    assert exp_fit.equation() == f"y = {np.exp(b0):.4g}e^({b1:.4g}x)"
    np.testing.assert_allclose(exp_fit.y, np.exp(b0 + b1 * exp_fit.x), rtol=1e-9)
    assert exp_fit.lower is not None and np.all(exp_fit.lower > 0)

    power = ols_curve(x, y, log_x=True, log_y=True)
    p1, p0 = np.polyfit(np.log(x), np.log(y), 1)
    np.testing.assert_allclose(power.coef, [p0, p1], rtol=1e-9)
    assert power.kind == "power" and power.equation() == f"y = {np.exp(p0):.4g}x^{p1:.4g}"

    log_fit = ols_curve(x, y, log_x=True)
    l1, l0 = np.polyfit(np.log(x), y, 1)
    np.testing.assert_allclose(log_fit.coef, [l0, l1], rtol=1e-9)
    assert log_fit.kind == "logarithmic"
    assert log_fit.equation() == f"y = {l1:.4g}ln(x) {'-' if l0 < 0 else '+'} {abs(l0):.4g}"


def test_equation_labels() -> None:
    x = np.arange(10.0)
    assert ols_curve(x, 2 * x - 3).equation() == "y = 2x - 3"
    assert ols_curve(x, -(x**2) + 4, degree=2).equation() == "y = -x^2 + 4"
    assert ols_curve(x, 0.5 * x**3 + x, degree=3).equation() == "y = 0.5x^3 + x"


def test_ols_drops_non_positive_values_for_log_forms() -> None:
    fit = ols_curve([-1.0, 0.0, 1.0, 2.0, 3.0], [1.0, 2.0, 2.5, 3.0, 3.5], log_x=True)
    assert fit.n == 3 and fit.n_missing == 2
    assert fit.warnings[0] == "Left out 2 points where x is not positive."


def test_ols_degenerate_inputs() -> None:
    with pytest.raises(StatsInputError, match="x has no variation"):
        ols_curve([2.0, 2.0, 2.0], [1.0, 2.0, 3.0])
    with pytest.raises(StatsInputError, match="at least 3 distinct x values"):
        ols_curve([1.0, 1.0, 2.0, 2.0], [1.0, 2.0, 3.0, 4.0], degree=2)
    with pytest.raises(StatsInputError, match="at least 2 points"):
        ols_curve([1.0], [1.0])
    with pytest.raises(StatsInputError, match="between 1 and 6"):
        ols_curve(np.arange(10.0), np.arange(10.0), degree=7)
    exact = ols_curve([1.0, 2.0], [3.0, 5.0])
    assert exact.lower is None and exact.sigma is None and exact.coef == pytest.approx([1, 2])
    assert "Too few points for a confidence band." in exact.warnings
    flat = ols_curve([1.0, 2.0, 3.0], [4.0, 4.0, 4.0])
    assert flat.r2 is None and "y has no variation, so R² is undefined." in flat.warnings
    nan = ols_curve([1.0, 2.0, np.nan, 3.0], [1.0, 2.0, 5.0, np.inf])
    assert nan.n == 2 and nan.n_missing == 2


def test_lowess_reproduces_a_line() -> None:
    x = np.linspace(0, 10, 200)
    fit = lowess_curve(x, 2 * x + 1, n_boot=50)
    np.testing.assert_allclose(fit.y, 2 * fit.x + 1, atol=1e-9)
    assert fit.lower is not None and fit.upper is not None
    assert np.max(fit.upper - fit.lower) < 1e-8
    assert fit.r2 == pytest.approx(1.0) and fit.equation() is None


def test_lowess_band_is_calibrated_and_seeded() -> None:
    covered = []
    for seed in range(3):
        rng = np.random.default_rng(100 + seed)
        x = rng.uniform(0, 10, 300)
        y = 3 * x + rng.normal(0, 2, 300)
        fit = lowess_curve(x, y, n_boot=100, seed=seed)
        assert fit.lower is not None and fit.upper is not None
        interior = (fit.x > 1) & (fit.x < 9)
        truth = 3 * fit.x
        inside = (fit.lower <= truth) & (truth <= fit.upper)
        covered.append(float(np.mean(inside[interior])))
    assert np.mean(covered) >= 0.85, covered
    rng = np.random.default_rng(7)
    x = rng.uniform(0, 2 * np.pi, 800)
    y = np.sin(x) + rng.normal(0, 0.3, 800)
    one = lowess_curve(x, y, frac=0.2, n_boot=60, seed=3)
    two = lowess_curve(x, y, frac=0.2, n_boot=60, seed=3)
    assert one.lower is not None and two.lower is not None
    np.testing.assert_array_equal(one.lower, two.lower)
    assert np.max(np.abs(one.y - np.sin(one.x))[10:-10]) < 0.15


def test_lowess_in_an_executor_matches_inline() -> None:
    rng = np.random.default_rng(8)
    x, y = rng.uniform(0, 1, 100), rng.normal(size=100)
    inline = lowess_curve(x, y, n_boot=40, seed=5)
    with ThreadPoolExecutor(2) as pool:
        pooled = lowess_curve(x, y, n_boot=40, seed=5, executor=pool)
    assert inline.lower is not None and pooled.lower is not None
    np.testing.assert_array_equal(inline.lower, pooled.lower)


def test_lowess_degenerate_inputs() -> None:
    with pytest.raises(StatsInputError, match="at least 3 points"):
        lowess_curve([1.0, 2.0], [1.0, 2.0])
    with pytest.raises(StatsInputError, match="x has no variation"):
        lowess_curve([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])
    assert lowess_curve([1.0, 2.0, 3.0, 4.0], [1.0, 3.0, 2.0, 4.0], ci_level=None).lower is None


def test_fit_trend_samples_and_reports(
    table_relation: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    rng = np.random.default_rng(9)
    x = rng.uniform(0, 10, 500)
    rel = table_relation({"x": x, "y": 4 * x + rng.normal(0, 1, 500)})
    full = fit_trend(rel, "x", "y", TrendlineKind.LINEAR)
    assert not full.sampled and full.n == 500 and full.n_total == 500
    assert full.coef[1] == pytest.approx(4.0, abs=0.1)
    monkeypatch.setattr(regression, "MODEL_CAP", 100)
    sampled = fit_trend(rel, "x", "y", TrendlineKind.POLYNOMIAL, degree=2)
    assert sampled.sampled and sampled.n == 100 and sampled.n_total == 500
    assert sampled.degree == 2 and sampled.kind == "polynomial"
    assert sampled.warnings[-1] == "Computed on a random sample of 100 of 500 rows."
    loess = fit_trend(rel, "x", "y", TrendlineKind.LOESS, ci_level=None)
    assert loess.kind == "loess" and loess.lower is None
    with pytest.raises(ValueError, match="moving averages"):
        fit_trend(rel, "x", "y", TrendlineKind.MOVING_AVERAGE)


def test_lowess_subsampled_band_matches_the_full_bootstrap() -> None:
    rng = np.random.default_rng(12)
    x = rng.uniform(0, 10, 3000)
    y = np.sin(x) + rng.normal(0, 0.5, 3000)
    full = lowess_curve(x, y, n_boot=60, band_points=3000, seed=1)
    quick = lowess_curve(x, y, n_boot=60, band_points=600, seed=1)
    np.testing.assert_array_equal(full.y, quick.y)
    assert full.lower is not None and full.upper is not None
    assert quick.lower is not None and quick.upper is not None
    ratio = np.mean(quick.upper - quick.lower) / np.mean(full.upper - full.lower)
    assert 0.75 < ratio < 1.33, ratio
