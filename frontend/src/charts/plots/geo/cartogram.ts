import type { ChartSpec, Column } from "../../../api/types";
import { makeFormatter } from "../../core/format";
import { paletteColors } from "../../core/palettes";
import { type Obj, param } from "../../core/series";
import { applyStyle } from "../../core/style";
import type { ChannelSchema, ParamSchema, StyleSection } from "../../core/types";
import { definePlot } from "../../core/types";
import type { MapResolution } from "./assets/maps";
import { matchRegion, readyMap } from "./assets/maps";
import { backdropMapName, geoBackdrop, prepareBackdrop } from "./backdrop";
import "./echarts";
import { escapeHtml } from "./htmlSafe";

/** The slice of ECharts' custom-series `renderItem` API this renderer actually uses. */
interface RenderItemParams {
  dataIndex: number;
}
interface RenderItemApi {
  value(dimIndex: number): number;
  coord(point: [number, number]): number[];
  style(extra?: Record<string, unknown>): Record<string, unknown>;
}

const SECTIONS: StyleSection[] = ["title", "tooltip", "palette"];

const CHANNELS: ChannelSchema[] = [
  {
    name: "region",
    label: "Region",
    required: true,
    multiple: false,
    accepts: ["nominal", "ordinal", "geo"],
    aggregate: false,
  },
  {
    name: "value",
    label: "Size by",
    required: true,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: true,
  },
  {
    name: "lat",
    label: "Latitude",
    required: true,
    multiple: false,
    accepts: ["geo", "quantitative"],
    aggregate: false,
    hint: "The region's representative point (e.g. a capital or centroid).",
  },
  {
    name: "lon",
    label: "Longitude",
    required: true,
    multiple: false,
    accepts: ["geo", "quantitative"],
    aggregate: false,
  },
];

const PARAMS: ParamSchema[] = [
  {
    name: "iterations",
    label: "Layout iterations",
    type: "int",
    affectsData: true,
    min: 10,
    max: 1000,
    default: 200,
  },
  {
    name: "padding",
    label: "Circle padding",
    type: "number",
    affectsData: true,
    min: 0,
    max: 0.5,
    step: 0.01,
    default: 0.04,
  },
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

export const cartogram = definePlot({
  key: "cartogram",
  label: "Cartogram",
  family: "geo",
  description: "Non-overlapping circles sized by a value, laid out near each region's location.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: SECTIONS,
  defaults: { params: { iterations: 200, padding: 0.04, resolution: "110m" } },
  async prepare(spec) {
    await prepareBackdrop(resolutionOf(spec));
  },
  render(spec, data, theme, ctx) {
    const resolution = resolutionOf(spec);
    const mapName = backdropMapName(resolution);
    // The backdrop map hasn't loaded yet (e.g. a smoke/snapshot test that renders without
    // going through `prepare` first): draw nothing rather than reference an unregistered map.
    if (!mapName) return applyStyle({ series: [] }, spec, theme, ctx, { defaultTrigger: "item" });
    const regions = (data.columns.region ?? []) as unknown[];
    const lons = (data.columns.lon ?? []) as number[];
    const lats = (data.columns.lat ?? []) as number[];
    const values = (data.columns.y0 ?? []) as number[];
    const radii = (data.columns.radius ?? []) as number[];
    const index = readyMap("country", resolution);
    const fmt = makeFormatter("auto", ctx?.locale);
    const valueLabel = data.meta.fields.y0 ?? "Value";
    const palette = paletteColors(spec.style?.palette, theme, 1);
    const color = palette[0] ?? theme.palette[0] ?? "#0072b2";
    const points = regions.map((r, i) => {
      const label = (index && matchRegion(index, r)) ?? String(r ?? "");
      return { name: label, value: [lons[i], lats[i], values[i], radii[i]] as number[] };
    });
    // The server computes `radius` in its own projected-plane units (longitude pre-scaled by
    // cos(mean latitude) so the plane is locally square); converting it to a longitude delta
    // with the same mean-latitude approximation and reading the pixel distance back out of
    // `api.coord` ties the drawn circle to the geo component's actual current projection and
    // zoom, so panning/zooming the map scales the circles correctly instead of leaving them a
    // fixed pixel size (which also made on-screen area not track the server's `value`).
    const meanLat = lats.length ? lats.reduce((a, b) => a + b, 0) / lats.length : 0;
    const cosLat0 = Math.max(0.15, Math.cos((meanLat * Math.PI) / 180));
    const option: Obj = {
      geo: geoBackdrop(mapName, theme),
      series: [
        {
          type: "custom",
          coordinateSystem: "geo",
          data: points,
          renderItem: (params: RenderItemParams, api: RenderItemApi) => {
            const lon = api.value(0);
            const lat = api.value(1);
            const radiusDeg = api.value(3);
            const center = api.coord([lon, lat]);
            const edge = api.coord([lon + radiusDeg / cosLat0, lat]);
            const r = Math.max(2, Math.abs((edge[0] ?? 0) - (center[0] ?? 0)));
            const point = points[params.dataIndex];
            return {
              type: "group",
              children: [
                {
                  type: "circle",
                  shape: { cx: center[0], cy: center[1], r },
                  style: api.style({
                    fill: color,
                    opacity: 0.7,
                    stroke: theme.background,
                    lineWidth: 1,
                  }),
                },
                {
                  type: "text",
                  silent: true,
                  style: {
                    text: point?.name ?? "",
                    x: center[0],
                    y: center[1],
                    fill: theme.text,
                    fontSize: 11,
                    align: "center",
                    verticalAlign: "middle",
                  },
                },
              ],
            };
          },
          itemStyle: { color },
          tooltip: {
            // p.name (a region label) and valueLabel both come from user data; escape before
            // building tooltip HTML (ECharts inserts a formatter's return via innerHTML).
            formatter: (p: { name: string; value: number[] }) =>
              `${escapeHtml(p.name)}<br/>${escapeHtml(valueLabel)}: ${escapeHtml(fmt(p.value[2]))}`,
          },
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  warnings(spec, data) {
    const index = readyMap("country", resolutionOf(spec));
    if (!index) return [];
    const regions = (data.columns.region ?? []) as unknown[];
    const unmatched = new Set<string>();
    for (const r of regions) {
      if (r === null || r === undefined || r === "") continue;
      if (!matchRegion(index, r)) unmatched.add(String(r));
    }
    if (unmatched.size === 0) return [];
    const names = [...unmatched];
    const shown = names.slice(0, 8).join(", ");
    const more = names.length > 8 ? `, and ${names.length - 8} more` : "";
    return [
      `${names.length} region label${names.length === 1 ? "" : "s"} didn't match the backdrop map: ${shown}${more}.`,
    ];
  },
  recommend(columns: Column[]) {
    const names = columns.map((c) => c.name.toLowerCase());
    const hasLat = names.some((n) => /^lat/.test(n));
    const hasLon = names.some((n) => /^lon|^lng/.test(n));
    const hasRegion = columns.some((c) => /country|iso|region|state|nation/i.test(c.name));
    const hasMeasure = columns.some((c) => c.semantic === "quantitative");
    return hasLat && hasLon && hasRegion && hasMeasure ? 0.4 : 0.05;
  },
});
