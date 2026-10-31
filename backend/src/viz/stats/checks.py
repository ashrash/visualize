"""Assumption checks reported with tests (CLAUDE.md: assumption checks are warnings, not errors).

Normality: Shapiro-Wilk, run when 3 <= n <= 5,000 (SciPy's p-value is accurate up to 5,000).
t-tests and ANOVA are robust to non-normality once each group has about 30 values (central
limit theorem), so a failed check becomes a warning only for smaller groups; the check
itself is always reported.

Equal variances: Brown-Forsythe (Levene with medians), exact in SQL (moments.levene).
"""

from __future__ import annotations

import math
import warnings as py_warnings

import numpy as np
from scipy import stats as sps

from viz.contracts import AssumptionCheck
from viz.stats.moments import Levene

ALPHA = 0.05
SHAPIRO_MAX = 5_000
SMALL_SAMPLE = 30


def _fmt_p(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def shapiro(values: np.ndarray | None, name: str, n: int | None = None) -> AssumptionCheck:
    """Shapiro-Wilk normality check of `values` (finite floats).

    Pass values=None with the sample size `n` when the sample is too large to fetch.
    """
    if values is None:
        if n is None or n <= SHAPIRO_MAX:
            raise ValueError("values are needed for samples of up to SHAPIRO_MAX")
    else:
        n = len(values)
    if n < 3:
        return AssumptionCheck(
            name=name, statistic=None, p_value=None, passed=None, note="Needs at least 3 values."
        )
    if n > SHAPIRO_MAX:
        return AssumptionCheck(
            name=name,
            statistic=None,
            p_value=None,
            passed=None,
            note=f"Not run above {SHAPIRO_MAX:,} values: with this many the test relies "
            "on the central limit theorem, not on normality.",
        )
    assert values is not None
    if float(np.ptp(values)) == 0.0:
        return AssumptionCheck(
            name=name, statistic=None, p_value=None, passed=None, note="All values are equal."
        )
    with py_warnings.catch_warnings():
        py_warnings.simplefilter("ignore")
        result = sps.shapiro(values)
    statistic, p_value = float(result.statistic), float(result.pvalue)
    passed = p_value >= ALPHA
    note = None
    if not passed:
        note = (
            "Small sample: results that assume normality may be unreliable."
            if n < SMALL_SAMPLE
            else f"With {n:,} values the test is fairly robust to non-normality."
        )
    return AssumptionCheck(
        name=name, statistic=statistic, p_value=p_value, passed=passed, note=note
    )


def normality_warning(
    check: AssumptionCheck, what: str, n: int, alternative: str, size: str = "n"
) -> str | None:
    """A warning when a small sample (n < 30) fails the normality check.

    `size` names n in the message (e.g. "smallest group n").
    """
    if check.passed is not False or n >= SMALL_SAMPLE or check.p_value is None:
        return None
    return (
        f"Normality is doubtful for {what} (Shapiro-Wilk {_fmt_p(check.p_value)}, "
        f"{size} = {n:,}); with so few values the result may be unreliable. "
        f"Consider {alternative}."
    )


def levene_check(result: Levene | None, name: str) -> AssumptionCheck:
    if result is None:
        return AssumptionCheck(
            name=name, statistic=None, p_value=None, passed=None, note="Needs at least 2 groups."
        )
    if result.p_value is None:
        note = (
            "Needs at least 2 values in some group."
            if result.df2 <= 0
            else "Every value equals its group median."
        )
        return AssumptionCheck(name=name, statistic=None, p_value=None, passed=None, note=note)
    passed = result.p_value >= ALPHA
    statistic = result.statistic if result.statistic is not None else None
    if statistic is not None and not math.isfinite(statistic):
        statistic = None
    return AssumptionCheck(
        name=name,
        statistic=statistic,
        p_value=result.p_value,
        passed=passed,
        note=None if passed else "The groups' variances differ.",
    )


def p_text(p: float) -> str:
    return _fmt_p(p)
