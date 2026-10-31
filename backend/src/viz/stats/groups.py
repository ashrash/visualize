"""Capping the number of groups a grouped kernel works on (keep the largest)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlglot import exp

from viz.stats.fields import key_expr
from viz.stats.relation import Relation
from viz.stats.sql import alias, count_star, lit

DEFAULT_MAX_GROUPS = 100


@dataclass(frozen=True)
class GroupCap:
    # The relation restricted to the kept groups (the input itself when nothing was dropped).
    relation: Relation
    # Groups found before capping.
    n_groups: int
    # Number of groups dropped; 0 when every group was kept.
    dropped: int

    @property
    def warning(self) -> str | None:
        if not self.dropped:
            return None
        kept = self.n_groups - self.dropped
        return f"Showing the {kept:,} largest of {self.n_groups:,} groups."


def _text(expr: exp.Expr) -> exp.Expr:
    return exp.cast(expr, "VARCHAR")


def cap_groups(rel: Relation, by: Sequence[str], max_groups: int) -> GroupCap:
    """Keep the `max_groups` largest groups of `by` (ties broken by key, NULL last).

    Groups are matched on their text form, so any column type works without typed literals.
    Grouping uses `key_expr`, the same NaN/+-inf-as-NULL guard the downstream kernels use, so a
    float column's NULL, NaN and +-inf values collapse into one group here too - otherwise they'd
    count as up to four separate groups during capping but merge into one afterwards, wasting cap
    slots and occasionally reporting a group's count wrong.
    """
    if max_groups < 1:
        raise ValueError("max_groups must be >= 1")
    if not by:
        return GroupCap(rel, 1, 0)
    by_keys = [key_expr(rel, b) for b in by]
    keys = [alias(_text(k), f"_k{i}") for i, k in enumerate(by_keys)]
    grouped = rel.select(*keys, alias(count_star(), "_n")).group_by(*by_keys)
    order = ", ".join(f"_k{i} NULLS LAST" for i in range(len(by)))
    sql = (
        f"SELECT *, count(*) OVER () AS _groups FROM ({grouped.sql(dialect='duckdb')}) AS _g "
        f"ORDER BY _n DESC, {order} LIMIT {int(max_groups)}"
    )
    rows = rel.rows(sql)
    n_groups = int(rows[0][-1]) if rows else 0
    if n_groups <= max_groups:
        return GroupCap(rel, n_groups, 0)
    conditions: list[exp.Expr] = []
    for row in rows:
        parts: list[exp.Expr] = []
        for key, value in zip(by_keys, row[: len(by)], strict=True):
            if value is None:
                parts.append(exp.Is(this=key, expression=exp.Null()))
            else:
                parts.append(exp.EQ(this=_text(key), expression=lit(str(value))))
        conditions.append(exp.and_(*parts))
    return GroupCap(rel.narrow(exp.or_(*conditions)), n_groups, n_groups - len(rows))
