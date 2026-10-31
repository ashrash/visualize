import { errorBars } from "../../core/primitives/errorBars";
import { categoryLabel, type Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { type ChartData, definePlot, type StyleSection } from "../../core/types";

const SECTIONS: StyleSection[] = ["title", "axes", "labels", "tooltip", "numberFormat", "palette"];

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export const errorBar = definePlot({
  key: "error_bar",
  label: "Error bar chart",
  family: "specialized",
  description: "A mean (or other summary) per category, with its spread or confidence interval.",
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
      required: true,
      multiple: false,
      accepts: ["quantitative"],
      aggregate: false,
    },
  ],
  paramsSchema: [
    {
      name: "kind",
      label: "Error bars show",
      affectsData: true,
      type: "select",
      default: "sd",
      options: [
        { value: "sd", label: "Standard deviation" },
        { value: "se", label: "Standard error" },
        { value: "ci", label: "Confidence interval" },
      ],
    },
    {
      name: "level",
      label: "Confidence level",
      affectsData: true,
      type: "number",
      min: 0.5,
      max: 0.999,
      step: 0.01,
      default: 0.95,
      showIf: { param: "kind", equals: "ci" },
    },
  ],
  styleSections: SECTIONS,
  defaults: { params: { kind: "sd", level: 0.95 } },
  render(spec, data: ChartData, theme, ctx) {
    const xs = data.columns.x ?? [];
    const ys = (data.columns.y0 ?? []).map(num);
    const lo = (data.columns.y0_lo ?? []).map(num);
    const hi = (data.columns.y0_hi ?? []).map(num);
    const categories = xs.map((x) => categoryLabel(x, data.kinds.x, null, ctx?.locale));
    const color = theme.palette[0] ?? theme.text;
    const option: Obj = {
      xAxis: { type: "category", data: categories },
      yAxis: { type: "value", name: data.meta.fields.y0 },
      series: [
        {
          type: "scatter",
          name: data.meta.fields.y0 ?? "Mean",
          symbolSize: 10,
          data: ys,
          itemStyle: { color },
        },
        errorBars({ x: categories, lo, hi, theme, color }),
      ],
      tooltip: { trigger: "axis" },
    };
    return applyStyle(option, spec, theme, ctx, { orientable: false, defaultTrigger: "axis" });
  },
  recommend(columns) {
    const hasCategory = columns.some((c) => c.semantic === "nominal" || c.semantic === "ordinal");
    const measure = columns.some((c) => c.semantic === "quantitative");
    return hasCategory && measure ? 0.25 : 0.05;
  },
});
