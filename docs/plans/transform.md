# transform plan: pipeline compiler + chart-data protocol (Wave 1)

## As built (where the code differs from the plan below)
- **Names:** the compiler output is `CompiledPipeline`, and a resolved source with the hidden row number is `SourceBody`. `resolve_source(engine, db, storage, ref, pipelines=(), filters=(), *, query_key=None, generation=None, ordered=False)` returns stats' `viz.stats.relation.Relation`.
- **Hidden row number:** it is `__viz_rn`, read with `read_parquet(path, file_row_number = true)`. The virtual `file_row_number` column hits a DuckDB 2.0.0 internal error under QUALIFY.
- **unpivot:** a zipped `unnest([...])` of names, values and positions rather than `UNPIVOT`. This keeps NULL values (melt semantics) and the row order. Mixed value types become DOUBLE (all numeric) or VARCHAR.
- **Plot seam:** stats' design: `viz.plots.get_plot` / `run_plot_stat` taking `PlotRequest`. chartdata's `ChartContext` is internal, and there is no `fallback` field.
- **Kernels:** stats owns `histogram`/`bin_edges`/`reservoir_sample`. chartdata keeps `aggregate_query`/`over_groups`, `m4_query`, `minmaxlttb`, `bin2d`, `hexbin`, `numeric_x`, `epoch_ms_values` and `x_range_predicate`.
- **Grouping and ordering:** GROUP BY uses the dimension expressions, never their aliases, because a source column called `x` would win. Ordering happens over a subquery that exposes only the aliases.
- **M4:** plain `arg_min`/`arg_max` (ties within a pixel column are arbitrary; cached responses keep a chart stable). With no time unit, the cap+1 probe is skipped when the import profile's `approx_distinct(x)` exceeds the cap, with or without filters. M4 reports the real group count, and the exact probe runs if the data fits the cap.
- **Series:** `n_out` per series is `min(2·widthPx, cap / series)`.
- **Cancellation:** tickets come from one monotonic counter. The endpoint takes the ticket before work starts; a disconnect cancels only that ticket, and only while it is the newest in its slot. `TicketEngine` checks the ticket before every cursor, including plot modules' own queries.
- **Time:** TIMESTAMPTZ positions use `epoch_ms(x)` directly (no cast). Bounds are absolute instants. Offsets in text are applied to naive TIMESTAMP columns, not dropped.
- **Formulas:** `replace`/`regexp_replace` take only quoted text of 64 characters or fewer, can't be nested inside each other, and at most 8 may appear per formula. At most 100 column references per formula. Every calc step's text or binary output is guarded at 1 MB with a generated `error()` (not on the allowlist), so growth can't compound across chained steps.
- **Overlays:** fits use numpy until stats' `ols_curve`/`lowess_curve` land. The raw-point reservoir sample uses `REPEATABLE (42)` on the filtered subquery. LOESS above 5,000 points uses a seeded (42) numpy subsample.

## 1. Pipeline compiler (`transform/compiler.py`)
- **Relation, not CREATE VIEW.** `compile(pipeline, ctx) -> Relation{sql: exp.Select, columns: list[SchemaField], key: str, steps: list[StepSchema], pending: list[Materialization]}`. The "view" is an inlined SQLGlot subquery, so there is no catalog state in the shared DuckDB, no view lifecycle and no global state (CLAUDE §11). `key` = sha256 of the generated SQL. It embeds `datasets/<id>/v<n>.parquet`, so it covers "dataset version + SQL hash" and a refresh changes it.
- **CTE chain:** `WITH "s0" AS (source), "s1" AS (...) ... SELECT * EXCLUDE ("__rn") FROM "sK"`. CTEs are named by enabled-step position, never by user ids. Every identifier is quoted via `chartdata.sql.col`.
- **Row order:** `s0 = read_parquet(path, file_row_number=true)` with the row number renamed `"__rn"`. The hidden ordinal is carried through every step. It makes dedupe `keep first/last`, forward/backward fill and preview paging deterministic (ORDER BY inside a CTE is not guaranteed). `sort`, `group_aggregate`, `pivot`, `unpivot` and `union` reassign `__rn = row_number() OVER (<natural order>)`.
- **Toggled steps:** a disabled step emits nothing. Its `StepSchema` has `status="disabled"` and passes the input columns through.
- **Schema propagation:** after each step, `DESCRIBE <prefix SQL>` (bind only, ~ms, cached by prefix hash) gives names + DuckDB types, and `semantic` is derived from the type. The next step validates its field refs against that schema. A single source of truth means no static type inference to keep in sync.
- **Per-step errors:** a builder raises `StepError(step_id, message, field?, start?, end?)`. DuckDB binder errors from DESCRIBE are caught and mapped to the step. The first failing step gets `status="error"` and later steps `status="blocked"`. Inspect and preview return 200 with these statuses. Chart-data returns 422 naming the step.
- **Step → SQL** (`transform/steps.py`, one builder per op, explicit projections built from the input schema):
  - **filter:** `WHERE` built from `predicate_sql` ANDed with the validated formula (DESCRIBE must show BOOLEAN).
  - **sort:** `row_number() OVER (ORDER BY keys…, __rn)` becomes the new `__rn`. Final and preview output use `ORDER BY __rn`.
  - **select, rename:** explicit column lists. Rename targets are checked for collisions, case-insensitively, because DuckDB identifiers are case-insensitive.
  - **cast:** `TRY_CAST` / `try_strptime(col, fmt)`. Failures become NULL, and preview warns with the count of new NULLs.
  - **fill_missing:** `value` → `COALESCE(c, lit)`. `mean|median|mode` → `COALESCE(c, avg|median|mode(c) OVER ())`. `forward` → `last_value(c IGNORE NULLS) OVER (ORDER BY __rn ROWS UNBOUNDED PRECEDING)`; `backward` mirrors it.
  - **drop_missing:** `any` ORs `IS NULL` across the fields, `all` ANDs them.
  - **dedupe:** `QUALIFY row_number() OVER (PARTITION BY fields|all ORDER BY __rn [DESC]) = 1`.
  - **calc:** the validated formula `AS name`. An existing name is replaced in place.
  - **group_aggregate:** GROUP BY with `agg_sql(fn, field)`, shared with chartdata.
  - **join:** the right `SourceRef` is compiled recursively (dataset or pipeline; cycles detected via a visiting set) and embedded as a subquery. Rules: a right key that has the same name as its left key is dropped; for `right`/`full` joins the left key becomes `COALESCE(l.k, r.k)`; colliding names get the suffix, then `_2`, `_3`, and so on.
  - **union:** `UNION ALL [BY NAME]`. Positional mode checks column counts first.
  - **pivot:** compile-time `SELECT DISTINCT col … LIMIT 1001` on the prefix (cached by prefix hash). It emits `fn(values) FILTER (WHERE col = v) AS "v"` per value, so the schema is stable and works inside a CTE. NULL becomes the column "null". Names are de-duplicated. More than 1000 values is a step error.
  - **unpivot:** DuckDB `UNPIVOT … INTO NAME n VALUE v` (verified inside a CTE). Values are cast to a common type.
  - **normalize:** `(c - avg) / NULLIF(stddev_samp, 0)`, `(c - min) / NULLIF(max - min, 0)` and `(c - median) / NULLIF(q3 - q1, 0)`, with window `OVER ()` aggregates. The output is DOUBLE.
- **Materialization (optimization only; correctness never depends on it):** a step with `materialize=true` has `mkey = sha256(prefix SQL)`. If `storage` has `derived/<mkey>.parquet`, that step's CTE becomes `read_parquet(that)`, including `__rn`. Otherwise the step compiles inline and `Materialization(step_id, mkey, sql)` is added to `pending`. `materialize_job(ctx, m)` runs `COPY (sql) TO tmp (FORMAT parquet)` and an atomic rename. It is cancellable via the engine key `mat:<mkey>`.
  - Chart-data and `POST /pipelines/materialize` enqueue pending items, deduped by `mkey`. Until `jobs/` lands the stub runner is a no-op, so steps stay inline.
  - Files are content-addressed, so a dataset refresh never serves stale data. Garbage collection of old derived files is a TODO for data-core.

## 2. Formula allowlist (`transform/formula.py`, one module)
1. Reject input over 4k characters. `sqlglot.parse(expr, read="duckdb")` must yield exactly one expression, and that expression must not be a statement.
2. Walk the AST with depth ≤ 64 and ≤ 2000 nodes. **Every node class must be in `ALLOWED_NODES`:**
   - Column (single unqualified part), Literal, Boolean, Null, Paren, Neg
   - Add/Sub/Mul/Div/IntDiv/Mod/Pow, comparisons, And/Or/Not/Is
   - In (literal list only), Between, Like/ILike, Case/If, Cast/TryCast to the `DataType` set, DPipe, Interval
   - `exp.Func` subclasses whose canonical name is in `FUNCTIONS`
   - `exp.Anonymous` whose lower-cased name is in `FUNCTIONS`

   Everything else is rejected by default, for example: Select, Subquery, Dot (`'f'.read_csv()`), Placeholder (`$1`, `?`), JSONExtract (`x -> …` parses as this), Lambda, Window, Star, Table, Command, Pragma, Set, Attach, Copy.
3. **`FUNCTIONS`** is roughly 110 names with category and signature, served to CodeMirror:
   - math, string, date, conditional, null-handling, and safe type functions (`typeof`)
   - regexp_* is safe because DuckDB uses RE2 (no catastrophic backtracking)
   - **excluded:** nondeterministic functions (now, current_date, random, uuid; they would break the cache), unbounded-output functions (repeat, range, generate_series, lpad/rpad, list_*), and every table, system or settings function (read_*, glob, getenv, query, current_setting, duckdb_*).
4. Column refs are resolved case-insensitively against the input schema (with a difflib "did you mean"). Identifiers are re-quoted, and the SQL is **regenerated from the validated AST**; the user string never reaches DuckDB.
5. `DESCRIBE SELECT <expr> FROM <input> LIMIT 0` returns the output type and maps binder errors to readable messages with character offsets.

## 3. Chart-data protocol (`chartdata/`)
**Pipeline:** request → `resolve_source` (dataset version, or compiled pipeline from `request.pipelines`) → field checks against the plot's declared channels → `base = SELECT * FROM rel WHERE spec.filters ∧ request.filters [∧ x_range]` → cache lookup → plot module `data(ctx)` or the built-in strategy → mark-cap guard → meta → Arrow IPC, or JSON `ChartStatResponse`.

**Column convention:**
- A channel declared `multiple` (y, tooltip, dimensions) is returned as `<channel><i>`: `y0..yN`.
- Single channels keep their bare name: `x`, `color`, `size`, `shape`, `facet`, `label`, `time`, `lat`, `lon`. Scatter therefore returns `x`, `y`.
- Reduction columns are `count`, plus bin edges `x_lo`/`x_hi`/`y_lo`/`y_hi` (never `y0`/`y1`). Hex bins return their center as `x`/`y`.
- `meta.fields` labels every returned column. Measures are DOUBLE; temporal x stays TIMESTAMP or DATE.

**Reduction rules** (marks = rows × series; cap = `settings.mark_cap`):

| Plot / encoding | Rule | `reduction` / `method` |
|---|---|---|
| bar/hbar/grouped/stacked, pie/donut | GROUP BY x[, color]; if groups > cap/len(y): top-N by \|y0\| plus a warning; `params.truncatedGroups` | aggregated / `group_by` |
| line/area, ordered x (temporal/quantitative) | GROUP BY x[, color]. If points per series > 2·widthPx: SQL M4 to widthPx pixel columns (≤4 pts/col/series), then tsdownsample MinMaxLTTB to 2·widthPx per series | aggregated / `group_by`, or downsampled / `group_by+minmaxlttb` |
| line/area, categorical x | treated like bar | aggregated / `group_by` |
| raw points (plot modules: scatter, strip, …) | `LIMIT cap+1` probe; ≤ cap → as is; else the module's fallback: `sample` or `bin2d`/`hexbin` | none, sampled / `reservoir`, or binned / `bin2d`, `hexbin` |
| histogram-like | `bin1d` (Freedman–Diaconis or `params.bins`) | binned / `fd`, `fixed` |
| more color series than `params.maxSeries` (default 50) | keep the top series by total, plus a warning | (unchanged) |

- **Sampling:** `USING SAMPLE reservoir(cap ROWS) REPEATABLE(seed)`. The seed is derived from the cache key, so the same request returns the same points with no flicker. Determinism was verified on DuckDB 2.0 and a test will lock it in. If it ever turns out to depend on thread scheduling, fall back to a hash-of-`__rn` Bernoulli sample.
- **x_range (detail on demand):** applies only to ordered x. It becomes a RangePredicate on the raw x field (widened to bucket edges when `timeUnit` is set), then reduction reruns on the visible slice. `meta.xDomain` returns the full extent (cheap cached `min`/`max` without x_range), so the dataZoom slider keeps its scale.
- **Payload guard:** if rows > cap after the plot module returns, chartdata truncates and warns. In tests this is an assertion. A payload over 5 MB adds a warning.
- **Kernels exported for plot agents (`chartdata/reduce.py`):** `aggregate`, `bin1d`, `bin2d`, `hexbin`, `m4`, `minmaxlttb`, `sample`, `x_range_predicate`, `agg_sql`. Each takes the `base` relation, returns `(table, ReductionInfo)` and runs through `ctx.query`.
- **Cache:** response-level LRU (cachetools, byte-bounded; or data-core's cache primitive once it merges). The key is sha256(rel.key, plotType, encodings, params, filters, x_range, widthPx rounded up to 100). `style`, `name` and `interactions` are excluded, so cosmetic edits never re-query; ui-charts should exclude them from its TanStack key too. A hit sets `meta.cached=true`.
- **Cancellation:**
  - The engine key is `chart:{slot or spec.id}`. `slot` lets two widgets that show the same chart avoid cancelling each other.
  - The endpoint becomes `async`: it awaits `run_in_threadpool(chart_data)` while a watcher polls `request.is_disconnected()` every 100 ms and calls `engine.interrupt(key)`.
  - A per-slot generation counter makes an older request that is between queries raise "superseded" (409) before it can start a query, so it never interrupts the newer one.

## 4. Dispatcher contract I need from stats (`plots/base.py`)
```python
class ChannelSpec(TypedDict): required: bool; multiple: bool; semantics: set[ColumnSemantic] | None
class PlotModule(Protocol):
    key: PlotType
    channels: dict[str, ChannelSpec]           # chartdata validates fields/types against this
    fallback: Literal["sample", "bin2d", "hexbin"] | None   # for raw-point plots over the cap
    def data(self, ctx: ChartContext) -> PlotOutput: ...    # optional; absent → built-in strategy
def get_plot(plot_type: PlotType) -> PlotModule | None      # discovery via pkgutil (stats)
```
chartdata owns and passes in `ChartContext` (`viz.chartdata.context`, which does not import `viz.plots`, so there is no cycle):
- `spec`, `viewport`, `mark_cap`
- `base: Relation` (filtered, x_range applied), `columns: dict[str, SchemaField]`
- `enc(channel, i=0) -> Encoding | None`
- `query(sql: exp.Expr) -> pa.Table`, which carries the slot key, the generation check and the semaphore
- `sample(n, fields) -> pa.Table`, sized by `settings` (stats: 1M rows; KDE: 100k)
- `reduce.*` kernels
- `run_cpu(fn, *args)`, which goes through data-core's ProcessPool for pure-Python work

A plot module returns `PlotOutput{table: pa.Table | None, stat: StatResult | None, reduction, method, fields: dict[str, str], params: dict[str, JsonValue], warnings: list[str]}`: exactly one of `table` or `stat`. Chartdata adds timing, cache, `rowsScanned`/`rowsMatched` and the cap guard.

## 5. HTTP endpoints (new or changed)
- `POST /api/charts/data` (changed): the request gains `pipelines` and `slot`. The response is Arrow, or `application/json` `ChartStatResponse`. Errors: 409 superseded, 422 spec/step error, 504 timeout.
- `POST /api/pipelines/inspect` → `PipelineInspectResult`: per-step schema and status, final columns, and optional SQL for "show SQL".
- `POST /api/pipelines/preview` → Arrow page of the data after `stepId`, with grid sort and filters; meta `PipelinePreviewMeta`.
- `POST /api/pipelines/formula/check` → `FormulaCheckResult`. `GET /api/pipelines/formula/functions` → `list[FormulaFunction]` for autocomplete.
- `POST /api/pipelines/materialize` → `MaterializeResult` (starts or reuses jobs).
- All requests are stateless: the client sends the pipeline plus the pipelines it references, so unsaved edits preview instantly. Share/embed (platform) loads them from the saved project server-side and never accepts them inline.

## 6. Contract additions (additive only)
```python
# contracts/pipeline.py
class SchemaField(Contract):
    name: str
    type: str  # DuckDB type
    semantic: ColumnSemantic
class StepError(Contract):
    message: str
    field: str | None = None
    start: int | None = None  # char offsets into the step's formula
    end: int | None = None
class StepSchema(Contract):  # server-only
    step_id: str
    status: Literal["ok", "error", "blocked", "disabled"]
    columns: list[SchemaField]
    error: StepError | None
    materialized: Literal["none", "ready", "pending"]
class PipelineInspectRequest(Contract):
    pipeline: Pipeline
    pipelines: list[Pipeline] = Field(default_factory=list)  # referenced by source/join/union
    include_sql: bool = False
class PipelineInspectResult(Contract):  # server-only
    ok: bool
    source_columns: list[SchemaField]
    steps: list[StepSchema]
    columns: list[SchemaField]
    sql: str | None
class PipelinePreviewRequest(Contract):
    pipeline: Pipeline
    pipelines: list[Pipeline] = Field(default_factory=list)
    step_id: str | None = None  # data after this step; None = final output
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1)
    sort: list[SortKey] = Field(default_factory=list)
    filters: list[Predicate] = Field(default_factory=list)
class PipelinePreviewMeta(Contract):  # server-only, travels as viz.meta
    step_id: str | None
    offset: int
    limit: int
    total_rows: int
    columns: list[SchemaField]
    ms: float
    warnings: list[str]
class FormulaCheckRequest(Contract):
    expression: str
    pipeline: Pipeline
    pipelines: list[Pipeline] = Field(default_factory=list)
    before_step_id: str | None = None  # validate against this step's input; None = final output
class FormulaCheckResult(Contract):  # server-only
    ok: bool
    error: StepError | None
    output_type: str | None
    fields: list[str]
class FormulaFunction(Contract):
    name: str
    category: Literal["math", "string", "date", "conditional", "null", "type"]
    signature: str
    description: str
class MaterializeRequest(Contract):
    pipeline: Pipeline
    pipelines: list[Pipeline] = Field(default_factory=list)
class MaterializeResult(Contract):  # server-only
    jobs: dict[str, str]  # step id → job id (started or already running)
    ready: list[str]      # step ids already materialized
# contracts/chartdata.py: ChartDataRequest gains
    pipelines: list[Pipeline] = Field(default_factory=list)
    slot: str | None = None  # cancellation slot; defaults to spec.id
# ChartDataMeta gains (server-only, so required)
    rows_matched: int | None   # after filters, before reduction (None when not counted)
    params: dict[str, JsonValue]  # resolved reduction params: binWidth, hexRadius, nOut, truncatedGroups…
    x_domain: tuple[float | str, float | str] | None
class ChartStatResponse(Contract):  # JSON body for stat-transform plots
    result: StatResult
    meta: ChartDataMeta
```
Add `ChartStatResponse`, `PipelinePreviewMeta` and `FormulaFunction` to `EXPORTED_MODELS`.

## 7. Tests (`backend/tests/transform/`, `backend/tests/chartdata/`)
- **Steps and predicates, known answers on tiny Parquet fixtures:** NULLs, empty input, dedupe keep-last with ties, pivot NULL and colliding values, unpivot type unification, constant column normalize → NULL, leading-NULL forward fill, cast failures → NULL, join suffix chains, union by name with missing columns, positional union mismatch → step error; every predicate op, including `in`/`not_in` with NULL and open range bounds.
- **Toggle/error:** a disabled step is the identity; an unknown field at step k gives k=error and later steps blocked; inspect columns equal the DESCRIBE of the executed result.
- **Formula:** positive cases per category, plus a parametrized attack corpus of 40+ strings: `read_csv(…)`, `'f'.read_csv()`, `(SELECT 1)`, `getenv`, `current_setting`, `query(…)`, `glob`, `1; ATTACH …`, `$1`, `x -> x`, comment smuggling, `repeat('a', 1e9)`, 10k-deep nesting, `"a"" ; DROP"`. Invariant: accepted output re-parses to allowlisted nodes only.
- **Hypothesis:**
  - (a) Column names from `st.text()` (quotes, spaces, unicode, `;--`, newlines, SQL keywords; unique case-insensitively). A schema-tracking strategy builds random valid step sequences, which must compile, run and match inspect's prediction.
  - (b) Random formula ASTs with optional forbidden nodes: the validator accepts exactly the clean ones.
  - (c) A differential test against pandas for filter/sort/dedupe/group_aggregate/normalize/fill.
- **Chart-data:**
  - reductions at 0, cap and cap+1; multi-y cap division
  - M4/LTTB keeps global min/max and the endpoints, at ≤ 2·widthPx per series
  - x_range gives full detail and keeps `xDomain`
  - samples are stable and ≤ cap
  - bin counts sum to n; FD width matches a hand calculation
  - a cache hit sets `cached` and a new version misses
  - same slot gives 409 for the older request, different slots are independent, a disconnect interrupts
  - a pipeline-source chart equals the same chart over the materialized Parquet
- **Perf:** `@pytest.mark.perf` (skipped by default), 10M-row aggregated chart at p50 ≤ 300 ms; qa wires it into `bench.py`.

## 8. Order (one commit each, tests green)
1. `sql.py` additions (`agg_sql`, `Relation`) with predicate tests.
2. `formula.py` with attack tests.
3. Compiler steps (simple → join/union/pivot) and the inspect, preview and formula endpoints.
4. Hypothesis fuzz.
5. Chartdata refactor: context, dataset/pipeline sources, cache, slot generations and disconnect, basic family with line downsampling, x_range.
6. Reduction kernels for plot agents.
7. Materialization on the jobs stub, then the real `jobs/`.
8. StatResult path once the stats seam lands.

## 9. Requests to others
- **data-core:**
  - `Engine.interrupt(key) -> bool`, and generation-aware keys (`cursor(key, generation=n)`, where an older generation never interrupts a newer one).
  - `LocalStorage.derived_rel(key) -> str` (`derived/<key>.parquet`), with GC later.
  - jobs: `submit(kind, dedupe_key, fn(JobContext)) -> job_id`, where `JobContext` has `progress(frac, msg)`, `cancelled` and `engine_key`. Until it lands I use a no-op stub.
- **stats:** the `plots/base.py` seam in §4.
- **ui-charts / ui-data:** send `pipelines` (and `slot` for dashboard widgets); exclude `style`/`name` from query keys; handle JSON `ChartStatResponse`; read the new meta fields.
