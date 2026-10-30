import { categoryLabel, type Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { type ChartData, definePlot, type StyleSection } from "../../core/types";

const SECTIONS: StyleSection[] = [
  "title",
  "legend",
  "labels",
  "tooltip",
  "numberFormat",
  "palette",
];

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export const pareto = definePlot({
  key: "pareto",
  label: "Pareto chart",
  family: "specialized",
  description: "Bars sorted largest to smallest, with a cumulative-percent line.",
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
      label: "Value",
      required: false,
      multiple: false,
      accepts: ["quantitative"],
      aggregate: true,
      hint: "Leave empty to count rows.",
    },
  ],
  paramsSchema: [],
  styleSections: [...SECTIONS, "multiAxis"],
  // The cumulative-% line rides the second y axis (CLAUDE.md §4 multiple Y axes): `yIndex: 1`
  // on its series plus this default `series` style entry is how applyStyle assigns a series to
  // a non-zero axis (a raw `yAxisIndex` on the series is overwritten by the generic handling).
  defaults: { style: { showLabels: false, series: [{ index: 1, yAxis: 1 }] } },
  render(spec, data: ChartData, theme, ctx) {
    const xs = data.columns.x ?? [];
    const ys = (data.columns.y0 ?? []).map(num);
    const cumPercent = (data.columns.cumPercent ?? []).map(num);
    const categories = xs.map((x) => categoryLabel(x, data.kinds.x, null, ctx?.locale));
    const option: Obj = {
      xAxis: { type: "category", data: categories },
      yAxis: [
        { type: "value", name: data.meta.fields.y0 ?? "Value" },
        {
          type: "value",
          name: "Cumulative %",
          min: 0,
          max: 100,
          axisLabel: { formatter: "{value}%" },
        },
      ],
      series: [
        {
          type: "bar",
          name: data.meta.fields.y0 ?? "Value",
          data: ys,
        },
        {
          type: "line",
          name: "Cumulative %",
          yIndex: 1,
          data: cumPercent,
          symbolSize: 6,
          lineStyle: { color: theme.palette[3] ?? theme.text, width: 2 },
          itemStyle: { color: theme.palette[3] ?? theme.text },
        },
      ],
      tooltip: { trigger: "axis" },
    };
    return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "axis" });
  },
  recommend(columns) {
    const hasCategory = columns.some(
      (c) => (c.semantic === "nominal" || c.semantic === "ordinal") && c.approxDistinct <= 30,
    );
    const measure = columns.some((c) => c.semantic === "quantitative");
    return hasCategory && measure ? 0.35 : 0.05;
  },
});
