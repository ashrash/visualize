import type { Column } from "../../../api/types";
import { makeFormatter } from "../../core/format";
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
    name: "maxGroups",
    affectsData: true,
    type: "int",
    label: "Max groups",
    min: 1,
    max: 200,
    default: 50,
  },
];

// The official @echarts-x/custom-violin renderer computes its own kernel density estimate from
// the raw (category, value) points, but its KDE sweeps a *hard-coded* y range of [0, 10]
// (see node_modules/@echarts-x/custom-violin/dist/violin.esm.mjs): it evaluates the density at
// 0..10 regardless of the data's actual scale. Real fields (body_mass_g, income, ...) would
// render as an empty sliver. We work around this by linearly rescaling y into [0, 10] before
// handing it to the series, and mapping the axis labels and tooltip back to the original units.
const VIOLIN_RANGE = 10;

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
    const categories = [...new Set(xs)];
    const categoryIndex = new Map(categories.map((c, i) => [c, i]));
    const fmt = makeFormatter(spec.style?.numberFormat ?? "auto", ctx?.locale);

    const finite = ys.filter((y) => typeof y === "number" && Number.isFinite(y));
    const dataMin = finite.length ? Math.min(...finite) : 0;
    const dataMax = finite.length ? Math.max(...finite) : 1;
    const span = dataMax - dataMin || 1;
    const scale = (y: number) => ((y - dataMin) / span) * VIOLIN_RANGE;
    const unscale = (v: number) => dataMin + (v / VIOLIN_RANGE) * span;

    const points = xs.map((x, i) => [categoryIndex.get(x) ?? 0, scale(ys[i] as number)]);
    const option: Obj = {
      xAxis: {
        type: "category",
        data: categories,
        name: data.meta.fields.x,
        boundaryGap: true,
      },
      yAxis: {
        type: "value",
        min: 0,
        max: VIOLIN_RANGE,
        name: data.meta.fields.y,
        axisLabel: { formatter: (v: number) => fmt(unscale(v)) },
      },
      series: [
        {
          type: "custom",
          renderItem: "violin",
          name: data.meta.fields.y ?? "Distribution",
          data: points,
          itemPayload: { areaOpacity: 0.6, bandWidthScale: 0.9, binCount: 100 },
          tooltip: {
            formatter: (p: { value: [number, number] }) =>
              `${categories[p.value[0]] ?? ""}: ${fmt(unscale(p.value[1]))}`,
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
