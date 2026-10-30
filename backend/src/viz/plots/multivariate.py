"""Plots.md §6 Multivariate: radar, parallel coordinates, PCA biplot, factor loadings, cluster
plot, dendrogram and Andrews curves. Owner: plots-multivariate-econometric.

Exact where cheap, sampled where not (CLAUDE.md §6):
- Radar aggregates and the covariance matrix behind PCA and factor analysis are computed in SQL
  on every eligible row (listwise: rows missing any selected field are left out). Only the
  biplot's points are a sample, projected onto the exact components.
- Clustering, the dendrogram, parallel coordinates and Andrews curves work on random samples
  (k-means 100k rows, DBSCAN 20k, hierarchical clustering 5k: its memory grows with n²).
- Pure-Python-heavy work (the linkage, the k-means sweep behind the elbow and silhouette hints)
  runs in the engine's process pool when one is given. BLAS threads are capped per process by
  the engine, so nothing here sets threadpoolctl limits of its own.
"""

from __future__ import annotations

import math
import warnings as py_warnings
from collections.abc import Callable, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pyarrow as pa
from pydantic import Field
from sqlglot import exp

from viz.contracts import PlotType, Reduction, StatResult
from viz.engine.pool import ProcessPool
from viz.plots.base import (
    CATEGORICAL,
    QUANT,
    Channel,
    PlotParams,
    PlotRequest,
    TableOutput,
    register_plot,
)
from viz.stats.errors import StatsInputError
from viz.stats.groups import cap_groups
from viz.stats.relation import Relation, col
from viz.stats.results import stat_result
from viz.stats.sampling import DEFAULT_SEED, numeric_array, reservoir_sample
from viz.stats.sql import alias, all_valid, as_double, count_if, count_star, fn, valid

MAX_FEATURES = 50
CLUSTER_CAP = 100_000
DBSCAN_CAP = 20_000
DIAGNOSTIC_CAP = 10_000
SILHOUETTE_CAP = 5_000
DENDROGRAM_CAP = 5_000
CPU_TIMEOUT_S = 120.0
DIAGNOSTIC_MAX_K = 10
NOTHING_TO_CLUSTER = "Every selected field is constant, so there is nothing to cluster."

ANY_FIELD = None
CATEGORICAL_OR_QUANT = CATEGORICAL | QUANT


# ---------------------------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------------------------


def run_cpu[T](executor: Executor | None, function: Callable[..., T], *args: object) -> T:
    """Run `function(*args)` in the process pool when one is given, else inline."""
    if executor is None:
        return function(*args)
    if isinstance(executor, ProcessPool):
        return executor.run(function, *args, timeout_s=CPU_TIMEOUT_S)
    return executor.submit(function, *args).result()


@dataclass(frozen=True)
class Rows:
    """Complete rows of the required fields (a random sample above the cap), plus extras."""

    table: pa.Table
    n: int
    # Rows with every required field present (before sampling).
    n_total: int
    # Rows left out because a required field was missing or non-finite.
    n_missing: int
    sampled: bool
    warnings: list[str] = field(default_factory=list)

    def matrix(self, fields: Sequence[str]) -> np.ndarray:
        if not fields:
            return np.empty((self.n, 0))
        return np.column_stack([numeric_array(self.table.column(f), f) for f in fields])

    def text(self, name: str) -> list[str | None]:
        return [None if v is None else _text(v) for v in self.table.column(name).to_pylist()]

    def notes(self, what: str = "rows") -> list[str]:
        notes = list(self.warnings)
        if self.n_missing:
            notes.append(
                f"Left out {self.n_missing:,} {what} with a missing value in a selected field."
            )
        return notes


def _text(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    iso = getattr(value, "isoformat", None)
    return str(iso()) if callable(iso) else str(value)


def complete_rows(
    rel: Relation,
    required: Sequence[str],
    extras: Sequence[str] = (),
    *,
    cap: int,
    by: Sequence[str] = (),
    seed: int = DEFAULT_SEED,
    min_rows: int = 1,
) -> Rows:
    """Rows where every `required` field has a usable value; extras may be null.

    Above `cap` rows a seeded random sample is taken (with `by`, a per-group sample so every
    group shows; at most `cap` rows overall).
    """
    for name in (*required, *extras, *by):
        rel.require(name)
    condition = all_valid([rel.columns[f] for f in required])
    if condition is None:
        raise ValueError("complete_rows needs at least one required field")
    total, eligible = rel.rows(
        rel.select(alias(count_star(), "total"), alias(count_if(condition), "eligible"))
    )[0]
    eligible = int(eligible)
    if eligible < min_rows:
        if eligible == 0:
            raise StatsInputError("No rows have a value in every selected field.")
        raise StatsInputError(
            f"This chart needs at least {min_rows} complete rows; there are {eligible}."
        )
    names = list(dict.fromkeys([*required, *extras]))
    names = [n for n in names if n not in by]
    sample = reservoir_sample(
        rel.narrow(condition),
        names,
        cap=cap,
        by=by,
        seed=seed,
        dropna=False,
        total_cap=cap,
    )
    return Rows(
        table=sample.table,
        n=sample.n,
        n_total=eligible,
        n_missing=int(total) - eligible,
        sampled=sample.n < eligible,
        warnings=list(sample.warnings),
    )


def _quant_fields(req: PlotRequest, channel: str) -> list[str]:
    names = req.fields(channel)
    if len(set(names)) != len(names):
        raise StatsInputError("Each field can be chosen only once.")
    return names


@dataclass(frozen=True)
class Moments:
    """Means and the sample covariance matrix (ddof 1) of complete rows, computed exactly."""

    names: list[str]
    n: int
    n_missing: int
    mean: np.ndarray
    cov: np.ndarray


def exact_moments(rel: Relation, names: Sequence[str]) -> Moments:
    """One SQL pass: count, means and every pairwise covar_samp over the complete rows."""
    for name in names:
        rel.require(name, QUANT)
    condition = all_valid([rel.columns[f] for f in names])
    assert condition is not None
    p = len(names)
    selects: list[exp.Expr] = [alias(count_star(), "_n")]
    selects += [alias(fn("avg", as_double(col(f))), f"_m{i}") for i, f in enumerate(names)]
    for i in range(p):
        for j in range(i, p):
            pair = fn("covar_samp", as_double(col(names[i])), as_double(col(names[j])))
            selects.append(alias(pair, f"_c{i}_{j}"))
    total = rel.rows(rel.select(alias(count_star(), "_t")))[0][0]
    row = rel.rows(rel.narrow(condition).select(*selects))[0]
    n = int(row[0])
    if n < 3:
        raise StatsInputError(
            f"This chart needs at least 3 rows with every selected field; there are {n}."
        )
    mean = np.array([float(v) for v in row[1 : 1 + p]])
    cov = np.zeros((p, p))
    k = 1 + p
    for i in range(p):
        for j in range(i, p):
            value = row[k]
            cov[i, j] = cov[j, i] = float(value) if value is not None else 0.0
            k += 1
    if not (np.all(np.isfinite(cov)) and np.all(np.isfinite(mean))):
        raise StatsInputError("The values are too large to analyse.")
    return Moments(list(names), n, int(total) - n, mean, cov)


def drop_constant(
    names: list[str], cov: np.ndarray, *, min_fields: int = 2
) -> tuple[list[int], list[str]]:
    """Indices of fields with variation, plus a warning naming the constant ones."""
    scale = float(np.max(np.abs(np.diag(cov)))) if cov.size else 0.0
    keep = [i for i in range(len(names)) if cov[i, i] > max(scale, 1e-300) * 1e-14]
    dropped = [names[i] for i in range(len(names)) if i not in keep]
    notes = []
    if dropped:
        notes.append(f"Left out fields with no variation: {', '.join(dropped)}.")
    if len(keep) < min_fields:
        detail = f" ({', '.join(dropped)} {'is' if len(dropped) == 1 else 'are'} constant)"
        raise StatsInputError(
            f"This chart needs at least {min_fields} fields that vary"
            + (detail if dropped else "")
            + "."
        )
    return keep, notes


def correlation(cov: np.ndarray) -> np.ndarray:
    sd = np.sqrt(np.diag(cov))
    corr = cov / np.outer(sd, sd)
    np.fill_diagonal(corr, 1.0)
    return np.asarray(np.clip(corr, -1.0, 1.0))


def sorted_eigen(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Eigenvalues (descending, clipped at 0) and unit eigenvectors as columns.

    Signs are fixed so the largest-magnitude entry of each vector is positive (as scikit-learn's
    svd_flip), so the same data always draws the same picture.
    """
    values, vectors = np.linalg.eigh((matrix + matrix.T) / 2)
    order = np.argsort(values)[::-1]
    values = np.clip(values[order], 0.0, None)
    vectors = vectors[:, order]
    biggest = np.argmax(np.abs(vectors), axis=0)
    signs = np.sign(vectors[biggest, np.arange(vectors.shape[1])])
    signs[signs == 0] = 1.0
    return values, vectors * signs


def standardize(x: np.ndarray, ddof: int = 1) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = x.mean(axis=0)
    sd = x.std(axis=0, ddof=ddof) if len(x) > ddof else np.zeros(x.shape[1])
    safe = np.where(sd > 0, sd, 1.0)
    return (x - mean) / safe, mean, sd


def _subset(n: int, cap: int, seed: int) -> np.ndarray:
    if n <= cap:
        return np.arange(n)
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(n, cap, replace=False))


# ---------------------------------------------------------------------------------------------
# Radar
# ---------------------------------------------------------------------------------------------

RadarAgg = Literal["mean", "median", "sum", "min", "max"]
_RADAR_AGG = {"mean": "avg", "median": "median", "sum": "sum", "min": "min", "max": "max"}


class RadarParams(PlotParams):
    agg: RadarAgg = "mean"
    # column_range: (agg - column min) / (column max - column min) over all rows;
    # group_range: min-max across the groups' aggregates; none: raw values.
    scale: Literal["column_range", "group_range", "none"] = "column_range"
    max_groups: int = Field(default=12, ge=1, le=50)
    # Cosmetic (client-side).
    fill: bool = True


@register_plot(
    PlotType.RADAR,
    channels=[Channel("y", QUANT, min=3, max=12), Channel("color", CATEGORICAL, min=0)],
    params=RadarParams,
)
def radar(req: PlotRequest) -> TableOutput:
    params = req.params
    assert isinstance(params, RadarParams)
    rel = req.relation
    fields = _quant_fields(req, "y")
    color = req.field("color")
    warnings: list[str] = []
    if color is not None:
        groups = cap_groups(rel, [color], params.max_groups)
        rel = groups.relation
        if groups.warning:
            warnings.append(groups.warning)
    agg_name = _RADAR_AGG[params.agg]

    def usable(name: str) -> exp.Expr:
        return exp.Case(ifs=[exp.If(this=valid(rel.columns[name]), true=as_double(col(name)))])

    selects: list[exp.Expr] = []
    if color is not None:
        selects.append(alias(exp.cast(col(color), "VARCHAR"), "color"))
    selects.append(alias(exp.cast(count_star(), "DOUBLE"), "n"))
    for i, name in enumerate(fields):
        selects.append(alias(as_double(fn(agg_name, usable(name))), f"y{i}"))
    query = rel.select(*selects)
    if color is not None:
        query = query.group_by(col(color))
    table = rel.arrow(query)
    if color is not None:
        table = table.sort_by([("color", "ascending")])
    if table.num_rows == 0 or (color is None and table.column("n")[0].as_py() == 0):
        raise StatsInputError("There are no rows to aggregate.")
    ranges = rel.rows(
        rel.select(
            *[
                item
                for i, name in enumerate(fields)
                for item in (
                    alias(fn("min", usable(name)), f"lo{i}"),
                    alias(fn("max", usable(name)), f"hi{i}"),
                )
            ]
        )
    )[0]
    column_ranges = [(ranges[2 * i], ranges[2 * i + 1]) for i in range(len(fields))]
    for i, name in enumerate(fields):
        values = np.array(
            [np.nan if v is None else float(v) for v in table.column(f"y{i}").to_pylist()]
        )
        if params.scale == "column_range":
            lo, hi = column_ranges[i]
        elif params.scale == "group_range":
            finite = values[np.isfinite(values)]
            lo, hi = (float(finite.min()), float(finite.max())) if finite.size else (None, None)
        else:
            lo = hi = None
        if lo is None or hi is None:
            normalized = values
            if params.scale != "none":
                warnings.append(f"'{name}' has no values.")
        elif hi > lo:
            normalized = (values - lo) / (hi - lo)
        else:
            normalized = np.where(np.isfinite(values), 0.5, np.nan)
            warnings.append(f"'{name}' does not vary, so it sits in the middle of its axis.")
        table = table.append_column(
            f"y{i}_norm",
            pa.array(
                [None if not math.isfinite(v) else float(v) for v in normalized], pa.float64()
            ),
        )
    if params.agg == "sum" and params.scale == "column_range":
        warnings.append(
            "Sums are scaled by the range of single values, so they can fall outside 0-1; "
            "choose 'Across groups' scaling for sums."
        )
    fields_map = {"n": "Rows"}
    if color is not None:
        fields_map["color"] = color
    for i, name in enumerate(fields):
        fields_map[f"y{i}"] = name
        fields_map[f"y{i}_norm"] = f"{name} (scaled)"
    return TableOutput(
        table=table,
        reduction=Reduction.AGGREGATED,
        method=f"{params.agg} per group, scaled by {params.scale.replace('_', ' ')}",
        fields=fields_map,
        params={
            "agg": params.agg,
            "scale": params.scale,
            "ranges": [[lo, hi] for lo, hi in column_ranges],
        },
        warnings=warnings,
    )


# ---------------------------------------------------------------------------------------------
# Parallel coordinates
# ---------------------------------------------------------------------------------------------


class ParallelParams(PlotParams):
    max_lines: int = Field(default=1000, ge=10, le=10_000)
    # Cosmetic.
    opacity: float = Field(default=0.35, ge=0.02, le=1.0)
    smooth: bool = False


@register_plot(
    PlotType.PARALLEL_COORDINATES,
    channels=[
        Channel("y", CATEGORICAL_OR_QUANT, min=2, max=15),
        Channel("color", ANY_FIELD, min=0),
    ],
    params=ParallelParams,
)
def parallel_coordinates(req: PlotRequest) -> TableOutput:
    params = req.params
    assert isinstance(params, ParallelParams)
    fields = _quant_fields(req, "y")
    color = req.field("color")
    cap = max(1, min(params.max_lines, req.mark_cap // len(fields)))
    rows = complete_rows(
        req.relation, fields, [color] if color and color not in fields else [], cap=cap
    )
    columns: dict[str, pa.Array | pa.ChunkedArray] = {}
    labels: dict[str, str] = {}
    for i, name in enumerate(fields):
        columns[f"y{i}"] = rows.table.column(name)
        labels[f"y{i}"] = name
    if color is not None:
        columns["color"] = rows.table.column(color)
        labels["color"] = color
    warnings = rows.notes()
    if rows.sampled:
        warnings.append(f"Showing a random sample of {rows.n:,} of {rows.n_total:,} rows.")
    return TableOutput(
        table=pa.table(columns),
        reduction=Reduction.SAMPLED if rows.sampled else Reduction.NONE,
        method="reservoir" if rows.sampled else None,
        fields=labels,
        rows_matched=rows.n_total,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------------------------
# PCA biplot
# ---------------------------------------------------------------------------------------------


class PcaParams(PlotParams):
    standardize: bool = True
    x_component: int = Field(default=1, ge=1, le=MAX_FEATURES)
    y_component: int = Field(default=2, ge=1, le=MAX_FEATURES)
    max_points: int = Field(default=2000, ge=10, le=20_000)
    # Cosmetic.
    show_points: bool = True
    show_loadings: bool = True
    label_points: bool = False


@dataclass(frozen=True)
class PcaFit:
    names: list[str]
    n: int
    n_missing: int
    mean: np.ndarray
    # Divisor per field (the SD when standardized, else 1).
    scale: np.ndarray
    eigenvalues: np.ndarray
    # Unit eigenvectors as columns (fields x components).
    vectors: np.ndarray
    standardized: bool
    warnings: list[str]

    @property
    def ratio(self) -> np.ndarray:
        total = float(self.eigenvalues.sum())
        return self.eigenvalues / total if total > 0 else np.zeros_like(self.eigenvalues)

    def loadings(self) -> np.ndarray:
        """Eigenvectors scaled by sqrt(eigenvalue): field-component correlations when
        standardized (covariances / component SD otherwise)."""
        return self.vectors * np.sqrt(self.eigenvalues)

    def scores(self, x: np.ndarray) -> np.ndarray:
        return np.asarray(((x - self.mean) / self.scale) @ self.vectors)


def pca_fit(rel: Relation, names: Sequence[str], *, standardize_fields: bool) -> PcaFit:
    """Principal components of the exact covariance (or correlation) matrix of complete rows."""
    moments = exact_moments(rel, names)
    keep, notes = drop_constant(moments.names, moments.cov)
    cov = moments.cov[np.ix_(keep, keep)]
    kept = [moments.names[i] for i in keep]
    mean = moments.mean[keep]
    sd = np.sqrt(np.diag(cov))
    matrix = correlation(cov) if standardize_fields else cov
    values, vectors = sorted_eigen(matrix)
    tiny = values <= values[0] * 1e-10
    if np.any(tiny[: len(kept)]):
        notes.append(
            f"{int(tiny.sum())} component(s) explain no variance: some fields are exact linear "
            "combinations of others."
        )
    return PcaFit(
        names=kept,
        n=moments.n,
        n_missing=moments.n_missing,
        mean=mean,
        scale=sd if standardize_fields else np.ones_like(sd),
        eigenvalues=values,
        vectors=vectors,
        standardized=standardize_fields,
        warnings=notes,
    )


@register_plot(
    PlotType.PCA_BIPLOT,
    channels=[
        Channel("y", QUANT, min=2, max=MAX_FEATURES),
        Channel("color", ANY_FIELD, min=0),
        Channel("label", ANY_FIELD, min=0),
    ],
    params=PcaParams,
)
def pca_biplot(req: PlotRequest) -> StatResult:
    params = req.params
    assert isinstance(params, PcaParams)
    fields = _quant_fields(req, "y")
    fit = pca_fit(req.relation, fields, standardize_fields=params.standardize)
    components = len(fit.eigenvalues)
    ix, iy = params.x_component - 1, params.y_component - 1
    if ix >= components or iy >= components:
        raise StatsInputError(f"There are only {components} components to choose from.")
    color, label = req.field("color"), req.field("label")
    extras = [f for f in (color, label) if f is not None and f not in fit.names]
    rows = complete_rows(req.relation, fit.names, extras, cap=min(params.max_points, req.mark_cap))
    scores = fit.scores(rows.matrix(fit.names))
    loadings = fit.loadings()
    sx, sy = scores[:, ix], scores[:, iy]
    lx, ly = loadings[:, ix], loadings[:, iy]
    arrow_scale = 1.0
    reach = max(float(np.max(np.abs(lx))), float(np.max(np.abs(ly))), 1e-300)
    spread = max(
        float(np.max(np.abs(sx))) if sx.size else 0.0, float(np.max(np.abs(sy))) if sy.size else 0.0
    )
    if spread > 0:
        arrow_scale = 0.8 * spread / reach
    notes = list(fit.warnings)
    if rows.sampled:
        notes.append(f"Components use all {fit.n:,} rows; {rows.n:,} of them are drawn as points.")
    ratio = fit.ratio
    values = {
        "variables": fit.names,
        "standardized": fit.standardized,
        "components": [params.x_component, params.y_component],
        "eigenvalues": fit.eigenvalues,
        "explainedVarianceRatio": ratio,
        "cumulativeRatio": np.cumsum(ratio),
        "loadings": [
            {"variable": name, "x": float(lx[k]), "y": float(ly[k])}
            for k, name in enumerate(fit.names)
        ],
        "arrowScale": arrow_scale,
        "points": {
            "x": sx,
            "y": sy,
            "color": rows.text(color) if color else None,
            "label": rows.text(label) if label else None,
        },
    }
    what = "correlation" if fit.standardized else "covariance"
    return stat_result(
        values,
        method=f"PCA of the {what} matrix (exact, {fit.n:,} complete rows)",
        n=fit.n,
        n_missing=fit.n_missing,
        sampled=False,
        warnings=notes,
    )


# ---------------------------------------------------------------------------------------------
# Factor loadings
# ---------------------------------------------------------------------------------------------


class FactorParams(PlotParams):
    method: Literal["fa", "pca"] = "fa"
    factors: int = Field(default=2, ge=1, le=10)
    rotation: Literal["varimax", "quartimax", "none"] = "varimax"
    # Cosmetic.
    display: Literal["heatmap", "bars"] = "heatmap"
    show_values: bool = True


def varimax(
    loadings: np.ndarray, method: Literal["varimax", "quartimax"] = "varimax"
) -> np.ndarray:
    """Orthogonal rotation with Kaiser normalization (as R's varimax(normalize = TRUE)).

    The criterion is optimised by statsmodels' gradient projection algorithm on the
    row-normalized loadings, which are then scaled back.
    """
    from statsmodels.multivariate.factor_rotation import rotate_factors

    if loadings.shape[1] < 2:
        return loadings
    h = np.sqrt((loadings**2).sum(axis=1))
    safe = np.where(h > 0, h, 1.0)
    rotated, _ = rotate_factors(loadings / safe[:, None], method)
    return np.asarray(rotated) * safe[:, None]


def _fa_sufficient_rows(corr: np.ndarray) -> np.ndarray:
    """A 2p x p matrix with mean 0 and population covariance exactly `corr`.

    Maximum-likelihood factor analysis depends on the data only through its covariance, so
    fitting scikit-learn's FactorAnalysis to these rows gives the full-data solution at a cost
    that doesn't grow with n.
    """
    values, vectors = np.linalg.eigh(corr)
    root = np.sqrt(np.clip(values, 0.0, None))[:, None] * vectors.T  # root.T @ root == corr
    p = corr.shape[0]
    return math.sqrt(p) * np.vstack([root, -root])


def factor_analysis(corr: np.ndarray, factors: int) -> tuple[np.ndarray, np.ndarray, bool]:
    """ML factor loadings (p x k) and uniquenesses of a correlation matrix (scikit-learn)."""
    from sklearn.decomposition import FactorAnalysis
    from sklearn.exceptions import ConvergenceWarning

    rows = _fa_sufficient_rows(corr)
    # scikit-learn's tolerance applies to the log-likelihood summed over rows; with only 2p rows
    # it must be tight. EM steps on a 2p x p matrix take microseconds, so many are affordable.
    model = FactorAnalysis(
        n_components=factors, svd_method="lapack", tol=1e-12, max_iter=200_000, random_state=0
    )
    with py_warnings.catch_warnings(record=True) as caught:
        py_warnings.simplefilter("always", ConvergenceWarning)
        model.fit(rows)
    converged = not any(issubclass(w.category, ConvergenceWarning) for w in caught)
    return np.asarray(model.components_.T), np.asarray(model.noise_variance_), converged


def orient(loadings: np.ndarray) -> np.ndarray:
    """Flip each factor so its loadings sum to a positive number (a display convention)."""
    signs = np.sign(loadings.sum(axis=0))
    signs[signs == 0] = 1.0
    return loadings * signs


@register_plot(
    PlotType.FACTOR_LOADING,
    channels=[Channel("y", QUANT, min=2, max=MAX_FEATURES)],
    params=FactorParams,
)
def factor_loading(req: PlotRequest) -> StatResult:
    params = req.params
    assert isinstance(params, FactorParams)
    fields = _quant_fields(req, "y")
    moments = exact_moments(req.relation, fields)
    keep, notes = drop_constant(moments.names, moments.cov)
    names = [moments.names[i] for i in keep]
    corr = correlation(moments.cov[np.ix_(keep, keep)])
    p = len(names)
    k = params.factors
    if k > p or (params.method == "fa" and k >= p):
        limit = p - 1 if params.method == "fa" else p
        raise StatsInputError(
            f"With {p} fields, choose at most {limit} factor{'s' if limit != 1 else ''}."
        )
    if params.method == "fa":
        loadings, uniqueness, converged = factor_analysis(corr, k)
        if not converged:
            notes.append("Factor analysis did not fully converge; treat the loadings as rough.")
        if np.any(uniqueness < 0.005):
            notes.append(
                "Some uniquenesses are near zero (a Heywood case): a factor may be driven by a "
                "single field."
            )
        method = f"Maximum-likelihood factor analysis of the correlation matrix ({k} factors)"
    else:
        values, vectors = sorted_eigen(corr)
        loadings = vectors[:, :k] * np.sqrt(values[:k])
        uniqueness = 1.0 - (loadings**2).sum(axis=1)
        method = f"Principal-component loadings of the correlation matrix ({k} components)"
    if params.rotation != "none" and k > 1:
        loadings = varimax(loadings, params.rotation)
        method += f", {params.rotation} rotation"
    loadings = orient(loadings)
    explained = (loadings**2).sum(axis=0)
    order = np.argsort(-explained, kind="stable")
    loadings, explained = loadings[:, order], explained[order]
    communality = (loadings**2).sum(axis=1)
    if moments.n < 5 * p:
        notes.append(
            f"Only {moments.n} rows for {p} fields: loadings from fewer than 5 rows per field "
            "are unstable."
        )
    prefix = "Factor" if params.method == "fa" else "PC"
    return stat_result(
        {
            "variables": names,
            "factors": [f"{prefix} {i + 1}" for i in range(k)],
            "loadings": loadings,
            "communality": communality,
            "uniqueness": uniqueness if params.method == "fa" else 1.0 - communality,
            "explainedVariance": explained,
            "explainedVarianceRatio": explained / p,
            "method": params.method,
            "rotation": params.rotation if k > 1 else "none",
        },
        method=method + f" (exact, {moments.n:,} complete rows)",
        n=moments.n,
        n_missing=moments.n_missing,
        sampled=False,
        warnings=notes,
    )


# ---------------------------------------------------------------------------------------------
# Cluster plot
# ---------------------------------------------------------------------------------------------


class ClusterParams(PlotParams):
    algorithm: Literal["kmeans", "dbscan"] = "kmeans"
    k: int = Field(default=3, ge=2, le=20)
    eps: float = Field(default=0.5, gt=0, le=1e9)
    min_samples: int = Field(default=5, ge=2, le=1000)
    standardize: bool = True
    max_points: int = Field(default=2000, ge=10, le=20_000)
    diagnostics: bool = True
    # Cosmetic.
    show_centroids: bool = True


def kmeans_fit(x: np.ndarray, k: int, seed: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Labels, centres and inertia of k-means (k-means++, 4 seeded starts)."""
    from sklearn.cluster import KMeans

    model = KMeans(n_clusters=k, n_init=4, random_state=seed).fit(x)
    return np.asarray(model.labels_), np.asarray(model.cluster_centers_), float(model.inertia_)


def kmeans_diagnostics(
    x: np.ndarray, max_k: int, seed: int
) -> tuple[list[float], list[float | None]]:
    """Inertia for k = 1..max_k and the silhouette for k = 2..max_k (on <= 5k rows).

    Top-level and picklable: runs in the process pool.
    """
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    inertias: list[float] = []
    silhouettes: list[float | None] = []
    distinct = len(np.unique(x, axis=0))
    for k in range(1, max_k + 1):
        if k > distinct:
            break
        model = KMeans(n_clusters=k, n_init=2, random_state=seed).fit(x)
        inertias.append(float(model.inertia_))
        if k >= 2:
            labels = model.labels_
            if len(np.unique(labels)) < 2:
                silhouettes.append(None)
                continue
            size = min(len(x), SILHOUETTE_CAP)
            silhouettes.append(
                float(silhouette_score(x, labels, sample_size=size, random_state=seed))
            )
    return inertias, silhouettes


def k_distance(x: np.ndarray, min_samples: int, points: int = 200) -> list[float]:
    """Sorted (descending) distance of each row to its min_samples-th neighbour: the DBSCAN
    eps hint (pick eps at the elbow)."""
    from sklearn.neighbors import NearestNeighbors

    neighbours = min(min_samples, len(x))
    distances, _ = NearestNeighbors(n_neighbors=neighbours).fit(x).kneighbors(x)
    kth = np.sort(distances[:, -1])[::-1]
    idx = np.unique(np.linspace(0, len(kth) - 1, min(points, len(kth))).round().astype(int))
    return [float(v) for v in kth[idx]]


def dbscan_fit(x: np.ndarray, eps: float, min_samples: int) -> np.ndarray:
    from sklearn.cluster import DBSCAN

    return np.asarray(DBSCAN(eps=eps, min_samples=min_samples).fit(x).labels_)


def _projection(x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Mean, 2 principal axes (as columns) and their explained-variance ratios."""
    mean = x.mean(axis=0)
    cov = np.cov(x - mean, rowvar=False) if len(x) > 1 else np.zeros((x.shape[1], x.shape[1]))
    values, vectors = sorted_eigen(np.atleast_2d(cov))
    total = float(values.sum())
    ratio = values / total if total > 0 else np.zeros_like(values)
    axes = vectors[:, :2]
    if axes.shape[1] < 2:
        axes = np.column_stack([axes, np.zeros(len(axes))])
        ratio = np.append(ratio, 0.0)
    return mean, axes, ratio[:2]


@register_plot(
    PlotType.CLUSTER,
    channels=[
        Channel("y", QUANT, min=2, max=MAX_FEATURES),
        Channel("label", ANY_FIELD, min=0),
    ],
    params=ClusterParams,
)
def cluster(req: PlotRequest) -> StatResult:
    params = req.params
    assert isinstance(params, ClusterParams)
    fields = _quant_fields(req, "y")
    label = req.field("label")
    cap = CLUSTER_CAP if params.algorithm == "kmeans" else DBSCAN_CAP
    rows = complete_rows(
        req.relation, fields, [label] if label and label not in fields else [], cap=cap, min_rows=2
    )
    raw = rows.matrix(fields)
    notes = rows.notes()
    sd = raw.std(axis=0, ddof=1)
    varying = [i for i in range(len(fields)) if sd[i] > 0]
    if len(varying) < len(fields):
        constant = [fields[i] for i in range(len(fields)) if i not in varying]
        notes.append(f"Left out fields with no variation: {', '.join(constant)}.")
    if not varying:
        raise StatsInputError(NOTHING_TO_CLUSTER)
    used = [fields[i] for i in varying]
    x = raw[:, varying]
    if params.standardize:
        x, _, _ = standardize(x)
    seed = DEFAULT_SEED
    distinct = len(np.unique(x, axis=0))
    values: dict[str, object] = {"variables": used, "algorithm": params.algorithm}
    if params.algorithm == "kmeans":
        if params.k > distinct:
            raise StatsInputError(
                f"k = {params.k} needs at least {params.k} distinct rows; there are {distinct}."
            )
        labels, centres, inertia = kmeans_fit(x, params.k, seed)
        values["k"] = params.k
        values["inertia"] = inertia
        method = f"k-means (k = {params.k}, k-means++ with 4 seeded starts)"
        if params.diagnostics:
            sub = x[_subset(len(x), DIAGNOSTIC_CAP, seed)]
            inertias, silhouettes = run_cpu(
                req.executor, kmeans_diagnostics, sub, DIAGNOSTIC_MAX_K, seed
            )
            values["elbow"] = [{"k": i + 1, "inertia": v} for i, v in enumerate(inertias)]
            values["silhouettes"] = [
                {"k": i + 2, "silhouette": v} for i, v in enumerate(silhouettes)
            ]
            if len(sub) < len(x):
                notes.append(
                    f"The elbow and silhouette hints use a random {len(sub):,} of the rows."
                )
    else:
        labels = dbscan_fit(x, params.eps, params.min_samples)
        found = sorted(int(c) for c in np.unique(labels) if c >= 0)
        centres = (
            np.vstack([x[labels == c].mean(axis=0) for c in found])
            if found
            else np.empty((0, x.shape[1]))
        )
        noise = int((labels < 0).sum())
        values["k"] = len(found)
        values["noise"] = noise
        values["eps"] = params.eps
        values["minSamples"] = params.min_samples
        sub = x[_subset(len(x), SILHOUETTE_CAP, seed)]
        values["kDistance"] = run_cpu(req.executor, k_distance, sub, params.min_samples)
        method = f"DBSCAN (eps = {params.eps:g}, min samples = {params.min_samples})"
        if not found:
            notes.append(
                "DBSCAN marked every point as noise: increase eps or lower min samples "
                "(the k-distance curve's elbow is a good eps)."
            )
        elif noise:
            notes.append(f"{noise:,} points are noise (in no cluster).")
    silhouette = None
    clustered = labels >= 0
    if len(np.unique(labels[clustered])) >= 2:
        from sklearn.metrics import silhouette_score

        size = min(int(clustered.sum()), SILHOUETTE_CAP)
        silhouette = float(
            silhouette_score(x[clustered], labels[clustered], sample_size=size, random_state=seed)
        )
    values["silhouette"] = silhouette
    mean, axes, ratio = _projection(x)
    display = _subset(len(x), min(params.max_points, req.mark_cap), seed)
    projected = (x[display] - mean) @ axes
    sizes = [int((labels == c).sum()) for c in range(len(centres))]
    if params.algorithm == "dbscan":
        found_ids = sorted(int(c) for c in np.unique(labels) if c >= 0)
        sizes = [int((labels == c).sum()) for c in found_ids]
    centre_xy = (centres - mean) @ axes if len(centres) else np.empty((0, 2))
    label_names = rows.text(label) if label else None
    values["explainedVarianceRatio"] = ratio
    values["points"] = {
        "x": projected[:, 0],
        "y": projected[:, 1],
        "cluster": labels[display].astype(int),
        "label": [label_names[i] for i in display] if label_names is not None else None,
    }
    values["centroids"] = [
        {"cluster": i, "x": float(centre_xy[i, 0]), "y": float(centre_xy[i, 1]), "size": sizes[i]}
        for i in range(len(centre_xy))
    ]
    values["standardized"] = params.standardize
    if len(display) < len(x):
        notes.append(f"Showing {len(display):,} of the {len(x):,} clustered points.")
    return stat_result(
        values,
        method=method + "; shown on the first two principal components",
        n=rows.n,
        n_total=rows.n_total,
        n_missing=rows.n_missing,
        warnings=notes,
    )


# ---------------------------------------------------------------------------------------------
# Dendrogram
# ---------------------------------------------------------------------------------------------


class DendrogramParams(PlotParams):
    method: Literal["ward", "complete", "average", "single"] = "ward"
    metric: Literal["euclidean", "cityblock", "cosine"] = "euclidean"
    standardize: bool = True
    max_rows: int = Field(default=2000, ge=2, le=DENDROGRAM_CAP)
    max_leaves: int = Field(default=40, ge=2, le=500)
    # 0 = automatic colouring (70% of the tallest merge, as SciPy).
    clusters: int = Field(default=0, ge=0, le=50)
    # Cosmetic.
    orientation: Literal["top", "left"] = "top"


def linkage_matrix(x: np.ndarray, method: str, metric: str) -> np.ndarray:
    """SciPy hierarchical clustering. Top-level and picklable: runs in the process pool."""
    from scipy.cluster.hierarchy import linkage

    return np.asarray(linkage(x, method=method, metric=metric))


def cut_height(z: np.ndarray, clusters: int) -> float:
    """A height that cuts the tree into `clusters` groups (0 = 70% of the tallest merge)."""
    heights = np.sort(z[:, 2])
    if clusters <= 0:
        return 0.7 * float(heights[-1])
    n = len(heights) + 1
    if clusters >= n:
        return float(heights[0]) / 2
    if clusters <= 1:
        return float(heights[-1]) * 1.01 + 1e-12
    upper = heights[n - clusters]  # the merge that would join the last two of the k groups
    lower = heights[n - clusters - 1]
    return float((upper + lower) / 2)


@register_plot(
    PlotType.DENDROGRAM,
    channels=[
        Channel("y", QUANT, min=1, max=MAX_FEATURES),
        Channel("label", ANY_FIELD, min=0),
    ],
    params=DendrogramParams,
)
def dendrogram(req: PlotRequest) -> StatResult:
    from scipy.cluster.hierarchy import cophenet, fcluster
    from scipy.cluster.hierarchy import dendrogram as scipy_dendrogram

    params = req.params
    assert isinstance(params, DendrogramParams)
    if params.method == "ward" and params.metric != "euclidean":
        raise StatsInputError("Ward linkage needs euclidean distance.")
    fields = _quant_fields(req, "y")
    label = req.field("label")
    rows = complete_rows(
        req.relation,
        fields,
        [label] if label and label not in fields else [],
        cap=params.max_rows,
        min_rows=2,
    )
    x = rows.matrix(fields)
    notes = rows.notes()
    if rows.sampled:
        notes.append(
            f"Clustered a random {rows.n:,} of {rows.n_total:,} rows: hierarchical clustering "
            f"needs memory that grows with the square of the rows (limit {params.max_rows:,})."
        )
    if params.standardize:
        x, _, sd = standardize(x)
        if np.all(sd == 0):
            raise StatsInputError(NOTHING_TO_CLUSTER)
    if params.metric == "cosine" and np.any(np.all(x == 0, axis=1)):
        raise StatsInputError("Cosine distance is undefined for rows that are all zero.")
    z = run_cpu(req.executor, linkage_matrix, x, params.method, params.metric)
    n = len(x)
    names = rows.text(label) if label else [f"Row {i + 1}" for i in range(n)]
    leaf_names = [name if name is not None else "(missing)" for name in names]
    threshold = cut_height(z, params.clusters)
    membership = fcluster(z, threshold, criterion="distance")
    # Colour each link by the cluster below it when the whole subtree is one cluster.
    node_cluster: dict[int, int] = {i: int(membership[i]) - 1 for i in range(n)}
    for i, (a, b, height, _count) in enumerate(z):
        ca, cb = node_cluster[int(a)], node_cluster[int(b)]
        node_cluster[n + i] = ca if ca == cb and ca >= 0 and height < threshold else -1

    def link_color(node: int) -> str:
        return f"c{node_cluster.get(node, -1)}"

    truncate = n > params.max_leaves
    tree = scipy_dendrogram(
        z,
        no_plot=True,
        labels=leaf_names,
        truncate_mode="lastp" if truncate else None,
        p=params.max_leaves,
        link_color_func=link_color,
        show_leaf_counts=True,
    )
    colors = [int(c[1:]) for c in tree["color_list"]]
    coph, _ = cophenet(z, _pdist(x, params.metric))
    leaves = list(tree["ivl"])
    leaf_counts = [int(z[leaf - n, 3]) if leaf >= n else 1 for leaf in tree["leaves"]]
    n_clusters = len(np.unique(membership))
    if truncate:
        notes.append(
            f"Showing the top {params.max_leaves} merges; leaves like '(12)' stand for that "
            "many rows."
        )
    return stat_result(
        {
            "icoord": tree["icoord"],
            "dcoord": tree["dcoord"],
            "colors": colors,
            "leaves": leaves,
            "leafCounts": leaf_counts,
            "cutHeight": threshold,
            "clusters": n_clusters,
            "maxHeight": float(z[:, 2].max()),
            "cophenetic": float(coph),
            "standardized": params.standardize,
        },
        method=f"Hierarchical clustering ({params.method} linkage, {params.metric} distance)",
        n=rows.n,
        n_total=rows.n_total,
        n_missing=rows.n_missing,
        warnings=notes,
    )


def _pdist(x: np.ndarray, metric: str) -> np.ndarray:
    from scipy.spatial.distance import pdist

    return np.asarray(pdist(x, metric=metric))


# ---------------------------------------------------------------------------------------------
# Andrews curves
# ---------------------------------------------------------------------------------------------


class AndrewsParams(PlotParams):
    max_curves: int = Field(default=200, ge=1, le=2000)
    points: int = Field(default=100, ge=16, le=500)
    scale: Literal["zscore", "minmax", "none"] = "zscore"
    # Cosmetic.
    opacity: float = Field(default=0.4, ge=0.02, le=1.0)


def andrews_basis(t: np.ndarray, p: int) -> np.ndarray:
    """Rows of the Andrews basis at each t: 1/sqrt(2), sin t, cos t, sin 2t, cos 2t, ..."""
    basis = np.empty((len(t), p))
    basis[:, 0] = 1.0 / math.sqrt(2.0)
    for j in range(1, p):
        harmonic = (j + 1) // 2
        basis[:, j] = np.sin(harmonic * t) if j % 2 == 1 else np.cos(harmonic * t)
    return basis


@register_plot(
    PlotType.ANDREWS_CURVES,
    channels=[
        Channel("y", QUANT, min=2, max=20),
        Channel("color", CATEGORICAL, min=0),
    ],
    params=AndrewsParams,
)
def andrews_curves(req: PlotRequest) -> TableOutput:
    params = req.params
    assert isinstance(params, AndrewsParams)
    fields = _quant_fields(req, "y")
    color = req.field("color")
    curves = max(1, min(params.max_curves, req.mark_cap // params.points))
    rows = complete_rows(req.relation, fields, cap=curves, by=[color] if color else [], min_rows=1)
    x = rows.matrix(fields)
    if params.scale == "zscore":
        x, _, _ = standardize(x)
    elif params.scale == "minmax":
        lo, hi = x.min(axis=0), x.max(axis=0)
        span = np.where(hi > lo, hi - lo, 1.0)
        x = (x - lo) / span
    t = np.linspace(-math.pi, math.pi, params.points)
    values = x @ andrews_basis(t, len(fields)).T  # curves x points
    n = len(x)
    table = {
        "curve": pa.array(np.repeat(np.arange(n), len(t)), pa.int32()),
        "x": pa.array(np.tile(t, n), pa.float64()),
        "y0": pa.array(values.ravel(), pa.float64()),
    }
    labels = {"curve": "Row", "x": "t", "y0": "f(t)"}
    if color is not None:
        groups = rows.text(color)
        table["color"] = pa.array(np.repeat(np.array(groups, dtype=object), len(t)), pa.string())
        labels["color"] = color
    notes = rows.notes()
    if rows.sampled:
        notes.append(f"Showing {n:,} randomly chosen of {rows.n_total:,} rows.")
    return TableOutput(
        table=pa.table(table),
        reduction=Reduction.SAMPLED if rows.sampled else Reduction.NONE,
        method="andrews" + (" (reservoir)" if rows.sampled else ""),
        fields=labels,
        params={"curves": n, "points": params.points, "scale": params.scale},
        rows_matched=rows.n_total,
        warnings=notes,
    )
