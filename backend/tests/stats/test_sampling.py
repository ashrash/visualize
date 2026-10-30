from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from sqlglot import exp

from viz.engine import Engine
from viz.stats.errors import StatsInputError
from viz.stats.relation import col
from viz.stats.sampling import numeric_array, reservoir_sample

_WITH_MISSING = """
SELECT * FROM (VALUES
  (1.0, 'a'), (2.0, 'a'), (NULL, 'a'), ('nan'::DOUBLE, 'b'), ('inf'::DOUBLE, 'b'), (3.0, NULL)
) t(x, g)
"""


def test_small_input_returns_everything(make_relation: Any) -> None:
    rel = make_relation(_WITH_MISSING)
    sample = reservoir_sample(rel, ["x"], cap=10)
    assert sorted(sample.column("x").tolist()) == [1.0, 2.0, 3.0]
    assert (sample.n, sample.n_total, sample.n_missing, sample.sampled) == (3, 3, 3, False)
    assert sample.notes() == []


def test_dropna_false_keeps_missing_values(make_relation: Any) -> None:
    sample = reservoir_sample(make_relation(_WITH_MISSING), ["x"], cap=10, dropna=False)
    assert (sample.n, sample.n_total, sample.n_missing) == (6, 6, 0)


def test_large_input_is_capped(make_relation: Any) -> None:
    rel = make_relation("SELECT range::DOUBLE AS x FROM range(5000)")
    sample = reservoir_sample(rel, ["x"], cap=200)
    assert (sample.n, sample.n_total, sample.sampled) == (200, 5000, True)
    values = sample.column("x")
    assert len(set(values.tolist())) == 200
    assert values.min() >= 0 and values.max() < 5000
    assert sample.notes() == ["Computed on a random sample of 200 of 5,000 rows."]


def test_filters_apply_before_sampling(make_relation: Any) -> None:
    where = exp.GTE(this=col("x"), expression=exp.Literal.number(4000))
    rel = make_relation("SELECT range::DOUBLE AS x FROM range(5000)", where=where)
    sample = reservoir_sample(rel, ["x"], cap=200)
    assert sample.n == 200 and sample.n_total == 1000
    assert sample.column("x").min() >= 4000


def test_sample_is_roughly_uniform(make_relation: Any) -> None:
    rel = make_relation("SELECT range::DOUBLE AS x FROM range(200000)")
    values = reservoir_sample(rel, ["x"], cap=20000).column("x")
    se = 200000 / np.sqrt(12) / np.sqrt(20000)
    assert abs(values.mean() - 99999.5) < 5 * se


def test_repeatable_with_one_thread(tmp_path: Path, relation_builder: Any) -> None:
    engine = Engine(temp_dir=tmp_path / "t", threads=1)
    try:
        rel = relation_builder(engine, tmp_path / "d", "SELECT range AS x FROM range(10000)")
        first = reservoir_sample(rel, ["x"], cap=50, seed=7).column("x")
        second = reservoir_sample(rel, ["x"], cap=50, seed=7).column("x")
        assert first.tolist() == second.tolist()
    finally:
        engine.close()


def test_stratified_caps_each_group(make_relation: Any) -> None:
    rel = make_relation(
        "SELECT range::DOUBLE AS x, CASE WHEN range < 1000 THEN 'big' WHEN range < 1050 "
        "THEN 'small' ELSE NULL END AS g FROM range(1300)"
    )
    sample = reservoir_sample(rel, ["x"], cap=100, by=["g"])
    groups = sample.values("g")
    counts = {g: groups.count(g) for g in set(groups)}
    assert counts == {"big": 100, "small": 50, None: 100}
    assert (sample.n_total, sample.sampled) == (1300, True)
    xs = sample.column("x")
    big = xs[[g == "big" for g in groups]]
    assert big.max() < 1000 and len(set(big.tolist())) == 100


def test_stratified_without_capping_is_not_sampled(make_relation: Any) -> None:
    rel = make_relation("SELECT range::DOUBLE AS x, range % 3 AS g FROM range(30)")
    sample = reservoir_sample(rel, ["x"], cap=5, by=["g"])
    assert sample.n == 15 and sample.sampled
    sample = reservoir_sample(rel, ["x"], cap=10, by=["g"])
    assert sample.n == 30 and not sample.sampled


def test_unknown_field_is_rejected(make_relation: Any) -> None:
    with pytest.raises(StatsInputError, match="Unknown field 'y'"):
        reservoir_sample(make_relation(_WITH_MISSING), ["y"], cap=10)


def test_numeric_array_conversions() -> None:
    ts = pa.chunked_array([pa.array([0, 1_500_000], type=pa.timestamp("us"))])
    assert numeric_array(ts).tolist() == [0.0, 1500.0]
    ts_s = pa.array([dt.datetime(1970, 1, 2)], type=pa.timestamp("s"))
    assert numeric_array(ts_s).tolist() == [86_400_000.0]
    dates = pa.array([dt.date(1970, 1, 3), None], type=pa.date32())
    out = numeric_array(dates)
    assert out[0] == 2 * 86_400_000.0 and np.isnan(out[1])
    assert numeric_array(pa.array([True, False])).tolist() == [1.0, 0.0]
    assert numeric_array(pa.array([Decimal("1.25")])).tolist() == [1.25]
    with pytest.raises(StatsInputError, match="'name' is not a numeric"):
        numeric_array(pa.array(["a"]), "name")
