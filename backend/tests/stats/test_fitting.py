from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from scipy import stats as sps

from viz.contracts import DistributionFitRequest, DistributionFitValues, SourceRef
from viz.stats import fitting
from viz.stats.errors import StatsInputError
from viz.stats.fitting import fit_distributions

SOURCE = SourceRef(kind="dataset", id="d")


def _run(rel: Any, **kwargs: Any) -> tuple[Any, DistributionFitValues]:
    result = fit_distributions(rel, DistributionFitRequest(source=SOURCE, **kwargs))
    return result, DistributionFitValues.model_validate(result.values)


def _params(v: DistributionFitValues, name: str) -> dict[str, float]:
    fit = next(f for f in v.fits if f.distribution == name)
    return {p.name: p.value for p in fit.params}


def test_closed_form_maximum_likelihood(table_relation: Any) -> None:
    rng = np.random.default_rng(3)
    x = rng.lognormal(1.0, 0.5, 2000)
    _, v = _run(
        table_relation({"x": x}), field="x", distributions=["norm", "lognorm", "expon", "uniform"]
    )
    norm = _params(v, "norm")
    assert norm["loc"] == pytest.approx(x.mean(), rel=1e-12)
    assert norm["scale"] == pytest.approx(x.std(ddof=0), rel=1e-9)
    lognorm = _params(v, "lognorm")
    assert lognorm["s"] == pytest.approx(np.log(x).std(ddof=0), rel=1e-6)
    assert lognorm["scale"] == pytest.approx(math.exp(np.log(x).mean()), rel=1e-6)
    assert lognorm["loc"] == 0.0
    assert _params(v, "expon")["scale"] == pytest.approx(x.mean(), rel=1e-9)
    uniform = _params(v, "uniform")
    assert uniform["loc"] == pytest.approx(x.min()) and uniform["scale"] == pytest.approx(np.ptp(x))
    lognorm_fit = next(f for f in v.fits if f.distribution == "lognorm")
    assert {p.name: p.fixed for p in lognorm_fit.params} == {
        "s": False,
        "loc": True,
        "scale": False,
    }
    # Normal log-likelihood and information criteria in closed form.
    fit = next(f for f in v.fits if f.distribution == "norm")
    n = len(x)
    ll = -n / 2 * (math.log(2 * math.pi * x.var()) + 1)
    assert fit.log_likelihood == pytest.approx(ll, rel=1e-10)
    assert fit.aic == pytest.approx(4 - 2 * ll, rel=1e-10)
    assert fit.bic == pytest.approx(2 * math.log(n) - 2 * ll, rel=1e-10)
    ks = sps.kstest(x, sps.norm(norm["loc"], norm["scale"]).cdf)
    assert fit.ks_statistic == pytest.approx(ks.statistic, rel=1e-12)
    assert fit.ks_p_value == pytest.approx(ks.pvalue, rel=1e-9)


def test_ranking_picks_the_generating_family(table_relation: Any) -> None:
    rng = np.random.default_rng(10)
    x = rng.gamma(2.0, 3.0, 5000)
    result, v = _run(table_relation({"x": x}), field="x")
    assert v.best == "gamma" and v.fits[0].delta_aic == 0
    gamma = _params(v, "gamma")
    assert gamma["a"] == pytest.approx(2.0, rel=0.05) and gamma["scale"] == pytest.approx(
        3.0, rel=0.05
    )
    aics = [f.aic for f in v.fits]
    assert aics == sorted(aics)
    assert sum(f.aic_weight or 0 for f in v.fits) == pytest.approx(1.0)
    assert [s.distribution for s in v.skipped] == ["beta"]
    assert any("Beta was not fitted" in w for w in result.warnings)
    assert any("approximate" in w for w in result.warnings)
    # Plot data: a density histogram integrating to 1 and fitted densities on a grid.
    widths = np.diff(v.edges)
    assert float((np.array(v.density) * widths).sum()) == pytest.approx(1.0)
    assert len(v.x) == 200 and all(len(f.pdf) == 200 for f in v.fits)
    assert result.method == "Maximum-likelihood distribution fits, ranked by AIC"


def test_support_rules(table_relation: Any) -> None:
    rng = np.random.default_rng(1)
    signed = rng.normal(0, 1, 500)
    _, v = _run(table_relation({"x": signed}), field="x")
    skipped = {s.distribution for s in v.skipped}
    assert skipped == {"lognorm", "gamma", "weibull_min", "expon", "beta"}
    unit = rng.beta(2, 5, 500)
    _, b = _run(table_relation({"x": unit}), field="x", distributions=["beta", "norm"])
    assert b.best == "beta"
    params = _params(b, "beta")
    ref = sps.beta.fit(unit, floc=0, fscale=1)
    assert (params["a"], params["b"]) == pytest.approx(ref[:2], rel=1e-9)


def test_sampled_above_the_cap(table_relation: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fitting, "FIT_CAP", 500)
    rng = np.random.default_rng(2)
    result, _ = _run(
        table_relation({"x": rng.normal(0, 1, 3000)}), field="x", distributions=["norm"]
    )
    assert result.sampled and result.n == 500 and result.n_total == 3000


def test_degenerate_inputs(table_relation: Any) -> None:
    with pytest.raises(StatsInputError, match="All values are equal"):
        _run(table_relation({"x": [2.0] * 10}), field="x")
    with pytest.raises(StatsInputError, match="at least 3 values"):
        _run(table_relation({"x": [1.0, 2.0]}), field="x")
    with pytest.raises(StatsInputError, match="None of the distributions"):
        _run(table_relation({"x": [-1.0, 0.5, 2.0, 3.0]}), field="x", distributions=["gamma"])
    result, _ = _run(
        table_relation({"x": [1.0, 2.0, 2.0, 3.0] * 10}), field="x", distributions=["norm"]
    )
    assert any("only 3 distinct values" in w for w in result.warnings)
