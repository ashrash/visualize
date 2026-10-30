"""Capped random samples for model fits (CLAUDE.md §6: exact where cheap, sampled where not).

DuckDB applies USING SAMPLE before WHERE, so the filtered rows are sampled as a subquery.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from viz.stats.errors import StatsInputError
from viz.stats.groups import DEFAULT_MAX_GROUPS, cap_groups
from viz.stats.relation import Relation, col
from viz.stats.results import sample_note
from viz.stats.sql import alias, all_valid, count_if, count_star

DEFAULT_SEED = 42
# Default caps (CLAUDE.md §6): model fits 1M rows, KDE 100k; LOWESS refits are costlier.
MODEL_CAP = 1_000_000
KDE_CAP = 100_000
LOWESS_CAP = 10_000

_MS_PER_UNIT = {"s": 1000.0, "ms": 1.0, "us": 1e-3, "ns": 1e-6}


def numeric_array(values: pa.ChunkedArray | pa.Array, name: str = "value") -> np.ndarray:
    """float64 values; temporal becomes epoch milliseconds; nulls become NaN."""
    kind = values.type
    if pa.types.is_timestamp(kind):
        scale = _MS_PER_UNIT[kind.unit]
        ints = pc.cast(values, pa.int64())
        return np.asarray(pc.cast(ints, pa.float64()).to_numpy(zero_copy_only=False)) * scale
    if pa.types.is_date32(kind):
        days = pc.cast(values, pa.int32())
        return np.asarray(pc.cast(days, pa.float64()).to_numpy(zero_copy_only=False)) * 86_400_000.0
    if pa.types.is_date64(kind):
        ms = pc.cast(values, pa.int64())
        return np.asarray(pc.cast(ms, pa.float64()).to_numpy(zero_copy_only=False))
    if pa.types.is_boolean(kind) or pa.types.is_integer(kind) or pa.types.is_floating(kind):
        return np.asarray(pc.cast(values, pa.float64()).to_numpy(zero_copy_only=False))
    if pa.types.is_decimal(kind):
        return np.asarray(pc.cast(values, pa.float64()).to_numpy(zero_copy_only=False))
    raise StatsInputError(f"'{name}' is not a numeric or date/time field.")


@dataclass(frozen=True)
class Sample:
    table: pa.Table
    # Rows in the sample.
    n: int
    # Eligible rows (after filters and missing-value removal) the sample was drawn from.
    n_total: int
    # Rows dropped because a field was null, NaN or +/-inf.
    n_missing: int
    sampled: bool
    # Rows per group the sample was capped at (stratified samples only).
    cap_per_group: int | None = None
    warnings: list[str] = field(default_factory=list)

    def column(self, name: str) -> np.ndarray:
        return numeric_array(self.table.column(name), name)

    def values(self, name: str) -> list[object]:
        return list(self.table.column(name).to_pylist())

    def notes(self) -> list[str]:
        """The sampling note (when sampled) plus any warnings, for StatResult.warnings."""
        note = [sample_note(self.n, self.n_total)] if self.sampled else []
        return [*note, *self.warnings]


def reservoir_sample(
    rel: Relation,
    fields: Sequence[str],
    *,
    cap: int,
    by: Sequence[str] = (),
    seed: int = DEFAULT_SEED,
    dropna: bool = True,
    max_groups: int = DEFAULT_MAX_GROUPS,
    total_cap: int = MODEL_CAP,
) -> Sample:
    """Up to `cap` random rows of (by + fields); with `by`, up to `cap` rows per group.

    With dropna, rows where any of `fields` is null or non-finite are left out first (and
    counted in n_missing); `by` columns may be null (NULL is a group).

    With `by`, only the `max_groups` largest groups are sampled, and the per-group cap is
    lowered to total_cap / groups so a sample never exceeds `total_cap` rows. Stratified
    samples use random() and are not repeatable (`seed` applies to unstratified samples);
    chart results are cached per data version, so a chart doesn't change between views.
    """
    if cap < 1:
        raise ValueError("cap must be >= 1")
    for name in (*by, *fields):
        rel.require(name)
    names = list(dict.fromkeys([*by, *fields]))
    condition = all_valid([rel.columns[f] for f in fields]) if dropna else None

    eligible_count = count_if(condition) if condition is not None else count_star()
    total, eligible = rel.rows(
        rel.select(alias(count_star(), "total"), alias(eligible_count, "eligible"))
    )[0]
    eligible_rel = rel.narrow(condition) if condition is not None else rel
    warnings: list[str] = []
    cap_per_group: int | None = None
    if by:
        groups = cap_groups(eligible_rel, by, max_groups)
        if groups.dropped:
            eligible_rel = groups.relation
            eligible = eligible_rel.count()
            warnings.append(groups.warning or "")
        cap_per_group = max(1, min(cap, total_cap // max(1, groups.n_groups - groups.dropped)))
        cap = cap_per_group
    inner = eligible_rel.filtered(*names)

    if eligible <= cap:
        table = eligible_rel.arrow(inner)
    elif not by:
        table = rel.arrow(_reservoir_sql(inner.sql(dialect="duckdb"), cap, int(eligible), seed))
        if table.num_rows < cap:  # the Bernoulli prefilter fell short (vanishingly rare)
            table = rel.arrow(_reservoir_sql(inner.sql(dialect="duckdb"), cap, 0, seed))
    else:
        table = rel.arrow(_stratified_sql(inner.sql(dialect="duckdb"), names, by, cap))
    return Sample(
        table=table,
        n=table.num_rows,
        n_total=int(eligible),
        n_missing=int(total - eligible),
        sampled=table.num_rows < eligible,
        cap_per_group=cap_per_group,
        warnings=warnings,
    )


def _reservoir_sql(inner: str, cap: int, eligible: int, seed: int) -> str:
    """Exactly `cap` uniformly sampled rows.

    DuckDB's reservoir sampler is mostly serial, so when the input is much larger than the
    sample a parallel Bernoulli pass first keeps ~cap + 5*sqrt(cap) rows (falling short of cap
    has probability below 1e-6); a uniform reservoir of a Bernoulli sample is still uniform.
    """
    sampled = f"SELECT * FROM ({inner}) AS _s"
    expected = cap + 5 * math.sqrt(cap) + 50
    if eligible > 4 * expected:
        percent = 100.0 * expected / eligible
        sampled = (
            f"SELECT * FROM ({inner}) AS _s "
            f"USING SAMPLE {percent:.10f} PERCENT (bernoulli, {int(seed)})"
        )
        sampled = f"SELECT * FROM ({sampled}) AS _b"
    return f"{sampled} USING SAMPLE reservoir({int(cap)} ROWS) REPEATABLE ({int(seed)})"


def _stratified_sql(inner: str, names: Sequence[str], by: Sequence[str], cap: int) -> str:
    """Bottom-k sampling per group: one random key per row, keep the cap smallest.

    A Bernoulli prefilter (expected ~2*cap survivors per group) keeps the sort small.
    """
    cols = ", ".join(col(n).sql(dialect="duckdb") for n in names)
    groups = ", ".join(col(n).sql(dialect="duckdb") for n in by)
    expected = 2 * cap + 10 * math.sqrt(cap) + 20
    return (
        f"SELECT {cols} FROM ("
        f"  SELECT {cols}, row_number() OVER (PARTITION BY {groups} ORDER BY _r) AS _rn FROM ("
        f"    SELECT {cols}, random() AS _r, count(*) OVER (PARTITION BY {groups}) AS _n"
        f"    FROM ({inner}) AS _a"
        f"  ) AS _b WHERE _r < least(1.0, {expected!r} / _n)"
        f") AS _c WHERE _rn <= {int(cap)}"
    )
