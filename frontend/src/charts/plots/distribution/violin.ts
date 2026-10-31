import type { Column } from "../../../api/types";
import { maxOf } from "../../core/math";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { type ChannelSchema, definePlot, type ParamSchema, type PlotEntry } from "../../core/types";

const CHANNELS: ChannelSchema[] = [
  {
    name: "x",
    label: "Group by",
    required: false,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  },
  {
    name: "y",
    label: "Value",
    required: true,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: false,
  },
];

const PARAMS: ParamSchema[] = [
  {
    name: "bandwidth",
    affectsData: true,
    type: "select",
    label: "Bandwidth rule",
    default: "silverman",
    options: [
      { value: "silverman", label: "Silverman" },
      { value: "scott", label: "Scott" },
    ],
  },
  {
    name: "adjust",
    affectsData: true,
    type: "number",
    label: "Bandwidth adjustment",
    min: 0.1,
    max: 5,
    step: 0.1,
    default: 1,
  },
  {
    name: "gridSize",
    affectsData: true,
    type: "int",
    label: "Curve resolution",
    min: 16,
    max: 2048,
    default: 200,
  },
  {
    name: "maxGroups",
    affectsData: true,
    type: "int",
    label: "Max groups",
    min: 1,
    max: 100,
    default: 20,
  },
];

const HTML_ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/** ECharts tooltip/label formatters return HTML that goes straight into innerHTML: any raw
 * category value (a column name, free-text group label, …) must be escaped before it is
 * interpolated, or a crafted value renders as markup/script on hover. */
function escapeHtml(value: unknown): string {
  return String(value).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c] ?? c);
}

interface RenderApi {
  value(dim: number, dataIndex?: number): number | string;
  coord(point: number[]): number[];
  size(point: number[]): number[];
  style(): Record<string, unknown>;
}

interface CustomParams {
  context: { groups?: Map<number, { firstIndex: number; ys: number[]; ds: number[]; n: number }> };
  dataInsideLength: number;
  dataIndexInside: number;
}

/** One polygon per category, traced through the server's KDE curve (mirrored left/right),
 * scaled to the category's own peak density (so every violin reaches the same half-width).
 *
 * On a category axis, `api.value(0)` is the category's *ordinal index* (a number, e.g. `0`),
 * not the category string: `String(api.value(0))` then feeding that into `api.coord` asks for
 * a category literally named "0", which doesn't exist, so every point coords to NaN and the
 * whole series draws nothing. Groups are keyed, and `api.coord` called, with that raw numeric
 * ordinal throughout.
 */
function renderItem(params: CustomParams, api: RenderApi): Obj | undefined {
  if (!params.context.groups) {
    const groups = new Map<number, { firstIndex: number; ys: number[]; ds: number[]; n: number }>();
    for (let i = 0; i < params.dataInsideLength; i++) {
      const cat = Number(api.value(0, i));
      let group = groups.get(cat);
      if (!group) {
        group = { firstIndex: i, ys: [], ds: [], n: Number(api.value(3, i)) || 0 };
        groups.set(cat, group);
      }
      group.ys.push(Number(api.value(1, i)));
      group.ds.push(Number(api.value(2, i)));
    }
    params.context.groups = groups;
  }
  const cat = Number(api.value(0));
  const group = params.context.groups.get(cat);
  // Only the first row of each category's curve draws (it draws the whole polygon at once);
  // every other row of that category renders nothing.
  if (!group || group.firstIndex !== params.dataIndexInside) return undefined;

  const maxDensity = maxOf(group.ds, 1e-12);
  const halfWidth = (api.size([1, 0])[0] ?? 40) * 0.42;
  const left: number[][] = [];
  const right: number[][] = [];
  for (let i = 0; i < group.ys.length; i++) {
    const [cx = 0, cy = 0] = api.coord([cat, group.ys[i] as number]);
    const w = ((group.ds[i] as number) / maxDensity) * halfWidth;
    left.push([cx - w, cy]);
    right.push([cx + w, cy]);
  }
  return { type: "polygon", shape: { points: [...left, ...right.reverse()] }, style: api.style() };
}

export const violin: PlotEntry = definePlot({
  key: "violin",
  label: "Violin plot",
  family: "distribution",
  description: "A box plot's category axis with the full shape of each group's distribution.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: ["title", "legend", "axes", "tooltip", "numberFormat", "palette"],
  defaults: {},
  render(spec, data, theme, ctx) {
    const xs = (data.columns.x ?? []) as string[];
    const ys = (data.columns.y ?? []) as number[];
    const densities = (data.columns.density ?? []) as number[];
    const ns = (data.columns.n ?? []) as number[];
    const categories = [...new Set(xs)];
    const seriesData = xs.map((x, i) => [x, ys[i], densities[i], ns[i]]);

    const option: Obj = {
      xAxis: { type: "category", data: categories, name: data.meta.fields.x, boundaryGap: true },
      yAxis: { type: "value", name: data.meta.fields.y },
      series: [
        {
          type: "custom",
          name: data.meta.fields.y ?? "Distribution",
          data: seriesData,
          encode: { x: 0, y: 1, tooltip: [0, 3] },
          renderItem,
          tooltip: {
            formatter: (p: { value: [string, number, number, number] }) =>
              `${escapeHtml(p.value[0])} (n=${escapeHtml(p.value[3])})`,
          },
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  recommend(columns: Column[]) {
    const hasValue = columns.some((c) => c.semantic === "quantitative");
    const hasGroup = columns.some((c) => c.semantic === "nominal" || c.semantic === "ordinal");
    return hasValue && hasGroup ? 0.4 : 0.1;
  },
});
