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
  value(dim: number): number;
  coord(point: number[]): number[];
  style(): Record<string, unknown>;
}

/** A pointy-top regular hexagon's vertices, `radius` px from `[cx, cy]`. */
function hexagonPoints(cx: number, cy: number, radius: number): number[][] {
  const points: number[][] = [];
  for (let i = 0; i < 6; i++) {
    const angle = (Math.PI / 180) * (60 * i - 30);
    points.push([cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)]);
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
    const radiusPx = Number(spec.params?.radiusPx ?? 18);
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
            const [cx = 0, cy = 0] = api.coord([api.value(0), api.value(1)]);
            return {
              type: "polygon",
              shape: { points: hexagonPoints(cx, cy, radiusPx) },
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
