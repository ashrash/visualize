"""Plots.md §6 Multivariate stat transforms: known answers, degenerate inputs."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sklearn.datasets import load_iris
from sqlglot import exp

import viz.plots as plots
from viz.contracts import (
    ChartSpec,
    Column,
    ColumnSemantic,
    PlotType,
    Reduction,
    StatResult,
    Viewport,
)
from viz.engine import Engine
from viz.errors import InvalidSpecError
from viz.plots import multivariate as mv
from viz.plots.base import TableOutput
from viz.stats.relation import Relation

_counter = itertools.count()
IRIS = ["sepal_length", "sepal_width", "petal_length", "petal_width"]

Rel = Callable[..., Relation]


def _semantic(arrow_type: pa.DataType) -> ColumnSemantic:
    if pa.types.is_integer(arrow_type) or pa.types.is_floating(arrow_type):
        return ColumnSemantic.QUANTITATIVE
    if pa.types.is_temporal(arrow_type):
        return ColumnSemantic.TEMPORAL
    return ColumnSemantic.NOMINAL


@pytest.fixture
def rel(engine: Engine, tmp_path: Path) -> Rel:
    def make(data: pa.Table | dict[str, Any]) -> Relation:
        table = data if isinstance(data, pa.Table) else pa.table(data)
        path = tmp_path / f"mv é {next(_counter)}.parquet"
        pq.write_table(table, path)
        columns = {
            f.name: Column(
                name=f.name,
                type=str(f.type).upper(),
                semantic=_semantic(f.type),
                null_count=0,
                approx_distinct=0,
            )
            for f in table.schema
        }
        source = exp.Anonymous(this="read_parquet", expressions=[exp.Literal.string(str(path))])
        return Relation(
            engine=engine,
            source=source,
            where=None,
            columns=columns,
            row_count=table.num_rows,
            version_key=f"t{next(_counter)}@v1",
        )

    return make


def run(
    relation: Relation,
    plot: PlotType,
    params: dict[str, Any] | None = None,
    mark_cap: int = 50_000,
    **channels: list[str],
) -> Any:
    spec = ChartSpec.model_validate(
        {
            "id": "c",
            "plotType": plot.value,
            "source": {"kind": "dataset", "id": "d"},
            "encodings": {k: [{"field": f} for f in v] for k, v in channels.items()},
            "params": params or {},
        }
    )
    return plots.run_plot_stat(spec, relation, viewport=Viewport(), mark_cap=mark_cap)


def iris_table() -> pa.Table:
    iris = load_iris()
    cols: dict[str, Any] = {name: iris.data[:, i] for i, name in enumerate(IRIS)}
    cols["species"] = [str(iris.target_names[t]) for t in iris.target]
    return pa.table(cols)


def stat(out: Any) -> dict[str, Any]:
    assert isinstance(out, StatResult)
    assert "NaN" not in repr(out.values)
    return out.values


# --- registration -----------------------------------------------------------------------------


def test_every_multivariate_plot_is_registered() -> None:
    for plot in (
        PlotType.RADAR,
        PlotType.PARALLEL_COORDINATES,
        PlotType.PCA_BIPLOT,
        PlotType.FACTOR_LOADING,
        PlotType.CLUSTER,
        PlotType.DENDROGRAM,
        PlotType.ANDREWS_CURVES,
    ):
        assert plots.get_plot(plot) is not None, plot


# --- PCA --------------------------------------------------------------------------------------

# R: prcomp(iris[, 1:4], scale. = TRUE) and prcomp(iris[, 1:4]).
IRIS_SDEV_SCALED = [1.7083611, 0.9560494, 0.3830886, 0.1439265]
IRIS_PC1_SCALED = [0.5210659, -0.2693474, 0.5804131, 0.5648565]
IRIS_SDEV = [2.0562689, 0.4926162, 0.2796596, 0.1543862]
IRIS_PC1 = [0.36138659, -0.08452251, 0.85667061, 0.35828920]


def test_pca_iris_matches_published_prcomp(rel: Rel) -> None:
    r = rel(iris_table())
    values = stat(run(r, PlotType.PCA_BIPLOT, y=IRIS))
    assert values["eigenvalues"] == pytest.approx([s**2 for s in IRIS_SDEV_SCALED], abs=2e-6)
    assert values["explainedVarianceRatio"] == pytest.approx(
        [0.7296245, 0.2285076, 0.0366892, 0.0051787], abs=1e-6
    )
    # Correlation-scale loadings = eigenvector * sdev; R's sign convention may differ.
    loadings = [row["x"] for row in values["loadings"]]
    expected = np.array(IRIS_PC1_SCALED) * IRIS_SDEV_SCALED[0]
    sign = np.sign(loadings[0] / expected[0])
    assert np.array(loadings) == pytest.approx(sign * expected, abs=1e-5)
    unscaled = stat(run(r, PlotType.PCA_BIPLOT, {"standardize": False}, y=IRIS))
    assert unscaled["eigenvalues"] == pytest.approx([s**2 for s in IRIS_SDEV], abs=2e-6)
    pc1 = np.array([row["x"] for row in unscaled["loadings"]]) / IRIS_SDEV[0]
    assert np.abs(pc1) == pytest.approx(np.abs(IRIS_PC1), abs=1e-6)


def test_pca_scores_match_scikit_learn(rel: Rel) -> None:
    from sklearn.decomposition import PCA

    table = iris_table()
    values = stat(
        run(rel(table), PlotType.PCA_BIPLOT, {"maxPoints": 500}, y=IRIS, color=["species"])
    )
    data = np.column_stack([table.column(n).to_numpy() for n in IRIS])
    z = (data - data.mean(axis=0)) / data.std(axis=0, ddof=1)
    expected = PCA().fit_transform(z)
    got = np.column_stack([values["points"]["x"], values["points"]["y"]])
    # Same rows (no sampling at 150 < 500), same order; signs may differ per component.
    for k in range(2):
        sign = np.sign(got[0, k] / expected[0, k])
        assert got[:, k] == pytest.approx(sign * expected[:, k], abs=1e-9)
    assert set(values["points"]["color"]) == {"setosa", "versicolor", "virginica"}
    assert values["arrowScale"] > 0


def test_pca_display_sample_but_exact_components(rel: Rel) -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=(3000, 3))
    r = rel({"a": x[:, 0], "b": x[:, 1] + x[:, 0], "c": x[:, 2]})
    out = run(r, PlotType.PCA_BIPLOT, {"maxPoints": 100}, y=["a", "b", "c"])
    values = stat(out)
    assert len(values["points"]["x"]) == 100
    assert out.n == 3000 and out.sampled is False
    corr = np.corrcoef(np.column_stack([x[:, 0], x[:, 1] + x[:, 0], x[:, 2]]), rowvar=False)
    assert values["eigenvalues"] == pytest.approx(np.sort(np.linalg.eigvalsh(corr))[::-1], abs=1e-9)
    assert any("drawn as points" in w for w in out.warnings)


def test_pca_collinear_and_constant_fields(rel: Rel) -> None:
    r = rel(
        {"a": [1.0, 2, 3, 4, 5], "b": [2.0, 4, 6, 8, 10], "c": [1.0, 0, 1, 0, 2], "k": [7.0] * 5}
    )
    out = run(r, PlotType.PCA_BIPLOT, y=["a", "b", "c", "k"])
    values = stat(out)
    assert values["variables"] == ["a", "b", "c"]
    assert any("no variation" in w for w in out.warnings)
    assert any("linear" in w for w in out.warnings)
    assert values["eigenvalues"][-1] == pytest.approx(0, abs=1e-9)


def test_pca_degenerate_inputs(rel: Rel) -> None:
    with pytest.raises(InvalidSpecError, match="at least 3 rows"):
        run(rel({"a": [1.0, 2], "b": [3.0, 1]}), PlotType.PCA_BIPLOT, y=["a", "b"])
    with pytest.raises(InvalidSpecError, match="fields that vary"):
        run(rel({"a": [1.0, 2, 3], "b": [3.0, 3, 3]}), PlotType.PCA_BIPLOT, y=["a", "b"])
    with pytest.raises(InvalidSpecError, match="only 2 components"):
        run(
            rel({"a": [1.0, 2, 3, 5], "b": [3.0, 1, 2, 2]}),
            PlotType.PCA_BIPLOT,
            {"yComponent": 3},
            y=["a", "b"],
        )


def test_pca_drops_incomplete_rows(rel: Rel) -> None:
    r = rel({"a": [1.0, 2, 3, None, 5, float("nan")], "b": [2.0, 1, 4, 3, 5, 6]})
    out = run(r, PlotType.PCA_BIPLOT, y=["a", "b"])
    assert out.n == 4 and out.n_missing == 2


# --- Factor loadings --------------------------------------------------------------------------


def test_pca_loadings_iris_match_psych_principal(rel: Rel) -> None:
    # psych::principal(iris[, 1:4], nfactors = 1): 0.89, -0.46, 0.99, 0.96.
    values = stat(
        run(
            rel(iris_table()),
            PlotType.FACTOR_LOADING,
            {"method": "pca", "factors": 1, "rotation": "none"},
            y=IRIS,
        )
    )
    loadings = np.array(values["loadings"])[:, 0]
    assert loadings == pytest.approx(np.array(IRIS_PC1_SCALED) * IRIS_SDEV_SCALED[0], abs=1e-6)
    assert values["explainedVariance"][0] == pytest.approx(IRIS_SDEV_SCALED[0] ** 2, abs=1e-6)
    assert values["communality"] == pytest.approx(loadings**2, abs=1e-12)


def _two_factor_data(n: int = 4000, seed: int = 3) -> tuple[pa.Table, np.ndarray]:
    rng = np.random.default_rng(seed)
    true = np.array([[0.8, 0.0], [0.7, 0.1], [0.75, 0.0], [0.0, 0.85], [0.1, 0.7], [0.0, 0.6]])
    f = rng.normal(size=(n, 2))
    noise = rng.normal(size=(n, 6)) * np.sqrt(1 - (true**2).sum(axis=1))
    x = f @ true.T + noise
    return pa.table({f"v{i}": x[:, i] for i in range(6)}), x


def test_fa_matches_full_data_fit_and_statsmodels(rel: Rel) -> None:
    from sklearn.decomposition import FactorAnalysis
    from statsmodels.multivariate.factor import Factor

    table, x = _two_factor_data()
    values = stat(
        run(rel(table), PlotType.FACTOR_LOADING, {"rotation": "none"}, y=table.column_names)
    )
    ours = np.array(values["loadings"])
    # The sufficient-statistic shortcut equals scikit-learn on all rows (standardized, ddof 1).
    z = (x - x.mean(axis=0)) / x.std(axis=0, ddof=1)
    full = FactorAnalysis(2, svd_method="lapack", tol=1e-9, max_iter=200_000).fit(z)
    # scikit-learn's covariance has divisor n; the correlation matrix has divisor n - 1.
    common_full = ((full.components_.T * math.sqrt(len(x) / (len(x) - 1))) ** 2).sum(axis=1)
    diff = np.abs(np.asarray(values["communality"]) - common_full).max()
    assert diff < 1e-6, diff
    # An independent ML implementation (statsmodels) agrees on the rotation-invariant parts.
    sm = Factor(corr=np.corrcoef(x, rowvar=False), n_factor=2, method="ml", nobs=len(x)).fit()
    assert values["communality"] == pytest.approx(sm.communality, abs=2e-3)
    assert (ours**2).sum() == pytest.approx((np.asarray(sm.loadings) ** 2).sum(), abs=5e-3)


def r_varimax(x: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    """A transcription of R's stats::varimax(normalize = TRUE) (Kaiser's SVD algorithm)."""
    sc = np.sqrt((x**2).sum(axis=1))
    xn = x / sc[:, None]
    p, nc = xn.shape
    tt = np.eye(nc)
    d = 0.0
    for _ in range(1000):
        z = xn @ tt
        b = xn.T @ (z**3 - z @ np.diag((z**2).sum(axis=0)) / p)
        u, s, vt = np.linalg.svd(b)
        tt = u @ vt
        d_past, d = d, float(s.sum())
        if d < d_past * (1 + eps):
            break
    return (xn @ tt) * sc[:, None]


def test_varimax_matches_r_algorithm() -> None:
    a = np.array([[0.8, 0.3], [0.7, 0.4], [0.6, 0.5], [0.3, 0.8], [0.2, 0.7], [0.4, 0.6]])
    ours = mv.orient(mv.varimax(a))
    ref = mv.orient(r_varimax(a))
    # Same up to column order.
    if abs(ours[0, 0] - ref[0, 0]) > abs(ours[0, 0] - ref[0, 1]):
        ref = ref[:, ::-1]
    assert ours == pytest.approx(ref, abs=1e-5)
    # Orthogonal rotation keeps communalities.
    assert (ours**2).sum(axis=1) == pytest.approx((a**2).sum(axis=1), abs=1e-10)


def test_rotation_recovers_simple_structure(rel: Rel) -> None:
    table, _ = _two_factor_data(n=20_000, seed=5)
    values = stat(run(rel(table), PlotType.FACTOR_LOADING, y=table.column_names))
    loadings = np.abs(np.array(values["loadings"]))
    first = loadings[:3].argmax(axis=1)
    second = loadings[3:].argmax(axis=1)
    assert len(set(first)) == 1 and len(set(second)) == 1 and first[0] != second[0]


def test_factor_degenerate_inputs(rel: Rel) -> None:
    r = rel({"a": [1.0, 2, 3, 4], "b": [2.0, 1, 4, 3], "c": [0.0, 1, 1, 0]})
    with pytest.raises(InvalidSpecError, match="at most 2 factors"):
        run(r, PlotType.FACTOR_LOADING, {"factors": 3}, y=["a", "b", "c"])
    with pytest.raises(InvalidSpecError, match="at most 3"):
        run(r, PlotType.FACTOR_LOADING, {"factors": 4, "method": "pca"}, y=["a", "b", "c"])
    out = run(r, PlotType.FACTOR_LOADING, {"factors": 1}, y=["a", "b", "c"])
    assert any("unstable" in w for w in out.warnings)


# --- Clustering -------------------------------------------------------------------------------


def test_kmeans_iris_known_solution(rel: Rel) -> None:
    from sklearn.metrics import silhouette_score

    table = iris_table()
    values = stat(
        run(rel(table), PlotType.CLUSTER, {"standardize": False, "k": 3}, y=IRIS, label=["species"])
    )
    # Raw iris, k = 3: Lloyd's algorithm (scikit-learn) stops at the well-known fixed point with
    # sizes 50/61/39 and total within SS 78.8557; R's Hartigan-Wong moves one more point
    # (50/62/38, 78.8514).
    assert values["inertia"] == pytest.approx(78.855666, abs=1e-5)
    assert sorted(c["size"] for c in values["centroids"]) == [39, 50, 61]
    data = np.column_stack([table.column(n).to_numpy() for n in IRIS])
    labels = np.array(values["points"]["cluster"])
    assert values["silhouette"] == pytest.approx(silhouette_score(data, labels), abs=1e-9)
    assert [e["k"] for e in values["elbow"]] == list(range(1, 11))
    inertias = [e["inertia"] for e in values["elbow"]]
    assert inertias[0] == pytest.approx(((data - data.mean(axis=0)) ** 2).sum())
    assert inertias[2] == pytest.approx(78.85, abs=1e-2)
    assert values["silhouettes"][0]["k"] == 2
    assert values["explainedVarianceRatio"][0] == pytest.approx(0.92461872, abs=1e-6)


def test_dbscan_finds_blobs_and_noise(rel: Rel) -> None:
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.1, size=(50, 2))
    b = rng.normal(5, 0.1, size=(50, 2))
    x = np.vstack([a, b, [[20.0, -20.0]]])
    out = run(
        rel({"p": x[:, 0], "q": x[:, 1]}),
        PlotType.CLUSTER,
        {"algorithm": "dbscan", "eps": 1.0, "standardize": False},
        y=["p", "q"],
    )
    values = stat(out)
    assert values["k"] == 2 and values["noise"] == 1
    assert sorted(c["size"] for c in values["centroids"]) == [50, 50]
    assert values["kDistance"] == sorted(values["kDistance"], reverse=True)
    all_noise = run(
        rel({"p": x[:, 0], "q": x[:, 1]}),
        PlotType.CLUSTER,
        {"algorithm": "dbscan", "eps": 1e-6, "standardize": False},
        y=["p", "q"],
    )
    assert stat(all_noise)["k"] == 0 and any("noise" in w for w in all_noise.warnings)


def test_cluster_degenerate_inputs(rel: Rel) -> None:
    with pytest.raises(InvalidSpecError, match="distinct rows"):
        run(
            rel({"a": [1.0, 1, 2, 2], "b": [0.0, 0, 1, 1]}),
            PlotType.CLUSTER,
            {"k": 3},
            y=["a", "b"],
        )
    with pytest.raises(InvalidSpecError, match="constant"):
        run(rel({"a": [1.0, 1, 1], "b": [2.0, 2, 2]}), PlotType.CLUSTER, y=["a", "b"])
    # Singular covariance (a duplicated field) still clusters.
    rng = np.random.default_rng(2)
    v = np.r_[rng.normal(0, 1, 30), rng.normal(10, 1, 30)]
    values = stat(run(rel({"a": v, "b": v}), PlotType.CLUSTER, {"k": 2}, y=["a", "b"]))
    assert sorted(c["size"] for c in values["centroids"]) == [30, 30]


def test_kmeans_diagnostics_runs_in_a_process_pool() -> None:
    from concurrent.futures import ProcessPoolExecutor

    x = load_iris().data
    with ProcessPoolExecutor(1) as pool:
        inertias, silhouettes = mv.run_cpu(pool, mv.kmeans_diagnostics, x, 4, 42)
    assert len(inertias) == 4 and len(silhouettes) == 3


# --- Dendrogram -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "heights"),
    [("single", [1, 2, 4]), ("complete", [1, 3, 7]), ("average", [1, 2.5, 17 / 3])],
)
def test_dendrogram_hand_computed_heights(rel: Rel, method: str, heights: list[float]) -> None:
    # Points 0, 1, 3, 7. Single: 1, 2, 4. Complete: 1, max(3, 2) = 3, 7. Average: 1,
    # (3 + 2) / 2 = 2.5, then {0, 1, 3} to 7 = (7 + 6 + 4) / 3.
    r = rel({"v": [0.0, 1, 3, 7], "name": ["a", "b", "c", "d"]})
    values = stat(
        run(
            r,
            PlotType.DENDROGRAM,
            {"method": method, "standardize": False},
            y=["v"],
            label=["name"],
        )
    )
    tops = sorted(max(d) for d in values["dcoord"])
    assert tops == pytest.approx(heights)
    assert sorted(values["leaves"]) == ["a", "b", "c", "d"]
    assert values["maxHeight"] == pytest.approx(heights[-1])


def test_dendrogram_matches_scipy_and_cuts(rel: Rel) -> None:
    from scipy.cluster.hierarchy import cophenet, dendrogram, linkage
    from scipy.spatial.distance import pdist

    rng = np.random.default_rng(4)
    x = np.vstack([rng.normal(c, 0.3, size=(8, 2)) for c in (0, 4, 8)])
    r = rel({"a": x[:, 0], "b": x[:, 1]})
    values = stat(run(r, PlotType.DENDROGRAM, {"standardize": False, "clusters": 3}, y=["a", "b"]))
    z = linkage(x, "ward")
    ref = dendrogram(z, no_plot=True)
    assert np.asarray(values["icoord"]) == pytest.approx(np.asarray(ref["icoord"]))
    assert np.asarray(values["dcoord"]) == pytest.approx(np.asarray(ref["dcoord"]))
    assert values["clusters"] == 3
    assert values["cophenetic"] == pytest.approx(cophenet(z, pdist(x))[0])
    # Three coloured subtrees plus the links above the cut.
    assert set(values["colors"]) == {-1, 0, 1, 2}
    assert sorted(z[:, 2])[-3] < values["cutHeight"] < sorted(z[:, 2])[-2]


def test_dendrogram_caps_rows_and_truncates(rel: Rel) -> None:
    rng = np.random.default_rng(6)
    r = rel({"a": rng.normal(size=60), "b": rng.normal(size=60)})
    out = run(r, PlotType.DENDROGRAM, {"maxRows": 30, "maxLeaves": 10}, y=["a", "b"])
    values = stat(out)
    assert out.sampled and out.n == 30 and out.n_total == 60
    assert any("square of the rows" in w for w in out.warnings)
    assert len(values["leaves"]) == 10 and sum(values["leafCounts"]) == 30


def test_dendrogram_degenerate_inputs(rel: Rel) -> None:
    with pytest.raises(InvalidSpecError, match="at least 2"):
        run(rel({"a": [1.0]}), PlotType.DENDROGRAM, y=["a"])
    with pytest.raises(InvalidSpecError, match="euclidean"):
        run(rel({"a": [1.0, 2]}), PlotType.DENDROGRAM, {"metric": "cosine"}, y=["a"])
    with pytest.raises(InvalidSpecError, match="constant"):
        run(rel({"a": [1.0, 1, 1]}), PlotType.DENDROGRAM, y=["a"])
    assert mv.cut_height(np.array([[0, 1, 1.0, 2], [2, 3, 2.0, 2], [4, 5, 5.0, 4]]), 2) == 3.5


# --- Andrews curves ---------------------------------------------------------------------------


def test_andrews_curve_formula(rel: Rel) -> None:
    out = run(
        rel({"a": [1.0], "b": [2.0], "c": [3.0]}),
        PlotType.ANDREWS_CURVES,
        {"scale": "none", "points": 101},
        y=["a", "b", "c"],
    )
    assert isinstance(out, TableOutput)
    t = np.array(out.table.column("x").to_pylist())
    f = np.array(out.table.column("y0").to_pylist())
    # f(t) = a / sqrt(2) + b sin t + c cos t.
    assert f == pytest.approx(1 / math.sqrt(2) + 2 * np.sin(t) + 3 * np.cos(t))
    assert f[50] == pytest.approx(1 / math.sqrt(2) + 3)
    assert f[-1] == pytest.approx(1 / math.sqrt(2) - 3)
    basis = mv.andrews_basis(np.array([0.7]), 5)[0]
    assert basis == pytest.approx(
        [1 / math.sqrt(2), math.sin(0.7), math.cos(0.7), math.sin(1.4), math.cos(1.4)]
    )


def test_andrews_samples_per_group_within_the_mark_cap(rel: Rel) -> None:
    out = run(
        rel(iris_table()),
        PlotType.ANDREWS_CURVES,
        {"maxCurves": 30, "points": 50},
        y=IRIS,
        color=["species"],
    )
    assert out.reduction == Reduction.SAMPLED
    curves = set(out.table.column("curve").to_pylist())
    assert len(curves) <= 30 and out.table.num_rows == len(curves) * 50
    assert set(out.table.column("color").to_pylist()) == {"setosa", "versicolor", "virginica"}
    capped = run(rel(iris_table()), PlotType.ANDREWS_CURVES, {"points": 100}, mark_cap=1000, y=IRIS)
    assert capped.table.num_rows <= 1000


# --- Radar ------------------------------------------------------------------------------------


def test_radar_aggregates_and_scales(rel: Rel) -> None:
    r = rel(
        {
            "g": ["A", "A", "B", "B"],
            "u": [1.0, 3, 5, 7],
            "v": [10.0, 10, 20, 40],
            "w": [0.0, 1, None, 1],
        }
    )
    out = run(r, PlotType.RADAR, y=["u", "v", "w"], color=["g"])
    assert isinstance(out, TableOutput)
    t = out.table.to_pydict()
    assert t["color"] == ["A", "B"] and t["n"] == [2, 2]
    assert t["y0"] == [2, 6] and t["y1"] == [10, 30] and t["y2"] == [0.5, 1]
    # Column range: u spans 1..7, v 10..40, w 0..1.
    assert t["y0_norm"] == pytest.approx([1 / 6, 5 / 6])
    assert t["y1_norm"] == pytest.approx([0, 2 / 3])
    grouped = run(r, PlotType.RADAR, {"scale": "group_range"}, y=["u", "v", "w"], color=["g"])
    assert grouped.table.column("y0_norm").to_pylist() == [0, 1]
    single = run(r, PlotType.RADAR, {"agg": "max"}, y=["u", "v", "w"])
    assert single.table.column("y0").to_pylist() == [7]
    assert single.table.column("y0_norm").to_pylist() == [1]


def test_radar_constant_axis_and_group_cap(rel: Rel) -> None:
    r = rel(
        {"g": ["a", "b", "c", "c"], "u": [1.0, 1, 1, 1], "v": [1.0, 2, 3, 4], "w": [1.0, 2, 3, 5]}
    )
    out = run(r, PlotType.RADAR, {"maxGroups": 2}, y=["u", "v", "w"], color=["g"])
    assert out.table.num_rows == 2
    assert out.table.column("y0_norm").to_pylist() == [0.5, 0.5]
    assert any("largest" in w for w in out.warnings)
    assert any("does not vary" in w for w in out.warnings)


# --- Parallel coordinates ---------------------------------------------------------------------


def test_parallel_coordinates_sample(rel: Rel) -> None:
    out = run(
        rel(iris_table()),
        PlotType.PARALLEL_COORDINATES,
        {"maxLines": 40},
        y=IRIS,
        color=["species"],
    )
    assert isinstance(out, TableOutput)
    assert out.table.num_rows == 40 and out.reduction == Reduction.SAMPLED
    assert out.table.column_names == ["y0", "y1", "y2", "y3", "color"]
    assert out.fields["y2"] == "petal_length"
    full = run(rel(iris_table()), PlotType.PARALLEL_COORDINATES, y=[*IRIS, "species"])
    assert full.table.num_rows == 150 and full.reduction == Reduction.NONE


def test_wrong_channel_types_are_rejected(rel: Rel) -> None:
    with pytest.raises(InvalidSpecError, match="needs a numeric"):
        run(rel(iris_table()), PlotType.PCA_BIPLOT, y=["sepal_length", "species"])
    with pytest.raises(InvalidSpecError, match="at least 3"):
        run(rel(iris_table()), PlotType.RADAR, y=["sepal_length", "sepal_width"])
