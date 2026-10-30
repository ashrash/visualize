import { definePlot, type StyleSection } from "../../core/types";
import { cartesianChannels, cartesianWarnings, LINE_PARAMS, renderCartesian } from "./cartesian";

const LINE_SECTIONS: StyleSection[] = [
  "title",
  "legend",
  "axes",
  "multiAxis",
  "labels",
  "annotations",
  "refLines",
  "markers",
  "lines",
  "orientation",
  "trendline",
  "errorBars",
  "tooltip",
  "numberFormat",
  "palette",
  "seriesStyle",
];

const X_ACCEPTS = ["temporal", "quantitative", "ordinal", "nominal"] as const;

export const line = definePlot({
  key: "line",
  label: "Line chart",
  family: "basic",
  description: "Show how values change along an ordered or time axis.",
  encodingsSchema: cartesianChannels("X axis", [...X_ACCEPTS]),
  paramsSchema: LINE_PARAMS,
  styleSections: LINE_SECTIONS,
  defaults: { params: { step: "none", connectNulls: false } },
  render: (spec, data, theme, ctx) => renderCartesian("line", spec, data, theme, ctx),
  warnings: cartesianWarnings,
  recommend(columns) {
    const temporal = columns.some((c) => c.semantic === "temporal");
    const measure = columns.some((c) => c.semantic === "quantitative");
    return temporal && measure ? 0.9 : measure ? 0.3 : 0.1;
  },
});

export const area = definePlot({
  key: "area",
  label: "Area chart",
  family: "basic",
  description: "A line chart with the area below filled; stack to show composition over time.",
  encodingsSchema: cartesianChannels("X axis", [...X_ACCEPTS]),
  paramsSchema: LINE_PARAMS,
  styleSections: [...LINE_SECTIONS.filter((s) => s !== "trendline" && s !== "errorBars"), "stack"],
  defaults: { params: { step: "none", connectNulls: false } },
  render: (spec, data, theme, ctx) => renderCartesian("area", spec, data, theme, ctx),
  warnings: cartesianWarnings,
  recommend(columns) {
    const temporal = columns.some((c) => c.semantic === "temporal");
    const measure = columns.some((c) => c.semantic === "quantitative");
    return temporal && measure ? 0.6 : 0.1;
  },
});
