"""Known-answer tests for the specialized plot family's stat transforms."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from viz.contracts import ChartSpec, Encoding, PlotType, Reduction, SourceRef, Viewport
from viz.plots.base import PlotParams, PlotRequest
from viz.plots.specialized import (
    AlluvialParams,
    DensityParams,
    DumbbellParams,
    ErrorBarParams,
    GanttParams,
    GraphParams,
    RaincloudParams,
    SortableBarParams,
    _drop_cycles,
    alluvial,
    dumbbell,
    error_bar,
    gantt,
    lollipop,
    network,
    pareto,
    raincloud,
    ridgeline,
    sankey,
)
from viz.stats.relation import Relation

MakeRelation = Callable[..., Relation]


def _spec(
    plot_type: PlotType, encodings: dict[str, list[Encoding]], params: dict | None = None
) -> ChartSpec:
    return ChartSpec(
        id="c1",
        plot_type=plot_type,
        source=SourceRef(kind="dataset", id="d1"),
        encodings=encodings,
        params=params or {},
    )


def _request(
    relation: Relation,
    plot_type: PlotType,
    encodings: dict[str, list[Encoding]],
    params_model=PlotParams,
    params: dict | None = None,
    mark_cap: int = 50_000,
) -> PlotRequest:
    spec = _spec(plot_type, encodings, params)
    return PlotRequest(
        spec=spec,
        params=params_model.model_validate(params or {}),
        relation=relation,
        viewport=Viewport(),
        mark_cap=mark_cap,
    )


def test_sankey_aggregates_and_reports_totals(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "source": ["Ads", "Ads", "Search"],
            "target": ["Home", "Home", "Home"],
            "value": [10.0, 5.0, 20.0],
        }
    )
    req = _request(
        rel,
        PlotType.SANKEY,
        {
            "source": [Encoding(field="source")],
            "target": [Encoding(field="target")],
            "size": [Encoding(field="value")],
        },
        GraphParams,
    )
    out = sankey(req)
    links = out.values["links"]
    assert len(links) == 2
    by_source = {link["source"]: link["value"] for link in links}
    assert by_source["Ads"] == pytest.approx(15.0)
    assert by_source["Search"] == pytest.approx(20.0)
    nodes = {node["name"]: node["value"] for node in out.values["nodes"]}
    assert nodes["Home"] == pytest.approx(35.0)


def test_sankey_caps_nodes_into_other(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "source": ["A", "B", "C"],
            "target": ["X", "X", "X"],
            "value": [100.0, 2.0, 1.0],
        }
    )
    req = _request(
        rel,
        PlotType.SANKEY,
        {
            "source": [Encoding(field="source")],
            "target": [Encoding(field="target")],
            "size": [Encoding(field="value")],
        },
        GraphParams,
        params={"maxNodes": 2},
    )
    out = sankey(req)
    names = {node["name"] for node in out.values["nodes"]}
    # Dropped sources and dropped targets get separate buckets, so capping alone can never wire
    # up a source-side drop to a target-side drop and look like a cycle.
    assert "(Other: from)" in names
    assert "A" in names and "X" in names


def test_sankey_drops_only_the_cycle_not_the_rest_of_the_graph(
    table_relation: MakeRelation,
) -> None:
    # A -> B -> A is a cycle; B -> C -> D is not, and must survive untouched.
    rel = table_relation(
        {
            "source": ["A", "B", "B", "C"],
            "target": ["B", "A", "C", "D"],
            "value": [1.0, 1.0, 1.0, 1.0],
        }
    )
    req = _request(
        rel,
        PlotType.SANKEY,
        {"source": [Encoding(field="source")], "target": [Encoding(field="target")]},
        GraphParams,
    )
    out = sankey(req)
    links = {(link["source"], link["target"]) for link in out.values["links"]}
    assert ("B", "C") in links
    assert ("C", "D") in links
    # Exactly one of the two cycle edges is dropped, not both.
    assert len({("A", "B"), ("B", "A")} & links) == 1
    assert len(links) == 3
    assert any("loop" in w for w in out.warnings)


def test_sankey_self_loop_only_drops_itself(table_relation: MakeRelation) -> None:
    # The self-loop is dropped by _cap_graph (a sankey can't draw A -> A), not by the cycle
    # detector, so it must still be called out in a warning rather than silently vanishing --
    # a real self-loop (e.g. a "retried itself" edge) can be meaningful.
    rel = table_relation(
        {"source": ["A", "A"], "target": ["A", "B"], "value": [1.0, 1.0]},
    )
    req = _request(
        rel,
        PlotType.SANKEY,
        {"source": [Encoding(field="source")], "target": [Encoding(field="target")]},
        GraphParams,
    )
    out = sankey(req)
    links = {(link["source"], link["target"]) for link in out.values["links"]}
    assert links == {("A", "B")}
    assert any("self-referencing" in w for w in out.warnings)


def test_sankey_handles_a_null_target_without_crashing(table_relation: MakeRelation) -> None:
    # A NULL target is a valid, common group-by result. Before _edges cast source/target to
    # VARCHAR and the tie-break sort got a total order, two nodes tied on weight (here "B" and
    # the NULL target) raised TypeError comparing None to str during cycle detection.
    rel = table_relation(
        {"source": ["A", "B"], "target": [None, "C"], "value": [1.0, 1.0]},
    )
    req = _request(
        rel,
        PlotType.SANKEY,
        {"source": [Encoding(field="source")], "target": [Encoding(field="target")]},
        GraphParams,
    )
    out = sankey(req)
    # _graph_result stringifies every node name for JSON output (str(name)), so a NULL target
    # becomes the literal "None" - this test is only about the TypeError the None value used to
    # raise during the Python-side tie-break sort in _drop_cycles, not about what NULL renders as.
    links = {(link["source"], link["target"]) for link in out.values["links"]}
    assert links == {("A", "None"), ("B", "C")}


def test_sankey_handles_integer_node_ids_once_rollup_triggers(
    table_relation: MakeRelation,
) -> None:
    # Arrow infers int64 from the first rows of source/target; _cap_graph's "(Other: ...)"
    # rollup buckets are strings, so building the rolled-up table used to raise ArrowInvalid
    # for any integer-id graph (user ids, step numbers, ...) with more nodes than max_nodes.
    rel = table_relation(
        {
            "source": list(range(60)),
            "target": [i + 1 for i in range(60)],
            "value": [1.0] * 60,
        }
    )
    req = _request(
        rel,
        PlotType.SANKEY,
        {"source": [Encoding(field="source")], "target": [Encoding(field="target")]},
        GraphParams,
        params={"maxNodes": 40},
    )
    out = sankey(req)
    names = {node["name"] for node in out.values["nodes"]}
    assert "(Other: from)" in names or "(Other: to)" in names


def test_drop_cycles_is_deterministic_regardless_of_input_order() -> None:
    # A tied 2-cycle (A <-> B, equal weight): which edge gets dropped must depend only on the
    # graph itself, not on the order edges happen to arrive in (e.g. from GROUP BY), which is
    # unspecified and can otherwise flip the result between refreshes.
    forward = [("A", "B", 1.0), ("B", "A", 1.0), ("B", "C", 1.0)]
    backward = list(reversed(forward))
    kept_forward, dropped_forward = _drop_cycles(forward)
    kept_backward, dropped_backward = _drop_cycles(backward)
    assert dropped_forward == dropped_backward == 1
    assert set(kept_forward) == set(kept_backward)


def test_network_is_undirected_and_keeps_cycles(table_relation: MakeRelation) -> None:
    rel = table_relation({"source": ["A", "B"], "target": ["B", "A"], "value": [1.0, 2.0]})
    req = _request(
        rel,
        PlotType.NETWORK,
        {"source": [Encoding(field="source")], "target": [Encoding(field="target")]},
        GraphParams,
    )
    out = network(req)
    assert len(out.values["links"]) == 2


def test_alluvial_is_acyclic_by_construction(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "entry": ["Ads", "Ads", "Search"],
            "page": ["Home", "Pricing", "Home"],
            "outcome": ["Signup", "Bounce", "Signup"],
            "users": [10.0, 5.0, 20.0],
        }
    )
    req = _request(
        rel,
        PlotType.ALLUVIAL,
        {
            "label": [Encoding(field="entry"), Encoding(field="page"), Encoding(field="outcome")],
            "size": [Encoding(field="users")],
        },
        AlluvialParams,
    )
    out = alluvial(req)
    # 5 distinct stage-to-stage links (Home -> Signup merges the two rows that share it), none
    # dropped as a "loop": a layered, stage-by-stage graph can never contain a cycle.
    assert len(out.values["links"]) == 5
    assert not any("loop" in w for w in out.warnings)


def test_alluvial_rollup_keeps_each_stage_in_its_own_other_bucket(
    table_relation: MakeRelation,
) -> None:
    # 3 stages, each with several distinct values: without a per-stage "(Other)" bucket, the
    # smallest nodes at every stage would collapse into one shared node and create artificial
    # cross-stage cycles (and the loop-detector would then drop almost everything).
    n = 6
    rel = table_relation(
        {
            "a": [f"a{i}" for i in range(n)],
            "b": [f"b{i}" for i in range(n)],
            "c": [f"c{i}" for i in range(n)],
            "users": [float(n - i) for i in range(n)],
        }
    )
    req = _request(
        rel,
        PlotType.ALLUVIAL,
        {
            "label": [Encoding(field="a"), Encoding(field="b"), Encoding(field="c")],
            "size": [Encoding(field="users")],
        },
        AlluvialParams,
        params={"maxNodes": 3},
    )
    out = alluvial(req)
    assert not any("loop" in w for w in out.warnings)
    others = [n for n in out.values["nodes"] if "(Other)" in n["name"]]
    # A separate Other bucket per stage (0, 1, 2), not one shared across all of them.
    assert len(others) == 3
    assert len({o["depth"] for o in others}) == 3


def test_gantt_returns_rows_sorted_by_start(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "task": ["C", "A", "B"],
            "start": ["2024-03-01", "2024-01-01", "2024-02-01"],
            "end": ["2024-03-10", "2024-01-10", "2024-02-10"],
        },
        semantics=None,
    )
    req = _request(
        rel,
        PlotType.GANTT,
        {
            "label": [Encoding(field="task")],
            "start": [Encoding(field="start")],
            "end": [Encoding(field="end")],
        },
        GanttParams,
    )
    out = gantt(req)
    assert out.table.column("label").to_pylist() == ["A", "B", "C"]


def test_gantt_drops_tasks_with_no_start_or_end_and_warns(table_relation: MakeRelation) -> None:
    # A regression: a null start/end slipping through used to set the time axis's auto-computed
    # extent to 1970 on the frontend, squeezing every real bar into a sliver.
    rel = table_relation(
        {
            "task": ["A", "Unfinished", "B"],
            "start": ["2024-01-01", "2024-02-01", None],
            "end": ["2024-01-10", None, "2024-03-10"],
        },
        semantics=None,
    )
    req = _request(
        rel,
        PlotType.GANTT,
        {
            "label": [Encoding(field="task")],
            "start": [Encoding(field="start")],
            "end": [Encoding(field="end")],
        },
        GanttParams,
    )
    out = gantt(req)
    assert out.table.column("label").to_pylist() == ["A"]
    assert any("no start or end" in w for w in out.warnings)
    assert out.reduction == Reduction.NONE


def test_pareto_sorts_descending_with_cumulative_percent(table_relation: MakeRelation) -> None:
    rel = table_relation({"defect": ["Scratch", "Dent", "Crack"], "count": [10.0, 50.0, 40.0]})
    req = _request(
        rel,
        PlotType.PARETO,
        {"x": [Encoding(field="defect")], "y": [Encoding(field="count")]},
    )
    out = pareto(req)
    rows = out.table.to_pylist()
    assert [r["x"] for r in rows] == ["Dent", "Crack", "Scratch"]
    assert rows[-1]["cumPercent"] == pytest.approx(100.0)
    assert rows[0]["cumPercent"] == pytest.approx(50.0)
    assert rows[1]["cumPercent"] == pytest.approx(90.0)


def test_pareto_cum_percent_reflects_the_true_total_when_truncated(
    table_relation: MakeRelation,
) -> None:
    # cumPercent must be computed against the grand total of every category, not just the
    # ones that survive the mark cap -- otherwise a truncated chart misleadingly reads 100%
    # at its last visible bar.
    rel = table_relation(
        {"defect": ["A", "B", "C", "D"], "count": [40.0, 30.0, 20.0, 10.0]},
    )
    req = _request(
        rel,
        PlotType.PARETO,
        {"x": [Encoding(field="defect")], "y": [Encoding(field="count")]},
        mark_cap=2,
    )
    out = pareto(req)
    rows = out.table.to_pylist()
    assert len(rows) == 2
    # Against the true total of 100 (not 70, the sum of just the two shown bars).
    assert rows[0]["cumPercent"] == pytest.approx(40.0)
    assert rows[1]["cumPercent"] == pytest.approx(70.0)


def test_error_bar_sd_around_the_mean(table_relation: MakeRelation) -> None:
    rel = table_relation({"species": ["A", "A", "A", "B"], "mass": [10.0, 20.0, 30.0, 5.0]})
    req = _request(
        rel,
        PlotType.ERROR_BAR,
        {"x": [Encoding(field="species")], "y": [Encoding(field="mass")]},
        ErrorBarParams,
        params={"kind": "sd"},
    )
    out = error_bar(req)
    rows = {r["x"]: r for r in out.table.to_pylist()}
    assert rows["A"]["y0"] == pytest.approx(20.0)
    # sample stddev of [10, 20, 30] is 10.
    assert rows["A"]["y0_lo"] == pytest.approx(10.0)
    assert rows["A"]["y0_hi"] == pytest.approx(30.0)
    assert rows["B"]["y0_lo"] is None  # n=1: stddev is undefined


def test_error_bar_is_capped_by_mark_cap(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {"species": ["A", "B", "C"], "mass": [10.0, 20.0, 30.0]},
    )
    req = _request(
        rel,
        PlotType.ERROR_BAR,
        {"x": [Encoding(field="species")], "y": [Encoding(field="mass")]},
        ErrorBarParams,
        mark_cap=2,
    )
    out = error_bar(req)
    assert out.table.num_rows == 2
    assert out.params.get("truncatedGroups") is True


def test_lollipop_sorts_by_value_by_default(table_relation: MakeRelation) -> None:
    rel = table_relation({"defect": ["A", "B", "C"], "count": [5.0, 50.0, 10.0]})
    req = _request(
        rel,
        PlotType.LOLLIPOP,
        {"x": [Encoding(field="defect")], "y": [Encoding(field="count")]},
        SortableBarParams,
    )
    out = lollipop(req)
    assert out.table.column("x").to_pylist() == ["B", "C", "A"]


def test_dumbbell_pairs_two_measures(table_relation: MakeRelation) -> None:
    rel = table_relation({"country": ["A", "B"], "exp2000": [60.0, 70.0], "exp2020": [65.0, 68.0]})
    req = _request(
        rel,
        PlotType.DUMBBELL,
        {
            "x": [Encoding(field="country")],
            "y": [Encoding(field="exp2000"), Encoding(field="exp2020")],
        },
        DumbbellParams,
    )
    out = dumbbell(req)
    rows = {r["x"]: (r["y0"], r["y1"]) for r in out.table.to_pylist()}
    assert rows["A"] == (60.0, 65.0)
    assert rows["B"] == (70.0, 68.0)
    # Default sort is by descending delta (A: +5, B: -2), so A comes first.
    assert out.table.column("x").to_pylist()[0] == "A"


def test_ridgeline_returns_one_curve_per_group(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "temp": [10.0, 11.0, 9.0, 12.0, 30.0, 31.0, 29.0, 32.0],
            "month": ["Jan", "Jan", "Jan", "Jan", "Jul", "Jul", "Jul", "Jul"],
        }
    )
    req = _request(
        rel,
        PlotType.RIDGELINE,
        {"value": [Encoding(field="temp")], "group": [Encoding(field="month")]},
        DensityParams,
    )
    out = ridgeline(req)
    groups = {g["key"]: g for g in out.values["groups"]}
    assert set(groups) == {"Jan", "Jul"}
    assert groups["Jan"]["n"] == 4
    assert len(groups["Jan"]["x"]) == len(groups["Jan"]["density"])


def test_raincloud_has_box_density_and_points_per_group(table_relation: MakeRelation) -> None:
    rel = table_relation(
        {
            "mass": [10.0, 20.0, 30.0, 40.0, 50.0, 1.0, 2.0, 3.0, 4.0, 5.0],
            "species": ["A"] * 5 + ["B"] * 5,
        }
    )
    req = _request(
        rel,
        PlotType.RAINCLOUD,
        {"value": [Encoding(field="mass")], "group": [Encoding(field="species")]},
        RaincloudParams,
    )
    out = raincloud(req)
    groups = {g["key"]: g for g in out.values["groups"]}
    # DuckDB quantile_cont is NumPy's "linear" method (R type 7): for [10,20,30,40,50],
    # p=0.25 -> index 1.0 -> 20.0, p=0.75 -> index 3.0 -> 40.0.
    assert groups["A"]["box"]["median"] == pytest.approx(30.0)
    assert groups["A"]["box"]["q1"] == pytest.approx(20.0)
    assert groups["A"]["box"]["q3"] == pytest.approx(40.0)
    assert len(groups["A"]["points"]) == 5
    assert len(groups["A"]["density"]["x"]) == len(groups["A"]["density"]["density"])


def test_raincloud_without_a_group_gives_one_group(table_relation: MakeRelation) -> None:
    rel = table_relation({"mass": [10.0, 20.0, 30.0, 40.0, 50.0]})
    req = _request(
        rel,
        PlotType.RAINCLOUD,
        {"value": [Encoding(field="mass")]},
        RaincloudParams,
    )
    out = raincloud(req)
    assert len(out.values["groups"]) == 1
    assert out.values["groups"][0]["key"] is None
    assert out.values["groups"][0]["box"]["median"] == pytest.approx(30.0)


def test_raincloud_points_are_capped_by_mark_cap(table_relation: MakeRelation) -> None:
    # A StatResult has no generic cap guard (unlike a table response), so raincloud must cap its
    # own point sample by the mark cap itself, split evenly across the groups drawn.
    rel = table_relation(
        {
            "mass": [float(i) for i in range(40)],
            "species": (["A"] * 20) + (["B"] * 20),
        }
    )
    req = _request(
        rel,
        PlotType.RAINCLOUD,
        {"value": [Encoding(field="mass")], "group": [Encoding(field="species")]},
        RaincloudParams,
        params={"pointSample": 20},
        mark_cap=10,
    )
    out = raincloud(req)
    groups = out.values["groups"]
    total_points = sum(len(g["points"]) for g in groups)
    assert total_points <= 10
    assert any("Showing at most" in w for w in out.warnings)
