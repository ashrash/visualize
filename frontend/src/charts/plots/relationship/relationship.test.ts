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

  it("labels row 0's y axis on its diagonal cell too, not just the off-diagonal one", () => {
    const data = makeData(
      { dimension0: [1, 2, 3], dimension1: [3, 2, 1] },
      { dimension0: "bill_length_mm", dimension1: "bill_depth_mm" },
    );
    const o = render("pair", {}, data);
    // Grid cells are row-major: [0,0] diagonal, [0,1] off-diagonal, [1,0] off-diagonal, [1,1]
    // diagonal. Column 0 (cells 0 and 2) should carry dimension0's label on both its cells;
    // previously the diagonal cell (0) had no `name` at all.
    expect(o.yAxis[0].name).toBe("bill_length_mm");
    expect(o.yAxis[2].name).toBe("bill_depth_mm");
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

  it("escapes a crafted field/category name in the tooltip (stored XSS)", () => {
    const malicious = '<img src=x onerror="alert(1)">';
    const data = makeData(
      { x: [malicious, malicious], y: [malicious, malicious], value: [1, 1] },
      { x: "Field", y: "Field", value: "r" },
    );
    const o = render("correlation_heatmap", {}, data);
    const html = o.tooltip.formatter({ value: [0, 0, 1] });
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
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

  it("escapes a crafted field/category name in the tooltip (stored XSS)", () => {
    const malicious = "<script>alert(1)</script>";
    const data = makeData(
      { x: [malicious], y: [malicious], value: [1], n: [10], p: [0.001] },
      { x: "Field", y: "Field", value: "r" },
    );
    const o = render("correlogram", {}, data);
    const html = o.tooltip.formatter({ value: [0, 0, 1, 0.001] });
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });

  it("shows a small p-value as '<0.001' (HTML-escaped) rather than rounding it to 0.000", () => {
    const data = makeData(
      { x: ["a"], y: ["b"], value: [0.9], n: [100], p: [0.0000001] },
      { x: "Field", y: "Field", value: "r" },
    );
    const o = render("correlogram", {}, data);
    const html = o.tooltip.formatter({ value: [0, 0, 0.9, 0.0000001] });
    expect(html).toContain("&lt;0.001");
  });
});

describe("hexbin", () => {
  it("draws one hexagon polygon per cell, colored by count", () => {
    const data = makeData({ x: [0, 1], y: [0, 1], count: [5, 10] }, { x: "a", y: "b" });
    const o = render("hexbin", {}, data);
    expect(o.series[0].type).toBe("custom");
    expect(o.visualMap.max).toBe(10);
  });

  it("sizes hexagons from the server's xPerPx/yPerPx via api.coord, not a fixed pixel radius", () => {
    const data = makeData(
      { x: [0], y: [0], count: [1] },
      { x: "a", y: "b" },
      { meta: { params: { radiusPx: 10, xPerPx: 2, yPerPx: 5 } } },
    );
    const o = render("hexbin", {}, data);
    // A mock api: coord() just scales data-space points by a fixed factor, standing in for
    // whatever the chart's actual (possibly nice-rounded, margin-shrunk) axis scale is.
    const coordCalls: number[][] = [];
    const api = {
      value: (dim: number) => [0, 0][dim],
      coord: (p: number[]) => {
        coordCalls.push(p);
        return [(p[0] ?? 0) * 3, (p[1] ?? 0) * 3];
      },
      style: () => ({}),
    };
    const shape = o.series[0].renderItem({}, api);
    expect(shape.shape.points).toHaveLength(6);
    // Each vertex offset is radiusPx * xPerPx/yPerPx in data space (not a fixed pixel radius
    // applied after conversion): the first vertex is at angle -30deg, so dx = 10*2*cos(-30deg).
    const [first] = coordCalls;
    expect(first?.[0]).toBeCloseTo(10 * 2 * Math.cos((-30 * Math.PI) / 180));
    expect(first?.[1]).toBeCloseTo(10 * 5 * Math.sin((-30 * Math.PI) / 180));
  });
});

describe("contour", () => {
  // Does NOT use the official @echarts-x/custom-contour series (see contour.ts/relationship.py
  // for why), and is not true isoline contouring either: the server now always returns a
  // `bin2d` density grid (the same shape scatter/bubble fall back to over their mark cap), and
  // the renderer reuses the shared `densityFallback` primitive directly.
  it("draws the server's bin2d grid via densityFallback", () => {
    const data = makeData(
      { x_lo: [0, 1], x_hi: [1, 2], y_lo: [0, 1], y_hi: [1, 2], count: [3, 7] },
      { x_lo: "a", x_hi: "a", y_lo: "b", y_hi: "b", count: "Count" },
      { meta: { reduction: "binned" } },
    );
    const o = render("contour", {}, data);
    expect(o.series).toHaveLength(1);
    expect(o.series[0].type).toBe("custom");
    expect(o.series[0].data).toEqual([
      [0, 1, 0, 1, 3],
      [1, 2, 1, 2, 7],
    ]);
    expect(o.visualMap).toBeDefined();
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

  it("accepts a temporal x (the backend fits NUMERIC_OR_TIME for regression)", () => {
    const xChannel = byKey("regression").encodingsSchema.find((c) => c.name === "x");
    expect(xChannel?.accepts).toContain("temporal");
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

  it("does not accept a temporal x (the backend's residual fit is quantitative-only)", () => {
    const xChannel = byKey("residual").encodingsSchema.find((c) => c.name === "x");
    expect(xChannel?.accepts).toEqual(["quantitative"]);
  });
});
