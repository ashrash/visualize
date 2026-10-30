import type { ChartSpec } from "../../../api/types";
import { ciBand, refLineItem } from "../../core/primitives";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import type { ChartData, ChartTheme, RenderContext } from "../../core/types";
import { definePlot } from "../../core/types";
import { metaParams, serverWarnings, timeChannel, valueChannel } from "./shared";

const LAGS_PARAM = {
  name: "lags",
  affectsData: true,
  type: "int",
  label: "Lags",
  min: 1,
  max: 1000,
  default: 10,
} as const;

const CI_LEVEL_PARAM = {
  name: "ciLevel",
  affectsData: true,
  type: "number",
  label: "Confidence level",
  min: 0.5,
  max: 0.999,
  step: 0.01,
  default: 0.95,
} as const;

/** Shared renderer for ACF and PACF: a bar per lag plus a shaded Bartlett confidence band. */
function render(
  spec: ChartSpec,
  data: ChartData,
  theme: ChartTheme,
  ctx: RenderContext | undefined,
): Obj {
  const { columns, meta } = data;
  const categories = (columns.x ?? []).map((v) => String(v));
  const y0 = (columns.y0 ?? []) as (number | null)[];
  const lo = (columns.y0_lo ?? []) as (number | null)[];
  const hi = (columns.y0_hi ?? []) as (number | null)[];
  const ciLevel = metaParams(data).number("ciLevel");
  const bandLabel = ciLevel ? `${Math.round(ciLevel * 100)}% confidence band` : "Confidence band";
  const bar: Obj = {
    type: "bar",
    name: meta.fields.y0 ?? "Value",
    data: y0,
    barWidth: "40%",
    emphasis: { focus: "series" },
    markLine: {
      silent: true,
      symbol: "none",
      data: [refLineItem({ axis: "y", value: 0, style: "solid" })],
    },
  };
  const band = ciBand({ x: categories, lo, hi, axis: "category", opacity: 0.15 });
  // Name only the filled half, so the legend shows one entry for the band.
  const [belowBand, fillBand] = band;
  if (fillBand) fillBand.name = bandLabel;
  const option: Obj = {
    xAxis: { type: "category", data: categories, name: meta.fields.x },
    yAxis: { type: "value" },
    series: [bar, belowBand, fillBand].filter((s): s is Obj => s !== undefined),
    tooltip: { trigger: "axis" },
  };
  return applyStyle(option, spec, theme, ctx, { defaultTrigger: "axis" });
}

const SECTIONS = ["title", "legend", "axes", "tooltip", "numberFormat", "palette"] as const;

export const acf = definePlot({
  key: "acf",
  label: "Autocorrelation (ACF)",
  family: "timeseries",
  description:
    "Correlation of a series with its own lagged values, with Bartlett confidence bands.",
  encodingsSchema: [timeChannel(), { ...valueChannel(), required: true, hint: undefined }],
  paramsSchema: [LAGS_PARAM, CI_LEVEL_PARAM],
  styleSections: [...SECTIONS],
  defaults: { params: { ciLevel: 0.95 } },
  render,
  warnings: serverWarnings,
  recommend(columns) {
    const temporal = columns.some((c) => c.semantic === "temporal");
    const measure = columns.some((c) => c.semantic === "quantitative");
    return temporal && measure ? 0.25 : 0.02;
  },
});

export const pacf = definePlot({
  key: "pacf",
  label: "Partial autocorrelation (PACF)",
  family: "timeseries",
  description: "Correlation of a series with its own lagged values, controlling for shorter lags.",
  encodingsSchema: [timeChannel(), { ...valueChannel(), required: true, hint: undefined }],
  paramsSchema: [LAGS_PARAM, CI_LEVEL_PARAM],
  styleSections: [...SECTIONS],
  defaults: { params: { ciLevel: 0.95 } },
  render,
  warnings: serverWarnings,
  recommend(columns) {
    const temporal = columns.some((c) => c.semantic === "temporal");
    const measure = columns.some((c) => c.semantic === "quantitative");
    return temporal && measure ? 0.2 : 0.02;
  },
});
