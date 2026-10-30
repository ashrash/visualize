import type { ChartSpec } from "../../../api/types";
import { param } from "../../core/series";
import { applyStyle } from "../../core/style";
import type { ChartData, ChartTheme, RenderContext } from "../../core/types";
import { definePlot } from "../../core/types";
import { componentLabel } from "./pca";
import {
  ANY,
  colorsFor,
  count,
  EMPTY,
  fmt,
  num,
  nums,
  obj,
  objs,
  QUANT,
  statValues,
  str,
  strs,
  type Values,
} from "./stat";

type Obj = Record<string, unknown>;

function hintChart(
  spec: ChartSpec,
  theme: ChartTheme,
  ctx: RenderContext | undefined,
  o: { x: (number | null)[]; y: (number | null)[]; xName: string; yName: string; mark?: number },
) {
  const option = {
    xAxis: { type: "value", name: o.xName, minInterval: 1, scale: true },
    yAxis: { type: "value", name: o.yName, scale: true },
    tooltip: { trigger: "axis" },
    series: [
      {
        type: "line",
        name: o.yName,
        data: o.x.map((x, i) => [x, o.y[i] ?? null]),
        symbol: "circle",
        symbolSize: 6,
        markLine:
          o.mark !== undefined
            ? {
                silent: true,
                symbol: "none",
                lineStyle: { type: "dashed", color: theme.textMuted },
                data: [{ xAxis: o.mark, name: "chosen k" }],
              }
            : undefined,
      },
    ],
  };
  return applyStyle(option, spec, theme, ctx, { orientable: false });
}

function renderClusters(v: Values, spec: ChartSpec, theme: ChartTheme, ctx?: RenderContext) {
  const ratios = nums(v.explainedVarianceRatio);
  const points = obj(v.points);
  const xs = nums(points.x);
  const ys = nums(points.y);
  const clusters = nums(points.cluster).map((c) => c ?? -1);
  const labels = strs(points.label);
  const ids = [...new Set(clusters)].sort((a, b) => a - b);
  const colors = colorsFor(spec, theme, ids.filter((c) => c >= 0).length);
  const series: Obj[] = ids.map((c) => ({
    type: "scatter",
    name: c < 0 ? "Noise" : `Cluster ${c + 1}`,
    symbolSize: c < 0 ? 4 : 6,
    itemStyle: { color: c < 0 ? theme.textMuted : colors[c % colors.length], opacity: 0.75 },
    data: xs
      .map((x, i) => ({ i, x }))
      .filter(({ i }) => clusters[i] === c)
      .map(({ i, x }) => ({ value: [x, ys[i] ?? null], name: labels[i] ?? undefined })),
  }));
  if (param(spec, "showCentroids", true)) {
    const centroids = objs(v.centroids);
    series.push({
      type: "scatter",
      name: "Centroids",
      symbol: "diamond",
      symbolSize: 16,
      z: 5,
      itemStyle: { color: theme.text, borderColor: theme.background ?? "#fff", borderWidth: 1.5 },
      data: centroids.map((c) => ({
        value: [num(c.x), num(c.y)],
        name: `Cluster ${(num(c.cluster) ?? 0) + 1} (${num(c.size) ?? 0} rows)`,
      })),
    });
  }
  const silhouette = num(v.silhouette);
  const option = {
    legend: {},
    xAxis: { type: "value", name: componentLabel(1, ratios), scale: true },
    yAxis: { type: "value", name: componentLabel(2, ratios), scale: true },
    tooltip: {
      trigger: "item",
      formatter: (p: { seriesName: string; name?: string; value: number[] }) =>
        `${p.name ? `<b>${p.name}</b><br/>` : ""}${p.seriesName}<br/>` +
        `PC1: ${fmt(p.value[0])}, PC2: ${fmt(p.value[1])}` +
        (silhouette !== null ? `<br/>Silhouette (all): ${fmt(silhouette)}` : ""),
    },
    series,
  };
  return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "item" });
}

function renderCluster(spec: ChartSpec, data: ChartData, theme: ChartTheme, ctx?: RenderContext) {
  const v = statValues(data);
  if (!v) return applyStyle(EMPTY, spec, theme, ctx);
  const view = param(spec, "view", "clusters");
  const k = num(v.k) ?? undefined;
  if (view === "elbow" && v.elbow) {
    const elbow = objs(v.elbow);
    return hintChart(spec, theme, ctx, {
      x: elbow.map((e) => num(e.k)),
      y: elbow.map((e) => num(e.inertia)),
      xName: "k (clusters)",
      yName: "Within-cluster sum of squares",
      mark: k,
    });
  }
  if (view === "silhouette" && v.silhouettes) {
    const sil = objs(v.silhouettes);
    return hintChart(spec, theme, ctx, {
      x: sil.map((e) => num(e.k)),
      y: sil.map((e) => num(e.silhouette)),
      xName: "k (clusters)",
      yName: "Mean silhouette",
      mark: k,
    });
  }
  if (view === "kdistance" && v.kDistance) {
    const d = nums(v.kDistance);
    return hintChart(spec, theme, ctx, {
      x: d.map((_, i) => i + 1),
      y: d,
      xName: "Points (sorted)",
      yName: `Distance to neighbour ${num(v.minSamples) ?? ""}`.trim(),
    });
  }
  return renderClusters(v, spec, theme, ctx);
}

export const cluster = definePlot({
  key: "cluster",
  label: "Cluster plot",
  family: "multivariate",
  description: "k-means or DBSCAN clusters, shown on the first two principal components.",
  encodingsSchema: [
    {
      name: "y",
      label: "Measures (2+)",
      required: true,
      multiple: true,
      accepts: QUANT,
      aggregate: false,
    },
    {
      name: "label",
      label: "Point label",
      required: false,
      multiple: false,
      accepts: ANY,
      aggregate: false,
    },
  ],
  paramsSchema: [
    {
      name: "algorithm",
      affectsData: true,
      type: "select",
      label: "Algorithm",
      default: "kmeans",
      options: [
        { value: "kmeans", label: "k-means" },
        { value: "dbscan", label: "DBSCAN (density)" },
      ],
    },
    {
      name: "k",
      affectsData: true,
      type: "int",
      label: "Clusters (k)",
      min: 2,
      max: 20,
      default: 3,
      showIf: { param: "algorithm", equals: "kmeans" },
    },
    {
      name: "eps",
      affectsData: true,
      type: "number",
      label: "Neighbourhood radius (eps)",
      min: 0.001,
      step: 0.05,
      default: 0.5,
      showIf: { param: "algorithm", equals: "dbscan" },
    },
    {
      name: "minSamples",
      affectsData: true,
      type: "int",
      label: "Min points per core",
      min: 2,
      max: 1000,
      default: 5,
      showIf: { param: "algorithm", equals: "dbscan" },
    },
    {
      name: "standardize",
      affectsData: true,
      type: "boolean",
      label: "Standardize measures",
      default: true,
    },
    {
      name: "diagnostics",
      affectsData: true,
      type: "boolean",
      label: "Compute elbow and silhouette hints",
      default: true,
      showIf: { param: "algorithm", equals: "kmeans" },
    },
    {
      name: "maxPoints",
      affectsData: true,
      type: "int",
      label: "Max points drawn",
      min: 10,
      max: 20000,
      default: 2000,
    },
    {
      name: "view",
      affectsData: false,
      type: "select",
      label: "Show",
      default: "clusters",
      options: [
        { value: "clusters", label: "Clusters" },
        { value: "elbow", label: "Elbow (k-means)" },
        { value: "silhouette", label: "Silhouette by k (k-means)" },
        { value: "kdistance", label: "k-distance (DBSCAN eps hint)" },
      ],
    },
    {
      name: "showCentroids",
      affectsData: false,
      type: "boolean",
      label: "Show centroids",
      default: true,
    },
  ],
  styleSections: ["title", "legend", "axes", "tooltip", "palette"],
  defaults: {
    params: {
      algorithm: "kmeans",
      k: 3,
      eps: 0.5,
      minSamples: 5,
      standardize: true,
      diagnostics: true,
      maxPoints: 2000,
      view: "clusters",
      showCentroids: true,
    },
  },
  render: renderCluster,
  warnings(spec, data) {
    const v = statValues(data);
    const view = param(spec, "view", "clusters");
    if (!v || view === "clusters") return [];
    const has = { elbow: v.elbow, silhouette: v.silhouettes, kdistance: v.kDistance }[view];
    return has
      ? []
      : [`The ${view} view is not available for ${str(v.algorithm) ?? "this"} clustering.`];
  },
  recommend(columns) {
    return count(columns, "quantitative") >= 2 ? 0.25 : 0;
  },
});
