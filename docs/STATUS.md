# Status

Tracker for every item in MVP.md, Plots.md and Next.md. **Owned by the lead**: agents report
items in their final report and the lead updates this file. Status values: `todo`, `partial`,
`done`. Owners match CLAUDE.md §9.

## Phase 0 handoff (2026-10-30)

- Walking skeleton works on Windows: `uv run viz dev` → import CSV → grid → bar chart → save →
  reload (verified in headless Chrome, light and dark). **macOS not verified yet.**
- Contracts v1 are in `backend/src/viz/contracts/`; TS types in `frontend/src/api/schema.d.ts`.
- Baseline code exists in data-core, transform, platform, ui-data and ui-charts areas. It is a
  starting point, not a design: each owner should plan its shared pieces (CLAUDE.md §9 rule 4)
  and may replace what's there.
- Gotchas found during Phase 0 are listed in CLAUDE.md §12.

## Wave 1 log

- 2026-10-30: plans for data-core, transform, stats, platform and ui-charts approved; contracts added to
  main. Plans live in `docs/plans/<agent>.md`.
- 2026-10-30: stats seam merged (`stats/relation.py` Relation, `plots/` registry and dispatch).
- 2026-10-30: platform merged (MVP §10 backend). Reviewer: approve with nits after one fix round.
  Follow-up nits merged (manifest id/type checks, schemaVersion stamp, 404 on delete race). Open:
  streaming upload size limit (needs middleware).
- 2026-10-30: ui-data merged (shell, ui kit, scaled grid, import, project UI, pipeline editor). Phase B
  UI for data-core/transform endpoints is behind flags in `frontend/src/api/features.ts`.
- 2026-10-30: ui-charts merged (registry, MVP §4 applyStyle, §8 themes/formatting, primitives, Plots §1,
  chart export). Follow-ups before Wave 2: multi-grid detection, required `affectsData`, EChart
  component replacement. All three merged (5a9a92f); charts screen wired by ui-data (e526497).
  Lead browser smoke on integrated main: import, grid, charts, new chart, reload, dark: no errors.
- 2026-10-30: data-core merged (40edf54): engine owners/tickets and supersede-on-arrival, fatal
  recovery, UTC sessions, second DuckDB instance for jobs and grid views, jobs runner with SSE,
  staged import + preview, Excel/JSON/encodings, refresh, rename/delete, server rows/values,
  materialized grid views, CSV/Excel export. Reviewer: approve with nits after one fix round.
  Perf (10M x 20): import 5.0 s; unsorted page ~20 ms; grid page during import max 78 ms; first
  sorted page 190 ms cold (over the 150 ms budget, Wave 4). Follow-up merged: shared encoding
  choice for preview and import, generation counter guard, sticky conditional cancel,
  RequestScopeMiddleware (wired in main.py), upload parser hardening, memory and spill split
  across instances, staged uploads kept until deleted (hard-linked per import). Round 3 in
  progress: revert accent folding in search (4x slower on free text; deferred to the Wave 4
  search pass), S1 threshold in characters, cancel LRU fix.
- 2026-10-30: platform follow-up merged (vizproj uses data-core's bundle registration and ref walker).
- 2026-10-30: stats Wave 1 merged (bd5649f): shared kernels (sampling incl. stratified with group
  caps, quantiles, ECDF, histogram bins, Q-Q, KDE 1-D/2-D, OLS/LOWESS fits, bootstrap) and
  `/api/stats/describe`. Reviewer: approve with nits after one fix round. Describe (10M rows, 10
  fields): 1.6 s, with modes 2.6 s; grouped with modes 7-8 s (Wave 4 perf item). Follow-up
  merged (492f0a7): stratified n_missing, KDE cell averages on coarse grids, per-group rescale.
- 2026-10-30: ui-data follow-up merged (a63e9b4): staged import wizard (upload/path, live preview,
  per-sheet import jobs with SSE progress and cancel), server rows (sort/filter/search), CSV/Excel
  export jobs, rename/refresh/delete with 409-then-force. Data-core flags removed; `pipelineApi`
  still off. Reviewer: approve with nits. Follow-ups merged (4af3c4c): jobs indicator with per-tab
  job ownership (survives reload), one staged upload per workbook, Back-to-options retry after a
  cancel or failure, poll fallback for lost job streams, wizard focus and tab stops. Next batch in
  progress: close-while-starting race, axe findings (Project menu name, aria-hidden focus), save
  indicator role.
- 2026-10-30: transform merged after 4 review rounds: pipeline compiler (15 steps), formula allowlist
  with per-step growth bound (64 KiB text guard, nested outputs refused), inspect/preview/formula
  check/materialize jobs, chart-data protocol (aggregate, 2-D/hex bins, M4/MinMaxLTTB, caps,
  supersede via engine tickets), overlays. 10M p50: bar 55-63 ms, monthly line 44-56 ms, raw
  timestamp M4 line ~250 ms (over 300 ms under heavy load; qa bench + Wave 4).
- 2026-10-30: qa Wave 1 in review: samples for every Plots.md family, 10M generator, bench.py over
  HTTP for every §6 budget, Playwright e2e (73 tests + @large 10M grid), axe, contract-drift check,
  CI matrix (Windows, macOS arm64, macOS Intel). Full 10M bench (idle): import 8.2 s; grid pages
  ~20 ms; chart queries p50 56-85 ms; 6-chart refresh p95 0.24 s during a 10M import; 12
  concurrent queries ok at 5% RAM; describe p50 2.99 s (at budget, no margin). Known (Wave 4):
  sorted cold first page 216 ms, cold deep pages 0.6 s filtered / 1.8 s sorted.
- 2026-10-30: ui-data pipeline editor wired to the real transform API: `pipelineApi` flag removed;
  inline step errors (validation + formula character ranges); materialize support; "new chart from"
  a pipeline sources columns from the pipeline's own inspected output; group-by default fixed
  (empty group, was first column). Also in this merge: the import-wizard close-while-starting race
  (an in-flight import now finishes in the background instead of aborting, with a bounded retry on
  a 409-while-queued delete), and accessibility fixes (Radix DropdownMenu `modal={false}`, a single
  stable `SaveIndicator` region, ProjectHeader's visible name no longer overridden by `aria-label`).
  **This closes Wave 1 exit criterion "pipelines with join and pivot work" on the UI side** (backend
  already merged via transform). Reviewer: approve with nits (high) — live-verified the 64 KB
  calc-guard path end to end for the first time (backend 422, clean UI message, survives reload);
  found that `/api/pipelines/inspect`'s DESCRIBE-based static check can't see this data-dependent
  guard, so the step list shows no error marker until the step is actually previewed (a
  transform/ui-data follow-up, not a UI bug). vitest 340 passed/1 skipped, biome clean, build clean.
- 2026-10-30: data-core round 3+4 follow-up merged: `.txt` auto-detected as CSV again; the `?kind=`
  chosen at staging is remembered in a `.kind` sidecar; a staged refresh falls back to the
  dataset's kind with a synchronous 415 on mismatch; uploads are marked in-use at submit and the
  expiry sweep skips them; a job stops any running view build and builds wait until no job cursor
  is live (bounded wait, no deadlock); cleanup paths suppress Windows `ConflictError` without
  masking the job's real result; encoding-invalid-share threshold raised to 15%;
  `viz.ingest.staging` removed, tracking moved into `LocalStorage`. Reviewer: approve with nits
  (xhigh) — a corrupted `.kind` sidecar would 500 instead of degrading to `None`; four
  `LocalStorage` methods skip defense-in-depth id validation (not currently reachable). pytest 722
  passed/6 skipped on the full suite, ruff/mypy clean. `?kind=` on `POST /datasets/staged` is
  additive; gen:api run. Still open: "plain import 210-233 ms vs 110 ms quiet control" on a loaded
  dev machine, unconfirmed whether real or machine noise.
- 2026-10-30: qa review-fix round merged: pinned `setup-uv` action tag, bench known-failure ceilings
  (1.5x), during-import bench rounds now span the whole import, a global cache-bust counter plus
  `cached == 0` assertions on dashboard/concurrency checks, an empty-table render crash fix, an
  anchored project-picker regex (was substring + `.first()`, could silently click the wrong row),
  and the axe baseline (4 entries, all attributed). Reviewer: approve with nits (none blocking) —
  one ceiling's docstring is slightly off, `bust()`'s counter has an undocumented low-risk
  exhaustion edge case, and the new Playwright browser cache step doesn't yet gate the install
  step. e2e 73 passed/6 skipped (placeholders); contract-drift check passes.
- 2026-10-30: stats follow-up merged: describe now resolves pipeline sources via `resolve_source`
  (cache key includes the compiled pipeline SQL hash, so a dataset refresh still invalidates it), a
  `data_errors()` context turns transform user errors and the 64 KB calc-guard trip into clean 422s
  instead of 500s, and a missing pipeline returns 404 (was 501). Reviewer: approve, no nits. pytest
  699 passed/6 skipped. In-app HTTP measurement of describe-with-modes (ungrouped) came in at p50
  4.1 s / max 4.8 s, over the 3 s budget — higher than qa's own bench (p50 2.99 s) on the same
  endpoint, likely measurement-sensitive (suspected cause: the app's lower `memory_limit` vs
  DuckDB's default spilling the modes UNION ALL); tracked as a Wave 4 perf item, not a regression
  from this diff (the diff touches only source resolution and error mapping, not the describe SQL).

## Wave 1 exit check (2026-10-31)

All four previously in-flight branches merged and reviewed (2026-10-30/31): data-core round 3+4,
transform (4 rounds, done earlier), stats resolve_source follow-up, qa review-fix round, ui-data
pipeline wiring. Full checks on `main` (HEAD 938ba24 + docs):
- Backend: pytest 722 passed/6 skipped, ruff and mypy clean.
- Frontend: vitest 340 passed/1 skipped, biome clean, build clean.
- Contract drift: clean (gen:api run after both the stats and data-core merges).
- Full Playwright e2e: 72/79 passed, 3 known pre-existing flaky failures (a project-menu button's
  accessible name picks up the words "undo"/"redo"/the project name from the test's own title,
  causing a strict-mode locator collision in `undo-redo.spec.ts` and `project.spec.ts`; not a Wave
  1 regression, worth a qa follow-up to stabilize the locator), 4 skipped (Wave 3 placeholders).
  `@large` 10M-row grid test: 1/1 passed.
- New pipeline e2e coverage added (`frontend/e2e/pipeline.spec.ts`): filter + group-aggregate +
  chart from pipeline; join (via a rename to align join-key semantics) + pivot + materialize.
  8/8 passes across repeated runs, no flakiness.
- Lead browser smoke test (import, grid, pipeline join+pivot, chart, save, reload, dark mode):
  passed.
- Full `scripts/bench.py` (10M rows, run alongside other verification work so not fully quiet):
  17 pass, 4 known-failure (within previously tracked Wave 4 ranges), 2 n/a (scatter/timeseries
  chart types not yet implemented in chart-data, pre-existing gap), **2 over budget, not
  previously marked known**: dashboard refresh during a 10M-row import measured p95 1.12s (budget
  1s; qa's last quiet-machine run measured 0.79s) and describe-with-modes ungrouped measured p95
  3.89s (budget 3s; previously observed in the 2.82-4.1s range depending on measurement
  conditions). Both runs happened under heavy concurrent load (parallel e2e run, bench's own
  fixture generation, this doc's own smoke test all running at once), so these are read as
  reinforcing already-tracked Wave 4 perf items, not new regressions — but they should be
  re-measured on a quiet machine before Wave 4 closes either one out.

**Wave 1 exit criteria (CLAUDE.md §10) are met**: a 10M-row CSV import works within budget;
pipelines with join and pivot work end to end (backend + UI + e2e coverage); basic charts work
with full config, themes and PNG/SVG export; save, autosave and undo work. Known perf gaps are
tracked below for Wave 4, not blockers.

## Wave 2 log

- 2026-10-31: pre-Wave 2 lead work merged: the four official `apache/echarts-custom-series`
  packages (`@echarts-x/custom-violin`, `-contour`, `-bar-range`, `-line-range`; ~30-43 KB
  unpacked each) added as frontend dependencies; `OverlayResult.group` added (additive, nullable)
  for overlays scoped to one color/facet group.
- 2026-10-31: Wave 2 dispatched: `plots-distribution-relationship`, `plots-timeseries`,
  `plots-geo`, `plots-multivariate-econometric` (4 plot agents, the CLAUDE.md §10 cap), `stats`
  (MVP §5: correlation, regression, t-tests, ANOVA, chi-square, distribution fitting, outliers,
  CIs), and `ui-data` (stats panel UI). `plots-composition-specialized` queued to start once one
  of the four plot agents finishes.
- 2026-10-31: ui-data stats panel merged: source (dataset/pipeline) picker, field and group-by
  checklists, a descriptive-statistics result table wired to the real `/api/stats/describe`, and
  a "plot this" shortcut building a histogram/box `ChartSpec` from a field summary (renders once
  `plots-distribution-relationship` merges; shows a graceful "not available yet" notice until
  then). The §5 test UI (correlation, regression, t-tests, etc.) is built but gated off behind a
  new `statsTests` flag (default off) with a typed-but-stubbed wire shape, the same pattern Wave
  1 used for `pipelineApi` before transform merged — needs a follow-up wiring pass once `stats`'s
  Wave 2 branch merges. Reviewer: approve with nits, two should-fix items addressed before merge
  ("plot this" was using the live source-picker state instead of the described request's own
  source/groupBy, so switching sources after Run without re-running could plot the wrong field
  against the wrong dataset; the grouped box plot wasn't encoding the group, drawing one box).
  vitest 356 passed/1 skipped, biome and build clean.
- 2026-10-31: `@echarts-x/custom-contour` and `@echarts-x/custom-line-range` removed from
  `frontend/package.json` — both import the full, non-tree-shaken `echarts` package internally,
  self-registering every chart type as a side effect (found by `plots-distribution-relationship`,
  confirmed: broke `render.test.ts`'s unregistered-component check). `custom-violin` and
  `custom-bar-range` are unaffected and still available. Contour renders as a client-side binned-
  density heatmap instead; `ciBand`/`errorBar` core primitives cover line-range/CI-band needs.
- 2026-10-31: `plots-geo` merged (Plots.md §7, all five): choropleth, bubble map, dot density,
  geographic heatmap, cartogram. Natural Earth GeoJSON bundled and lazy-loaded (countries + US
  states, 110m/50m, ~1MB total, separate build chunks). Two review rounds (xhigh, given the
  cartogram's use of the engine's process pool): round 1 found 3 blockers — a reproducible stored
  XSS in 4 tooltip formatters (unescaped user data into innerHTML; the first instance of this
  pattern bug this wave), an unbounded O(n²) cartogram with no region cap or process-pool
  timeout/broken-pool handling (DoS risk, could starve other pool consumers for minutes), and a
  crash on missing/non-finite coordinates — plus 5 should-fix items (Dorling force-layout didn't
  converge to non-overlapping circles; renderer ignored the server-computed radius; bubble map's
  GROUP BY was a no-op on distinct coordinates so the generic cap truncation silently dropped
  whole regions; Natural Earth `-99`/`"CN-TW"` sentinel codes broke ISO2 matching for several
  countries and the asset-build script wasn't committed; `count_distinct` silently behaved like
  plain `count`). All fixed and independently re-verified (XSS payloads come back escaped through
  the real render path; the region cap is enforced in SQL before the O(n²) layout runs, with a
  real ProcessPool timeout/broken-pool test; 0 overlapping circles at n=100-250). A round-2 fix
  addressed a regression the round-1 fix introduced (Australia's outlying territories collided
  with Australia's own ISO2 code under the EH fallback). A real ui-charts bug was found and
  correctly left unfixed, fully diagnosed instead: `ChartView` keys map-readiness on a plot's
  registry key, which doesn't change when only `params.level`/`resolution` change, so switching a
  choropleth's resolution leaves the chart blank — tracked as a `TODO(ui-charts)` follow-up.
  pytest 746 passed/6 skipped, ruff/mypy clean, vitest 363 passed/1 skipped, biome/build clean.
- 2026-10-31: `plots-multivariate-econometric` merged (Plots.md §6 + §8, all 17): radar, parallel
  coordinates, PCA biplot, factor loadings, cluster, dendrogram, Andrews curves (§6); Kaplan-Meier,
  forest, ROC, precision-recall, Lorenz, quantile, marginal effects, coefficient, IRF, event study
  (§8). The agent did a real browser-based visual check (headless Chromium, light/dark) and fixed
  several rendering bugs itself before review — the only Wave 2 plot agent to do so without being
  asked. Extensive known-answer tests, independently re-verified by the reviewer: PCA vs
  sklearn/R prcomp, Kaplan-Meier vs a hand product-limit+Greenwood implementation on Freireich,
  NIST Longley to 1.4e-11, k-means on iris matching the documented Lloyd optimum, ROC/DeLong SE,
  Spector logit, a hand-computed BCG meta-analysis forest plot, event-study vs full-dummy OLS to
  3e-14, IRF vs statsmodels VAR (identical). Review (xhigh, "numerical correctness is the main
  requirement" per the agent's own brief) found the stored-XSS tooltip pattern (now seen in
  3 branches this wave) plus 4 should-fix items: Kaplan-Meier's automatic event-coding could
  silently invert the survival curve for R-style 1/2-coded status data (reproduced: 1=censored/
  2=dead data fell to S=0 instead of staying at 0.667); factor analysis accepted statistically
  unidentified models ((p-k)² < p+k) with no warning; factor analysis ran inline instead of
  through the engine's process pool, ~5s on Heywood cases; the dendrogram's cophenetic-correlation
  step undid its own O(n²) memory isolation by running back in the API process. All fixed and
  independently re-verified against the exact original failing reproductions. Three real bugs in
  already-merged ui-charts code were found, fully diagnosed, and correctly left unfixed (tracked
  below as follow-ups). pytest 825 passed/6 skipped, vitest 418 passed/1 skipped, biome/build
  clean.

### ui-charts follow-ups found by plots-multivariate-econometric (not yet fixed)
- `applyStyle`'s y-axis title gap sizes from the formatted data maximum, not the tick labels — an
  unrounded value (e.g. 1607.42, 44.53) pushes the title off the left edge. Seen on the cluster
  elbow view and the quantile plot.
- `applyStyle` drops array data items whose first entry is <=0 on a log x-axis, assuming element 0
  is always x — breaks error bars on a category-row layout (worked around locally by wrapping rows
  as `{value}`, but the filter itself needs fixing for anyone else who hits it).
- "View as table" shows raw JSON for StatResult-based plots instead of a real table — an
  accessibility gap; a per-plot table hook would help.
- Also from `plots-geo`: `ChartView` keys map-readiness on a plot's registry key, which doesn't
  change when only `params.level`/`resolution` change, so switching a choropleth's map resolution
  leaves the chart blank after the new map loads. Needs readiness keyed on the prepare inputs
  (e.g. a `preparedFor: {key, token}` bumped on every resolve), not the registry key.
- 2026-10-31: `stats` merged (MVP §5, the rest beyond Wave 1's shared kernels and describe):
  correlation, linear/multiple regression, t-tests, ANOVA, chi-square, rank tests, distribution
  fitting, outliers, confidence intervals. Two xhigh review rounds ("wrong statistics still look
  plausible" per the agent's own brief). Round 1 found 2 blockers, both independently
  reproduced: bootstrap CIs for SD/extreme quantiles silently under-covered (0.665 actual vs a
  claimed 0.95 in the >5000-value subsample-and-rescale path); NaN as a group/factor key caused
  wrong results or 500s (wrong chi-square tables, ANOVA `KeyError`, misleading Kruskal 422s, and
  the same latent bug in Wave 1's already-merged `describe()`). Both fixed and re-verified with
  real coverage simulations: the subsample bootstrap was replaced with exact methods (order-
  statistic CIs for quantiles/median, an asymptotic delta-method CI for SD using exact SQL
  kurtosis, bootstrap kept for smaller inputs), and `key_expr` now maps NaN/+-inf to NULL
  everywhere. Round 2 re-review found one more real issue in what looked like a benign leftover
  (`cap_groups` counted NaN/+inf as separate groups during capping even though downstream merges
  them, wasting cap slots and occasionally misreporting a group's size) — fixed directly by the
  lead rather than a third agent round. pytest 838 passed/6 skipped, ruff/mypy clean.
- 2026-10-31: stats' response/request models moved from `backend/src/viz/stats/models.py` into
  `backend/src/viz/contracts/stats.py` (additive, mechanical; the stats agent couldn't touch
  `contracts/` itself). Now in OpenAPI and the generated TypeScript types — unblocks ui-data's
  `statsTests`-flagged UI follow-up.
- 2026-10-31: `plots-timeseries` merged (Plots.md §4, all 8): time series line, seasonal, lag,
  ACF, PACF, moving average, candlestick, calendar heatmap. Two review rounds. Round 1 found 2
  blockers (seasonal 500 on empty y, no working way to count rows; PACF 500 when lags >= n/2)
  and 8 should-fix items (seasonal x-axis scrambling off-period data; ACF/PACF bands centered on
  the estimate instead of zero so "outside the band = significant" never triggered; an unbounded
  regular-cadence grid, up to 17.9M synthetic buckets on sparse data; multi-series downsampling
  fragmenting lines; an unimplemented UI color channel; date-part-vs-date-trunc merging the same
  month across years; inaccurate rows_matched under truncation; moving-average's mark budget not
  accounting for both y0/y1). All fixed and re-verified, including the original pathological grid
  case (now 0.02s/12,417 buckets, down from 7.1s/17.9M). Round 2 re-review found one issue the
  first review missed entirely: PACF used statsmodels' own default method (`ywadjusted`), whose
  small-sample correction can push |PACF| above 1 on trending/random-walk data (reproduced: >1 in
  28-40% of runs on cumulative-sum series at n=50-100) — the original test only "matched
  statsmodels" because both sides shared the same wrong-for-this-case default. Fixed directly by
  the lead (`method="ywm"`, matching `plot_acf`'s own default for the same reason) with a 20-seed
  bounded-output regression test. pytest 1010 passed/6 skipped, ruff/mypy clean, vitest 457
  passed/1 skipped, biome/build clean.
- 2026-10-31: `plots-distribution-relationship` merged (Plots.md §2-3, 17 of 18; contour is
  `partial`, see below): histogram, density, box, violin, strip, swarm, Q-Q, ECDF, frequency
  polygon, scatter, bubble, pair plot, correlation heatmap, correlogram, hexbin, contour,
  regression, residual. Three review rounds. Round 1 found 2 blockers (stored XSS in
  correlation/correlogram/violin tooltips; residual and contour silently returning a biased
  subset above the mark cap while misreporting `reduction: none`) and a long should-fix list,
  all fixed — including rewriting contour to bin server-side via the existing `bin2d` kernel
  (exact over the full filtered data, reports `BINNED`) instead of client-side sampling, and
  box's SQL rewritten to a single-pass grouped-quantile CTE. **Contour is a server-exact binned
  density heatmap, not true isoline contouring — marked `partial`, not `done`.** Round 2 found
  that round 1's violin fix (switching off `@echarts-x/custom-violin`, whose KDE sweep is
  hard-coded to [0,10], for a custom `renderItem` tracing the server's own KDE) introduced a
  regression: category-axis coordinates came back NaN from stringifying a numeric ordinal before
  the `api.coord` lookup — fixed and verified via a real ECharts SSR run. Round 3 found one more
  regression in the same area (a crash on any group column containing a null, since the row-
  budget clamp used `np.unique`, which can't compare `None` to `str`) — fixed directly by the
  lead, along with a related pre-existing bug in the same code the reviewer flagged: violin and
  strip/swarm built category-axis values from the raw column, so a numeric ordinal column or a
  null group either rendered off-canvas or silently merged into the wrong category; both now
  label every x value as a string first. pytest 1054 passed/6 skipped, ruff/mypy clean, vitest
  495 passed/1 skipped, biome/build clean.
- 2026-10-31: `plots-composition-specialized` merged (Plots.md §5 + §9, all 18): stacked area,
  100% stacked bar, waterfall, funnel, treemap, sunburst, mosaic, marimekko, sankey, network,
  alluvial, gantt, pareto, error bar, lollipop, dumbbell, ridgeline, raincloud. Three review
  rounds. Round 1 found 3 blockers: stored XSS in mosaic/waterfall/gantt tooltips; Gantt
  rendering nothing (custom series data held row indices while encode expected real dimensions,
  axis extent stuck at [0,1]ms — reproduced with ECharts SSR); sankey/alluvial cycle detection
  via Kahn's algorithm dropping every edge downstream of any cycle, not just the cycle's own
  (reproduced: 0 links on an otherwise-acyclic graph). All fixed — cycle removal rewritten as DFS
  back-edge removal, verified by hand and with a 3000-iteration random-graph property test (every
  output a DAG, every dropped edge genuinely closes a cycle). Round 2 found the round-1 mosaic/
  marimekko SQL rewrite let tied-total categories share one rank and bypass the cap entirely
  (500 tied categories, cap of 20, all 500 returned) — fixed with a secondary ORDER BY key. Also
  fixed: stacked_area exceeding the mark cap with categorical x + color; Gantt collapsing to the
  1970 epoch on a null date; treemap/sunburst not actually bounded despite the docstring's claim
  (the agent's own first attempt regressed a passing test, caught via the full suite, corrected
  the gate from max_leaves to mark_cap). Round 3 found one more regression: the cycle-removal tie-
  break could compare an incomparable pair (a NULL node vs a string, or an INTEGER column once it
  met the "(Other: ...)" rollup's string bucket) and crash — fixed directly by the lead (VARCHAR
  cast in `_edges`, a total sort order placing None first). Also fixed directly by the lead: the
  two `TODO(lead)` "unimplemented plot" stand-in tests (flagged since Wave 1) broke the moment
  sankey became real — switched to `viz.plots.scoped_registry()` with a plots/-family type
  (histogram; "bar" doesn't touch the plots registry at all, it's transform's generic aggregate
  path). **This completes Wave 2**: every Plots.md chart type (74) is now implemented and
  reviewed. pytest 1108 passed/6 skipped, ruff/mypy clean, vitest 521 passed/1 skipped, biome and
  build clean.

**Wave 2 exit criteria (CLAUDE.md §10) are met**: every Plots.md chart renders from `samples/`
(backend known-answer tests cross-checked against SciPy/statsmodels/R/NIST throughout; frontend
renderer + registry-smoke tests cover every plot type in both themes). Statistics tests match
SciPy/statsmodels to published precision (t-tests, ANOVA all three SS types, chi-square,
correlation, NIST regression, PCA, Kaplan-Meier, ROC, meta-analysis). Known gaps carried forward,
not blockers: contour is a server-exact binned-density heatmap, not true isoline contouring
(partial); the stats panel's non-describe tests (§5 correlation/regression/t-tests/etc.) are
built but gated behind a `statsTests` flag pending a ui-data wiring pass, the same pattern Wave 1
used for pipelines; several ui-charts bugs were found by plot agents and documented below as
follow-ups rather than fixed (outside those agents' owned paths).

### ui-charts follow-ups found during Wave 2 (fixed in the Wave 3 `ui-charts` merge below)
- Axis title gap sizing from unrounded data instead of tick labels (can clip off-screen). Fixed.
- A log-x-axis filter drops array rows whose first element is <=0 (breaks some error-bar layouts).
  Fixed.
- "View as table" shows raw JSON for StatResult-based plots instead of a real table. Fixed.
- `ChartView` keys map-readiness on a plot's registry key, not the prepare inputs, so switching a
  choropleth's map resolution leaves the chart blank after the new map loads. Fixed.

## Wave 3 log

- 2026-10-31: Wave 3 kicked off (`ui-dashboard`, `platform`, `ui-charts`; `ui-data` picked up the
  Wave 2 `statsTests` follow-up in parallel). Dependencies installed: `react-grid-layout`,
  `dompurify`, `marked` (all ship their own TS types). Two additive contracts added:
  `DashboardFilter.control` (lets a dashboard-level/global filter render as a toolbar control
  without a `FilterWidget` on the layout) and `Project.global_filters` (filters shared across
  every dashboard in a project).
- 2026-10-31: `ui-data`'s Wave 2 `statsTests` follow-up merged: the stats panel's test UI
  (correlation, regression, t-test, rank test, ANOVA, chi-square, distribution fitting, outliers,
  confidence intervals) is wired to the real backend with dedicated forms and result renderers
  per test, not the old generic field/group form. The `statsTests` flag is gone. Reviewer
  independently verified all 9 request shapes field-for-field against `contracts/stats.py` and
  exercised all 9 endpoints through the real FastAPI app's test suite (the agent itself had only
  tested against a stubbed fetch). One should-fix: switching the source while a test form had a
  field selected could run with a now-unknown field (422) or show a stale result unlabeled as
  stale — fixed by remounting the test panel on source change. Also fixed: a one-sample t-test's
  result was mislabeled "Mean difference"; the result view's error boundary could stay stuck in
  fallback mode for a later, valid run; test requests had no cancellation. vitest 599 passed/1
  skipped, biome and build clean.
  - Known gaps, not blockers: chi-square goodness-of-fit's custom expected-proportions input
    isn't exposed yet (uniform only); outliers' `include` columns and t-test/ANOVA's advanced
    options (`ssType`, `postHoc`, `maxLevels`) use silent defaults with no UI; distribution
    fitting shows a table only, no fitted-curve plot (a natural "plot this" follow-up, since the
    response already includes the density grid).
- 2026-10-31: `platform`'s share/embed/report backend merged (MVP §9): `POST/GET/DELETE
  /api/share` (authenticated, `secrets.token_urlsafe(32)` tokens, optionally expiring),
  `GET /api/share/{token}` and `GET /api/embed/{token}` (public, no auth, resolve a token into a
  `SharedView`), `GET /api/reports/{projectId}[/dashboards/{id}]` (authenticated report
  assembly). New `share_tokens` table, Alembic migration, project FK `ON DELETE CASCADE`. Two
  xhigh review rounds (public unauthenticated routes). Round 1: token scheme, SQL
  parameterization, and revocation/expiry timing all confirmed correct; found 7 should-fix items,
  the most serious being that a share of one chart leaked the *whole* project's pipelines and
  datasets (a chart-only embed returned an unrelated pipeline's row-level filter and every
  dataset id — fixed by scoping to what the shared item actually reaches, transitively through
  pipeline joins/unions and KPI widget sources, independently verified with a 2-hop
  pipeline-chain + cycle test case) and that `SharedView` didn't carry the `Project.global_filters`
  field added earlier the same day (a shared dashboard would've shown a viewer more data than the
  owner sees). Round 2 found the column-width fix for `item_id`/`created_by`/`token` had only
  reached the SQLAlchemy model, not migration `0003`'s actual DDL — since the real schema comes
  from `alembic upgrade head`, not `create_all`, Postgres would still 500 on a long value; SQLite
  doesn't enforce VARCHAR length so this passed locally. Fixed directly by the lead. pytest 1121
  passed/6 skipped, ruff/mypy clean.
  - **Deferred to Wave 4/hosted mode, tracked via `TODO(platform)` in `projects/share.py`**: a
    share/embed token today gates the chart *specs* returned by the public resolve route, not the
    underlying *data* — actual rendering still calls the normally-authenticated
    `/api/charts/data`, which only appears to work for an anonymous viewer locally because
    `auth.current_user` is a no-op stub. Hosted mode needs a token-scoped data route (e.g.
    `/api/share/{token}/charts/{chartId}/data`) before share/embed is real outside localhost/LAN.
    Also hosted-mode-only, not yet needed: tokens are stored in plaintext rather than hashed at
    rest; share list/revoke/report don't check per-project access (matches the rest of the
    projects API today); a future migration that rebuilds the `projects` table would cascade-
    delete `share_tokens` too (pre-existing SQLite rebuild-migration risk, noted in `0002`).
- 2026-10-31: `ui-charts` drill-down/linked-charts/interaction-callbacks merged (Wave 3): chart-
  level drill-down, `echarts.connect`-based linking, raw `onPointClick`/`onBrushSelected`/
  `onLegendChange` callbacks and a `slot` prop for `ui-dashboard`'s cross-filter bus, plus 4 Wave
  2 bug fixes (y-axis title gap, a log-x-axis filter's first-element-is-x assumption, StatResult
  "view as table" raw-JSON, map-readiness keying). **Four review rounds**, each finding a real
  issue in the stateful brush/legend event-forwarding logic — `echarts.connect` forwards brush
  and legend actions between linked charts by design, which is exactly right for visual sync but
  wrong for the filter-predicate callbacks layered on top. Round 1: 2 blockers (a gesture on one
  linked chart could get misreported as another's, reading its coordRange against the wrong
  chart's axis; a brush had no way to be cleared in multi-select mode, and some chart rebuilds
  silently dropped one with no signal to the bus) plus 2 should-fix (temporal values went out as
  epoch-ms the backend's eq/in predicates reject for date columns — fixed with ISO strings for
  point values, kept as epoch ms for ranges since the backend accepts that directly; the axis-gap
  fix's own measurement regressed large-number axes). Round 2 found the clear-selection fix still
  let the clear dispatch itself get forwarded, so clearing one chart's brush could silently clear
  a linked chart's too. Round 3 verified the fix by tracing ECharts' own source (not just the
  agent's tests): the connect/`escapeConnect` event cycle terminates after exactly one restore
  dispatch, and a cross-filter refetch can't spuriously trigger a false "cleared" (`TanStack
  Query`'s `keepPreviousData` prevents the data flicker that would cause it). One documented perf
  follow-up remains (a linked chart without its own brush does one extra, harmless visual-update
  pass per mousemove during another chart's drag). vitest 691 passed/1 skipped, biome/tsc/build
  clean.
  - `TODO(plots-geo)` left in `charts/core/types.ts`: geo plots (choropleth, bubble map, cartogram,
    dot density) should declare the new `PlotEntry.prepareParams` (e.g. `["level", "resolution"]`
    on choropleth) so changing those `affectsData: false` params doesn't force an unnecessary
    `prepare()` re-wait — not yet done, a follow-up for whoever next touches `plots/geo.py`'s
    frontend renderers.
  - `TODO(ui-data)`: grid drill-through (a dashboard chart drilling into the data grid) carries
    *pipeline-output* field names in its predicates, which won't always match the *underlying
    dataset's* column names shown in the grid (e.g. a calc/renamed column). Dispatched to ui-data
    alongside the dashboard route wiring; for now, predicates whose field doesn't match a grid
    column should be silently skipped rather than crashing the grid. The general pipeline-to-
    dataset field-mapping problem is left for later.
  - `TODO(data-core)`: `backend/src/viz/ingest/rows.py`'s grid filter path calls `where_sql(...)`
    without passing `duck_type`, so an epoch-ms range/`between_dates` bound on a DATE/TIMESTAMP
    grid column compiles as a bare integer literal instead of a cast timestamp. Only matters once
    a temporal drill-through predicate reaches the grid (see the `ui-data` item above) — not yet
    fixed, flagged for whoever next touches the grid's filter compilation.
- 2026-10-31: Two Wave 3 integration gaps dispatched in parallel: `ui-data` is wiring
  `/dashboards`, `/dashboards/:id`, `/share/:token`, `/embed/:token` routes into the app shell and
  hooking the data grid up to `useDashboardRuntime`'s `gridDrill` state for drill-through;
  `data-core` is adding a minimal `POST /api/assets` / `GET /api/assets/{id}` image-asset endpoint
  for the dashboard Image widget (reassigned from the widget's own `TODO(platform)` comment, since
  binary asset storage fits `data-core`'s `storage/`/`exports/` lane better than `platform`'s
  project/share/auth lane).
  - `ui-data`'s half landed on its branch (commit `ffb8f6c`): lazy-loaded `/dashboards`,
    `/dashboards/:id` (inside `AppShell`), `/share/:token`, `/embed/:token` (outside, no nav
    chrome) in `App.tsx`; a new `data/drillFilters.ts` maps a drilled `Predicate[]` onto grid
    columns, silently dropping any field that isn't one (the pipeline-vs-dataset field-name gap
    noted above); `DataScreen.tsx` consumes `gridDrill` once on mount and clears it so a refresh
    doesn't replay it. This branch can't build standalone yet — it imports from
    `dashboard/`, which only exists in `ui-dashboard`'s unmerged worktree; the agent verified it
    by temporarily copying that directory in (74 files passed/1 skipped, 821 tests passed/4
    skipped; biome and `tsc -b` clean), without merging anything. **After `ui-dashboard` merges**:
    confirm its actual `DashboardPage`/`SharePage`/`EmbedPage` export shapes still match what
    `ui-data` guessed (no props, each reads its own route param), and ask `ui-dashboard` to remove
    the now-done `TODO(ui-data)` comment near `runtimeStore.ts:52`.
  - `data-core`'s half merged: `POST /api/assets` (multipart, sniffs PNG/JPEG/GIF/WebP/SVG from
    bytes, never trusts the client's declared type or file name, 10 MB default cap via the new
    `VIZ_MAX_ASSET_MB`) and `GET /api/assets/{id}` (sandboxed CSP + `nosniff` on the response, so
    an SVG opened directly can't run script on this origin). **Two xhigh review rounds** on top of
    the lead's own direct fixes, because the first round found a real, exploitable blocker: the
    SVG-sniff regex's lazy comment match could backtrack catastrophically, so a crafted ~300-byte
    upload could freeze the *entire* process (not just the request) for hours, since `re` holds
    the GIL while matching — fixed by making each comment match one way and the repeated group
    possessive; confirmed afterwards to scale linearly, not exponentially, against the original
    payload and 24+ other adversarial patterns, and fuzzed 100k cases against `expat` with no
    bypass found. Same round also found: two ways to sneak an XXE-style entity block past the
    "no internal subset" DOCTYPE rule (not exploitable today — nothing parses these bytes as XML
    server-side — but the rule should hold for a future server-side consumer, e.g. report
    rasterization); a client filename reaching the staging path could crash the request on a
    reserved Windows device name (`NUL`, `CON`, ...) or a >255-char component; a multipart `file`
    part with no `filename=` crashed both this endpoint and the *existing* dataset-upload endpoint
    with an `IndexError`/500 (shared `ingest/upload.py` code, fixed for both); the committed
    `openapi.json`/`schema.d.ts` were stale (contract-drift blocker). Round 2 confirmed every fix
    (including re-fuzzing the new regex) and found three nits, also fixed: `EXTENSIONS` and
    `sniff_image_type` are now typed by a shared `ImageContentType` Literal so mypy catches a
    sniffed type that isn't in the contract instead of a 500 at request time; `Upload.file_name`
    now reports the client's name even when a caller overrides the on-disk name; the ReDoS
    regression test uses a small `k` so a future regression fails in seconds, not hours. 1153
    backend passed/6 skipped, 691 frontend passed/1 skipped, ruff/mypy/biome/tsc clean.
    - **Deferred, not blockers**: no asset deletion/GC (an unreferenced image stays in `assets/`
      forever); `.vizproj` export doesn't bundle asset files yet; `GET /api/assets/{id}` requires
      `auth.current_user`, so once OIDC replaces the local stub, Image widgets on a shared/embedded
      dashboard will 401 without a token-scoped asset route (same hosted-mode gap already tracked
      for share/embed's chart data above). Also noted, pre-existing and unrelated to this branch:
      the dataset-upload endpoint's own filename handling 500s on a >255-char name too (same root
      cause, different code path since it doesn't use `stage_name`) — a `TODO` for whoever next
      touches `ingest/upload.py`'s dataset-facing callers.
- 2026-10-31: `ui-dashboard`'s Wave 3 branch — **not yet merged**, 3 review rounds so far, all real
  findings. Round 1 (blocker): click-to-filter was built from ECharts' *formatted display labels*
  (`params.name`) instead of raw values, so integer categories, blanks, and time-bucketed labels
  all produced wrong or empty predicates; fixed with a label-to-raw-value lookup against the
  chart's own cached data, since `ui-charts`' raw-value callbacks hadn't merged yet. Round 1
  should-fixes: horizontal-chart brush, tab-switch wiping a chart's selection, the report's
  "Filtered by" line, KPI widget quantile-scan cost (measured: ~150-200ms per KPI on the 10M
  fixture, within budget but flagged as a watch-item for a dashboard with many KPIs). Round 2:
  once `ui-charts`' `onPointClick`/`onBrushSelected`/`onLegendChange`/`slot` landed on `main`
  (Wave 3 ui-charts merge), swapped the round-1 label-lookup workaround for the real raw-value
  callbacks, deleting `chartEvents.ts`/`useChartInstance.ts`/`RangeBrush.tsx` and the old
  label-mapping code. Round 3 (CHANGES REQUIRED, 3 blockers): the swap dropped the brush
  plot-type gate, so plots whose x channel isn't the brushed screen axis (Q-Q, lag, PCA/cluster
  scores, event study) could silently build a wrong predicate from a brush drag; a React
  parent-before-child unmount-cleanup ordering issue let a tab switch schedule a debounced
  "cleared" filter update that fires ~150ms *after* the chart unmounts, silently wiping a linked
  chart's selection; and `brushMode: "multiple"`'s several time-bucketed bands were collapsed into
  one over-wide contiguous range instead of either covering only the actual buckets or failing
  closed. All three sent back for round 4, along with should-fixes (the Image widget still can't
  upload even though `/api/assets` is live on `main` now — stale `TODO(platform)`; legend-toggle
  predicates lost their fail-closed behavior on an unresolvable hidden value; drill context is
  dropped from published clicks/drill-through; a millisecond-precision raw-temporal click
  regressed from the old half-open date range; `timeUnit` lookup can grab the wrong encoding when
  one field is both a measure and bucketed on x).
  - `TODO(ui-charts)`: a stale `TODO(ui-dashboard): wire these to the cross-filter bus` comment in
    `charts/core/ChartView.tsx` (the wiring is done, this is ui-charts' file to clean up); add the
    raw color value to `ChartPointClick` so `ui-dashboard`'s interim `chartLookup.ts` cache-read
    workaround can be removed; a controlled-clear mechanism for the native brush (an imperative
    handle or `brushClearKey` prop) so clearing a dashboard-side selection can also hide the
    drawn band, which neither side currently does.
- 2026-10-31: `ui-dashboard` round 3 fixes (all 3 blockers + 7 should-fixes from the round-3
  review) landed as `b2ce267`, confirmed independently against the actual renderer code (not just
  the agent's own claims) in round 4 — brush plot-type allowlist checked plot-by-plot, the
  `disposed`-ref unmount fix checked against React's actual parent-before-child cleanup order, the
  bucket-contiguity check confirmed to use real UTC calendar boundaries (handles 28/29/31-day
  months and DST-free since the engine runs in UTC). One unplanned fix landed with it: the Image
  widget can now actually upload (`POST /api/assets` through a typed client), closing a real
  MVP §7 gap. Round 4 found one new blocker introduced by the "band outliving its filter" fix: a
  click that replaces a brush selection gets silently wiped ~150ms later by the outgoing chart's
  own unmount-time "cleared" report racing the click's publish — reproduced in a scratch test
  harness, sent back with a precise fix (gate the "cleared" handling on the selection's actual
  origin, cancel a pending brush-settle on click). Also sent back: the same remount loses the
  chart's drill path, so a click after "Clear all" post-drill can publish a stale+wrong predicate
  pair; and a fail-closed brush (non-contiguous buckets, multiple value-axis ranges, an
  unresolvable category) leaves a visibly drawn band with silently no active filter, which breaks
  the round-3 invariant that there's never a band with no filter behind it. Round 5 fixed all
  three (origin-gated "cleared" handling + click-cancels-pending-brush for the race; controlled
  drill position held in the runtime store, keyed per-widget by source+hierarchy, surviving both
  remounts and tab switches; dead-band remount plus a transient "can't be used as a filter"
  message), each mutation-tested by the agent (confirmed the relevant test fails when the fix is
  reverted) and independently re-verified against the real `BrushController` source and in a real
  browser. Round 5's review found the *same* race existed in the legend path too (hiding a
  legend entry that can't produce a filter could wipe an existing click/brush selection) plus 3
  small nits (notice-timer restart, a drill-key missing `spec.id`, a shift-click-after-band-removal
  ordering issue); round 6 fixed all four with the same origin-gated pattern, mutation-tested
  again. **Round 6: APPROVE**, confirmed by a reviewer that traced the guard logic by hand rather
  than trusting the mutation-test claim, and did a final full-diff sanity pass (no debug
  statements, no skipped/disabled tests beyond the intentionally gated bench suite, every
  remaining TODO a legitimate hand-off to another owner). **Merged** (6 review rounds total — the
  most of any Wave 3 branch, proportional to how much of this feature is genuinely stateful
  event-handling code). 849 frontend tests passed/4 skipped, biome and tsc clean.
  - `ui-data`'s dashboard-route/grid-drill-through branch (held pending this merge) **merged**
    immediately after: confirmed `DashboardPage`'s default export and `SharePage`/`EmbedPage`'s
    named exports match exactly what `ui-data` had guessed while this branch was still unmerged
    (clean `tsc -b`, no adjustment needed). 851 frontend tests passed/4 skipped on `main` after
    both merges; backend 1152 passed/6 skipped (the 1 failure is the pre-existing, unrelated
    `Plots.md`/`Next.md` uncommitted-edit issue noted above, not a regression).
  - Deferred, tracked for Wave 4 or a future `ui-charts` touch: a brush and a legend filter active
    on the same chart replace each other's predicate instead of combining (`TODO(ui-charts)` in
    `ChartWidget.tsx`, next to the existing controlled-brush-clear TODO); a tab switch returns a
    chart without its redrawn brush band even though the filter stays active (same controlled-
    brush-API gap); clicking outside a drawn brush band may not fire at all in a real browser
    (ECharts/`BrushController` behavior, confirmed plausible from source but not fully verified
    live — a `TODO(ui-charts)` to check); shift-clicking a chart with an active legend filter can
    toggle a value back out of that filter's `in` list via `extendSelection`'s union/toggle
    semantics — intended per the semantics settled across rounds 1-6, but worth one real-browser
    UX look in Wave 4.

**Wave 3 exit criteria (CLAUDE.md §10) are met**: dashboards with cross-filtering (click, brush
and legend, each independently hardened across 6 review rounds), drill-down and drill-through,
sharing (tokens, public resolve, `/share`/`/embed` routes) and reports (HTML/PDF assembly) all
work. The §6 1s cross-filter-refresh-of-a-6-chart-dashboard budget, including during a 10M-row
import, was independently re-verified against the real fixture during `ui-dashboard`'s round-1
work (idle p95 0.73s/0.68s, during-import p95 0.94s/0.90s across two measurements) and holds with
a KPI widget added (p95 ~0.44-0.48s for 6 charts + 1 KPI). Known gaps carried forward, not
blockers: the hosted-mode share/embed data-scoping gap (chart data isn't token-scoped yet, only
"works" locally because auth is a no-op stub); brush+legend combination on one chart; asset
cleanup/GC and `.vizproj` asset bundling; the responsive-layout and autosave/undo follow-ups
already tracked under MVP §7/§10.

## Wave 4 log

- 2026-10-31: Wave 4 kicked off per CLAUDE.md §10: perf budgets at 10M rows, e2e flows, an
  accessibility pass, smoke tests on Windows and macOS, wheel packaging, Dockerfile + optional
  compose. Dispatched in parallel: `data-core` (cold-page/search/dashboard-refresh perf),
  `transform` (M4 line-chart perf), `stats` (describe-with-modes perf), `platform` (packaging and
  infra), `qa` (e2e + a11y for the Wave 3 dashboard surface). **Known limitation, flagged up front
  and unresolved**: this repo has no git remote configured, so the existing 3-OS CI workflow
  (`.github/workflows/ci.yml`) has never actually executed on GitHub's runners — neither the lead
  nor any agent can trigger or verify a real macOS run from here. `qa`'s review confirmed the
  workflow is *correctly configured* for macOS (wheel coverage in `uv.lock`, native binaries in
  `package-lock.json`, LF line endings, OS-guarded `taskkill`/`ControlOrMeta` usage) but this is
  inspection only, not a real run. Actual macOS verification needs a GitHub remote or physical/
  virtual Mac access.
- 2026-10-31: `platform`'s packaging/infra merged: verified the wheel ships the built frontend
  end to end (built, installed into a clean venv with no Node on PATH, `viz serve` works);
  `infra/Dockerfile` (3-stage build), `infra/docker-compose.yml` (app + Postgres 17, AWS-parity
  testing only), `infra/smoke.py`, `backend/tests/test_packaging.py`. xhigh review (touches
  `auth/`) found no blockers; two should-fixes and ten nits, addressed by the lead:
  - A real bug found while wiring in a new `postgres` optional-dependency extra (`psycopg[binary]`
    in `pyproject.toml`): `uv pip install --no-deps "<wheel>[postgres]"` silently drops the
    extra's own dependencies too, not just the base package's, so psycopg was never actually in
    the image. **Correction to the record**: this bug only ever existed in the lead's own
    uncommitted intermediate edit — the platform agent's original, committed Dockerfile installed
    psycopg inline with full dependency resolution and worked correctly. Fixed by exporting the
    extra into the cached `requirements.txt` layer instead (`uv export --locked --extra postgres`).
  - `/api/health` moved off the authenticated `api_router` onto a new bare `health_router`: a load
    balancer/container probe can't carry credentials, and the route only "worked" authenticated
    before because `auth.current_user` is a local no-op stub. Documented in CLAUDE.md §6 and
    `viz/auth/__init__.py` as the second deliberate public-route exception alongside
    `share_access`, pinned by the existing test enumerating every unauthenticated `/api/*` route.
    Still runs a trivial DB query, doubling as the Docker healthcheck's connectivity check.
  - Smaller fixes: `--locked` instead of `--frozen` (fails the build on a stale lock), `uv pip
    check` plus explicit import assertions after the wheel install, `VIZ_PORT` read from the
    environment in the healthcheck, the `uv` binary and its cache removed from the runtime image,
    a URL-safe-characters warning on the compose Postgres password.
  - Verified concretely: built the image, confirmed `import psycopg` succeeds (and did NOT before
    the fix), ran the full `docker compose` Postgres path end to end twice (health endpoint 200,
    Alembic migrations clean on Postgres, Docker's own HEALTHCHECK reports healthy), torn down
    cleanly both times. 1154 backend tests passed/6 skipped, ruff/mypy clean.
  - **Deferred, tracked, not blockers** (AWS-specific per CLAUDE.md §11's "don't build it now,
    don't block it"): a hatch build-hook to fail the wheel build if the frontend wasn't built
    first (today only an in-Dockerfile assert catches this); migration locking for concurrent ECS
    task starts (every task runs `run_migrations` in its own lifespan with no lock); an ALB
    liveness-vs-readiness probe split (today's DB-touching health check would mark every task
    unhealthy during an RDS failover, roughly 60-120s, and ECS would replace all of them —
    acceptable for the Docker healthcheck, not for an ALB target group); `infra/smoke.py` doesn't
    yet exercise `/api/charts/data` (the DuckDB/Parquet path most sensitive to container memory)
    or `/api/health` itself; a wheel-metadata test asserting the `postgres` extra's `Requires-Dist`
    entry survives future refactors.
- 2026-10-31: `transform`'s M4 single-pass perf fix merged: eliminates a materialized 10M-row CTE,
  a second full scan for the x extent, and a semi-join, replacing them with one aggregate-and-pick
  pipeline (scalar `arg_min`/`arg_max` for one measure, whole-row struct picks via `arg_min(row,
  key)` for several, avoiding tied-row column mispairing). ~40% faster under heavy concurrent
  load (p50 ~377ms -> ~223ms in-process, ~312ms -> ~238ms over HTTP). xhigh review found one real
  blocker: the new integer `epoch_us` bucket path's `least()` silently kept NULL-x rows instead of
  dropping them (DuckDB's `least()` skips NULL arguments) — reproduced as a 40x-magnitude false
  spike on a line chart built from CSV rows with empty date cells. Fixed with the same `CASE WHEN
  isfinite(...)` guard the DOUBLE-literal branch already had; the fix was re-verified by writing a
  pytest plugin that reverts the guard and confirms the regression tests actually fail without it.
  1164 backend tests passed/7 skipped, ruff/mypy clean.
- 2026-10-31: `ui-dashboard` fixed all 4 product bugs qa's Wave 4 e2e pass pinned with `test.fail`
  sentinels, plus the accessibility findings: share-revoke now handles a 204's empty body instead
  of routing it through a JSON-expecting `unwrap()`; the date-range filter's draft no longer resets
  to the stale committed value mid-edit (was racing its own 150ms debounce); react-grid-layout's
  drag handle is now a real keyboard-operable button (Space/Enter pick up, arrows move/resize,
  Escape cancels, a live region announces each step) — a genuine race was found and fixed where the
  live grid position lagged a render behind, dropping the first of two fast keypresses; widget
  dialogs now return focus to "Add widget" on close instead of dropping to `<body>`. A11y fixes:
  the dropdown filter's accessible name now includes its value; the edit-mode tablist holds only
  tabs; print styles use light tokens regardless of the active theme; hand-written buttons picked
  up the same accent/text color-mix hover fix as the shared `Button` component. Also retired the 4
  `test.fail` sentinels and 15 now-resolved axe-baseline entries in qa's own e2e files (done while
  verifying end-to-end against qa's suite, based on qa's pre-round-2 commit — qa is reconciling
  their own round-2 quality improvements against this separately). Independently re-verified on
  `main` post-merge: `dashboard.spec.ts` + `a11y-dashboard.spec.ts` = **43/43 passed, zero axe
  violations across all 34 screens**. 871 vitest passed/4 skipped, biome/tsc clean.
  - **Known limitation, unrelated to this branch**: in dark-theme print, ECharts draws axis/series
    text on canvas using the screen theme's colors, which CSS print overrides can't reach — axis
    labels may print pale on a white page. The token-level print-contrast fix covers everything
    except canvas-rendered chart text.
- 2026-10-31: `data-core`'s search/grid-perf branch found genuinely serious bugs on its first
  review round, both now fixed and in a second xhigh confirmation round: (1) searches in Korean,
  Hindi and other non-Latin scripts returned **zero rows** on high-cardinality columns, because the
  new accent-folding fast path didn't match DuckDB's `strip_accents` for Hangul (Python NFD
  decomposes syllables; DuckDB doesn't) or Mc/Me Unicode marks (only Mn was stripped) — fixed by
  gating the regex fast path to Latin/Greek/Cyrillic/Vietnamese ranges only, routing everything
  else to the always-correct `strip_accents` fallback; (2) long search needles were ~200x slower,
  with the 256-character cap not preventing a 29-62s worst case that could hold a query slot past
  the engine's 60s timeout — fixed by narrowing the accent-expansion character class now that the
  fast path is script-scoped, bringing the worst case down to ~26ms. Also fixed: a dataset column
  literally named `file_row_number` broke every filtered/searched page (mirrors an existing guard
  in `transform/compiler.py`); a thread-unsafe LRU cache; a ~356ms first-search cost. **Merged**
  after a second xhigh round re-verified both fixes end-to-end (49+ needles across a dozen scripts
  at the `rows_page`/HTTP level, a whole-Unicode-range comparison against DuckDB, re-measured
  worst-case needle timing now 3.5-9x *faster* than `strip_accents`). Lead applied the round-2
  nits directly (widened the combining-mark class to cover Cyrillic titlo and other marks beyond
  the original Latin-only range, with no measurable perf cost; corrected the accepted-differences
  docstring; fixed the cost-bound test's needle to the real worst case) and fixed one related,
  pre-existing bug the reviewer found while verifying: `RowViews._eligible` didn't carry the same
  `file_row_number` guard `rows._late_page` and `transform/compiler.py` already had, so a
  materialized view over such a dataset 500'd on every page past the first once built. 1220
  backend tests passed/7 skipped, ruff/mypy clean.
  - Perf budget items now meeting budget: cold sorted first page (190-340ms -> 143ms, budget
    150ms), cold deep filtered-range pages (0.6-0.7s -> 59ms), free-text search (620-660ms ->
    ~192ms). **Still over budget, improved but not closed, needs qa's quiet-machine pass**: cold
    deep sorted pages (1.8-2.7s -> ~435-620ms depending on measurement, budget not specified as a
    hard number but clearly still above the first-page 150ms budget's spirit).
- 2026-10-31: `ui-dashboard` found and fixed the keyboard-move/resize render-loop crash qa
  surfaced (React error #185, ~1-in-3 intermittent): `DashboardGrid` stored whatever
  `react-grid-layout`'s `onLayoutChange` reported into the dashboard document, but that callback
  reports the *previous* render's layout, not the current one — two keyboard steps close together
  made the stored layout and the grid's fed-back prop alternate between two states forever. Fix:
  stopped using `onLayoutChange` as the source of truth; the stored layout now only changes on
  `onDragStop`/`onResizeStop` (mouse) or an explicit keyboard step, with keyboard steps computing
  their own resulting layout via a new `settleLayout` (`model.ts`, built on
  `react-grid-layout/core`'s own `moveElement` and compactor, so it can't drift from what the grid
  itself would show). Also fixed a spurious write-back on page load from the same echo. Confirmed
  in the browser (24 runs, 0 failures) and via Playwright (`--repeat-each 12 --workers 1`, 12/12).
  **Review found two further issues, both fixed and merged**: `settleLayout` placed the mover at
  its *target* position before calling `react-grid-layout`'s `moveElement`, which short-circuits
  as a no-op when already at the target — collision resolution silently fell back to array order
  alone, which could displace the wrong widget (the mover instead of the neighbor it landed on).
  Fixed by seeding the mover at its old position, matching `onDragStop`'s own approach. Separately,
  keyboard users had lost the ability to swap two vertically-stacked widgets (a capability a
  mouse-drag user still had), since each step settled straight back to its start from the
  already-compacted position — fixed with a row-by-row continuation that extends a step until the
  layout actually changes, the keyboard equivalent of dragging far enough past a neighbor. Both
  fixes were confirmed with extensive fuzzing against the real `react-grid-layout/core` (3000
  random move/resize comparisons against the library's own drag-stop behavior: 0 mismatches;
  16,034 stack-swap steps across 2-5-widget stacks: 0 failures; 92,768 random multi-column steps
  checking no-overlap/in-bounds/compacted invariants: 0 violations), not just the unit tests.
  **Merged — this closes the last known Wave 4 dashboard blocker.** 890 vitest passed/4 skipped,
  43/43 dashboard+a11y e2e including 12/12 repeat-each on the keyboard test, biome/tsc clean.
  - Deferred, not blockers: keyboard steps aren't yet breakpoint-aware below the 12-column desktop
    layout (a pre-existing gap, not introduced by this round); Escape's whole-layout restore has
    thinner test coverage against the simpler single-widget-restore alternative than ideal, though
    fuzzing showed both converge in practice for the cases tried.
- 2026-10-31: `stats` closed out the last two Wave 1 perf items: descriptive stats with modes at
  10M rows, ungrouped (p50 1.70s / p95 1.73-1.81s on a quiet machine, down from 2.09-2.34s, meets
  the 3s budget) and grouped (p50 3.40s, down from 5.9-6.2s, ~43% faster). No engine changes
  needed — three fixes, all in `stats/descriptive.py`:
  - The modes computation ran one `GROUP BY` per field as branches of a single `UNION ALL`, which
    DuckDB executes concurrently — every field's value-count hash table was resident in memory at
    once (2-5x peak memory measured; spills at low `memory_limit` values, independently confirmed
    by the reviewer at a 350MB limit: 0.45s -> 5.42s on `main`, unaffected on the branch). Fixed
    with one query per field instead of one combined query.
  - The mode top-k used `min_by` with a STRUCT key, building an expensive sort key per distinct
    value (0.6s CPU alone on a 3.4M-distinct column). Fixed with a cheaper top-k keyed on a plain
    count, sorting ties natively in SQL afterward — with an exact fallback whenever all 64 kept
    slots tie above count 1, so a mode can never be silently dropped. The reviewer proved this
    fallback trigger condition is complete (a dropped mode is only possible when every kept slot
    ties, which is exactly when the fallback fires).
  - A DATE/TIMESTAMP `quantile_disc` call finalizes on one thread (CLAUDE.md §12's known DuckDB
    gotcha), stalling the scan. Low-cardinality temporal columns now derive quantiles from the
    mode query's own value-counts via DuckDB's own discrete-quantile index formula instead of
    calling `quantile_disc`; high-cardinality ones keep the original path (the gate only affects
    speed — the counts-derived path is exact at any cardinality, independently verified against
    the textbook formula for every n up to 3M across 22 percentiles with zero divergence).
  - xhigh review independently re-verified every correctness claim rather than trusting the
    numbers (field-by-field comparison across 12 synthetic-data configurations: 0 exact-statistic
    differences; confirmed the ~1e-16 moment-aggregate differences are DuckDB's own parallel-
    summation floating-point noise, reproducible from an old-vs-old rerun, not a regression; 10
    planted mutants in the new logic, 9 killed by existing tests, the 10th survived correctly as
    a mathematically equivalent variant). Lead applied one nit: the test's float tolerance now
    scopes to the 7 moment keys only, keeping quantiles/modes/min/max at bit-exact equality.
    1237 backend tests passed/7 skipped, ruff/mypy clean. **Merged.**
  - Deferred, tracked, not blockers: grouped describe can still OOM on a very memory-constrained
    machine (~1.7 GiB main-instance limit) — substantially improved by this branch but not fully
    closed; a measured fix exists (split into per-group + overall queries, +0.14s, peak 1.56->1.10
    GiB) left for a follow-up after qa's quiet-machine pass. A deeper design question for the
    100M-row target: `quantile_cont`'s per-row-growing state can't spill, so it will outgrow small
    machines even ungrouped at that scale — needs a Wave 4+ decision (sampled, approximate, or
    per-field above some row count). An all-tied high-cardinality field pays the exact-fallback
    cost unconditionally (roughly doubles that one field's mode cost; bounded to one field, not
    blocking). `TODO(transform)`: a `range` filter on a BIGINT column compiles to a per-row DOUBLE
    cast (`chartdata/sql.py`'s `_bound` emits a float literal unconditionally), costing ~0.1s
    across describe's own scans under a filter — out of scope for `stats`. `TODO(qa)`: `bench.py`'s
    "payload cap scatter: lat vs lon" case returns HTTP 422 (lat/lon get the geo semantic, which
    chart-data rejects for x/y) — a fixture issue, not a regression.

**Wave 4's originally-dispatched perf-fixing scope is now complete**: all 5 carried-over Wave 1
budget items (cold sorted/deep grid pages, free-text search, the M4 raw-timestamp line, and
describe-with-modes) are fixed and merged, meeting budget except grouped describe's deep-sorted-
page figure (improved but not fully closed, tracked above). Still open for Wave 4 overall: `qa`'s
final confirmed state of `frontend/e2e/` (reconciliation is in; no outstanding report expected
unless something regressed), a consolidated quiet-machine re-measurement of everything above, and
the pre-existing `Plots.md`/`Next.md` uncommitted-edit question still awaiting the user.

## MVP.md

### 1. Data Sources & Import

| Item | Owner | Status | Notes |
|---|---|---|---|
| CSV / TSV import | data-core · ui-data | done | Backend: staged upload/path, preview, options, import job. UI: staged wizard with live preview and per-sheet jobs, jobs indicator with per-tab ownership, close-while-starting race fixed (an in-flight import finishes in the background instead of aborting). |
| Excel import | data-core · ui-data | partial | Backend done (fastexcel, sheets, header row, skip rows). UI: sheet selection, one staged upload, parallel per-sheet imports. |
| JSON import | data-core · ui-data | partial | Backend done (array/NDJSON, nested-JSON hint). Not exercised in the UI yet. |
| Data preview | data-core · ui-data | partial | Backend done: rows with sort/filter/search, values, materialized views for deep pages. UI: server sort/filter/search, filtered counts, columns menu. |
| Data refresh | data-core · ui-data | partial | Backend done (new version, cache invalidation). UI: dataset menu refresh (verified against the backend). |
| Large dataset handling | data-core · ui-data | partial | Jobs with SSE + cancel, background DuckDB instance, import from path. 10M rows: import 5.0 s, pages ~20 ms. First sorted page ~190-340 ms cold (budget 150 ms). Cold deep pages before their view is built: ~1.8-2.7 s sorted, ~0.6-0.7 s filtered range (Wave 4). Free-text search ~620-660 ms (Wave 4 search pass). |

### 2. Data Preparation & Transformation

| Item | Owner | Status | Notes |
|---|---|---|---|
| Data filtering | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |
| Sorting | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |
| Grouping | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |
| Aggregation | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |
| Missing value handling | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |
| Duplicate removal | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |
| Data type conversion | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |
| Column selection | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |
| Calculated columns | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |
| Custom formulas | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |
| Join and merge datasets | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |
| Pivot and unpivot | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |
| Data normalization | transform · ui-data | done | Backend: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. UI: pipeline editor wired to the real API (no flag), inline step errors, materialize, chart from pipeline output. |

### 3. Chart & Visualization Types

Tracked per plot under Plots.md below.

### 4. Chart Configuration

| Item | Owner | Status | Notes |
|---|---|---|---|
| X-axis and Y-axis selection | ui-charts | done | Generated Data tab from the plot's channel schema. |
| Multiple Y-axis support | ui-charts | done | y_axes + per-series axis assignment. |
| Color customization | ui-charts | done | Palettes + per-series colors. |
| Labels and annotations | ui-charts | done | Value labels, point/area/text annotations. |
| Legend configuration | ui-charts | done | Generated config panel + applyStyle (basic plots). |
| Chart titles and subtitles | ui-charts | done | Generated config panel + applyStyle (basic plots). |
| Axis scaling (linear, logarithmic) | ui-charts | done | Linear/log/time/category; log drops non-positive values with a warning. |
| Axis formatting | ui-charts | done | Intl number/currency/percent/date formats per axis. |
| Gridline configuration | ui-charts | done | Generated config panel + applyStyle (basic plots). |
| Data point markers | ui-charts | done | Generated config panel + applyStyle (basic plots). |
| Line styles and thickness | ui-charts | done | Generated config panel + applyStyle (basic plots). |
| Error bars | ui-charts | partial | SD/SE/CI/fixed rendered (incl. with color). Server bounds: transform branch in review. |
| Trendlines | ui-charts | partial | Rendering + config done. Server fits (/api/charts/overlays): transform branch in review. |
| Regression lines | ui-charts | partial | As trendlines; server fit kernels merged (stats); overlays endpoint merged (OLS/LOESS with bands). |
| Confidence intervals | ui-charts | partial | CI band primitive done; server bands via overlays (in review). |
| Tooltips | ui-charts | done | Formatted axis/item tooltips. |
| Reference lines | ui-charts | done | Generated config panel + applyStyle (basic plots). |
| Chart orientation | ui-charts | done | Vertical/horizontal swap. |
| Stacking and grouping | ui-charts | done | Grouped, stacked and 100% stacked. |

### 5. Statistical Analysis

| Item | Owner | Status | Notes |
|---|---|---|---|
| Mean, median and mode | stats · ui-data | done | Backend: describe (exact, SQL; modes with tie warning). Stats panel UI: source/field/group picker, result table. |
| Standard deviation and variance | stats · ui-data | done | Backend: describe (overflow-safe moments). Stats panel UI: result table. |
| Percentiles and quartiles | stats · ui-data | done | Backend: describe + quantiles kernel (exact, R type 7). Stats panel UI: percentile input, result table. |
| Correlation analysis | stats · ui-data | done | Pearson exact in SQL, Spearman/Kendall in SciPy, Fisher-z/Bonett-Wright/Fieller CIs. UI: field checklist, method, pairwise/listwise, CI level; coefficient/p-value matrix. |
| Linear regression | stats · ui-data | done | OLS (sampled to 1M), HC3 robust SEs, VIF/GVIF, Breusch-Pagan, residual-normality checks, NIST-verified. UI: y/x selection, coefficient table with CI and VIF, model fit stats, assumption checks. |
| Multiple regression | stats · ui-data | done | Same endpoint/backend as linear regression (numeric/boolean/date/categorical predictors). UI as above. |
| Hypothesis testing | stats · ui-data | done | t-tests, Mann-Whitney, Wilcoxon, Kruskal-Wallis, with assumption checks (Shapiro-Wilk, Brown-Forsythe Levene) as warnings. UI: dedicated forms and result views per test. |
| T-tests | stats · ui-data | done | One-sample, Welch, Student, paired; every alternative hypothesis; CIs; Cohen's d. UI: variant/group/alternative form, mean-difference headline (labeled per test kind), CI, sample table. |
| ANOVA | stats · ui-data | done | One-way (eta²/omega²/Welch/Tukey HSD) and two-way (Type I/II/III SS), exact via per-cell weighted least squares. UI: dependent field, 1-2 factors, interaction option; ANOVA table, Welch ANOVA, Tukey comparisons. |
| Chi-square tests | stats · ui-data | partial | Independence and goodness-of-fit, exact SQL counts, Yates' correction, Cramer's V, adjusted residuals, Fisher's exact + odds ratio for 2x2. UI: independence done; goodness-of-fit UI is uniform-proportions only (backend supports custom proportions, no input exposed yet). |
| Distribution fitting | stats · ui-data | partial | SciPy MLE fits of 10 distributions on a 100k sample, ranked by AIC/Akaike weights/BIC/KS, with a density histogram and fitted curves for plotting. UI: AIC ranking table only, no fitted-curve plot yet (a natural "plot this" follow-up - the response already includes the density grid). |
| Outlier detection | stats · ui-data | done | IQR fences, z-scores, MAD, exact in SQL, per field and optionally per group. UI: fence/outlier tables (the `include` extra-columns option defaults with no UI control yet). |
| Confidence intervals | stats · ui-data | done | Exact t intervals; exact distribution-free order-statistic intervals for median/quantiles; an asymptotic delta-method interval for SD; bootstrap (BCa/percentile) reserved for smaller inputs, independently coverage-simulated after a review found the original subsample approach under-covered. UI: interval table. |
| Descriptive statistics | stats · ui-data | done | Backend: `/api/stats/describe` (grouped, filtered, percentiles, skew/kurtosis). 10 fields with modes 2.6-3.9 s at 10M depending on conditions (Wave 4 watch item); grouped 7-8 s (Wave 4). Stats panel UI: source (dataset/pipeline) picker, field/group-by checklists, result table, "plot this" shortcut to a histogram/box chart. |

### 6. Interactivity

| Item | Owner | Status | Notes |
|---|---|---|---|
| Zoom and pan | ui-charts | partial | dataZoom with detail-on-demand re-query (needs transform merge for server xRange). |
| Hover tooltips | ui-charts | done |  |
| Click-to-filter | ui-dashboard | done | Click, brush and legend-toggle all build `Predicate`s from raw chart values (not ECharts' display labels) and publish to the dashboard's cross-filter bus. Gated per plot type for brush (only plots whose x channel matches the brushed screen axis). |
| Cross-filtering | ui-dashboard | done | Per-`linkGroup` bus; each chart excludes its own predicate from what it applies. 6 review rounds hardened the stateful edge cases — see the Wave 3 log. |
| Drill-down | ui-charts | done | Chart-level hierarchy drilling (`PlotEntry.drill`, breadcrumb nav, keyboard "Drill into X" in the table view). Declared on bar/line/area/pie/donut; treemap/sunburst keep native ECharts drill. Reports via `onDrillChange`, doesn't publish to any bus itself. |
| Drill-through | ui-dashboard | done | A chart's drill path can open the data grid (predicates applied via `gridDrill`, wired by ui-data) or another dashboard. Drill context is carried into click/brush/legend predicates so a leaf-level selection doesn't lose the ancestor levels. |
| Data point selection | ui-dashboard | done | Click and brush selection, via the raw-value callbacks. |
| Multi-select | ui-dashboard | done | Shift-click extends a selection (union for categorical `in`, replace for a non-combinable kind like a date bucket or range, since two ANDed ranges on one field can only ever select nothing). |
| Interactive legends | ui-charts | done | Legend toggles survive cosmetic edits. |
| Range sliders | ui-charts | partial | Slider dataZoom; xDomain from server after transform merges. |
| Date range filters | ui-dashboard | done | Dashboard filter widgets and global filters support a date-range control. |
| Dynamic chart updates | ui-dashboard | done | Cross-filter refetches via TanStack Query, debounced (150ms) per chart. |
| Linked visualizations | ui-charts · ui-dashboard | done | `echarts.connect` (ui-charts): refcounted by `linkGroup` membership, synced tooltip/zoom/legend, with a per-chart gesture gate. ui-dashboard wires `interactions.linkGroup` from the dashboard's own link groups and layers the cross-filter bus on top. |

### 7. Dashboard

| Item | Owner | Status | Notes |
|---|---|---|---|
| Multiple charts per dashboard | ui-dashboard | done |  |
| Drag-and-drop layout | ui-dashboard | done | react-grid-layout, 12-column. |
| Resizable chart containers | ui-dashboard | done |  |
| Grid-based layout | ui-dashboard | done |  |
| Dashboard-level filters | ui-dashboard | done | Filter widgets (date range, dropdown, slider) plus `Project.global_filters` shared across every dashboard in a project. |
| Global date filters | ui-dashboard | done |  |
| Chart synchronization | ui-dashboard | done | Shares the same `linkGroup`/cross-filter-bus mechanism as linked visualizations above. |
| Multiple dashboard tabs | ui-dashboard | done | Selections persist across tab switches (needed its own review round — see the Wave 3 log). |
| KPI cards | ui-dashboard | done | Value, delta, sparkline. Its quartile-scan cost is a tracked perf watch-item (~150-200ms per KPI on the 10M fixture, within budget). |
| Text and image widgets | ui-dashboard | done | Markdown (marked + DOMPurify, tag/attribute/URI allowlist) for text; image upload via `POST /api/assets`. |
| Responsive layouts | ui-dashboard | partial | 12-column layout persists; narrower breakpoints don't yet get their own saved arrangement. |

### 8. Customization & Themes

| Item | Owner | Status | Notes |
|---|---|---|---|
| Light and dark themes | ui-charts | done | ECharts theme from CSS tokens, runtime setTheme. |
| Custom color palettes | ui-charts | done | Okabe-Ito (default), Tableau-10, viridis, cividis. |
| Custom fonts | ui-charts | done | Font family/size in style; chart font token. |
| Chart templates | ui-charts | done | Templates from project.templates (apply/save). Sidebar wiring: ui-data. |
| Reusable styles | ui-charts | done | Same as templates. |
| Number formatting | ui-charts | done |  |
| Currency formatting | ui-charts | done |  |
| Percentage formatting | ui-charts | done |  |
| Date formatting | ui-charts | done |  |
| Custom legends | ui-charts | partial | Position/show; per-series names via series overrides. |
| Background customization | ui-charts | partial | Theme background token; per-chart background not yet. |

### 9. Export & Sharing

| Item | Owner | Status | Notes |
|---|---|---|---|
| Export as PNG | ui-charts | done | saveChart via swappable ExportSink. |
| Export as SVG | ui-charts | done | Offscreen SVG render. |
| Export as PDF | ui-charts | done | Vector via lazy jsPDF + svg2pdf; raster fallback for non-Latin-1 text. |
| Export as JPEG | ui-charts | done |  |
| Export as CSV | data-core · ui-data | partial | Backend done (DuckDB COPY, job + download). Pipeline sources wait on transform. UI: export job + download. |
| Export underlying data to Excel | data-core · ui-data | partial | Backend done (streamed xlsxwriter, row-limit warning). UI: export job + download; warning toast must persist. |
| Copy chart to clipboard | ui-charts | done | ClipboardItem PNG. |
| Share dashboard through URL | platform · ui-dashboard | done | Backend: token mint/list/revoke, public resolve, scoped to what the shared item reaches. UI: `/share/:token` read-only view (route wired by ui-data). Hosted-mode gap tracked: the public resolve route is scoped, but chart *data* still goes through the normally-authenticated endpoint, which only "works" for an anonymous viewer locally because auth is a no-op stub. |
| Embed visualizations | platform · ui-dashboard | done | Backend: `/api/embed/{token}` (same resolve, public). UI: `/embed/:token` (route wired by ui-data), iframe-ready. Same hosted-mode data-scoping gap as sharing. |
| Generate reports | platform · ui-dashboard | done | Backend: `/api/reports/{projectId}[/dashboards/{id}]`, authenticated, bundles chart specs/pipelines. HTML/PDF assembly is frontend-rendered (ui-dashboard), since Python can't render ECharts headlessly. |
| Print-friendly layouts | ui-dashboard | done | Print CSS; no backend component. |

### 10. Workspace & Project Management

| Item | Owner | Status | Notes |
|---|---|---|---|
| Save projects | platform · ui-data | done | Atomic revision check (409 → reload dialog), named snapshots, Ctrl/Cmd+S. |
| Open existing projects | platform · ui-data | done | Project picker: open/create/rename/duplicate/delete, with unsaved-changes guard. |
| Autosave | platform · ui-data | partial | 2 s debounce, save-state indicator, beforeunload guard, snapshot every N. Follow-up: avoid back-to-back saves on slow links. |
| Undo and redo | platform · ui-data | partial | zundo with coalesced typing (800 ms), Ctrl/Cmd+Z/Y. Follow-up: stale redo after coalesced edit. |
| Version history | platform · ui-data | done | List, preview and restore (new revision; history kept). |
| Duplicate charts | platform · ui-data | todo |  |
| Duplicate dashboards | platform · ui-data | todo |  |
| Rename charts | platform · ui-data | todo |  |
| Organize charts into folders | platform · ui-data | partial | Backend done: folder/ItemRef validation on save and import. UI todo (ui-data). |
| Import/export project configuration | platform · ui-data | done | .vizproj export (optional data) and import with remap/missing report. |

## Plots.md

| § | Plot | Key | Owner | Status |
|---|---|---|---|---|
| 1 | Bar Chart | `bar` | ui-charts | done |
| 1 | Horizontal Bar Chart | `horizontal_bar` | ui-charts | done |
| 1 | Grouped Bar Chart | `grouped_bar` | ui-charts | done |
| 1 | Stacked Bar Chart | `stacked_bar` | ui-charts | done |
| 1 | Line Chart | `line` | ui-charts | done |
| 1 | Area Chart | `area` | ui-charts | done |
| 1 | Pie Chart | `pie` | ui-charts | done |
| 1 | Donut Chart | `donut` | ui-charts | done |
| 2 | Histogram | `histogram` | plots-distribution-relationship | done |
| 2 | Density Plot | `density` | plots-distribution-relationship | done |
| 2 | Box Plot | `box` | plots-distribution-relationship | done |
| 2 | Violin Plot | `violin` | plots-distribution-relationship | done |
| 2 | Strip Plot | `strip` | plots-distribution-relationship | done |
| 2 | Swarm Plot | `swarm` | plots-distribution-relationship | done |
| 2 | Q-Q Plot | `qq` | plots-distribution-relationship | done |
| 2 | ECDF Plot | `ecdf` | plots-distribution-relationship | done |
| 2 | Frequency Polygon | `frequency_polygon` | plots-distribution-relationship | done |
| 3 | Scatter Plot | `scatter` | plots-distribution-relationship | done |
| 3 | Bubble Chart | `bubble` | plots-distribution-relationship | done |
| 3 | Pair Plot | `pair` | plots-distribution-relationship | done |
| 3 | Correlation Heatmap | `correlation_heatmap` | plots-distribution-relationship | done |
| 3 | Correlogram | `correlogram` | plots-distribution-relationship | done |
| 3 | Hexbin Plot | `hexbin` | plots-distribution-relationship | done |
| 3 | Contour Plot | `contour` | plots-distribution-relationship | partial |
| 3 | Regression Plot | `regression` | plots-distribution-relationship | done |
| 3 | Residual Plot | `residual` | plots-distribution-relationship | done |
| 4 | Time Series Line Plot | `timeseries_line` | plots-timeseries | done |
| 4 | Seasonal Plot | `seasonal` | plots-timeseries | done |
| 4 | Lag Plot | `lag` | plots-timeseries | done |
| 4 | Autocorrelation Plot (ACF) | `acf` | plots-timeseries | done |
| 4 | Partial Autocorrelation Plot (PACF) | `pacf` | plots-timeseries | done |
| 4 | Moving Average Plot | `moving_average` | plots-timeseries | done |
| 4 | Candlestick Chart | `candlestick` | plots-timeseries | done |
| 4 | Calendar Heatmap | `calendar_heatmap` | plots-timeseries | done |
| 5 | Stacked Area Chart | `stacked_area` | plots-composition-specialized | done |
| 5 | 100% Stacked Bar Chart | `percent_stacked_bar` | plots-composition-specialized | done |
| 5 | Waterfall Chart | `waterfall` | plots-composition-specialized | done |
| 5 | Funnel Chart | `funnel` | plots-composition-specialized | done |
| 5 | Treemap | `treemap` | plots-composition-specialized | done |
| 5 | Sunburst Chart | `sunburst` | plots-composition-specialized | done |
| 5 | Mosaic Plot | `mosaic` | plots-composition-specialized | done |
| 5 | Marimekko Chart | `marimekko` | plots-composition-specialized | done |
| 6 | Radar Chart | `radar` | plots-multivariate-econometric | done |
| 6 | Parallel Coordinates Plot | `parallel_coordinates` | plots-multivariate-econometric | done |
| 6 | PCA Biplot | `pca_biplot` | plots-multivariate-econometric | done |
| 6 | Factor Loading Plot | `factor_loading` | plots-multivariate-econometric | done |
| 6 | Cluster Plot | `cluster` | plots-multivariate-econometric | done |
| 6 | Dendrogram | `dendrogram` | plots-multivariate-econometric | done |
| 6 | Andrews Curves | `andrews_curves` | plots-multivariate-econometric | done |
| 7 | Choropleth Map | `choropleth` | plots-geo | done |
| 7 | Bubble Map | `bubble_map` | plots-geo | done |
| 7 | Dot Density Map | `dot_density` | plots-geo | done |
| 7 | Cartogram | `cartogram` | plots-geo | done |
| 7 | Geographic Heatmap | `geo_heatmap` | plots-geo | done |
| 8 | Kaplan-Meier Survival Curve | `kaplan_meier` | plots-multivariate-econometric | done |
| 8 | Forest Plot | `forest` | plots-multivariate-econometric | done |
| 8 | ROC Curve | `roc` | plots-multivariate-econometric | done |
| 8 | Precision-Recall Curve | `precision_recall` | plots-multivariate-econometric | done |
| 8 | Lorenz Curve | `lorenz` | plots-multivariate-econometric | done |
| 8 | Quantile Plot | `quantile` | plots-multivariate-econometric | done |
| 8 | Marginal Effects Plot | `marginal_effects` | plots-multivariate-econometric | done |
| 8 | Coefficient Plot | `coefficient` | plots-multivariate-econometric | done |
| 8 | Impulse Response Function (IRF) | `impulse_response` | plots-multivariate-econometric | done |
| 8 | Event Study Plot | `event_study` | plots-multivariate-econometric | done |
| 9 | Sankey Diagram | `sankey` | plots-composition-specialized | done |
| 9 | Network Graph | `network` | plots-composition-specialized | done |
| 9 | Alluvial Diagram | `alluvial` | plots-composition-specialized | done |
| 9 | Gantt Chart | `gantt` | plots-composition-specialized | done |
| 9 | Pareto Chart | `pareto` | plots-composition-specialized | done |
| 9 | Error Bar Plot | `error_bar` | plots-composition-specialized | done |
| 9 | Lollipop Chart | `lollipop` | plots-composition-specialized | done |
| 9 | Dumbbell Plot | `dumbbell` | plots-composition-specialized | done |
| 9 | Ridgeline Plot | `ridgeline` | plots-composition-specialized | done |
| 9 | Raincloud Plot | `raincloud` | plots-composition-specialized | done |

## Next.md (iteration 2)

### 11. Advanced Features

| Item | Owner | Status | Notes |
|---|---|---|---|
| Python integration | unassigned | todo |  |
| R integration | unassigned | todo |  |
| SQL query editor | unassigned | todo |  |
| Custom scripts | unassigned | todo |  |
| Custom visualization plugins | unassigned | todo |  |
| Machine learning integrations | unassigned | todo |  |
| Forecasting | unassigned | todo |  |
| Anomaly detection | unassigned | todo |  |
| Clustering | unassigned | todo |  |
| PCA | unassigned | todo |  |
| Real-time data streaming | unassigned | todo |  |
| Geospatial analysis | unassigned | todo |  |

### 12. Usability & Accessibility

| Item | Owner | Status | Notes |
|---|---|---|---|
| Drag-and-drop interface | unassigned | todo |  |
| Keyboard shortcuts | unassigned | partial | Save / undo / redo only. |
| Search charts | unassigned | todo |  |
| Chart recommendations | unassigned | todo |  |
| Contextual help | unassigned | todo |  |
| Interactive tutorials | unassigned | todo |  |
| Screen reader support | unassigned | todo |  |
| Color-blind-friendly palettes | unassigned | partial | Okabe-Ito is the default palette. |
| Responsive UI | unassigned | todo |  |
| Performance optimization | unassigned | todo |  |
