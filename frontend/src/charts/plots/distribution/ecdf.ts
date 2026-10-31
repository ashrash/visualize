import type { Column } from "../../../api/types";
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

const PARAMS: ParamSchema[] = [
  {
    name: "maxPoints",
    affectsData: true,
    type: "int",
    label: "Max points per curve",
    min: 10,
    max: 20000,
    default: 2000,
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

export const ecdf: PlotEntry = definePlot({
  key: "ecdf",
  label: "ECDF plot",
  family: "distribution",
  description: "The empirical cumulative distribution of a numeric field.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: ["title", "legend", "axes", "refLines", "tooltip", "numberFormat", "palette"],
  defaults: {},
  render(spec, data, theme, ctx) {
    const xs = (data.columns.x ?? []) as number[];
    const ys = (data.columns.y ?? []) as number[];
    const colorCol = data.columns.color as unknown[] | undefined;
    const option: Obj = {
      xAxis: { type: "value", name: data.meta.fields.x },
      yAxis: { type: "value", name: colorCol ? undefined : "Cumulative probability", max: 1 },
      tooltip: { trigger: "axis" },
    };
    if (!colorCol) {
      option.series = [
        {
          type: "line",
          name: "F(x)",
          data: xs.map((x, i) => [x, ys[i]]),
          step: "end",
          symbol: "none",
        },
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
        pts.push([xs[i] as number, ys[i] as number]);
      });
      option.series = order.map((c) => ({
        type: "line",
        name: String(c),
        data: byColor.get(c),
        step: "end",
        symbol: "none",
      }));
    }
    return applyStyle(option, spec, theme, ctx);
  },
  recommend(columns: Column[]) {
    return columns.some((c) => c.semantic === "quantitative") ? 0.3 : 0.1;
  },
});
