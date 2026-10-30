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
  Open nits: duplicate manifest ids, Parquet column-type check, `schemaVersion` stamp in
  `create_from_doc`, streaming upload size limit (needs middleware).

## MVP.md

### 1. Data Sources & Import

| Item | Owner | Status | Notes |
|---|---|---|---|
| CSV / TSV import | data-core · ui-data | partial | Upload + import-from-path → Parquet with column profile. No import options UI yet. |
| Excel import | data-core · ui-data | todo |  |
| JSON import | data-core · ui-data | partial | Backend via DuckDB read_json; not exercised in the UI yet. |
| Data preview | data-core · ui-data | partial | Virtual grid with server row pages. No sort/filter/resize yet. |
| Data refresh | data-core · ui-data | todo |  |
| Large dataset handling | data-core · ui-data | partial | Parquet + DuckDB + paging. No jobs, no perf-budget run yet. |

### 2. Data Preparation & Transformation

| Item | Owner | Status | Notes |
|---|---|---|---|
| Data filtering | transform · ui-data | todo |  |
| Sorting | transform · ui-data | todo |  |
| Grouping | transform · ui-data | todo |  |
| Aggregation | transform · ui-data | todo |  |
| Missing value handling | transform · ui-data | todo |  |
| Duplicate removal | transform · ui-data | todo |  |
| Data type conversion | transform · ui-data | todo |  |
| Column selection | transform · ui-data | todo |  |
| Calculated columns | transform · ui-data | todo |  |
| Custom formulas | transform · ui-data | todo |  |
| Join and merge datasets | transform · ui-data | todo |  |
| Pivot and unpivot | transform · ui-data | todo |  |
| Data normalization | transform · ui-data | todo |  |

### 3. Chart & Visualization Types

Tracked per plot under Plots.md below.

### 4. Chart Configuration

| Item | Owner | Status | Notes |
|---|---|---|---|
| X-axis and Y-axis selection | ui-charts | partial | Phase 0 builder (x, y, aggregate) for bar. |
| Multiple Y-axis support | ui-charts | todo |  |
| Color customization | ui-charts | todo |  |
| Labels and annotations | ui-charts | todo |  |
| Legend configuration | ui-charts | todo |  |
| Chart titles and subtitles | ui-charts | todo |  |
| Axis scaling (linear, logarithmic) | ui-charts | todo |  |
| Axis formatting | ui-charts | todo |  |
| Gridline configuration | ui-charts | todo |  |
| Data point markers | ui-charts | todo |  |
| Line styles and thickness | ui-charts | todo |  |
| Error bars | ui-charts | todo |  |
| Trendlines | ui-charts | todo |  |
| Regression lines | ui-charts | todo |  |
| Confidence intervals | ui-charts | todo |  |
| Tooltips | ui-charts | partial | Axis tooltip on bar. |
| Reference lines | ui-charts | todo |  |
| Chart orientation | ui-charts | todo |  |
| Stacking and grouping | ui-charts | todo |  |

### 5. Statistical Analysis

| Item | Owner | Status | Notes |
|---|---|---|---|
| Mean, median and mode | stats · ui-data | todo |  |
| Standard deviation and variance | stats · ui-data | todo |  |
| Percentiles and quartiles | stats · ui-data | todo |  |
| Correlation analysis | stats · ui-data | todo |  |
| Linear regression | stats · ui-data | todo |  |
| Multiple regression | stats · ui-data | todo |  |
| Hypothesis testing | stats · ui-data | todo |  |
| T-tests | stats · ui-data | todo |  |
| ANOVA | stats · ui-data | todo |  |
| Chi-square tests | stats · ui-data | todo |  |
| Distribution fitting | stats · ui-data | todo |  |
| Outlier detection | stats · ui-data | todo |  |
| Confidence intervals | stats · ui-data | todo |  |
| Descriptive statistics | stats · ui-data | todo |  |

### 6. Interactivity

| Item | Owner | Status | Notes |
|---|---|---|---|
| Zoom and pan | ui-charts | todo |  |
| Hover tooltips | ui-charts | todo |  |
| Click-to-filter | ui-dashboard | todo |  |
| Cross-filtering | ui-dashboard | todo |  |
| Drill-down | ui-dashboard | todo |  |
| Drill-through | ui-dashboard | todo |  |
| Data point selection | ui-dashboard | todo |  |
| Multi-select | ui-dashboard | todo |  |
| Interactive legends | ui-charts | todo |  |
| Range sliders | ui-charts | todo |  |
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
| Light and dark themes | ui-charts | partial | CSS tokens + OS/forced mode; chart colors from tokens. |
| Custom color palettes | ui-charts | todo |  |
| Custom fonts | ui-charts | todo |  |
| Chart templates | ui-charts | todo |  |
| Reusable styles | ui-charts | todo |  |
| Number formatting | ui-charts | todo |  |
| Currency formatting | ui-charts | todo |  |
| Percentage formatting | ui-charts | todo |  |
| Date formatting | ui-charts | todo |  |
| Custom legends | ui-charts | todo |  |
| Background customization | ui-charts | todo |  |

### 9. Export & Sharing

| Item | Owner | Status | Notes |
|---|---|---|---|
| Export as PNG | ui-charts | todo |  |
| Export as SVG | ui-charts | todo |  |
| Export as PDF | ui-charts | todo |  |
| Export as JPEG | ui-charts | todo |  |
| Export as CSV | data-core | todo |  |
| Export underlying data to Excel | data-core | todo |  |
| Copy chart to clipboard | ui-charts | todo |  |
| Share dashboard through URL | platform · ui-dashboard | todo |  |
| Embed visualizations | platform · ui-dashboard | todo |  |
| Generate reports | platform · ui-dashboard | todo |  |
| Print-friendly layouts | platform · ui-dashboard | todo |  |

### 10. Workspace & Project Management

| Item | Owner | Status | Notes |
|---|---|---|---|
| Save projects | platform · ui-data | partial | Backend done: atomic revision check (409), named snapshots. UI: manual save; autosave UI in review (ui-data). |
| Open existing projects | platform · ui-data | partial | Backend done: list/get/delete/rename/duplicate. UI: project picker in review (ui-data). |
| Autosave | platform · ui-data | partial | Backend done: no-op skip, snapshot every N (VIZ_AUTOSAVE_SNAPSHOT_EVERY), pruning. UI: 2 s debounce in review (ui-data). |
| Undo and redo | platform · ui-data | partial | zundo history, buttons and Ctrl/Cmd+Z / Shift+Z / Y. |
| Version history | platform · ui-data | partial | Backend done: list (kind/limit), preview, restore as new revision. UI: read-only list in review; restore UI todo (ui-data). |
| Duplicate charts | platform · ui-data | todo |  |
| Duplicate dashboards | platform · ui-data | todo |  |
| Rename charts | platform · ui-data | todo |  |
| Organize charts into folders | platform · ui-data | partial | Backend done: folder/ItemRef validation on save and import. UI todo (ui-data). |
| Import/export project configuration | platform · ui-data | partial | Backend done: .vizproj export/import (sanitized, fresh dataset ids, optional Parquet). UI todo (ui-data). |

## Plots.md

| § | Plot | Key | Owner | Status |
|---|---|---|---|---|
| 1 | Bar Chart | `bar` | ui-charts | done |
| 1 | Horizontal Bar Chart | `horizontal_bar` | ui-charts | todo |
| 1 | Grouped Bar Chart | `grouped_bar` | ui-charts | todo |
| 1 | Stacked Bar Chart | `stacked_bar` | ui-charts | todo |
| 1 | Line Chart | `line` | ui-charts | todo |
| 1 | Area Chart | `area` | ui-charts | todo |
| 1 | Pie Chart | `pie` | ui-charts | todo |
| 1 | Donut Chart | `donut` | ui-charts | todo |
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
