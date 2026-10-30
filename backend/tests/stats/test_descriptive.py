from __future__ import annotations

import datetime as dt
import json
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from scipy import stats as sps
from sqlglot import exp

from viz.contracts import DescribeRequest, FieldSummary, SourceRef
from viz.stats.descriptive import describe
from viz.stats.relation import col

SOURCE = SourceRef(kind="dataset", id="d")
SKEW_124 = float(sps.skew([1, 2, 4], bias=False))


def _request(fields: list[str], **kwargs: Any) -> DescribeRequest:
    return DescribeRequest(source=SOURCE, fields=fields, **kwargs)


def _summaries(result: Any) -> list[FieldSummary]:
    return [FieldSummary.model_validate(s) for s in result.values["summaries"]]


def _one(result: Any, field: str, group: list[Any] | None = None) -> FieldSummary:
    matches = [s for s in _summaries(result) if s.field == field and s.group == group]
    assert len(matches) == 1, matches
    return matches[0]


@pytest.fixture
def data() -> pa.Table:
    rng = np.random.default_rng(42)
    n = 400
    x = rng.gamma(2.0, 3.0, n)
    x_obj: list[float | None] = x.tolist()
    x_obj[5] = None
    x_obj[6] = float("nan")
    x_obj[7] = float("inf")
    x_obj[8] = float("-inf")
    groups = rng.choice(["a", "b", "c"], n).tolist()
    groups[:6] = [None] * 6
    return pa.table(
        {
            "x": pa.array(x_obj, type=pa.float64()),
            "k": pa.array(rng.integers(0, 12, n), type=pa.int64()),
            "g": pa.array(groups, type=pa.string()),
            "d": pa.array(
                [dt.date(2024, 1, 1) + dt.timedelta(days=int(i)) for i in rng.integers(0, 90, n)]
            ),
            "b": pa.array((rng.random(n) < 0.3).tolist()),
            "s": pa.array(rng.choice(["red", "green", "blue"], n, p=[0.2, 0.4, 0.4]).tolist()),
        }
    )


def _clean(x: pa.ChunkedArray) -> np.ndarray:
    values = np.asarray(x.to_numpy(zero_copy_only=False), dtype=float)
    return values[np.isfinite(values)]


def _check_numeric(summary: FieldSummary, values: np.ndarray, rows: int) -> None:
    assert summary.kind == "numeric"
    assert summary.count == len(values) and summary.missing == rows - len(values)
    assert summary.mean == pytest.approx(values.mean(), rel=1e-10)
    assert summary.std == pytest.approx(values.std(ddof=1), rel=1e-10)
    assert summary.variance == pytest.approx(values.var(ddof=1), rel=1e-10)
    assert summary.sem == pytest.approx(sps.sem(values), rel=1e-10)
    assert summary.sum == pytest.approx(values.sum(), rel=1e-10)
    assert summary.min == values.min() and summary.max == values.max()
    assert summary.skewness == pytest.approx(sps.skew(values, bias=False), rel=1e-8)
    assert summary.kurtosis == pytest.approx(sps.kurtosis(values, bias=False), rel=1e-8)
    q1, median, q3 = np.quantile(values, [0.25, 0.5, 0.75])
    assert (summary.q1, summary.median, summary.q3) == pytest.approx((q1, median, q3), rel=1e-12)
    assert summary.iqr == pytest.approx(q3 - q1, rel=1e-12)
    expected = np.quantile(values, [q.p for q in summary.quantiles])
    assert [q.value for q in summary.quantiles] == pytest.approx(expected.tolist(), rel=1e-12)


def test_numeric_matches_numpy_and_scipy(table_relation: Any, data: pa.Table) -> None:
    result = describe(table_relation(data), _request(["x", "k"], percentiles=[0.95, 0.05]))
    assert result.method == "Descriptive statistics (exact)"
    assert result.n == 400 and not result.sampled
    assert result.values["percentiles"] == [0.05, 0.95]
    x = _one(result, "x")
    _check_numeric(x, _clean(data.column("x")), 400)
    assert x.missing == 4  # null, NaN, +inf, -inf
    assert x.modes == [] and x.mode_count == 1 and x.distinct == 396

    k_values = data.column("k").to_numpy().astype(float)
    k = _one(result, "k")
    _check_numeric(k, k_values, 400)
    mode = sps.mode(k_values)
    assert k.modes[0] == mode.mode and k.mode_count == mode.count
    assert k.distinct == len(np.unique(k_values))
    assert any("'x': no value repeats" in w for w in result.warnings)


def test_groups_match_pandas(table_relation: Any, data: pa.Table) -> None:
    result = describe(table_relation(data), _request(["x"], group_by=["g"]))
    frame = data.to_pandas()
    frame["x"] = frame["x"].replace([np.inf, -np.inf], np.nan)
    summaries = _summaries(result)
    assert [s.group for s in summaries] == [None, ["a"], ["b"], ["c"], [None]]
    for key, part in frame.groupby("g", dropna=False):
        label = None if pd.isna(key) else key
        values = part["x"].dropna().to_numpy()
        _check_numeric(_one(result, "x", [label]), values, len(part))
    _check_numeric(_one(result, "x"), _clean(data.column("x")), 400)


def test_combined_pass_equals_separate_passes(table_relation: Any, data: pa.Table) -> None:
    rel = table_relation(data)
    combined = describe(rel, _request(["x", "k", "d", "s"], group_by=["g"]))
    groups_only = describe(
        rel, _request(["x", "k", "d", "s"], group_by=["g"], include_overall=False)
    )
    overall = describe(rel, _request(["x", "k", "d", "s"]))
    expected = [s for s in _summaries(groups_only)] + _summaries(overall)
    got = _summaries(combined)
    key = lambda s: (s.field, json.dumps(s.group))  # noqa: E731
    assert sorted(got, key=key) == sorted(expected, key=key)


def test_temporal_boolean_and_categorical(table_relation: Any, data: pa.Table) -> None:
    result = describe(table_relation(data), _request(["d", "b", "s"], percentiles=[0.1]))
    dates = sorted(data.column("d").to_pylist())
    d = _one(result, "d")
    assert d.kind == "temporal"
    assert d.min == dates[0].isoformat() and d.max == dates[-1].isoformat()
    ordinals = np.array([v.toordinal() for v in dates])
    for p, got in ((0.5, d.median), (0.25, d.q1), (0.1, d.quantiles[0].value)):
        expected = dt.date.fromordinal(int(np.quantile(ordinals, p, method="inverted_cdf")))
        assert got == expected.isoformat()
    assert d.mean is None and d.std is None and d.iqr is None

    b = _one(result, "b")
    flags = data.column("b").to_pylist()
    assert b.kind == "boolean" and b.mean == pytest.approx(sum(flags) / len(flags))
    assert b.modes == [False] and b.distinct == 2

    s = _one(result, "s")
    counts = pd.Series(data.column("s").to_pylist()).value_counts()
    assert s.kind == "categorical" and s.distinct == 3
    top = counts.max()
    assert s.mode_count == top and s.modes == sorted(counts[counts == top].index.tolist())
    assert s.mean is None and s.median is None


def test_tied_modes_are_sorted_and_capped(table_relation: Any) -> None:
    values = ["b", "a", "b", "a", "c", "d", "e", "f", "g"] + list("hijk") * 2
    result = describe(table_relation({"s": values}), _request(["s"]))
    s = _one(result, "s")
    assert s.mode_count == 2 and s.modes == ["a", "b", "h", "i", "j"]
    assert "'s': 6 values tie for the mode; showing 5 (overall)." in result.warnings


def test_filters_apply(table_relation: Any, data: pa.Table) -> None:
    where = exp.EQ(this=col("g"), expression=exp.Literal.string("b"))
    result = describe(table_relation(data, where=where), _request(["x"]))
    frame = data.to_pandas()
    values = frame.loc[frame["g"] == "b", "x"].replace([np.inf, -np.inf], np.nan).dropna()
    assert result.n == int((frame["g"] == "b").sum())
    _check_numeric(_one(result, "x"), values.to_numpy(), result.n)


@pytest.mark.parametrize(
    ("name", "first", "rest", "mean", "sd", "sd_rtol"),
    [
        ("NumAcc1", [10000001.0, 10000003.0, 10000002.0], None, 10000002.0, 1.0, 1e-12),
        ("NumAcc2", [1.2], (1.1, 1.3), 1.2, 0.1, 1e-12),
        ("NumAcc3", [1000000.2], (1000000.1, 1000000.3), 1000000.2, 0.1, 1e-8),
        ("NumAcc4", [10000000.2], (10000000.1, 10000000.3), 10000000.2, 0.1, 1e-7),
    ],
)
def test_nist_strd_univariate_accuracy(
    table_relation: Any,
    name: str,
    first: list[float],
    rest: tuple[float, float] | None,
    mean: float,
    sd: float,
    sd_rtol: float,
) -> None:
    """NIST StRD univariate summary statistics, certified mean and standard deviation."""
    values = first + (list(rest) * 500 if rest else [])
    result = describe(table_relation({"y": values}), _request(["y"]))
    y = _one(result, "y")
    assert y.mean == pytest.approx(mean, rel=1e-14), name
    assert y.std == pytest.approx(sd, rel=sd_rtol), name


@pytest.mark.parametrize(
    ("values", "expected", "warning"),
    [
        ([], {"count": 0, "mean": None, "std": None}, "'v': no values (overall)."),
        ([5.0], {"count": 1, "mean": 5.0, "std": None, "median": 5.0},
         "'v': standard deviation and variance need at least 2 values (overall)."),
        ([1.0, 2.0], {"std": pytest.approx(0.7071067811865476), "skewness": None, "kurtosis": None},
         "'v': skewness needs at least 3 values (overall)."),
        ([1.0, 2.0, 4.0], {"skewness": pytest.approx(SKEW_124), "kurtosis": None},
         "'v': kurtosis needs at least 4 values (overall)."),
        ([3.0] * 5, {"std": 0.0, "skewness": None, "kurtosis": None, "iqr": 0.0},
         "'v': all values are equal, so skewness and kurtosis are undefined (overall)."),
    ],
)  # fmt: skip
def test_degenerate_inputs(
    table_relation: Any, values: list[float], expected: dict[str, Any], warning: str
) -> None:
    table = pa.table({"v": pa.array([*values, None], type=pa.float64())})
    result = describe(table_relation(table), _request(["v"]))
    v = _one(result, "v")
    for key, value in expected.items():
        assert getattr(v, key) == value, key
    assert v.missing == 1
    assert warning in result.warnings
    json.loads(result.model_dump_json())  # never NaN


def test_no_rows_match(table_relation: Any, data: pa.Table) -> None:
    where = exp.EQ(this=col("g"), expression=exp.Literal.string("nope"))
    result = describe(table_relation(data, where=where), _request(["x", "s"], group_by=["g"]))
    assert result.n == 0
    assert "No rows match the filters." in result.warnings
    assert [(s.field, s.group, s.count) for s in _summaries(result)] == [
        ("x", None, 0),
        ("s", None, 0),
    ]


def test_max_groups_keeps_the_largest(table_relation: Any) -> None:
    groups = ["a"] * 5 + ["b"] * 4 + ["c"] * 3 + ["d"] * 2 + [None] * 4
    values = list(range(len(groups)))
    rel = table_relation({"g": groups, "v": pa.array(values, type=pa.float64())})
    result = describe(rel, _request(["v"], group_by=["g"], max_groups=2))
    assert [s.group for s in _summaries(result)] == [None, ["a"], ["b"]]
    assert _one(result, "v").count == len(values)  # overall covers every row
    assert _one(result, "v", ["b"]).mean == pytest.approx(6.5)
    assert "Showing the 2 largest of 5 groups." in result.warnings
    ties = describe(rel, _request(["v"], group_by=["g"], max_groups=3))
    assert [s.group for s in _summaries(ties)] == [None, ["a"], ["b"], [None]]


def test_overall_and_modes_can_be_skipped(table_relation: Any, data: pa.Table) -> None:
    result = describe(
        table_relation(data),
        _request(["k"], group_by=["g"], include_overall=False, include_mode=False),
    )
    summaries = _summaries(result)
    assert None not in [s.group for s in summaries]
    assert all(s.distinct is None and s.modes == [] and s.mode_count is None for s in summaries)
    assert result.n == 400


def test_group_by_date_gives_iso_keys(table_relation: Any) -> None:
    rel = table_relation(
        {
            "t": pa.array([dt.datetime(2024, 1, 1, 9, 30), dt.datetime(2024, 1, 1, 9, 30), None]),
            "v": [1.0, 3.0, 5.0],
        }
    )
    result = describe(rel, _request(["v", "t"], group_by=["t"]))
    assert _one(result, "v", ["2024-01-01T09:30:00"]).mean == 2.0
    assert _one(result, "v", [None]).mean == 5.0
    t = _one(result, "t")
    assert t.min == "2024-01-01T09:30:00" and t.modes == ["2024-01-01T09:30:00"]


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    st.lists(
        st.one_of(st.none(), st.floats(min_value=-1e6, max_value=1e6, allow_nan=False)),
        min_size=0,
        max_size=40,
    )
)
def test_fuzz_against_numpy(table_relation: Any, values: list[float | None]) -> None:
    table = pa.table({"v": pa.array(values, type=pa.float64())})
    v = _one(describe(table_relation(table), _request(["v"])), "v")
    clean = np.array([x for x in values if x is not None], dtype=float)
    assert v.count == len(clean) and v.missing == len(values) - len(clean)
    if len(clean) == 0:
        assert v.mean is None and v.median is None
        return
    assert v.mean == pytest.approx(clean.mean(), rel=1e-9, abs=1e-9)
    assert v.median == pytest.approx(np.median(clean), rel=1e-12, abs=1e-12)
    if len(clean) >= 2:
        assert v.std == pytest.approx(clean.std(ddof=1), rel=1e-7, abs=1e-6)
    else:
        assert v.std is None
