import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../api/types";
import { categoryLabel } from "../charts/core/series";
import {
  brushPredicates,
  bucketBounds,
  type ChartLookup,
  canBrush,
  canClickFilter,
  extendSelection,
  legendPredicates,
  pointPredicates,
  sameSelection,
} from "./predicates";

function spec(encodings: ChartSpec["encodings"], over: Partial<ChartSpec> = {}): ChartSpec {
  return {
    id: "c1",
    name: "c",
    plotType: "bar",
    source: { kind: "dataset", id: "d" },
    encodings,
    ...over,
  } as ChartSpec;
}

const lookup = (over: Partial<ChartLookup> = {}): ChartLookup => ({
  xs: [],
  colors: [],
  ...over,
});

describe("pointPredicates", () => {
  it("maps a category label back to the raw value in the data", () => {
    const s = spec({ x: [{ field: "region" }], y: [{ field: "sales", agg: "sum" }] });
    const l = lookup({ xs: ["North", "South"], xKind: "string" });
    expect(pointPredicates(s, { name: "North" }, l)).toEqual([
      { op: "in", field: "region", values: ["North"] },
    ]);
  });

  it("filters an integer category by its number, not the grouped label ('2,020')", () => {
    const s = spec({ x: [{ field: "year" }] });
    const l = lookup({ xs: [2019, 2020, 2021], xKind: "number" });
    expect(categoryLabel(2020, "number")).toBe("2,020"); // what ECharts reports
    expect(pointPredicates(s, { name: "2,020" }, l)).toEqual([
      { op: "in", field: "year", values: [2020] },
    ]);
  });

  it("filters a blank category by NULL, not by the '(blank)' label", () => {
    const s = spec({ x: [{ field: "region" }] });
    const l = lookup({ xs: ["North", null], xKind: "string" });
    expect(pointPredicates(s, { name: "(blank)" }, l)).toEqual([
      { op: "in", field: "region", values: [null] },
    ]);
  });

  it("filters the right month for a month-formatted axis, whatever the machine's time zone", () => {
    const s = spec(
      { x: [{ field: "order_date", timeUnit: "month" }] },
      {
        style: { xAxis: { format: "month" } } as never,
      },
    );
    const feb = Date.UTC(2024, 1, 1);
    const l = lookup({ xs: [Date.UTC(2024, 0, 1), feb], xKind: "temporal", xFormat: "month" });
    const label = categoryLabel(feb, "temporal", "month");
    expect(pointPredicates(s, { name: label }, l)).toEqual([
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

  it("handles a quarter-formatted axis (the label is not a parseable date)", () => {
    const s = spec(
      { x: [{ field: "d", timeUnit: "quarter" }] },
      {
        style: { xAxis: { format: "quarter" } } as never,
      },
    );
    const q2 = Date.UTC(2024, 3, 1);
    const l = lookup({ xs: [Date.UTC(2024, 0, 1), q2], xKind: "temporal", xFormat: "quarter" });
    const [p] = pointPredicates(s, { name: categoryLabel(q2, "temporal", "quarter") }, l);
    expect(p).toMatchObject({ min: "2024-04-01T00:00:00.000Z", max: "2024-07-01T00:00:00.000Z" });
  });

  it("uses the raw axis value on a time axis (no label involved)", () => {
    const s = spec({ x: [{ field: "d", timeUnit: "day" }] });
    const [p] = pointPredicates(s, { name: "", axisValue: Date.UTC(2024, 4, 15, 13) });
    expect(p).toMatchObject({
      op: "between_dates",
      min: "2024-05-15T00:00:00.000Z",
      max: "2024-05-16T00:00:00.000Z",
    });
  });

  it("uses the raw axis value on a numeric x axis instead of an empty label", () => {
    const s = spec({ x: [{ field: "t" }] }, { plotType: "line" });
    expect(pointPredicates(s, { name: "", axisValue: 12.5 })).toEqual([
      { op: "in", field: "t", values: [12.5] },
    ]);
  });

  it("adds the raw colour group, mapped back from the series name", () => {
    const s = spec({ x: [{ field: "region" }], color: [{ field: "tier" }] });
    const l = lookup({ xs: ["N"], xKind: "string", colors: [1, 2], colorKind: "number" });
    expect(pointPredicates(s, { name: "N", seriesName: "2" }, l)).toEqual([
      { op: "in", field: "region", values: ["N"] },
      { op: "in", field: "tier", values: [2] },
    ]);
  });

  it("does not guess: an unknown or ambiguous label, or no data, filters nothing", () => {
    const s = spec({ x: [{ field: "region" }] });
    expect(
      pointPredicates(s, { name: "East" }, lookup({ xs: ["North"], xKind: "string" })),
    ).toEqual([]);
    // 1000 and "1,000" both label as "1,000": ambiguous
    const ambiguous = lookup({ xs: [1000, "1,000"], xKind: "number" });
    expect(pointPredicates(s, { name: "1,000" }, ambiguous)).toEqual([]);
    expect(pointPredicates(s, { name: "North" })).toEqual([]);
    expect(pointPredicates(spec({ x: [{ agg: "count" }] }), { name: "x" }, lookup())).toEqual([]);
  });
});

describe("which plots can filter", () => {
  it("only plots whose x channel carries raw x values click-filter", () => {
    expect(canClickFilter(spec({}, { plotType: "bar" }))).toBe(true);
    expect(canClickFilter(spec({}, { plotType: "pie" }))).toBe(true);
    expect(canClickFilter(spec({}, { plotType: "scatter" }))).toBe(false);
    expect(canClickFilter(spec({}, { plotType: "histogram" }))).toBe(false);
  });

  it("brush needs a horizontal x axis: not horizontal charts or other families", () => {
    expect(canBrush(spec({}, { plotType: "bar" }))).toBe(true);
    expect(canBrush(spec({}, { plotType: "horizontal_bar" }))).toBe(false);
    expect(
      canBrush(spec({}, { plotType: "bar", style: { orientation: "horizontal" } as never })),
    ).toBe(false);
    expect(canBrush(spec({}, { plotType: "scatter" }))).toBe(false);
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

  it("maps brushed category labels back to raw values (integers, blanks)", () => {
    const s = spec({ x: [{ field: "year" }] });
    const l = lookup({ xs: [999, 1000, 2020, null], xKind: "number" });
    expect(brushPredicates(s, { categories: ["999", "1,000", "(blank)"] }, l)).toEqual([
      { op: "in", field: "year", values: [999, 1000, null] },
    ]);
  });

  it("turns brushed month categories into one half-open date range through the last bucket", () => {
    const s = spec({ x: [{ field: "d", timeUnit: "month" }] });
    const months = [0, 1, 2, 3].map((m) => Date.UTC(2024, m, 1));
    const l = lookup({ xs: months, xKind: "temporal", xFormat: "month" });
    const labels = [1, 2].map((i) => categoryLabel(months[i], "temporal", "month"));
    expect(brushPredicates(s, { categories: labels }, l)).toEqual([
      {
        op: "between_dates",
        field: "d",
        min: "2024-02-01T00:00:00.000Z",
        max: "2024-04-01T00:00:00.000Z",
        includeMin: true,
        includeMax: false,
      },
    ]);
  });

  it("cancels when any brushed label cannot be mapped, and when empty", () => {
    const s = spec({ x: [{ field: "region" }] });
    const l = lookup({ xs: ["N", "S"], xKind: "string" });
    expect(brushPredicates(s, { categories: ["N", "Elsewhere"] }, l)).toEqual([]);
    expect(brushPredicates(s, { categories: [] }, l)).toEqual([]);
    expect(brushPredicates(s, { categories: ["N"] })).toEqual([]);
  });
});

describe("legendPredicates", () => {
  const s = spec({ x: [{ field: "region" }], color: [{ field: "channel" }] });
  const l = lookup({ colors: ["web", "store", "app"], colorKind: "string" });
  it("keeps only the groups still on, as raw values", () => {
    expect(legendPredicates(s, { web: true, store: false, app: true }, l)).toEqual([
      { op: "in", field: "channel", values: ["web", "app"] },
    ]);
  });
  it("does not filter when everything (or nothing) is on, or a group is unknown", () => {
    expect(legendPredicates(s, { web: true, store: true }, l)).toEqual([]);
    expect(legendPredicates(s, { web: false, store: false }, l)).toEqual([]);
    expect(legendPredicates(s, { web: true, ghost: true, store: false }, l)).toEqual([]);
    expect(legendPredicates(s, { web: true, store: false })).toEqual([]);
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
