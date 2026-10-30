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
- 2026-10-30: stats follow-up merged: describe now resolves pipeline sources via `resolve_source`
  (cache key includes the compiled pipeline SQL hash, so a dataset refresh still invalidates it), a
  `data_errors()` context turns transform user errors and the 64 KB calc-guard trip into clean 422s
  instead of 500s, and a missing pipeline returns 404 (was 501). Reviewer: approve, no nits. pytest
  699 passed/6 skipped. In-app HTTP measurement of describe-with-modes (ungrouped) came in at p50
  4.1 s / max 4.8 s, over the 3 s budget — higher than qa's own bench (p50 2.99 s) on the same
  endpoint, likely measurement-sensitive (suspected cause: the app's lower `memory_limit` vs
  DuckDB's default spilling the modes UNION ALL); tracked as a Wave 4 perf item, not a regression
  from this diff (the diff touches only source resolution and error mapping, not the describe SQL).

## MVP.md

### 1. Data Sources & Import

| Item | Owner | Status | Notes |
|---|---|---|---|
| CSV / TSV import | data-core · ui-data | partial | Backend done: staged upload/path, preview, options, import job. UI: staged wizard with live preview and per-sheet jobs. Jobs indicator and per-tab ownership done. Remaining: close-while-starting race. |
| Excel import | data-core · ui-data | partial | Backend done (fastexcel, sheets, header row, skip rows). UI: sheet selection, one staged upload, parallel per-sheet imports. |
| JSON import | data-core · ui-data | partial | Backend done (array/NDJSON, nested-JSON hint). Not exercised in the UI yet. |
| Data preview | data-core · ui-data | partial | Backend done: rows with sort/filter/search, values, materialized views for deep pages. UI: server sort/filter/search, filtered counts, columns menu. |
| Data refresh | data-core · ui-data | partial | Backend done (new version, cache invalidation). UI: dataset menu refresh (verified against the backend). |
| Large dataset handling | data-core · ui-data | partial | Jobs with SSE + cancel, background DuckDB instance, import from path. 10M rows: import 5.0 s, pages ~20 ms. First sorted page ~190-340 ms cold (budget 150 ms). Cold deep pages before their view is built: ~1.8-2.7 s sorted, ~0.6-0.7 s filtered range (Wave 4). Free-text search ~620-660 ms (Wave 4 search pass). |

### 2. Data Preparation & Transformation

| Item | Owner | Status | Notes |
|---|---|---|---|
| Data filtering | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |
| Sorting | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |
| Grouping | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |
| Aggregation | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |
| Missing value handling | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |
| Duplicate removal | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |
| Data type conversion | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |
| Column selection | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |
| Calculated columns | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |
| Custom formulas | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |
| Join and merge datasets | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |
| Pivot and unpivot | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |
| Data normalization | transform · ui-data | partial | Backend done: compiler, inspect, preview at any step, formula check (allowlist, growth bounds), materialize jobs. Editor wiring to the API: ui-data in progress (`pipelineApi`). |

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
| Mean, median and mode | stats · ui-data | partial | Backend: describe (exact, SQL; modes with tie warning). Stats panel UI: Wave 2. |
| Standard deviation and variance | stats · ui-data | partial | Backend: describe (overflow-safe moments). Stats panel UI: Wave 2. |
| Percentiles and quartiles | stats · ui-data | partial | Backend: describe + quantiles kernel (exact, R type 7). Stats panel UI: Wave 2. |
| Correlation analysis | stats · ui-data | todo |  |
| Linear regression | stats · ui-data | partial | Kernel: OLS fit with CI band (used by trendlines). Endpoint + UI: Wave 2. |
| Multiple regression | stats · ui-data | todo |  |
| Hypothesis testing | stats · ui-data | todo |  |
| T-tests | stats · ui-data | todo |  |
| ANOVA | stats · ui-data | todo |  |
| Chi-square tests | stats · ui-data | todo |  |
| Distribution fitting | stats · ui-data | todo |  |
| Outlier detection | stats · ui-data | todo |  |
| Confidence intervals | stats · ui-data | partial | Kernel: bootstrap CIs; fit bands. Endpoint + UI: Wave 2. |
| Descriptive statistics | stats · ui-data | partial | Backend done: `/api/stats/describe` (grouped, filtered, percentiles, skew/kurtosis). 10 fields with modes 2.6 s at 10M; grouped 7-8 s (Wave 4). Stats panel UI: Wave 2. |

### 6. Interactivity

| Item | Owner | Status | Notes |
|---|---|---|---|
| Zoom and pan | ui-charts | partial | dataZoom with detail-on-demand re-query (needs transform merge for server xRange). |
| Hover tooltips | ui-charts | done |  |
| Click-to-filter | ui-dashboard | todo |  |
| Cross-filtering | ui-dashboard | todo |  |
| Drill-down | ui-dashboard | todo |  |
| Drill-through | ui-dashboard | todo |  |
| Data point selection | ui-dashboard | todo |  |
| Multi-select | ui-dashboard | todo |  |
| Interactive legends | ui-charts | done | Legend toggles survive cosmetic edits. |
| Range sliders | ui-charts | partial | Slider dataZoom; xDomain from server after transform merges. |
| Date range filters | ui-dashboard | todo |  |
| Dynamic chart updates | ui-dashboard | todo |  |
| Linked visualizations | ui-dashboard | todo |  |

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
| Share dashboard through URL | platform · ui-dashboard | todo |  |
| Embed visualizations | platform · ui-dashboard | todo |  |
| Generate reports | platform · ui-dashboard | todo |  |
| Print-friendly layouts | platform · ui-dashboard | todo |  |

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
| 2 | Histogram | `histogram` | plots-distribution-relationship | todo |
| 2 | Density Plot | `density` | plots-distribution-relationship | todo |
| 2 | Box Plot | `box` | plots-distribution-relationship | todo |
| 2 | Violin Plot | `violin` | plots-distribution-relationship | todo |
| 2 | Strip Plot | `strip` | plots-distribution-relationship | todo |
| 2 | Swarm Plot | `swarm` | plots-distribution-relationship | todo |
| 2 | Q-Q Plot | `qq` | plots-distribution-relationship | todo |
| 2 | ECDF Plot | `ecdf` | plots-distribution-relationship | todo |
| 2 | Frequency Polygon | `frequency_polygon` | plots-distribution-relationship | todo |
| 3 | Scatter Plot | `scatter` | plots-distribution-relationship | todo |
| 3 | Bubble Chart | `bubble` | plots-distribution-relationship | todo |
| 3 | Pair Plot | `pair` | plots-distribution-relationship | todo |
| 3 | Correlation Heatmap | `correlation_heatmap` | plots-distribution-relationship | todo |
| 3 | Correlogram | `correlogram` | plots-distribution-relationship | todo |
| 3 | Hexbin Plot | `hexbin` | plots-distribution-relationship | todo |
| 3 | Contour Plot | `contour` | plots-distribution-relationship | todo |
| 3 | Regression Plot | `regression` | plots-distribution-relationship | todo |
| 3 | Residual Plot | `residual` | plots-distribution-relationship | todo |
| 4 | Time Series Line Plot | `timeseries_line` | plots-timeseries | todo |
| 4 | Seasonal Plot | `seasonal` | plots-timeseries | todo |
| 4 | Lag Plot | `lag` | plots-timeseries | todo |
| 4 | Autocorrelation Plot (ACF) | `acf` | plots-timeseries | todo |
| 4 | Partial Autocorrelation Plot (PACF) | `pacf` | plots-timeseries | todo |
| 4 | Moving Average Plot | `moving_average` | plots-timeseries | todo |
| 4 | Candlestick Chart | `candlestick` | plots-timeseries | todo |
| 4 | Calendar Heatmap | `calendar_heatmap` | plots-timeseries | todo |
| 5 | Stacked Area Chart | `stacked_area` | plots-composition-specialized | todo |
| 5 | 100% Stacked Bar Chart | `percent_stacked_bar` | plots-composition-specialized | todo |
| 5 | Waterfall Chart | `waterfall` | plots-composition-specialized | todo |
| 5 | Funnel Chart | `funnel` | plots-composition-specialized | todo |
| 5 | Treemap | `treemap` | plots-composition-specialized | todo |
| 5 | Sunburst Chart | `sunburst` | plots-composition-specialized | todo |
| 5 | Mosaic Plot | `mosaic` | plots-composition-specialized | todo |
| 5 | Marimekko Chart | `marimekko` | plots-composition-specialized | todo |
| 6 | Radar Chart | `radar` | plots-multivariate-econometric | todo |
| 6 | Parallel Coordinates Plot | `parallel_coordinates` | plots-multivariate-econometric | todo |
| 6 | PCA Biplot | `pca_biplot` | plots-multivariate-econometric | todo |
| 6 | Factor Loading Plot | `factor_loading` | plots-multivariate-econometric | todo |
| 6 | Cluster Plot | `cluster` | plots-multivariate-econometric | todo |
| 6 | Dendrogram | `dendrogram` | plots-multivariate-econometric | todo |
| 6 | Andrews Curves | `andrews_curves` | plots-multivariate-econometric | todo |
| 7 | Choropleth Map | `choropleth` | plots-geo | todo |
| 7 | Bubble Map | `bubble_map` | plots-geo | todo |
| 7 | Dot Density Map | `dot_density` | plots-geo | todo |
| 7 | Cartogram | `cartogram` | plots-geo | todo |
| 7 | Geographic Heatmap | `geo_heatmap` | plots-geo | todo |
| 8 | Kaplan-Meier Survival Curve | `kaplan_meier` | plots-multivariate-econometric | todo |
| 8 | Forest Plot | `forest` | plots-multivariate-econometric | todo |
| 8 | ROC Curve | `roc` | plots-multivariate-econometric | todo |
| 8 | Precision-Recall Curve | `precision_recall` | plots-multivariate-econometric | todo |
| 8 | Lorenz Curve | `lorenz` | plots-multivariate-econometric | todo |
| 8 | Quantile Plot | `quantile` | plots-multivariate-econometric | todo |
| 8 | Marginal Effects Plot | `marginal_effects` | plots-multivariate-econometric | todo |
| 8 | Coefficient Plot | `coefficient` | plots-multivariate-econometric | todo |
| 8 | Impulse Response Function (IRF) | `impulse_response` | plots-multivariate-econometric | todo |
| 8 | Event Study Plot | `event_study` | plots-multivariate-econometric | todo |
| 9 | Sankey Diagram | `sankey` | plots-composition-specialized | todo |
| 9 | Network Graph | `network` | plots-composition-specialized | todo |
| 9 | Alluvial Diagram | `alluvial` | plots-composition-specialized | todo |
| 9 | Gantt Chart | `gantt` | plots-composition-specialized | todo |
| 9 | Pareto Chart | `pareto` | plots-composition-specialized | todo |
| 9 | Error Bar Plot | `error_bar` | plots-composition-specialized | todo |
| 9 | Lollipop Chart | `lollipop` | plots-composition-specialized | todo |
| 9 | Dumbbell Plot | `dumbbell` | plots-composition-specialized | todo |
| 9 | Ridgeline Plot | `ridgeline` | plots-composition-specialized | todo |
| 9 | Raincloud Plot | `raincloud` | plots-composition-specialized | todo |

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
