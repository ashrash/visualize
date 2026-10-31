import type { ChartSpec, ChartStyle, Encoding } from "../../api/types";
import type { AxisStyle, DeepPartial, PlotEntry, StyleSection } from "../core/types";

/** A full style from a partial one: every required field present. */
export function fullStyle(style: ChartStyle | undefined): ChartStyle {
  return {
    orientation: "vertical",
    stack: "none",
    showLabels: false,
    titlePosition: "top",
    ...style,
  };
}

export function patchStyle(spec: ChartSpec, patch: Partial<ChartStyle>): ChartSpec {
  return { ...spec, style: { ...fullStyle(spec.style), ...patch } };
}

export function axisOf(axis: AxisStyle | undefined): AxisStyle {
  return { grid: true, ...axis };
}

export function patchXAxis(spec: ChartSpec, patch: Partial<AxisStyle>): ChartSpec {
  return patchStyle(spec, { xAxis: { ...axisOf(spec.style?.xAxis), ...patch } });
}

export function yAxesOf(spec: ChartSpec): AxisStyle[] {
  const axes = spec.style?.yAxes;
  return axes && axes.length > 0 ? axes.map(axisOf) : [axisOf(undefined)];
}

export function patchYAxis(spec: ChartSpec, index: number, patch: Partial<AxisStyle>): ChartSpec {
  const axes = yAxesOf(spec);
  axes[index] = { ...axisOf(axes[index]), ...patch };
  return patchStyle(spec, { yAxes: axes });
}

export function setParam(spec: ChartSpec, name: string, value: unknown): ChartSpec {
  return { ...spec, params: { ...spec.params, [name]: value } };
}

/** Set (or clear, with undefined) the encodings of one channel. */
export function setChannel(
  spec: ChartSpec,
  channel: string,
  encodings: Encoding[] | undefined,
): ChartSpec {
  const next = { ...spec.encodings };
  if (encodings && encodings.length > 0) next[channel] = encodings;
  else delete next[channel];
  return { ...spec, encodings: next };
}

function mergeDeep<T extends object>(base: T, patch: DeepPartial<T> | undefined): T {
  if (!patch) return base;
  const out: Record<string, unknown> = { ...(base as Record<string, unknown>) };
  for (const [key, value] of Object.entries(patch)) {
    const current = out[key];
    out[key] =
      value &&
      typeof value === "object" &&
      !Array.isArray(value) &&
      current &&
      typeof current === "object"
        ? mergeDeep(current as object, value as DeepPartial<object>)
        : value;
  }
  return out as T;
}

/**
 * Style settings the target plot has no Style-tab section for would linger invisibly (a pie
 * that is still "stacked", a scatter with leftover error bars). Reset them to their defaults.
 */
function dropUnsupported(style: ChartStyle, entry: PlotEntry): ChartStyle {
  const has = (s: StyleSection) => entry.styleSections.includes(s);
  const out: ChartStyle = { ...style };
  // Hidden value labels would still draw: reset them only for a plot with no labels section.
  if (!has("labels")) out.showLabels = false;
  if (!has("stack")) out.stack = "none";
  if (!has("orientation")) out.orientation = "vertical";
  if (!has("errorBars")) out.errorBars = null;
  if (!has("trendline")) out.trendlines = [];
  if (!has("annotations")) out.annotations = [];
  if (!has("refLines")) out.refLines = [];
  if (!has("seriesStyle") && !has("multiAxis")) out.series = [];
  if (!has("multiAxis")) out.yAxes = out.yAxes?.slice(0, 1);
  return out;
}

/**
 * Switch a spec to another plot type: take that plot's default params, style and interactions
 * and keep only the channels it understands.
 */
export function applyPlotDefaults(spec: ChartSpec, entry: PlotEntry): ChartSpec {
  const known = new Set(entry.encodingsSchema.map((c) => c.name));
  const encodings: NonNullable<ChartSpec["encodings"]> = {};
  for (const [channel, value] of Object.entries(spec.encodings ?? {})) {
    if (known.has(channel)) encodings[channel] = value;
  }
  for (const [channel, value] of Object.entries(entry.defaults.encodings ?? {})) {
    if (!encodings[channel]) encodings[channel] = value;
  }
  return {
    ...spec,
    plotType: entry.key,
    encodings,
    params: { ...entry.defaults.params },
    style: mergeDeep(
      dropUnsupported(fullStyle(spec.style), entry),
      entry.defaults.style as DeepPartial<ChartStyle>,
    ),
    interactions: entry.defaults.interactions
      ? ({ ...spec.interactions, ...entry.defaults.interactions } as ChartSpec["interactions"])
      : spec.interactions,
  };
}

/** A new chart for a plot type, with the plot's defaults applied. The caller supplies the id. */
export function createChartSpec(
  entry: PlotEntry,
  base: Pick<ChartSpec, "id" | "source"> & Partial<ChartSpec>,
): ChartSpec {
  return applyPlotDefaults(
    { name: entry.label, plotType: entry.key, encodings: {}, ...base },
    entry,
  );
}

/**
 * Templates (MVP §8) are saved style presets. They keep the look (palette, fonts, legend,
 * axis formats, line and marker styles) and leave out what belongs to one chart's data: its
 * titles, axis titles and bounds, annotations, reference lines, trendlines, error bars and
 * per-series overrides.
 */
const CHART_SPECIFIC = [
  "title",
  "subtitle",
  "templateId",
  "annotations",
  "refLines",
  "trendlines",
  "series",
  "errorBars",
] as const;

const stripAxis = (axis: AxisStyle): AxisStyle => ({ ...axis, title: null, min: null, max: null });

export function templateStyle(style: ChartStyle | undefined): ChartStyle {
  const full = fullStyle(style);
  const out: ChartStyle = { ...full };
  for (const key of CHART_SPECIFIC) delete out[key];
  if (full.xAxis) out.xAxis = stripAxis(full.xAxis);
  if (full.yAxes) out.yAxes = full.yAxes.map(stripAxis);
  return out;
}

/** Apply a template: its look replaces the chart's, the chart's own data-specific parts stay. */
export function applyTemplate(spec: ChartSpec, template: ChartStyle, id: string): ChartSpec {
  const current = fullStyle(spec.style);
  const kept: Record<string, unknown> = {};
  for (const key of CHART_SPECIFIC) {
    if (current[key] !== undefined) kept[key] = current[key];
  }
  const own = (axis: AxisStyle | undefined) => ({
    title: axis?.title ?? null,
    min: axis?.min ?? null,
    max: axis?.max ?? null,
  });
  return {
    ...spec,
    style: {
      ...fullStyle(template),
      ...kept,
      xAxis: template.xAxis ? { ...template.xAxis, ...own(current.xAxis) } : current.xAxis,
      yAxes: template.yAxes
        ? template.yAxes.map((axis, i) => ({ ...axis, ...own(current.yAxes?.[i]) }))
        : current.yAxes,
      templateId: id,
    },
  };
}
