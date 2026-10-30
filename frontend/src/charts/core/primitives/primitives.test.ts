import { describe, expect, it } from "vitest";
import { makeData, makeTheme } from "../testing";
import {
  chartParams,
  chartXDomain,
  ciBand,
  densityFallback,
  errorBars,
  hierarchyToTree,
  offsetKde,
  refLine,
  refLineItem,
} from "./index";

const theme = makeTheme();

describe("hierarchyToTree", () => {
  it("nests levels and sums values up the tree", () => {
    const rows = [
      { region: "N", product: "A", sales: 1 },
      { region: "N", product: "B", sales: 2 },
      { region: "S", product: "A", sales: 4 },
      { region: null, product: "A", sales: 8 },
    ];
    expect(hierarchyToTree({ rows, levels: ["region", "product"], value: "sales" })).toEqual([
      {
        name: "N",
        value: 3,
        children: [
          { name: "A", value: 1 },
          { name: "B", value: 2 },
        ],
      },
      { name: "S", value: 4, children: [{ name: "A", value: 4 }] },
      { name: "(blank)", value: 8, children: [{ name: "A", value: 8 }] },
    ]);
  });

  it("treats missing or non-numeric values as zero", () => {
    const tree = hierarchyToTree({
      rows: [{ a: "x", v: "n/a" }, { a: "x" }],
      levels: ["a"],
      value: "v",
    });
    expect(tree).toEqual([{ name: "x", value: 0 }]);
  });
});

describe("ciBand", () => {
  it("is an invisible base at lo plus a filled band of height hi - lo", () => {
    const [base, band] = ciBand({
      x: ["a", "b", "c"],
      lo: [1, 2, null],
      hi: [3, 5, 9],
      axis: "category",
      color: "#123456",
    });
    expect(base?.data).toEqual([1, 2, null]);
    expect(band?.data).toEqual([2, 3, null]);
    expect(base?.stack).toBe(band?.stack);
    expect(base?.lineStyle).toEqual({ opacity: 0 });
    expect(band?.areaStyle).toMatchObject({ color: "#123456" });
    expect(band?.tooltip).toEqual({ show: false });
  });

  it("pairs x with values on value axes; stack ids come from the caller, so render is pure", () => {
    const a = ciBand({ x: [1, 2], lo: [0, 0], hi: [1, 1], id: "a" });
    const b = ciBand({ x: [1, 2], lo: [0, 0], hi: [1, 1], id: "b" });
    // Same input, same output (no hidden counter).
    expect(ciBand({ x: [1, 2], lo: [0, 0], hi: [1, 1], id: "a" })).toEqual(a);
    expect(a[0]?.data).toEqual([
      [1, 0],
      [2, 0],
    ]);
    expect(a[0]?.stack).not.toBe(b[0]?.stack);
  });
});

describe("errorBars", () => {
  it("drops rows without both bounds and encodes the value axis", () => {
    const series = errorBars({ x: ["a", "b", "c"], lo: [1, null, 3], hi: [2, 5, 4], theme });
    expect(series.data).toEqual([
      ["a", 1, 2],
      ["c", 3, 4],
    ]);
    expect(series.encode).toEqual({ x: 0, y: [1, 2] });
    expect(errorBars({ x: ["a"], lo: [1], hi: [2], orientation: "h", theme }).encode).toEqual({
      x: [1, 2],
      y: 0,
    });
  });

  it("aligns grouped whiskers using the bar chart's real gaps", () => {
    const make = (barGap: number, categoryGap: number) =>
      errorBars({
        x: ["a"],
        lo: [1],
        hi: [2],
        groupIndex: 0,
        groupCount: 2,
        barGap,
        categoryGap,
        theme,
      });
    const api = {
      value: (dim: number) => [0, 1, 2][dim] as number,
      coord: () => [100, 100],
      size: () => [100, 0],
    };
    type Item = { children: { shape: { x1: number } }[] };
    const x1 = (series: ReturnType<typeof make>) =>
      (series.renderItem as (p: unknown, a: typeof api) => Item)(null, api).children[0]?.shape.x1;
    // Band 100, two bars: width = 100*(1-categoryGap)/(2+barGap); the first bar's centre sits
    // half a (width * (1 + barGap)) step left of the category centre.
    expect(x1(make(0.3, 0.2))).toBeCloseTo(100 - ((80 / 2.3) * 1.3) / 2, 5);
    expect(x1(make(0.1, 0.4))).toBeCloseTo(100 - ((60 / 2.1) * 1.1) / 2, 5);
  });

  it("draws a whisker with two caps", () => {
    const series = errorBars({ x: ["a"], lo: [1], hi: [2], theme });
    const api = {
      value: (dim: number) => [0, 1, 2][dim] as number,
      coord: (p: unknown[]) => [10 * (p[0] as number), 100 - 10 * (p[1] as number)],
      size: () => [40, 0],
    };
    const item = (series.renderItem as (p: unknown, a: typeof api) => { children: unknown[] })(
      null,
      api,
    );
    expect(item.children).toHaveLength(3);
  });
});

describe("refLine", () => {
  it("builds a markLine item on the right axis, swapped for horizontal charts", () => {
    expect(refLineItem({ axis: "y", value: 5, label: "target" })).toMatchObject({
      yAxis: 5,
      name: "target",
    });
    expect(refLineItem({ axis: "y", value: 5, swapped: true })).toMatchObject({ xAxis: 5 });
  });

  it("converts date strings on time axes", () => {
    expect(refLineItem({ axis: "x", value: "2026-01-01", time: true })).toMatchObject({
      xAxis: Date.UTC(2026, 0, 1),
    });
  });

  it("draws the y = x diagonal as a series", () => {
    const { series } = refLine({ axis: "diagonal", extent: [0, 10] });
    expect(series?.data).toEqual([
      [0, 0],
      [10, 10],
    ]);
  });
});

describe("offsetKde", () => {
  const groups = [
    { name: "A", x: [0, 1], density: [0, 2] },
    { name: "B", x: [0, 1], density: [0, 1] },
  ];

  it("offsets each group by a step and labels the y axis with group names", () => {
    const { series, yAxis, step } = offsetKde({ groups, overlap: 0.5, theme });
    expect(step).toBe(1); // peak 2, overlap 0.5
    expect(series).toHaveLength(4);
    expect(series[0]?.data).toEqual([
      [0, 1],
      [1, 1],
    ]); // group A (top row) sits at offset (n-1)*step
    expect(series[2]?.data).toEqual([
      [0, 0],
      [1, 0],
    ]);
    const formatter = (yAxis.axisLabel as { formatter: (v: number) => string }).formatter;
    expect([formatter(1), formatter(0)]).toEqual(["A", "B"]);
  });
});

describe("densityFallback", () => {
  const binned = makeData(
    {
      x_lo: [0, 1],
      x_hi: [1, 2],
      y_lo: [0, 0],
      y_hi: [1, 1],
      count: [5, 20],
    },
    {},
    { meta: { reduction: "binned", method: "bin2d" } },
  );

  it("returns null unless the data is binned", () => {
    expect(densityFallback(makeData({ x: [1], y: [2] }), { theme })).toBeNull();
  });

  it("draws one rect per bin colored by count", () => {
    const result = densityFallback(binned, { theme });
    expect(result?.series.data).toEqual([
      [0, 1, 0, 1, 5],
      [1, 2, 0, 1, 20],
    ]);
    expect(result?.visualMap).toMatchObject({ dimension: 4, max: 20 });
  });

  it("ties the visualMap to the density series by index", () => {
    expect(densityFallback(binned, { theme, seriesIndex: 2 })?.visualMap).toMatchObject({
      seriesIndex: 2,
    });
    expect(densityFallback(binned, { theme })?.visualMap).toMatchObject({ seriesIndex: 0 });
  });
});

describe("meta readers", () => {
  it("read params and xDomain defensively", () => {
    const data = makeData(
      { x: [1] },
      {},
      { meta: { params: { binWidth: 2, name: "x" }, xDomain: [1, 9] } },
    );
    expect(chartParams(data.meta).number("binWidth")).toBe(2);
    expect(chartParams(data.meta).number("name")).toBeUndefined();
    expect(chartParams(data.meta).string("name")).toBe("x");
    expect(chartXDomain(data.meta)).toEqual([1, 9]);
    expect(chartXDomain({ ...data.meta, xDomain: null })).toBeUndefined();
  });
});
