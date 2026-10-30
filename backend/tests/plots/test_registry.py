from __future__ import annotations

import textwrap
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pytest
import sqlglot
from pydantic import Field

import viz.plots as plots
from viz.contracts import (
    PLOT_FAMILY,
    ChartSpec,
    Column,
    ColumnSemantic,
    Encoding,
    PlotType,
    Reduction,
    SourceRef,
    StatResult,
    Viewport,
)
from viz.engine import Engine
from viz.errors import InvalidSpecError, NotImplementedYetError
from viz.plots.base import (
    CATEGORICAL,
    QUANT,
    Channel,
    PlotParams,
    PlotRegistrationError,
    PlotRegistry,
    PlotRequest,
    TableOutput,
    register_plot,
)
from viz.stats.relation import Relation


@pytest.fixture
def registry() -> Iterator[PlotRegistry]:
    with plots.scoped_registry() as reg:
        yield reg


@pytest.fixture
def relation(engine: Engine) -> Relation:
    source = sqlglot.parse_one(
        "SELECT * FROM (VALUES (1.5, 'a', DATE '2024-01-01'), (2.5, 'b', DATE '2024-01-02'))"
        " t(value, grp, day)",
        dialect="duckdb",
    )
    semantics = {
        "value": ColumnSemantic.QUANTITATIVE,
        "grp": ColumnSemantic.NOMINAL,
        "day": ColumnSemantic.TEMPORAL,
    }
    columns = {
        name: Column(name=name, type="X", semantic=sem, null_count=0, approx_distinct=2)
        for name, sem in semantics.items()
    }
    return Relation(
        engine=engine, source=source, where=None, columns=columns, row_count=2, version_key="t@1"
    )


def _spec(
    plot_type: PlotType, params: dict[str, object] | None = None, **channels: list[str | None]
) -> ChartSpec:
    return ChartSpec.model_validate(
        {
            "id": "c1",
            "plotType": plot_type.value,
            "source": SourceRef(kind="dataset", id="d1").model_dump(),
            "encodings": {
                name: [Encoding(field=f).model_dump(by_alias=True) for f in fields]
                for name, fields in channels.items()
            },
            "params": params or {},
        }
    )


class DensityParams(PlotParams):
    bin_count: int = Field(default=10, ge=1)
    ci_level: float | None = None


def _register_density() -> None:
    @register_plot(
        PlotType.DENSITY,
        channels=[Channel("x", QUANT), Channel("color", CATEGORICAL, min=0)],
        params=DensityParams,
    )
    def density(req: PlotRequest) -> TableOutput:
        assert isinstance(req.params, DensityParams)
        table = req.relation.arrow(req.relation.filtered(req.fields("x")[0]))
        return TableOutput(
            table=table,
            reduction=Reduction.NONE,
            method="test",
            fields={"value": "Value"},
            params={"binCount": req.params.bin_count, "color": req.field("color")},
        )


def test_register_and_run_returns_table_output(registry: PlotRegistry, relation: Relation) -> None:
    _register_density()
    assert plots.get_plot(PlotType.DENSITY) is not None
    assert set(plots.registered()) == {PlotType.DENSITY}
    spec = _spec(PlotType.DENSITY, {"binCount": 4}, x=["value"], color=["grp"])
    out = plots.run_plot_stat(spec, relation, viewport=Viewport(), mark_cap=100)
    assert isinstance(out, TableOutput)
    assert out.table.column("value").to_pylist() == [1.5, 2.5]
    assert out.params == {"binCount": 4, "color": "grp"}
    assert out.rows_scanned is None and out.rows_matched is None and out.warnings == []


def test_stat_result_output(registry: PlotRegistry, relation: Relation) -> None:
    @register_plot(PlotType.QQ, channels=[Channel("x", QUANT)])
    def qq(req: PlotRequest) -> StatResult:
        return StatResult(values={"ok": True}, method="m", n=2)

    out = plots.run_plot_stat(
        _spec(PlotType.QQ, x=["value"]), relation, viewport=Viewport(), mark_cap=10
    )
    assert isinstance(out, StatResult) and out.values == {"ok": True}


def test_wrong_return_type_is_a_bug(registry: PlotRegistry, relation: Relation) -> None:
    @register_plot(PlotType.ECDF, channels=[Channel("x")])
    def ecdf(req: PlotRequest) -> TableOutput:
        return pa.table({"a": [1]})  # type: ignore[return-value]

    with pytest.raises(TypeError, match="expected TableOutput or StatResult"):
        plots.run_plot_stat(
            _spec(PlotType.ECDF, x=["value"]), relation, viewport=Viewport(), mark_cap=10
        )


def test_unregistered_plot_is_not_implemented(registry: PlotRegistry, relation: Relation) -> None:
    with pytest.raises(NotImplementedYetError):
        plots.run_plot_stat(
            _spec(PlotType.VIOLIN, x=["value"]), relation, viewport=Viewport(), mark_cap=10
        )


@pytest.mark.parametrize(
    ("channels", "message"),
    [
        ({}, "Choose a field for 'x'."),
        ({"x": [None]}, "Choose a field for 'x'."),
        ({"x": ["value", "value"]}, "'x' takes at most 1 field."),
        ({"x": ["nope"]}, "Unknown field 'nope' in 'x'."),
        ({"x": ["grp"]}, "'x' needs a numeric field; 'grp' is not."),
        (
            {"x": ["value"], "color": ["day"]},
            "'color' needs a categorical or ordinal field; 'day' is not.",
        ),
    ],
)
def test_channel_problems(
    registry: PlotRegistry, relation: Relation, channels: dict[str, list[str | None]], message: str
) -> None:
    _register_density()
    with pytest.raises(InvalidSpecError) as info:
        plots.run_plot_stat(
            _spec(PlotType.DENSITY, **channels), relation, viewport=Viewport(), mark_cap=10
        )
    assert message in str(info.value)
    assert info.value.status_code == 422


def test_all_channel_problems_are_reported_together(
    registry: PlotRegistry, relation: Relation
) -> None:
    _register_density()
    spec = _spec(PlotType.DENSITY, x=["grp"], color=["value", "grp"])
    definition = plots.get_plot(PlotType.DENSITY)
    assert definition is not None
    problems = plots.channel_problems(definition, spec, relation.columns)
    assert problems == [
        "'x' needs a numeric field; 'grp' is not.",
        "'color' takes at most 1 field.",
        "'color' needs a categorical or ordinal field; 'value' is not.",
    ]


def test_min_and_unbounded_channels(registry: PlotRegistry, relation: Relation) -> None:
    @register_plot(PlotType.PAIR, channels=[Channel("fields", QUANT, min=2, max=None)])
    def pair(req: PlotRequest) -> StatResult:
        return StatResult(values={"fields": req.fields("fields")}, method="m", n=0)

    with pytest.raises(InvalidSpecError, match=r"'fields' needs at least 2 fields\."):
        plots.run_plot_stat(
            _spec(PlotType.PAIR, fields=["value"]), relation, viewport=Viewport(), mark_cap=1
        )
    out = plots.run_plot_stat(
        _spec(PlotType.PAIR, fields=["value", "value", "value"]),
        relation,
        viewport=Viewport(),
        mark_cap=1,
    )
    assert isinstance(out, StatResult) and out.values["fields"] == ["value"] * 3


def test_undeclared_channels_are_ignored(registry: PlotRegistry, relation: Relation) -> None:
    _register_density()
    spec = _spec(PlotType.DENSITY, x=["value"], tooltip=["whatever"])
    assert isinstance(
        plots.run_plot_stat(spec, relation, viewport=Viewport(), mark_cap=10), TableOutput
    )


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"binCount": 0}, "'binCount': Input should be greater than or equal to 1"),
        ({"bogus": 1}, "'bogus': Extra inputs are not permitted"),
    ],
)
def test_params_are_validated(
    registry: PlotRegistry, relation: Relation, params: dict[str, object], message: str
) -> None:
    _register_density()
    with pytest.raises(InvalidSpecError, match="Invalid chart settings") as info:
        plots.run_plot_stat(
            _spec(PlotType.DENSITY, params, x=["value"]), relation, viewport=Viewport(), mark_cap=10
        )
    assert message in str(info.value)


def test_duplicate_registration_is_rejected(registry: PlotRegistry) -> None:
    _register_density()
    _register_density()  # the same function again (e.g. a module reload) is fine
    with pytest.raises(PlotRegistrationError, match="already registered"):

        @register_plot(PlotType.DENSITY, channels=[Channel("x")])
        def other(req: PlotRequest) -> StatResult:
            raise AssertionError


def test_family_modules_may_only_register_their_family(registry: PlotRegistry) -> None:
    def stat(req: PlotRequest) -> StatResult:
        raise AssertionError

    stat.__module__ = "viz.plots.geo"
    with pytest.raises(PlotRegistrationError, match=r"belongs in viz\.plots\.distribution"):
        register_plot(PlotType.HISTOGRAM, channels=[Channel("x")])(stat)
    stat.__module__ = "viz.plots.distribution"
    register_plot(PlotType.HISTOGRAM, channels=[Channel("x")])(stat)
    assert plots.get_plot(PlotType.HISTOGRAM) is not None


def test_invalid_declarations(registry: PlotRegistry) -> None:
    with pytest.raises(PlotRegistrationError, match="min must be"):
        Channel("x", min=-1)
    with pytest.raises(PlotRegistrationError, match="max must be"):
        Channel("x", min=2, max=1)
    with pytest.raises(PlotRegistrationError, match="duplicate channel"):
        register_plot(PlotType.BOX, channels=[Channel("x"), Channel("x")])
    with pytest.raises(PlotRegistrationError, match="params must subclass PlotParams"):
        register_plot(PlotType.BOX, channels=[Channel("x")], params=dict)  # type: ignore[arg-type]


def test_plot_infos(registry: PlotRegistry) -> None:
    _register_density()
    infos = plots.plot_infos()
    assert [i.type for i in infos] == list(PLOT_FAMILY)
    by_type = {i.type: i for i in infos}
    density = by_type[PlotType.DENSITY]
    assert density.channels is not None
    assert [c.model_dump(by_alias=True) for c in density.channels] == [
        {"name": "x", "accepts": ["quantitative"], "min": 1, "max": 1},
        {"name": "color", "accepts": ["nominal", "ordinal"], "min": 0, "max": 1},
    ]
    assert density.params_schema is not None
    properties = density.params_schema["properties"]
    assert isinstance(properties, dict) and set(properties) == {"binCount", "ciLevel"}
    assert by_type[PlotType.BAR].channels is None and by_type[PlotType.BAR].params_schema is None


def test_import_plot_modules_skips_base_and_private(
    registry: PlotRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "fakeplots_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "base.py").write_text("raise RuntimeError('base must not be imported')\n")
    (package / "_helpers.py").write_text("raise RuntimeError('private must not be imported')\n")
    (package / "family.py").write_text(
        textwrap.dedent(
            """
            from viz.contracts import PlotType, StatResult
            from viz.plots.base import QUANT, Channel, register_plot

            @register_plot(PlotType.ECDF, channels=[Channel("x", QUANT)])
            def ecdf(req):
                return StatResult(values={}, method="m", n=0)
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert plots.import_plot_modules("fakeplots_pkg", [str(package)]) == ["family"]
    assert plots.get_plot(PlotType.ECDF) is not None


def test_real_family_modules_register_their_own_types() -> None:
    for plot_type, definition in plots.registered().items():
        assert plot_type in PLOT_FAMILY
        assert definition.stat.__module__ == f"viz.plots.{PLOT_FAMILY[plot_type].value}"


def test_scoped_registry_restores_the_real_one() -> None:
    before = dict(plots.registered())
    with plots.scoped_registry():
        _register_density()
        assert plots.get_plot(PlotType.DENSITY) is not None
    assert dict(plots.registered()) == before
