import { chartParams } from "../../core/primitives/extra";
import { param } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import { CATEGORICAL, colorsFor, count, fmt, QUANT } from "./stat";

type Obj = Record<string, unknown>;

export const andrewsCurves = definePlot({
  key: "andrews_curves",
  label: "Andrews curves",
  family: "multivariate",
  description: "Each row as a Fourier curve; rows that are alike draw alike curves.",
  encodingsSchema: [
    {
      name: "y",
      label: "Measures (2-20)",
      required: true,
      multiple: true,
      accepts: QUANT,
      aggregate: false,
    },
    {
      name: "color",
      label: "Group",
      required: false,
      multiple: false,
      accepts: CATEGORICAL,
      aggregate: false,
    },
  ],
  paramsSchema: [
    {
      name: "maxCurves",
      affectsData: true,
      type: "int",
      label: "Max curves (random sample above)",
      min: 1,
      max: 2000,
      default: 200,
    },
    {
      name: "points",
      affectsData: true,
      type: "int",
      label: "Points per curve",
      min: 16,
      max: 500,
      default: 100,
    },
    {
      name: "scale",
      affectsData: true,
      type: "select",
      label: "Scale measures",
      default: "zscore",
      options: [
        { value: "zscore", label: "Standardize (z-score)" },
        { value: "minmax", label: "Min-max (0-1)" },
        { value: "none", label: "None" },
      ],
    },
    {
      name: "opacity",
      affectsData: false,
      type: "number",
      label: "Line opacity",
      min: 0.02,
      max: 1,
      step: 0.05,
      default: 0.4,
    },
  ],
  styleSections: ["title", "legend", "axes", "tooltip", "palette"],
  defaults: { params: { maxCurves: 200, points: 100, scale: "zscore", opacity: 0.4 } },
  render(spec, data, theme, ctx) {
    // One row per curve; f(t_j) in y{j} on an even t grid (meta.params tMin, tMax, points).
    const params = chartParams(data.meta);
    const points = Object.keys(data.columns).filter((k) => /^y\d+$/.test(k)).length;
    const tMin = params.number("tMin") ?? -Math.PI;
    const tMax = params.number("tMax") ?? Math.PI;
    const t = Array.from({ length: points }, (_, j) =>
      points > 1 ? tMin + ((tMax - tMin) * j) / (points - 1) : 0,
    );
    const color = data.columns.color;
    const groupOf = (i: number) =>
      color
        ? color[i] === null || color[i] === undefined
          ? "(missing)"
          : String(color[i])
        : "Rows";
    const groups = color ? [...new Set(color.map((_, i) => groupOf(i)))].sort() : [];
    const colors = colorsFor(spec, theme, Math.max(1, groups.length));
    const opacity = param(spec, "opacity", 0.4);
    const series: Obj[] = Array.from({ length: data.rowCount }, (_, i) => {
      const gi = Math.max(0, groups.indexOf(groupOf(i)));
      return {
        type: "line",
        name: groupOf(i),
        data: t.map((tj, j) => [tj, data.columns[`y${j}`]?.[i] ?? null]),
        showSymbol: false,
        symbol: "none",
        lineStyle: { width: 1, opacity, color: colors[gi] },
        itemStyle: { color: colors[gi] },
        emphasis: { focus: "series", lineStyle: { width: 2, opacity: 1 } },
      };
    });
    const option = {
      legend: groups.length > 1 ? { data: groups } : undefined,
      xAxis: {
        type: "value",
        name: "t",
        min: tMin,
        max: tMax,
        axisLabel: { formatter: (v: number) => fmt(v, 2) },
      },
      yAxis: { type: "value", name: "f(t)" },
      tooltip: { trigger: "item" },
      series,
    };
    return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "item" });
  },
  recommend(columns) {
    return count(columns, "quantitative") >= 3 ? 0.1 : 0;
  },
});
