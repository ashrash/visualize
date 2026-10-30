import type { ChartSpec, Column } from "../../../api/types";
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

export const dotDensity = definePlot({
  key: "dot_density",
  label: "Dot density map",
  family: "geo",
  description: "One point per record, placed by latitude and longitude.",
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
    const colors = data.columns.color as (string | null)[] | undefined;
    const groups = new Map<string | null, [number, number][]>();
    for (let i = 0; i < lats.length; i++) {
      const key = colors ? (colors[i] ?? null) : null;
      const list = groups.get(key) ?? [];
      list.push([lons[i] as number, lats[i] as number]);
      groups.set(key, list);
    }
    const palette = paletteColors(spec.style?.palette, theme, Math.max(groups.size, 2));
    const series = [...groups.entries()].map(([key, points], i) => ({
      type: "scatter",
      coordinateSystem: "geo",
      name: key ?? "Points",
      data: points,
      symbolSize: 4,
      // ECharts' "large" mode (WebGL-free batched rendering) for up to the chart-data cap.
      large: true,
      largeThreshold: 1000,
      itemStyle: { color: palette[i % palette.length], opacity: 0.6 },
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
    return hasLat && hasLon ? 0.5 : 0.05;
  },
});
