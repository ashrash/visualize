import type { ChartSpec } from "../../../api/types";
import { chartParams } from "../../core/primitives";
import { dataZoomOption, type Obj, resolveStyle, type XAxisType } from "../../core/series";
import type { ChannelSchema, ChartData, RenderContext, StyleSection } from "../../core/types";

/** Channels shared by the plots that take one required time field and value measure(s). */
export function timeChannel(label = "Time"): ChannelSchema {
  return {
    name: "x",
    label,
    required: true,
    multiple: false,
    accepts: ["temporal"],
    aggregate: false,
    timeUnit: true,
  };
}

export function valueChannel(opts: Partial<ChannelSchema> = {}): ChannelSchema {
  return {
    name: "y",
    label: "Value",
    required: false,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: true,
    hint: "Leave empty to count rows.",
    ...opts,
  };
}

export function colorChannel(label = "Group by"): ChannelSchema {
  return {
    name: "color",
    label,
    required: false,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  };
}

export const CARTESIAN_SECTIONS: StyleSection[] = [
  "title",
  "legend",
  "axes",
  "multiAxis",
  "labels",
  "annotations",
  "refLines",
  "markers",
  "lines",
  "tooltip",
  "numberFormat",
  "palette",
  "seriesStyle",
];

/** A warning for the data-reduction / gap-filling notices the server already reports in meta. */
export function serverWarnings(_spec: ChartSpec, data: ChartData): string[] {
  return data.meta.warnings ?? [];
}

/** `meta.params` read helper (period, cycle, timeUnit, ...), shared by the family's renderers. */
export function metaParams(data: ChartData) {
  return chartParams(data.meta);
}

/** Standard dataZoom for a time-indexed x axis. */
export function timeDataZoom(
  spec: ChartSpec,
  count: number,
  ctx?: RenderContext,
): Obj[] | undefined {
  return dataZoomOption(spec, { type: "time" as XAxisType, horizontal: false, count }, ctx);
}

export function styleOf(spec: ChartSpec) {
  return resolveStyle(spec);
}
