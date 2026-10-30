import type { ChartSpec, Column } from "../../../api/types";
import { makeFormatter } from "../../core/format";
import { sequentialRamp } from "../../core/palettes";
import { type Obj, param } from "../../core/series";
import { applyStyle } from "../../core/style";
import type { ChannelSchema, ParamSchema, StyleSection } from "../../core/types";
import { definePlot } from "../../core/types";
import type { MapResolution } from "./assets/maps";
import { backdropMapName, geoBackdrop, prepareBackdrop } from "./backdrop";
import "./echarts";

const SECTIONS: StyleSection[] = ["title", "tooltip", "palette"];

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
    name: "value",
    label: "Weight",
    required: false,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: true,
    hint: "Leave empty to count points per cell.",
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

export const geoHeatmap = definePlot({
  key: "geo_heatmap",
  label: "Geographic heatmap",
  family: "geo",
  description: "Density of points over a latitude/longitude grid.",
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
    const finite = values.filter((v) => Number.isFinite(v));
    const lo = finite.length ? Math.min(...finite) : 0;
    const hi = finite.length ? Math.max(...finite) : 1;
    const fmt = makeFormatter("auto", ctx?.locale);
    const valueLabel = data.meta.fields.y0 ?? "Count";
    const option: Obj = {
      geo: geoBackdrop(mapName, theme),
      visualMap: {
        min: lo,
        max: Math.max(hi, lo + 1e-9),
        left: 8,
        bottom: 8,
        calculable: true,
        inRange: { color: sequentialRamp(theme) },
        textStyle: { color: theme.textMuted },
      },
      series: [
        {
          type: "heatmap",
          coordinateSystem: "geo",
          data: lons.map((lon, i) => [lon, lats[i], values[i]]),
          pointSize: 14,
          blurSize: 20,
          tooltip: {
            formatter: (p: { value: [number, number, number] }) =>
              `${valueLabel}: ${fmt(p.value[2])}`,
          },
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  recommend(columns: Column[]) {
    const names = columns.map((c) => c.name.toLowerCase());
    const hasLat = names.some((n) => /^lat/.test(n));
    const hasLon = names.some((n) => /^lon|^lng/.test(n));
    return hasLat && hasLon ? 0.45 : 0.05;
  },
});
