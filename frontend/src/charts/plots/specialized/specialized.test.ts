import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../../../api/types";
import { makeData, makeSpec, makeTheme } from "../../core/testing";
import type { PlotEntry, StatResult } from "../../core/types";
import entries from "./index";

const byKey = (key: string): PlotEntry => {
  const entry = entries.find((e) => e.key === key);
  if (!entry) throw new Error(`no ${key}`);
  return entry;
};

const theme = makeTheme();
// biome-ignore lint/suspicious/noExplicitAny: tests probe deep into the ECharts option
type Opt = Record<string, any>;

function withStat(stat: StatResult) {
  const data = makeData({});
  return { ...data, stat };
}

const render = (
  key: string,
  data: ReturnType<typeof makeData>,
  params: Record<string, unknown> = {},
) =>
  byKey(key).render(
    makeSpec({ plotType: key as ChartSpec["plotType"], params }),
    data,
    theme,
  ) as Opt;

describe("sankey", () => {
  it("draws nodes and links from the stat result", () => {
    const data = withStat({
      values: {
        nodes: [
          { name: "Ads", value: 15 },
          { name: "Home", value: 15 },
        ],
        links: [{ source: "Ads", target: "Home", value: 15 }],
      },
      method: "source_target_aggregate",
      n: 1,
      sampled: false,
      warnings: [],
      nMissing: 0,
    });
    const o = render("sankey", data);
    expect(o.series[0].type).toBe("sankey");
    expect(o.series[0].data).toEqual([{ name: "Ads" }, { name: "Home" }]);
    expect(o.series[0].links).toEqual([{ source: "Ads", target: "Home", value: 15 }]);
  });
});

describe("alluvial", () => {
  it("labels nodes by their stage-stripped name", () => {
    const data = withStat({
      values: {
        nodes: [{ name: "0⁣Ads", value: 10, label: "Ads", depth: 0 }],
        links: [],
        stages: 2,
      },
      method: "stage_aggregate",
      n: 0,
      sampled: false,
      warnings: [],
      nMissing: 0,
    });
    const o = render("alluvial", data);
    expect(o.series[0].data[0]).toMatchObject({ name: "0⁣Ads", depth: 0 });
  });
});

describe("network", () => {
  it("sizes node symbols by their relative total", () => {
    const data = withStat({
      values: {
        nodes: [
          { name: "A", value: 100 },
          { name: "B", value: 10 },
        ],
        links: [{ source: "A", target: "B", value: 10 }],
      },
      method: "edge_aggregate",
      n: 1,
      sampled: false,
      warnings: [],
      nMissing: 0,
    });
    const o = render("network", data);
    expect(o.series[0].type).toBe("graph");
    expect(o.series[0].data[0].symbolSize).toBeGreaterThan(o.series[0].data[1].symbolSize);
  });
});

describe("gantt", () => {
  it("draws one task bar per row on a time axis", () => {
    const data = makeData(
      {
        label: ["Design", "Build"],
        start: ["2024-01-01", "2024-01-10"],
        end: ["2024-01-09", "2024-01-20"],
      },
      { label: "task" },
    );
    const o = render("gantt", data);
    expect(o.xAxis.type).toBe("time");
    expect(o.yAxis.data).toEqual(["Design", "Build"]);
    expect(o.series[0].type).toBe("custom");
    expect(o.series[0].data).toHaveLength(2);
  });
});

describe("pareto", () => {
  it("puts the cumulative-% line on a second y axis", () => {
    const data = makeData(
      { x: ["Dent", "Crack", "Scratch"], y0: [50, 40, 10], cumPercent: [50, 90, 100] },
      { x: "defect", y0: "count", cumPercent: "Cumulative %" },
    );
    const o = render("pareto", data);
    expect(o.series).toHaveLength(2);
    expect(o.series[0].type).toBe("bar");
    expect(o.series[1].type).toBe("line");
    expect(o.yAxis).toHaveLength(2);
    expect(o.series[1].data).toEqual([50, 90, 100]);
  });
});

describe("error_bar", () => {
  it("draws a mean marker plus an error-bar series", () => {
    const data = makeData(
      { x: ["A", "B"], y0: [20, 6], y0_lo: [10, null], y0_hi: [30, null] },
      { x: "species", y0: "mean mass" },
    );
    const o = render("error_bar", data);
    expect(o.series).toHaveLength(2);
    expect(o.series[0].type).toBe("scatter");
    expect(o.series[0].data).toEqual([20, 6]);
    expect(o.series[1].type).toBe("custom");
  });
});

describe("lollipop", () => {
  it("draws a thin stem and a dot per category", () => {
    const data = makeData({ x: ["A", "B", "C"], y0: [5, 50, 10] }, { x: "defect", y0: "count" });
    const o = render("lollipop", data);
    expect(o.series).toHaveLength(2);
    expect(o.series[0].type).toBe("bar");
    expect(o.series[0].barWidth).toBe(2);
    expect(o.series[1].type).toBe("scatter");
    expect(o.series[1].data).toEqual([5, 50, 10]);
  });
});

describe("dumbbell", () => {
  it("draws a connecting segment plus start/end scatter points", () => {
    const data = makeData(
      { x: ["A", "B"], y0: [60, 70], y1: [65, 68] },
      { x: "country", y0: "2000", y1: "2020" },
    );
    const o = render("dumbbell", data);
    expect(o.series).toHaveLength(3);
    expect(o.series[0].type).toBe("custom"); // the error-bar-primitive segment
    expect(o.series[1].data).toEqual([60, 70]);
    expect(o.series[2].data).toEqual([65, 68]);
  });
});

describe("ridgeline", () => {
  it("builds one offset density curve per group", () => {
    const data = withStat({
      values: {
        groups: [
          { key: "Jan", n: 4, x: [0, 1, 2], density: [0.1, 0.5, 0.1] },
          { key: "Jul", n: 4, x: [0, 1, 2], density: [0.2, 0.4, 0.2] },
        ],
      },
      method: "kde_silverman",
      n: 8,
      sampled: false,
      warnings: [],
      nMissing: 0,
    });
    const o = render("ridgeline", data);
    // offsetKde makes 2 series per group (an invisible base + a filled curve).
    expect(o.series).toHaveLength(4);
    expect(o.yAxis.min).toBe(0);
  });
});

describe("raincloud", () => {
  it("draws a density curve, a box and jittered points per group", () => {
    const data = withStat({
      values: {
        groups: [
          {
            key: "A",
            n: 5,
            density: { x: [0, 1, 2], density: [0.1, 0.5, 0.1] },
            box: { n: 5, q1: 20, median: 30, q3: 40, lo: 10, hi: 50 },
            points: [10, 20, 30, 40, 50],
          },
        ],
      },
      method: "kde_silverman+quartiles+sample",
      n: 5,
      sampled: false,
      warnings: [],
      nMissing: 0,
    });
    const o = render("raincloud", data);
    const types = o.series.map((s: Opt) => s.type);
    expect(types).toContain("custom"); // the box
    expect(types).toContain("scatter"); // the jittered points
    const points = o.series.find((s: Opt) => s.name === "points");
    expect(points.data).toHaveLength(5);
  });
});
