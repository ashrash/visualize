import type { Column } from "../../../api/types";
import { makeFormatter } from "../../core/format";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { type ChannelSchema, definePlot, type ParamSchema, type PlotEntry } from "../../core/types";

const CHANNELS: ChannelSchema[] = [
  {
    name: "x",
    label: "Value",
    required: true,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: false,
  },
  {
    name: "color",
    label: "Group by",
    required: false,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  },
];

// Neither "bins" nor "binWidth" is in this plot's `defaults.params`, so a fresh chart sends
// neither and the server picks Freedman-Diaconis bins (CLAUDE.md §9 family notes). The controls
// below only take effect once the user touches them; their `default` is what a cleared field
// resets to (so it must itself be a value the server accepts, never a 0/empty sentinel).
export const BIN_PARAMS: ParamSchema[] = [
  {
    name: "bins",
    affectsData: true,
    type: "int",
    label: "Number of bins",
    min: 1,
    max: 1000,
    default: 10,
    help: "Overrides the automatic (Freedman-Diaconis) bin count.",
  },
  {
    name: "binWidth",
    affectsData: true,
    type: "number",
    label: "Bin width",
    min: 0.01,
    step: 0.01,
    default: 1,
    help: "Overrides the automatic bin width.",
  },
  {
    name: "maxGroups",
    affectsData: true,
    type: "int",
    label: "Max groups",
    min: 1,
    max: 100,
    default: 20,
    showIf: { param: "color", equals: true },
  },
];

/** Bin rows (x_lo/x_hi/count[/color]) pivoted into one count array per color, on shared edges. */
function pivotBins(data: { columns: Record<string, unknown[]> }) {
  const lo = (data.columns.x_lo ?? []) as number[];
  const hi = (data.columns.x_hi ?? []) as number[];
  const count = (data.columns.count ?? []) as number[];
  const color = data.columns.color as unknown[] | undefined;
  if (!color) {
    return { edges: lo.map((l, i) => [l, hi[i] as number]), series: [{ name: undefined, count }] };
  }
  const order: unknown[] = [];
  const byColor = new Map<unknown, { lo: number[]; hi: number[]; count: number[] }>();
  color.forEach((c, i) => {
    let bucket = byColor.get(c);
    if (!bucket) {
      bucket = { lo: [], hi: [], count: [] };
      byColor.set(c, bucket);
      order.push(c);
    }
    bucket.lo.push(lo[i] as number);
    bucket.hi.push(hi[i] as number);
    bucket.count.push(count[i] as number);
  });
  const first = byColor.get(order[0]);
  const edges = (first?.lo ?? []).map((l, i) => [l, first?.hi[i] as number]);
  const series = order.map((c) => ({ name: String(c), count: byColor.get(c)?.count ?? [] }));
  return { edges, series };
}

function binLabel(lo: number, hi: number): string {
  const fmt = makeFormatter("auto");
  return `${fmt(lo)}–${fmt(hi)}`;
}

export const histogram: PlotEntry = definePlot({
  key: "histogram",
  label: "Histogram",
  family: "distribution",
  description: "Counts of a numeric field split into equal-width bins.",
  encodingsSchema: CHANNELS,
  paramsSchema: BIN_PARAMS,
  styleSections: [
    "title",
    "legend",
    "axes",
    "labels",
    "annotations",
    "refLines",
    "stack",
    "tooltip",
    "numberFormat",
    "palette",
  ],
  defaults: {},
  render(spec, data, theme, ctx) {
    const { edges, series } = pivotBins(data);
    const categories = edges.map(([lo, hi]) => binLabel(lo as number, hi as number));
    const stacked = spec.style?.stack !== "none" && spec.style?.stack !== undefined;
    const option: Obj = {
      xAxis: { type: "category", data: categories, name: data.meta.fields.x, boundaryGap: true },
      yAxis: { type: "value", name: series.length === 1 ? "Count" : undefined },
      series: series.map((s) => ({
        type: "bar",
        name: s.name,
        data: s.count,
        barCategoryGap: "0%",
        barMaxWidth: 60,
        stack: stacked ? "total" : undefined,
        emphasis: { focus: "series" },
      })),
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    };
    return applyStyle(option, spec, theme, ctx);
  },
  recommend(columns: Column[]) {
    return columns.some((c) => c.semantic === "quantitative") ? 0.6 : 0.1;
  },
});

export const frequencyPolygon: PlotEntry = definePlot({
  key: "frequency_polygon",
  label: "Frequency polygon",
  family: "distribution",
  description: "A histogram drawn as a line through each bin's midpoint.",
  encodingsSchema: CHANNELS,
  paramsSchema: BIN_PARAMS,
  styleSections: [
    "title",
    "legend",
    "axes",
    "labels",
    "annotations",
    "refLines",
    "tooltip",
    "numberFormat",
    "palette",
  ],
  defaults: {},
  render(spec, data, theme, ctx) {
    const xs = (data.columns.x ?? []) as number[];
    const y0 = (data.columns.y0 ?? []) as number[];
    const colorCol = data.columns.color as unknown[] | undefined;
    const option: Obj = {
      xAxis: { type: "value", name: data.meta.fields.x },
      yAxis: { type: "value", name: colorCol ? undefined : "Count" },
      tooltip: { trigger: "axis" },
    };
    if (!colorCol) {
      option.series = [
        { type: "line", name: "Count", data: xs.map((x, i) => [x, y0[i]]), symbol: "none" },
      ];
    } else {
      const order: unknown[] = [];
      const byColor = new Map<unknown, [number, number][]>();
      colorCol.forEach((c, i) => {
        let pts = byColor.get(c);
        if (!pts) {
          pts = [];
          byColor.set(c, pts);
          order.push(c);
        }
        pts.push([xs[i] as number, y0[i] as number]);
      });
      option.series = order.map((c) => ({
        type: "line",
        name: String(c),
        data: byColor.get(c),
        symbol: "none",
      }));
    }
    return applyStyle(option, spec, theme, ctx);
  },
  recommend(columns: Column[]) {
    return columns.some((c) => c.semantic === "quantitative") ? 0.3 : 0.1;
  },
});
