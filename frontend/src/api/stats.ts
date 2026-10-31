import { api, unwrap } from "./client";
import type {
  AnovaRequest,
  ChiSquareRequest,
  ConfidenceIntervalRequest,
  CorrelationRequest,
  DescribeRequest,
  DistributionFitRequest,
  OutlierRequest,
  RankTestRequest,
  RegressionRequest,
  StatResult,
  TTestRequest,
} from "./types";

// stats endpoints (MVP §5). Every one returns a `StatResult` whose `values` bag is typed
// per-endpoint by the generated `*Values` schemas (see `valuesOf`).

/** POST /api/stats/describe -> StatResult; `values.summaries` is a list of FieldSummary. */
export async function describeFields(
  body: DescribeRequest,
  signal?: AbortSignal,
): Promise<StatResult> {
  return unwrap(await api.POST("/api/stats/describe", { body, signal }));
}

/** The statistical tests beyond describe, one per `/api/stats/<path>` endpoint. */
export type StatTestKind =
  | "correlation"
  | "regression"
  | "ttest"
  | "ranktest"
  | "anova"
  | "chisquare"
  | "fit"
  | "outliers"
  | "ci";

export const STAT_TEST_LABELS: Record<StatTestKind, string> = {
  correlation: "Correlation",
  regression: "Linear regression",
  ttest: "t-test",
  ranktest: "Rank test (Mann-Whitney, Wilcoxon, Kruskal)",
  anova: "ANOVA",
  chisquare: "Chi-square test",
  fit: "Distribution fitting",
  outliers: "Outlier detection",
  ci: "Confidence interval",
};

/** A test request tagged with the endpoint it goes to. */
export type StatTestCall =
  | { kind: "correlation"; body: CorrelationRequest }
  | { kind: "regression"; body: RegressionRequest }
  | { kind: "ttest"; body: TTestRequest }
  | { kind: "ranktest"; body: RankTestRequest }
  | { kind: "anova"; body: AnovaRequest }
  | { kind: "chisquare"; body: ChiSquareRequest }
  | { kind: "fit"; body: DistributionFitRequest }
  | { kind: "outliers"; body: OutlierRequest }
  | { kind: "ci"; body: ConfidenceIntervalRequest };

/** Runs one test against its real endpoint. */
export async function runStatTest(call: StatTestCall, signal?: AbortSignal): Promise<StatResult> {
  switch (call.kind) {
    case "correlation":
      return unwrap(await api.POST("/api/stats/correlation", { body: call.body, signal }));
    case "regression":
      return unwrap(await api.POST("/api/stats/regression", { body: call.body, signal }));
    case "ttest":
      return unwrap(await api.POST("/api/stats/ttest", { body: call.body, signal }));
    case "ranktest":
      return unwrap(await api.POST("/api/stats/ranktest", { body: call.body, signal }));
    case "anova":
      return unwrap(await api.POST("/api/stats/anova", { body: call.body, signal }));
    case "chisquare":
      return unwrap(await api.POST("/api/stats/chisquare", { body: call.body, signal }));
    case "fit":
      return unwrap(await api.POST("/api/stats/fit", { body: call.body, signal }));
    case "outliers":
      return unwrap(await api.POST("/api/stats/outliers", { body: call.body, signal }));
    case "ci":
      return unwrap(await api.POST("/api/stats/ci", { body: call.body, signal }));
  }
}

/**
 * The OpenAPI schema types `StatResult.values` as an open JSON object (it is shared by every
 * stat and plot transform); each endpoint's concrete shape is the matching generated `*Values`
 * model. This is the one place that narrowing happens.
 */
export function valuesOf<T>(result: StatResult): T {
  return result.values as unknown as T;
}
