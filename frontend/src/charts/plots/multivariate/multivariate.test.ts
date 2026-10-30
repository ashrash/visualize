import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../../../api/types";
import { makeData, makeSpec, makeTheme } from "../../core/testing";
import type { ChartData, PlotEntry } from "../../core/types";
import { validateEntry } from "../../core/validate";
import { linkPoints } from "./dendrogram";
import entries from "./index";
import { componentLabel } from "./pca";

// biome-ignore lint/suspicious/noExplicitAny: tests probe deep into the ECharts option
type Opt = Record<string, any>;

const light = makeTheme();
const dark = makeTheme({
  mode: "dark",
  text: "#eeeeee",
  textMuted: "#aaaaaa",
  background: "#111111",
});

const byKey = (key: string): PlotEntry => {
  const entry = entries.find((e) => e.key === key);
  if (!entry) throw new Error(`no ${key}`);
  return entry;
};

function statData(values: Record<string, unknown>): ChartData {
  return {
    ...makeData({}, {}, { meta: { reduction: "aggregated", method: "test" } }),
    stat: { values: values as never, method: "test", n: 10, sampled: false, nMissing: 0 },
  };
}

const render = (key: string, data: ChartData, params: ChartSpec["params"] = {}, theme = light) => {
  const entry = byKey(key);
  const spec = makeSpec({
    plotType: key as never,
    params: { ...(entry.defaults.params ?? {}), ...params },
  });
  return entry.render(spec, data, theme) as Opt;
};

const radarData = makeData(
  {
    color: ["A", "B"],
    n: [2, 2],
    y0: [2, 6],
    y1: [10, 30],
    y2: [0.5, 1],
    y0_norm: [1 / 6, 5 / 6],
    y1_norm: [0, 2 / 3],
    y2_norm: [0.5, 1],
  },
  { y0: "u", y1: "v", y2: "w", color: "g" },
);

const parallelData = makeData(
  { y0: [1, 2, 3], y1: ["a", "b", "a"], color: ["x", "y", "x"] },
  { y0: "len", y1: "kind", color: "grp" },
  { kinds: { y0: "number", y1: "string", color: "string" } },
);

const pcaData = statData({
  variables: ["a", "b"],
  components: [1, 2],
  explainedVarianceRatio: [0.75, 0.25],
  loadings: [
    { variable: "a", x: 0.9, y: 0.1 },
    { variable: "b", x: -0.5, y: 0.8 },
  ],
  arrowScale: 2,
  points: { x: [1, -1, 0.5], y: [0, 1, -1], color: ["p", "q", "p"], label: null },
});

const factorData = statData({
  variables: ["a", "b", "c"],
  factors: ["Factor 1", "Factor 2"],
  loadings: [
    [0.9, 0.1],
    [0.8, -0.2],
    [0.1, 0.7],
  ],
  communality: [0.82, 0.68, 0.5],
  explainedVarianceRatio: [0.5, 0.2],
});

const clusterData = statData({
  algorithm: "kmeans",
  k: 2,
  explainedVarianceRatio: [0.8, 0.2],
  points: { x: [0, 1, 5, 6], y: [0, 1, 5, 6], cluster: [0, 0, 1, 1], label: null },
  centroids: [
    { cluster: 0, x: 0.5, y: 0.5, size: 2 },
    { cluster: 1, x: 5.5, y: 5.5, size: 2 },
  ],
  elbow: [
    { k: 1, inertia: 50 },
    { k: 2, inertia: 2 },
  ],
  silhouettes: [{ k: 2, silhouette: 0.9 }],
  silhouette: 0.9,
});

const dendrogramData = statData({
  icoord: [
    [5, 5, 15, 15],
    [25, 25, 35, 35],
    [10, 10, 30, 30],
  ],
  dcoord: [
    [0, 1, 1, 0],
    [0, 2, 2, 0],
    [1, 4, 4, 2],
  ],
  colors: [0, 1, -1],
  leaves: ["a", "b", "c", "d"],
  leafCounts: [1, 1, 1, 1],
  cutHeight: 3,
});

const andrewsData = makeData(
  {
    curve: [0, 0, 1, 1],
    x: [-3.14, 3.14, -3.14, 3.14],
    y0: [1, 2, 3, 4],
    color: ["a", "a", "b", "b"],
  },
  { curve: "Row", x: "t", y0: "f(t)", color: "species" },
);

const FIXTURES: [string, ChartData][] = [
  ["radar", radarData],
  ["parallel_coordinates", parallelData],
  ["pca_biplot", pcaData],
  ["factor_loading", factorData],
  ["cluster", clusterData],
  ["dendrogram", dendrogramData],
  ["andrews_curves", andrewsData],
];

describe("multivariate registry entries", () => {
  it("registers every §6 plot, each valid", () => {
    expect(entries.map((e) => e.key).sort()).toEqual(
      [
        "andrews_curves",
        "cluster",
        "dendrogram",
        "factor_loading",
        "parallel_coordinates",
        "pca_biplot",
        "radar",
      ].sort(),
    );
    for (const entry of entries) {
      expect(validateEntry(entry)).toEqual([]);
      expect(entry.family).toBe("multivariate");
      // Every param the panel can set has a default, so specs round-trip to the server.
      for (const p of entry.paramsSchema) {
        if (p.type !== "column") expect(entry.defaults.params).toHaveProperty(p.name);
      }
    }
  });

  it.each(FIXTURES)("%s renders the same option in light and dark (snapshot)", (key, data) => {
    expect(render(key, data)).toMatchSnapshot("light");
    expect(render(key, data, {}, dark)).toMatchSnapshot("dark");
  });

  it.each(FIXTURES)("%s renders an empty result without throwing", (key) => {
    const empty =
      key === "radar" || key === "parallel_coordinates" || key === "andrews_curves"
        ? makeData({})
        : statData({});
    expect(() => render(key, empty)).not.toThrow();
  });

  it("recommends on suitable columns only", () => {
    const quant = (name: string) => ({
      name,
      type: "DOUBLE",
      semantic: "quantitative" as const,
      nullCount: 0,
      approxDistinct: 100,
    });
    const species = {
      name: "species",
      type: "VARCHAR",
      semantic: "nominal" as const,
      nullCount: 0,
      approxDistinct: 3,
    };
    const cols = [quant("a"), quant("b"), quant("c"), quant("d"), species];
    for (const entry of entries) {
      const score = entry.recommend?.(cols) ?? 0;
      expect(score).toBeGreaterThan(0);
      expect(entry.recommend?.([species]) ?? 0).toBeLessThanOrEqual(0.05);
    }
  });
});

describe("radar", () => {
  it("draws scaled values with raw values in the tooltip", () => {
    const o = render("radar", radarData);
    expect(o.radar.indicator.map((i: Opt) => [i.name, i.min, i.max])).toEqual([
      ["u", 0, 1],
      ["v", 0, 1],
      ["w", 0, 1],
    ]);
    expect(o.series[0].data[1].value).toEqual([5 / 6, 2 / 3, 1]);
    expect(o.series[0].tooltip.formatter({ name: "B", data: o.series[0].data[1] })).toContain(
      "Mean u: 6",
    );
    const raw = render("radar", radarData, { scale: "none" });
    expect(raw.series[0].data[1].value).toEqual([6, 30, 1]);
    expect(raw.radar.indicator[1].max).toBeCloseTo(33);
  });
});

describe("parallel coordinates", () => {
  it("makes category axes for text and one series per color group", () => {
    const o = render("parallel_coordinates", parallelData);
    expect(o.parallelAxis[1]).toMatchObject({ type: "category", data: ["a", "b"] });
    expect(o.series.map((s: Opt) => [s.name, s.data.length])).toEqual([
      ["x", 2],
      ["y", 1],
    ]);
  });
});

describe("PCA biplot", () => {
  it("labels axes with explained variance and scales arrows", () => {
    expect(componentLabel(2, [0.75, 0.25])).toBe("PC2 (25.0%)");
    const o = render("pca_biplot", pcaData);
    expect(o.xAxis.name).toBe("PC1 (75.0%)");
    const arrows = o.series.find((s: Opt) => s.name === "Loadings");
    expect(arrows.markLine.data[0][1].coord).toEqual([1.8, 0.2]);
    expect(o.series.filter((s: Opt) => s.type === "scatter" && s.name !== "Loadings")).toHaveLength(
      2,
    );
    const hidden = render("pca_biplot", pcaData, { showLoadings: false });
    expect(hidden.series.some((s: Opt) => s.name === "Loadings")).toBe(false);
  });
});

describe("factor loadings", () => {
  it("draws a diverging heatmap with the first variable on top, or bars", () => {
    const o = render("factor_loading", factorData);
    expect(o.yAxis.data).toEqual(["c", "b", "a"]);
    expect(o.visualMap).toMatchObject({ min: -1, max: 1 });
    expect(o.series[0].data).toContainEqual([0, 2, 0.9]);
    const bars = render("factor_loading", factorData, { display: "bars" });
    expect(bars.series).toHaveLength(2);
    expect(bars.series[0].data).toEqual([0.1, 0.8, 0.9]);
  });
});

describe("cluster plot", () => {
  it("draws clusters with centroids, and the elbow view", () => {
    const o = render("cluster", clusterData);
    expect(o.series.map((s: Opt) => s.name)).toEqual(["Cluster 1", "Cluster 2", "Centroids"]);
    const elbow = render("cluster", clusterData, { view: "elbow" });
    expect(elbow.series[0].data).toEqual([
      [1, 50],
      [2, 2],
    ]);
    const entry = byKey("cluster");
    const spec = makeSpec({ plotType: "cluster", params: { view: "kdistance" } });
    expect(entry.warnings?.(spec, clusterData)[0]).toContain("not available");
  });
});

describe("dendrogram", () => {
  it("maps SciPy coordinates onto leaf positions", () => {
    expect(linkPoints([5, 5, 15, 15], [0, 1, 1, 0])).toEqual([
      [0, 0],
      [0, 1],
      [1, 1],
      [1, 0],
    ]);
    const o = render("dendrogram", dendrogramData);
    expect(o.xAxis.data).toEqual(["a", "b", "c", "d"]);
    expect(o.series[0].data).toHaveLength(3);
    expect(o.series[0].data[2]).toEqual([2, 4]);
    expect(o.series[0].markLine.data).toEqual([{ yAxis: 3 }]);
    const left = render("dendrogram", dendrogramData, { orientation: "left" });
    expect(left.yAxis.data).toEqual(["a", "b", "c", "d"]);
    // renderItem draws polylines through api.coord.
    const api = {
      value: () => 0,
      coord: ([x, y]: number[]) => [(x ?? 0) * 100, 400 - (y ?? 0) * 10],
      size: () => [100, 10],
    };
    const shape = o.series[0].renderItem({ dataIndex: 0 }, api);
    expect(shape.shape.points).toEqual([
      [0, 400],
      [0, 390],
      [100, 390],
      [100, 400],
    ]);
  });
});

describe("Andrews curves", () => {
  it("draws one line per row, colored and named by group", () => {
    const o = render("andrews_curves", andrewsData);
    expect(o.series.map((s: Opt) => s.name)).toEqual(["a", "b"]);
    expect(o.series[1].data).toEqual([
      [-3.14, 3],
      [3.14, 4],
    ]);
    expect(o.series[0].lineStyle.color).not.toBe(o.series[1].lineStyle.color);
  });
});
