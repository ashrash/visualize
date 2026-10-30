# CLAUDE.md — Visualize

A web-based data visualization and analysis tool. Python does the heavy lifting and a light React UI draws the results. It runs locally on Windows and macOS with one command, and is built so it can move to AWS later without a rewrite.

**Requirements (source of truth; read the relevant section before building a feature):**
- [MVP.md](MVP.md): iteration 1, feature areas §1–§10
- [Plots.md](Plots.md): ~70 chart types (this is MVP §3)
- [Next.md](Next.md): iteration 2 (§11 advanced features, §12 usability/accessibility)

Anything not in these files is out of scope until the user confirms it.

**Status (2026-10-31):** Phase 0 and Wave 1 are done and verified on Windows; macOS is not verified yet. Wave 2 (plot families, stats §5, stats panel UI) has not started. Per-feature progress is in [docs/STATUS.md](docs/STATUS.md), which also lists the Wave 4 perf watch-items carried out of Wave 1 (ungrouped describe and dashboard-refresh-during-import both straddle their budgets depending on measurement conditions; need a quiet-machine re-measurement). Keep this file accurate when a decision changes.

---

## 1. Guiding principles

1. **Compute in Python, draw in the browser.** DuckDB/Python does all scanning, aggregation, binning and statistics on the server. The browser receives only what it will draw.
2. **Never ship raw rows to a chart.** Chart responses are capped (default 50k marks). Above the cap the server reduces the data by aggregating, binning (1-D, 2-D or hex), downsampling (M4/LTTB) or sampling. It reports which in `meta.reduction`, so the UI can show a "sampled" badge.
3. **Parquet is the durable data format; DuckDB is a stateless engine.** Each import is converted once to Parquet. DuckDB runs in-memory over views on those Parquet files, and no `.duckdb` file holds user data. This avoids lock-in to DuckDB's storage format and Windows file-locking problems.
4. **Everything a user builds is a declarative JSON spec:** pipeline, chart, dashboard, project. Specs are versioned Pydantic models stored as JSON documents, which gives undo/redo, version history, autosave, duplicate, import/export, refresh and sharing almost for free.
5. **Contracts first.** Pydantic models in `backend/src/viz/contracts/` are the single source of truth. TypeScript types are generated from OpenAPI; never hand-write duplicate types.
6. **Local-first, cloud-ready.** These swap by config alone: storage (local dir ↔ S3), metadata DB (SQLite ↔ Postgres), auth (none ↔ OIDC), jobs (in-process ↔ queue). Running locally needs no Docker.
7. **Light frontend.** Use tree-shaken ECharts and headless libraries; no heavy UI frameworks. Watch bundle size.

## 2. Tech stack (decided)

### Backend (Python)
| Concern | Choice | Notes |
|---|---|---|
| Runtime / packaging | Python 3.13 (≥3.12), **uv** | One tool for the Python install, venv and lockfile; works the same on Windows and macOS |
| API | **FastAPI** + Uvicorn, Pydantic v2, pydantic-settings | OpenAPI is the source for the TS types |
| Query engine | **DuckDB 2.0.x LTS** (`duckdb>=2.0,<2.1`) | Embedded columnar OLAP: multithreaded, spills to disk for larger-than-RAM data, reads CSV/JSON/Parquet/S3. 2.0 LTS was released 2026-10-21 and is supported to at least Oct 2027; move to ≥2.0.1 once it ships. Use Python-style lambdas (`lambda x: x + 1`); the arrow form is off by default in 2.0. **2.1 drops macOS x86_64**, so stay on 2.0.x while Intel Macs must be supported. |
| Data transfer | **Apache Arrow** (pyarrow ↔ `apache-arrow` JS), IPC stream | For grid pages and chart data; JSON for everything else |
| Excel | **fastexcel** (calamine, Rust) | Reads xlsx/xls/xlsb/ods into Arrow and is much faster than openpyxl. DuckDB's excel extension can't read `.xls`. |
| SQL building / validation | **SQLGlot** | Quotes identifiers; compiles specs to DuckDB SQL; validates formulas and user SQL |
| Stats / ML | NumPy, SciPy, statsmodels, scikit-learn | pandas only at the library boundary, on capped samples |
| Downsampling | **tsdownsample** (M4, MinMaxLTTB; Rust) | For line and area series |
| Export | DuckDB `COPY` (CSV), **xlsxwriter** (Excel) | |
| Metadata | SQLAlchemy 2.0 + Alembic; **SQLite** locally, Postgres on AWS (`VIZ_DATABASE_URL`) | |
| Data dir | platformdirs (`%LOCALAPPDATA%\visualize`, `~/Library/Application Support/visualize`); `VIZ_DATA_DIR` overrides | |
| Cache | in-process LRU (cachetools) keyed by (dataset version, SQL hash) | Redis only if AWS runs multiple instances |
| CLI | Typer (`viz dev`, `viz build`, `viz serve`) | |
| Quality | pytest, hypothesis (fuzzing the pipeline compiler), ruff (lint + format), mypy | |

### Frontend (React)
| Concern | Choice | Notes |
|---|---|---|
| Base | React 19 + **TypeScript 5.x** (strict) + Vite 8, **npm**, Node LTS (≥22) | npm ships with Node, so there's one fewer prerequisite. Stay on TypeScript 5 until openapi-typescript supports 7 (see §12). |
| Charts | **Apache ECharts 6**, tree-shaken from `echarts/core` behind our own thin wrapper (not echarts-for-react), plus two of the official `echarts-custom-series` packages (`@echarts-x/custom-violin`, `@echarts-x/custom-bar-range`) | Canvas rendering with large/progressive modes. Native series cover most of Plots.md: bar, line, pie, scatter, boxplot, heatmap, candlestick, calendar, treemap, sunburst, sankey, graph, funnel, radar, parallel, geo/map. v6 adds beeswarm and jitter (swarm/strip), a matrix coordinate system (pair plot, correlogram), broken axes, runtime `setTheme` and dark mode. dataZoom, brush, PNG/JPEG/SVG export and `aria` are built in. **`@echarts-x/custom-contour` and `@echarts-x/custom-line-range` are not used**: both import the full, non-tree-shaken `echarts` package internally (`import { zrUtil } from 'echarts'`), which self-registers every chart type and component as a side effect — confirmed to break `render.test.ts`'s unregistered-component check. Contour is rendered instead as a client-side binned-density heatmap reusing the already-registered `HeatmapChart`; anything that wanted a line-range/CI-band look uses the `ciBand`/`errorBar` core primitives instead. |
| Data grid | **TanStack Table + TanStack Virtual**, rows windowed from the server | |
| Dashboard layout | **react-grid-layout v2** | Drag, resize, responsive breakpoints |
| State | **Zustand** (+ **zundo** for undo/redo), **TanStack Query** for the server cache | |
| UI kit | **shadcn/ui** (Radix) + Tailwind CSS v4, lucide icons | Component source lives in the repo; no runtime UI framework |
| Editors | **CodeMirror 6** | Formulas, SQL, scripts |
| Export | ECharts `getDataURL`, **jsPDF + svg2pdf.js**, Clipboard API | |
| Quality | Vitest + Testing Library, Playwright (e2e), Biome (lint + format), axe (a11y) | |

### Do we need Postgres or Docker? **No.**
DuckDB is embedded and SQLite is a single file, so local runs need no database server and no Docker. Postgres is used only as the metadata store on AWS. An optional `infra/docker-compose.yml` with Postgres arrives in Wave 4, for AWS-parity testing only.

### Rejected alternatives (don't reintroduce without updating this section)
- **Plotly.js / Dash:** ~3.5 MB bundle, SVG-heavy at scale, and Dash's callback model doesn't fit drag-and-drop dashboards with undo/redo. ECharts covers more of Plots.md natively at a fraction of the size.
- **Streamlit / Panel / Dash as the app framework:** good for prototypes, wrong shape for this much custom UX.
- **Postgres (any row store) as the analytics engine:** slow full scans and it needs a server.
- **pandas as the engine:** in-memory and single-threaded. Polars is good, but DuckDB SQL composes generated queries better, works out-of-core and reads every input format.
- **DuckDB-WASM / Mosaic in the browser:** bounded by browser memory, large downloads, and it moves logic out of Python. Revisit later for offline embeds.
- **AG Grid:** heavy, and its server-side row model is Enterprise-only. **Glide Data Grid:** last npm publish Feb 2024, so it's a maintenance risk.
- **Celery/Redis locally:** unnecessary; use the in-process job runner behind an interface.

## 3. Architecture

```
Browser — React SPA
  ├─ Data:       import wizard · virtual grid · pipeline editor · stats panel
  ├─ Charts:     plot registry → render(spec, data, theme) → ECharts option
  ├─ Dashboards: grid layout · widgets · filters · cross-filter bus
  └─ State:      project doc (Zustand + zundo) · server cache (TanStack Query)
        │   JSON: specs, metadata, stat results     Arrow IPC: grid pages, chart data
        ▼
FastAPI /api/*  (sync endpoints → threadpool; never block the event loop)
  ├─ ingest/     file → Arrow → Parquet · column profiling · refresh = new version
  ├─ transform/  pipeline spec → SQL (SQLGlot) → DuckDB view (optional Parquet materialisation)
  ├─ chartdata/  ChartSpec + Predicates → aggregate | bin | downsample | sample → Arrow
  ├─ stats/      descriptive & contingency (SQL, exact) · tests & models (SciPy/statsmodels, sampled)
  ├─ plots/      per-family stat transforms (KDE, Q-Q, ACF, KM, PCA, …)
  ├─ projects/   specs · revisions · snapshots · folders · share tokens ──► SQLite | Postgres
  ├─ exports/    CSV · Excel · report assets
  └─ jobs/       long imports / materialisations / exports, progress via SSE, cancellable
        │
DuckDB — in-memory, one per process, cursor per request, memory_limit + temp_directory for spill
        │
Data dir — sources/ (copied originals) · datasets/<id>/v<n>.parquet · temp/ · assets/   (local disk | S3)
```

### Core specs (in `contracts/`)
- **Dataset:** `id, name, source {kind: csv|tsv|excel|json, path|uri, options (delimiter, encoding, sheet, header row, json path)}, versions[] {n, parquetPath, rowCount, columns[{name, type, semantic: quantitative|temporal|nominal|ordinal|geo, nullCount, approxDistinct}], importedAt}`.
- **Pipeline:** `datasetId` plus ordered `steps[]`, a discriminated union on `op`: `filter, sort, select, rename, cast, fill_missing, drop_missing, dedupe, calc (formula), group_aggregate, join, union, pivot, unpivot, normalize (zscore|minmax|robust)`. Each step can be toggled on or off. The output is a derived dataset (a view), and `materialize: true` writes Parquet for heavy steps such as join and pivot.
- **ChartSpec:** `id, plotType (PlotType enum = registry key), source (dataset|pipeline id), encodings {x, y[], color, size, shape, facet, label, tooltip[], time, lat, lon, …}, params (plot-specific: bins, bandwidth, ciLevel, lags, …), style {title, subtitle, axes[{scale: linear|log|time|category, format, grid, min, max}], legend, palette, markers, lines, annotations, refLines, orientation, stack: none|stack|percent}, interactions {zoom, brush, clickFilter, drillHierarchy[], linkGroup}`.
- **Predicate:** `{field, op: in|not_in|eq|range|between_dates|is_null, value}`. Dashboard filters, click-to-filter, brush selections and drill-down all use this one type.
- **Chart data:** `POST /api/charts/data {spec, filters: Predicate[], viewport {widthPx, xRange?}}` returns an Arrow table plus `meta {rowsScanned, reduction: none|aggregated|binned|downsampled|sampled, method, cached, ms}`. Plot types that use a stat transform return a JSON `StatResult` instead, for structured output such as five-number summaries or a dendrogram tree.
- **Dashboard:** `tabs[] → items[{i, x, y, w, h, widget: chart|kpi|text|image|filter}], filters[], linkGroups[], theme`.
- **Project:** `datasets, pipelines, charts, dashboards, folders, themes, templates, revision` (optimistic concurrency) and `schemaVersion`. Snapshots go in `project_versions`.

### Plot registry: the plugin seam
Each plot type is one registry entry with the same key on both sides:
- **Backend** `viz/plots/<family>.py`: an optional `stat(spec, relation) -> StatResult | Arrow`, plus required encodings and column types.
- **Frontend** `src/charts/plots/<family>/<plot>.ts`: `{key, label, family, encodingsSchema, paramsSchema, defaults, render(spec, data, theme) => EChartsOption, recommend?(columns) => score}`.
- The config panel is generated from the encodings/params schema; never hand-build a form per plot.
- A test enforces that every `PlotType` value has a renderer, and a stat transform if it declares one.
- Next.md's custom visualization plugins and chart recommendations plug into this same seam.

## 4. Repository layout (target)

```
visualize/
├── CLAUDE.md  MVP.md  Plots.md  Next.md
├── .claude/agents/            # agent definitions: model, effort, owned paths (§9)
├── pyproject.toml  uv.lock  .python-version  .gitignore  .gitattributes
├── backend/
│   ├── src/viz/
│   │   ├── main.py  cli.py  config.py
│   │   ├── contracts/         # Pydantic specs; shared contract, additive changes only
│   │   ├── api/               # thin routers, one file per domain: api/<domain>.py
│   │   ├── engine/            # DuckDB lifecycle, SQL helpers, cache, timeouts/interrupt
│   │   ├── ingest/  transform/  chartdata/  stats/  plots/
│   │   ├── projects/  exports/  jobs/  storage/  auth/
│   │   ├── meta/              # SQLAlchemy models + Alembic migrations
│   │   └── static/            # built frontend (generated, git-ignored)
│   └── tests/                 # mirrors src/viz; small fixtures in tests/data
├── frontend/
│   ├── package.json  vite.config.ts  biome.json
│   ├── e2e/                   # Playwright
│   └── src/
│       ├── api/               # schema.d.ts (generated), client, arrow fetch helper
│       ├── app/  state/  components/ui/
│       ├── data/              # import wizard, grid, pipeline editor
│       ├── charts/{core,config,plots/<family>}/
│       ├── dashboard/  stats/  export/
├── samples/                   # small committed datasets (<5 MB total)
├── scripts/                   # gen_large_dataset.py, bench.py
├── docs/STATUS.md             # tracker: every MVP/Plots/Next item → owner, status
└── infra/                     # Dockerfile, docker-compose.yml (Wave 4), aws/ (later)
```

The Python project lives at the repo root (`pyproject.toml` builds the package from `backend/src/viz`), so every command runs from the root.

## 5. Commands (created in Phase 0; keep this list accurate)

Run everything from the repo root. Commands are the same in PowerShell and macOS shells. Don't add Makefiles or bash-only scripts; orchestration lives in the Python CLI.

```
uv sync                                   # backend deps
npm --prefix frontend install             # frontend deps
uv run viz dev                            # uvicorn --reload :8000 + vite :5173 (proxies /api)
uv run viz build                          # build frontend into backend/src/viz/static
uv run viz serve                          # single process on 127.0.0.1:8000, opens browser
uv run pytest                             # backend tests; -k <name> for one
uv run ruff check --fix                   # lint
uv run ruff format                        # format
uv run mypy backend/src                   # types
npm --prefix frontend run test            # vitest
npm --prefix frontend run lint            # biome
npm --prefix frontend run gen:api         # regenerate src/api/schema.d.ts; run after ANY contract change
npm --prefix frontend run e2e             # playwright (qa adds this script in Wave 1)
uv run viz openapi --out <file>           # write the OpenAPI schema (gen:api uses this)
uv run python scripts/gen_large_dataset.py --rows 10000000   # perf fixture (git-ignored)
uv run python scripts/bench.py            # check perf budgets
```

Prerequisites: `uv` and Node LTS (≥22). On Windows, `winget install astral-sh.uv` installs uv; open a new terminal afterwards so it's on PATH. Wave 4 ships the built frontend inside the Python wheel, so people who only run the app need just `uv`.

## 6. Engineering rules

### Data and performance
- **Budgets** (laptop, 10M rows × 20 cols): CSV import ≤ 60 s · grid page ≤ 150 ms · aggregated chart query p50 ≤ 300 ms · cross-filter refresh of a 6-chart dashboard ≤ 1 s, **including while a 10M-row import job runs** · 12 concurrent chart queries finish without running out of memory · chart payload ≤ 50k marks and ≤ 5 MB · descriptive stats for 10 fields with modes ≤ 3 s, ungrouped (an analysis panel, not a dashboard path; grouped describe with modes runs 7–8 s at 10M and is a Wave 4 item). Design for 100M+ rows, larger than RAM: set DuckDB `memory_limit` (default ~60% of RAM) and point `temp_directory` at the data dir.
- **Large files:** local mode offers **import from path**, reading the file in place with no multi-GB browser upload. AWS mode uses S3 presigned multipart upload.
- **Long work runs as jobs** (import, materialize, big export) with SSE progress and cancellation (`cursor.interrupt()`). DuckDB and SciPy calls never run on the event loop.
- **Exact where cheap, sampled where not:** descriptive stats, counts, quantiles, Pearson correlation and contingency tables run in DuckDB on the full data. Model fits (regression, KDE, distribution fitting, ML) use a reservoir sample (default 1M rows; KDE 100k) and return `sampled: true, n`.
- **Series:** downsample with M4/MinMaxLTTB to about 2× the viewport width in pixels. On dataZoom, re-query only the visible range (detail on demand).
- Cache keys always include the dataset version, so a refresh invalidates them automatically.

### Concurrency model
Most parallelism comes from the libraries. Our job is to **limit it and cancel stale work**; never hand-roll threading or chunking.
- **Inside a query:** DuckDB spreads each query across all cores by itself, using one scheduler per instance that all cursors share. Set only `threads` (default: all cores). Don't partition data at the app level.
- **Across requests:** run DuckDB/SciPy work in FastAPI's threadpool (sync `def` endpoints or `run_in_threadpool`). Give each request its own `cursor()`, because a DuckDB connection is not thread-safe; DuckDB releases the GIL while a query runs. Cap concurrent DuckDB work with a semaphore (`VIZ_MAX_CONCURRENT_QUERIES`, default 8, tuned with the bench) so bursts queue instead of exhausting memory.
- **Cancel stale work (highest value):** cross-filtering and zoom make most in-flight requests obsolete. The frontend aborts superseded requests (TanStack Query `signal`). The backend calls `cursor.interrupt()` when the client disconnects or a newer request arrives for the same chart id.
- **Python CPU work:** write stat transforms vectorized; NumPy/SciPy/statsmodels spend their time in C/BLAS with the GIL released. Pure-Python-heavy work (bootstrap, force layouts, long loops) goes to a small `ProcessPoolExecutor` (default 2 workers).
  - Pre-warm the pool at startup: Windows and macOS start processes with `spawn`, so each worker re-imports SciPy (~1–2 s).
  - Worker functions must be top-level and picklable.
  - Pass a Parquet path + SQL or a capped Arrow table, never a 10M-row DataFrame.
  - Use threadpoolctl to cap BLAS/OpenMP threads per call, so parallel fits don't oversubscribe the cores.
- **Jobs vs interactive:** jobs (import, materialize, export) run on their own executor, at most 1–2 at once, and never use the request threadpool. **Decided after the Wave 1 bench:** jobs and grid-view builds run on a second in-memory DuckDB instance with ¼ of the cores (at least 2) and ⅓ of the memory budget (`VIZ_JOB_DUCKDB_THREADS`; 0 shares the main instance). This took grid pages during a 10M-row import from a 594 ms worst case down to 74 ms. The cost is that the import itself slowed from 3 s to 4.7 s.
- **Processes:** run one Uvicorn worker locally (one DuckDB, one cache, one `memory_limit`). On AWS, scale by adding ECS tasks, not workers per task.
- **Deliberately not used (for now):** free-threaded (no-GIL) Python builds, because wheel support across the scientific stack is uneven. Web Workers in the browser, because payload caps keep Arrow decoding small; add one only if profiling shows decode blocking the main thread for more than 50 ms.

### Security (matters locally, essential on AWS)
- Never interpolate user input into SQL strings. Build queries with SQLGlot and quote identifiers.
- **Formulas** (calculated columns) are a DuckDB expression subset: parse them with SQLGlot and check against a **function allowlist**. Reject subqueries, table functions (`read_*`, `glob`, `getenv`, …) and PRAGMA/SET/ATTACH/COPY/INSTALL/LOAD.
- **Bound value growth per step.** Formulas can blow up a single value: `replace` expansion, `||` chains and list concatenation compound across chained calc steps. Every step that produces text gets a generated length guard. Calc outputs with nested types are refused, except a bare column reference. Each formula has a documented worst-case growth factor, enforced by tests. DuckDB's `memory_limit` doesn't cover string heaps, so very wide inputs can still exhaust memory. Rows can also multiply, bounded only by the query timeout and the memory limit: through chained many-to-many joins, chained unpivots (each step doubles the rows), and unions that reference one pipeline twice. And because DuckDB evaluates 2,048-row vectors before the guard runs, a single step can transiently hold about 2,048 × its per-value peak. All of this is accepted locally; hosted mode needs per-query process isolation with a memory cap.
- **User SQL** (Next §11) is SELECT-only and runs on a locked connection (`enable_external_access=false`, `allowed_directories` = data dir, `lock_configuration=true`) with a timeout.
- Never `eval`/`exec` user input in the API process. Custom Python/R scripts (Next) run in a subprocess with a timeout and memory cap, with Arrow in and out. In hosted mode they stay disabled unless an isolated runner is configured.
- The server binds `127.0.0.1` by default; `--host 0.0.0.0` is opt-in. Every router depends on `auth.current_user` (a local stub now, OIDC later); never bypass it.
- Enforce upload size limits and sniff file types. Sanitize Markdown in text widgets with DOMPurify.

### Cross-platform
- Use `pathlib` everywhere and never hard-code separators. Test paths with spaces and non-ASCII characters.
- Windows locks files that are open in Excel. Uploads, and path imports up to `VIZ_COPY_SOURCE_MAX_MB` (default 512), are copied into `sources/` before reading. Larger path imports are read in place, so a multi-GB file isn't duplicated on disk. On `PermissionError`, show a clear "close the file in Excel" message.
- `.gitattributes` enforces LF line endings.
- New Python dependencies must ship wheels for win_amd64, macOS arm64 and macOS x86_64; check before adding one.

### Code
- API routers stay thin. Logic lives in services that can be tested without HTTP.
- **Contract changes:** additive ones (new optional field, new enum member) are fine. A rename or removal needs `schemaVersion` bumped and an upgrader in `contracts/migrations.py` so saved projects still load. Run `gen:api` and commit the generated types in the same change.
- **Frontend:** no `any`. Fetch data only through the `src/api` client and render charts only through the registry. Take colors and fonts only from theme tokens; that is what keeps dark mode and custom palettes working.
- **Accessibility from day one,** even though §12 is in Next: keyboard-reachable controls, ECharts `aria` enabled, color-blind-safe palettes (Okabe-Ito, viridis, cividis) in the default set, and "view as table" on every chart.
- **Tests:** every transform step and stat function gets known-answer unit tests, cross-checked against SciPy/statsmodels or hand-computed values. Test chart-data endpoints for their reduction behavior at the caps. Snapshot-test renderers (spec + fixture → ECharts option).

## 7. MVP feature → implementation map

| § | Area | Backend | Frontend |
|---|---|---|---|
| 1 | Import & sources | DuckDB `read_csv` (auto-detect, with delimiter/encoding overrides) and `read_json` (array or NDJSON); fastexcel per sheet. All imports become Parquet with a column profile. Refresh re-imports as a new version. | Import wizard (upload or path, options, live preview of the first 1k rows), dataset list, virtual grid with server sort/filter |
| 2 | Prep & transform | Pipeline compiler (steps in §3) → SQL views; formula validation; join/union/pivot/unpivot; normalize; materialization | Pipeline editor (step list with reorder/toggle/delete, per-step form, CodeMirror formula editor with column autocomplete, preview at any step) |
| 3 | Chart types | `chartdata/` + `plots/` stat transforms | Registry renderers; see §8 |
| 4 | Chart config | Trendlines, regression and CIs computed on the server | Generated config panel; multiple Y axes (`yAxisIndex`); linear/log/time scales; `markLine` reference lines; `markPoint`/`markArea`/`graphic` annotations; shared error-bar series; orientation swap; stack/percent; markers and line styles; formatted tooltips |
| 5 | Statistics | `stats/`: descriptive (SQL), correlation (Pearson in SQL; Spearman/Kendall in SciPy), OLS/multiple regression (statsmodels), t-tests, ANOVA, χ² (counts from SQL → SciPy), distribution fitting (SciPy `fit` + AIC/KS), outliers (IQR/z/MAD), CIs (t, bootstrap). Every result is `{values, method, n, sampled, warnings}`. | Stats panel: pick dataset/columns/test, result tables, "plot this" shortcuts |
| 6 | Interactivity | Predicates on every chart query | dataZoom (zoom, pan, range sliders), tooltips, brush (selection, multi-select), click → Predicate, interactive legends, a cross-filter bus per `linkGroup` (each chart excludes its own predicate), drill-down (`drillHierarchy`), drill-through (open a target dashboard or grid with the predicates), `echarts.connect` for synced tooltips/zoom |
| 7 | Dashboard | Dashboard spec CRUD | react-grid-layout (12 columns, drag, resize, responsive), tabs, widgets: chart, KPI (value, delta, sparkline), text (Markdown), image, filter (date range, dropdown, slider). Global filters map to fields across datasets. |
| 8 | Themes & formats | — | CSS-variable design tokens → generated ECharts theme; light/dark via `setTheme`; palettes (including color-blind-safe); fonts (bundled Inter + system); templates = saved style presets; number/currency/percent/date formatting via `Intl` |
| 9 | Export & share | CSV (`COPY`), Excel (xlsxwriter; warn at the 1,048,576-row limit), read-only share/embed tokens, report assembly | PNG/JPEG/SVG (`getDataURL`), PDF (jsPDF + svg2pdf), copy to clipboard (`ClipboardItem` PNG), `/share/:token`, `/embed/:token` with iframe snippet, print CSS, HTML/PDF dashboard report |
| 10 | Projects | Project CRUD with a revision check, autosave endpoint, snapshots (on save and every N autosaves), folders, duplicate, `.vizproj` export (zip of `project.json` + optional Parquet) | Project store + zundo undo/redo, debounced autosave (2 s), version history with restore, folder tree, rename/duplicate |

Share URLs on `localhost` work only on the same machine (or the LAN with `--host 0.0.0.0`). Real sharing arrives with AWS and auth.

## 8. Plot implementation map (Plots.md)

**S** = server work (Python/DuckDB); **C** = client rendering (ECharts 6). "agg" means chartdata aggregation.

1. **Basic:** S agg. C: bar (horizontal = swapped axes; grouped; stacked), line, area (`areaStyle`), pie, donut (`radius: [inner, outer]`).
2. **Distribution:** Histogram: S bins in SQL (Freedman–Diaconis default) → bar. Density: S KDE (SciPy, sampled) → line. Box: S exact quartiles + IQR outliers in SQL → boxplot. Violin: S KDE per group → custom violin. Strip / Swarm: S sampled points → scatter `jitter` (swarm adds `jitterOverlap: false`). Q-Q: S `scipy.stats.probplot` → scatter + line. ECDF: S quantile grid in SQL → step line. Frequency polygon: S bins → line.
3. **Relationship:** Scatter / Bubble: S raw points up to the cap, otherwise 2-D bins → scatter (`large`) or heatmap. Pair plot: S sample → matrix coordinate of scatters with histograms on the diagonal. Correlation heatmap / Correlogram: S `corr()` matrix in SQL → heatmap, or matrix with sized circles. Hexbin: S hex bins in SQL → custom polygons. Contour: S 2-D KDE grid → custom contour. Regression: S OLS/LOWESS + CI → scatter + line + band. Residual: S residuals vs fitted → scatter + zero line.
4. **Time series:** Line: S time bucketing or M4/LTTB → line + dataZoom. Seasonal: S aggregation by date part per period → multi-line. Lag: S `LAG()` → scatter. ACF/PACF: S statsmodels with CI → bar + band. Moving average: S `AVG() OVER (window)` → overlay line. Candlestick: S OHLC per bucket (`arg_min(price, ts)`, `max`, `min`, `arg_max(price, ts)`) → candlestick (+ volume bars). Calendar heatmap: S daily agg → calendar coordinate heatmap.
5. **Composition:** Stacked area; 100% stacked bar (S normalizes per group). Waterfall: S running sum → stacked bar on a transparent base. Funnel: native. Treemap / Sunburst: S hierarchical group-by → tree JSON. Mosaic / Marimekko: S crosstab with widths → custom rects.
6. **Multivariate:** Radar: S normalized group agg → radar. Parallel coordinates: S sample → parallel. PCA biplot: S scikit-learn PCA scores + loadings → scatter + arrows. Factor loadings: S PCA/FactorAnalysis loadings → heatmap or bar. Cluster plot: S KMeans/DBSCAN + 2-D PCA → colored scatter + centroids. Dendrogram: S `scipy.cluster.hierarchy.linkage` → custom elbow lines. Andrews curves: S Fourier projection of a sample → lines.
7. **Geospatial:** bundle Natural Earth GeoJSON (countries, admin-1) so maps work offline. Choropleth: S agg by region key → `map` series. Bubble map: S agg by lat/lon → scatter on `geo`. Dot density: S capped/sampled points → `large` scatter on `geo`. Geographic heatmap: S lat/lon grid bins → heatmap on `geo`. Cartogram: S Dorling layout (non-overlapping circles via force relaxation) → scatter on `geo`.
8. **Statistical & econometric:** Kaplan–Meier: S statsmodels `SurvfuncRight` + CI → step line + band. Forest: S estimates + CI → error-bar primitive. ROC / PR: S scikit-learn curves + AUC → line. Lorenz: S cumulative shares in SQL + Gini → line + equality diagonal. Quantile plot: S sorted quantiles → scatter/line. Marginal effects: S statsmodels `get_margeff` (discrete models) or analytic (OLS) → error-bar. Coefficient plot: S model params + CI → error-bar. IRF: S statsmodels VAR `irf` → multi-line + bands. Event study: S OLS with relative-time dummies → coefficient ± CI over event time.
9. **Specialized:** Sankey / Alluvial: S source→target agg (multi-level for alluvial) → sankey. Network: S node/edge agg → graph (force layout). Gantt: S tasks → custom bars on a time axis. Pareto: S sorted agg + cumulative % → bar + line on a dual axis. Error bar: S mean ± SD/SE/CI → error-bar. Lollipop: thin bar + scatter. Dumbbell: custom segment + two scatters. Ridgeline: S KDE per group → offset area lines. Raincloud: half-violin + box + jittered strip.

**Shared primitives** (build once in `charts/core/primitives/` and reuse): error bar, CI band, reference/diagonal line, offset KDE, hierarchy → tree, density fallback for over-cap scatters.

## 9. Agent plan

Agent definitions live in `.claude/agents/`. Each file sets that agent's model, effort, worktree isolation and owned paths, and the files are the source of truth for those settings. The table below is the overview.

**How to run**
- **Lead.** Start the orchestrating session with `claude --agent lead` (Fable 5.1, effort high). `lead.md` has a deliberately empty body, so Claude Code keeps its default system prompt; the lead's instructions are this file. For routine merging, the lead can switch to Opus 5.5 with `/model claude-opus-5-5`.
- **Builders** run as subagents, each in its own git worktree, and commit only to their worktree branch. **Worktrees branch from the default branch**, so the lead merges finished work into `main` before starting any agent that depends on it.
- **Review.** Every branch goes through `reviewer` before the lead merges it. The lead then runs the full test suite and updates `docs/STATUS.md` from the agent's report.
- **Concurrency.** Run at most 6 builders at once, plus the reviewer and the lead. If reviews or merges back up, drop to 4.
- **If the agent types aren't loaded** (a session that started before `.claude/agents/` existed), run each builder as a `general-purpose` subagent with the model, effort and worktree isolation from its file, and tell it to read its `.claude/agents/<name>.md` as its brief.
- **Plans** that rule 4 asks for go in `docs/plans/<agent>.md`, committed on the agent's branch, so the worktree survives until the lead continues the agent.

| Agent | Model · effort | Owns (write access) | Scope |
|---|---|---|---|
| `lead` | Fable 5.1 · high | `contracts/`, `CLAUDE.md`, `docs/STATUS.md`, `.claude/`, root config (`pyproject.toml`, `frontend/package.json`, Vite/TS/Biome config), merges | Phase 0, contract changes, dependencies, integration |
| `reviewer` | Opus 5.5 · high | nothing (read-only) | Reviews every branch before merge |
| `data-core` | Opus 5.5 · medium | `ingest/ storage/ engine/ jobs/ exports/`, `api/{datasets,jobs,exports}.py` | MVP §1, data exports (§9), concurrency model |
| `transform` | Opus 5.5 · high | `transform/ chartdata/`, `api/{pipelines,charts}.py` | MVP §2, chart-data protocol |
| `stats` | Opus 5.5 · xhigh | `stats/`, `plots/__init__.py`, `plots/base.py`, `api/stats.py` | MVP §5, shared stat kernels, plot-module discovery |
| `platform` | Sonnet 5.5 · medium | `projects/ meta/ auth/`, `main.py cli.py config.py errors.py`, `api/__init__.py`, `api/deps.py`, `api/{projects,share}.py`, `infra/`, `alembic.ini` | MVP §10 backend, share/embed, CLI, packaging |
| `ui-data` | Sonnet 5.5 · medium | `frontend/src/{app,state,components/ui,data,stats,api}/` (not `schema.d.ts`) | Shell, tokens, API client, import, grid, pipeline editor, stats panel, project UI |
| `ui-charts` | Sonnet 5.5 · high | `frontend/src/charts/{core,config,plots/basic}/`, `charts/index.ts`, `frontend/src/export/` | Wrapper, registry, config panel, primitives, MVP §4 and §8, basic charts, chart export |
| `ui-dashboard` | Sonnet 5.5 · high | `frontend/src/dashboard/` | MVP §6, §7, share/embed/report views |
| `qa` | Sonnet 5.5 · medium | `frontend/e2e/`, `scripts/`, `samples/`, CI config | Fixtures, generator, benchmarks, e2e, axe, contract drift |
| `plots-distribution-relationship` | Sonnet 5.5 · high | `plots/{distribution,relationship}.py`, `charts/plots/{distribution,relationship}/` | Plots.md §2–3 |
| `plots-timeseries` | Sonnet 5.5 · high | `plots/timeseries.py`, `charts/plots/timeseries/` | Plots.md §4 |
| `plots-composition-specialized` | Sonnet 5.5 · medium | `plots/{composition,specialized}.py`, `charts/plots/{composition,specialized}/` | Plots.md §5, §9 |
| `plots-geo` | Sonnet 5.5 · medium | `plots/geo.py`, `charts/plots/geo/` | Plots.md §7 |
| `plots-multivariate-econometric` | Opus 5.5 · high | `plots/{multivariate,econometric}.py`, `charts/plots/{multivariate,econometric}/` | Plots.md §6, §8 |
| `chores` | Haiku 5.5 · low | only the files the lead names | Mechanical tasks |

Backend paths are relative to `backend/src/viz/`, with tests mirrored under `backend/tests/`. Frontend `charts/` paths are under `frontend/src/`. Plot modules register themselves: the backend discovers them via `pkgutil`, the frontend via `import.meta.glob`. Plot agents therefore never edit a shared registry file.

**How agents work**
1. Before coding, read this file, the requirement sections in scope, and the handoff notes in `docs/STATUS.md`.
2. Write only in paths you own. If you need a change elsewhere, stub it on your side, leave a `TODO(<owner>):` note, and list it in your report.
3. Contracts, root config and new dependencies go through the lead. Contract changes are additive, and the lead runs `gen:api` afterwards.
4. **Plan before code on shared pieces:** the engine API, the jobs interface, the pipeline compiler, the chart-data protocol, the stat kernels and `StatResult`, the plot registry and config schema, the chart primitives, and the cross-filter bus. The agent returns a short plan and stops. The lead approves it and continues the agent with SendMessage.
5. **Write tests first** where the answer is known: statistics, transforms, data reduction.
6. **Definition of done:**
   - tests, lint and types pass
   - perf budgets hold if a data path was touched
   - the work is committed on the agent's branch
   - the final report lists STATUS.md items done, checks run and their results, requests to other owners, and open questions

   The lead then updates STATUS.md.
7. **Escalate instead of retrying.** After two failed attempts, the agent stops and reports. The lead re-runs the work one effort level higher, then on a stronger model (Sonnet 5.5 → Opus 5.5 → Fable 5.1). Use `max` effort only to debug stuck performance, numerical or concurrency problems.
8. Before adding a dependency, check that it ships wheels for every platform (Python) or what it adds to the bundle (frontend). Record notable additions in §2.

**Effort overrides the lead applies when delegating**, on top of each agent file's default:
- `reviewer` at `xhigh` for branches that touch `contracts/`, formula or SQL building in `transform/`, `engine/` concurrency, `auth/`, or the script runner.
- `data-core` at `high` for `engine/` concurrency and `jobs/` work.

## 10. Phases

**Phase 0: walking skeleton** (lead, sequential; no parallel work starts until this lands)
- `git init`, `.gitignore`, `.gitattributes`, and a first commit on `main` (worktree agents branch from it). Scaffold the uv project and Vite app, the CLI (`viz dev/build/serve`), config, data dir, SQLite + Alembic, and the auth stub.
- Contracts v1: Dataset, Pipeline (step union), ChartSpec, Predicate, chart-data meta, StatResult, Dashboard, Project, and a `PlotType` enum holding every Plots.md key. Wire up OpenAPI → TS generation.
- DuckDB engine module: process connection, cursor per request, timeouts, memory/temp settings.
- ECharts wrapper + registry with one plot (bar) working end-to-end.
- Generate `docs/STATUS.md` from MVP.md, Plots.md and Next.md.
- **Exit:** on both Windows and macOS, `uv run viz dev` → import a CSV → preview the grid → draw a bar chart → save the project → reload it.

**Wave 1** (parallel):
- data-core (§1)
- transform (§2 + chart-data protocol)
- stats (shared kernels, descriptive stats)
- platform (§10 backend)
- ui-data (shell, import, grid, pipeline editor, project UI)
- ui-charts (§4 config panel, §8 themes, Plots.md §1 basic charts, chart export)
- qa, once the first endpoints exist (fixtures, generator, bench harness)

**Exit:** a 10M-row CSV imports within budget; pipelines with join and pivot work; basic charts work with full config, themes and PNG/SVG export; save, autosave and undo work.

**Wave 2** (parallel, at most 4 plot agents at once):
- the five `plots-*` agents (Plots.md §2–9)
- stats (§5)
- ui-data (stats panel)

**Exit:** every Plots.md chart renders from `samples/` in both themes; statistics tests match SciPy/statsmodels.

**Wave 3** (parallel):
- ui-dashboard (§6, §7)
- platform (§9 share/embed/report, version history)
- ui-charts (drill-down, linked charts)

**Exit:** dashboards with cross-filtering, drill-down, sharing and reports work within the §6 budgets.

**Wave 4: MVP hardening.** Meet the perf budgets at 10M rows; e2e flows; a11y pass; smoke tests on Windows and macOS; ship the wheel with the built frontend so end users need only `uv`; Dockerfile + optional compose (Postgres) for AWS parity.

**Iteration 2 (Next.md):**
- §11: SQL editor (CodeMirror + locked DuckDB connection). Python/R custom scripts (subprocess runner, Arrow I/O; R via `Rscript` + the arrow package when installed). Custom viz plugins (external ES-module renderers + a Python entry-point group `viz.plots`). Forecasting (statsmodels ETS/ARIMA/SARIMAX). Anomaly detection (IsolationForest, STL residuals). Clustering/PCA exposed as transforms that add columns. Real-time streaming (WebSocket ingest → in-memory ring table → ECharts `appendData`). Geospatial analysis (DuckDB spatial extension; deck.gl + MapLibre for basemaps and large point layers).
- §12: command palette and chart search (cmdk), keyboard shortcuts (tinykeys), chart recommendations (registry `recommend()` scoring on column semantics and cardinality), contextual help and interactive tutorials (driver.js), screen-reader pass (ARIA descriptions, table fallback), responsive layouts, performance pass.

## 11. AWS path (later; don't build it now, don't block it)
- One container image. Stateless app tasks on ECS Fargate behind an ALB. S3 for sources and Parquet (DuckDB httpfs, with `CREATE SECRET` using the IAM credential chain). RDS Postgres for metadata. Cognito/OIDC behind `auth.current_user`. Ephemeral task storage for DuckDB temp. Jobs move to SQS + a worker task using the same `jobs` interface. ElastiCache only if multi-instance caching is needed.
- To keep this path open: no local-path assumptions outside `storage/`, no process-global user state, all config via `VIZ_*` env vars.

## 12. Gotchas (found while building; add to this list when you hit a new one)

**DuckDB 2.0 Python API**
- `relation.arrow()` returns a streaming `RecordBatchReader`, not a Table. Use `.to_arrow_table()`.
- Relations have no `fetchone()`. Use `.fetchall()` or `.limit(1).fetchall()`.
- A cursor refuses a new query while a result is still open. Get a fresh cursor per query via `engine.cursor()`, and consume the result inside the `with` block.
- `cur.sql(query, params=[...]).to_arrow_table()` is the efficient path. Arrow output from `cur.execute(...)` makes an extra copy.
- `USING SAMPLE` is applied **before** `WHERE`. To sample filtered rows, sample a filtered subquery.
- **Internal error that poisons the whole database (2.0.0):** a filter over a window ordered by the *virtual* `file_row_number` column, e.g. `QUALIFY row_number() OVER (... ORDER BY file_row_number)`. This was seen on pyarrow-written files with 3+ columns. After it, every query in the process fails. Read the row number explicitly with `read_parquet(path, file_row_number = true)`, which is safe. The engine must also recreate its DuckDB instance after a `duckdb.FatalException`.
- A single NaN makes `avg` return NaN. Treat NaN and ±inf as missing (`CASE WHEN isfinite(x) THEN x END`). On degenerate input (std with n<2, skewness with n<3, kurtosis with n<4, a constant column), DuckDB returns NULL rather than NaN.
- `skewness`/`kurtosis` match SciPy with `bias=False` (G1 and excess G2). `quantile_cont` is NumPy `linear` (R type 7); `quantile_disc` is NumPy `inverted_cdf`.
- DuckDB finalizes each quantile aggregate on one thread, so many quantiles in one SELECT run serially. Unpivot to (field, value) and GROUP BY field to parallelize them.
- `quantile_disc` with a long list of probabilities is O(n·m). For ECDF/Q-Q grids, use a `row_number()` window instead.
- `query_progress()` returns -1 from Python, so job progress is reported by stage, plus byte or batch counts.
- `SET TimeZone` is session-scoped, so new cursors don't inherit it. Use `SET GLOBAL TimeZone='UTC'` on every instance. The engine runs in UTC so results don't depend on the server's zone.
- The CSV sniffer rejects files with mixed CRLF/LF line endings unless `strict_mode=False`.
- `ORDER BY <non-unique> LIMIT/OFFSET` is not stable between queries, so pages repeat and skip rows. Always add the row number, read as an explicit column, as the last sort key.
- `cursor.interrupt()` on a closed cursor raises, and an interrupt that arrives between statements is lost. The engine handles both cases; never call `interrupt()` directly.
- Python `read_csv` accepts only UTF-8. Non-UTF-8 files are transcoded into `sources/`, even above the copy threshold.
- Fetching TIMESTAMPTZ with `fetchall()` needs pytz, which isn't installed. Use Arrow, or cast to VARCHAR.
- DECIMAL reaches the browser as unscaled integers and TIME as microseconds. Cast them in chart-data output: DECIMAL and HUGEINT to DOUBLE, TIME to VARCHAR.

**SQLGlot 30**
- `exp.Bracket` shifts list indexes by one when generating DuckDB SQL. Use `list_extract` through `exp.Anonymous`.

**Python tooling**
- `ruff format --check` also formats Python code blocks inside Markdown files. `docs/` is excluded in `pyproject.toml`; any other Markdown with Python blocks must be formatted.
- mypy types `int ** float` as `Any`; use `math.pow` or `math.cbrt`.

**Metadata DB**
- Optimistic concurrency needs a conditional write: `UPDATE ... WHERE id = ? AND revision = ?`, with a 409 when no row matches. Reading the revision first and comparing in Python loses edits under concurrent saves.
- Never use an id taken from an imported file (`.vizproj`, manifests) as a path component or a catalog key. Windows and macOS paths are case-insensitive and normalise `.` and trailing spaces. Mint a new id instead.

**SQLGlot 30**
- The base expression type is `exp.Expr`; `exp.Expression` is a subclass. Annotate with `exp.Expr`.

**Contracts and OpenAPI**
- Give `id` fields no default; whoever creates an object (client or server) passes `new_id()` / `crypto.randomUUID()`. A `default_factory` id makes the field optional in TypeScript.
- openapi-typescript marks a field required when its schema has a `default`. A `default_factory` emits no default, so those fields stay optional.
- Server-only response models (e.g. `ChartDataMeta`) have no defaults, so every field is required on the frontend.
- FastAPI runs with `separate_input_output_schemas=False`: one schema per model.
- The server fills defaults when it saves a document (e.g. `agg: null`). Compare round-trips on the fields you set, not whole objects. The same applies to Arrow `viz.meta` dicts: an added optional field breaks a whole-dict assert.
- Each contract model name must be unique across `contracts/`: OpenAPI keys schemas by class name. Shared small types (`SortKey`, `SourceRef`, `Scalar`) live in `base.py`.

**Frontend**
- TypeScript 7 is the new natively compiled release, and openapi-typescript 7.x still requires TypeScript 5 as a peer dependency. Stay on TS 5 until that changes.
- Tailwind v4 skips gitignored files when it scans for class names. Anchor `.gitignore` patterns to the root (`/data/`, not `data/`); an unanchored pattern hid `frontend/src/data/` from both Tailwind and git.
- apache-arrow returns dictionary-encoded columns for repeated strings and DuckDB enums. Look through `DataType.isDictionary` to the value type.
- `int64` arrives in the browser as `BigInt`. Convert with `toJsValue()`, and send chart measures as DOUBLE from SQL.
- ECharts draws axis titles and labels on the canvas, so browser tests can't find them as text. Assert on DOM state (selects, table, footer) and look at screenshots.
- Inside a CSS grid, use `minmax(0,1fr)` for the main column so wide children (the data grid) scroll inside their own box instead of stretching the page.

**Windows**
- Running `viz build` while `viz serve` is running can fail to empty `static/`, because Windows locks files that are open. Stop the server or retry.
- Consoles default to cp1252, so keep CLI output ASCII (no `→`).
- `python` may point at the Microsoft Store stub; use `uv run python` (or `py`).
