import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../api/types";
import {
  brushPredicates,
  bucketBounds,
  extendSelection,
  legendPredicates,
  pointPredicates,
  sameSelection,
} from "./predicates";

function spec(encodings: ChartSpec["encodings"]): ChartSpec {
  return {
    id: "c1",
    name: "c",
    plotType: "bar",
    source: { kind: "dataset", id: "d" },
    encodings,
  } as ChartSpec;
}

describe("pointPredicates", () => {
  it("turns a category click into an `in` predicate on the x field", () => {
    const s = spec({ x: [{ field: "region" }], y: [{ field: "sales", agg: "sum" }] });
    expect(pointPredicates(s, { name: "North" })).toEqual([
      { op: "in", field: "region", values: ["North"] },
    ]);
  });

  it("adds the colour group from the series name", () => {
    const s = spec({ x: [{ field: "region" }], color: [{ field: "channel" }] });
    expect(pointPredicates(s, { name: "North", seriesName: "web" })).toEqual([
      { op: "in", field: "region", values: ["North"] },
      { op: "in", field: "channel", values: ["web"] },
    ]);
  });

  it("turns a click on a time bucket into a half-open between_dates range", () => {
    const s = spec({ x: [{ field: "order_date", timeUnit: "month" }] });
    const ts = Date.UTC(2024, 1, 1);
    expect(pointPredicates(s, { axisValue: ts })).toEqual([
      {
        op: "between_dates",
        field: "order_date",
        min: "2024-02-01T00:00:00.000Z",
        max: "2024-03-01T00:00:00.000Z",
        includeMin: true,
        includeMax: false,
      },
    ]);
  });

  it("returns nothing when the chart has no x field", () => {
    expect(pointPredicates(spec({ x: [{ agg: "count" }] }), { name: "x" })).toEqual([]);
  });
});

describe("bucketBounds", () => {
  it("covers each unit in UTC", () => {
    const ts = Date.UTC(2024, 4, 15, 13, 45); // Wed 2024-05-15 13:45Z
    const iso = (unit: Parameters<typeof bucketBounds>[1]) =>
      bucketBounds(ts, unit).map((d) => d.toISOString());
    expect(iso("year")).toEqual(["2024-01-01T00:00:00.000Z", "2025-01-01T00:00:00.000Z"]);
    expect(iso("quarter")).toEqual(["2024-04-01T00:00:00.000Z", "2024-07-01T00:00:00.000Z"]);
    expect(iso("week")).toEqual(["2024-05-13T00:00:00.000Z", "2024-05-20T00:00:00.000Z"]);
    expect(iso("day")).toEqual(["2024-05-15T00:00:00.000Z", "2024-05-16T00:00:00.000Z"]);
    expect(iso("hour")).toEqual(["2024-05-15T13:00:00.000Z", "2024-05-15T14:00:00.000Z"]);
    expect(iso("minute")).toEqual(["2024-05-15T13:45:00.000Z", "2024-05-15T13:46:00.000Z"]);
  });

  it("starts a week on Monday, including for a Sunday", () => {
    const [start] = bucketBounds(Date.UTC(2024, 4, 19), "week"); // Sunday
    expect(start?.toISOString()).toBe("2024-05-13T00:00:00.000Z");
  });
});

describe("brushPredicates", () => {
  it("makes a numeric range, ordering reversed bounds", () => {
    const s = spec({ x: [{ field: "price" }] });
    expect(brushPredicates(s, { min: 9, max: 3 })).toEqual([
      { op: "range", field: "price", min: 3, max: 9, includeMin: true, includeMax: true },
    ]);
  });

  it("makes a date range for a time axis", () => {
    const s = spec({ x: [{ field: "d", timeUnit: "day" }] });
    const [p] = brushPredicates(s, { min: Date.UTC(2024, 0, 2), max: Date.UTC(2024, 0, 5) });
    expect(p).toMatchObject({
      op: "between_dates",
      min: "2024-01-02T00:00:00.000Z",
      max: "2024-01-05T00:00:00.000Z",
    });
  });

  it("multi-selects categories, and clears on an empty brush", () => {
    const s = spec({ x: [{ field: "region" }] });
    expect(brushPredicates(s, { categories: ["N", "S"] })).toEqual([
      { op: "in", field: "region", values: ["N", "S"] },
    ]);
    expect(brushPredicates(s, { categories: [] })).toEqual([]);
  });
});

describe("legendPredicates", () => {
  const s = spec({ x: [{ field: "region" }], color: [{ field: "channel" }] });
  it("keeps only the groups still on", () => {
    expect(legendPredicates(s, { web: true, store: false, app: true })).toEqual([
      { op: "in", field: "channel", values: ["web", "app"] },
    ]);
  });
  it("does not filter when everything (or nothing) is on", () => {
    expect(legendPredicates(s, { web: true, store: true })).toEqual([]);
    expect(legendPredicates(s, { web: false, store: false })).toEqual([]);
  });
});

describe("extendSelection", () => {
  const inP = (values: string[]) => ({ op: "in" as const, field: "region", values });
  it("adds a value on the same field and removes it when clicked again", () => {
    const added = extendSelection([inP(["N"])], [inP(["S"])]);
    expect(added).toEqual([inP(["N", "S"])]);
    expect(extendSelection(added, [inP(["N"])])).toEqual([inP(["S"])]);
    expect(extendSelection([inP(["S"])], [inP(["S"])])).toEqual([]);
  });
  it("compares selections regardless of value order", () => {
    expect(sameSelection([inP(["a", "b"])], [inP(["b", "a"])])).toBe(true);
    expect(sameSelection([inP(["a"])], [inP(["b"])])).toBe(false);
  });
});
