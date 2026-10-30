import { api, unwrap } from "./client";
import { postJson } from "./http";
import type { DescribeRequest, Pipeline, Predicate, SourceRef, StatResult } from "./types";

// stats endpoints (MVP §5). `/api/stats/describe` is real and merged (stats Wave 1). The rest
// (correlation, regression, hypothesis tests, distribution fitting, outliers, CIs) are the
// `stats` agent's Wave 2 work, built concurrently in a separate worktree and not yet on `main`.

/** POST /api/stats/describe -> StatResult; `values.summaries` is a list of FieldSummary. */
export async function describeFields(
  body: DescribeRequest,
  signal?: AbortSignal,
): Promise<StatResult> {
  return unwrap(await api.POST("/api/stats/describe", { body, signal }));
}

// ---- TODO(stats): Wave 2 tests (not merged yet; gated behind features().statsTests) ----
//
// The shapes below are hand-typed, not generated from schema.d.ts, because the lead has not
// published the Wave 2 stats contracts yet (no `/api/stats/{correlation,regression,...}` in
// openapi.json). They follow the StatResult conventions already established on `main` in
// backend/src/viz/stats/results.py (read there before changing this file):
//   - `values` keys are camelCase; curves are column-oriented ({x, y, lower, upper});
//     intervals use estimate/ciLow/ciHigh/ciLevel; tests use statistic/pValue/df; groups are
//     [{key, n, ...}]; probabilities are in [0, 1].
//   - every StatResult carries n, sampled, warnings, nTotal, nMissing (see StatResult in
//     backend/src/viz/contracts/stats.py / api/types.ts — that part IS the real contract).
// Endpoint paths are a provisional guess (`/api/stats/<test>`, one request shape per test, same
// request envelope as DescribeRequest: source + pipelines + filters). Replace this whole block,
// including the request/response types, once the `stats` agent's branch merges and `gen:api`
// emits real types — do not keep hand-typing guesses alongside generated ones.

/** The Wave 2 statistical tests MVP §5 asks for, beyond describe. */
export type StatTestKind =
  | "correlation"
  | "simple_regression"
  | "multiple_regression"
  | "t_test"
  | "anova"
  | "chi_square"
  | "distribution_fit"
  | "outliers"
  | "confidence_interval";

/** TODO(stats): provisional request envelope; fields narrow further per test kind. */
export interface StatTestRequest {
  kind: StatTestKind;
  source: SourceRef;
  pipelines?: Pipeline[];
  filters?: Predicate[];
  /** Field names the test operates on; meaning depends on `kind` (e.g. [x, y] for correlation). */
  fields: string[];
  /** Grouping field(s) for ANOVA / group comparisons. */
  groupBy?: string[];
  /** Test-specific knobs (e.g. confidence level, distribution family, outlier method). */
  params?: Record<string, unknown>;
}

/** TODO(stats): provisional label per test kind, for the UI's "coming soon" state. */
export const STAT_TEST_LABELS: Record<StatTestKind, string> = {
  correlation: "Correlation",
  simple_regression: "Linear regression",
  multiple_regression: "Multiple regression",
  t_test: "T-test",
  anova: "ANOVA",
  chi_square: "Chi-square test",
  distribution_fit: "Distribution fitting",
  outliers: "Outlier detection",
  confidence_interval: "Confidence interval",
};

/**
 * TODO(stats): runs one Wave 2 test. The endpoint does not exist on `main` yet, so this call
 * 404s until the `stats` branch merges; callers must keep it behind `featureOn("statsTests")`.
 */
export async function runStatTest(
  body: StatTestRequest,
  signal?: AbortSignal,
): Promise<StatResult> {
  return postJson<StatTestRequest, StatResult>(`/api/stats/${body.kind}`, body, signal);
}
