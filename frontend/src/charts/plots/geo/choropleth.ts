import type { Column } from "../../../api/types";
import { makeFormatter } from "../../core/format";
import { sequentialRamp } from "../../core/palettes";
import { type Obj, param } from "../../core/series";
import { applyStyle } from "../../core/style";
import type { ChannelSchema, ParamSchema, PlotEntry, StyleSection } from "../../core/types";
import { definePlot } from "../../core/types";
import {
  type MapLevel,
  type MapResolution,
  matchRegion,
  prepareMap,
  readyMap,
} from "./assets/maps";
import "./echarts";

const SECTIONS: StyleSection[] = ["title", "tooltip", "palette"];

const CHANNELS: ChannelSchema[] = [
  {
    name: "region",
    label: "Region",
    required: true,
    multiple: false,
    accepts: ["nominal", "ordinal", "geo"],
    aggregate: false,
    hint: "An ISO country code / name, or a US state name / USPS code.",
  },
  {
    name: "value",
    label: "Value",
    required: false,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: true,
    hint: "Leave empty to count rows per region.",
  },
];

const PARAMS: ParamSchema[] = [
  {
    name: "level",
    label: "Map",
    type: "select",
    affectsData: false,
    options: [
      { value: "country", label: "World (countries)" },
      { value: "us_state", label: "United States (states)" },
    ],
    default: "country",
  },
  {
    name: "resolution",
    label: "Detail",
    type: "select",
    affectsData: false,
    options: [
      { value: "110m", label: "Low (smaller, faster)" },
      { value: "50m", label: "High" },
    ],
    default: "110m",
  },
];

function levelOf(spec: Parameters<PlotEntry["render"]>[0]) {
  return param(spec, "level", "country") as MapLevel;
}
function resolutionOf(spec: Parameters<PlotEntry["render"]>[0]) {
  return param(spec, "resolution", "110m") as MapResolution;
}

function unmatchedWarning(level: MapLevel, resolution: MapResolution, regions: unknown[]) {
  const index = readyMap(level, resolution);
  if (!index) return [];
  const unmatched = new Set<string>();
  for (const r of regions) {
    if (r === null || r === undefined || r === "") continue;
    if (!matchRegion(index, r)) unmatched.add(String(r));
  }
  if (unmatched.size === 0) return [];
  const names = [...unmatched];
  const shown = names.slice(0, 8).join(", ");
  const more = names.length > 8 ? `, and ${names.length - 8} more` : "";
  const noun = names.length === 1 ? "region" : "regions";
  return [`${names.length} ${noun} didn't match the map: ${shown}${more}.`];
}

export const choropleth = definePlot({
  key: "choropleth",
  label: "Choropleth map",
  family: "geo",
  description: "Color regions (countries or US states) by a value.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: SECTIONS,
  defaults: { params: { level: "country", resolution: "110m" } },
  async prepare(spec) {
    await prepareMap(levelOf(spec), resolutionOf(spec));
  },
  render(spec, data, theme, ctx) {
    const level = levelOf(spec);
    const resolution = resolutionOf(spec);
    const index = readyMap(level, resolution);
    // The map hasn't loaded yet (e.g. a smoke/snapshot test that renders without going through
    // `prepare` first): draw nothing rather than reference an unregistered map name.
    if (!index) return applyStyle({ series: [] }, spec, theme, ctx, { defaultTrigger: "item" });
    const regions = (data.columns.region ?? []) as unknown[];
    const values = (data.columns.y0 ?? []) as (number | null)[];
    const seriesData = regions.map((r, i) => ({
      name: matchRegion(index, r) ?? (r === null || r === undefined ? "" : String(r)),
      value: values[i] ?? null,
    }));
    const finite = values.filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    const lo = finite.length ? Math.min(...finite) : 0;
    const hi = finite.length ? Math.max(...finite) : 1;
    const fmt = makeFormatter("auto", ctx?.locale);
    const valueLabel = data.meta.fields.y0 ?? "Value";
    const option: Obj = {
      visualMap: {
        min: lo,
        max: Math.max(hi, lo + 1e-9),
        left: 8,
        bottom: 8,
        calculable: true,
        text: [fmt(hi), fmt(lo)],
        inRange: { color: sequentialRamp(theme) },
        textStyle: { color: theme.textMuted },
      },
      series: [
        {
          type: "map",
          map: index.mapName,
          roam: true,
          data: seriesData,
          itemStyle: { borderColor: theme.border, areaColor: theme.grid },
          emphasis: { itemStyle: { areaColor: theme.textMuted } },
          tooltip: {
            formatter: (p: { name: string; value: number | null }) =>
              `${p.name}<br/>${valueLabel}: ${p.value === null || p.value === undefined ? "no data" : fmt(p.value)}`,
          },
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  warnings(spec, data) {
    return unmatchedWarning(
      levelOf(spec),
      resolutionOf(spec),
      (data.columns.region ?? []) as unknown[],
    );
  },
  recommend(columns: Column[]) {
    const geo = columns.filter((c) => c.semantic === "geo");
    const looksLikeRegion = geo.some((c) => /country|iso|region|state|nation/i.test(c.name));
    const looksLikeCoord = geo.some((c) => /^lat|^lon|^lng/i.test(c.name));
    if (looksLikeRegion && !looksLikeCoord) return 0.7;
    if (geo.length > 0 && !looksLikeCoord) return 0.35;
    return 0.05;
  },
});
