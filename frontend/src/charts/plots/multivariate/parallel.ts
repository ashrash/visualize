import { ParallelChart } from "echarts/charts";
import { ParallelComponent } from "echarts/components";
import { echarts } from "../../core/echarts";
import { sequentialRamp } from "../../core/palettes";
import { param } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import { ANY, CATEGORICAL, colorsFor, count, QUANT } from "./stat";

echarts.use([ParallelChart, ParallelComponent]);

const label = (v: unknown) => (v === null || v === undefined ? "(missing)" : String(v));

export const parallelCoordinates = definePlot({
  key: "parallel_coordinates",
  label: "Parallel coordinates",
  family: "multivariate",
  description: "One line per row across many measures; spot clusters and trade-offs.",
  encodingsSchema: [
    {
      name: "y",
      label: "Axes (2-15)",
      required: true,
      multiple: true,
      accepts: [...QUANT, ...CATEGORICAL],
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
  ],
  paramsSchema: [
    {
      name: "maxLines",
      affectsData: true,
      type: "int",
      label: "Max lines (random sample above)",
      min: 10,
      max: 10000,
      default: 1000,
    },
    {
      name: "opacity",
      affectsData: false,
      type: "number",
      label: "Line opacity",
      min: 0.02,
      max: 1,
      step: 0.05,
      default: 0.35,
    },
    { name: "smooth", affectsData: false, type: "boolean", label: "Smooth lines", default: false },
  ],
  styleSections: ["title", "legend", "tooltip", "palette"],
  defaults: { params: { maxLines: 1000, opacity: 0.35, smooth: false } },
  render(spec, data, theme, ctx) {
    const dims = Object.keys(data.columns)
      .filter((k) => /^y\d+$/.test(k))
      .sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)));
    const rows = data.rowCount;
    const parallelAxis = dims.map((k, i) => {
      const categorical = data.kinds[k] !== "number";
      return {
        dim: i,
        name: data.meta.fields[k] ?? k,
        type: categorical ? "category" : "value",
        data: categorical ? [...new Set((data.columns[k] ?? []).map(label))].sort() : undefined,
        scale: !categorical,
        nameTextStyle: { color: theme.textMuted },
        axisLine: { lineStyle: { color: theme.axis ?? theme.border } },
        axisLabel: { color: theme.textMuted },
      };
    });
    const point = (i: number) =>
      dims.map((k) => {
        const v = data.columns[k]?.[i];
        return data.kinds[k] === "number" ? v : label(v);
      });
    const opacity = param(spec, "opacity", 0.35);
    const smooth = param(spec, "smooth", false);
    const colorCol = data.columns.color;
    const numericColor = colorCol !== undefined && data.kinds.color === "number";
    const option: Record<string, unknown> = {
      parallel: { left: 60, right: 60, top: 60, bottom: 40 },
      parallelAxis,
      tooltip: { trigger: "item" },
    };
    const lineStyle = { width: 1, opacity };
    if (!colorCol || numericColor) {
      option.series = [
        {
          type: "parallel",
          name: colorCol ? (data.meta.fields.color ?? "color") : undefined,
          smooth,
          lineStyle,
          data: Array.from({ length: rows }, (_, i) =>
            numericColor ? [...point(i), colorCol[i]] : point(i),
          ),
        },
      ];
      if (numericColor) {
        const values = colorCol.filter((v): v is number => typeof v === "number");
        option.visualMap = {
          type: "continuous",
          dimension: dims.length,
          min: Math.min(...values),
          max: Math.max(...values),
          text: [data.meta.fields.color ?? "color", ""],
          calculable: true,
          right: 4,
          top: "middle",
          inRange: { color: sequentialRamp(theme) },
          textStyle: { color: theme.textMuted },
        };
        (option.parallel as Record<string, unknown>).right = 100;
      }
    } else {
      const groups = [...new Set(colorCol.map(label))].sort();
      const colors = colorsFor(spec, theme, groups.length);
      option.legend = { data: groups };
      option.series = groups.map((g, gi) => ({
        type: "parallel",
        name: g,
        smooth,
        lineStyle: { ...lineStyle, color: colors[gi] },
        data: Array.from({ length: rows }, (_, i) => i)
          .filter((i) => label(colorCol[i]) === g)
          .map(point),
      }));
    }
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  recommend(columns) {
    return count(columns, "quantitative") >= 4 ? 0.3 : 0.05;
  },
});
