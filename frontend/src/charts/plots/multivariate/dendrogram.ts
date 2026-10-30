import { param } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import {
  ANY,
  colorsFor,
  count,
  EMPTY,
  fmt,
  list,
  num,
  nums,
  QUANT,
  statValues,
  strs,
} from "./stat";

interface RenderApi {
  value(dim: number): number;
  coord(point: number[]): number[];
  size(point: number[]): number[];
}

/**
 * SciPy's dendrogram coordinates put leaf i at x = 10 i + 5. Map them to category positions
 * (leaf index, fractional between leaves) and draw each link as a polyline.
 */
export function linkPoints(icoord: (number | null)[], dcoord: (number | null)[]): number[][] {
  return icoord.map((x, k) => [((x ?? 5) - 5) / 10, dcoord[k] ?? 0]);
}

export const dendrogram = definePlot({
  key: "dendrogram",
  label: "Dendrogram",
  family: "multivariate",
  description: "Hierarchical clustering tree: which rows join first, and at what distance.",
  encodingsSchema: [
    {
      name: "y",
      label: "Measures",
      required: true,
      multiple: true,
      accepts: QUANT,
      aggregate: false,
    },
    {
      name: "label",
      label: "Leaf label",
      required: false,
      multiple: false,
      accepts: ANY,
      aggregate: false,
    },
  ],
  paramsSchema: [
    {
      name: "method",
      affectsData: true,
      type: "select",
      label: "Linkage",
      default: "ward",
      options: [
        { value: "ward", label: "Ward" },
        { value: "complete", label: "Complete" },
        { value: "average", label: "Average" },
        { value: "single", label: "Single" },
      ],
    },
    {
      name: "metric",
      affectsData: true,
      type: "select",
      label: "Distance",
      help: "Ward linkage needs euclidean distance.",
      default: "euclidean",
      options: [
        { value: "euclidean", label: "Euclidean" },
        { value: "cityblock", label: "Manhattan" },
        { value: "cosine", label: "Cosine" },
      ],
    },
    {
      name: "standardize",
      affectsData: true,
      type: "boolean",
      label: "Standardize measures",
      default: true,
    },
    {
      name: "clusters",
      affectsData: true,
      type: "int",
      label: "Color clusters (0 = automatic)",
      min: 0,
      max: 50,
      default: 0,
    },
    {
      name: "maxLeaves",
      affectsData: true,
      type: "int",
      label: "Max leaves shown",
      min: 2,
      max: 500,
      default: 40,
    },
    {
      name: "maxRows",
      affectsData: true,
      type: "int",
      label: "Max rows clustered (sample above)",
      min: 2,
      max: 5000,
      default: 2000,
    },
    {
      name: "orientation",
      affectsData: false,
      type: "select",
      label: "Orientation",
      default: "top",
      options: [
        { value: "top", label: "Root at the top" },
        { value: "left", label: "Root at the right (leaves left)" },
      ],
    },
  ],
  styleSections: ["title", "axes", "tooltip", "palette"],
  defaults: {
    params: {
      method: "ward",
      metric: "euclidean",
      standardize: true,
      clusters: 0,
      maxLeaves: 40,
      maxRows: 2000,
      orientation: "top",
    },
  },
  render(spec, data, theme, ctx) {
    const v = statValues(data);
    if (!v) return applyStyle(EMPTY, spec, theme, ctx);
    const leaves = strs(v.leaves).map((s) => s ?? "");
    const icoord = list(v.icoord).map((row) => nums(row));
    const dcoord = list(v.dcoord).map((row) => nums(row));
    const linkColors = nums(v.colors).map((c) => c ?? -1);
    const nClusters = Math.max(1, ...linkColors.map((c) => c + 1));
    const palette = colorsFor(spec, theme, nClusters);
    const horizontal = param(spec, "orientation", "top") === "left";
    const cut = num(v.cutHeight);
    const links = icoord.map((ic, k) => {
      const points = linkPoints(ic, dcoord[k] ?? []);
      const height = Math.max(0, ...points.map((p) => p[1] ?? 0));
      const centre = Math.round(points.reduce((s, p) => s + (p[0] ?? 0), 0) / points.length);
      return {
        // [leaf position, height]: puts the merge heights into the value axis' extent.
        value: [Math.max(0, Math.min(leaves.length - 1, centre)), height],
        points,
        color:
          (linkColors[k] ?? -1) < 0
            ? theme.textMuted
            : palette[(linkColors[k] ?? 0) % palette.length],
        height,
      };
    });
    const leafAxis = {
      type: "category",
      data: leaves,
      boundaryGap: true,
      axisTick: { show: false },
      axisLabel: { rotate: horizontal ? 0 : leaves.length > 12 ? 90 : 0, interval: 0 },
    };
    const heightAxis = { type: "value", name: "Distance", min: 0 };
    const option = {
      xAxis: horizontal ? heightAxis : leafAxis,
      yAxis: horizontal ? leafAxis : heightAxis,
      tooltip: { trigger: "item" },
      series: [
        {
          type: "custom",
          name: "Merges",
          data: links.map((l) => l.value),
          encode: horizontal ? { y: 0, x: 1 } : { x: 0, y: 1 },
          tooltip: {
            formatter: (p: { dataIndex: number }) =>
              `Merge at distance ${fmt(links[p.dataIndex]?.height ?? null)}`,
          },
          renderItem(params: { dataIndex: number }, api: RenderApi) {
            const link = links[params.dataIndex];
            if (!link) return null;
            // Leaf positions can fall between categories: interpolate from leaves 0 and 1
            // (the signed step also covers a vertical category axis running bottom-up).
            const first = api.coord([0, 0]);
            const second = api.coord(horizontal ? [0, 1] : [1, 0]);
            const axis = horizontal ? 1 : 0;
            const step = (second[axis] ?? 0) - (first[axis] ?? 0);
            const points = link.points.map(([leaf = 0, height = 0]) => {
              const h = api.coord(horizontal ? [height, 0] : [0, height]);
              const along = (first[axis] ?? 0) + leaf * step;
              return horizontal ? [h[0] ?? 0, along] : [along, h[1] ?? 0];
            });
            return {
              type: "polyline",
              shape: { points },
              style: { stroke: link.color, lineWidth: 1.5, fill: "none" },
            };
          },
          markLine:
            cut !== null
              ? {
                  silent: true,
                  symbol: "none",
                  lineStyle: { type: "dashed", color: theme.textMuted },
                  label: { formatter: "cut", color: theme.textMuted },
                  data: [horizontal ? { xAxis: cut } : { yAxis: cut }],
                }
              : undefined,
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "item" });
  },
  recommend(columns) {
    return count(columns, "quantitative") >= 2 && columns.length < 60 ? 0.15 : 0;
  },
});
