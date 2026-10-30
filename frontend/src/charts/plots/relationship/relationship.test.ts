import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../../../api/types";
import { makeData, makeSpec, makeTheme } from "../../core/testing";
import type { OverlayResult, PlotEntry } from "../../core/types";
import entries from "./index";

const byKey = (key: string): PlotEntry => {
  const entry = entries.find((e) => e.key === key);
  if (!entry) throw new Error(`no ${key}`);
  return entry;
};

const theme = makeTheme();
// biome-ignore lint/suspicious/noExplicitAny: tests probe deep into the ECharts option
type Opt = Record<string, any>;

const render = (
  key: string,
  spec: Partial<ChartSpec>,
  data: ReturnType<typeof makeData>,
  ctx = undefined as never,
) => byKey(key).render(makeSpec({ plotType: key as never, ...spec }), data, theme, ctx) as Opt;

describe("scatter", () => {
  it("draws raw points under the cap", () => {
    const data = makeData({ x: [1, 2, 3], y: [4, 5, 6] }, { x: "a", y: "b" });
    const o = render("scatter", {}, data);
    expect(o.series).toHaveLength(1);
    expect(o.series[0].data).toEqual([
      { value: [1, 4], symbolSize: undefined },
      { value: [2, 5], symbolSize: undefined },
      { value: [3, 6], symbolSize: undefined },
    ]);
  });

  it("draws one series per color group", () => {
    const data = makeData(
      { x: [1, 2, 3], y: [4, 5, 6], color: ["a", "b", "a"] },
      { x: "x", y: "y" },
    );
    const o = render("scatter", {}, data);
    expect(o.series.map((s: Opt) => s.name)).toEqual(["a", "b"]);
  });

  it("switches to a density heatmap over the cap (reduction: binned)", () => {
    const data = makeData(
      { x_lo: [0], x_hi: [1], y_lo: [0], y_hi: [1], count: [42] },
      { x_lo: "a", x_hi: "a", y_lo: "b", y_hi: "b", count: "Count" },
      { meta: { reduction: "binned" } },
    );
    const o = render("scatter", {}, data);
    expect(o.series[0].type).toBe("custom");
    expect(o.visualMap).toBeDefined();
  });
});

describe("bubble", () => {
  it("scales symbol size by the size field", () => {
    const data = makeData({ x: [1, 2], y: [3, 4], size: [1, 100] }, { x: "a", y: "b" });
    const o = render("bubble", {}, data);
    const sizes = o.series[0].data.map((d: Opt) => d.symbolSize);
    expect(sizes[0]).toBeLessThan(sizes[1]);
  });
});

describe("pair plot", () => {
  it("lays out an NxN grid with a histogram on the diagonal", () => {
    const data = makeData(
      { dimension0: [1, 2, 3], dimension1: [3, 2, 1] },
      { dimension0: "a", dimension1: "b" },
    );
    const o = render("pair", {}, data);
    expect(o.grid).toHaveLength(4);
    expect(o.xAxis).toHaveLength(4);
    const types = o.series.map((s: Opt) => s.type);
    expect(types.filter((t: string) => t === "bar")).toHaveLength(2);
    expect(types.filter((t: string) => t === "scatter")).toHaveLength(2);
  });
});

describe("correlation heatmap", () => {
  it("places cells by field index and colors by value", () => {
    const data = makeData(
      { x: ["a", "a", "b", "b"], y: ["a", "b", "a", "b"], value: [1, 0.5, 0.5, 1] },
      { x: "Field", y: "Field", value: "r" },
    );
    const o = render("correlation_heatmap", {}, data);
    expect(o.xAxis.data).toEqual(["a", "b"]);
    expect(o.series[0].data).toEqual([
      [0, 0, 1],
      [1, 0, 0.5],
      [0, 1, 0.5],
      [1, 1, 1],
    ]);
    expect(o.visualMap.min).toBe(-1);
    expect(o.visualMap.max).toBe(1);
  });
});

describe("correlogram", () => {
  it("sizes points by the magnitude of the correlation", () => {
    const data = makeData(
      { x: ["a", "a"], y: ["a", "b"], value: [1, 0.2], n: [10, 10], p: [0.001, 0.3] },
      { x: "Field", y: "Field", value: "r" },
    );
    const o = render("correlogram", {}, data);
    const sizes = o.series[0].data.map((d: Opt) => d.symbolSize);
    expect(sizes[0]).toBeGreaterThan(sizes[1]);
  });
});

describe("hexbin", () => {
  it("draws one hexagon polygon per cell, colored by count", () => {
    const data = makeData({ x: [0, 1], y: [0, 1], count: [5, 10] }, { x: "a", y: "b" });
    const o = render("hexbin", {}, data);
    expect(o.series[0].type).toBe("custom");
    expect(o.visualMap.max).toBe(10);
  });
});

describe("contour", () => {
  // Does NOT use the official @echarts-x/custom-contour series: it transitively imports the
  // full (non-tree-shaken) "echarts" package, which breaks CLAUDE.md's bundle-size rule and was
  // observed to break charts/core/render.test.ts's unregistered-component check as a side
  // effect. See the comment in contour.ts.
  it("bins the raw points into a heatmap, with counts summing to the point count", () => {
    const data = makeData(
      { x: [1, 1.1, 1.2, 5, 5.1], y: [1, 1.1, 1.2, 5, 5.1] },
      { x: "a", y: "b" },
    );
    const o = render("contour", { params: { grid: 4 } }, data);
    const heat = o.series.find((s: Opt) => s.type === "heatmap");
    expect(heat).toBeDefined();
    const total = heat.data.reduce((sum: number, [, , c]: number[]) => sum + (c ?? 0), 0);
    expect(total).toBe(5);
    expect(o.xAxis.data).toHaveLength(4);
    expect(o.series.some((s: Opt) => s.name === "Points")).toBe(false);
  });

  it("shows a points layer when showPoints is true", () => {
    const data = makeData({ x: [1, 2], y: [1, 2] }, { x: "a", y: "b" });
    const o = render("contour", { params: { showPoints: true } }, data);
    expect(o.series.some((s: Opt) => s.name === "Points")).toBe(true);
  });
});

describe("regression", () => {
  it("draws the scatter and, given an overlay, the fitted line with a band", () => {
    const data = makeData({ x: [1, 2, 3], y: [2, 4, 6] }, { x: "a", y: "b" });
    const overlay: OverlayResult = {
      kind: "linear",
      series: 0,
      group: null,
      x: [1, 3],
      y: [2, 6],
      lo: [1.5, 5.5],
      hi: [2.5, 6.5],
      equation: "y = 2x",
      r2: 0.99,
      n: 3,
      sampled: false,
      warnings: [],
    };
    const o = render("regression", {}, data, { overlays: [overlay] } as never);
    const scatterSeries = o.series.find((s: Opt) => s.type === "scatter");
    expect(scatterSeries.data).toEqual([
      [1, 2],
      [2, 4],
      [3, 6],
    ]);
    const trend = o.series.find((s: Opt) => s.type === "line" && s.lineStyle?.type === "dashed");
    expect(trend).toBeDefined();
  });
});

describe("residual", () => {
  it("draws fitted-vs-residual points with a y=0 reference line", () => {
    const data = makeData({ x: [1, 2, 3], y: [0.1, -0.2, 0.05] }, { x: "a", y: "b" });
    // The plot's own `defaults.refLines` only applies via applyPlotDefaults/createChartSpec in
    // the real app; tests set the style explicitly, as the other render tests in this repo do.
    const spec = {
      style: { refLines: [{ axis: "y", value: 0, label: "0" }] } as ChartSpec["style"],
    };
    const o = render("residual", spec, data);
    expect(o.series[0].markLine.data[0]).toMatchObject({ yAxis: 0 });
  });

  it("declares a y=0 reference line as its own default", () => {
    expect(byKey("residual").defaults.style?.refLines?.[0]).toMatchObject({ axis: "y", value: 0 });
  });

  it("regression declares a linear trendline with a confidence band as its own default", () => {
    expect(byKey("regression").defaults.style?.trendlines?.[0]).toMatchObject({
      kind: "linear",
      series: 0,
      ciLevel: 0.95,
    });
  });
});
