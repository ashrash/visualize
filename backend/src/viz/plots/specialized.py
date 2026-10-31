"""Specialized plot family (Plots.md section 9).

sankey, alluvial, network: graph aggregation (GROUP BY source/target, SUM value) answered as a
`StatResult` of `{nodes, links}` (sankey/alluvial) or `{nodes, edges}` (network), not a table:
these are graphs, not rows. Nodes and edges are capped by total weight, with the remainder
rolled into an "(Other)" node; a simple Kahn's-algorithm pass drops any edge that would make the
sankey/alluvial graph cyclic (ECharts sankey needs a DAG) and reports it as a warning.

gantt: a plain row selection (no aggregation), capped and sorted by start.
pareto: GROUP BY + SUM, sorted descending, with a cumulative-percent column for the second axis.
error_bar, lollipop, dumbbell: small GROUP BY aggregates with the same `x`/`y0..` column
convention as the basic family. error_bar reuses the shape of the basic family's
`style.errorBars` (mean +/- sd/se/ci) as a dedicated, always-on plot.
ridgeline, raincloud: per-group kernel density estimates (`viz.stats.kde`) on a capped sample,
plus (raincloud) exact quartiles in SQL and a jittered sample of raw points.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Literal

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import Field
from scipy import stats as sps
from sqlglot import exp

from viz.chartdata.basic import measure_label
from viz.chartdata.reduce import aggregate_query, over_groups
from viz.chartdata.sql import (
    alias,
    cast,
    col,
    default_agg,
    fn,
    lit,
    measure_sql,
    ordered,
    subquery,
    window,
)
from viz.contracts import AggFn, Encoding, PlotType, Reduction, StatResult
from viz.errors import InvalidSpecError
from viz.plots.base import (
    CATEGORICAL,
    QUANT,
    TEMPORAL,
    Channel,
    PlotParams,
    PlotRequest,
    TableOutput,
    register_plot,
)
from viz.stats.groups import DEFAULT_MAX_GROUPS, cap_groups
from viz.stats.kde import Bandwidth, kde_by_group
from viz.stats.kde import kde as kde_single
from viz.stats.results import stat_result
from viz.stats.sampling import KDE_CAP, reservoir_sample

# ---------------------------------------------------------------- shared helpers


def _first_encoding(req: PlotRequest, channel: str) -> Encoding | None:
    items = req.spec.encodings.get(channel)
    return items[0] if items else None


def _size_measure(size_enc: Encoding | None) -> exp.Expr:
    if size_enc is not None:
        return measure_sql(size_enc)
    return cast(exp.Count(this=exp.Star()), "DOUBLE")


def _size_label(size_enc: Encoding | None) -> str:
    if size_enc is None:
        return "Count"
    return size_enc.label or measure_label(size_enc)


def _truncation_warning(limit: int) -> str:
    return f"Showing the first {limit:,} rows; narrow the data with a filter."


def _finite(expr: exp.Expr) -> exp.Expr:
    value = cast(expr, "DOUBLE")
    return exp.and_(
        exp.Not(this=exp.Is(this=value.copy(), expression=exp.Null())), fn("isfinite", value)
    )


# ---------------------------------------------------------------- graphs


class GraphParams(PlotParams):
    max_nodes: int = Field(default=40, ge=2, le=500)


def _edges(
    req: PlotRequest, source_field: str, target_field: str, value_enc: Encoding | None
) -> pa.Table:
    measure = _size_measure(value_enc)
    grouped = aggregate_query(
        req.relation,
        [("source", col(source_field)), ("target", col(target_field))],
        [("value", measure)],
    )
    return req.relation.arrow(over_groups(grouped))


_DEFAULT_OTHER_NAMES: Callable[[Any, Any], tuple[Any, Any]] = lambda _s, _t: ("(Other)", "(Other)")  # noqa: E731


def _cap_graph(
    edges: pa.Table,
    max_nodes: int,
    *,
    other_names: Callable[[Any, Any], tuple[Any, Any]] = _DEFAULT_OTHER_NAMES,
) -> tuple[pa.Table, int, int]:
    """Keep the `max_nodes` highest-total nodes; the rest collapse into one or more "(Other)"
    buckets, named by `other_names(source, target)` per edge (a source and a target bucket, so
    capping alone can't fuse unrelated dropped nodes into a single node that then looks cyclic:
    sankey buckets its sources and targets separately, alluvial buckets per stage).

    Returns `(edges, nodes_rolled_up, self_loops_dropped)`: a self-loop (source == target, real
    or created by two dropped nodes landing in the same bucket) carries no information for a
    sankey/network drawing and is dropped, but the caller should still say so -- a real self-loop
    (e.g. a "retried itself" edge) can be meaningful and silently vanishing it is a real loss,
    not just cosmetic rollup.
    """
    totals: dict[Any, float] = {}
    for row in edges.to_pylist():
        totals[row["source"]] = totals.get(row["source"], 0.0) + (row["value"] or 0.0)
        totals[row["target"]] = totals.get(row["target"], 0.0) + (row["value"] or 0.0)
    rolled = max(0, len(totals) - max_nodes)
    keep = (
        {k for k, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:max_nodes]}
        if rolled
        else set(totals)
    )
    merged: dict[tuple[Any, Any], float] = {}
    self_loops = 0
    for row in edges.to_pylist():
        s_other, t_other = other_names(row["source"], row["target"])
        s = row["source"] if row["source"] in keep else s_other
        t = row["target"] if row["target"] in keep else t_other
        if s == t:
            self_loops += 1
            continue
        merged[(s, t)] = merged.get((s, t), 0.0) + (row["value"] or 0.0)
    rows = [{"source": s, "target": t, "value": v} for (s, t), v in merged.items()]
    return pa.Table.from_pylist(rows) if rows else edges.slice(0, 0), rolled, self_loops


def _drop_cycles(edges: list[tuple[str, str, float]]) -> tuple[list[tuple[str, str, float]], int]:
    """Remove only the edges that close a cycle (DFS back-edges), not everything downstream of
    one: Kahn's algorithm (repeatedly removing nodes with no incoming edge) throws away every
    edge reachable from a cycle, which is almost the whole graph once rollup's "(Other)" bucket
    creates even one cross-link.

    Each node's outgoing edges are visited heaviest first, so when a back edge is found it is
    more likely to be one of the lighter ones (the Kahn version's stated intent, preserved as a
    heuristic: minimum feedback edge set is NP-hard in general).
    """
    adjacency: dict[str, list[tuple[str, float, int]]] = {}
    weight: dict[str, float] = {}
    seen_nodes: set[str] = set()
    for i, (s, t, v) in enumerate(edges):
        adjacency.setdefault(s, []).append((t, v, i))
        for n in (s, t):
            seen_nodes.add(n)
            weight[n] = weight.get(n, 0.0) + v
    for out in adjacency.values():
        out.sort(key=lambda e: -e[1])
    # Deterministic start order: by total weight then name, not by GROUP BY/insertion order,
    # which is unspecified and can flip which edge of a tied cycle gets dropped between runs.
    nodes = sorted(seen_nodes, key=lambda n: (-weight.get(n, 0.0), n))

    dropped: set[int] = set()
    UNVISITED, IN_STACK, DONE = 0, 1, 2
    state: dict[str, int] = {}

    def visit(start: str) -> None:
        stack: list[tuple[str, int]] = [(start, 0)]
        state[start] = IN_STACK
        while stack:
            node, i = stack[-1]
            out = adjacency.get(node, [])
            if i >= len(out):
                state[node] = DONE
                stack.pop()
                continue
            stack[-1] = (node, i + 1)
            target, _value, edge_index = out[i]
            if edge_index in dropped:
                continue
            target_state = state.get(target, UNVISITED)
            if target_state == IN_STACK:
                dropped.add(edge_index)
            elif target_state == UNVISITED:
                state[target] = IN_STACK
                stack.append((target, 0))

    for node in nodes:
        if state.get(node, UNVISITED) == UNVISITED:
            visit(node)

    kept = [e for i, e in enumerate(edges) if i not in dropped]
    return kept, len(dropped)


def _stage_of(name: Any) -> str:
    prefix, sep, _ = str(name).partition("⁣")
    return prefix if sep else "?"


def _self_loop_warning(count: int) -> str:
    return (
        f"{count:,} self-referencing edge{'s' if count != 1 else ''} "
        f"{'were' if count != 1 else 'was'} left out."
    )


def _sankey_other_names(_s: Any, _t: Any) -> tuple[str, str]:
    # Separate buckets for dropped sources and dropped targets: one shared "(Other)" would let
    # capping alone wire up a source-side drop to a target-side drop and look like a cycle.
    return "(Other: from)", "(Other: to)"


def _alluvial_other_names(s: Any, t: Any) -> tuple[str, str]:
    # One "(Other)" per stage: nodes are already stage-prefixed, so without this a rollup would
    # fuse dropped nodes from *different* stages into a single node, breaking the stage-by-stage
    # DAG the alluvial's links are otherwise guaranteed to form.
    return f"{_stage_of(s)}⁣(Other)", f"{_stage_of(t)}⁣(Other)"


def _graph_result(
    edges: pa.Table, *, directed: bool, mark_cap: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    rows = edges.to_pylist()
    warnings: list[str] = []
    if directed:
        triples = [(r["source"], r["target"], float(r["value"] or 0.0)) for r in rows]
        kept, dropped = _drop_cycles(triples)
        if dropped:
            warnings.append(
                f"{dropped} flow{'s' if dropped != 1 else ''} would make a loop and were left out."
            )
        rows = [{"source": s, "target": t, "value": v} for s, t, v in kept]
    cap = max(1, mark_cap)
    if len(rows) > cap:
        rows.sort(key=lambda r: -(r["value"] or 0.0))
        rows = rows[:cap]
        warnings.append(f"Showing the {cap:,} largest flows; narrow the data with a filter.")
    totals: dict[Any, float] = {}
    for row in rows:
        totals[row["source"]] = totals.get(row["source"], 0.0) + row["value"]
        totals[row["target"]] = totals.get(row["target"], 0.0) + row["value"]
    nodes = [{"name": str(name), "value": total} for name, total in totals.items()]
    links = [
        {"source": str(r["source"]), "target": str(r["target"]), "value": r["value"]} for r in rows
    ]
    return nodes, links, warnings


@register_plot(
    PlotType.SANKEY,
    channels=[
        Channel("source", CATEGORICAL, min=1, max=1),
        Channel("target", CATEGORICAL, min=1, max=1),
        Channel("size", QUANT, min=0, max=1),
    ],
    params=GraphParams,
)
def sankey(req: PlotRequest) -> StatResult:
    params = req.params
    assert isinstance(params, GraphParams)
    source_field = req.field("source")
    target_field = req.field("target")
    if source_field is None or target_field is None:
        raise InvalidSpecError("Choose fields for 'source' and 'target'.")
    edges = _edges(req, source_field, target_field, _first_encoding(req, "size"))
    edges, rolled, self_loops = _cap_graph(edges, params.max_nodes, other_names=_sankey_other_names)
    nodes, links, warnings = _graph_result(edges, directed=True, mark_cap=req.mark_cap)
    if rolled:
        warnings.append(f"The {rolled} smallest nodes are grouped into '(Other)'.")
    if self_loops:
        warnings.append(_self_loop_warning(self_loops))
    return stat_result(
        {"nodes": nodes, "links": links},
        method="source_target_aggregate",
        n=len(links),
        warnings=warnings,
    )


class AlluvialParams(GraphParams):
    pass


@register_plot(
    PlotType.ALLUVIAL,
    channels=[
        Channel("label", CATEGORICAL, min=2, max=6),
        Channel("size", QUANT, min=0, max=1),
    ],
    params=AlluvialParams,
)
def alluvial(req: PlotRequest) -> StatResult:
    """Consecutive stages (`label0 -> label1 -> label2 ...`) become sankey links; node names are
    prefixed with their stage so the same value at two stages doesn't collide."""
    params = req.params
    assert isinstance(params, AlluvialParams)
    stages = req.fields("label")
    if len(stages) < 2:
        raise InvalidSpecError("Choose at least 2 fields for 'label'.")
    size_enc = _first_encoding(req, "size")
    measure = _size_measure(size_enc)
    dims = [(f"s{i}", col(f)) for i, f in enumerate(stages)]
    grouped = aggregate_query(req.relation, dims, [("value", measure)])
    table = req.relation.arrow(over_groups(grouped))
    rows = table.to_pylist()

    def node_name(stage: int, value: Any) -> str:
        return f"{stage}⁣{value}"

    link_totals: dict[tuple[str, str], float] = {}
    for row in rows:
        v = float(row["value"] or 0.0)
        for i in range(len(stages) - 1):
            a = node_name(i, row[f"s{i}"])
            b = node_name(i + 1, row[f"s{i + 1}"])
            link_totals[(a, b)] = link_totals.get((a, b), 0.0) + v
    link_rows = [{"source": s, "target": t, "value": v} for (s, t), v in link_totals.items()]
    edges = (
        pa.Table.from_pylist(link_rows)
        if link_rows
        else pa.table({"source": [], "target": [], "value": []})
    )
    edges, rolled, self_loops = _cap_graph(
        edges, params.max_nodes, other_names=_alluvial_other_names
    )
    nodes, links, warnings = _graph_result(edges, directed=True, mark_cap=req.mark_cap)
    for node in nodes:
        stage_str, _, label = node["name"].partition("⁣")
        node["label"] = label
        node["depth"] = int(stage_str) if stage_str.isdigit() else 0
    if rolled:
        warnings.append(f"The {rolled} smallest nodes are grouped into '(Other)'.")
    if self_loops:
        warnings.append(_self_loop_warning(self_loops))
    return stat_result(
        {"nodes": nodes, "links": links, "stages": len(stages)},
        method="stage_aggregate",
        n=len(links),
        warnings=warnings,
    )


@register_plot(
    PlotType.NETWORK,
    channels=[
        Channel("source", CATEGORICAL, min=1, max=1),
        Channel("target", CATEGORICAL, min=1, max=1),
        Channel("size", QUANT, min=0, max=1),
    ],
    params=GraphParams,
)
def network(req: PlotRequest) -> StatResult:
    """An undirected graph: edges and node totals, for a force layout. Unlike sankey, the graph
    may be cyclic (force layout has no acyclic requirement), so no edges are dropped for that."""
    params = req.params
    assert isinstance(params, GraphParams)
    source_field = req.field("source")
    target_field = req.field("target")
    if source_field is None or target_field is None:
        raise InvalidSpecError("Choose fields for 'source' and 'target'.")
    edges = _edges(req, source_field, target_field, _first_encoding(req, "size"))
    edges, rolled, self_loops = _cap_graph(edges, params.max_nodes)
    nodes, links, warnings = _graph_result(edges, directed=False, mark_cap=req.mark_cap)
    if rolled:
        warnings.append(f"The {rolled} smallest nodes are grouped into '(Other)'.")
    if self_loops:
        warnings.append(_self_loop_warning(self_loops))
    return stat_result(
        {"nodes": nodes, "links": links},
        method="edge_aggregate",
        n=len(links),
        warnings=warnings,
    )


# ---------------------------------------------------------------- gantt


class GanttParams(PlotParams):
    max_tasks: int = Field(default=500, ge=1, le=5000)


@register_plot(
    PlotType.GANTT,
    channels=[
        Channel("label", CATEGORICAL, min=1, max=1),
        Channel("start", TEMPORAL, min=1, max=1),
        Channel("end", TEMPORAL, min=1, max=1),
        Channel("color", CATEGORICAL, min=0, max=1),
        Channel("progress", QUANT, min=0, max=1),
    ],
    params=GanttParams,
)
def gantt(req: PlotRequest) -> TableOutput:
    params = req.params
    assert isinstance(params, GanttParams)
    label_field = req.field("label")
    start_field = req.field("start")
    end_field = req.field("end")
    if label_field is None or start_field is None or end_field is None:
        raise InvalidSpecError("Choose fields for 'label', 'start' and 'end'.")
    color_field = req.field("color")
    progress_field = req.field("progress")

    warnings: list[str] = []
    params_out: dict[str, Any] = {}
    # A task with no start or end has nowhere to draw a bar; a null start/end slipping through
    # (rather than being dropped here) would otherwise set the time axis's auto-computed extent
    # to 1970, squeezing every real bar into a sliver.
    has_both = exp.and_(
        exp.Not(this=exp.Is(this=col(start_field), expression=exp.Null())),
        exp.Not(this=exp.Is(this=col(end_field), expression=exp.Null())),
    )
    stats_row = req.relation.arrow(
        req.relation.select(
            alias(exp.Count(this=exp.Star()), "n"),
            alias(exp.Count(this=exp.Case(ifs=[exp.If(this=has_both, true=lit(1))])), "with_dates"),
        )
    ).to_pylist()[0]
    n_dropped = int(stats_row["n"] or 0) - int(stats_row["with_dates"] or 0)
    relation = req.relation.narrow(has_both) if n_dropped else req.relation
    if n_dropped:
        warnings.append(
            f"{n_dropped:,} task{'s' if n_dropped != 1 else ''} with no start or end date "
            f"{'were' if n_dropped != 1 else 'was'} left out."
        )

    selects: list[exp.Expr] = [
        alias(col(label_field), "label"),
        alias(col(start_field), "start"),
        alias(col(end_field), "end"),
    ]
    if color_field is not None:
        selects.append(alias(col(color_field), "color"))
    if progress_field is not None:
        selects.append(alias(cast(col(progress_field), "DOUBLE"), "progress"))
    cap = max(1, min(params.max_tasks, req.mark_cap))
    query = relation.select(*selects).order_by(ordered(col("start"))).limit(cap + 1)
    table = relation.arrow(query)
    if table.num_rows > cap:
        table = table.slice(0, cap)
        params_out["truncatedGroups"] = True
        warnings.append(_truncation_warning(cap))
    fields = {"label": label_field, "start": start_field, "end": end_field}
    if color_field is not None:
        fields["color"] = color_field
    if progress_field is not None:
        fields["progress"] = progress_field
    # "None" means every eligible task made it through (eligible = has both dates); tasks
    # dropped for missing dates are already called out in their own warning above.
    eligible = int(stats_row["with_dates"] or 0)
    return TableOutput(
        table=table,
        reduction=Reduction.NONE if table.num_rows == eligible else Reduction.SAMPLED,
        method="rows",
        fields=fields,
        params=params_out,
        warnings=warnings,
    )


# ---------------------------------------------------------------- pareto


@register_plot(
    PlotType.PARETO,
    channels=[
        Channel("x", CATEGORICAL, min=1, max=1),
        Channel("y", QUANT, min=0, max=1),
    ],
)
def pareto(req: PlotRequest) -> TableOutput:
    x_field = req.field("x")
    if x_field is None:
        raise InvalidSpecError("Choose a field for 'x'.")
    x_enc = req.spec.encodings["x"][0]
    y0 = _first_encoding(req, "y") or Encoding(agg=AggFn.COUNT)
    if y0.field is None and default_agg(y0) != AggFn.COUNT:
        raise InvalidSpecError(f"{default_agg(y0).value} needs a field")
    dims = [("x", col(x_field))]
    measures = [("y0", measure_sql(y0))]
    grouped = aggregate_query(req.relation, dims, measures)
    # The grand total rides along as a window function in the same query (one GROUP BY instead
    # of two): cumPercent must read 100% at the last bar actually drawn only when nothing was
    # cut off, not whenever the cap truncates the table, so it needs the *un-truncated* total.
    scored = exp.select(exp.Star(), alias(window(fn("sum", col("y0"))), "__viz_total")).from_(
        subquery(grouped, "__viz_g")
    )
    cap = max(1, req.mark_cap)
    table = req.relation.arrow(scored.order_by(ordered(col("y0"), desc=True)).limit(cap + 1))
    warnings: list[str] = []
    params_out: dict[str, Any] = {}
    if table.num_rows > cap:
        table = table.slice(0, cap)
        params_out["truncatedGroups"] = True
        warnings.append(_truncation_warning(cap))
    grand_total = 1.0
    if table.num_rows:
        grand_total = float(pc.cast(table.column("__viz_total"), pa.float64())[0].as_py() or 0.0)
        grand_total = grand_total or 1.0
    values = pc.cast(table.column("y0"), pa.float64()).to_numpy(zero_copy_only=False)
    cum_percent = np.cumsum(np.where(np.isfinite(values), values, 0.0)) / grand_total * 100.0
    table = table.drop_columns(["__viz_total"]).append_column(
        "cumPercent", pa.array(cum_percent, pa.float64())
    )
    fields = {"x": x_enc.label or x_field, "y0": measure_label(y0), "cumPercent": "Cumulative %"}
    return TableOutput(
        table=table,
        reduction=Reduction.AGGREGATED,
        method="group_by+cumulative",
        fields=fields,
        params=params_out,
        warnings=warnings,
    )


# ---------------------------------------------------------------- error bar, lollipop, dumbbell


class ErrorBarParams(PlotParams):
    kind: Literal["sd", "se", "ci"] = "sd"
    level: float = Field(default=0.95, gt=0, lt=1)


@register_plot(
    PlotType.ERROR_BAR,
    channels=[
        Channel("x", CATEGORICAL, min=1, max=1),
        Channel("y", QUANT, min=1, max=1),
    ],
    params=ErrorBarParams,
)
def error_bar(req: PlotRequest) -> TableOutput:
    """Mean of `y` by `x`, plus a dedicated always-on error bar (sd / se / a t confidence
    interval), the same computation the basic family's optional `style.errorBars` uses."""
    params = req.params
    assert isinstance(params, ErrorBarParams)
    x_field = req.field("x")
    y_field = req.field("y")
    if x_field is None or y_field is None:
        raise InvalidSpecError("Choose fields for 'x' and 'y'.")
    x_enc = req.spec.encodings["x"][0]
    y_enc = req.spec.encodings["y"][0]
    value = _finite(col(y_field))
    grouped = aggregate_query(
        req.relation,
        [("x", col(x_field))],
        [
            (
                "y0",
                fn(
                    "avg",
                    exp.Case(ifs=[exp.If(this=value.copy(), true=cast(col(y_field), "DOUBLE"))]),
                ),
            ),
            (
                "__viz_sd",
                fn(
                    "stddev_samp",
                    exp.Case(ifs=[exp.If(this=value.copy(), true=cast(col(y_field), "DOUBLE"))]),
                ),
            ),
            ("__viz_n", exp.Count(this=exp.Case(ifs=[exp.If(this=value.copy(), true=lit(1))]))),
        ],
    )
    cap = max(1, req.mark_cap)
    table = req.relation.arrow(over_groups(grouped).order_by(ordered(col("x"))).limit(cap + 1))
    warnings: list[str] = []
    params_out: dict[str, Any] = {"errorKind": params.kind}
    if table.num_rows > cap:
        table = table.slice(0, cap)
        params_out["truncatedGroups"] = True
        warnings.append(_truncation_warning(cap))
    sd = pc.cast(table.column("__viz_sd"), pa.float64()).to_numpy(zero_copy_only=False)
    n = pc.cast(table.column("__viz_n"), pa.float64()).to_numpy(zero_copy_only=False)
    center = pc.cast(table.column("y0"), pa.float64()).to_numpy(zero_copy_only=False)
    with np.errstate(divide="ignore", invalid="ignore"):
        if params.kind == "sd":
            half = sd
        elif params.kind == "se":
            half = sd / np.sqrt(n)
        else:
            t = sps.t.ppf((1 + params.level) / 2, np.where(n > 1, n - 1, np.nan))
            half = t * sd / np.sqrt(n)
    lo, hi = center - half, center + half
    mask = ~np.isfinite(lo) | ~np.isfinite(hi)
    table = table.drop_columns(["__viz_sd", "__viz_n"])
    table = table.append_column("y0_lo", pa.array(lo, pa.float64(), mask=mask))
    table = table.append_column("y0_hi", pa.array(hi, pa.float64(), mask=mask))
    fields = {
        "x": x_enc.label or x_field,
        "y0": measure_label(y_enc.model_copy(update={"agg": AggFn.MEAN})),
        "y0_lo": f"{params.kind.upper()} low",
        "y0_hi": f"{params.kind.upper()} high",
    }
    return TableOutput(
        table=table,
        reduction=Reduction.AGGREGATED,
        method="mean+error",
        fields=fields,
        params=params_out,
        warnings=warnings,
    )


class SortableBarParams(PlotParams):
    sort_by: Literal["value", "label"] = "value"


@register_plot(
    PlotType.LOLLIPOP,
    channels=[
        Channel("x", CATEGORICAL, min=1, max=1),
        Channel("y", QUANT, min=0, max=1),
    ],
    params=SortableBarParams,
)
def lollipop(req: PlotRequest) -> TableOutput:
    x_field = req.field("x")
    if x_field is None:
        raise InvalidSpecError("Choose a field for 'x'.")
    x_enc = req.spec.encodings["x"][0]
    params = req.params
    assert isinstance(params, SortableBarParams)
    y0 = _first_encoding(req, "y") or Encoding(agg=AggFn.COUNT)
    if y0.field is None and default_agg(y0) != AggFn.COUNT:
        raise InvalidSpecError(f"{default_agg(y0).value} needs a field")
    grouped = aggregate_query(req.relation, [("x", col(x_field))], [("y0", measure_sql(y0))])
    order_expr = ordered(col("y0"), desc=True) if params.sort_by == "value" else ordered(col("x"))
    cap = max(1, req.mark_cap)
    table = req.relation.arrow(over_groups(grouped).order_by(order_expr).limit(cap + 1))
    warnings: list[str] = []
    params_out: dict[str, Any] = {}
    if table.num_rows > cap:
        table = table.slice(0, cap)
        params_out["truncatedGroups"] = True
        warnings.append(_truncation_warning(cap))
    fields = {"x": x_enc.label or x_field, "y0": measure_label(y0)}
    return TableOutput(
        table=table,
        reduction=Reduction.AGGREGATED,
        method="group_by",
        fields=fields,
        params=params_out,
        warnings=warnings,
    )


class DumbbellParams(PlotParams):
    sort_by: Literal["delta", "label", "end"] = "delta"


@register_plot(
    PlotType.DUMBBELL,
    channels=[
        Channel("x", CATEGORICAL, min=1, max=1),
        Channel("y", QUANT, min=2, max=2),
    ],
    params=DumbbellParams,
)
def dumbbell(req: PlotRequest) -> TableOutput:
    """Two measures (`y0` the start, `y1` the end) per category, e.g. a before/after pair."""
    x_field = req.field("x")
    if x_field is None:
        raise InvalidSpecError("Choose a field for 'x'.")
    x_enc = req.spec.encodings["x"][0]
    params = req.params
    assert isinstance(params, DumbbellParams)
    ys = req.spec.encodings["y"]
    for y in ys:
        if y.field is None:
            raise InvalidSpecError("Both 'y' fields are required for a dumbbell chart.")
    measures = [(f"y{i}", measure_sql(y)) for i, y in enumerate(ys)]
    grouped = aggregate_query(req.relation, [("x", col(x_field))], measures)
    cap = max(1, req.mark_cap // 2)
    # Ordered (not an arbitrary LIMIT): an unordered cap doesn't just pick an arbitrary subset,
    # it can pick a *different* arbitrary subset on every request.
    table = req.relation.arrow(over_groups(grouped).order_by(ordered(col("x"))).limit(cap + 1))
    warnings: list[str] = []
    params_out: dict[str, Any] = {}
    if table.num_rows > cap:
        table = table.slice(0, cap)
        params_out["truncatedGroups"] = True
        warnings.append(_truncation_warning(cap))
    y0 = pc.cast(table.column("y0"), pa.float64()).to_numpy(zero_copy_only=False)
    y1 = pc.cast(table.column("y1"), pa.float64()).to_numpy(zero_copy_only=False)
    if params.sort_by == "label":
        order = pc.sort_indices(table, sort_keys=[("x", "ascending")])
    elif params.sort_by == "end":
        order = pc.sort_indices(pa.table({"v": y1}), sort_keys=[("v", "descending")])
    else:
        order = pc.sort_indices(pa.table({"v": pa.array(y1 - y0)}), sort_keys=[("v", "descending")])
    table = table.take(order)
    fields = {"x": x_enc.label or x_field, "y0": measure_label(ys[0]), "y1": measure_label(ys[1])}
    return TableOutput(
        table=table,
        reduction=Reduction.AGGREGATED,
        method="group_by",
        fields=fields,
        params=params_out,
        warnings=warnings,
    )


# ---------------------------------------------------------------- ridgeline, raincloud


class DensityParams(PlotParams):
    bandwidth: Literal["silverman", "scott"] = "silverman"
    adjust: float = Field(default=1.0, gt=0, le=10)
    grid_size: int = Field(default=200, ge=16, le=2048)
    max_groups: int = Field(default=DEFAULT_MAX_GROUPS, ge=1, le=500)
    # Cosmetic-only (affectsData=false on the frontend); accepted so the request validates.
    overlap: float = Field(default=0.5, ge=0, le=1)


def _sampled_values(
    req: PlotRequest, field: str, group_field: str | None, max_groups: int
) -> tuple[np.ndarray, np.ndarray | None, list[str]]:
    relation = req.relation
    warnings: list[str] = []
    by: Sequence[str] = (group_field,) if group_field else ()
    if group_field:
        capped = cap_groups(relation, by, max_groups)
        if capped.dropped:
            relation = capped.relation
            warnings.append(capped.warning or "")
    sample = reservoir_sample(relation, [field], cap=KDE_CAP, by=by)
    warnings += sample.notes()
    values = sample.column(field)
    groups = np.asarray(sample.values(group_field), dtype=object) if group_field else None
    return values, groups, [w for w in warnings if w]


@register_plot(
    PlotType.RIDGELINE,
    channels=[
        Channel("value", QUANT, min=1, max=1),
        Channel("group", CATEGORICAL, min=1, max=1),
    ],
    params=DensityParams,
)
def ridgeline(req: PlotRequest) -> StatResult:
    params = req.params
    assert isinstance(params, DensityParams)
    value_field = req.field("value")
    group_field = req.field("group")
    if value_field is None or group_field is None:
        raise InvalidSpecError("Choose fields for 'value' and 'group'.")
    values, groups, warnings = _sampled_values(req, value_field, group_field, params.max_groups)
    assert groups is not None
    bw: Bandwidth = params.bandwidth
    result = kde_by_group(values, groups, bw=bw, adjust=params.adjust, grid_size=params.grid_size)
    curves = [
        {
            "key": "null" if c.key is None else str(c.key),
            "n": c.n,
            "x": c.curve.x,
            "density": c.curve.density,
            "bandwidth": c.curve.bandwidth,
        }
        for c in result.curves
    ]
    return stat_result(
        {"groups": curves},
        method=f"kde_{params.bandwidth}",
        n=len(values),
        warnings=[*warnings, *result.warnings],
    )


class RaincloudParams(DensityParams):
    point_sample: int = Field(default=300, ge=10, le=5000)
    # Cosmetic-only (jitter width on the strip); accepted so the request validates.
    jitter: float = Field(default=0.15, ge=0, le=1)


def _group_box_stats(
    req: PlotRequest, value_field: str, group_field: str | None
) -> dict[Any, dict[str, float]]:
    value = _finite(col(value_field))
    guarded = exp.Case(ifs=[exp.If(this=value.copy(), true=cast(col(value_field), "DOUBLE"))])
    dims = [("group", col(group_field))] if group_field else []
    measures = [
        ("n", exp.Count(this=guarded.copy())),
        ("q1", fn("quantile_cont", guarded.copy(), lit(0.25))),
        ("median", fn("quantile_cont", guarded.copy(), lit(0.5))),
        ("q3", fn("quantile_cont", guarded.copy(), lit(0.75))),
        ("lo", fn("min", guarded.copy())),
        ("hi", fn("max", guarded.copy())),
    ]
    grouped = aggregate_query(req.relation, dims, measures)
    table = req.relation.arrow(over_groups(grouped) if dims else grouped)
    out: dict[Any, dict[str, float]] = {}
    for row in table.to_pylist():
        key = row.get("group") if group_field else None
        out[key] = {k: row[k] for k in ("n", "q1", "median", "q3", "lo", "hi")}
    return out


@register_plot(
    PlotType.RAINCLOUD,
    channels=[
        Channel("value", QUANT, min=1, max=1),
        Channel("group", CATEGORICAL, min=0, max=1),
    ],
    params=RaincloudParams,
)
def raincloud(req: PlotRequest) -> StatResult:
    """A half-violin (KDE) + box (exact SQL quartiles) + jittered strip (sampled points) per
    group. The box's whiskers are the data's min/max rather than the classic 1.5xIQR rule: the
    strip already draws every sampled point, so a separate outlier list would be redundant."""
    params = req.params
    assert isinstance(params, RaincloudParams)
    value_field = req.field("value")
    if value_field is None:
        raise InvalidSpecError("Choose a field for 'value'.")
    group_field = req.field("group")
    boxes = _group_box_stats(req, value_field, group_field)
    values, groups, warnings = _sampled_values(req, value_field, group_field, params.max_groups)
    rng = np.random.default_rng(42)
    out_groups: list[dict[str, Any]] = []
    if group_field:
        assert groups is not None
        kde_result = kde_by_group(
            values, groups, bw=params.bandwidth, adjust=params.adjust, grid_size=params.grid_size
        )
        # The strip's points count toward the mark cap too (StatResult outputs otherwise have no
        # generic cap guard): spread it evenly over the groups actually drawn.
        n_groups = max(1, len(kde_result.curves))
        point_cap = max(1, min(params.point_sample, req.mark_cap // n_groups))
        if point_cap < params.point_sample:
            warnings.append(f"Showing at most {point_cap:,} sampled points per group.")
        for curve in kde_result.curves:
            key = curve.key
            idx = np.where(groups == key)[0]
            sample_idx = rng.choice(idx, size=min(len(idx), point_cap), replace=False)
            box = boxes.get(key, {})
            out_groups.append(
                {
                    "key": "null" if key is None else str(key),
                    "n": curve.n,
                    "density": {"x": curve.curve.x, "density": curve.curve.density},
                    "box": box,
                    "points": values[sample_idx].tolist(),
                }
            )
        warnings += kde_result.warnings
    else:
        finite = values[np.isfinite(values)]
        single = kde_single(
            finite, bw=params.bandwidth, adjust=params.adjust, grid_size=params.grid_size
        )
        point_cap = max(1, min(params.point_sample, req.mark_cap))
        if point_cap < params.point_sample:
            warnings.append(f"Showing at most {point_cap:,} sampled points.")
        sample_idx = rng.choice(len(finite), size=min(len(finite), point_cap), replace=False)
        out_groups.append(
            {
                "key": None,
                "n": single.n,
                "density": {"x": single.x, "density": single.density},
                "box": boxes.get(None, {}),
                "points": finite[sample_idx].tolist(),
            }
        )
        warnings += single.warnings
    return stat_result(
        {"groups": out_groups},
        method=f"kde_{params.bandwidth}+quartiles+sample",
        n=len(values),
        warnings=[*warnings],
    )
