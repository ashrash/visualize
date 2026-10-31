import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../api/types";
import type { ChartBrush, ChartLegendChange, ChartPointClick } from "../charts";
import {
  brushPredicates,
  bucketBounds,
  canBrushFilter,
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

  it("matches the millisecond around a raw time value when there is no bucket", () => {
    // timestamps carry finer precision than a JavaScript date: an exact match would miss them
    const s = spec({ x: [{ field: "ts" }] }, { plotType: "line" });
    expect(pointPredicates(s, click("ts", "2024-05-15T13:45:00.000Z"), { temporal: true })).toEqual(
      [
        {
          op: "between_dates",
          field: "ts",
          min: "2024-05-15T13:45:00.000Z",
          max: "2024-05-15T13:45:00.001Z",
          includeMin: true,
          includeMax: false,
        },
      ],
    );
    // a text column that merely looks like a date stays an exact match
    expect(pointPredicates(s, click("ts", "2024-05-15T13:45:00.000Z"))).toEqual([
      { op: "in", field: "ts", values: ["2024-05-15T13:45:00.000Z"] },
    ]);
  });

  it("takes the bucket from the x channel, not another channel using the same field", () => {
    // order_date is bucketed on x but also a plain measure (count distinct) on y
    const s = spec({
      y: [{ field: "order_date", agg: "count_distinct" }],
      x: [{ field: "order_date", timeUnit: "month" }],
    });
    const [p] = pointPredicates(s, click("order_date", "2024-02-01T00:00:00.000Z"));
    expect(p).toMatchObject({ op: "between_dates", max: "2024-03-01T00:00:00.000Z" });
    // and a field that is only a measure is never a bucket
    const measureOnly = spec({
      y: [{ field: "d", timeUnit: "month" }],
      x: [{ field: "region" }],
    });
    expect(pointPredicates(measureOnly, click("d", "2024-02-01T00:00:00.000Z"))).toEqual([
      { op: "in", field: "d", values: ["2024-02-01T00:00:00.000Z"] },
    ]);
  });

  it("puts the drill path in front, so a leaf click keeps its context", () => {
    const s = spec({ x: [{ field: "country" }] });
    const path = [{ op: "eq" as const, field: "country", value: "US" }];
    expect(pointPredicates(s, click("state", "Texas"), { drill: path })).toEqual([
      { op: "eq", field: "country", value: "US" },
      { op: "in", field: "state", values: ["Texas"] },
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
    expect(pointPredicates(s, click("region", "N"), { color: 2 })).toEqual([
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

describe("which plots can brush-filter", () => {
  it("only plots whose brushed axis is in the units of the x field", () => {
    for (const plotType of [
      "bar",
      "horizontal_bar",
      "grouped_bar",
      "stacked_bar",
      "line",
      "area",
      "scatter",
      "bubble",
      "timeseries_line",
      "moving_average",
    ]) {
      expect(canBrushFilter(spec({}, { plotType: plotType as never }))).toBe(true);
    }
    // the x channel names a field, but the axis shows something computed from it
    for (const plotType of [
      "qq",
      "lag",
      "andrews_curves",
      "pca_biplot",
      "cluster",
      "event_study",
      "pie",
      "histogram",
    ]) {
      expect(canBrushFilter(spec({}, { plotType: plotType as never }))).toBe(false);
    }
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

  it("fails closed for bands that do not touch: one range would include buckets nobody picked", () => {
    const s = spec({ x: [{ field: "d", timeUnit: "month" }] });
    const b = brushOf({
      field: "d",
      // Jan-Feb and Jun-Jul: a single range [Jan 1, Aug 1) would take in Mar-May as well
      categories: ["Jan", "Feb", "Jun", "Jul"],
      values: [
        "2024-01-01T00:00:00.000Z",
        "2024-02-01T00:00:00.000Z",
        "2024-06-01T00:00:00.000Z",
        "2024-07-01T00:00:00.000Z",
      ],
    });
    expect(brushPredicates(s, b)).toEqual([]);
  });

  it("accepts touching bands in any order, and a single bucket", () => {
    const s = spec({ x: [{ field: "d", timeUnit: "month" }] });
    const touching = brushOf({
      field: "d",
      categories: ["Mar", "Jan", "Feb"],
      values: ["2024-03-01T00:00:00.000Z", "2024-01-01T00:00:00.000Z", "2024-02-01T00:00:00.000Z"],
    });
    expect(brushPredicates(s, touching)).toEqual([
      {
        op: "between_dates",
        field: "d",
        min: "2024-01-01T00:00:00.000Z",
        max: "2024-04-01T00:00:00.000Z",
        includeMin: true,
        includeMax: false,
      },
    ]);
    const one = brushOf({ field: "d", categories: ["Jan"], values: ["2024-01-01T00:00:00.000Z"] });
    expect(brushPredicates(s, one)[0]).toMatchObject({ max: "2024-02-01T00:00:00.000Z" });
  });

  it("keeps the drill path in front of a brush", () => {
    const s = spec({ x: [{ field: "state" }] });
    const path = [{ op: "eq" as const, field: "country", value: "US" }];
    expect(
      brushPredicates(s, brushOf({ field: "state", categories: ["TX"], values: ["TX"] }), {
        drill: path,
      }),
    ).toEqual([path[0], { op: "in", field: "state", values: ["TX"] }]);
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

  it("ignores a value range along a y axis, and several value ranges", () => {
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

  it("a horizontal chart brushes along y, where its categories run, and still filters them", () => {
    const s = spec({ x: [{ field: "region" }] });
    expect(brushPredicates(s, brushOf({ axis: "y", categories: ["N"], values: ["N"] }))).toEqual([
      { op: "in", field: "region", values: ["N"] },
    ]);
  });
});

const legendOf = (over: Partial<ChartLegendChange>) => ({
  field: "channel" as string | null,
  hidden: ["app"],
  hiddenValues: ["app"] as ChartLegendChange["hiddenValues"],
  ...over,
});
const domain = ["web", "app", "store"];

describe("legendPredicates (raw values from ChartView)", () => {
  it("keeps every colour value except the hidden ones", () => {
    expect(legendPredicates(legendOf({}), { domain, measures: 1 })).toEqual([
      { op: "in", field: "channel", values: ["web", "store"] },
    ]);
    expect(
      legendPredicates(legendOf({ hidden: ["1"], hiddenValues: [1] }), {
        domain: [1, 2, null],
        measures: 1,
      }),
    ).toEqual([{ op: "in", field: "channel", values: [2, null] }]);
  });

  it("fails open: a hidden entry that does not resolve to a value filters nothing", () => {
    // 'ghost' is hidden but maps to no raw value: narrowing to the others would be a guess
    expect(
      legendPredicates(legendOf({ hidden: ["app", "ghost"], hiddenValues: ["app"] }), {
        domain,
        measures: 1,
      }),
    ).toEqual([]);
  });

  it("does not filter when nothing is hidden, everything is, or the hidden value is not in the data", () => {
    expect(
      legendPredicates(legendOf({ hidden: [], hiddenValues: [] }), { domain, measures: 1 }),
    ).toEqual([]);
    expect(
      legendPredicates(legendOf({ hidden: domain, hiddenValues: domain }), { domain, measures: 1 }),
    ).toEqual([]);
    expect(
      legendPredicates(legendOf({ hidden: ["x"], hiddenValues: ["x"] }), { domain, measures: 1 }),
    ).toEqual([]);
  });

  it("does not filter without a colour field or data, or with several measures (entries are series)", () => {
    expect(legendPredicates(legendOf({ field: null }), { domain, measures: 1 })).toEqual([]);
    expect(legendPredicates(legendOf({}), { domain: undefined, measures: 1 })).toEqual([]);
    expect(legendPredicates(legendOf({}), { domain, measures: 2 })).toEqual([]);
  });

  it("keeps the drill path in front", () => {
    const path = [{ op: "eq" as const, field: "country", value: "US" }];
    expect(legendPredicates(legendOf({}), { domain, measures: 1, drill: path })[0]).toEqual(
      path[0],
    );
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
  it("replaces an earlier date bucket or range on the same field instead of stacking them", () => {
    const bucket = (m: number) => ({
      op: "between_dates" as const,
      field: "d",
      min: `2024-0${m}-01T00:00:00.000Z`,
      max: `2024-0${m + 1}-01T00:00:00.000Z`,
      includeMin: true,
      includeMax: false,
    });
    expect(extendSelection([bucket(1)], [bucket(3)])).toEqual([bucket(3)]);
  });
  it("does not repeat the same drill predicate when extending", () => {
    const drill = { op: "eq" as const, field: "country", value: "US" };
    expect(extendSelection([drill, inP(["N"])], [drill, inP(["S"])])).toEqual([
      drill,
      inP(["N", "S"]),
    ]);
  });
  it("compares selections regardless of value order", () => {
    expect(sameSelection([inP(["a", "b"])], [inP(["b", "a"])])).toBe(true);
    expect(sameSelection([inP(["a"])], [inP(["b"])])).toBe(false);
  });
});
