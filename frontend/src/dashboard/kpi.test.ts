import { describe, expect, it } from "vitest";
import type { FieldSummary } from "../api/types";
import {
  computeDelta,
  describeRequest,
  headlineValue,
  sparklinePoints,
  sparklineSpec,
  summaryValue,
} from "./kpi";
import type { KpiWidgetValue } from "./types";

const widget = (over: Partial<KpiWidgetValue> = {}): KpiWidgetValue => ({
  kind: "kpi",
  title: "Revenue",
  source: { kind: "dataset", id: "d1" },
  measure: { field: "sales", agg: "sum" },
  time: { field: "order_date" },
  comparison: "previous_period",
  ...over,
});

const summary = (over: Partial<FieldSummary>): FieldSummary =>
  ({
    field: "sales",
    group: null,
    kind: "numeric",
    count: 90,
    missing: 10,
    distinct: 7,
    mean: 5,
    std: 2,
    variance: 4,
    sem: 0.2,
    sum: 450,
    iqr: 1,
    min: 1,
    max: 9,
    median: 5,
    q1: 3,
    q3: 7,
    skewness: 0,
    kurtosis: 0,
    quantiles: [],
    modes: [],
    modeCount: null,
    ...over,
  }) as FieldSummary;

describe("summaryValue", () => {
  it("reads each aggregate from the field summary", () => {
    const s = summary({});
    expect(summaryValue(s, "sum", true)).toBe(450);
    expect(summaryValue(s, "mean", true)).toBe(5);
    expect(summaryValue(s, "median", true)).toBe(5);
    expect(summaryValue(s, "min", true)).toBe(1);
    expect(summaryValue(s, "max", true)).toBe(9);
    expect(summaryValue(s, "var", true)).toBe(4);
    expect(summaryValue(s, "count_distinct", true)).toBe(7);
  });

  it("counts non-missing values for a field and all rows without one", () => {
    expect(summaryValue(summary({}), "count", true)).toBe(90);
    expect(summaryValue(summary({}), "count", false)).toBe(100);
  });

  it("is null for a non-numeric min/max (a text column)", () => {
    expect(summaryValue(summary({ min: "a", max: "z" }), "max", true)).toBeNull();
  });
});

describe("describeRequest", () => {
  it("asks for one cheap overall summary with the filters", () => {
    const filters = [{ op: "eq" as const, field: "region", value: "N" }];
    const req = describeRequest(widget(), filters, [], undefined);
    expect(req).toMatchObject({
      source: { kind: "dataset", id: "d1" },
      fields: ["sales"],
      filters,
      includeMode: false,
      includeOverall: true,
    });
    expect(req).not.toHaveProperty("pipelines");
  });

  it("falls back to a given column for a field-less count, and null without one", () => {
    const w = widget({ measure: { agg: "count" } });
    expect(describeRequest(w, [], [], "order_id")?.fields).toEqual(["order_id"]);
    expect(describeRequest(w, [], [], undefined)).toBeNull();
  });
});

describe("sparklineSpec", () => {
  it("is a monthly line of the measure, with a per-widget id (its own cancellation slot)", () => {
    const spec = sparklineSpec("w1", widget());
    expect(spec).toMatchObject({
      id: "kpi:w1",
      plotType: "line",
      encodings: {
        x: [{ field: "order_date", timeUnit: "month" }],
        y: [{ field: "sales", agg: "sum" }],
      },
    });
  });
  it("keeps a chosen time unit and is null without a time field", () => {
    expect(
      sparklineSpec("w", widget({ time: { field: "d", timeUnit: "week" } }))?.encodings?.x?.[0]
        ?.timeUnit,
    ).toBe("week");
    expect(sparklineSpec("w", widget({ time: null }))).toBeNull();
  });
});

describe("computeDelta", () => {
  it("compares the last two buckets", () => {
    expect(
      computeDelta({
        comparison: "previous_period",
        series: [10, 100, 120],
        value: null,
        target: null,
      }),
    ).toEqual({ kind: "previous_period", change: 20, fraction: 0.2, direction: "up" });
    expect(
      computeDelta({ comparison: "previous_period", series: [100, 80], value: null, target: null }),
    ).toMatchObject({ change: -20, fraction: -0.2, direction: "down" });
  });

  it("has no fraction when the previous bucket is zero, and no delta with one bucket", () => {
    expect(
      computeDelta({ comparison: "previous_period", series: [0, 5], value: null, target: null }),
    ).toMatchObject({ change: 5, fraction: null });
    expect(
      computeDelta({ comparison: "previous_period", series: [5], value: null, target: null }),
    ).toBeNull();
  });

  it("compares with the target", () => {
    expect(computeDelta({ comparison: "target", series: [], value: 90, target: 100 })).toEqual({
      kind: "target",
      change: -10,
      fraction: -0.1,
      direction: "down",
    });
    expect(computeDelta({ comparison: "target", series: [], value: 90, target: null })).toBeNull();
    expect(computeDelta({ comparison: "none", series: [1, 2], value: 1, target: 1 })).toBeNull();
  });
});

describe("headlineValue", () => {
  it("is the last bucket for previous_period, else the exact total", () => {
    expect(headlineValue({ comparison: "previous_period", series: [1, 2, 3], total: 99 })).toBe(3);
    expect(headlineValue({ comparison: "none", series: [1, 2, 3], total: 99 })).toBe(99);
    expect(headlineValue({ comparison: "previous_period", series: [], total: 99 })).toBeNull();
  });
});

describe("sparklinePoints", () => {
  it("scales values into the box with y flipped", () => {
    expect(sparklinePoints([0, 10], 10, 10, 0)).toBe("0.0,10.0 10.0,0.0");
  });
  it("draws a flat line mid-way is not needed: needs two numbers, ignores non-numbers", () => {
    expect(sparklinePoints([5], 10, 10)).toBe("");
    expect(sparklinePoints([null, 1, "x", 1], 10, 10, 0)).toBe("0.0,10.0 10.0,10.0");
  });
});
