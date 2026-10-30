import { echarts } from "../../core/echarts";
import { categoryLabel, type Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import {
  type ChartData,
  type ChartTheme,
  definePlot,
  type PlotEntry,
  type RenderContext,
  type StyleSection,
} from "../../core/types";

const SECTIONS: StyleSection[] = [
  "title",
  "legend",
  "axes",
  "labels",
  "tooltip",
  "numberFormat",
  "palette",
];

const DIRECTIONS = ["total", "increase", "decrease"] as const;
type Direction = (typeof DIRECTIONS)[number];
const DIRECTION_LABEL: Record<Direction, string> = {
  total: "Total",
  increase: "Increase",
  decrease: "Decrease",
};

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

function render(
  data: ChartData,
  theme: ChartTheme,
  spec: Parameters<PlotEntry["render"]>[0],
  ctx?: RenderContext,
): Obj {
  const xs = data.columns.x ?? [];
  const base = (data.columns.base ?? []).map(num);
  const height = (data.columns.y0 ?? []).map(num);
  const direction = (data.columns.direction ?? []) as unknown[];
  const categories = xs.map((x) => categoryLabel(x, data.kinds.x, null, ctx?.locale));
  const defaultColors: Record<Direction, string> = {
    total: theme.textMuted,
    increase: theme.palette[2] ?? "#2ca02c",
    decrease: theme.palette[3] ?? "#d62728",
  };

  const baseSeries: Obj = {
    name: "base",
    type: "bar",
    stack: "waterfall",
    silent: true,
    itemStyle: { color: "transparent" },
    tooltip: { show: false },
    data: base,
  };
  const valueSeries: Obj[] = DIRECTIONS.map((d) => ({
    name: DIRECTION_LABEL[d],
    type: "bar",
    stack: "waterfall",
    itemStyle: { color: defaultColors[d] },
    data: height.map((h, i) => (direction[i] === d ? h : null)),
  }));

  const option: Obj = {
    xAxis: { type: "category", data: categories },
    yAxis: { type: "value" },
    series: [baseSeries, ...valueSeries],
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const items = (Array.isArray(params) ? params : [params]) as {
          axisValueLabel: string;
          seriesName: string;
          value: unknown;
        }[];
        const real = items.find((p) => p.seriesName !== "base" && typeof p.value === "number");
        if (!real) return "";
        const safe = echarts.format.encodeHTML;
        return `${safe(real.axisValueLabel)}<br/>${safe(real.seriesName)}: ${real.value}`;
      },
    },
  };
  return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "axis" });
}

export const waterfall = definePlot({
  key: "waterfall",
  label: "Waterfall chart",
  family: "composition",
  description: "A running total across categories, shown as floating bars.",
  encodingsSchema: [
    {
      name: "x",
      label: "Category",
      required: true,
      multiple: false,
      accepts: ["nominal", "ordinal"],
      aggregate: false,
    },
    {
      name: "y",
      label: "Change",
      required: true,
      multiple: false,
      accepts: ["quantitative"],
      aggregate: true,
    },
    {
      name: "order",
      label: "Sort order (optional)",
      required: false,
      multiple: false,
      accepts: ["quantitative"],
      aggregate: false,
      hint: "A numeric field giving the category order; otherwise x's own order is used.",
    },
    {
      name: "kind",
      label: "Total marker (optional)",
      required: false,
      multiple: false,
      accepts: ["nominal", "ordinal"],
      aggregate: false,
      hint: 'A field equal to "total" resets the running sum at that category.',
    },
  ],
  paramsSchema: [
    {
      name: "firstIsTotal",
      label: "First category is a total",
      affectsData: true,
      type: "boolean",
      default: true,
      showIf: { param: "kind", equals: false },
    },
    {
      name: "lastIsTotal",
      label: "Last category is a total",
      affectsData: true,
      type: "boolean",
      default: false,
      showIf: { param: "kind", equals: false },
      help: 'Off by default: resetting the last bar to its own value would misrepresent a plain delta sequence as already totalling to it. Use "Add a total bar" for a genuine grand total instead.',
    },
    {
      name: "appendTotal",
      label: "Add a total bar",
      affectsData: true,
      type: "boolean",
      default: false,
      help: "Appends one more bar: the true running total after every category.",
    },
  ],
  styleSections: SECTIONS,
  defaults: { params: { firstIsTotal: true, lastIsTotal: false, appendTotal: false } },
  render: (spec, data, theme, ctx) => render(data, theme, spec, ctx),
  recommend(columns) {
    const hasCategory = columns.some((c) => c.semantic === "nominal" || c.semantic === "ordinal");
    const measure = columns.some((c) => c.semantic === "quantitative");
    return hasCategory && measure ? 0.2 : 0.05;
  },
});
