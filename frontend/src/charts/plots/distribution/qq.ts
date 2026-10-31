import type { Column } from "../../../api/types";
import { maxOf, minOf } from "../../core/math";
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
];

const PARAMS: ParamSchema[] = [
  {
    name: "distribution",
    affectsData: true,
    type: "select",
    label: "Reference distribution",
    default: "norm",
    options: [
      { value: "norm", label: "Normal" },
      { value: "expon", label: "Exponential" },
      { value: "uniform", label: "Uniform" },
      { value: "logistic", label: "Logistic" },
      { value: "laplace", label: "Laplace" },
    ],
  },
  {
    name: "line",
    affectsData: true,
    type: "select",
    label: "Reference line",
    default: "quartiles",
    options: [
      { value: "quartiles", label: "Through the quartiles" },
      { value: "fit", label: "Least-squares fit" },
    ],
  },
];

export const qq: PlotEntry = definePlot({
  key: "qq",
  label: "Q-Q plot",
  family: "distribution",
  description: "Sample quantiles against a theoretical distribution's quantiles.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: ["title", "legend", "axes", "refLines", "tooltip", "numberFormat", "palette"],
  defaults: {},
  render(spec, data, theme, ctx) {
    const xs = (data.columns.x ?? []) as number[];
    const ys = (data.columns.y ?? []) as number[];
    const slope = Number(data.meta.params?.slope ?? 1);
    const intercept = Number(data.meta.params?.intercept ?? 0);
    const lo = minOf(xs, 0);
    const hi = maxOf(xs, 1);
    const option: Obj = {
      xAxis: { type: "value", name: data.meta.fields.x ?? "Theoretical quantile" },
      yAxis: { type: "value", name: data.meta.fields.y },
      tooltip: { trigger: "item" },
      series: [
        {
          type: "scatter",
          name: "Sample",
          data: xs.map((x, i) => [x, ys[i]]),
          symbolSize: 6,
        },
        {
          type: "line",
          name: "Reference line",
          data: [
            [lo, slope * lo + intercept],
            [hi, slope * hi + intercept],
          ],
          symbol: "none",
          lineStyle: { type: "dashed" },
          silent: true,
          tooltip: { show: false },
        },
      ],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  recommend(columns: Column[]) {
    return columns.some((c) => c.semantic === "quantitative") ? 0.25 : 0.1;
  },
});
