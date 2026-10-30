# data-core plan (Wave 1)

Scope: MVP §1, data exports (§9), `engine/` concurrency, `jobs/`. Existing `Engine.cursor(key)` and
`Engine.query_arrow(sql, params, *, key)` keep their signatures and behaviour; everything below is additive.

## 1. `engine/` public API (`from viz.engine import ...`)
```python
class Engine:  # new kw-only ctor args: cache_mb=256, process_workers=2, blas_threads=None, job_threads=None, prewarm=False
    def cursor(self, key: str | None = None) -> ContextManager[DuckDBPyConnection]          # unchanged
    def query_arrow(self, sql, params=None, *, key=None) -> pa.Table                       # unchanged
    def query_arrow_cached(self, sql: str, params: Sequence[object] | None = None, *,
                           versions: Sequence[str], key: str | None = None) -> CachedTable  # (table, cached: bool)
    def background_cursor(self, key: str | None = None) -> ContextManager[DuckDBPyConnection]
    def interrupt(self, key: str, reason: str = "cancelled") -> bool   # True if a query was running
    def progress(self, key: str) -> float | None                       # DuckDB query_progress, 0-1, None if unknown
    cache: ResultCache          # also usable directly for JSON results (stats)
    pool: ProcessPool           # created lazily; from_settings() pre-warms it
def version_token(dataset_id: str, n: int) -> str                  # "ds:<id>@v<n>"; pipelines combine tokens + spec hash
async def run_cancellable(request: Request, engine: Engine, key: str, fn: Callable[[], T]) -> T
```
- **Interactive cursors** (unchanged): semaphore `max_concurrent_queries`, timeout, same-key supersede.
- **`background_cursor`** is for jobs: no semaphore slot (jobs are capped by the job executor), no timeout,
  interruptible via `interrupt(key)`. If `job_threads` is set it uses a second in-memory DuckDB with that many
  threads and its own `memory_limit` share (CLAUDE.md §6 fallback); off by default, the bench decides.
- **`ResultCache`** (`engine/cache.py`): `cachetools.LRUCache` bounded by bytes (`getsizeof=table.nbytes`), behind a
  lock. Key = sha256(sorted versions, sql, params). `get_or_compute(versions, key, compute, nbytes) -> (value, hit)`;
  `evict(prefix)` drops entries for a deleted dataset. No single-flight: two identical concurrent misses compute twice.
- **`ProcessPool`** (`engine/pool.py`): `ProcessPoolExecutor(workers, mp_context=spawn, initializer=_init_worker)`.
  The initializer sets `OMP/OPENBLAS/MKL_NUM_THREADS` and `threadpoolctl.threadpool_limits(blas_threads)` (default
  `cores // workers`), then imports numpy/scipy/statsmodels/sklearn. `prewarm()` submits one no-op per worker without
  blocking startup. `run(fn, *args, timeout_s=None) -> T` blocks (call from the threadpool); `submit()` returns a Future.
  `fn` must be top-level and picklable; pass Parquet path + SQL or a capped Arrow table. On timeout or
  `BrokenProcessPool` the pool is terminated and recreated (a running process task can't be cancelled otherwise),
  raising `QueryInterruptedError("timed out")`.
- **`run_cancellable`** (`engine/cancel.py`): for `async def` endpoints. Runs `fn` in the threadpool and polls
  `request.is_disconnected()` every 100 ms; on disconnect calls `engine.interrupt(key)`. transform's chart endpoint
  and my rows endpoint use it, so aborted fetches (TanStack `signal`) stop their DuckDB query.

## 2. `jobs/` interface
```python
JobHandler = Callable[[JobContext, P], JobResult]          # P: a Pydantic params model (serializable for a queue)
def job_handler(kind: JobKind, params: type[P]) -> Callable[[JobHandler], JobHandler]   # registry decorator
class JobContext:
    id: str; engine: Engine; storage: LocalStorage; settings: Settings
    def session(self) -> Session                              # fresh metadata-DB session
    def progress(self, fraction: float | None, message: str | None = None) -> None   # throttled to 4 events/s
    def check_cancelled(self) -> None                         # raises JobCancelledError; call between chunks
    def cursor(self) -> ContextManager[DuckDBPyConnection]    # engine.background_cursor(f"job:{id}")
class JobRunner:  # Protocol; InProcessJobRunner now
    def submit(self, kind: JobKind, params: BaseModel, *, title: str) -> Job
    def get(self, job_id: str) -> Job;  def list(self, *, active: bool = False) -> list[Job]
    def cancel(self, job_id: str) -> Job      # queued: dropped; running: flag + engine.interrupt("job:<id>")
    async def watch(self, job_id: str) -> AsyncIterator[Job]   # snapshot now, then on each seq change, ends when terminal
    def wait(self, job_id: str, timeout_s: float) -> Job       # tests / sync callers
    def close(self) -> None                                    # cancels running jobs, shuts the executor
```
- Own `ThreadPoolExecutor(max_concurrent_jobs=1..2)`, never the request threadpool. State in memory, pruned after
  `job_retention_s`. While a job's DuckDB statement runs, a ticker reads `engine.progress("job:<id>")` (DuckDB
  returned -1 for a `range()` scan in a quick check; verify on Parquet/CSV writes, else stage-level progress only).
  Byte-level progress for source copy/transcode; batch-level for Excel export.
- **SSE** `GET /api/jobs/{id}/events` (FastAPI `EventSourceResponse`): `event: job`, `id: <seq>`, `data: <Job JSON>`.
  First event is the current snapshot; one event per change; stream closes after succeeded/failed/cancelled;
  `: ping` comment every 15 s. Each event is a full snapshot, so reconnecting needs no replay.
- **AWS:** a `QueueJobRunner` implements the same Protocol: `submit` writes a job row (Postgres) and an SQS message
  `{jobId, kind, params}`; a worker task looks up the same `job_handler` registry; progress/cancel flags go through
  the job row, which `watch` polls. Handlers therefore take serializable params, never closures.

## 3. HTTP endpoints (all under `/api`, all behind `current_user`)
| Method · path | Request | Response |
|---|---|---|
| `POST /datasets/staged` | multipart `file` (size limit, type sniff) | 201 `StagedFile` |
| `DELETE /datasets/staged/{stagedId}` | | 204 |
| `POST /datasets/preview` | `ImportPreviewRequest` (stagedId or path) | Arrow IPC ≤1k rows, meta `ImportPreviewMeta`; 422 with readable detail on parse errors |
| `POST /datasets/import` | `ImportRequest` | 202 `Job` (result.datasetId) |
| `POST /datasets/{id}/refresh` | `RefreshRequest` | 202 `Job` (new version; schema changes as warnings) |
| `PATCH /datasets/{id}` | `DatasetUpdate` | `Dataset` (rename) |
| `DELETE /datasets/{id}?force=false` | | 204; 409 if a job is running on it or a project lists it (unless force) |
| `POST /datasets/{id}/rows` | `RowsRequest` (sort, filters, search, columns, version) | Arrow IPC, meta `RowsPageMeta` |
| `POST /datasets/{id}/values` | `ColumnValuesRequest` | `ColumnValues` (distinct values + counts, for filter dropdowns) |
| `GET /jobs?active=` · `GET /jobs/{id}` · `POST /jobs/{id}/cancel` | | `list[Job]` · `Job` · `Job` |
| `GET /jobs/{id}/events` | | `text/event-stream` (above) |
| `POST /exports` | `ExportRequest` | 202 `Job` (result.export) |
| `GET /exports/{exportId}/download` | | file (`Content-Disposition`); 404 once expired |

Kept unchanged for compatibility: `GET /datasets`, `GET /datasets/{id}`, sync `POST /datasets/upload` (gains
optional form fields `kind`, `options` as JSON) and `POST /datasets/import-path`, `GET /datasets/{id}/rows`.

## 4. Contract additions (additive; lead adds them)
```python
# contracts/dataset.py
class JsonFormat(StrEnum):
    AUTO = "auto"; ARRAY = "array"; NEWLINE_DELIMITED = "newline_delimited"

class ImportOptions(Contract):  # new optional fields after the existing ones
    skip_rows: int | None = None            # CSV: lines before the header; Excel: rows after header_row
    quote: str | None = None
    escape: str | None = None
    decimal_separator: str | None = None    # "." or ","
    null_strings: list[str] | None = None
    date_format: str | None = None          # strptime format, e.g. "%d/%m/%Y"
    timestamp_format: str | None = None
    column_types: dict[str, str] | None = None   # column -> DuckDB type; checked against an allowlist
    all_varchar: bool | None = None
    json_format: JsonFormat | None = None
    json_path: str | None = None            # dotted key path to the records array, e.g. "data.items"

class DatasetVersion(Contract):  # new optional fields
    source_path: str | None = None          # data-dir-relative copy this version was built from
    options: ImportOptions | None = None    # options used for this version
    size_bytes: int | None = None           # Parquet file size

class StagedFile(Contract):
    staged_id: str
    file_name: str
    kind: SourceKind
    size_bytes: int
    sheets: list[str]                       # Excel sheet names; [] for other kinds

class ImportPreviewRequest(Contract):       # exactly one of staged_id / path (model_validator)
    staged_id: str | None = None
    path: str | None = None
    kind: SourceKind | None = None
    options: ImportOptions = Field(default_factory=ImportOptions)
    limit: int = Field(default=1000, ge=1, le=1000)

class ImportPreviewMeta(Contract):
    kind: SourceKind
    detected: ImportOptions                 # what auto-detection chose (delimiter, header, quote, formats, sheet)
    sheets: list[str]
    columns: list[Column]                   # profiled on the preview rows only
    rows_shown: int
    estimated_rows: int | None              # exact for Excel, byte-based estimate for CSV/JSON
    warnings: list[str]

class ImportRequest(Contract):              # exactly one of staged_id / path
    staged_id: str | None = None
    path: str | None = None
    name: str | None = None
    kind: SourceKind | None = None
    options: ImportOptions = Field(default_factory=ImportOptions)

class RefreshRequest(Contract):
    staged_id: str | None = None            # re-upload; omit to re-read source.originalPath
    options: ImportOptions | None = None    # omit to reuse the current options

class DatasetUpdate(Contract):
    name: str | None = None

class SortKey(Contract):
    field: str
    desc: bool = False

class RowsRequest(Contract):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=200, ge=1)
    sort: list[SortKey] = Field(default_factory=list)
    filters: list[Predicate] = Field(default_factory=list)
    search: str | None = None               # case-insensitive contains across text columns
    columns: list[str] | None = None
    version: int | None = None              # default: current version

class ColumnValuesRequest(Contract):
    field: str
    search: str | None = None
    filters: list[Predicate] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=1000)

class ValueCount(Contract):
    value: Scalar
    count: int

class ColumnValues(Contract):
    field: str
    values: list[ValueCount]
    truncated: bool

# contracts/chartdata.py: RowsPageMeta gains
    dataset_rows: int | None = None         # rows in the version; totalRows counts rows after filters/search
    version: int | None = None

# contracts/jobs.py (new)
class JobKind(StrEnum):
    IMPORT = "import"; REFRESH = "refresh"; MATERIALIZE = "materialize"; EXPORT = "export"

class JobStatus(StrEnum):
    QUEUED = "queued"; RUNNING = "running"; SUCCEEDED = "succeeded"; FAILED = "failed"; CANCELLED = "cancelled"

class ExportFormat(StrEnum):
    CSV = "csv"; XLSX = "xlsx"

class ExportFile(Contract):
    export_id: str
    file_name: str
    format: ExportFormat
    size_bytes: int
    row_count: int
    download_url: str                       # /api/exports/{id}/download locally; presigned S3 URL on AWS
    expires_at: datetime

class JobResult(Contract):                  # server-only: no defaults
    dataset_id: str | None
    version: int | None
    pipeline_id: str | None
    export: ExportFile | None
    warnings: list[str]

class Job(Contract):                        # server-only: no defaults
    id: str
    kind: JobKind
    title: str
    status: JobStatus
    progress: float | None                  # 0-1; None while indeterminate
    message: str | None                     # current stage, e.g. "Converting to Parquet"
    seq: int                                # bumps on every change; SSE event id
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    result: JobResult | None

class ExportRequest(Contract):
    source: SourceRef
    format: ExportFormat
    version: int | None = None              # dataset sources only
    filters: list[Predicate] = Field(default_factory=list)
    sort: list[SortKey] = Field(default_factory=list)
    columns: list[str] | None = None
    file_name: str | None = None            # default "<source name>.<ext>"
    delimiter: str | None = None            # CSV only; default ","
```
Add `Job`, `StagedFile`, `ImportPreviewMeta`, `ExportRequest` to `EXPORTED_MODELS`.

## 5. Implementation notes
- **Readers:** CSV/TSV via DuckDB `read_csv` (relation API kwargs, never string-built SQL), `sniff_csv` for the
  `detected` options. Encodings other than utf-8/utf-16/latin-1 are transcoded to UTF-8 while copying into `sources/`
  (no network `encodings` extension). JSON via `read_json` (auto/array/NDJSON); `json_path` unnests the nested
  array. Excel via fastexcel (`sheet_names`, `load_sheet(header_row, skip_rows, n_rows)`, `total_height`) → Arrow →
  DuckDB. Headers trimmed and de-duplicated.
- **Writes are atomic:** `storage.atomic_write(rel)` yields a temp path in the same dir and renames on success;
  a cancelled or failed job leaves no partial version. Refresh = next `v<n>`, earlier versions untouched.
- **Storage** grows `staged_dir`, `exports_dir`, `atomic_write`, `delete_dataset`, `download_url`, expiry sweeps
  (staged 24 h, exports 1 h). Still the only module that knows the disk layout.
- **Rows:** SQLGlot query over `read_parquet` with `where_sql` (transform's helpers, read-only), `ORDER BY`, LIMIT/
  OFFSET; filtered count cached via `query_arrow_cached`. Measure deep pages at 10M rows; if over 150 ms, page by
  `file_row_number` ranges (unsorted) and revisit sorted deep pages.
- **Exports:** CSV by DuckDB `COPY (query) TO ... (HEADER, DELIMITER)`. Excel by xlsxwriter `constant_memory`,
  `strings_to_formulas/urls/numbers=False` (no formula injection), batches from a DuckDB record-batch reader,
  `remove_timezone`. Above 1,048,575 data rows: write the first 1,048,575 and add a warning suggesting CSV.
  Pipeline sources need transform's resolver (below); until then 501.

## 6. For other agents
- **transform:** use `query_arrow_cached(..., versions=[version_token(...)...])` for chart data and
  `run_cancellable` in the charts endpoint. Materialize = register `@job_handler(JobKind.MATERIALIZE, YourParams)`
  and write via `ctx.cursor()` + `storage.atomic_write`. Please expose
  `resolve_source(db, storage, ref: SourceRef) -> (exp.Expr relation, versions: list[str], name: str)` so exports
  work for pipelines (I'll call it; `TODO(transform)` until it exists).
- **stats / plots:** `engine.pool.run(fn, ...)` for pure-Python work; `engine.cache.get_or_compute` for results.
- **ui-data:** wizard = `POST /datasets/staged` (or a path) → `POST /datasets/preview` on each option change →
  `POST /datasets/import` → follow `/jobs/{id}/events` → `GET /datasets/{id}`. Grid uses `POST /datasets/{id}/rows`.
- **lead / platform:** new `Settings` fields `cache_mb=256`, `process_workers=2`, `blas_threads: int|None`,
  `max_concurrent_jobs=1`, `job_duckdb_threads: int|None`, `job_retention_s=3600`, `export_retention_s=3600`,
  `staged_retention_s=86400`. `main.py` lifespan: create `InProcessJobRunner` → `app.state.jobs`, close it before
  the engine; `Engine.from_settings` pre-warms the pool. `api/__init__.py`: include `jobs` and `exports` routers.
  I'd like approval to make these few additive lines on my branch rather than stubbing.

## 7. Tests (written first where answers are known) and order
1. **engine:** cache hit/miss, byte-bound eviction, `evict(prefix)`; `interrupt(key)`; background cursor skips the
   semaphore and timeout; pool runs a top-level fn under spawn, pre-warm, timeout recycles the pool;
   `run_cancellable` with a fake disconnecting request.
2. **storage/jobs:** atomic write leaves nothing on failure; job lifecycle and progress order; failure → `failed` +
   message; cancel queued; cancel running interrupts a slow DuckDB query; concurrency cap; SSE stream via TestClient
   ends at a terminal event; retention pruning.
3. **ingest:** CSV options (`;`, no header, skip_rows, decimal `,`, quote, null strings, BOM, cp1252 transcode),
   TSV, JSON array/NDJSON/json_path, Excel (fixture generated with xlsxwriter: sheets, selection, header_row);
   preview limits, `detected` and estimates; readable 422s; Excel-lock `PermissionError` message; paths with spaces
   and non-ASCII; refresh by path and by re-upload (v1 intact, schema-change warnings); rename; delete (+409).
4. **rows/values:** sort, each predicate op, search, column subset, totals vs datasetRows, version param.
5. **exports:** CSV round-trip with filters/columns/sort; Excel read back with fastexcel; truncation warning with
   the limit monkeypatched small; download + expiry; pipeline source → 501.
6. **perf** (opt-in, `VIZ_PERF=1`): generate 10M×20 CSV in a temp dir; import ≤ 60 s; grid first/deep/sorted/
   filtered pages ≤ 150 ms; page latency while an import job runs. Numbers go in the report.

Order: engine → storage → jobs + `api/jobs.py` → ingest (readers, staging, preview, import/refresh jobs, rename/
delete) → rows/values → exports → perf run and tuning.

## Open questions
- Copying multi-GB sources into `sources/` doubles disk use; keep the copy always (reproducible refresh, Excel locks)
  or skip it above a size threshold for path imports?
- No version pruning in Wave 1; every refresh keeps its Parquet. Add "keep last N versions" later?
- Multi-sheet import: one dataset per sheet, one job each (UI submits several). OK?

Answered by the lead: (a) copy uploads and path imports up to `VIZ_COPY_SOURCE_MAX_MB`, read larger
path imports in place; (b) no pruning in Wave 1; (c) yes.

## As built (implementation notes)
- Lead changes applied: shared `SortKey {field, descending, nullsLast}`; `ExportRequest.pipelines`;
  `cursor(key, generation)`; `storage.derived_rel/atomic_write`; `submit(..., dedupe_key)`;
  `catalog.register_dataset_bundle`; `engine.pool` is a `concurrent.futures.Executor`; BLAS caps in
  the pool initializer and once in the API process (`Engine.from_settings`).
- DuckDB's `query_progress()` reports -1 from Python even with the progress bar enabled, so job
  progress is stage-level plus bytes for source copy/transcode and batches for Excel export.
- DuckDB's Python `read_csv` accepts only UTF-8, so every non-UTF-8 text file is transcoded into
  `sources/` (even above `copy_source_max_mb`); detection falls back to cp1252.
- Sorted or filtered deep pages at 10M rows took 0.8-1.4 s, so `ingest/views.py` materializes a
  sorted/filtered view in the background after the first direct page (datasets >= 500k rows);
  later pages are `file_row_number` range reads (~25 ms). Views live in `temp/views/`, are
  LRU-limited (8), dropped with their dataset and wiped at startup.
- Sync `POST /datasets/upload` and `/import-path` now run the same import job and wait for it.

## After the xhigh review
- Engine: request owners (contextvar) with arrival tickets from one global counter
  (`next_generation()`), `cancel_owner`, conditional `cancel(key, generation)`; supersede at
  arrival; sticky interrupts and timeouts via a 50 ms reaper; interrupt/close under the engine
  lock; `FatalException` recreates the instance (503 for affected requests); `SET GLOBAL
  TimeZone='UTC'`. Jobs and view builds run on a background instance (cores/4 threads) by
  default (`VIZ_JOB_DUCKDB_THREADS=0` shares the main one).
- Sorted pages, views and exports order by the sort keys and then `file_row_number`
  (explicit `read_parquet(..., file_row_number := true)` column).
- Views: built only once a page past the first is requested, one build per dataset (newer
  supersedes), byte budget `VIZ_GRID_VIEWS_MB`, start after interactive queries go quiet.
- Imports roll back on failure (staged upload kept, `sources/<id>`/`datasets/<id>` removed);
  startup sweep for orphans; hourly expiry sweep; streamed multipart uploads.

## Handoff notes (follow-up round)
- **The engine runs in UTC** (`SET GLOBAL TimeZone='UTC'`), so TIMESTAMPTZ day/week/month
  bucketing is in UTC. transform may later need a display time zone for bucketing.
- Explicit generations advance the shared ticket counter (`observe_generation`);
  `cancel(key, generation)` is sticky for that generation. `RequestScopeMiddleware` (in
  `viz.engine`) gives every HTTP request an owner; the lead wires it in `main.py`.
- Encoding: the whole text file is scanned at import (preview: first 8 MB). Mostly-UTF-8
  files are read as UTF-8 with the bad bytes replaced (warned); cp1252 only when there is no
  valid multi-byte UTF-8 at all. The scan adds ~1 s per GB to imports.
- `receive_upload(request, storage, settings, *, max_bytes=None, accept=None,
  stage_dir=None) -> Upload` for other streaming uploads (platform's `.vizproj`).
