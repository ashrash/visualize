import type { Column } from "../../../api/types";
import { densityFallback } from "../../core/primitives";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { type ChannelSchema, definePlot, type ParamSchema, type PlotEntry } from "../../core/types";

const CHANNELS: ChannelSchema[] = [
  {
    name: "x",
    label: "X",
    required: true,
    multiple: false,
    accepts: ["quantitative"],
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
];

const PARAMS: ParamSchema[] = [
  {
    name: "grid",
    affectsData: true,
    type: "int",
    label: "Grid resolution",
    min: 8,
    max: 200,
    default: 40,
  },
];

export const contour: PlotEntry = definePlot({
  key: "contour",
  label: "Contour plot",
  family: "relationship",
  description: "A binned density surface for two numeric fields.",
  encodingsSchema: CHANNELS,
  paramsSchema: PARAMS,
  styleSections: ["title", "axes", "tooltip", "palette"],
  defaults: {},
  render(spec, data, theme, ctx) {
    // The server always returns a `bin2d` shape here (x_lo/x_hi/y_lo/y_hi/count), the same one
    // scatter/bubble fall back to over their mark cap: reuse that primitive directly. Not the
    // official @echarts-x/custom-contour series or true isolines — see relationship.py's
    // contour() docstring for why, and STATUS.md tracks this plot as partial until a real
    // marching-squares renderer replaces this density view.
    const density = densityFallback(data, { theme });
    const option: Obj = {
      xAxis: { type: "value", name: data.meta.fields.x_lo },
      yAxis: { type: "value", name: data.meta.fields.y_lo },
      tooltip: { trigger: "item" },
      series: density ? [density.series] : [],
      visualMap: density?.visualMap,
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  recommend(columns: Column[]) {
    return columns.filter((c) => c.semantic === "quantitative").length >= 2 ? 0.15 : 0.05;
  },
});
