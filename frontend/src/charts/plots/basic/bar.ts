import type { ChartSpec } from "../../../api/types";
import { definePlot, type PlotEntry, type StyleSection } from "../../core/types";
import { BAR_PARAMS, cartesianChannels, hasMeasureAndCategory, renderCartesian } from "./cartesian";

const SECTIONS: StyleSection[] = [
  "title",
  "legend",
  "axes",
  "multiAxis",
  "labels",
  "annotations",
  "refLines",
  "stack",
  "orientation",
  "errorBars",
  "tooltip",
  "numberFormat",
  "palette",
  "seriesStyle",
];

const channels = () =>
  cartesianChannels("Category", ["nominal", "ordinal", "temporal", "quantitative"]);

function recommend(columns: Parameters<NonNullable<PlotEntry["recommend"]>>[0]) {
  const [hasCategory, hasMeasure] = hasMeasureAndCategory(columns, 50);
  return hasCategory && hasMeasure ? 0.8 : hasCategory ? 0.5 : 0.1;
}

/** Variants are presets of one renderer: they differ in forced style and in defaults. */
function variant(
  key: PlotEntry["key"],
  label: string,
  description: string,
  force: (spec: ChartSpec) => ChartSpec["style"],
  sections: StyleSection[] = SECTIONS,
): PlotEntry {
  return definePlot({
    key,
    label,
    family: "basic",
    description,
    encodingsSchema: channels(),
    paramsSchema: BAR_PARAMS,
    styleSections: sections,
    defaults: { params: { barWidth: 60, barGap: 10 } },
    render: (spec, data, theme, ctx) =>
      renderCartesian("bar", { ...spec, style: force(spec) }, data, theme, ctx),
    recommend,
  });
}

const without = (...omit: StyleSection[]) => SECTIONS.filter((s) => !omit.includes(s));
const styleOf = (spec: ChartSpec) => spec.style as NonNullable<ChartSpec["style"]>;
const withStyle = (spec: ChartSpec, patch: Partial<NonNullable<ChartSpec["style"]>>) =>
  ({
    orientation: "vertical",
    stack: "none",
    showLabels: false,
    ...spec.style,
    ...patch,
  }) as ChartSpec["style"];

export const bar = variant("bar", "Bar chart", "Compare values across categories.", (spec) =>
  withStyle(spec, {}),
);

export const horizontalBar = variant(
  "horizontal_bar",
  "Horizontal bar chart",
  "Bars drawn sideways: easier to read long category names.",
  (spec) => withStyle(spec, { orientation: "horizontal" }),
  without("orientation"),
);

export const groupedBar = variant(
  "grouped_bar",
  "Grouped bar chart",
  "Bars side by side for each group within a category.",
  (spec) => withStyle(spec, { stack: "none" }),
  without("stack"),
);

export const stackedBar = variant(
  "stacked_bar",
  "Stacked bar chart",
  "Groups stacked within each bar; switch to 100% to compare shares.",
  (spec) => withStyle(spec, { stack: styleOf(spec)?.stack === "percent" ? "percent" : "stack" }),
);

// The plain bar chart is a preset too: nothing is forced, so every style control applies.
// (Variants that force a setting hide that control.)
export const barEntries = [bar, horizontalBar, groupedBar, stackedBar];
