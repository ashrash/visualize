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
    const curve = data.columns.curve ?? [];
    const xs = data.columns.x ?? [];
    const ys = data.columns.y0 ?? [];
    const color = data.columns.color;
    const groups = color
      ? [...new Set(color.map((g) => (g === null ? "(missing)" : String(g))))].sort()
      : [];
    const colors = colorsFor(spec, theme, Math.max(1, groups.length));
    const opacity = param(spec, "opacity", 0.4);
    const byCurve = new Map<unknown, { group: string; data: [unknown, unknown][] }>();
    curve.forEach((id, i) => {
      let entry = byCurve.get(id);
      if (!entry) {
        const g = color ? (color[i] === null ? "(missing)" : String(color[i])) : "Rows";
        entry = { group: g, data: [] };
        byCurve.set(id, entry);
      }
      entry.data.push([xs[i], ys[i]]);
    });
    const series: Obj[] = [...byCurve.values()].map((c) => {
      const gi = Math.max(0, groups.indexOf(c.group));
      return {
        type: "line",
        name: c.group,
        data: c.data,
        showSymbol: false,
        symbol: "none",
        lineStyle: { width: 1, opacity, color: colors[gi] },
        itemStyle: { color: colors[gi] },
        emphasis: { focus: "series", lineStyle: { width: 2, opacity: 1 } },
      };
    });
    const pi = Math.PI;
    const option = {
      legend: groups.length > 1 ? { data: groups } : undefined,
      xAxis: {
        type: "value",
        name: "t",
        min: -pi,
        max: pi,
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
