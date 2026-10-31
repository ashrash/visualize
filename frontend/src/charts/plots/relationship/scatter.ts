import type { Column, PlotType } from "../../../api/types";
import { maxOf, minOf } from "../../core/math";
import { densityFallback } from "../../core/primitives";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { type ChannelSchema, definePlot, type ParamSchema, type PlotEntry } from "../../core/types";

const BASE_CHANNELS: ChannelSchema[] = [
  {
    name: "x",
    label: "X",
    required: true,
    multiple: false,
    accepts: ["quantitative", "temporal"],
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
  {
    name: "color",
    label: "Color by",
    required: false,
    multiple: false,
    accepts: ["nominal", "ordinal"],
    aggregate: false,
  },
];

const SIZE_CHANNEL: ChannelSchema = {
  name: "size",
  label: "Size by",
  required: false,
  multiple: false,
  accepts: ["quantitative"],
  aggregate: false,
};

const PARAMS: ParamSchema[] = [
  {
    name: "bins",
    affectsData: true,
    type: "int",
    label: "Density bins (over the cap)",
    min: 10,
    max: 300,
    default: 60,
    help: "Only used once the number of points exceeds the chart's mark cap.",
  },
];

function timeAxis(kind: string | undefined): Obj {
  return kind === "temporal" ? { type: "time" } : { type: "value" };
}

function sizeScale(values: number[], minPx: number, maxPx: number) {
  const lo = minOf(values, 0);
  const hi = maxOf(values, lo + 1);
  const span = hi - lo || 1;
  return (v: number) => minPx + ((v - lo) / span) * (maxPx - minPx);
}

function scatterPlot(
  key: PlotType,
  label: string,
  description: string,
  bubble: boolean,
): PlotEntry {
  const channels = bubble
    ? [
        BASE_CHANNELS[0] as ChannelSchema,
        BASE_CHANNELS[1] as ChannelSchema,
        { ...SIZE_CHANNEL, required: true },
        BASE_CHANNELS[2] as ChannelSchema,
      ]
    : [...BASE_CHANNELS, SIZE_CHANNEL];
  return definePlot({
    key,
    label,
    family: "relationship",
    description,
    encodingsSchema: channels,
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
    defaults: {},
    render(spec, data, theme, ctx) {
      const option: Obj = {
        xAxis: { ...timeAxis(data.kinds.x), name: data.meta.fields.x },
        yAxis: { type: "value", name: data.meta.fields.y },
        tooltip: { trigger: "item" },
      };
      const density = densityFallback(data, { theme });
      if (density) {
        option.series = [density.series];
        option.visualMap = density.visualMap;
        return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
      }
      const xs = (data.columns.x ?? []) as number[];
      const ys = (data.columns.y ?? []) as number[];
      const sizes = data.columns.size as number[] | undefined;
      const scaleSize = sizes ? sizeScale(sizes, 6, 36) : undefined;
      const colorCol = data.columns.color as unknown[] | undefined;
      const toItem = (i: number) => ({
        value: [xs[i], ys[i]],
        symbolSize: scaleSize ? scaleSize(sizes?.[i] ?? 0) : undefined,
      });
      if (!colorCol) {
        option.series = [
          {
            type: "scatter",
            name: data.meta.fields.y ?? "Points",
            yIndex: 0,
            data: xs.map((_, i) => toItem(i)),
            large: xs.length > 2000,
            symbolSize: scaleSize ? undefined : 8,
          },
        ];
      } else {
        const order: unknown[] = [];
        const byColor = new Map<unknown, number[]>();
        colorCol.forEach((c, i) => {
          const rows = byColor.get(c);
          if (rows) rows.push(i);
          else {
            byColor.set(c, [i]);
            order.push(c);
          }
        });
        option.series = order.map((c) => ({
          type: "scatter",
          name: String(c),
          data: byColor.get(c)?.map((i) => toItem(i)),
          large: xs.length > 2000,
          symbolSize: scaleSize ? undefined : 8,
        }));
      }
      return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
    },
    recommend(columns: Column[]) {
      const quant = columns.filter((c) => c.semantic === "quantitative").length;
      return quant >= 2 ? (bubble ? 0.3 : 0.55) : 0.1;
    },
  });
}

export const scatter = scatterPlot(
  "scatter",
  "Scatter plot",
  "Raw points for two numeric fields; switches to a density heatmap above the chart's cap.",
  false,
);

export const bubble = scatterPlot(
  "bubble",
  "Bubble chart",
  "A scatter plot with a third numeric field mapped to point size.",
  true,
);
