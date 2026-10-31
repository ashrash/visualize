"""Request and result-value models for the MVP §5 statistics endpoints.

TODO(lead): move every model in this module into `contracts/stats.py` (all additive: new
models only, no change to StatResult), add the request models and the `*Values` models to
`EXPORTED_MODELS`, re-export them from `viz.contracts`, and run gen:api. The class names are
unique across contracts/, so the OpenAPI schema names don't change when they move.

Each endpoint returns a StatResult whose `values` is the matching `*Values` model dumped by
alias (camelCase). `*Values` and their parts are server-produced, so they have no defaults
and every field is required on the frontend. `None` means undefined, and the StatResult's
warnings say why.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from viz.contracts import Contract, Pipeline, Predicate, Scalar, SourceRef

Alternative = Literal["two_sided", "less", "greater"]
CiLevel = Annotated[float, Field(gt=0, lt=1)]


class StatsSourceRequest(Contract):
    """Common part of every statistics request: the data and the filters on it."""

    source: SourceRef
    # Pipelines the source references, as for chart data.
    pipelines: list[Pipeline] = Field(default_factory=list)
    filters: list[Predicate] = Field(default_factory=list)


class AssumptionCheck(Contract):
    """An assumption check that ran (or couldn't) alongside a test. Server-produced."""

    # e.g. "Levene (Brown-Forsythe) equal variances".
    name: str
    statistic: float | None
    p_value: float | None
    # True = no evidence against the assumption at the 5% level; None = not run.
    passed: bool | None
    # Why it wasn't run, or what a failure means.
    note: str | None


class SampleSummary(Contract):
    """n, mean and spread of one sample or group. Server-produced."""

    # Display label, e.g. "North" or "region=North, product=Gadget"; "all" when ungrouped.
    label: str
    # Group key values (aligned with the request's grouping fields); None when ungrouped.
    group: list[Scalar] | None
    n: int
    mean: float | None
    std: float | None
    # Standard error of the mean.
    sem: float | None


# ---------------------------------------------------------------- correlation

CorrelationMethod = Literal["pearson", "spearman", "kendall"]
MissingPolicy = Literal["pairwise", "listwise"]


class CorrelationRequest(StatsSourceRequest):
    """Correlation matrix of numeric fields (booleans count 0/1, dates count days)."""

    fields: list[str] = Field(min_length=2, max_length=50)
    method: CorrelationMethod = "pearson"
    # pairwise: each pair uses the rows where both are present; listwise: only rows where
    # every field is present.
    missing: MissingPolicy = "pairwise"
    ci_level: CiLevel = 0.95


class CorrelationValues(Contract):
    fields: list[str]
    method: CorrelationMethod
    missing: MissingPolicy
    ci_level: float
    # Square matrices aligned with `fields` (row i, column j). The diagonal is 1 (p null).
    coefficient: list[list[float | None]]
    p_value: list[list[float | None]]
    ci_low: list[list[float | None]]
    ci_high: list[list[float | None]]
    # Pairs used for each cell (the diagonal: values of that field).
    n: list[list[int]]


# ---------------------------------------------------------------- regression


class RegressionRequest(StatsSourceRequest):
    """OLS of `y` on `x`: numeric, boolean (0/1), date (days) or categorical (dummies)."""

    y: str
    x: list[str] = Field(min_length=1, max_length=20)
    intercept: bool = True
    # Heteroscedasticity-consistent (HC3) standard errors.
    robust: bool = False
    ci_level: CiLevel = 0.95
    # Most levels a categorical predictor may have (one dummy per level after the first).
    max_levels: int = Field(default=50, ge=2, le=200)


class RegressionTerm(Contract):
    # "(Intercept)", "price", or "region[South]" for a dummy.
    term: str
    # The source field; None for the intercept.
    field: str | None
    # The level of a categorical dummy; None otherwise.
    level: Scalar
    estimate: float | None
    std_error: float | None
    # t statistic (z under robust standard errors is still reported as t, as statsmodels).
    statistic: float | None
    p_value: float | None
    ci_low: float | None
    ci_high: float | None
    # Variance inflation factor of the term's field: GVIF^(1/df) (Fox & Monette), which is
    # the VIF for a one-column field and, for a categorical field, one value shared by its
    # dummies. None for the intercept, without an intercept, or when undefined.
    vif: float | None


class ReferenceLevel(Contract):
    field: str
    # The level every dummy of `field` is compared with (the first in sorted order).
    level: Scalar


class RegressionValues(Contract):
    y: str
    terms: list[RegressionTerm]
    reference_levels: list[ReferenceLevel]
    r2: float | None
    adj_r2: float | None
    f_statistic: float | None
    f_p_value: float | None
    df_model: float
    df_resid: float
    # Residual standard error.
    sigma: float | None
    log_likelihood: float | None
    aic: float | None
    bic: float | None
    condition_number: float | None
    ci_level: float
    robust: bool
    # Residual normality, heteroscedasticity.
    checks: list[AssumptionCheck]


# ---------------------------------------------------------------- t-tests

TTestKind = Literal["one_sample", "welch", "student", "paired"]


class TTestRequest(StatsSourceRequest):
    """t-tests, exact on the full data (they need only counts, means and variances).

    - one_sample: mean of `field` vs `mu`
    - welch / student: `field` in groups[0] vs groups[1] of `group_field` (difference a - b)
    - paired: mean of `field - paired_field` vs `mu`, over rows where both are present
    """

    kind: TTestKind = "welch"
    field: str
    mu: float = 0.0
    group_field: str | None = None
    groups: list[Scalar] | None = Field(default=None, min_length=2, max_length=2)
    paired_field: str | None = None
    alternative: Alternative = "two_sided"
    ci_level: CiLevel = 0.95


class TTestValues(Contract):
    kind: TTestKind
    alternative: Alternative
    ci_level: float
    mu: float
    statistic: float | None
    df: float | None
    p_value: float | None
    # Mean (one-sample), mean difference a - b (two-sample), mean of the differences (paired).
    estimate: float | None
    std_error: float | None
    # Interval for `estimate`; for a one-sided alternative the open side is null.
    ci_low: float | None
    ci_high: float | None
    # Cohen's d: (estimate - mu) / SD; two-sample uses the pooled SD.
    cohens_d: float | None
    # The samples: one (one-sample), two groups, or the two fields and their difference.
    samples: list[SampleSummary]
    checks: list[AssumptionCheck]


# ---------------------------------------------------------------- rank tests

RankTestKind = Literal["mann_whitney", "wilcoxon", "kruskal"]


class RankTestRequest(StatsSourceRequest):
    """Rank-based tests (SciPy, on a sample above 1M rows).

    - mann_whitney: `field` in groups[0] vs groups[1] of `group_field`
    - wilcoxon: signed-rank test of `field - paired_field` (or of `field - mu` without a
      paired field)
    - kruskal: `field` across the groups of `group_field` (`groups` picks a subset)
    """

    kind: RankTestKind = "mann_whitney"
    field: str
    mu: float = 0.0
    group_field: str | None = None
    groups: list[Scalar] | None = Field(default=None, min_length=2, max_length=200)
    paired_field: str | None = None
    alternative: Alternative = "two_sided"
    max_groups: int = Field(default=50, ge=2, le=200)


class RankSampleSummary(Contract):
    label: str
    group: list[Scalar] | None
    n: int
    median: float | None
    # Mean rank in the pooled ranking (Mann-Whitney, Kruskal-Wallis); None for Wilcoxon.
    mean_rank: float | None


class RankTestValues(Contract):
    kind: RankTestKind
    alternative: Alternative
    # U of the first group (Mann-Whitney), W+ (Wilcoxon), H (Kruskal-Wallis).
    statistic: float | None
    df: float | None
    p_value: float | None
    # Rank-biserial correlation (Mann-Whitney, Wilcoxon) or epsilon squared (Kruskal-Wallis).
    effect_size: float | None
    effect_size_name: str
    samples: list[RankSampleSummary]


# ---------------------------------------------------------------- ANOVA


class AnovaRequest(StatsSourceRequest):
    """One- or two-way ANOVA of `field`, exact on the full data (from per-cell aggregates)."""

    field: str
    factors: list[str] = Field(min_length=1, max_length=2)
    # Two-way only: include the factors' interaction.
    interaction: bool = True
    # Sums of squares for unbalanced two-way designs (one-way: all types agree).
    ss_type: Literal[1, 2, 3] = 2
    # One-way only: Tukey HSD (Tukey-Kramer) pairwise comparisons.
    post_hoc: bool = True
    ci_level: CiLevel = 0.95
    max_levels: int = Field(default=50, ge=2, le=200)


class AnovaRow(Contract):
    # A factor, "a:b" for the interaction, or "Residuals".
    term: str
    df: float
    sum_sq: float | None
    mean_sq: float | None
    statistic: float | None
    p_value: float | None
    # SS_term / (SS_term + SS_residual); None for the residual row.
    partial_eta_sq: float | None


class WelchAnova(Contract):
    statistic: float | None
    df1: float | None
    df2: float | None
    p_value: float | None


class TukeyComparison(Contract):
    # diff = mean(b) - mean(a).
    a: Scalar
    b: Scalar
    diff: float
    ci_low: float | None
    ci_high: float | None
    # Family-wise adjusted p-value.
    p_value: float | None


class AnovaValues(Contract):
    field: str
    factors: list[str]
    ss_type: Literal[1, 2, 3]
    interaction: bool
    table: list[AnovaRow]
    # One-way effect sizes; None for two-way.
    eta_sq: float | None
    omega_sq: float | None
    # One-way Welch ANOVA (no equal-variance assumption); None for two-way.
    welch: WelchAnova | None
    # One-way Tukey HSD; None when not asked for or two-way.
    post_hoc: list[TukeyComparison] | None
    ci_level: float
    # Cells: the groups (one-way) or the factor-level combinations present (two-way).
    cells: list[SampleSummary]
    empty_cells: int
    checks: list[AssumptionCheck]


# ---------------------------------------------------------------- chi-square


class ExpectedProportion(Contract):
    value: Scalar
    proportion: float = Field(gt=0)


class ChiSquareRequest(StatsSourceRequest):
    """Pearson's chi-square on counts computed in SQL.

    - independence: `field` x `column_field` contingency table
    - goodness_of_fit: counts of `field` vs `expected` proportions (uniform when omitted)
    """

    kind: Literal["independence", "goodness_of_fit"] = "independence"
    field: str
    column_field: str | None = None
    expected: list[ExpectedProportion] | None = Field(default=None, min_length=2, max_length=200)
    # Yates' continuity correction for 2x2 tables (as R and SciPy).
    correction: bool = True
    max_levels: int = Field(default=50, ge=2, le=200)


class ChiSquareValues(Contract):
    kind: Literal["independence", "goodness_of_fit"]
    statistic: float | None
    df: int
    p_value: float | None
    # Levels of `field` (rows) and `column_field` (columns; None for goodness of fit).
    rows: list[Scalar]
    columns: list[Scalar] | None
    # rows x columns (goodness of fit: one row per level, one column).
    observed: list[list[int]]
    expected: list[list[float]]
    # Adjusted standardized residuals (independence) or (O - E) / sqrt(E) (goodness of fit).
    residuals: list[list[float | None]]
    cramers_v: float | None
    yates: bool
    # 2x2 only: Fisher's exact test and the sample odds ratio.
    fisher_p_value: float | None
    odds_ratio: float | None
    min_expected: float | None


# ---------------------------------------------------------------- distribution fitting

Distribution = Literal[
    "norm",
    "lognorm",
    "gamma",
    "weibull_min",
    "expon",
    "logistic",
    "laplace",
    "uniform",
    "beta",
    "gumbel_r",
    "t",
    "cauchy",
]


class DistributionFitRequest(StatsSourceRequest):
    """Maximum-likelihood fits of continuous distributions, ranked by AIC (on a sample)."""

    field: str
    # None = the default set (everything except the slow t and cauchy fits).
    distributions: list[Distribution] | None = Field(default=None, min_length=1)
    # Points on the density curves returned for plotting.
    grid_size: int = Field(default=200, ge=10, le=2000)


class FitParameter(Contract):
    # SciPy's name ("loc", "scale", "s", "a", "c", "df", ...).
    name: str
    value: float
    # Fixed rather than estimated (e.g. loc = 0 for the positive distributions).
    fixed: bool


class DistributionFit(Contract):
    distribution: Distribution
    label: str
    params: list[FitParameter]
    log_likelihood: float | None
    aic: float | None
    bic: float | None
    delta_aic: float | None
    # Akaike weight: relative likelihood of this model among the fitted ones.
    aic_weight: float | None
    ks_statistic: float | None
    # Approximate: the parameters were estimated from the same data.
    ks_p_value: float | None
    # Density on DistributionFitValues.x.
    pdf: list[float | None]


class SkippedDistribution(Contract):
    distribution: Distribution
    reason: str


class DistributionFitValues(Contract):
    field: str
    # Ranked by AIC (best first).
    fits: list[DistributionFit]
    skipped: list[SkippedDistribution]
    best: Distribution | None
    # Density histogram of the data: bin edges (len k + 1) and densities (len k).
    edges: list[float]
    density: list[float]
    # Grid for the fitted densities.
    x: list[float]


# ---------------------------------------------------------------- outliers

OutlierMethod = Literal["iqr", "zscore", "mad"]


class OutlierRequest(StatsSourceRequest):
    """Outliers by Tukey's fences (iqr), z-scores or modified z-scores (mad), exact in SQL.

    A value is an outlier when |score| > threshold; defaults are 1.5 (iqr), 3 (zscore) and
    3.5 (mad, Iglewicz-Hoaglin).
    """

    fields: list[str] = Field(min_length=1, max_length=20)
    method: OutlierMethod = "iqr"
    threshold: float | None = Field(default=None, gt=0)
    group_by: str | None = None
    max_groups: int = Field(default=50, ge=1, le=500)
    # Most extreme outlier rows returned per field.
    max_rows: int = Field(default=100, ge=0, le=1000)
    # Extra columns returned with each outlier row.
    include: list[str] = Field(default_factory=list, max_length=20)


class OutlierSummary(Contract):
    field: str
    group: list[Scalar] | None
    # Values present.
    n: int
    # Median (iqr, mad) or mean (zscore).
    center: float | None
    # IQR, standard deviation, or the scaled MAD (MAD / 0.6745).
    spread: float | None
    # Fences: values below `low` or above `high` are outliers.
    low: float | None
    high: float | None
    n_low: int
    n_high: int
    share: float | None


class OutlierRow(Contract):
    field: str
    group: list[Scalar] | None
    value: float
    # iqr: IQRs beyond the nearer quartile; zscore: z; mad: modified z. None if spread is 0.
    score: float | None
    side: Literal["low", "high"]
    # The `include` columns of the row.
    row: dict[str, Scalar]


class OutlierValues(Contract):
    method: OutlierMethod
    threshold: float
    group_by: str | None
    summaries: list[OutlierSummary]
    rows: list[OutlierRow]


# ---------------------------------------------------------------- confidence intervals

CiStatistic = Literal["mean", "median", "std", "quantile"]
IntervalMethod = Literal[
    "t",
    "order statistic",
    "asymptotic",
    "bca bootstrap",
    "percentile bootstrap",
    "basic bootstrap",
]


class ConfidenceIntervalRequest(StatsSourceRequest):
    """Confidence intervals; estimates are exact on all values.

    `auto`: t for the mean, order statistics (distribution-free) for the median and
    quantiles, and for the SD the bootstrap up to 10,000 values per group and the
    kurtosis-adjusted asymptotic interval above. `bootstrap` bootstraps groups of up to
    10,000 values (all of them, never a subsample) and uses the statistic's exact interval
    above. `t` is for the mean only.
    """

    fields: list[str] = Field(min_length=1, max_length=20)
    statistic: CiStatistic = "mean"
    # For statistic = "quantile".
    quantile: float | None = Field(default=None, gt=0, lt=1)
    method: Literal["auto", "t", "bootstrap"] = "auto"
    ci_level: CiLevel = 0.95
    group_by: str | None = None
    max_groups: int = Field(default=50, ge=1, le=200)
    n_resamples: int = Field(default=2000, ge=100, le=10000)


class ConfidenceInterval(Contract):
    field: str
    group: list[Scalar] | None
    n: int
    estimate: float | None
    ci_low: float | None
    ci_high: float | None
    # None for order-statistic intervals.
    std_error: float | None
    method: IntervalMethod


class ConfidenceIntervalValues(Contract):
    statistic: CiStatistic
    quantile: float | None
    ci_level: float
    group_by: str | None
    intervals: list[ConfidenceInterval]
