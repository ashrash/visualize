"""Pearson's chi-square tests on counts computed exactly in DuckDB.

- independence: the field x column_field contingency table; scipy.stats.chi2_contingency,
  with Yates' continuity correction for 2x2 tables when `correction` is on (as R's
  chisq.test). Also Cramer's V (from the uncorrected statistic), adjusted standardized
  residuals (O - E) / sqrt(E (1 - row share)(1 - column share)), and for 2x2 tables Fisher's
  exact test with the sample odds ratio.
- goodness_of_fit: counts of one field vs expected proportions (uniform when omitted);
  scipy.stats.chisquare, residuals (O - E) / sqrt(E).
Rows with a missing category are left out (counted in nMissing). Small expected counts are
reported as warnings (Cochran: none below 1 and at most 20% below 5).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy import stats as sps
from sqlglot import exp

from viz.contracts import ChiSquareRequest, ChiSquareValues, Scalar, StatResult
from viz.stats.errors import StatsInputError
from viz.stats.fields import check_distinct, key_expr, key_value, match_case
from viz.stats.relation import Relation
from viz.stats.results import stat_result
from viz.stats.sql import alias, count_star


def _present(*exprs: exp.Expr) -> exp.Expr:
    return exp.and_(*[exp.Not(this=exp.Is(this=e.copy(), expression=exp.Null())) for e in exprs])


def _sort_one(value: object) -> tuple[int, object]:
    return (0, value) if value is not None else (1, 0)


def chi_square(rel: Relation, req: ChiSquareRequest) -> StatResult:
    if req.kind == "independence":
        if req.column_field is None:
            raise StatsInputError("A test of independence needs two categorical fields.")
        if req.expected is not None:
            raise StatsInputError("Expected proportions apply to a goodness-of-fit test only.")
        check_distinct([req.field, req.column_field], "the rows and the columns")
        return _independence(rel, req)
    if req.column_field is not None:
        raise StatsInputError("A goodness-of-fit test uses a single field.")
    return _goodness_of_fit(rel, req)


def _total(rel: Relation) -> int:
    return int(rel.rows(rel.select(count_star()))[0][0])


def _check_levels(rel: Relation, names: Sequence[str], keys: Sequence[exp.Expr], cap: int) -> None:
    selects = [
        alias(exp.Count(this=exp.Distinct(expressions=[k.copy()])), f"d{i}")
        for i, k in enumerate(keys)
    ]
    counts = rel.rows(rel.select(*selects))[0]
    for name, count in zip(names, counts, strict=True):
        if count > cap:
            raise StatsInputError(
                f"'{name}' has {int(count):,} categories; the test supports at most {cap:,}. "
                "Filter the data or raise maxLevels (up to 200)."
            )


def _expected_warning(expected: np.ndarray) -> str | None:
    small = float((expected < 5).mean())
    smallest = float(expected.min())
    if smallest >= 1 and small <= 0.2:
        return None
    return (
        f"{small:.0%} of the expected counts are below 5 (smallest {smallest:.3g}), so the "
        "chi-square approximation may be poor. Combine sparse categories"
        + (" or use Fisher's exact test." if expected.shape == (2, 2) else ".")
    )


def _independence(rel: Relation, req: ChiSquareRequest) -> StatResult:
    assert req.column_field is not None
    names = [req.field, req.column_field]
    keys = [key_expr(rel, n) for n in names]
    _check_levels(rel, names, keys, req.max_levels)
    total_rows = _total(rel)
    counted = rel.narrow(_present(*keys)).select(
        alias(keys[0], "r"), alias(keys[1], "c"), alias(count_star(), "n")
    )
    counted = counted.group_by(keys[0].copy(), keys[1].copy())
    cells = rel.rows(counted)
    row_levels = sorted({r for r, _, _ in cells}, key=_sort_one)
    col_levels = sorted({c for _, c, _ in cells}, key=_sort_one)
    for name, levels in zip(names, (row_levels, col_levels), strict=True):
        if len(levels) < 2:
            raise StatsInputError(
                f"'{name}' needs at least 2 categories with data; it has {len(levels)}."
            )
    ri = {v: i for i, v in enumerate(row_levels)}
    ci = {v: i for i, v in enumerate(col_levels)}
    observed = np.zeros((len(row_levels), len(col_levels)), dtype=np.int64)
    for r, c, n in cells:
        observed[ri[r], ci[c]] = int(n)
    total = int(observed.sum())
    result = sps.chi2_contingency(observed, correction=req.correction)
    expected = np.asarray(result.expected_freq, dtype=float)
    dof = int(result.dof)
    yates = req.correction and dof == 1
    uncorrected = (
        float(sps.chi2_contingency(observed, correction=False).statistic)
        if yates
        else float(result.statistic)
    )
    k = min(observed.shape) - 1
    cramers_v = math.sqrt(uncorrected / (total * k)) if total > 0 and k > 0 else None
    row_share = observed.sum(axis=1, keepdims=True) / total
    col_share = observed.sum(axis=0, keepdims=True) / total
    denominator = np.sqrt(expected * (1 - row_share) * (1 - col_share))
    with np.errstate(divide="ignore", invalid="ignore"):
        residuals = np.where(denominator > 0, (observed - expected) / denominator, np.nan)
    fisher_p = odds_ratio = None
    if observed.shape == (2, 2):
        fisher = sps.fisher_exact(observed)
        fisher_p = float(fisher.pvalue)
        odds_ratio = float(fisher.statistic)
    warnings = [w for w in [_expected_warning(expected)] if w]
    method = "Pearson's chi-square test of independence" + (
        " with Yates' continuity correction" if yates else ""
    )
    values = ChiSquareValues(
        kind="independence",
        statistic=float(result.statistic),
        df=dof,
        p_value=float(result.pvalue),
        rows=[key_value(rel, req.field, v) for v in row_levels],
        columns=[key_value(rel, req.column_field, v) for v in col_levels],
        observed=observed.tolist(),
        expected=expected.tolist(),
        residuals=[[None if math.isnan(x) else float(x) for x in row] for row in residuals],
        cramers_v=cramers_v,
        yates=yates,
        fisher_p_value=fisher_p,
        odds_ratio=odds_ratio if odds_ratio is not None and math.isfinite(odds_ratio) else None,
        min_expected=float(expected.min()),
    )
    return stat_result(
        values.model_dump(by_alias=True),
        method=method,
        n=total,
        n_missing=total_rows - total,
        sampled=False,
        warnings=warnings,
    )


def _goodness_of_fit(rel: Relation, req: ChiSquareRequest) -> StatResult:
    key = key_expr(rel, req.field)
    total_rows = _total(rel)
    warnings: list[str] = []
    if req.expected is None:
        _check_levels(rel, [req.field], [key], req.max_levels)
        counted = rel.narrow(_present(key)).select(alias(key, "v"), alias(count_star(), "n"))
        cells = sorted(rel.rows(counted.group_by(key.copy())), key=lambda r: _sort_one(r[0]))
        levels: list[Scalar] = [key_value(rel, req.field, v) for v, _ in cells]
        observed = np.array([int(n) for _, n in cells], dtype=np.int64)
        proportions = np.full(len(cells), 1.0 / max(1, len(cells)))
    else:
        wanted = [e.value for e in req.expected]
        if len({repr(v) for v in wanted}) != len(wanted):
            raise StatsInputError("Each expected category may appear only once.")
        index = match_case(rel, req.field, wanted)
        counted = rel.narrow(_present(key)).select(alias(index, "i"), alias(count_star(), "n"))
        rows = rel.rows(counted.group_by(index.copy()))
        unmatched = sum(int(n) for i, n in rows if i is None)
        if unmatched:
            raise StatsInputError(
                f"{unmatched:,} rows have a value of '{req.field}' that isn't among the "
                "expected categories."
            )
        counts = {int(i): int(n) for i, n in rows}
        levels = list(wanted)
        observed = np.array([counts.get(i, 0) for i in range(len(wanted))], dtype=np.int64)
        # Each proportion is > 0 (the request model enforces it).
        proportions = np.array([e.proportion for e in req.expected], dtype=float)
        if abs(proportions.sum() - 1) > 1e-9:
            warnings.append("The expected proportions were rescaled to sum to 1.")
        proportions = proportions / proportions.sum()
    if len(levels) < 2:
        raise StatsInputError(
            f"'{req.field}' needs at least 2 categories with data; it has {len(levels)}."
        )
    total = int(observed.sum())
    if total == 0:
        raise StatsInputError(f"No rows have a value for '{req.field}'.")
    expected = proportions * total
    result = sps.chisquare(observed, f_exp=expected)
    note = _expected_warning(expected)
    if note:
        warnings.append(note)
    residuals = (observed - expected) / np.sqrt(expected)
    values = ChiSquareValues(
        kind="goodness_of_fit",
        statistic=float(result.statistic),
        df=len(levels) - 1,
        p_value=float(result.pvalue),
        rows=levels,
        columns=None,
        observed=[[int(o)] for o in observed],
        expected=[[float(e)] for e in expected],
        residuals=[[float(r)] for r in residuals],
        cramers_v=None,
        yates=False,
        fisher_p_value=None,
        odds_ratio=None,
        min_expected=float(expected.min()),
    )
    return stat_result(
        values.model_dump(by_alias=True),
        method="Pearson's chi-square goodness-of-fit test"
        + (" (uniform expected proportions)" if req.expected is None else ""),
        n=total,
        n_missing=total_rows - total,
        sampled=False,
        warnings=warnings,
    )
