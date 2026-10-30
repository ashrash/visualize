import "./echarts";
import { categoryLabel, type Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot, type StyleSection } from "../../core/types";

const SECTIONS: StyleSection[] = [
  "title",
  "legend",
  "labels",
  "tooltip",
  "numberFormat",
  "palette",
];

export const funnel = definePlot({
  key: "funnel",
  label: "Funnel chart",
  family: "composition",
  description: "Stages of a process, each sized by its value.",
  encodingsSchema: [
    {
      name: "x",
      label: "Stage",
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
    {
      name: "order",
      label: "Stage order (optional)",
      required: false,
      multiple: false,
      accepts: ["quantitative"],
      aggregate: false,
      hint: "A numeric field giving the stage order; otherwise stages sort by value.",
    },
  ],
  paramsSchema: [],
  styleSections: SECTIONS,
  defaults: { style: { showLabels: true } },
  render(spec, data, theme, ctx) {
    const xs = data.columns.x ?? [];
    const ys = data.columns.y0 ?? [];
    const items = xs.map((x, i) => ({
      name: categoryLabel(x, data.kinds.x, null, ctx?.locale),
      value: typeof ys[i] === "number" ? (ys[i] as number) : 0,
    }));
    const option: Obj = {
      legend: { data: items.map((s) => s.name) },
      tooltip: { trigger: "item" },
      series: [
        {
          type: "funnel",
          name: data.meta.fields.y0 ?? "Value",
          left: "6%",
          right: "6%",
          top: 36,
          bottom: 12,
          min: 0,
          max: Math.max(1, ...items.map((s) => s.value)),
          sort: "none",
          gap: 2,
          label: { show: true, position: "inside", color: theme.background ?? "#fff" },
          itemStyle: { borderColor: theme.background ?? "transparent", borderWidth: 1 },
          data: items,
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, {
      defaultTrigger: "item",
      colorCount: items.length,
    });
  },
  recommend(columns) {
    const hasCategory = columns.some(
      (c) => (c.semantic === "nominal" || c.semantic === "ordinal") && c.approxDistinct <= 12,
    );
    const measure = columns.some((c) => c.semantic === "quantitative");
    return hasCategory && measure ? 0.3 : 0.05;
  },
});
