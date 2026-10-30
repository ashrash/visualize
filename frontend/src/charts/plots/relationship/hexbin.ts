import type { Column } from "../../../api/types";
import { maxOf } from "../../core/math";
import { sequentialRamp } from "../../core/palettes";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { resolveTheme } from "../../core/theme";
import { type ChannelSchema, definePlot, type ParamSchema, type PlotEntry } from "../../core/types";

const CHANNELS: ChannelSchema[] = [
  {
    name: "x",
    label: "X",
    required: true,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: false,
  },
  {
    name: "y",
    label: "Y",
    required: true,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: false,
  },
];

const PARAMS: ParamSchema[] = [
  {
    name: "radiusPx",
    affectsData: true,
    type: "number",
    label: "Hex radius (px)",
    min: 3,
    max: 100,
    step: 1,
    default: 18,
  },
];

interface RenderApi {
  value(dim: number): number | string;
  coord(point: number[]): number[];
  style(): Record<string, unknown>;
}

/**
 * A pointy-top regular hexagon's vertices, laid out in *data* space (a `radiusPx`-by-`xPerPx`/
 * `yPerPx` offset from the cell center) and mapped to pixels one vertex at a time via
 * `api.coord`. The server lays its hex lattice out assuming a particular viewport size
 * (`xPerPx`/`yPerPx`, in its response `params`); drawing an offset in screen pixels directly
 * (a fixed `radiusPx` added to the already-converted center) would misalign and overlap once
 * the real plot area (axis labels, the visualMap column, a title) differs from that assumption,
 * which it almost always does. Routing the offset back through `api.coord` keeps the hexagon
 * sized correctly in the chart's *current* coordinate mapping instead.
 */
function hexagonPoints(
  dataX: number,
  dataY: number,
  radiusPx: number,
  xPerPx: number,
  yPerPx: number,
  api: RenderApi,
): number[][] {
  const points: number[][] = [];
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI / 180) * (60 * i - 30);
    const dx = radiusPx * xPerPx * Math.cos(angle);
    const dy = radiusPx * yPerPx * Math.sin(angle);
    points.push(api.coord([dataX + dx, dataY + dy]));
  }
  return points;
}

export const hexbin: PlotEntry = definePlot({
  key: "hexbin",
  label: "Hexbin plot",
  family: "relationship",
  description: "Point density binned into hexagonal cells (good for large, overplotted scatters).",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: ["title", "axes", "tooltip", "numberFormat", "palette"],
  defaults: {},
  render(spec, data, theme, ctx) {
    const xs = (data.columns.x ?? []) as number[];
    const ys = (data.columns.y ?? []) as number[];
    const count = (data.columns.count ?? []) as number[];
    // Read the lattice params the server actually used (not the request's own copy of
    // radiusPx), so the drawn hexagons match the lattice exactly.
    const radiusPx = Number(data.meta.params?.radiusPx ?? spec.params?.radiusPx ?? 18);
    const xPerPx = Number(data.meta.params?.xPerPx ?? 1) || 1;
    const yPerPx = Number(data.meta.params?.yPerPx ?? 1) || 1;
    const t = resolveTheme(theme);
    const max = maxOf(count, 1);

    const option: Obj = {
      xAxis: { type: "value", name: data.meta.fields.x },
      yAxis: { type: "value", name: data.meta.fields.y },
      tooltip: { trigger: "item" },
      visualMap: {
        type: "continuous",
        min: 0,
        max,
        calculable: false,
        orient: "vertical",
        right: 4,
        top: "middle",
        inRange: { color: sequentialRamp(t) },
        textStyle: { color: t.textMuted },
        text: ["More", "Fewer"],
      },
      series: [
        {
          type: "custom",
          name: "Count",
          data: xs.map((x, i) => [x, ys[i], count[i]]),
          encode: { x: 0, y: 1, tooltip: 2 },
          renderItem(_params: unknown, api: RenderApi) {
            const x = Number(api.value(0));
            const y = Number(api.value(1));
            return {
              type: "polygon",
              shape: { points: hexagonPoints(x, y, radiusPx, xPerPx, yPerPx, api) },
              style: api.style(),
            };
          },
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  recommend(columns: Column[]) {
    return columns.filter((c) => c.semantic === "quantitative").length >= 2 ? 0.15 : 0.05;
  },
});
