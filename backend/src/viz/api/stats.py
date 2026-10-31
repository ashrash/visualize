"""Statistics endpoints. Sync handlers, so DuckDB and SciPy work runs in the threadpool.

Every endpoint takes a dataset or pipeline source (with `pipelines` holding every pipeline
it reaches, unsaved edits included) plus filters, and returns a StatResult whose `values`
is the endpoint's *Values model. A newer request of the same analysis on the same source
cancels an older one still running.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter

from viz.api.deps import DbDep, EngineDep, StorageDep
from viz.contracts import (
    AnovaRequest,
    ChiSquareRequest,
    ConfidenceIntervalRequest,
    CorrelationRequest,
    DescribeRequest,
    DistributionFitRequest,
    OutlierRequest,
    RankTestRequest,
    RegressionRequest,
    StatResult,
    StatsSourceRequest,
    TTestRequest,
)
from viz.engine import Engine
from viz.stats.anova import anova
from viz.stats.cache import cached
from viz.stats.chisquare import chi_square
from viz.stats.correlation import correlation
from viz.stats.descriptive import describe_cached
from viz.stats.fitting import fit_distributions
from viz.stats.intervals import confidence_intervals
from viz.stats.linear_model import linear_regression
from viz.stats.outliers import outliers
from viz.stats.ranktests import rank_test
from viz.stats.relation import Relation
from viz.stats.source import data_errors, relation_for
from viz.stats.ttest import t_test
from viz.storage import LocalStorage

router = APIRouter(prefix="/stats", tags=["stats"])


@router.post("/describe", response_model=StatResult)
def describe_fields(
    body: DescribeRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> StatResult:
    """Exact descriptive statistics; `values.summaries` is a list of FieldSummary.

    The source may be a dataset or a pipeline (with `pipelines` holding every pipeline it
    reaches, unsaved edits included).
    """
    with data_errors():
        # One cancellation slot per source: a newer describe of the same source cancels this.
        relation = relation_for(
            db,
            storage,
            engine,
            body.source,
            pipelines=body.pipelines,
            filters=body.filters,
            query_key=f"describe:{body.source.kind}:{body.source.id}",
        )
        return describe_cached(relation, body)


def _run(
    analysis: str,
    body: StatsSourceRequest,
    db: DbDep,
    engine: Engine,
    storage: LocalStorage,
    compute: Callable[[Relation], StatResult],
) -> StatResult:
    with data_errors():
        relation = relation_for(
            db,
            storage,
            engine,
            body.source,
            pipelines=body.pipelines,
            filters=body.filters,
            query_key=f"stats:{analysis}:{body.source.kind}:{body.source.id}",
        )
        return cached(analysis, relation, body, lambda: compute(relation))


@router.post("/correlation", response_model=StatResult)
def correlation_endpoint(
    body: CorrelationRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> StatResult:
    """Correlation matrix; `values` is CorrelationValues. Pearson is exact; Spearman and
    Kendall run on a sample when the data are large."""
    return _run("correlation", body, db, engine, storage, lambda r: correlation(r, body))


@router.post("/regression", response_model=StatResult)
def regression_endpoint(
    body: RegressionRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> StatResult:
    """Linear or multiple OLS regression (sampled above 1M rows); `values` is
    RegressionValues."""
    return _run("regression", body, db, engine, storage, lambda r: linear_regression(r, body))


@router.post("/ttest", response_model=StatResult)
def ttest_endpoint(
    body: TTestRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> StatResult:
    """One-sample, Welch, Student or paired t-test (exact); `values` is TTestValues."""
    return _run("ttest", body, db, engine, storage, lambda r: t_test(r, body))


@router.post("/ranktest", response_model=StatResult)
def ranktest_endpoint(
    body: RankTestRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> StatResult:
    """Mann-Whitney U, Wilcoxon signed-rank or Kruskal-Wallis test; `values` is
    RankTestValues."""
    return _run("ranktest", body, db, engine, storage, lambda r: rank_test(r, body))


@router.post("/anova", response_model=StatResult)
def anova_endpoint(
    body: AnovaRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> StatResult:
    """One- or two-way ANOVA (exact); `values` is AnovaValues."""
    return _run("anova", body, db, engine, storage, lambda r: anova(r, body))


@router.post("/chisquare", response_model=StatResult)
def chisquare_endpoint(
    body: ChiSquareRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> StatResult:
    """Chi-square test of independence or goodness of fit (exact counts); `values` is
    ChiSquareValues."""
    return _run("chisquare", body, db, engine, storage, lambda r: chi_square(r, body))


@router.post("/fit", response_model=StatResult)
def fit_endpoint(
    body: DistributionFitRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> StatResult:
    """Maximum-likelihood distribution fits ranked by AIC; `values` is
    DistributionFitValues."""
    return _run("fit", body, db, engine, storage, lambda r: fit_distributions(r, body))


@router.post("/outliers", response_model=StatResult)
def outliers_endpoint(
    body: OutlierRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> StatResult:
    """Outliers by IQR fences, z-scores or modified z-scores (exact); `values` is
    OutlierValues."""
    return _run("outliers", body, db, engine, storage, lambda r: outliers(r, body))


@router.post("/ci", response_model=StatResult)
def ci_endpoint(
    body: ConfidenceIntervalRequest, db: DbDep, engine: EngineDep, storage: StorageDep
) -> StatResult:
    """Confidence intervals (t or bootstrap); `values` is ConfidenceIntervalValues."""
    return _run(
        "ci",
        body,
        db,
        engine,
        storage,
        lambda r: confidence_intervals(r, body, executor=engine.pool),
    )
