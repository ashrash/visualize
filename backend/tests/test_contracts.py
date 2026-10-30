from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from viz.contracts import (
    PLOT_FAMILY,
    ChartSpec,
    InPredicate,
    PlotFamily,
    PlotType,
    Project,
    RangePredicate,
)
from viz.contracts.migrations import NewerSchemaError, upgrade_project
from viz.contracts.pipeline import Pipeline

PLOTS_MD = Path(__file__).resolve().parents[2] / "Plots.md"

# Plots.md section number → family.
SECTION_FAMILY = {
    1: PlotFamily.BASIC,
    2: PlotFamily.DISTRIBUTION,
    3: PlotFamily.RELATIONSHIP,
    4: PlotFamily.TIMESERIES,
    5: PlotFamily.COMPOSITION,
    6: PlotFamily.MULTIVARIATE,
    7: PlotFamily.GEO,
    8: PlotFamily.ECONOMETRIC,
    9: PlotFamily.SPECIALIZED,
}


def _plots_md_counts() -> dict[PlotFamily, int]:
    counts: dict[PlotFamily, int] = {}
    family: PlotFamily | None = None
    for line in PLOTS_MD.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^(\d+)\.", line.strip())
        if heading:
            family = SECTION_FAMILY[int(heading.group(1))]
            counts[family] = 0
        elif line.strip().startswith("- ") and family is not None:
            counts[family] += 1
    return counts


def test_every_plot_in_plots_md_has_a_plot_type() -> None:
    expected = _plots_md_counts()
    actual = {f: sum(1 for fam in PLOT_FAMILY.values() if fam == f) for f in PlotFamily}
    assert actual == expected
    assert set(PLOT_FAMILY) == set(PlotType)


def test_contracts_are_camel_case_on_the_wire() -> None:
    spec = ChartSpec.model_validate(
        {
            "id": "c1",
            "plotType": "bar",
            "source": {"kind": "dataset", "id": "d1"},
            "encodings": {"x": [{"field": "region"}], "y": [{"field": "sales", "agg": "sum"}]},
            "style": {"yAxes": [{"scale": "log"}], "showLabels": True},
        }
    )
    dumped = spec.model_dump(mode="json")
    assert dumped["plotType"] == "bar"
    assert dumped["style"]["yAxes"][0]["scale"] == "log"
    assert "plot_type" not in dumped


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ChartSpec.model_validate(
            {"id": "c1", "plotType": "bar", "source": {"kind": "dataset", "id": "d1"}, "colour": 1}
        )
    with pytest.raises(ValidationError):
        ChartSpec.model_validate({"plotType": "bar", "source": {"kind": "dataset", "id": "d1"}})


def test_predicates_and_steps_are_discriminated_unions() -> None:
    spec = ChartSpec.model_validate(
        {
            "id": "c2",
            "plotType": "line",
            "source": {"kind": "dataset", "id": "d1"},
            "filters": [
                {"op": "not_in", "field": "region", "values": ["East", None]},
                {"op": "between_dates", "field": "order_date", "min": "2024-01-01"},
            ],
        }
    )
    assert isinstance(spec.filters[0], InPredicate)
    assert isinstance(spec.filters[1], RangePredicate)

    pipeline = Pipeline.model_validate(
        {
            "id": "p1",
            "source": {"kind": "dataset", "id": "d1"},
            "steps": [
                {"id": "s1", "op": "calc", "name": "margin", "expression": "sales - cost"},
                {
                    "id": "s2",
                    "op": "pivot",
                    "index": ["region"],
                    "columns": "product",
                    "values": "sales",
                },
            ],
        }
    )
    assert [s.op for s in pipeline.steps] == ["calc", "pivot"]


def test_project_upgrade_rejects_newer_documents() -> None:
    doc = Project(id="p1", name="p").model_dump(mode="json")
    assert upgrade_project(dict(doc))["schemaVersion"] == 1
    doc["schemaVersion"] = 99
    with pytest.raises(NewerSchemaError):
        upgrade_project(doc)
