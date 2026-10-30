import { categoryLabel, type Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { type ChartData, definePlot, type StyleSection } from "../../core/types";

const SECTIONS: StyleSection[] = [
  "title",
  "axes",
  "orientation",
  "labels",
  "tooltip",
  "numberFormat",
  "palette",
];

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

export const lollipop = definePlot({
  key: "lollipop",
  label: "Lollipop chart",
  family: "specialized",
  description: "A thin stem and a dot: a lighter-weight bar chart.",
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
  paramsSchema: [
    {
      name: "sortBy",
      label: "Sort by",
      affectsData: true,
      type: "select",
      default: "value",
      options: [
        { value: "value", label: "Value (descending)" },
        { value: "label", label: "Category" },
      ],
    },
  ],
  styleSections: SECTIONS,
  defaults: { params: { sortBy: "value" } },
  render(spec, data: ChartData, theme, ctx) {
    const xs = data.columns.x ?? [];
    const ys = (data.columns.y0 ?? []).map(num);
    const categories = xs.map((x) => categoryLabel(x, data.kinds.x, null, ctx?.locale));
    const color = theme.palette[0] ?? theme.text;
    const option: Obj = {
      xAxis: { type: "category", data: categories },
      yAxis: { type: "value", name: data.meta.fields.y0 },
      series: [
        {
          type: "bar",
          name: data.meta.fields.y0 ?? "Value",
          data: ys,
          barWidth: 2,
          itemStyle: { color },
          silent: true,
          tooltip: { show: false },
        },
        {
          type: "scatter",
          name: data.meta.fields.y0 ?? "Value",
          data: ys,
          symbolSize: 12,
          itemStyle: { color },
        },
      ],
      tooltip: { trigger: "axis" },
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "axis" });
  },
  recommend(columns) {
    const hasCategory = columns.some(
      (c) => (c.semantic === "nominal" || c.semantic === "ordinal") && c.approxDistinct <= 40,
    );
    const measure = columns.some((c) => c.semantic === "quantitative");
    return hasCategory && measure ? 0.2 : 0.03;
  },
});
