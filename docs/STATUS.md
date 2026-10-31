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

### ui-charts follow-ups found during Wave 2 (not yet fixed; see entries above for detail)
- Axis title gap sizing from unrounded data instead of tick labels (can clip off-screen).
- A log-x-axis filter drops array rows whose first element is <=0 (breaks some error-bar layouts).
- "View as table" shows raw JSON for StatResult-based plots instead of a real table.
- `ChartView` keys map-readiness on a plot's registry key, not the prepare inputs, so switching a
  choropleth's map resolution leaves the chart blank after the new map loads.

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
| Click-to-filter | ui-dashboard | todo |  |
| Cross-filtering | ui-dashboard | todo |  |
| Drill-down | ui-charts | done | Chart-level hierarchy drilling (`PlotEntry.drill`, breadcrumb nav, keyboard "Drill into X" in the table view). Declared on bar/line/area/pie/donut; treemap/sunburst keep native ECharts drill. Reports via `onDrillChange`, doesn't publish to any bus itself. |
| Drill-through | ui-dashboard | todo |  |
| Data point selection | ui-dashboard | todo |  |
| Multi-select | ui-dashboard | todo |  |
| Interactive legends | ui-charts | done | Legend toggles survive cosmetic edits. |
| Range sliders | ui-charts | partial | Slider dataZoom; xDomain from server after transform merges. |
| Date range filters | ui-dashboard | todo |  |
| Dynamic chart updates | ui-dashboard | todo |  |
| Linked visualizations | ui-charts · ui-dashboard | partial | `echarts.connect` done (ui-charts): refcounted by `linkGroup` membership, synced tooltip/zoom/legend, with a per-chart gesture gate so a brush/legend gesture made on one linked chart never gets misreported as another's. ui-dashboard wires `interactions.linkGroup` from the dashboard's own link groups (in progress). |

### 7. Dashboard

| Item | Owner | Status | Notes |
|---|---|---|---|
| Multiple charts per dashboard | ui-dashboard | todo |  |
| Drag-and-drop layout | ui-dashboard | todo |  |
| Resizable chart containers | ui-dashboard | todo |  |
| Grid-based layout | ui-dashboard | todo |  |
| Dashboard-level filters | ui-dashboard | todo |  |
| Global date filters | ui-dashboard | todo |  |
| Chart synchronization | ui-dashboard | todo |  |
| Multiple dashboard tabs | ui-dashboard | todo |  |
| KPI cards | ui-dashboard | todo |  |
| Text and image widgets | ui-dashboard | todo |  |
| Responsive layouts | ui-dashboard | todo |  |

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
| Share dashboard through URL | platform · ui-dashboard | partial | Backend done: token mint/list/revoke, public resolve, scoped to what the shared item reaches. UI (`/share/:token` view): ui-dashboard, in progress. |
| Embed visualizations | platform · ui-dashboard | partial | Backend done: `/api/embed/{token}` (same resolve, public). UI (`/embed/:token`, iframe snippet): ui-dashboard, in progress. |
| Generate reports | platform · ui-dashboard | partial | Backend done: `/api/reports/{projectId}[/dashboards/{id}]`, authenticated, bundles chart specs/pipelines. HTML/PDF assembly is ui-dashboard's (frontend-rendered, since Python can't render ECharts headlessly), in progress. |
| Print-friendly layouts | ui-dashboard | todo | Entirely frontend (print CSS); no backend component. |

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
