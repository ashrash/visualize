import type { ChartSpec } from "../../../api/types";
import { definePlot, type PlotEntry, type StyleSection } from "../../core/types";
import { cartesianWarnings, renderCartesian } from "../basic/cartesian";

/** `spec.style` with a forced `stack`, seeding the fields OpenAPI marks required (they carry a
 * server default) before spreading, so the cast back to `ChartSpec["style"]` type-checks. */
function withStack(
  spec: ChartSpec,
  patch: Partial<NonNullable<ChartSpec["style"]>>,
): ChartSpec["style"] {
  return {
    orientation: "vertical",
    stack: "none",
    showLabels: false,
    ...spec.style,
    ...patch,
  } as ChartSpec["style"];
}

const SECTIONS: StyleSection[] = [
  "title",
  "legend",
  "axes",
  "labels",
  "annotations",
  "refLines",
  "tooltip",
  "numberFormat",
  "palette",
  "seriesStyle",
];

const encodingsSchema: PlotEntry["encodingsSchema"] = [
  {
    name: "x",
    label: "X axis",
    required: true,
    multiple: false,
    accepts: ["nominal", "ordinal", "temporal", "quantitative"],
    aggregate: false,
    timeUnit: true,
    sort: true,
  },
  {
    name: "y",
    label: "Value",
    required: false,
    multiple: true,
    accepts: ["quantitative"],
    aggregate: true,
    hint: "Leave empty to count rows.",
  },
  {
    name: "color",
    label: "Group by",
    required: false,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  },
];

/** stacked_area: the same column shape as `area`; the server groups and sums it directly
 * (it isn't part of the basic family's generic aggregate path, so it has its own stat). */
export const stackedArea = definePlot({
  key: "stacked_area",
  label: "Stacked area chart",
  family: "composition",
  description: "Cumulative value over an ordered axis, one band per group.",
  encodingsSchema,
  paramsSchema: [
    {
      name: "maxSeries",
      label: "Max series",
      affectsData: true,
      type: "int",
      min: 1,
      max: 500,
      default: 50,
      showIf: { param: "color", equals: true },
    },
  ],
  styleSections: SECTIONS,
  defaults: { params: { maxSeries: 50 }, style: { stack: "stack" } },
  render: (spec, data, theme, ctx) =>
    renderCartesian(
      "area",
      { ...spec, style: withStack(spec, { stack: "stack" }) },
      data,
      theme,
      ctx,
    ),
  warnings: (spec, data) =>
    cartesianWarnings({ ...spec, style: withStack(spec, { stack: "stack" }) }, data),
  recommend(columns) {
    const ordered = columns.some((c) => c.semantic === "temporal" || c.semantic === "quantitative");
    const hasGroup = columns.some((c) => c.semantic === "nominal" || c.semantic === "ordinal");
    const measure = columns.some((c) => c.semantic === "quantitative");
    return ordered && hasGroup && measure ? 0.55 : 0.1;
  },
});

/** percent_stacked_bar: the server already normalizes each x's shares to sum to 100 (0-100), so
 * the frontend forces `stack: "stack"` (not "percent") on the basic bar renderer -- that keeps
 * the shared client-side percent normalization (which assumes raw, un-normalized values) from
 * running a second time on data that is already a percentage. */
export const percentStackedBar = definePlot({
  key: "percent_stacked_bar",
  label: "100% stacked bar chart",
  family: "composition",
  description: "Each bar's groups sized by their share of that bar's total.",
  encodingsSchema: [
    encodingsSchema[0] as PlotEntry["encodingsSchema"][number],
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
      name: "color",
      label: "Group by",
      required: true,
      multiple: false,
      accepts: ["nominal", "ordinal"],
      aggregate: false,
    },
  ],
  paramsSchema: [
    {
      name: "maxSeries",
      label: "Max series",
      affectsData: true,
      type: "int",
      min: 1,
      max: 500,
      default: 50,
    },
  ],
  styleSections: SECTIONS.filter((s) => s !== "numberFormat"),
  defaults: { params: { maxSeries: 50 }, style: { stack: "stack" } },
  render: (spec, data, theme, ctx) =>
    renderCartesian(
      "bar",
      {
        ...spec,
        style: withStack(spec, {
          stack: "stack",
          numberFormat: spec.style?.numberFormat ?? "percent100:0",
        }),
      },
      data,
      theme,
      ctx,
    ),
  recommend(columns) {
    const hasCategory = columns.some(
      (c) => (c.semantic === "nominal" || c.semantic === "ordinal") && c.approxDistinct <= 50,
    );
    const measure = columns.some((c) => c.semantic === "quantitative");
    return hasCategory && measure ? 0.35 : 0.05;
  },
});
