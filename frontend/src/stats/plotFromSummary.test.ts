import { describe, expect, it } from "vitest";
import type { FieldSummary } from "../api/types";
import { plotFromSummary } from "./plotFromSummary";

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

describe("plotFromSummary", () => {
  it("builds a histogram of the field for an ungrouped summary", () => {
    const spec = plotFromSummary(summary({ group: null }), { kind: "dataset", id: "d1" });
    expect(spec.plotType).toBe("histogram");
    expect(spec.source).toEqual({ kind: "dataset", id: "d1" });
    expect(spec.encodings?.x).toEqual([{ field: "age" }]);
  });

  it("builds a box plot across groups for a grouped summary", () => {
    const spec = plotFromSummary(summary({ group: ["east"] }), { kind: "pipeline", id: "p1" });
    expect(spec.plotType).toBe("box");
    expect(spec.encodings?.y).toEqual([{ field: "age" }]);
  });

  it("gives every chart a fresh id", () => {
    const a = plotFromSummary(summary({}), { kind: "dataset", id: "d1" });
    const b = plotFromSummary(summary({}), { kind: "dataset", id: "d1" });
    expect(a.id).not.toBe(b.id);
  });
});
