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
    // A real time extent from the data (not the [0, 1] ms default a custom series with no
    // "real" dimensions in its data would otherwise leave every bar near 1970-01-01, off-canvas).
    expect(o.xAxis.min).toBe(Date.parse("2024-01-01"));
    expect(o.xAxis.max).toBe(Date.parse("2024-01-20"));
    expect(o.yAxis.data).toEqual(["Design", "Build"]);
    expect(o.series[0].type).toBe("custom");
    expect(o.series[0].data).toHaveLength(2);
    // Each data item carries real [categoryIndex, startMs, endMs] dimensions for ECharts'
    // own axis-extent inference, matching `encode: {x: [1, 2], y: 0}`.
    expect(o.series[0].data[0]).toEqual([0, Date.parse("2024-01-01"), Date.parse("2024-01-09")]);
    expect(o.series[0].encode).toEqual({ x: [1, 2], y: 0 });
  });

  it("renders real bars through ECharts SSR, not off-canvas at the epoch", async () => {
    const { echarts } = await import("../../core/echarts");
    const data = makeData(
      {
        label: ["Design", "Build"],
        start: ["2024-01-01", "2024-01-10"],
        end: ["2024-01-09", "2024-01-20"],
      },
      { label: "task" },
    );
    const o = render("gantt", data);
    const chart = echarts.init(null, undefined, {
      renderer: "svg",
      ssr: true,
      width: 400,
      height: 200,
    });
    try {
      chart.setOption({ animation: false, ...o });
      const svg = chart.renderToSVGString();
      // ECharts' SVG renderer draws a custom series' "rect" shapes as <path> elements (an
      // earlier version of this test matched <rect> instead, which only exists for the
      // background/grid and would have passed even against the original broken renderer).
      // Pulling out each path's starting "M x,y" and checking it lands on the 400x200 canvas is
      // what actually catches a bar placed off-canvas near the 1970 epoch.
      const starts = [...svg.matchAll(/<path[^>]*\sd="M\s*(-?[\d.]+)[ ,]\s*(-?[\d.]+)/g)].map(
        (m) => [Number(m[1]), Number(m[2])] as const,
      );
      expect(starts.length).toBeGreaterThan(0);
      for (const [x, y] of starts) {
        expect(x).toBeGreaterThanOrEqual(-10);
        expect(x).toBeLessThanOrEqual(410);
        expect(y).toBeGreaterThanOrEqual(-10);
        expect(y).toBeLessThanOrEqual(210);
      }
    } finally {
      chart.dispose();
    }
  });

  it("drops a task with an unparseable start/end instead of placing it at the epoch", () => {
    const data = makeData(
      {
        label: ["Design", "Bad"],
        start: ["2024-01-01", "not-a-date"],
        end: ["2024-01-09", "2024-01-20"],
      },
      { label: "task" },
    );
    const o = render("gantt", data);
    expect(o.yAxis.data).toEqual(["Design"]);
    expect(o.series[0].data).toHaveLength(1);
    expect(o.xAxis.min).toBe(Date.parse("2024-01-01"));
    expect(o.xAxis.max).toBe(Date.parse("2024-01-09"));
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

/** Renders `option` through real ECharts (SSR, no DOM) and returns the SVG string. Layout bugs
 * (an axis with no real extent, a custom series whose coordinates land far off-canvas) show up
 * here in a way plain option-shape assertions can't catch. */
async function ssr(option: Opt): Promise<string> {
  const { echarts } = await import("../../core/echarts");
  const chart = echarts.init(null, undefined, {
    renderer: "svg",
    ssr: true,
    width: 500,
    height: 350,
  });
  try {
    chart.setOption({ animation: false, ...option });
    return chart.renderToSVGString();
  } finally {
    chart.dispose();
  }
}

describe("visual sanity through real ECharts SSR", () => {
  it("sankey with a node rollup keeps most of the graph on-canvas", async () => {
    // channel_to_page / page_to_outcome style data, like flows.csv. The backend's own
    // cycle-detection (viz.plots.specialized._drop_cycles, covered by its own known-answer
    // tests) guarantees the renderer never actually receives a cyclic graph; ECharts' sankey
    // layout throws outright if it does, which is exactly why that guarantee matters.
    const nodes = [
      ...Array.from({ length: 6 }, (_, i) => ({ name: `Channel${i}`, value: 10 })),
      { name: "Page", value: 60 },
      { name: "Outcome", value: 50 },
    ];
    const links = [
      ...Array.from({ length: 6 }, (_, i) => ({
        source: `Channel${i}`,
        target: "Page",
        value: 10,
      })),
      { source: "Page", target: "Outcome", value: 50 },
    ];
    const data = withStat({
      values: { nodes, links },
      method: "source_target_aggregate",
      n: links.length,
      sampled: false,
      warnings: [],
      nMissing: 0,
    });
    const o = render("sankey", data, { maxNodes: 5 });
    const svg = await ssr(o);
    expect(svg).toContain("<svg");
    expect(svg).not.toMatch(/NaN/);
    // Several node rects/paths should have been drawn, not collapsed to nothing.
    expect((svg.match(/<path/g) ?? []).length).toBeGreaterThan(3);
  });

  it("a 3-stage alluvial with a rollup renders without losing its stage structure", async () => {
    const stageValue = (stage: number, i: number) => `${stage}⁣v${i}`;
    const nodes = [0, 1, 2].flatMap((stage) =>
      Array.from({ length: 6 }, (_, i) => ({
        name: stageValue(stage, i),
        label: `v${i}`,
        depth: stage,
        value: 6 - i,
      })),
    );
    const links = [0, 1].flatMap((stage) =>
      Array.from({ length: 6 }, (_, i) => ({
        source: stageValue(stage, i),
        target: stageValue(stage + 1, i),
        value: 6 - i,
      })),
    );
    const data = withStat({
      values: { nodes, links, stages: 3 },
      method: "stage_aggregate",
      n: links.length,
      sampled: false,
      warnings: [],
      nMissing: 0,
    });
    const o = render("alluvial", data, { maxNodes: 3 });
    const svg = await ssr(o);
    expect(svg).toContain("<svg");
    expect(svg).not.toMatch(/NaN/);
    expect((svg.match(/<path/g) ?? []).length).toBeGreaterThan(3);
  });

  it("a network with 40+ nodes places force-laid-out nodes on-canvas", async () => {
    const n = 45;
    const nodes = Array.from({ length: n }, (_, i) => ({ name: `n${i}`, value: (i % 5) + 1 }));
    const links = Array.from({ length: n - 1 }, (_, i) => ({
      source: `n${i}`,
      target: `n${i + 1}`,
      value: 1,
    }));
    const data = withStat({
      values: { nodes, links },
      method: "edge_aggregate",
      n: links.length,
      sampled: false,
      warnings: [],
      nMissing: 0,
    });
    const o = render("network", data, { maxNodes: 50 });
    const svg = await ssr(o);
    expect(svg).toContain("<svg");
    expect(svg).not.toMatch(/NaN/);
  });
});
