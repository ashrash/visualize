import { errorBars } from "../../core/primitives/errorBars";
import { categoryLabel, type Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { type ChartData, definePlot, type StyleSection } from "../../core/types";

const SECTIONS: StyleSection[] = [
  "title",
  "legend",
  "axes",
  "orientation",
  "tooltip",
  "numberFormat",
  "palette",
];

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export const dumbbell = definePlot({
  key: "dumbbell",
  label: "Dumbbell chart",
  family: "specialized",
  description: "Two values per category, connected by a segment (e.g. before and after).",
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
      label: "Values",
      required: true,
      multiple: true,
      accepts: ["quantitative"],
      aggregate: true,
      hint: "Exactly two: the start and the end value.",
    },
  ],
  paramsSchema: [
    {
      name: "sortBy",
      label: "Sort by",
      affectsData: true,
      type: "select",
      default: "delta",
      options: [
        { value: "delta", label: "Change (end - start)" },
        { value: "end", label: "End value" },
        { value: "label", label: "Category" },
      ],
    },
  ],
  styleSections: SECTIONS,
  defaults: { params: { sortBy: "delta" } },
  render(spec, data: ChartData, theme, ctx) {
    const xs = data.columns.x ?? [];
    const y0 = (data.columns.y0 ?? []).map(num);
    const y1 = (data.columns.y1 ?? []).map(num);
    const categories = xs.map((x) => categoryLabel(x, data.kinds.x, null, ctx?.locale));
    const startColor = theme.palette[0] ?? theme.text;
    const endColor = theme.palette[1] ?? theme.textMuted;
    const lo = y0.map((v, i) =>
      v === null || y1[i] === null ? null : Math.min(v, y1[i] as number),
    );
    const hi = y0.map((v, i) =>
      v === null || y1[i] === null ? null : Math.max(v, y1[i] as number),
    );
    const option: Obj = {
      xAxis: { type: "category", data: categories },
      yAxis: { type: "value" },
      series: [
        errorBars({ x: categories, lo, hi, theme, color: theme.textMuted, capWidth: 0 }),
        {
          type: "scatter",
          name: data.meta.fields.y0 ?? "Start",
          data: y0,
          symbolSize: 11,
          itemStyle: { color: startColor },
        },
        {
          type: "scatter",
          name: data.meta.fields.y1 ?? "End",
          data: y1,
          symbolSize: 11,
          itemStyle: { color: endColor },
        },
      ],
      tooltip: { trigger: "axis" },
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "axis" });
  },
  recommend(columns) {
    const hasCategory = columns.some((c) => c.semantic === "nominal" || c.semantic === "ordinal");
    const measures = columns.filter((c) => c.semantic === "quantitative").length;
    return hasCategory && measures >= 2 ? 0.25 : 0.03;
  },
});
