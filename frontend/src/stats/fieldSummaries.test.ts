import { describe, expect, it } from "vitest";
import type { FieldSummary, StatResult } from "../api/types";
import { groupLabel, groupSummaries, summariesOf } from "./fieldSummaries";

const summary = (overrides: Partial<FieldSummary>): FieldSummary => ({
  field: "age",
  group: null,
  kind: "numeric",
  count: 10,
  missing: 0,
  distinct: 10,
  mean: 5,
  std: 1,
  variance: 1,
  sem: 0.3,
  sum: 50,
  iqr: 2,
  min: 1,
  max: 9,
  median: 5,
  q1: 4,
  q3: 6,
  skewness: 0,
  kurtosis: 0,
  quantiles: [],
  modes: [5],
  modeCount: 3,
  ...overrides,
});

describe("summariesOf", () => {
  it("reads values.summaries off a describe StatResult", () => {
    const result: StatResult = {
      values: { summaries: [summary({})] },
      method: "describe",
      n: 10,
      sampled: false,
      warnings: [],
      nMissing: 0,
    };
    expect(summariesOf(result)).toHaveLength(1);
  });

  it("returns an empty list for an undefined result or a malformed values bag", () => {
    expect(summariesOf(undefined)).toEqual([]);
    expect(
      summariesOf({
        values: { summaries: "not an array" },
        method: "x",
        n: 0,
        sampled: false,
        warnings: [],
        nMissing: 0,
      }),
    ).toEqual([]);
    expect(
      summariesOf({
        values: { summaries: [{ not: "a summary" }] },
        method: "x",
        n: 0,
        sampled: false,
        warnings: [],
        nMissing: 0,
      }),
    ).toEqual([]);
  });
});

describe("groupSummaries", () => {
  it("groups summaries by field name", () => {
    const grouped = groupSummaries([
      summary({ field: "age", group: ["east"] }),
      summary({ field: "age", group: ["west"] }),
      summary({ field: "income", group: null }),
    ]);
    expect(grouped.get("age")).toHaveLength(2);
    expect(grouped.get("income")).toHaveLength(1);
  });
});

describe("groupLabel", () => {
  it("labels the overall row and joins group keys", () => {
    expect(groupLabel(null)).toBe("Overall");
    expect(groupLabel([])).toBe("Overall");
    expect(groupLabel(["east", 2024])).toBe("east / 2024");
  });
});
