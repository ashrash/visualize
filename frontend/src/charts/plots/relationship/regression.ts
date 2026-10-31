import type { Column } from "../../../api/types";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { type ChannelSchema, definePlot, type ParamSchema, type PlotEntry } from "../../core/types";

const COLOR_CHANNEL: ChannelSchema = {
  name: "color",
  label: "Color by",
  required: false,
  multiple: false,
  accepts: ["nominal", "ordinal"],
  aggregate: false,
};

const Y_CHANNEL: ChannelSchema = {
  name: "y",
  label: "Y",
  required: true,
  multiple: false,
  accepts: ["quantitative"],
  aggregate: false,
};

// Regression's raw/sampled points can come from a temporal x (the backend accepts
// NUMERIC_OR_TIME). Residual's backend fits/accepts a quantitative x only (plots/relationship.py
// Channel("x", QUANT)); offering temporal there would let a user bind a date column and get a
// 422 with no obvious reason why, so it gets its own, narrower channel list.
const REGRESSION_CHANNELS: ChannelSchema[] = [
  {
    name: "x",
    label: "X",
    required: true,
    multiple: false,
    accepts: ["quantitative", "temporal"],
    aggregate: false,
  },
  Y_CHANNEL,
  COLOR_CHANNEL,
];

const RESIDUAL_CHANNELS: ChannelSchema[] = [
  {
    name: "x",
    label: "X",
    required: true,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: false,
  },
  Y_CHANNEL,
  COLOR_CHANNEL,
];

const PARAMS: ParamSchema[] = [];

function colorSeries(data: {
  columns: Record<string, unknown[]>;
  meta: { fields: Record<string, string> };
}): Obj[] {
  const xs = (data.columns.x ?? []) as number[];
  const ys = (data.columns.y ?? []) as number[];
  const colorCol = data.columns.color as unknown[] | undefined;
  if (!colorCol) {
    return [
      {
        type: "scatter",
        name: data.meta.fields.y ?? "Points",
        yIndex: 0,
        data: xs.map((x, i) => [x, ys[i]]),
      },
    ];
  }
  const order: unknown[] = [];
  const byColor = new Map<unknown, [number, number][]>();
  colorCol.forEach((c, i) => {
    let pts = byColor.get(c);
    if (!pts) {
      pts = [];
      byColor.set(c, pts);
      order.push(c);
    }
    pts.push([xs[i] as number, ys[i] as number]);
  });
  return order.map((c) => ({ type: "scatter", name: String(c), data: byColor.get(c) }));
}

export const regression: PlotEntry = definePlot({
  key: "regression",
  label: "Regression plot",
  family: "relationship",
  description: "A scatter plot with a server-fitted trend line and confidence band.",
  encodingsSchema: REGRESSION_CHANNELS,
  paramsSchema: PARAMS,
  styleSections: [
    "title",
    "legend",
    "axes",
    "refLines",
    "trendline",
    "tooltip",
    "numberFormat",
    "palette",
  ],
  defaults: {
    style: {
      trendlines: [
        { kind: "linear", series: 0, degree: 1, ciLevel: 0.95, showEquation: true, showR2: true },
      ],
    },
  },
  render(spec, data, theme, ctx) {
    const option: Obj = {
      xAxis: { type: data.kinds.x === "temporal" ? "time" : "value", name: data.meta.fields.x },
      yAxis: { type: "value", name: data.meta.fields.y },
      tooltip: { trigger: "item" },
      series: colorSeries(data),
    };
    // applyStyle draws the fitted line (+ band) from ctx.overlays automatically, matching the
    // series whose yIndex equals the trendline's `series` index (see core/style.ts).
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  recommend(columns: Column[]) {
    return columns.filter((c) => c.semantic === "quantitative").length >= 2 ? 0.4 : 0.1;
  },
});

export const residual: PlotEntry = definePlot({
  key: "residual",
  label: "Residual plot",
  family: "relationship",
  description: "Residuals from a linear fit against the fitted values; look for patterns.",
  encodingsSchema: RESIDUAL_CHANNELS,
  paramsSchema: PARAMS,
  styleSections: ["title", "legend", "axes", "refLines", "tooltip", "numberFormat", "palette"],
  defaults: { style: { refLines: [{ axis: "y", value: 0, label: "0", lineStyle: "dashed" }] } },
  render(spec, data, theme, ctx) {
    const option: Obj = {
      xAxis: { type: "value", name: data.meta.fields.x ?? "Fitted value" },
      yAxis: { type: "value", name: data.meta.fields.y ?? "Residual" },
      tooltip: { trigger: "item" },
      series: colorSeries(data),
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  recommend(columns: Column[]) {
    return columns.filter((c) => c.semantic === "quantitative").length >= 2 ? 0.2 : 0.05;
  },
});
