"""Capping the number of groups a grouped kernel works on (keep the largest)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlglot import exp

from viz.stats.relation import Relation, col
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
    """
    if max_groups < 1:
        raise ValueError("max_groups must be >= 1")
    if not by:
        return GroupCap(rel, 1, 0)
    keys = [alias(_text(col(b)), f"_k{i}") for i, b in enumerate(by)]
    grouped = rel.select(*keys, alias(count_star(), "_n")).group_by(*[col(b) for b in by])
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
        for name, value in zip(by, row[: len(by)], strict=True):
            if value is None:
                parts.append(exp.Is(this=col(name), expression=exp.Null()))
            else:
                parts.append(exp.EQ(this=_text(col(name)), expression=lit(str(value))))
        conditions.append(exp.and_(*parts))
    return GroupCap(rel.narrow(exp.or_(*conditions)), n_groups, n_groups - len(rows))
