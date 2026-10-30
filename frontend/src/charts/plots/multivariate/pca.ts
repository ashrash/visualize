import type { ChartSpec } from "../../../api/types";
import { param } from "../../core/series";
import { applyStyle } from "../../core/style";
import type { ChartData, ChartTheme, RenderContext } from "../../core/types";
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
  obj,
  objs,
  QUANT,
  statValues,
  str,
  strs,
} from "./stat";

type Obj = Record<string, unknown>;

/** "PC1 (72.9%)" for component `c` (1-based). */
export function componentLabel(c: number, ratios: (number | null)[]): string {
  const ratio = ratios[c - 1];
  return ratio === null || ratio === undefined ? `PC${c}` : `PC${c} (${(ratio * 100).toFixed(1)}%)`;
}

function renderBiplot(spec: ChartSpec, data: ChartData, theme: ChartTheme, ctx?: RenderContext) {
  const v = statValues(data);
  if (!v) return applyStyle(EMPTY, spec, theme, ctx);
  const [cx, cy] = list(v.components).map((c) => num(c) ?? 1) as [number, number];
  const ratios = nums(v.explainedVarianceRatio);
  const points = obj(v.points);
  const xs = nums(points.x);
  const ys = nums(points.y);
  const colorOf = strs(points.color);
  const labels = strs(points.label);
  const showPoints = param(spec, "showPoints", true);
  const showLoadings = param(spec, "showLoadings", true);
  const labelPoints = param(spec, "labelPoints", false);
  const groups = colorOf.length
    ? [...new Set(colorOf.map((g) => g ?? "(missing)"))].sort()
    : ["Rows"];
  const colors = colorsFor(spec, theme, groups.length);
  const series: Obj[] = [];
  if (showPoints) {
    groups.forEach((g, gi) => {
      const items = xs
        .map((x, i) => ({ i, x, y: ys[i] ?? null }))
        .filter(({ i }) => !colorOf.length || (colorOf[i] ?? "(missing)") === g)
        .map(({ i, x, y }) => ({ value: [x, y], name: labels[i] ?? undefined }));
      series.push({
        type: "scatter",
        name: g,
        data: items,
        symbolSize: 6,
        itemStyle: { color: colors[gi], opacity: 0.75 },
        label: labelPoints
          ? {
              show: true,
              formatter: "{b}",
              position: "right",
              fontSize: 10,
              color: theme.textMuted,
            }
          : undefined,
      });
    });
  }
  if (showLoadings) {
    const scale = num(v.arrowScale) ?? 1;
    const lines = objs(v.loadings).map((l) => {
      const x = (num(l.x) ?? 0) * scale;
      const y = (num(l.y) ?? 0) * scale;
      const name = str(l.variable) ?? "";
      return [
        { coord: [0, 0] },
        {
          coord: [x, y],
          name,
          value: `${fmt(num(l.x))}, ${fmt(num(l.y))}`,
          label: { show: true, formatter: name, position: "end", color: theme.text },
        },
      ];
    });
    series.push({
      type: "scatter",
      name: "Loadings",
      data: [[0, 0]],
      symbolSize: 0,
      // The legend swatch matches the arrows (and toggles them).
      itemStyle: { color: theme.text },
      silent: true,
      tooltip: { show: false },
      markLine: {
        symbol: ["none", "arrow"],
        symbolSize: 8,
        lineStyle: { color: theme.text, width: 1.5, type: "solid" },
        tooltip: { formatter: (p: { name: string; value: string }) => `${p.name}: ${p.value}` },
        data: lines,
      },
    });
  }
  const option = {
    xAxis: { type: "value", name: componentLabel(cx, ratios), scale: true },
    yAxis: { type: "value", name: componentLabel(cy, ratios), scale: true },
    tooltip: {
      trigger: "item",
      formatter: (p: { seriesName: string; name?: string; value: number[] }) =>
        `${p.name ? `<b>${p.name}</b><br/>` : ""}${p.seriesName}<br/>` +
        `${componentLabel(cx, ratios)}: ${fmt(p.value[0])}<br/>` +
        `${componentLabel(cy, ratios)}: ${fmt(p.value[1])}`,
    },
    legend: groups.length > 1 ? {} : undefined,
    series,
  };
  return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "item" });
}

export const pcaBiplot = definePlot({
  key: "pca_biplot",
  label: "PCA biplot",
  family: "multivariate",
  description: "Rows on the first two principal components, with each measure as an arrow.",
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
      name: "color",
      label: "Color",
      required: false,
      multiple: false,
      accepts: ANY,
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
      name: "standardize",
      affectsData: true,
      type: "boolean",
      label: "Standardize measures (correlation PCA)",
      default: true,
    },
    {
      name: "xComponent",
      affectsData: true,
      type: "int",
      label: "X component",
      min: 1,
      max: 50,
      default: 1,
    },
    {
      name: "yComponent",
      affectsData: true,
      type: "int",
      label: "Y component",
      min: 1,
      max: 50,
      default: 2,
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
      name: "showPoints",
      affectsData: false,
      type: "boolean",
      label: "Show points",
      default: true,
    },
    {
      name: "showLoadings",
      affectsData: false,
      type: "boolean",
      label: "Show loading arrows",
      default: true,
    },
    {
      name: "labelPoints",
      affectsData: false,
      type: "boolean",
      label: "Label points",
      default: false,
      showIf: { param: "label", equals: true },
    },
  ],
  styleSections: ["title", "legend", "axes", "tooltip", "palette", "refLines", "annotations"],
  defaults: {
    params: {
      standardize: true,
      xComponent: 1,
      yComponent: 2,
      maxPoints: 2000,
      showPoints: true,
      showLoadings: true,
      labelPoints: false,
    },
  },
  render: renderBiplot,
  recommend(columns) {
    return count(columns, "quantitative") >= 3 ? 0.35 : 0;
  },
});
