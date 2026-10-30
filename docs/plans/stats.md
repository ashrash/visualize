# Plan: stats (Wave 1: kernels, plot seam, descriptive statistics)

**Verified on the locked deps** (DuckDB 2.0.0, SciPy 1.18.1, statsmodels 0.15.0):
- DuckDB `skewness`/`kurtosis` equal SciPy `bias=False` (G1, excess G2). `quantile_cont` = NumPy `linear` (R type 7); `quantile_disc` = NumPy `inverted_cdf`.
- Degenerate input gives NULL, not NaN (std n<2, skew n<3, kurtosis n<4, constant → skew/kurt NULL). One NaN poisons `avg`. `USING SAMPLE` runs **before** `WHERE`, so a filtered sample must sample a subquery. statsmodels `lowess` refuses `delta` together with `xvals`.

**Layout (paths I own).** `stats/`: `errors.py` (StatsInputError ⊂ InvalidSpecError → 422) · `results.py` · `relation.py` · `sampling.py` · `quantiles.py` · `kde.py` · `regression.py` · `bootstrap.py` · `_workers.py` (picklable pool workers) · `descriptive.py`. Also `api/stats.py`, `plots/base.py`, `plots/__init__.py`.

## 1. Kernel API
**Relation** (frozen dataclass): the filtered source that every SQL kernel and plot stat reads.
- Fields: `engine`, `source: exp.Expr` (`read_parquet(..)` or a compiled pipeline subquery), `where: exp.Expr|None`, `columns: Mapping[str, Column]`, `row_count: int|None`, `version_key: str` (`<datasetId>@v<n>`, plus the pipeline hash), `query_key: str|None` (Engine interrupt key, e.g. `chart:<id>`).
- Methods: `select(*exprs) -> exp.Select` (FROM + WHERE applied), `arrow(query) -> pa.Table`, `require(field, accepts) -> Column` (StatsInputError on an unknown field or wrong semantic). All SQL goes through SQLGlot with quoted identifiers.

**Sampling.** `reservoir_sample(rel, fields, *, cap, by=(), seed=42, dropna=True) -> Sample(table, n, n_total, n_missing, sampled)`, plus `.column(name) -> float64` (temporal → epoch ms) and `.notes()`.
- A count query runs first. If `n_total ≤ cap`, all rows are returned; otherwise `FROM (<filtered subquery>) USING SAMPLE reservoir(cap ROWS) REPEATABLE(seed)`.
- `by=` gives a stratified per-group cap: a Bernoulli prefilter at `2·cap/n_g`, then `QUALIFY row_number() OVER (PARTITION BY … ORDER BY random()) <= cap`.
- Repeatability is best-effort under multithreading; results are cached per version_key. Default caps: models 1M, KDE 100k, LOWESS 10k.

**Exact, SQL on the full data:**
- `quantiles(rel, field, probs, *, by=(), method="linear"|"inverted_cdf") -> Table[by…, p, value]`
- `ecdf(rel, field, *, max_points=2000, by=()) -> (Table[by…, x, p], exact)`: exact steps (GROUP BY value, windowed cumulative count/n) when distinct values ≤ max_points; otherwise `quantile_disc` at evenly spaced p, which keeps every point on the true ECDF.
- `bin_edges(n, lo, hi, *, iqr, std, rule="fd"|"sturges"|"scott"|"sqrt"|"auto", bins=None, width=None, nice=True, max_bins=1000)`: a pure function with NumPy's rules. FD falls back to Sturges when IQR = 0. `nice` rounds the width to {1, 2, 2.5, 5}·10^k and snaps `lo` down to a multiple of it.
- `histogram(rel, field, *, rule|bins|width, range=None, by=()) -> Table[by…, binStart, binEnd, count]`: one stats pass, then `GROUP BY least(floor((x-lo)/w), k-1)`. Bins are [start, end) with the last one closed (as in NumPy); empty bins are zero-filled.
- `qq(rel, field, *, dist="norm", shape=(), max_points=2000, line="quartiles"|"fit") -> QQ(theoretical, sample, slope, intercept, n, exact)`: Filliben plotting positions (= `scipy.stats.probplot`) when n ≤ max_points, otherwise exact order statistics at evenly spaced ranks. `line` is R's `qqline` through the quartiles, or a least-squares fit.

**KDE (on Sample arrays).**
- `bandwidth(x, rule)`: `silverman` (default) = 0.9·min(σ̂, IQR/1.34)·n^-1/5 (R `bw.nrd0`); `scott` = 1.06·min(σ̂, IQR/1.34)·n^-1/5 (R `bw.nrd`). If the min is 0, use σ̂. A float fixes the bandwidth.
- `kde(x, *, bw, adjust=1, grid_size=512, cut=3, clip=(None,None), grid=None) -> KdeCurve(x, density, bandwidth, n)`
- `kde_by_group(values, groups, *, common_grid=True, common_bw=False, min_n=2) -> [GroupCurve(key, n, curve)]`. `n` lets violin, ridgeline and raincloud scale by count.
- `kde2d(x, y, *, bw, grid_size=(100,100), cut=3) -> Kde2d(xs, ys, density[ny,nx], bw_x, bw_y, n)`
- Method: Gaussian kernel, linear binning onto a 2^k grid, then FFT convolution (R `density()`), O(n + G log G). 2-D uses a product kernel with per-axis bandwidths (as `MASS::kde2d`).
- Degenerate input: n < 2 or σ̂ = 0 raises StatsInputError. In grouped KDE, such a group is skipped with a warning.

**Regression.** Both return `FitCurve(x, y, lower, upper, pred_lower, pred_upper, coef, coef_se, r2, adj_r2, sigma, df_resid, n, warnings)`.
- `ols_curve(x, y, *, degree=1, log_x=False, log_y=False, ci_level=.95, prediction=False, grid_size=100)`: QR on a centred, scaled polynomial basis (degree ≤ 6) with a rank check; coefficients on the original scale; mean-CI and prediction bands = t·√(gᵀΣg). `log_x`/`log_y` give log, exp and power trendlines (values ≤ 0 dropped with a warning; back-transformed bands carry a warning).
- OLS degenerate input: x without variation or a rank-deficient design raises StatsInputError. n = degree+1 gives the curve with no band plus a warning. A perfect fit gives a zero-width band. Constant y gives `r2=None` plus a warning.
- `lowess_curve(x, y, *, frac=2/3, it=3, ci_level=.95|None, n_boot=200, seed=42, grid_size=100, executor=None)`: statsmodels lowess at the data points (`delta=0.01·range`), interpolated onto the grid. The band is a percentile bootstrap over (x, y) pairs, run in the executor. n < 3 or constant x raises StatsInputError.

**Bootstrap.** `bootstrap_ci(data: tuple[ndarray, ...], statistic, *, ci_level=.95, n_resamples=2000, method="auto"|"bca"|"percentile"|"basic", paired=True, seed=42, executor=None) -> Interval(estimate, low, high, se, method, n, n_resamples)`.
- Wraps `scipy.stats.bootstrap(vectorized=True, batch=…, rng=default_rng(seed))`. `auto` means BCa for n ≤ 10k (its jackknife is O(n²)) and percentile above that.
- Statistics are named, vectorized and picklable: mean, median, std, quantile(q), trimmed_mean, pearson_r. A statistic that isn't vectorized runs in the executor.
- n < 2 raises an error. Identical resamples give low = high = estimate plus a warning, where SciPy would return NaN.

**Pool and BLAS.** Kernels take `executor: concurrent.futures.Executor | None` (None = run inline, so tests need no pool). Workers are top-level functions and receive capped arrays. `limit_blas()` applies threadpoolctl once per process (default max(1, cores // 4)).

## 2. StatResult conventions
- **Keys** in `values` are camelCase, like the rest of the wire format. `json_values()` converts numpy → Python, ndarray → list, NaN/±inf → null and dates → ISO.
- **Shapes:** curves are column-oriented (`{"x":[…], "y":[…], "lower":[…], "upper":[…]}`); intervals `{"estimate","ciLow","ciHigh","ciLevel"}`; tests `{"statistic","pValue","df"}`; groups `[{"key":[…], "n":…, …}]`; probabilities in [0, 1].
- **Fixed definitions:** std and variance use ddof=1. Skewness is G1 (SciPy `bias=False`, Excel SKEW). Kurtosis is excess G2 (Excel KURT). Quantiles default to linear (type 7).
- **Counts:** `n` = observations actually used (the sample size when sampled). `nTotal` = eligible rows before sampling, after filters and missing removal. `nMissing` = rows dropped as null or non-finite. `sampled` = `nTotal > n`, and the note "Computed on a random sample of 100,000 of 9,876,543 rows." is added automatically.
- **Failures:** an undefined part of a result is null plus one warning that says why ("Kurtosis needs at least 4 values."). A whole-result failure (no rows, a non-numeric field, a singular design) raises StatsInputError (422). The output never contains NaN.
- **Warnings** are plain user-facing sentences, de-duplicated and prefixed with the field or group.

## 3. Plot-module seam (`plots/base.py`, `plots/__init__.py`)
```python
class PlotParams(Contract): ...  # base of each plot's params model (camelCase keys, extra=forbid)
@dataclass(frozen=True)
class Channel:
    name: str                                         # "x", "y", "color", "size", "group", ...
    accepts: frozenset[ColumnSemantic] | None = None  # None = any; consts QUANT, TEMPORAL, CATEGORICAL
    min: int = 1                                      # 0 = optional
    max: int | None = 1                               # None = unbounded
@dataclass(frozen=True)
class PlotRequest:
    spec: ChartSpec; params: PlotParams; relation: Relation  # relation has all filters applied
    viewport: Viewport; mark_cap: int; executor: Executor | None
@dataclass(frozen=True)
class TableOutput:  # chartdata turns it into Arrow + ChartDataMeta
    table: pa.Table; reduction: Reduction; method: str | None; fields: dict[str, str]
    rows_scanned: int; warnings: list[str] = field(default_factory=list)
PlotOutput = TableOutput | StatResult; StatFn = Callable[[PlotRequest], PlotOutput]
def register_plot(plot_type: PlotType, *, channels: Sequence[Channel],  # stores a frozen
                  params: type[PlotParams] = PlotParams) -> Callable[[StatFn], StatFn]: ...  # PlotDef
def get_plot(plot_type: PlotType) -> PlotDef | None  # None -> chartdata's generic aggregate path
def run_plot_stat(spec, relation, *, viewport, mark_cap, executor=None) -> PlotOutput
```
- **Discovery** runs lazily, once, under a lock: `pkgutil.iter_modules(viz.plots.__path__)` imports every module except `base` and `_*`. Next.md plugins can later add the `viz.plots` entry-point group here.
- **Import-time guards:** a PlotType registered twice, or a module registering a type outside its family (`viz.plots.<PLOT_FAMILY[type]>`).
- **`run_plot_stat`** reports every channel problem in one InvalidSpecError (missing, too many, unknown field, wrong semantic). It then validates `spec.params` against the plot's model (422) and calls the stat.
- **chartdata** times the call, caches by (version_key, spec hash), and maps TableOutput → Arrow and StatResult → JSON.

## 4. Descriptive statistics: `POST /api/stats/describe` (sync def)
- **Response:** `StatResult` with `values={"summaries": FieldSummary[]}`, method "Descriptive statistics (exact)", `sampled=false`.
- **Query 1** is one scan with `GROUP BY GROUPING SETS ((by…), ())` (`grouping()` marks the overall row). Numeric: count, missing, avg, stddev_samp, var_samp, sum, min, max, skewness, kurtosis, `quantile_cont(x, [.25,.5,.75,*percentiles])`. Floats go through `CASE WHEN isfinite(x) THEN x END`, so NaN/±inf count as missing. Temporal: min, max, `quantile_disc` (ISO). Boolean: mean = share true. Categorical: counts only.
- **Query 2 (modes)** is a UNION ALL with one branch per field: GROUP BY (by…, value), then a windowed max/count. It yields the exact `distinct`, `modes` (all ties, ascending, at most 5) and `modeCount`. When `modeCount=1`, `modes=[]` with a warning.
- **Groups, caching, budget:** groups are capped at `maxGroups`, keeping the largest, with a warning. Results go in an LRU keyed by (version_key, request JSON). Proposed budget: 10 numeric fields × 10M rows ≤ 2 s.

## 5. Contract additions (additive; the lead adds them to `contracts/stats.py`)
```python
class StatResult(Contract):  # existing fields unchanged; add:
    # Eligible rows before sampling (after filters and missing-value removal). None = same as n.
    n_total: int | None = None
    # Rows dropped because a required field was null, NaN or +/-inf.
    n_missing: int = 0


class Quantile(Contract):
    p: float
    value: float | str | None


class FieldSummary(Contract):
    """One field within one group of a descriptive-statistics table (server-only: no defaults)."""

    field: str
    group: list[Scalar] | None  # aligned with DescribeRequest.group_by; None = overall row
    kind: Literal["numeric", "temporal", "categorical", "boolean"]
    count: int  # non-missing values
    missing: int  # null, plus NaN/+-inf for float columns
    distinct: int | None  # None when include_mode is false
    mean: float | None
    std: float | None
    variance: float | None
    sem: float | None  # split per line (E702)
    sum: float | None
    iqr: float | None
    min: float | str | None
    max: float | str | None  # str = ISO date/timestamp
    median: float | str | None
    q1: float | str | None
    q3: float | str | None
    skewness: float | None  # adjusted Fisher-Pearson G1
    kurtosis: float | None  # excess, bias-corrected G2
    quantiles: list[Quantile]  # the requested percentiles
    modes: list[Scalar]
    mode_count: int | None


class DescribeRequest(Contract):
    source: SourceRef
    fields: list[str] = Field(min_length=1, max_length=50)
    group_by: list[str] = Field(default_factory=list, max_length=2)
    filters: list[Predicate] = Field(default_factory=list)
    percentiles: list[Annotated[float, Field(ge=0, le=1)]] = Field(
        default_factory=lambda: [0.05, 0.95], max_length=20
    )
    include_overall: bool = True
    include_mode: bool = True
    max_groups: int = Field(default=50, ge=1, le=500)
```
- Add `FieldSummary` to `EXPORTED_MODELS`, so the frontend can type `values.summaries`.
- For transform, unless they have one already: `ChartStatData(Contract): meta: ChartDataMeta; result: StatResult` as the JSON response for StatResult plots.

## 6. Tests (written first; `backend/tests/stats/`, `backend/tests/plots/`)
- **Descriptive:** gamma data with nulls, NaN and inf vs `scipy.stats.describe`, `skew`/`kurtosis(bias=False)`, `np.quantile` and `scipy.stats.mode` (smallest tie), rtol 1e-10. Grouped and overall rows vs pandas groupby; filters; NIST StRD NumAcc1/3/4 certified mean and SD; degenerate cases (n=0–3, constant, all-null, all-unique, ties); Hypothesis fuzzing vs NumPy.
- **Quantiles/ECDF/bins/QQ:** `np.quantile` (linear, inverted_cdf) per group; `scipy.stats.ecdf`, with grid points on the exact ECDF; `np.histogram` on our edges, values on edges included; `np.histogram_bin_edges` with `nice=False`; `scipy.stats.probplot` outputs.
- **Sampling:** all rows when n ≤ cap; exactly `cap` rows above it; correct `n_total`/`n_missing`; filters applied before sampling; stratified caps; repeatable under `Engine(threads=1)`.
- **KDE:** vs `gaussian_kde(bw_method=h/σ̂)` within 1e-3·peak; integrates to 1 ± 1e-3; `bw.nrd0`/`bw.nrd` by hand (1:10 → 1.7193); 2-D vs a direct product-Gaussian sum; degenerate groups skipped.
- **OLS:** NIST StRD Norris, Pontius and Wampler1 (exact fit) certified coefficients, SE, residual SD and R², rtol 1e-8, with data in `tests/data/stats/`. Bands vs statsmodels `get_prediction().summary_frame()`.
- **LOWESS:** reproduces a straight line exactly; seeded runs are deterministic; on sin + noise the band covers the truth at ≥ 85% of grid points.
- **Bootstrap:** Efron–Tibshirani law-school data (r = 0.7764, SE ≈ 0.13 ± 0.015); mean CI ≈ t-interval; 95% coverage simulation (marked slow); degenerate cases.
- **Seam:** discovery from a temp package; duplicate and family guards; channel and param messages; every key is in PLOT_FAMILY.
- **API:** describe on `sales_dataset` gives hand-computed values; unknown dataset → 404; unknown field → 422.

## 7. Order
(1) errors and results → (2) Relation and the plot seam (unblocks transform) → (3) sampling → (4) quantiles, ecdf, bins, qq → (5) describe and its API → (6) KDE → (7) OLS and LOWESS → (8) bootstrap → (9) perf check: reservoir 100k of 10M ≤ 300 ms; KDE 100k ≤ 30 ms (20 groups ≤ 200 ms); OLS 1M ≤ 200 ms.

## 8. Requests and open questions
- **lead/platform:** include `stats.router` in `api/__init__.py` (my tests mount it until then). Optional `Settings.blas_threads`; call `limit_blas()` in the lifespan and in the pool initializer.
- **transform:** build `Relation` (with pipelines) in chartdata and call `run_plot_stat`. Until then I import `where_sql`/`parquet_source` from `chartdata/sql.py`, read-only.
- **data-core:** expose the process pool as a `concurrent.futures.Executor` (e.g. `app.state.process_pool`).
- **lead:** should `/api/plots` publish each plot's channels and params JSON schema, so the frontend registry can't drift from backend validation?
