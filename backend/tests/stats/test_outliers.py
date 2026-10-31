from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest
from scipy import stats as sps

from viz.contracts import SourceRef
from viz.stats.models import OutlierRequest, OutlierValues
from viz.stats.outliers import outliers

SOURCE = SourceRef(kind="dataset", id="d")


def _run(rel: Any, **kwargs: Any) -> tuple[Any, OutlierValues]:
    result = outliers(rel, OutlierRequest(source=SOURCE, **kwargs))
    return result, OutlierValues.model_validate(result.values)


@pytest.fixture
def frame() -> pd.DataFrame:
    rng = np.random.default_rng(17)
    n = 500
    x = rng.normal(50, 5, n)
    x[[3, 40, 77]] = [95.0, 4.0, 120.0]
    y = rng.exponential(2.0, n)
    y_obj: list[float | None] = y.tolist()
    y_obj[5] = None
    y_obj[6] = float("nan")
    return pd.DataFrame(
        {
            "x": x,
            "y": pd.array(y_obj, dtype="Float64").astype(float),
            "id": np.arange(n),
            "g": rng.choice(["a", "b"], n),
            "day": [dt.date(2024, 1, 1) + dt.timedelta(days=int(i % 30)) for i in range(n)],
        }
    )


def _table(frame: pd.DataFrame) -> pa.Table:
    return pa.Table.from_pandas(frame, preserve_index=False)


def test_iqr_fences_counts_and_rows(table_relation: Any, frame: pd.DataFrame) -> None:
    rel = table_relation(_table(frame))
    result, v = _run(rel, fields=["x", "y"], include=["id", "day"], max_rows=5)
    for summary, name in zip(v.summaries, ["x", "y"], strict=True):
        values = frame[name].replace([np.inf, -np.inf], np.nan).dropna().to_numpy()
        q1, med, q3 = np.quantile(values, [0.25, 0.5, 0.75])
        iqr = q3 - q1
        assert summary.field == name and summary.group is None
        assert summary.n == len(values)
        assert summary.center == pytest.approx(med, rel=1e-12)
        assert summary.spread == pytest.approx(iqr, rel=1e-12)
        assert summary.low == pytest.approx(q1 - 1.5 * iqr, rel=1e-12)
        assert summary.high == pytest.approx(q3 + 1.5 * iqr, rel=1e-12)
        assert summary.n_low == int((values < q1 - 1.5 * iqr).sum())
        assert summary.n_high == int((values > q3 + 1.5 * iqr).sum())
        assert summary.share == pytest.approx((summary.n_low + summary.n_high) / len(values))
    x_rows = [r for r in v.rows if r.field == "x"]
    assert [r.value for r in x_rows[:3]] == pytest.approx([120.0, 4.0, 95.0])
    top = x_rows[0]
    q1, q3 = np.quantile(frame["x"], [0.25, 0.75])
    assert top.side == "high" and top.score == pytest.approx((120.0 - q3) / (q3 - q1))
    assert top.row["id"] == 77 and top.row["day"] == "2024-01-18"
    low = next(r for r in x_rows if r.value == 4.0)
    assert low.side == "low" and low.score == pytest.approx((4.0 - q1) / (q3 - q1))
    assert len(x_rows) <= 5 and len([r for r in v.rows if r.field == "y"]) <= 5
    assert result.method == "Outliers by Tukey's fences (threshold 1.5, exact)"
    assert result.n == 500


def test_zscore_and_mad(table_relation: Any, frame: pd.DataFrame) -> None:
    rel = table_relation(_table(frame))
    x = frame["x"].to_numpy()
    _, z = _run(rel, fields=["x"], method="zscore", threshold=2.5)
    s = z.summaries[0]
    assert s.center == pytest.approx(x.mean(), rel=1e-12)
    assert s.spread == pytest.approx(x.std(ddof=1), rel=1e-12)
    scores = (x - x.mean()) / x.std(ddof=1)
    assert s.n_low + s.n_high == int((np.abs(scores) > 2.5).sum())
    assert z.rows[0].score == pytest.approx(scores.max(), rel=1e-9)

    _, m = _run(rel, fields=["x"], method="mad")
    s = m.summaries[0]
    spread = sps.median_abs_deviation(x, scale="normal")
    assert s.center == pytest.approx(np.median(x), rel=1e-12)
    assert s.spread == pytest.approx(spread, rel=1e-12)
    modified = (x - np.median(x)) / spread
    assert s.n_low + s.n_high == int((np.abs(modified) > 3.5).sum())
    assert m.rows[0].score == pytest.approx(modified.max(), rel=1e-9)


def test_grouped_with_a_null_group(table_relation: Any) -> None:
    rel = table_relation(
        {
            "v": [1.0, 2.0, 3.0, 4.0, 100.0, 10.0, 11.0, 12.0, 13.0, 0.0, 5.0, 5.0, 6.0, 7.0],
            "g": ["a"] * 5 + ["b"] * 5 + [None] * 4,
        }
    )
    _, v = _run(rel, fields=["v"], group_by="g")
    by_group = {tuple(s.group or []): s for s in v.summaries}
    assert set(by_group) == {("a",), ("b",), (None,)}
    assert (by_group[("a",)].n_high, by_group[("b",)].n_low) == (1, 1)
    assert by_group[(None,)].n == 4 and by_group[(None,)].n_low + by_group[(None,)].n_high == 0
    assert {(r.group[0] if r.group else None, r.value) for r in v.rows} == {
        ("a", 100.0),
        ("b", 0.0),
    }


def test_mad_zero_falls_back_to_mean_absolute_deviation(table_relation: Any) -> None:
    x = [5.0] * 8 + [6.0, 30.0]
    result, v = _run(table_relation({"x": x}), fields=["x"], method="mad")
    mean_ad = np.abs(np.array(x) - 5.0).mean() * np.sqrt(np.pi / 2)
    assert v.summaries[0].spread == pytest.approx(mean_ad)
    assert v.summaries[0].n_high == 1
    assert any("MAD = 0" in w for w in result.warnings)


def test_degenerate_cases(table_relation: Any) -> None:
    result, v = _run(table_relation({"x": [3.0] * 6}), fields=["x"], method="zscore")
    assert v.summaries[0].n_low + v.summaries[0].n_high == 0
    assert "No outliers found." in result.warnings
    small, _ = _run(table_relation({"x": [1.0, 2.0, 3.0, 50.0]}), fields=["x"], method="zscore")
    assert any("no |z| can exceed 3" in w for w in small.warnings)
    flat, f = _run(table_relation({"x": [1.0, 1.0, 1.0, 1.0, 2.0]}), fields=["x"])
    assert f.summaries[0].spread == 0 and f.summaries[0].n_high == 1
    assert f.rows[0].score is None and f.rows[0].value == 2.0
    assert flat.warnings == []
