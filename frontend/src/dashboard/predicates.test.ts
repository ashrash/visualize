import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../api/types";
import type { ChartBrush, ChartLegendChange, ChartPointClick } from "../charts";
import {
  brushPredicates,
  bucketBounds,
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

const click = (
  field: string | null,
  rawValue: ChartPointClick["rawValue"],
  over: Partial<ChartPointClick> = {},
): Pick<ChartPointClick, "field" | "rawValue" | "drilled"> => ({
  field,
  rawValue,
  drilled: false,
  ...over,
});

describe("pointPredicates (raw values from ChartView)", () => {
  it("filters the x field by the raw value", () => {
    const s = spec({ x: [{ field: "region" }], y: [{ field: "sales", agg: "sum" }] });
    expect(pointPredicates(s, click("region", "North"))).toEqual([
      { op: "in", field: "region", values: ["North"] },
    ]);
  });

  it("keeps numbers as numbers (the drawn label was '2,020')", () => {
    expect(pointPredicates(spec({ x: [{ field: "year" }] }), click("year", 2020))).toEqual([
      { op: "in", field: "year", values: [2020] },
    ]);
  });

  it("filters a blank category by NULL", () => {
    for (const blank of [null, ""]) {
      expect(pointPredicates(spec({ x: [{ field: "region" }] }), click("region", blank))).toEqual([
        { op: "in", field: "region", values: [null] },
      ]);
    }
  });

  it("turns the start of a time bucket into the half-open range of that bucket", () => {
    const s = spec({ x: [{ field: "order_date", timeUnit: "month" }] });
    expect(pointPredicates(s, click("order_date", "2024-02-01T00:00:00.000Z"))).toEqual([
      {
        op: "between_dates",
        field: "order_date",
        min: "2024-02-01T00:00:00.000Z",
        max: "2024-03-01T00:00:00.000Z",
        includeMin: true,
        includeMax: false,
      },
    ]);
    const quarter = spec({ x: [{ field: "d", timeUnit: "quarter" }] });
    const [q] = pointPredicates(quarter, click("d", "2024-04-01T00:00:00.000Z"));
    expect(q).toMatchObject({ min: "2024-04-01T00:00:00.000Z", max: "2024-07-01T00:00:00.000Z" });
  });

  it("matches a raw time value exactly when there is no bucket", () => {
    const s = spec({ x: [{ field: "ts" }] }, { plotType: "line" });
    expect(pointPredicates(s, click("ts", "2024-05-15T13:45:00.000Z"))).toEqual([
      { op: "in", field: "ts", values: ["2024-05-15T13:45:00.000Z"] },
    ]);
  });

  it("uses the chart's current field after a drill-down, not the spec's x field", () => {
    const s = spec({ x: [{ field: "country" }] });
    expect(pointPredicates(s, click("state", "Texas"))).toEqual([
      { op: "in", field: "state", values: ["Texas"] },
    ]);
  });

  it("adds the raw colour group when it is known", () => {
    const s = spec({ x: [{ field: "region" }], color: [{ field: "tier" }] });
    expect(pointPredicates(s, click("region", "N"), 2)).toEqual([
      { op: "in", field: "region", values: ["N"] },
      { op: "in", field: "tier", values: [2] },
    ]);
    expect(pointPredicates(s, click("region", "N"))).toEqual([
      { op: "in", field: "region", values: ["N"] },
    ]);
  });

  it("filters nothing it cannot be sure of: unresolved, no field, or a drill-down click", () => {
    const s = spec({ x: [{ field: "region" }] });
    expect(pointPredicates(s, click("region", undefined))).toEqual([]);
    expect(pointPredicates(s, click(null, "N"))).toEqual([]);
    expect(pointPredicates(s, click("region", "N", { drilled: true }))).toEqual([]);
    const dated = spec({ x: [{ field: "d", timeUnit: "day" }] });
    expect(pointPredicates(dated, click("d", "not a date"))).toEqual([]);
  });
});

describe("which plots can click-filter", () => {
  it("only plots whose x channel carries the raw x values", () => {
    expect(canClickFilter(spec({}, { plotType: "bar" }))).toBe(true);
    expect(canClickFilter(spec({}, { plotType: "pie" }))).toBe(true);
    expect(canClickFilter(spec({}, { plotType: "scatter" }))).toBe(false);
    expect(canClickFilter(spec({}, { plotType: "histogram" }))).toBe(false);
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

const brushOf = (over: Partial<ChartBrush>): ChartBrush => ({
  axis: "x",
  kind: "category",
  ranges: [],
  categories: [],
  cleared: false,
  field: "region",
  values: [],
  ...over,
});

describe("brushPredicates (raw values from ChartView)", () => {
  it("filters the brushed categories by their raw values (integers, blanks)", () => {
    const s = spec({ x: [{ field: "year" }] });
    const b = brushOf({
      field: "year",
      categories: ["999", "1,000", "(blank)"],
      values: [999, 1000, null],
    });
    expect(brushPredicates(s, b)).toEqual([{ op: "in", field: "year", values: [999, 1000, null] }]);
  });

  it("makes a numeric range from a value axis", () => {
    const s = spec({ x: [{ field: "price" }] });
    expect(
      brushPredicates(s, brushOf({ field: "price", kind: "value", ranges: [[3, 9]] })),
    ).toEqual([
      { op: "range", field: "price", min: 3, max: 9, includeMin: true, includeMax: true },
    ]);
    expect(
      brushPredicates(s, brushOf({ field: "price", kind: "log", ranges: [[1, 100]] })),
    ).toEqual([
      { op: "range", field: "price", min: 1, max: 100, includeMin: true, includeMax: true },
    ]);
  });

  it("makes a date range from a time axis", () => {
    const s = spec({ x: [{ field: "d" }] }, { plotType: "line" });
    const [p] = brushPredicates(
      s,
      brushOf({ field: "d", kind: "time", ranges: [[Date.UTC(2024, 0, 2), Date.UTC(2024, 0, 5)]] }),
    );
    expect(p).toMatchObject({
      op: "between_dates",
      min: "2024-01-02T00:00:00.000Z",
      max: "2024-01-05T00:00:00.000Z",
      includeMax: true,
    });
  });

  it("turns brushed month buckets into one half-open range through the last bucket", () => {
    const s = spec({ x: [{ field: "d", timeUnit: "month" }] });
    const b = brushOf({
      field: "d",
      categories: ["Feb 2024", "Mar 2024"],
      values: ["2024-02-01T00:00:00.000Z", "2024-03-01T00:00:00.000Z"],
    });
    expect(brushPredicates(s, b)).toEqual([
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

  it("filters nothing when cleared, or when a brushed label has no value in the data", () => {
    const s = spec({ x: [{ field: "region" }] });
    expect(brushPredicates(s, brushOf({ cleared: true }))).toEqual([]);
    expect(brushPredicates(s, brushOf({ categories: [], values: [] }))).toEqual([]);
    expect(brushPredicates(s, brushOf({ categories: ["N", "Ghost"], values: ["N"] }))).toEqual([]);
    expect(brushPredicates(s, brushOf({ field: null, categories: ["N"], values: ["N"] }))).toEqual(
      [],
    );
  });

  it("ignores a band along the measure axis (horizontal charts) and several value ranges", () => {
    const s = spec({ x: [{ field: "region" }] });
    expect(brushPredicates(s, brushOf({ axis: "y", kind: "value", ranges: [[1, 2]] }))).toEqual([]);
    expect(
      brushPredicates(
        s,
        brushOf({
          kind: "value",
          ranges: [
            [1, 2],
            [5, 6],
          ],
        }),
      ),
    ).toEqual([]);
  });

  it("a horizontal chart's category brush (axis y) still filters its categories", () => {
    const s = spec({ x: [{ field: "region" }] });
    expect(brushPredicates(s, brushOf({ axis: "y", categories: ["N"], values: ["N"] }))).toEqual([
      { op: "in", field: "region", values: ["N"] },
    ]);
  });
});

const legendOf = (over: Partial<ChartLegendChange>): ChartLegendChange => ({
  toggled: "app",
  selected: {},
  shown: ["web", "store"],
  hidden: ["app"],
  field: "channel",
  shownValues: ["web", "store"],
  hiddenValues: ["app"],
  ...over,
});

describe("legendPredicates (raw values from ChartView)", () => {
  it("keeps only the groups still on, as raw values", () => {
    expect(legendPredicates(legendOf({}))).toEqual([
      { op: "in", field: "channel", values: ["web", "store"] },
    ]);
    expect(legendPredicates(legendOf({ shownValues: [2, null] }))).toEqual([
      { op: "in", field: "channel", values: [2, null] },
    ]);
  });

  it("does not filter when every group is on, none maps to data, or there is no colour field", () => {
    expect(legendPredicates(legendOf({ hidden: [], hiddenValues: [] }))).toEqual([]);
    expect(legendPredicates(legendOf({ shownValues: [] }))).toEqual([]);
    expect(legendPredicates(legendOf({ field: null }))).toEqual([]);
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
