import type { ChartSpec, Column } from "../../../api/types";
import { makeFormatter } from "../../core/format";
import { paletteColors } from "../../core/palettes";
import { type Obj, param } from "../../core/series";
import { applyStyle } from "../../core/style";
import type { ChannelSchema, ParamSchema, StyleSection } from "../../core/types";
import { definePlot } from "../../core/types";
import type { MapResolution } from "./assets/maps";
import { backdropMapName, geoBackdrop, prepareBackdrop } from "./backdrop";
import "./echarts";

const SECTIONS: StyleSection[] = ["title", "legend", "tooltip", "palette"];

const CHANNELS: ChannelSchema[] = [
  {
    name: "lat",
    label: "Latitude",
    required: true,
    multiple: false,
    accepts: ["geo", "quantitative"],
    aggregate: false,
  },
  {
    name: "lon",
    label: "Longitude",
    required: true,
    multiple: false,
    accepts: ["geo", "quantitative"],
    aggregate: false,
  },
  {
    name: "size",
    label: "Size by",
    required: false,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: true,
    hint: "Leave empty to size by the number of points at each location.",
  },
  {
    name: "color",
    label: "Color by",
    required: false,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  },
];

const PARAMS: ParamSchema[] = [
  {
    name: "resolution",
    label: "Map detail",
    type: "select",
    affectsData: false,
    options: [
      { value: "110m", label: "Low (smaller, faster)" },
      { value: "50m", label: "High" },
    ],
    default: "110m",
  },
];

const resolutionOf = (spec: ChartSpec) => param(spec, "resolution", "110m") as MapResolution;

function groupByColor(
  lats: number[],
  lons: number[],
  values: number[],
  colors: (string | null)[] | undefined,
): Map<string | null, { lat: number; lon: number; value: number }[]> {
  const groups = new Map<string | null, { lat: number; lon: number; value: number }[]>();
  for (let i = 0; i < lats.length; i++) {
    const key = colors ? (colors[i] ?? null) : null;
    const list = groups.get(key) ?? [];
    list.push({ lat: lats[i] as number, lon: lons[i] as number, value: values[i] as number });
    groups.set(key, list);
  }
  return groups;
}

export const bubbleMap = definePlot({
  key: "bubble_map",
  label: "Bubble map",
  family: "geo",
  description: "Points sized by a value, placed by latitude and longitude.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: SECTIONS,
  defaults: { params: { resolution: "110m" } },
  async prepare(spec) {
    await prepareBackdrop(resolutionOf(spec));
  },
  render(spec, data, theme, ctx) {
    const resolution = resolutionOf(spec);
    const mapName = backdropMapName(resolution);
    // The backdrop map hasn't loaded yet (e.g. a smoke/snapshot test that renders without
    // going through `prepare` first): draw nothing rather than reference an unregistered map.
    if (!mapName) return applyStyle({ series: [] }, spec, theme, ctx, { defaultTrigger: "item" });
    const lats = (data.columns.lat ?? []) as number[];
    const lons = (data.columns.lon ?? []) as number[];
    const values = (data.columns.y0 ?? []) as number[];
    const colors = data.columns.color as (string | null)[] | undefined;
    const finite = values.filter((v) => Number.isFinite(v));
    const maxAbs = finite.reduce((m, v) => Math.max(m, Math.abs(v)), 0) || 1;
    const toSymbolSize = (v: number) => 6 + 26 * Math.sqrt(Math.abs(v) / maxAbs);
    const groups = groupByColor(lats, lons, values, colors);
    const palette = paletteColors(spec.style?.palette, theme, Math.max(groups.size, 2));
    const fmt = makeFormatter("auto", ctx?.locale);
    const valueLabel = data.meta.fields.y0 ?? "Value";
    const series = [...groups.entries()].map(([key, points], i) => ({
      type: "scatter",
      coordinateSystem: "geo",
      name: key ?? valueLabel,
      data: points.map((p) => [p.lon, p.lat, p.value]),
      symbolSize: (val: [number, number, number]) => toSymbolSize(val[2]),
      itemStyle: { color: palette[i % palette.length], opacity: 0.75 },
      tooltip: {
        formatter: (p: { value: [number, number, number] }) =>
          `${key ?? valueLabel}<br/>${valueLabel}: ${fmt(p.value[2])}`,
      },
    }));
    const option: Obj = {
      geo: geoBackdrop(mapName, theme),
      series,
    };
    return applyStyle(option, spec, theme, ctx, {
      defaultTrigger: "item",
      colorCount: groups.size,
    });
  },
  recommend(columns: Column[]) {
    const names = columns.map((c) => c.name.toLowerCase());
    const hasLat = names.some((n) => /^lat/.test(n));
    const hasLon = names.some((n) => /^lon|^lng/.test(n));
    const hasMeasure = columns.some((c) => c.semantic === "quantitative");
    if (hasLat && hasLon) return hasMeasure ? 0.65 : 0.4;
    return 0.05;
  },
});
