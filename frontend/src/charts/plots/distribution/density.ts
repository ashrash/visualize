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
    help: "Multiplies the chosen bandwidth; below 1 shows more detail, above 1 smooths more.",
  },
  {
    name: "gridSize",
    affectsData: true,
    type: "int",
    label: "Curve resolution",
    min: 32,
    max: 2048,
    default: 512,
  },
];

export const density: PlotEntry = definePlot({
  key: "density",
  label: "Density plot",
  family: "distribution",
  description: "A smoothed (kernel density) estimate of a numeric field's distribution.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
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
      yAxis: { type: "value", name: colorCol ? undefined : "Density" },
      tooltip: { trigger: "axis" },
    };
    if (!colorCol) {
      option.series = [
        {
          type: "line",
          name: "Density",
          data: xs.map((x, i) => [x, y0[i]]),
          symbol: "none",
          areaStyle: { opacity: 0.15 },
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
        pts.push([xs[i] as number, y0[i] as number]);
      });
      option.series = order.map((c) => ({
        type: "line",
        name: String(c),
        data: byColor.get(c),
        symbol: "none",
        areaStyle: { opacity: 0.1 },
      }));
    }
    return applyStyle(option, spec, theme, ctx);
  },
  recommend(columns: Column[]) {
    return columns.some((c) => c.semantic === "quantitative") ? 0.4 : 0.1;
  },
});
