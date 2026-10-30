# Lead handoff: Wave 1 (2026-10-30)

Read this together with CLAUDE.md and docs/STATUS.md to resume as lead. Wave 1 is not closed yet.

## Merged on main (all checks green at fafdfb1 / aa9123e)
- **Merged:** platform (+ follow-up), stats (+ follow-up), data-core (Wave 1 + follow-up round 2), ui-charts (+ follow-up), ui-data (Wave 1, staged wizard, jobs indicator, single staging), transform (4 review rounds; B4 closed).
- **Last checks on main:**
  - pytest: 696 passed, 6 skipped
  - ruff, mypy: clean
  - vitest: 329 passed
  - biome, build: clean
- **Lead commits:**
  - RequestScopeMiddleware wired in `main.py`.
  - psutil dev dependency.
  - Playwright + axe devDeps and the `e2e` script.
  - CLAUDE.md:
    - describe budget applies ungrouped;
    - formula growth rule plus accepted local risks (wide inputs, row multiplication via joins/unpivot/union, per-vector peak).

## In flight: branches not yet merged
Each builder commits on `worktree-agent-<id>` (worktree in `.claude/worktrees/agent-<id>`). The agent ids below can be continued with SendMessage only from the session that spawned them. From a new session, read the branch and its last commit, or start a fresh agent with the brief in `.claude/agents/<name>.md`.

| Agent | Branch id | State | Next action for lead |
|---|---|---|---|
| data-core | af46922c273834353 | **Round 4 done, not reviewed: 1a15848** (on top of round 3, 8fe68c7 and 9b7f5a9).<br>**Checks:** pytest 369 passed, 1 skipped; ruff and mypy clean.<br>**Changes:**<ul><li>`.txt` is auto-detected as CSV again.</li><li>The `?kind=` chosen at staging is remembered in a `.kind` sidecar.</li><li>A staged refresh falls back to the dataset's kind, with a synchronous 415 on mismatch.</li><li>Uploads are marked in use at submit, and `JobRunner.submit(on_done=)` releases them.</li><li>The sweep skips uploads in use.</li><li>A job stops any running view build, and builds wait until no job cursor is live.</li><li>Cleanup paths suppress ConflictError.</li><li>The encoding threshold is now 15%.</li><li>New prose warning.</li></ul>**Perf (loaded machine):**<ul><li>During import + pending view build: max 91–93 ms (was 610).</li><li>Plain import: max 210–233 ms, against a 110 ms quiet control. Unconfirmed whether that's machine load; re-measure on a quiet machine.</li></ul>**OpenAPI:** `POST /datasets/staged` gains `?kind=` (run gen:api).<br>**Removed:** `viz.ingest.staging` (tracking moved to storage). The reviewer's `inuse.py` repro needs updating.<br>**Original round-4 brief:** | Review rounds 3+4 together (data-core reviewer, xhigh), including a quiet-machine during-import measurement. Then merge and run gen:api. Then tell ui-data: no kind picker is needed for `.txt`, an optional picker for files with no extension can wait, and DELETE staged returns 409 for queued jobs too. |
| data-core (old row) | af46922c273834353 | Round 4 brief, for reference: keep `.txt` → CSV; remember the staged `?kind=` (sidecar); staged-refresh kind fallback with a synchronous 415; the expiry sweep skips in-use uploads, marked at submit; no view builds while a job runs (the ~610 ms during-import stall); suppress ConflictError in cleanup paths; MAX_INVALID_SHARE 15%; multi-line prose warning. Round 3 (8fe68c7, 9b7f5a9) is approved conditionally and **not merged**. | When it reports: send it to the data-core reviewer (re-review at xhigh). Then merge, run gen:api (`POST /datasets/staged` gains `?kind=`), and brief ui-data on whether a kind picker is needed. |
| ui-data | a55c14286e9e8cf92 | **Done, not reviewed:** a6ce1ff (close-while-starting race, axe fixes, save-status role) and 7ecfab6 (pipeline editor on the real transform API, `pipelineApi` flag removed, inline step errors, materialize, "New chart from" pipeline, group-by default fix). Agent's browser check: filter → calc → group-by previews; formula error range shown; join + pivot (4 rows); materialize; chart from the pipeline in dark mode; reload keeps steps; 0 console errors. Not exercised: the "longer than 64 KB" guard message. vitest 340 passed. | Review a6ce1ff + 7ecfab6 together (ui-data reviewer, high), then merge. That satisfies exit criterion 2 once confirmed by the lead smoke test. Then tell qa to turn on the pipeline e2e fixmes. The accessible names for them are in ui-data's report: "Add step", "New pipeline", "Enable step N, <label>", "Materialize now", the "Pipeline preview data grid" region, and `[data-step-status=error]`. |
| stats | ad503027e5fcb4228 | **Done, not reviewed:** 3b4be26.<ul><li>`stats/source.py` uses `resolve_source`; describe on pipelines works.</li><li>The new `data_errors()` context turns transform user errors and the 64 KB guard into 422s.</li><li>A missing pipeline returns 404 (was 501).</li><li>pytest 699 passed, 6 skipped.</li></ul>**Perf concern:** in-app HTTP describe with modes, ungrouped, is p50 4.1 s and max 4.8 s, **over the 3 s budget**. Pipeline describe is 5.8 s. The agent suspects the app engine's lower `memory_limit` (12.5 GiB vs DuckDB's 18.7 GiB default) makes the modes UNION ALL spill; unconfirmed. Suggested fix: run mode branches sequentially. | Self-review the small diff, then merge. Decide on the describe budget: open a perf task for stats (sequential modes, check spilling with `EXPLAIN ANALYZE`) before Wave 2's stats panel, or carry it into Wave 4. |
| qa | a635bea14bed4f784 | **Review fixes done, not re-reviewed: 4174209** (on main merge 772340c).<ul><li>All of B1, S1–S7 and N1–N11 fixed.</li><li>For S6 it used an anchored regex instead of `exact: true`, because the picker's accessible name includes a timestamp.</li><li>The axe baseline holds 4 ui-data pairs. Remove them once a6ce1ff merges.</li></ul>**Checks:** e2e 73 passed ×2; drift check passes; bench --quick passes.<br>**Full 10M bench (idle), exit 0:**<ul><li>Import 5.75 s.</li><li>Describe p95 2.82 s.</li><li>The new `line` payload row passes.</li><li>With real cache-busting, the concurrency numbers rose: dashboard p95 0.57 s idle and **0.79 s during import (close to the 1 s budget)**; 12 concurrent queries in 4.48 s, peak RSS 9% of RAM.</li></ul>**Note for data-core:** the e2e server log shows a DuckDB IOException ("No files found … v1.parquet") when a dataset is deleted while the page still fetches it. Make that a clean 404.<br>**Original fix list:** | Quick re-check (qa reviewer), then merge. Flag the 0.79 s during-import dashboard number for Wave 4. |
| qa (old row) | a635bea14bed4f784 | Fix list, for reference: B1 setup-uv tag `@v10` → `@v10.3.3`; S1 known-failure ceilings (1.5×); S3 during-import rounds cover the whole import; S4 global cache-bust counter + assert `meta.cached` false; S5 empty table crash; S6 project picker exact match; S7 axe baseline; nits N1–N11. | When it reports: re-review (qa reviewer), then merge. Then have qa turn on the pipeline e2e fixmes after ui-data's pipeline wiring lands. |

Reviewers used, reachable by SendMessage only from the original session:

| Reviewer | Used for | Effort |
|---|---|---|
| abca67b9ee33b0f0d | data-core | xhigh |
| a033632c39102ffaf | transform | xhigh |
| a0320ecd9fb0ae74c | stats | |
| aac5e9ca17aae55f9 | ui-data | |
| a1b08f9ea2b5f4aeb | qa | |

Their repro scripts live in the session scratchpad: `dc_review/`, `transform-review/`, `review-stats/`, `uidata-review4/`, `qa-review/`. These are gone in a new session; reviewers can regenerate them.

## Wave 1 exit check (CLAUDE.md §10): remaining
1. **A 10M-row CSV imports within budget.** Done: 5–8 s against a 60 s budget.
2. **Pipelines with join and pivot work.** The backend is done; the UI is waiting on ui-data's pipeline wiring, verified in the browser.
3. **Basic charts work with full config, themes and PNG/SVG export.** Done (ui-charts; the lead smoke test passed).
4. **Save, autosave and undo work.** Done (platform + ui-data; qa e2e covers it).
5. After all merges, run:
   - the full test suite;
   - `npm --prefix frontend run e2e` (and `E2E_LARGE=1`);
   - `uv run python scripts/check_contract_drift.py`;
   - `uv run python scripts/bench.py` (full 10M, quiet machine);
   - a lead browser smoke test (import → grid → pipeline join + pivot → chart → save → reload → dark).

   Then update STATUS.md and the CLAUDE.md status line, and report to the user.

## Known items carried to Wave 4 (tracked in STATUS)
- First sorted page cold: ~190–340 ms. Cold deep pages before their view is built: 1.8–2.7 s sorted, 0.6–0.7 s filtered range.
- Free-text search: ~620–660 ms (accent folding deferred).
- Raw-timestamp M4 line: ~250 ms quiet, over 300 ms under load.
- Grouped describe with modes: 6.5–8 s. Ungrouped describe: 2.6–3.0 s, right at the budget.

## Before Wave 2
- Add `echarts-custom-series` (frontend dependency; check its bundle cost).
- Add the `OverlayResult.group` contract (additive).
- Notes for the plot agents:
  - 2-D KDE returns cell averages on coarse grids;
  - categorical modes come back as text;
  - the engine runs in UTC;
  - chart-data column naming is in docs/plans/transform.md.

## Housekeeping and open questions for the user
- 24 stale `viz-e2e-*` folders (about 3 MB each) in `%LOCALAPPDATA%\Temp` from early qa runs are safe to delete. qa wasn't permitted to delete them.
- Unanswered from earlier sessions:
  - record the "web-first, optional desktop shell later" decision?
  - add a localhost Host-header/token check?

## Environment notes
- uv isn't on the bash PATH. Use `export PATH="/c/Users/Ashwin/AppData/Local/Microsoft/WinGet/Packages/astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe:$PATH"`.
- Use `uv run python`, not `python`.
- Write multi-line Python to files, not heredocs.
