import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../../../api/types";
import { makeData, makeSpec, makeTheme } from "../../core/testing";
import type { PlotEntry } from "../../core/types";
import entries from "./index";

const byKey = (key: string): PlotEntry => {
  const entry = entries.find((e) => e.key === key);
  if (!entry) throw new Error(`no ${key}`);
  return entry;
};

const theme = makeTheme();
// biome-ignore lint/suspicious/noExplicitAny: tests probe deep into the ECharts option
type Opt = Record<string, any>;

const render = (key: string, spec: Partial<ChartSpec>, data: ReturnType<typeof makeData>) =>
  byKey(key).render(makeSpec({ plotType: key as never, ...spec }), data, theme, undefined) as Opt;

describe("histogram", () => {
  const data = makeData(
    { x_lo: [0, 10, 20], x_hi: [10, 20, 30], count: [3, 5, 2] },
    { x_lo: "value", x_hi: "value", count: "Count" },
  );

  it("draws one bar per bin with contiguous category gaps", () => {
    const o = render("histogram", {}, data);
    expect(o.xAxis.type).toBe("category");
    expect(o.xAxis.data).toHaveLength(3);
    expect(o.series).toHaveLength(1);
    expect(o.series[0]).toMatchObject({ type: "bar", data: [3, 5, 2], barCategoryGap: "0%" });
  });

  it("pivots a color column into one series per group on shared bins", () => {
    const grouped = makeData(
      {
        x_lo: [0, 10, 0, 10],
        x_hi: [10, 20, 10, 20],
        count: [3, 5, 1, 2],
        color: ["a", "a", "b", "b"],
      },
      { x_lo: "v", x_hi: "v", count: "Count" },
    );
    const o = render("histogram", {}, grouped);
    expect(o.series.map((s: Opt) => s.name)).toEqual(["a", "b"]);
    expect(o.series[0].data).toEqual([3, 5]);
    expect(o.series[1].data).toEqual([1, 2]);
    expect(o.legend.show).toBe(true);
  });
});

describe("frequency polygon", () => {
  it("draws a line through the bin midpoints", () => {
    const data = makeData({ x: [5, 15, 25], y0: [3, 5, 2] }, { x: "v", y0: "Count" });
    const o = render("frequency_polygon", {}, data);
    expect(o.series[0].data).toEqual([
      [5, 3],
      [15, 5],
      [25, 2],
    ]);
  });
});

describe("density", () => {
  it("draws one filled line for an ungrouped curve", () => {
    const data = makeData({ x: [0, 1, 2], y0: [0.1, 0.4, 0.1] }, { x: "v", y0: "Density" });
    const o = render("density", {}, data);
    expect(o.series).toHaveLength(1);
    expect(o.series[0].areaStyle).toBeDefined();
  });

  it("draws one curve per color group", () => {
    const data = makeData(
      { x: [0, 1, 0, 1], y0: [0.1, 0.2, 0.3, 0.4], color: ["a", "a", "b", "b"] },
      { x: "v" },
    );
    const o = render("density", {}, data);
    expect(o.series.map((s: Opt) => s.name)).toEqual(["a", "b"]);
  });
});

describe("box", () => {
  const data = makeData(
    {
      x: ["a", "b"],
      q1: [1, 10],
      median: [2, 20],
      q3: [3, 30],
      lo: [0, 8],
      hi: [4, 32],
      n: [5, 6],
    },
    { x: "group" },
    { meta: { params: { outliers: [{ x: "a", value: 100 }], truncatedOutliers: false } } },
  );

  it("builds [lo, q1, median, q3, hi] rows for the boxplot series", () => {
    const o = render("box", {}, data);
    const boxSeries = o.series.find((s: Opt) => s.type === "boxplot");
    expect(boxSeries.data).toEqual([
      [0, 1, 2, 3, 4],
      [8, 10, 20, 30, 32],
    ]);
  });

  it("plots outliers as a scatter series positioned at their category", () => {
    const o = render("box", {}, data);
    const outlierSeries = o.series.find((s: Opt) => s.type === "scatter");
    expect(outlierSeries.data).toEqual([[0, 100]]);
  });
});

describe("violin", () => {
  it("rescales values into the renderer's fixed [0, 10] sweep and labels the real values back", () => {
    // Real data far outside [0, 10]: the official custom-violin series hard-codes its KDE sweep
    // to that range, so values must be rescaled or the violin would render as an empty sliver.
    const data = makeData(
      { x: ["a", "a", "b"], y: [3000, 4000, 3500] },
      { x: "species", y: "body_mass_g" },
    );
    const o = render("violin", {}, data);
    const series = o.series[0];
    expect(series.renderItem).toBe("violin");
    for (const [, scaledY] of series.data as [number, number][]) {
      expect(scaledY).toBeGreaterThanOrEqual(0);
      expect(scaledY).toBeLessThanOrEqual(10);
    }
    expect(o.yAxis.axisLabel.formatter(0)).toBe("3,000");
    expect(o.yAxis.axisLabel.formatter(10)).toBe("4,000");
  });
});

describe("strip and swarm", () => {
  const data = makeData({ x: ["a", "a", "b"], y: [1, 2, 3] }, { x: "g", y: "v" });

  it("strip jitters within the category, allowing overlap", () => {
    const o = render("strip", {}, data);
    expect(o.xAxis.jitter).toBeGreaterThan(0);
    expect(o.xAxis.jitterOverlap).toBe(true);
    expect(o.series[0].data).toEqual([
      ["a", 1],
      ["a", 2],
      ["b", 3],
    ]);
  });

  it("swarm disables overlap (a beeswarm layout)", () => {
    const o = render("swarm", {}, data);
    expect(o.xAxis.jitterOverlap).toBe(false);
  });
});

describe("Q-Q plot", () => {
  it("draws the sample scatter and a reference line from slope/intercept", () => {
    const data = makeData(
      { x: [-1, 0, 1], y: [8, 10, 12] },
      { x: "Theoretical quantile", y: "value" },
      { meta: { params: { slope: 2, intercept: 10 } } },
    );
    const o = render("qq", {}, data);
    expect(o.series[0].data).toEqual([
      [-1, 8],
      [0, 10],
      [1, 12],
    ]);
    expect(o.series[1].data).toEqual([
      [-1, 8],
      [1, 12],
    ]);
  });
});

describe("ECDF", () => {
  it("draws a step line through the cumulative probabilities", () => {
    const data = makeData({ x: [1, 2, 3], y: [0.25, 0.75, 1] }, { x: "v", y: "F(x)" });
    const o = render("ecdf", {}, data);
    expect(o.series[0]).toMatchObject({ step: "end", type: "line" });
    expect(o.yAxis.max).toBe(1);
  });

  it("draws one step line per color group", () => {
    const data = makeData(
      { x: [1, 2, 3, 4], y: [0.5, 1, 0.5, 1], color: ["a", "a", "b", "b"] },
      { x: "v" },
    );
    const o = render("ecdf", {}, data);
    expect(o.series.map((s: Opt) => s.name)).toEqual(["a", "b"]);
  });
});
