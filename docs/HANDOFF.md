# Lead handoff: Wave 1 (2026-10-30)

Read this together with CLAUDE.md and docs/STATUS.md to resume as lead. Wave 1 is not closed yet.

## Merged on main (all checks green at fafdfb1 / aa9123e; stats resolve_source follow-up also merged, 2026-10-30)
- **Merged:** platform (+ follow-up), stats (+ follow-up, + resolve_source/data_errors follow-up), data-core (Wave 1 + follow-up round 2), ui-charts (+ follow-up), ui-data (Wave 1, staged wizard, jobs indicator, single staging), transform (4 review rounds; B4 closed).
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
| data-core | af46922c273834353 | **MERGED into main** (merge commit on top of 95e7a83, 1a15848 content on top of round 3's 8fe68c7/9b7f5a9). Reviewed at xhigh: APPROVE WITH NITS, nothing blocking. Full suite pytest 722 passed/6 skipped, ruff/mypy clean on main post-merge. `?kind=` on `POST /datasets/staged` regenerated via gen:api and committed (775a0a9). | Done. Non-blocking follow-ups to pick up opportunistically: a corrupted `.kind` sidecar raises a bare `ValueError` (500) instead of degrading to `None` (`importer.py`, `preview.py`); four `LocalStorage` methods (`touch_staged`, `acquire_staged`, `release_staged`, `staged_in_use`) don't independently validate `staged_id` against the id pattern — defense-in-depth only, not currently reachable since every call site already validates. Still open: "plain import 210-233 ms vs 110 ms quiet control" on a loaded dev machine — needs a quiet-machine re-measurement to confirm whether it's real or noise. Tell ui-data: no kind picker is needed for `.txt`, an optional picker for files with no extension can wait, and DELETE staged returns 409 for queued jobs too. |
| ui-data | a55c14286e9e8cf92 | **Done, not reviewed:** a6ce1ff (close-while-starting race, axe fixes, save-status role) and 7ecfab6 (pipeline editor on the real transform API, `pipelineApi` flag removed, inline step errors, materialize, "New chart from" pipeline, group-by default fix). Agent's browser check: filter → calc → group-by previews; formula error range shown; join + pivot (4 rows); materialize; chart from the pipeline in dark mode; reload keeps steps; 0 console errors. Not exercised: the "longer than 64 KB" guard message. vitest 340 passed. | Review a6ce1ff + 7ecfab6 together (ui-data reviewer, high), then merge. That satisfies exit criterion 2 once confirmed by the lead smoke test. Then tell qa to turn on the pipeline e2e fixmes. The accessible names for them are in ui-data's report: "Add step", "New pipeline", "Enable step N, <label>", "Materialize now", the "Pipeline preview data grid" region, and `[data-step-status=error]`. |
| stats | ad503027e5fcb4228 | **MERGED into main** (merge commit on top of 9f5180e, 3b4be26 content). Reviewed at medium effort: APPROVE, no nits. pytest 699 passed/6 skipped, ruff/mypy clean on main post-merge. | Done. Perf concern carried to Wave 4 (see STATUS.md Wave 1 log, 2026-10-30 stats follow-up entry): describe-with-modes ungrouped measured 4.1s p50/4.8s max over HTTP in-app vs qa's 2.99s bench on the same endpoint — not a regression from this diff, open question is whether to fix before Wave 2's stats panel or defer to Wave 4. |
| qa | a635bea14bed4f784 | **MERGED into main** (b46ecf5). Reviewed at medium effort: APPROVE WITH NITS, nothing blocking. e2e 73 passed/6 skipped (placeholders) confirmed by the reviewer independently; contract-drift check passes. This merge brought qa's entire Wave 1 contribution onto `main` for the first time (samples/, scripts/bench.py, scripts/check_contract_drift.py, scripts/gen_large_dataset.py, scripts/gen_samples.py, frontend/e2e/*, .github/workflows/ci.yml — the earlier "on main merge 772340c" note referred to merging *main into the qa branch*, not qa into main). | Done. Nits to fix opportunistically, not blocking: one `KNOWN_ISSUES` ceiling's docstring is slightly inconsistent with its value; `bust()`'s counter has an undocumented (currently unreachable) exhaustion edge case; the new Playwright browser cache CI step doesn't yet gate the install step (cache doesn't save time as wired). Flag the 0.79 s during-import dashboard p95 (close to the 1 s budget) for Wave 4, per the full 10M bench qa ran before this round. The data-core 404-on-delete-during-fetch note below is still open for data-core. |
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
