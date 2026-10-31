import type { Column, PlotType } from "../../../api/types";
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

function pointPlot(key: PlotType, label: string, description: string, swarm: boolean): PlotEntry {
  return definePlot({
    key,
    label,
    family: "distribution",
    description,
    encodingsSchema: CHANNELS,
    paramsSchema: PARAMS,
    styleSections: ["title", "legend", "axes", "tooltip", "numberFormat", "palette"],
    defaults: {},
    render(spec, data, theme, ctx) {
      const rawXs = (data.columns.x ?? []) as unknown[];
      const ys = (data.columns.y ?? []) as number[];
      // A category axis's ordinal lookup is only reliable for string data: a bare number is
      // read as an ordinal *index* rather than a value to look up, and null has no category of
      // its own. Label every x consistently, whether it's a numeric ordinal column or has a
      // missing (null) group.
      const xs = rawXs.map((x) => (x == null ? "(null)" : String(x)));
      const option: Obj = {
        xAxis: {
          type: "category",
          data: [...new Set(xs)],
          name: data.meta.fields.x,
          boundaryGap: true,
          // ECharts 6: spreads overlapping points sideways within their category band.
          // Swarm keeps points from overlapping at all; strip allows a looser random jitter.
          jitter: 24,
          jitterOverlap: !swarm,
          jitterMargin: 2,
        },
        yAxis: { type: "value", name: data.meta.fields.y },
        tooltip: { trigger: "item" },
        series: [
          {
            type: "scatter",
            name: data.meta.fields.y ?? "Value",
            data: xs.map((x, i) => [x, ys[i]]),
            symbolSize: 7,
            large: ys.length > 2000,
          },
        ],
      };
      return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
    },
    recommend(columns: Column[]) {
      const hasValue = columns.some((c) => c.semantic === "quantitative");
      const hasGroup = columns.some((c) => c.semantic === "nominal" || c.semantic === "ordinal");
      return hasValue && hasGroup ? 0.35 : hasValue ? 0.2 : 0.1;
    },
  });
}

export const strip = pointPlot(
  "strip",
  "Strip plot",
  "Individual values jittered sideways within each category.",
  false,
);

export const swarm = pointPlot(
  "swarm",
  "Swarm plot",
  "Like a strip plot, but points never overlap (a beeswarm layout).",
  true,
);
