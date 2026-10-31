import { offsetKde } from "../../core/primitives/offsetKde";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { type ChartData, definePlot, type StyleSection } from "../../core/types";
import { densityGroups } from "./statValues";

const SECTIONS: StyleSection[] = ["title", "axes", "tooltip", "palette"];

export const ridgeline = definePlot({
  key: "ridgeline",
  label: "Ridgeline plot",
  family: "specialized",
  description: "One density curve per group, offset so they form a skyline.",
  encodingsSchema: [
    {
      name: "value",
      label: "Value",
      required: true,
      multiple: false,
      accepts: ["quantitative"],
      aggregate: false,
    },
    {
      name: "group",
      label: "Group by",
      required: true,
      multiple: false,
      accepts: ["nominal", "ordinal"],
      aggregate: false,
    },
  ],
  paramsSchema: [
    {
      name: "bandwidth",
      label: "Bandwidth rule",
      affectsData: true,
      type: "select",
      default: "silverman",
      options: [
        { value: "silverman", label: "Silverman" },
        { value: "scott", label: "Scott" },
      ],
    },
    {
      name: "adjust",
      label: "Bandwidth adjustment",
      affectsData: true,
      type: "number",
      min: 0.1,
      max: 5,
      step: 0.1,
      default: 1,
    },
    {
      name: "gridSize",
      label: "Curve resolution",
      affectsData: true,
      type: "int",
      min: 32,
      max: 1024,
      default: 200,
    },
    {
      name: "overlap",
      label: "Overlap",
      affectsData: false,
      type: "number",
      min: 0,
      max: 1,
      step: 0.05,
      default: 0.5,
    },
  ],
  styleSections: SECTIONS,
  defaults: { params: { bandwidth: "silverman", adjust: 1, gridSize: 200, overlap: 0.5 } },
  render(spec, data: ChartData, theme, ctx) {
    const groups = densityGroups(data.stat?.values).map((g) => ({
      name: g.key ?? "(null)",
      x: g.x,
      density: g.density,
    }));
    const overlap = typeof spec.params?.overlap === "number" ? spec.params.overlap : 0.5;
    const { series, yAxis } = offsetKde({ groups, overlap, colors: theme.palette, theme });
    const option: Obj = {
      xAxis: { type: "value", name: data.meta.fields.value ?? undefined },
      yAxis,
      series,
      tooltip: { trigger: "item" },
    };
    return applyStyle(option, spec, theme, ctx, { axes: false, defaultTrigger: "item" });
  },
  recommend(columns) {
    const hasGroup = columns.some((c) => c.semantic === "nominal" || c.semantic === "ordinal");
    const measure = columns.some((c) => c.semantic === "quantitative");
    return hasGroup && measure ? 0.2 : 0.03;
  },
});
