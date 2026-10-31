"""NaN and +/-inf in a numeric grouping field count as missing, as everywhere else.

DuckDB groups NaNs together while NaN != NaN in Python, so a NaN key used to split rows,
crash (KeyError) or vanish. These run every analysis that groups by a field.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from scipy import stats as sps

from viz.contracts import (
    AnovaRequest,
    AnovaValues,
    ChiSquareRequest,
    ChiSquareValues,
    ConfidenceIntervalRequest,
    ConfidenceIntervalValues,
    DescribeRequest,
    FieldSummary,
    OutlierRequest,
    OutlierValues,
    RankTestRequest,
    RankTestValues,
    SourceRef,
)
from viz.stats.anova import anova
from viz.stats.chisquare import chi_square
from viz.stats.descriptive import describe
from viz.stats.intervals import confidence_intervals
from viz.stats.outliers import outliers
from viz.stats.ranktests import rank_test

SOURCE = SourceRef(kind="dataset", id="d")
NAN = float("nan")


@pytest.fixture
def table() -> pa.Table:
    rng = np.random.default_rng(3)
    g = [1.0] * 10 + [2.0] * 10 + [NAN] * 10 + [float("inf")] * 2
    side = (["l", "r"] * 16)[:32]
    return pa.table(
        {
            "g": pa.array(g, type=pa.float64()),
            "h": pa.array([0.5, 1.5] * 16, type=pa.float64()),
            "side": side,
            "y": rng.normal(0, 1, 32),
        }
    )


def _finite(table: pa.Table) -> tuple[np.ndarray, np.ndarray]:
    g = np.array(table["g"])
    keep = np.isfinite(g)
    return g[keep], np.array(table["y"])[keep]


def test_chi_square_drops_nan_keys(table_relation: Any, table: pa.Table) -> None:
    result = chi_square(
        table_relation(table), ChiSquareRequest(source=SOURCE, field="g", column_field="side")
    )
    v = ChiSquareValues.model_validate(result.values)
    assert v.rows == [1.0, 2.0] and v.observed == [[5, 5], [5, 5]]
    assert result.n == 20 and result.n_missing == 12


def test_anova_drops_nan_keys(table_relation: Any, table: pa.Table) -> None:
    rel = table_relation(table)
    one = anova(rel, AnovaRequest(source=SOURCE, field="y", factors=["g"]))
    v = AnovaValues.model_validate(one.values)
    g, y = _finite(table)
    assert v.table[0].statistic == pytest.approx(sps.f_oneway(y[g == 1], y[g == 2]).statistic)
    assert one.n == 20 and one.n_missing == 12
    two = anova(rel, AnovaRequest(source=SOURCE, field="y", factors=["g", "h"]))
    assert AnovaValues.model_validate(two.values).table[-1].df == 20 - 4


def test_kruskal_drops_nan_keys(table_relation: Any, table: pa.Table) -> None:
    result = rank_test(
        table_relation(table),
        RankTestRequest(source=SOURCE, kind="kruskal", field="y", group_field="g"),
    )
    v = RankTestValues.model_validate(result.values)
    g, y = _finite(table)
    assert [s.group for s in v.samples] == [[1.0], [2.0]]
    assert v.statistic == pytest.approx(sps.kruskal(y[g == 1], y[g == 2]).statistic)


def test_intervals_and_outliers_drop_nan_keys(table_relation: Any, table: pa.Table) -> None:
    rel = table_relation(table)
    ci = confidence_intervals(
        rel,
        ConfidenceIntervalRequest(source=SOURCE, fields=["y"], group_by="g", statistic="median"),
    )
    groups = [i.group for i in ConfidenceIntervalValues.model_validate(ci.values).intervals]
    assert groups == [[1.0], [2.0]]
    found = outliers(rel, OutlierRequest(source=SOURCE, fields=["y"], group_by="g"))
    summaries = OutlierValues.model_validate(found.values).summaries
    # NaN and inf keys join the missing (null) group, as null keys do elsewhere in outliers.
    assert sorted(s.n for s in summaries) == [10, 10, 12]
    assert [s.group for s in summaries if s.n == 12] == [[None]]


def test_cap_groups_merges_nan_and_inf_before_capping(table_relation: Any, table: pa.Table) -> None:
    # `g` has exactly 3 real groups once NaN and +inf merge into the null group: 1.0, 2.0 and
    # null (its 10 NaN + 2 inf rows). Before the fix, cap_groups grouped on the raw column, so
    # NaN and inf counted as 2 extra, separate groups (4 total) during capping - with
    # max_groups=3 that wasted a slot and spuriously dropped the smallest raw group (inf, 2
    # rows) with a "largest of 4" warning, even though nothing should have been capped. It also
    # meant the surviving "null" slot's reported size (from the raw NaN group alone, 10) didn't
    # match the true merged null group's size (12).
    found = outliers(
        table_relation(table),
        OutlierRequest(source=SOURCE, fields=["y"], group_by="g", max_groups=3),
    )
    assert not any("largest of" in w for w in found.warnings), (
        "only 3 groups once NaN/inf merge; none should be capped at max_groups=3"
    )
    v = OutlierValues.model_validate(found.values)
    null_group = next(s for s in v.summaries if s.group == [None])
    assert null_group.n == 12, "NULL + 10 NaN + 2 inf must all land in one null group of size 12"


def test_describe_groups_nan_keys_as_missing(table_relation: Any, table: pa.Table) -> None:
    result = describe(
        table_relation(table), DescribeRequest(source=SOURCE, fields=["y"], group_by=["g"])
    )
    summaries = [FieldSummary.model_validate(s) for s in result.values["summaries"]]
    by_group = {tuple(s.group) if s.group else None: s for s in summaries}
    g, y = _finite(table)
    assert by_group[(1.0,)].mean == pytest.approx(y[g == 1].mean())
    assert by_group[(None,)].count == 12
    assert all(s.median is not None for s in summaries)
