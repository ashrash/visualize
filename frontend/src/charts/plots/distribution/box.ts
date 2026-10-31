import type { Column } from "../../../api/types";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { type ChannelSchema, definePlot, type ParamSchema, type PlotEntry } from "../../core/types";

const CHANNELS: ChannelSchema[] = [
  {
    name: "y",
    label: "Value",
    required: true,
    multiple: false,
    accepts: ["quantitative"],
    aggregate: false,
  },
  {
    name: "x",
    label: "Group by",
    required: false,
    multiple: false,
    accepts: ["nominal", "ordinal"],
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

interface Outlier {
  x?: string | number | boolean | null;
  value: number;
}

export const box: PlotEntry = definePlot({
  key: "box",
  label: "Box plot",
  family: "distribution",
  description: "Quartiles, whiskers and outliers of a numeric field, by group.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: ["title", "legend", "axes", "refLines", "tooltip", "numberFormat", "palette"],
  defaults: {},
  render(spec, data, theme, ctx) {
    const xs = (data.columns.x ?? []) as string[];
    const q1 = (data.columns.q1 ?? []) as number[];
    const median = (data.columns.median ?? []) as number[];
    const q3 = (data.columns.q3 ?? []) as number[];
    const lo = (data.columns.lo ?? []) as number[];
    const hi = (data.columns.hi ?? []) as number[];
    const boxData = xs.map((_, i) => [lo[i], q1[i], median[i], q3[i], hi[i]]);
    const outliers = ((data.meta.params?.outliers as unknown as Outlier[] | undefined) ?? []).map(
      (o) => {
        // Match on the raw value (not a stringified one): categories can be null, a number or
        // a boolean, and `String(o.x)` would never equal those in `xs`, silently drawing every
        // such outlier on the first category instead.
        const index = o.x === undefined ? 0 : (xs as unknown[]).indexOf(o.x);
        return [Math.max(0, index), o.value];
      },
    );
    const option: Obj = {
      xAxis: { type: "category", data: xs, name: data.meta.fields.x },
      yAxis: { type: "value", name: data.meta.fields.median ?? data.meta.fields.q1 },
      tooltip: { trigger: "item" },
      series: [
        { type: "boxplot", name: "Distribution", data: boxData },
        ...(outliers.length > 0
          ? [{ type: "scatter", name: "Outliers", data: outliers, symbolSize: 6 }]
          : []),
      ],
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  recommend(columns: Column[]) {
    const hasValue = columns.some((c) => c.semantic === "quantitative");
    const hasGroup = columns.some((c) => c.semantic === "nominal" || c.semantic === "ordinal");
    return hasValue && hasGroup ? 0.55 : hasValue ? 0.3 : 0.1;
  },
});
