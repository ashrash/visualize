import type { ChartSpec, Column } from "../../../api/types";
import { makeFormatter } from "../../core/format";
import { divergingRamp } from "../../core/palettes";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { resolveTheme } from "../../core/theme";
import { type ChannelSchema, definePlot, type ParamSchema, type PlotEntry } from "../../core/types";

const CHANNELS: ChannelSchema[] = [
  {
    name: "dimensions",
    label: "Fields",
    required: true,
    multiple: true,
    accepts: ["quantitative"],
    aggregate: false,
  },
];

const PARAMS: ParamSchema[] = [];

/** Grid that leaves room for a title/subtitle above and a horizontal visualMap legend below;
 * this plot manages its own layout (`axes: false`), so applyStyle's usual grid sizing doesn't
 * apply here. */
function gridWithMargins(spec: ChartSpec): Obj {
  const top = (spec.style?.title ? 46 : 16) + (spec.style?.subtitle ? 18 : 0);
  return { left: 90, right: 24, top, bottom: 70, containLabel: false };
}

export const correlationHeatmap: PlotEntry = definePlot({
  key: "correlation_heatmap",
  label: "Correlation heatmap",
  family: "relationship",
  description: "Pearson correlation between every pair of numeric fields.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: ["title", "labels", "tooltip", "palette"],
  defaults: {},
  render(spec, data, theme, ctx) {
    const rows = (data.columns.x ?? []) as string[];
    const cols = (data.columns.y ?? []) as string[];
    const values = (data.columns.value ?? []) as (number | null)[];
    const fieldOrder = [...new Set(rows)];
    const rowIndex = new Map(fieldOrder.map((f, i) => [f, i]));
    const colIndex = new Map(fieldOrder.map((f, i) => [f, i]));
    const fmt = makeFormatter("number:2");
    const t = resolveTheme(theme);
    const cellData = rows.map((r, i) => [
      colIndex.get(cols[i] as string),
      rowIndex.get(r),
      values[i],
    ]);
    const showLabels = spec.style?.showLabels ?? true;

    const option: Obj = {
      grid: gridWithMargins(spec),
      xAxis: { type: "category", data: fieldOrder, splitArea: { show: true } },
      yAxis: { type: "category", data: fieldOrder, splitArea: { show: true } },
      tooltip: {
        trigger: "item",
        formatter: (p: { value: [number, number, number | null] }) =>
          `${fieldOrder[p.value[1]]} × ${fieldOrder[p.value[0]]}: ${
            p.value[2] === null ? "n/a" : fmt(p.value[2])
          }`,
      },
      visualMap: {
        type: "continuous",
        min: -1,
        max: 1,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 4,
        inRange: { color: divergingRamp(theme) },
        textStyle: { color: t.textMuted },
      },
      series: [
        {
          type: "heatmap",
          data: cellData,
          label: {
            show: showLabels,
            formatter: (p: { value: [number, number, number | null] }) =>
              p.value[2] === null ? "" : fmt(p.value[2]),
          },
          emphasis: { itemStyle: { borderColor: t.text, borderWidth: 1 } },
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item", axes: false });
  },
  recommend(columns: Column[]) {
    return columns.filter((c) => c.semantic === "quantitative").length >= 2 ? 0.45 : 0.1;
  },
});

export const correlogram: PlotEntry = definePlot({
  key: "correlogram",
  label: "Correlogram",
  family: "relationship",
  description: "Pairwise correlation sized and colored by strength, with significance.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: ["title", "tooltip", "palette"],
  defaults: {},
  render(spec, data, theme, ctx) {
    const rows = (data.columns.x ?? []) as string[];
    const cols = (data.columns.y ?? []) as string[];
    const values = (data.columns.value ?? []) as (number | null)[];
    const pValues = data.columns.p as (number | null)[] | undefined;
    const fieldOrder = [...new Set(rows)];
    const rowIndex = new Map(fieldOrder.map((f, i) => [f, i]));
    const colIndex = new Map(fieldOrder.map((f, i) => [f, i]));
    const fmt = makeFormatter("number:2");
    const t = resolveTheme(theme);
    const maxSize = 32;

    const points = rows.map((r, i) => {
      const v = values[i];
      const magnitude = typeof v === "number" ? Math.sqrt(Math.abs(v)) : 0;
      return {
        value: [colIndex.get(cols[i] as string), rowIndex.get(r), v ?? null, pValues?.[i] ?? null],
        symbolSize: magnitude * maxSize,
      };
    });

    const option: Obj = {
      grid: gridWithMargins(spec),
      xAxis: { type: "category", data: fieldOrder, splitLine: { show: true } },
      yAxis: { type: "category", data: fieldOrder, splitLine: { show: true } },
      tooltip: {
        trigger: "item",
        formatter: (p: { value: [number, number, number | null, number | null] }) => {
          const [, , r, pv] = p.value;
          const sig = pv === null ? "" : `<br/>p ${fmt(pv)}`;
          return `${fieldOrder[p.value[1]]} × ${fieldOrder[p.value[0]]}: ${
            r === null ? "n/a" : fmt(r)
          }${sig}`;
        },
      },
      visualMap: {
        type: "continuous",
        min: -1,
        max: 1,
        dimension: 2,
        calculable: true,
        orient: "horizontal",
        left: "center",
        bottom: 4,
        inRange: { color: divergingRamp(theme) },
        textStyle: { color: t.textMuted },
      },
      series: [{ type: "scatter", data: points, symbol: "circle" }],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item", axes: false });
  },
  recommend(columns: Column[]) {
    return columns.filter((c) => c.semantic === "quantitative").length >= 2 ? 0.3 : 0.1;
  },
});
