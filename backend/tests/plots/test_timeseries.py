"""Plots.md Sec4 (Time Series): known-answer tests for the server stat transforms.

Each plot is exercised through the real, globally discovered registry (`viz.plots.run_plot_stat`)
against a `Relation` built from a small in-memory parquet fixture, the same pattern
`backend/tests/chartdata/test_reduce.py` uses for the generic reduction kernels.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from statsmodels.tsa.stattools import acf as sm_acf
from statsmodels.tsa.stattools import pacf as sm_pacf

import viz.plots as plots
from viz.contracts import (
    ChartSpec,
    Column,
    ColumnSemantic,
    Encoding,
    PlotType,
    Reduction,
    SourceRef,
    Viewport,
)
from viz.engine import Engine
from viz.errors import InvalidSpecError
from viz.plots.base import TableOutput
from viz.stats.relation import Relation

# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    eng = Engine(temp_dir=tmp_path_factory.mktemp("duck-temp"), max_concurrent_queries=4)
    yield eng
    eng.close()


_SEMANTIC_TYPE = {
    ColumnSemantic.TEMPORAL: "TIMESTAMP",
    ColumnSemantic.QUANTITATIVE: "DOUBLE",
    ColumnSemantic.NOMINAL: "VARCHAR",
    ColumnSemantic.ORDINAL: "VARCHAR",
}


def relation_of(
    engine: Engine,
    data: pa.Table,
    path: Path,
    semantics: dict[str, ColumnSemantic],
) -> Relation:
    pq.write_table(data, path)
    from viz.chartdata.sql import parquet_source

    columns = {
        name: Column(
            name=name,
            type=_SEMANTIC_TYPE.get(sem, "VARCHAR"),
            semantic=sem,
            null_count=0,
            approx_distinct=data.num_rows,
        )
        for name, sem in semantics.items()
    }
    return Relation(engine, parquet_source(path), None, columns, data.num_rows, f"test@v{id(data)}")


def spec(
    plot_type: PlotType, params: dict[str, Any] | None = None, **channels: list[dict[str, Any]]
) -> ChartSpec:
    return ChartSpec.model_validate(
        {
            "id": "c1",
            "plotType": plot_type.value,
            "source": SourceRef(kind="dataset", id="d1").model_dump(),
            "encodings": {
                name: [Encoding.model_validate(e).model_dump(by_alias=True) for e in encs]
                for name, encs in channels.items()
            },
            "params": params or {},
        }
    )


def run(
    plot_type: PlotType,
    relation: Relation,
    params: dict[str, Any] | None = None,
    *,
    mark_cap: int = 50_000,
    viewport: Viewport | None = None,
    **channels: list[dict[str, Any]],
) -> TableOutput:
    out = plots.run_plot_stat(
        spec(plot_type, params, **channels),
        relation,
        viewport=viewport or Viewport(),
        mark_cap=mark_cap,
    )
    assert isinstance(out, TableOutput)
    return out


# ---------------------------------------------------------------------------------------------
# Timeseries Line
# ---------------------------------------------------------------------------------------------


def _daily_series(n: int, start: date = date(2024, 1, 1)) -> pa.Table:
    days = [datetime.combine(start + timedelta(days=i), datetime.min.time()) for i in range(n)]
    values = [float(i) for i in range(n)]
    return pa.table({"t": pa.array(days, pa.timestamp("us")), "v": values})


def test_timeseries_line_sums_raw_days(engine: Engine, tmp_path: Path) -> None:
    rel = relation_of(
        engine,
        _daily_series(5),
        tmp_path / "a.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(
        PlotType.TIMESERIES_LINE,
        rel,
        x=[{"field": "t"}],
        y=[{"field": "v", "agg": "sum"}],
    )
    assert out.table.column("x").to_pylist() == [datetime(2024, 1, 1 + i) for i in range(5)]
    assert out.table.column("y0").to_pylist() == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert out.reduction == Reduction.AGGREGATED
    assert out.method == "group_by"
    assert out.rows_matched == 5


def test_timeseries_line_auto_buckets_when_over_cap(engine: Engine, tmp_path: Path) -> None:
    # 180 one-minute points: a mark cap of 20 forces an auto-picked calendar bucket.
    n = 180
    start = datetime(2024, 1, 1, 0, 0, tzinfo=None)
    ts = [start + timedelta(minutes=i) for i in range(n)]
    data = pa.table({"t": pa.array(ts, pa.timestamp("us")), "v": [1.0] * n})
    rel = relation_of(
        engine,
        data,
        tmp_path / "b.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(
        PlotType.TIMESERIES_LINE,
        rel,
        mark_cap=20,
        viewport=Viewport(width_px=800),
        x=[{"field": "t"}],
        y=[{"field": "v", "agg": "sum"}],
    )
    assert "timeUnit" in out.params
    # Every raw point is worth 1.0, so no matter how it's bucketed the total is conserved.
    assert sum(out.table.column("y0").to_pylist()) == pytest.approx(float(n))
    assert out.rows_matched == n
    assert out.table.num_rows <= 20 or out.reduction == Reduction.DOWNSAMPLED


def test_timeseries_line_color_truncates_to_max_series(engine: Engine, tmp_path: Path) -> None:
    n_colors = 5
    rows_t, rows_c, rows_v = [], [], []
    base = datetime(2024, 1, 1)
    for c in range(n_colors):
        for d in range(3):
            rows_t.append(base + timedelta(days=d))
            rows_c.append(f"g{c}")
            rows_v.append(float((c + 1) * 10))  # bigger color index -> bigger total
    data = pa.table({"t": pa.array(rows_t, pa.timestamp("us")), "c": rows_c, "v": rows_v})
    rel = relation_of(
        engine,
        data,
        tmp_path / "c.parquet",
        {
            "t": ColumnSemantic.TEMPORAL,
            "c": ColumnSemantic.NOMINAL,
            "v": ColumnSemantic.QUANTITATIVE,
        },
    )
    out = run(
        PlotType.TIMESERIES_LINE,
        rel,
        {"maxSeries": 2},
        x=[{"field": "t"}],
        y=[{"field": "v", "agg": "sum"}],
        color=[{"field": "c"}],
    )
    colors = set(out.table.column("color").to_pylist())
    assert colors == {"g3", "g4"}  # the two largest totals
    assert out.params.get("truncatedSeries") is True


def test_timeseries_line_needs_a_field_for_sum(engine: Engine, tmp_path: Path) -> None:
    rel = relation_of(
        engine,
        _daily_series(3),
        tmp_path / "d.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    # The generic channel validator rejects a field-less encoding before the plot's own
    # (defense-in-depth) "agg needs a field" check ever runs.
    with pytest.raises(InvalidSpecError, match="Choose a field for 'y'"):
        run(PlotType.TIMESERIES_LINE, rel, x=[{"field": "t"}], y=[{"agg": "sum"}])


# ---------------------------------------------------------------------------------------------
# Seasonal
# ---------------------------------------------------------------------------------------------


def test_seasonal_groups_by_month_and_year(engine: Engine, tmp_path: Path) -> None:
    rows_t, rows_v = [], []
    for year in (2023, 2024):
        for month in range(1, 13):
            rows_t.append(datetime(year, month, 15))
            rows_v.append(float(year * 100 + month))
    data = pa.table({"t": pa.array(rows_t, pa.timestamp("us")), "v": rows_v})
    rel = relation_of(
        engine,
        data,
        tmp_path / "e.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(PlotType.SEASONAL, rel, x=[{"field": "t"}], y=[{"field": "v", "agg": "mean"}])
    xs = out.table.column("x").to_pylist()
    colors = out.table.column("color").to_pylist()
    y0 = out.table.column("y0").to_pylist()
    # x (period) is the primary sort key, numerically ascending, regardless of how many cycles
    # (years) exist: cartesianSeries dedups x in first-seen row order on the frontend, so x must
    # come first in the SQL ORDER BY or a cycle that doesn't start in January would scramble it.
    assert xs == sorted(xs)
    assert set(xs) == set(range(1, 13))
    assert set(colors) == {2023, 2024}
    by_key = dict(zip(zip(xs, colors, strict=False), y0, strict=True))
    assert by_key[(1, 2023)] == pytest.approx(2023 * 100 + 1)
    assert by_key[(12, 2024)] == pytest.approx(2024 * 100 + 12)
    assert out.rows_matched == 24


def test_seasonal_x_axis_stays_in_order_when_a_cycle_starts_mid_year(
    engine: Engine, tmp_path: Path
) -> None:
    """A cycle (year) that only has data from July onward must not scramble the shared x axis
    into Jul..Dec, Jan..Jun: every sample dataset happens to start in January, which is why an
    earlier version of this bug went uncaught."""
    rows_t, rows_v = [], []
    for month in range(7, 13):  # 2023: only Jul-Dec
        rows_t.append(datetime(2023, month, 1))
        rows_v.append(1.0)
    for month in range(1, 13):  # 2024: full year
        rows_t.append(datetime(2024, month, 1))
        rows_v.append(2.0)
    data = pa.table({"t": pa.array(rows_t, pa.timestamp("us")), "v": rows_v})
    rel = relation_of(
        engine,
        data,
        tmp_path / "e2.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(PlotType.SEASONAL, rel, x=[{"field": "t"}], y=[{"field": "v"}])
    xs = out.table.column("x").to_pylist()
    assert xs == sorted(xs)
    assert xs[0] == 1 and xs[-1] == 12


def test_seasonal_cycle_month_does_not_merge_across_years(engine: Engine, tmp_path: Path) -> None:
    """cycle='month' must group by the actual calendar month (2022-01 distinct from 2023-01),
    not date_part('month', ...), which would average every January together."""
    data = pa.table(
        {
            "t": pa.array(
                [datetime(2022, 1, 15), datetime(2022, 1, 20), datetime(2023, 1, 10)],
                pa.timestamp("us"),
            ),
            "v": [10.0, 20.0, 100.0],
        }
    )
    rel = relation_of(
        engine,
        data,
        tmp_path / "e3.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(
        PlotType.SEASONAL,
        rel,
        {"period": "day_of_month", "cycle": "month"},
        x=[{"field": "t"}],
        y=[{"field": "v", "agg": "mean"}],
    )
    colors = out.table.column("color").to_pylist()
    assert len(set(colors)) == 2  # 2022-01 and 2023-01 stay separate series
    y0 = out.table.column("y0").to_pylist()
    assert 100.0 in y0  # 2023-01's lone point is never averaged with 2022-01's


def test_seasonal_counts_rows_when_y_is_left_empty(engine: Engine, tmp_path: Path) -> None:
    """The UI hint says 'leave empty to count rows'; it must not 500."""
    rel = relation_of(
        engine,
        _daily_series(14),
        tmp_path / "e4.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(PlotType.SEASONAL, rel, x=[{"field": "t"}])
    assert sum(out.table.column("y0").to_pylist()) == 14
    assert out.fields["y0"] == "Count"


def test_seasonal_rejects_matching_period_and_cycle(engine: Engine, tmp_path: Path) -> None:
    rel = relation_of(
        engine,
        _daily_series(3),
        tmp_path / "f.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    with pytest.raises(InvalidSpecError, match="different units of time"):
        run(
            PlotType.SEASONAL,
            rel,
            {"period": "month", "cycle": "month"},
            x=[{"field": "t"}],
            y=[{"field": "v"}],
        )


# ---------------------------------------------------------------------------------------------
# Lag
# ---------------------------------------------------------------------------------------------


def test_lag_known_answer(engine: Engine, tmp_path: Path) -> None:
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    data = pa.table(
        {
            "t": pa.array(
                [datetime(2024, 1, 1) + timedelta(days=i) for i in range(len(values))],
                pa.timestamp("us"),
            ),
            "v": values,
        }
    )
    rel = relation_of(
        engine,
        data,
        tmp_path / "g.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(PlotType.LAG, rel, {"lag": 1}, x=[{"field": "t"}], y=[{"field": "v"}])
    pairs = sorted(
        zip(out.table.column("x").to_pylist(), out.table.column("y0").to_pylist(), strict=False)
    )
    assert pairs == [(10.0, 20.0), (20.0, 30.0), (30.0, 40.0), (40.0, 50.0)]
    assert out.rows_matched == 4
    assert out.reduction == Reduction.NONE


def test_lag_samples_when_over_cap(engine: Engine, tmp_path: Path) -> None:
    n = 500
    data = pa.table(
        {
            "t": pa.array(
                [datetime(2024, 1, 1) + timedelta(minutes=i) for i in range(n)], pa.timestamp("us")
            ),
            "v": [float(i) for i in range(n)],
        }
    )
    rel = relation_of(
        engine,
        data,
        tmp_path / "h.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(PlotType.LAG, rel, {"lag": 1}, mark_cap=50, x=[{"field": "t"}], y=[{"field": "v"}])
    assert out.table.num_rows == 50
    assert out.reduction == Reduction.SAMPLED
    assert out.rows_matched == n - 1
    assert out.warnings


# ---------------------------------------------------------------------------------------------
# ACF / PACF
# ---------------------------------------------------------------------------------------------


def _regular_daily(values: Sequence[float], start: date = date(2024, 1, 1)) -> pa.Table:
    days = [
        datetime.combine(start + timedelta(days=i), datetime.min.time()) for i in range(len(values))
    ]
    return pa.table({"t": pa.array(days, pa.timestamp("us")), "v": values})


def test_acf_matches_statsmodels_on_a_gap_free_series(engine: Engine, tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    n = 80
    values = list(np.cumsum(rng.normal(size=n)))
    rel = relation_of(
        engine,
        _regular_daily(values),
        tmp_path / "i.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(
        PlotType.ACF, rel, {"lags": 10, "ciLevel": 0.95}, x=[{"field": "t"}], y=[{"field": "v"}]
    )
    expected_vals, expected_ci = sm_acf(
        np.asarray(values),
        nlags=10,
        alpha=0.05,
        fft=True,
        bartlett_confint=True,
        result_object=False,
    )
    assert out.table.column("x").to_pylist() == list(range(11))
    np.testing.assert_allclose(out.table.column("y0").to_pylist(), expected_vals)
    # The band is centered on zero (confint - estimate), like statsmodels' own plot_acf, not on
    # the bar's own value: "bar outside the band = significant" has to actually be checkable.
    np.testing.assert_allclose(
        out.table.column("y0_lo").to_pylist(), expected_ci[:, 0] - expected_vals
    )
    np.testing.assert_allclose(
        out.table.column("y0_hi").to_pylist(), expected_ci[:, 1] - expected_vals
    )
    assert out.warnings == []


def test_pacf_matches_statsmodels(engine: Engine, tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    n = 80
    values = list(np.cumsum(rng.normal(size=n)))
    rel = relation_of(
        engine,
        _regular_daily(values),
        tmp_path / "j.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(
        PlotType.PACF, rel, {"lags": 10, "ciLevel": 0.95}, x=[{"field": "t"}], y=[{"field": "v"}]
    )
    expected_vals, expected_ci = sm_pacf(
        np.asarray(values), nlags=10, alpha=0.05, result_object=False
    )
    np.testing.assert_allclose(out.table.column("y0").to_pylist(), expected_vals)
    np.testing.assert_allclose(
        out.table.column("y0_lo").to_pylist(), expected_ci[:, 0] - expected_vals
    )
    np.testing.assert_allclose(
        out.table.column("y0_hi").to_pylist(), expected_ci[:, 1] - expected_vals
    )


def test_acf_fills_gaps_and_warns(engine: Engine, tmp_path: Path) -> None:
    # Day 3 is missing: its bucket is carried forward from day 2's value (10.0).
    values = [1.0, 5.0, 10.0, 20.0]
    days = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 5)]
    data = pa.table(
        {
            "t": pa.array(
                [datetime.combine(d, datetime.min.time()) for d in days], pa.timestamp("us")
            ),
            "v": values,
        }
    )
    rel = relation_of(
        engine,
        data,
        tmp_path / "k.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(PlotType.ACF, rel, {"lags": 2}, x=[{"field": "t"}], y=[{"field": "v"}])
    filled_series = np.asarray([1.0, 5.0, 10.0, 10.0, 20.0])
    expected_vals, _expected_ci = sm_acf(
        filled_series, nlags=2, alpha=0.05, fft=True, bartlett_confint=True, result_object=False
    )
    np.testing.assert_allclose(out.table.column("y0").to_pylist(), expected_vals)
    assert any("carried forward" in w for w in out.warnings)


def test_acf_rejects_too_few_points(engine: Engine, tmp_path: Path) -> None:
    rel = relation_of(
        engine,
        _regular_daily([1.0, 2.0]),
        tmp_path / "l.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    with pytest.raises(InvalidSpecError, match="Not enough points"):
        run(PlotType.ACF, rel, x=[{"field": "t"}], y=[{"field": "v"}])


def test_pacf_caps_lags_at_half_the_series(engine: Engine, tmp_path: Path) -> None:
    """statsmodels' pacf raises outright once nlags > n // 2; an explicit lags request past
    that must be capped, not propagate into a 500."""
    rng = np.random.default_rng(2)
    values = list(np.cumsum(rng.normal(size=50)))
    rel = relation_of(
        engine,
        _regular_daily(values),
        tmp_path / "pacf_cap.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(PlotType.PACF, rel, {"lags": 40}, x=[{"field": "t"}], y=[{"field": "v"}])
    assert out.params["lags"] == 25  # 50 // 2
    assert out.table.num_rows == 26


def test_acf_allows_more_lags_than_pacf_would(engine: Engine, tmp_path: Path) -> None:
    """ACF has no n // 2 restriction, unlike PACF; the same explicit request is fine here."""
    rng = np.random.default_rng(3)
    values = list(np.cumsum(rng.normal(size=50)))
    rel = relation_of(
        engine,
        _regular_daily(values),
        tmp_path / "acf_cap.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(PlotType.ACF, rel, {"lags": 40}, x=[{"field": "t"}], y=[{"field": "v"}])
    assert out.params["lags"] == 40
    assert out.table.num_rows == 41


def test_acf_rejects_a_constant_series(engine: Engine, tmp_path: Path) -> None:
    rel = relation_of(
        engine,
        _regular_daily([5.0] * 10),
        tmp_path / "const.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    with pytest.raises(InvalidSpecError, match="constant"):
        run(PlotType.ACF, rel, x=[{"field": "t"}], y=[{"field": "v"}])
    with pytest.raises(InvalidSpecError, match="constant"):
        run(PlotType.PACF, rel, x=[{"field": "t"}], y=[{"field": "v"}])


def test_acf_grid_is_bounded_for_a_few_widely_spaced_rows(engine: Engine, tmp_path: Path) -> None:
    """A handful of rows a minute apart but decades apart overall must not build a
    multi-million-bucket regular grid: the native cadence (1 minute) gets coarsened."""
    data = pa.table(
        {
            "t": pa.array(
                [
                    datetime(1990, 1, 1, 0, 0),
                    datetime(1990, 1, 1, 0, 1),
                    datetime(2024, 1, 1, 0, 0),
                    datetime(2024, 1, 1, 0, 1),
                ],
                pa.timestamp("us"),
            ),
            "v": [1.0, 2.0, 3.0, 4.0],
        }
    )
    rel = relation_of(
        engine,
        data,
        tmp_path / "wide.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    started = time.perf_counter()
    out = run(PlotType.ACF, rel, {"lags": 1}, x=[{"field": "t"}], y=[{"field": "v"}])
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0
    assert out.table.num_rows == 2
    assert any("coarser than the data's own cadence" in w for w in out.warnings)


def test_acf_warns_loudly_on_a_high_filled_fraction(engine: Engine, tmp_path: Path) -> None:
    """Business-day-only data regularized to a daily grid fills every weekend: ~2/7 of the
    series feeding the ACF would be synthetic, which must be called out, not silent."""
    days: list[date] = []
    d = date(2024, 1, 1)  # a Monday
    while len(days) < 40:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    data = pa.table(
        {
            "t": pa.array(
                [datetime.combine(d, datetime.min.time()) for d in days], pa.timestamp("us")
            ),
            "v": [float(i % 5) for i in range(len(days))],
        }
    )
    rel = relation_of(
        engine,
        data,
        tmp_path / "business.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(PlotType.ACF, rel, {"lags": 5}, x=[{"field": "t"}], y=[{"field": "v"}])
    assert any("large share of filled" in w for w in out.warnings)


# ---------------------------------------------------------------------------------------------
# Moving Average
# ---------------------------------------------------------------------------------------------


def test_moving_average_matches_hand_computed_window(engine: Engine, tmp_path: Path) -> None:
    values = [1.0, 2.0, 3.0, 4.0, 10.0]
    rel = relation_of(
        engine,
        _regular_daily(values),
        tmp_path / "m.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(
        PlotType.MOVING_AVERAGE,
        rel,
        {"window": 3},
        x=[{"field": "t"}],
        y=[{"field": "v", "agg": "sum"}],
    )
    assert out.table.column("y0").to_pylist() == values
    expected = [1.0, 1.5, 2.0, 3.0, 17.0 / 3.0]
    np.testing.assert_allclose(out.table.column("y1").to_pylist(), expected)
    assert out.params["window"] == 3


def test_moving_average_is_per_color_group(engine: Engine, tmp_path: Path) -> None:
    base = datetime(2024, 1, 1)
    rows_t = [base + timedelta(days=i) for i in range(3)] * 2
    rows_c = ["a"] * 3 + ["b"] * 3
    rows_v = [1.0, 2.0, 3.0, 100.0, 200.0, 300.0]
    data = pa.table({"t": pa.array(rows_t, pa.timestamp("us")), "c": rows_c, "v": rows_v})
    rel = relation_of(
        engine,
        data,
        tmp_path / "n.parquet",
        {
            "t": ColumnSemantic.TEMPORAL,
            "c": ColumnSemantic.NOMINAL,
            "v": ColumnSemantic.QUANTITATIVE,
        },
    )
    out = run(
        PlotType.MOVING_AVERAGE,
        rel,
        {"window": 2},
        x=[{"field": "t"}],
        y=[{"field": "v", "agg": "sum"}],
        color=[{"field": "c"}],
    )
    by_group: dict[str, list[float]] = {"a": [], "b": []}
    for c, y1 in zip(
        out.table.column("color").to_pylist(), out.table.column("y1").to_pylist(), strict=True
    ):
        by_group[c].append(y1)
    np.testing.assert_allclose(by_group["a"], [1.0, 1.5, 2.5])
    np.testing.assert_allclose(by_group["b"], [100.0, 150.0, 250.0])


def test_moving_average_mark_cap_accounts_for_both_series(engine: Engine, tmp_path: Path) -> None:
    """Each output row produces two marks (y0 and y1). Before this was fixed, a row count that
    fit the *un-halved* cap (e.g. 60 rows against a cap of 100) sailed through this module's own
    reduction untouched, and only got cut down downstream by chartdata's generic per-row-measure
    guard -- which just keeps the first N rows, silently dropping the back half of the time
    range. With the cap correctly halved here, the data is reduced properly (auto-bucketed or
    MinMaxLTTB-downsampled) before that point, so the full span survives."""
    n = 60
    values = [float(i) for i in range(n)]
    rel = relation_of(
        engine,
        _regular_daily(values),
        tmp_path / "ma_cap.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(
        PlotType.MOVING_AVERAGE,
        rel,
        {"window": 3},
        mark_cap=100,
        viewport=Viewport(width_px=800),
        x=[{"field": "t"}],
        y=[{"field": "v", "agg": "sum"}],
    )
    xs = out.table.column("x").to_pylist()
    # The tail of the range must survive: a naive "keep the first N rows" truncation (the old
    # bug) would cut off around day ~50 of 59 (allow slack for calendar-bucket flooring).
    assert max(xs) >= datetime(2024, 1, 1) + timedelta(days=n - 10)
    assert out.table.num_rows * 2 <= 100


# ---------------------------------------------------------------------------------------------
# Candlestick
# ---------------------------------------------------------------------------------------------


def test_candlestick_ohlc_per_raw_row(engine: Engine, tmp_path: Path) -> None:
    days = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(3)]
    data = pa.table(
        {
            "t": pa.array(days, pa.timestamp("us")),
            "o": [10.0, 11.0, 9.0],
            "h": [12.0, 13.0, 10.0],
            "lo": [9.0, 10.0, 8.0],
            "cl": [11.0, 9.5, 9.5],
        }
    )
    rel = relation_of(
        engine,
        data,
        tmp_path / "o.parquet",
        {
            "t": ColumnSemantic.TEMPORAL,
            "o": ColumnSemantic.QUANTITATIVE,
            "h": ColumnSemantic.QUANTITATIVE,
            "lo": ColumnSemantic.QUANTITATIVE,
            "cl": ColumnSemantic.QUANTITATIVE,
        },
    )
    out = run(
        PlotType.CANDLESTICK,
        rel,
        x=[{"field": "t"}],
        open=[{"field": "o"}],
        high=[{"field": "h"}],
        low=[{"field": "lo"}],
        close=[{"field": "cl"}],
    )
    assert out.table.column("open").to_pylist() == [10.0, 11.0, 9.0]
    assert out.table.column("high").to_pylist() == [12.0, 13.0, 10.0]
    assert out.table.column("low").to_pylist() == [9.0, 10.0, 8.0]
    assert out.table.column("close").to_pylist() == [11.0, 9.5, 9.5]
    assert out.warnings == []


def test_candlestick_buckets_weekly_and_sums_volume(engine: Engine, tmp_path: Path) -> None:
    # Two full weeks of daily bars: open = Monday's open, close = Sunday's close.
    days = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(14)]  # Mon 2024-01-01
    data = pa.table(
        {
            "t": pa.array(days, pa.timestamp("us")),
            "o": [float(10 + i) for i in range(14)],
            "h": [float(20 + i) for i in range(14)],
            "lo": [float(i) for i in range(14)],
            "cl": [float(15 + i) for i in range(14)],
            "vol": [100.0] * 14,
        }
    )
    rel = relation_of(
        engine,
        data,
        tmp_path / "p.parquet",
        {
            "t": ColumnSemantic.TEMPORAL,
            "o": ColumnSemantic.QUANTITATIVE,
            "h": ColumnSemantic.QUANTITATIVE,
            "lo": ColumnSemantic.QUANTITATIVE,
            "cl": ColumnSemantic.QUANTITATIVE,
            "vol": ColumnSemantic.QUANTITATIVE,
        },
    )
    out = run(
        PlotType.CANDLESTICK,
        rel,
        {},
        x=[{"field": "t", "timeUnit": "week"}],
        open=[{"field": "o"}],
        high=[{"field": "h"}],
        low=[{"field": "lo"}],
        close=[{"field": "cl"}],
        volume=[{"field": "vol"}],
    )
    assert out.table.num_rows == 2
    assert out.table.column("volume").to_pylist() == [700.0, 700.0]
    assert out.table.column("open").to_pylist()[0] == 10.0  # Monday week 1
    assert out.table.column("close").to_pylist()[0] == 15.0 + 6  # Sunday week 1
    assert out.table.column("high").to_pylist()[0] == 20.0 + 6
    assert out.table.column("low").to_pylist()[0] == 0.0


def test_candlestick_warns_on_duplicate_timestamps(engine: Engine, tmp_path: Path) -> None:
    same_day = datetime(2024, 1, 1)
    data = pa.table(
        {
            "t": pa.array([same_day, same_day], pa.timestamp("us")),
            "o": [10.0, 50.0],
            "h": [12.0, 55.0],
            "lo": [9.0, 49.0],
            "cl": [11.0, 51.0],
        }
    )
    rel = relation_of(
        engine,
        data,
        tmp_path / "q.parquet",
        {
            "t": ColumnSemantic.TEMPORAL,
            "o": ColumnSemantic.QUANTITATIVE,
            "h": ColumnSemantic.QUANTITATIVE,
            "lo": ColumnSemantic.QUANTITATIVE,
            "cl": ColumnSemantic.QUANTITATIVE,
        },
    )
    out = run(
        PlotType.CANDLESTICK,
        rel,
        x=[{"field": "t"}],
        open=[{"field": "o"}],
        high=[{"field": "h"}],
        low=[{"field": "lo"}],
        close=[{"field": "cl"}],
    )
    assert out.table.num_rows == 1
    assert any("same timestamp" in w for w in out.warnings)


def test_candlestick_auto_bucketing_is_aggregated_not_downsampled(
    engine: Engine, tmp_path: Path
) -> None:
    """Coarsening the bucket is still exact OHLC math (arg_min/max), never an approximation, so
    it must never be reported as Reduction.DOWNSAMPLED (which implies point-sampling/LTTB)."""
    n = 40
    days = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    data = pa.table(
        {
            "t": pa.array(days, pa.timestamp("us")),
            "o": [float(i) for i in range(n)],
            "h": [float(i + 1) for i in range(n)],
            "lo": [float(i) for i in range(n)],
            "cl": [float(i) for i in range(n)],
        }
    )
    rel = relation_of(
        engine,
        data,
        tmp_path / "cs_auto.parquet",
        {
            "t": ColumnSemantic.TEMPORAL,
            "o": ColumnSemantic.QUANTITATIVE,
            "h": ColumnSemantic.QUANTITATIVE,
            "lo": ColumnSemantic.QUANTITATIVE,
            "cl": ColumnSemantic.QUANTITATIVE,
        },
    )
    out = run(
        PlotType.CANDLESTICK,
        rel,
        mark_cap=10,
        x=[{"field": "t"}],
        open=[{"field": "o"}],
        high=[{"field": "h"}],
        low=[{"field": "lo"}],
        close=[{"field": "cl"}],
    )
    assert "timeUnit" in out.params  # confirms auto-bucketing actually happened
    assert out.reduction == Reduction.AGGREGATED


def test_timeseries_line_rows_matched_is_accurate_with_an_explicit_time_unit_over_cap(
    engine: Engine, tmp_path: Path
) -> None:
    """An explicit time_unit that still produces more groups than the cap must report the true
    total (via relation.count()), not the sum of a cap+1-row probe's own __viz_n column."""
    n = 500
    data = pa.table(
        {
            "t": pa.array(
                [datetime(2024, 1, 1) + timedelta(days=i) for i in range(n)], pa.timestamp("us")
            ),
            "v": [1.0] * n,
        }
    )
    rel = relation_of(
        engine,
        data,
        tmp_path / "explicit_over_cap.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(
        PlotType.TIMESERIES_LINE,
        rel,
        mark_cap=40,
        x=[{"field": "t", "timeUnit": "day"}],
        y=[{"field": "v", "agg": "sum"}],
    )
    assert out.rows_matched == n


# ---------------------------------------------------------------------------------------------
# Calendar Heatmap
# ---------------------------------------------------------------------------------------------


def test_calendar_heatmap_sums_per_day(engine: Engine, tmp_path: Path) -> None:
    data = pa.table(
        {
            "t": pa.array(
                [
                    datetime(2024, 1, 1, 1),
                    datetime(2024, 1, 1, 23),
                    datetime(2024, 1, 2, 12),
                ],
                pa.timestamp("us"),
            ),
            "v": [1.0, 2.0, 5.0],
        }
    )
    rel = relation_of(
        engine,
        data,
        tmp_path / "r.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(PlotType.CALENDAR_HEATMAP, rel, x=[{"field": "t"}], y=[{"field": "v", "agg": "sum"}])
    assert out.table.column("x").to_pylist() == [date(2024, 1, 1), date(2024, 1, 2)]
    assert out.table.column("y0").to_pylist() == [3.0, 5.0]
    assert out.rows_matched == 3


def test_calendar_heatmap_counts_rows_with_no_y(engine: Engine, tmp_path: Path) -> None:
    data = pa.table(
        {"t": pa.array([datetime(2024, 1, 1), datetime(2024, 1, 1)], pa.timestamp("us"))}
    )
    rel = relation_of(engine, data, tmp_path / "s.parquet", {"t": ColumnSemantic.TEMPORAL})
    out = run(PlotType.CALENDAR_HEATMAP, rel, x=[{"field": "t"}])
    assert out.table.column("y0").to_pylist() == [2.0]


# ---------------------------------------------------------------------------------------------
# Time zone / DST: pinning the *current* behavior (open question for the lead -- CLAUDE.md's
# own brief for this family says "respect the dataset's time zone", but there is no
# dataset-timezone contract field to respect: naive TIMESTAMP columns bucket on their literal
# wall-clock numbers, and TIMESTAMPTZ columns bucket on the UTC calendar day, because the engine
# always runs its sessions in UTC (CLAUDE.md Sec12). These tests document that behavior so a
# future contract change (e.g. a per-dataset timezone) is a deliberate decision, not a silent
# regression.
# ---------------------------------------------------------------------------------------------


def test_naive_timestamps_bucket_as_literal_wall_clock_across_a_dst_transition(
    engine: Engine, tmp_path: Path
) -> None:
    """US clocks spring forward at 2024-03-10 02:00 local (2:00-3:00 never happens locally).
    A naive TIMESTAMP column carries no timezone, so DuckDB (and this plot) treats the hours as
    plain numbers: nothing merges or drops them, even the "02:00" that a US local clock would
    never actually show. This is the current, deliberate behavior to pin down, not a claim that
    DST is handled "correctly" in any locale-aware sense."""
    hours = [datetime(2024, 3, 10, h) for h in (0, 1, 2, 3, 4)]
    data = pa.table({"t": pa.array(hours, pa.timestamp("us")), "v": [1.0] * len(hours)})
    rel = relation_of(
        engine,
        data,
        tmp_path / "dst.parquet",
        {"t": ColumnSemantic.TEMPORAL, "v": ColumnSemantic.QUANTITATIVE},
    )
    out = run(
        PlotType.TIMESERIES_LINE,
        rel,
        x=[{"field": "t", "timeUnit": "hour"}],
        y=[{"field": "v", "agg": "sum"}],
    )
    # One bucket per literal hour, including 02:00 -- no DST-aware merging or gap.
    assert out.table.column("x").to_pylist() == hours
    assert out.table.column("y0").to_pylist() == [1.0] * len(hours)


def test_timestamptz_buckets_on_the_utc_calendar_day(engine: Engine, tmp_path: Path) -> None:
    """2024-03-09T23:30:00-05:00 is 2024-03-10T04:30:00Z: the daily bucket (calendar_heatmap,
    and any other plot's day-level bucketing) lands on the UTC date (03-10), not the date the
    original local wall clock would show (03-09), because the engine's sessions run in UTC."""
    # pyarrow timestamps always store the UTC instant; the "tz" field is a display label only,
    # so this literal IS "2024-03-09T23:30:00-05:00".
    utc_instant = datetime(2024, 3, 10, 4, 30, tzinfo=UTC)
    data = pa.table({"t": pa.array([utc_instant], pa.timestamp("us", tz="UTC")), "v": [1.0]})
    columns = {
        "t": Column(
            name="t",
            type="TIMESTAMPTZ",
            semantic=ColumnSemantic.TEMPORAL,
            null_count=0,
            approx_distinct=1,
        ),
        "v": Column(
            name="v",
            type="DOUBLE",
            semantic=ColumnSemantic.QUANTITATIVE,
            null_count=0,
            approx_distinct=1,
        ),
    }
    path = tmp_path / "tz.parquet"
    pq.write_table(data, path)
    from viz.chartdata.sql import parquet_source

    rel = Relation(engine, parquet_source(path), None, columns, 1, "tz@v1")
    out = run(PlotType.CALENDAR_HEATMAP, rel, x=[{"field": "t"}], y=[{"field": "v", "agg": "sum"}])
    assert out.table.column("x").to_pylist() == [date(2024, 3, 10)]
