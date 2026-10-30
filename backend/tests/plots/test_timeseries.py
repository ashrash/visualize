"""Plots.md Sec4 (Time Series): known-answer tests for the server stat transforms.

Each plot is exercised through the real, globally discovered registry (`viz.plots.run_plot_stat`)
against a `Relation` built from a small in-memory parquet fixture, the same pattern
`backend/tests/chartdata/test_reduce.py` uses for the generic reduction kernels.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date, datetime, timedelta
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
    assert xs[:12] == list(range(1, 13))
    assert set(colors) == {2023, 2024}
    by_key = dict(zip(zip(xs, colors, strict=False), y0, strict=True))
    assert by_key[(1, 2023)] == pytest.approx(2023 * 100 + 1)
    assert by_key[(12, 2024)] == pytest.approx(2024 * 100 + 12)
    assert out.rows_matched == 24


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
    np.testing.assert_allclose(out.table.column("y0_lo").to_pylist(), expected_ci[:, 0])
    np.testing.assert_allclose(out.table.column("y0_hi").to_pylist(), expected_ci[:, 1])
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
    np.testing.assert_allclose(out.table.column("y0_lo").to_pylist(), expected_ci[:, 0])
    np.testing.assert_allclose(out.table.column("y0_hi").to_pylist(), expected_ci[:, 1])


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
