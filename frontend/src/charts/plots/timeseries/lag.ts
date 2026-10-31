import { categoryLabel, type Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import { colorChannel, serverWarnings, timeChannel, valueChannel } from "./shared";

/**
 * Lag Plot (Plots.md Sec4): value(t) against value(t-k), the classic check for autocorrelation
 * and non-randomness. The server returns raw (x, y0[, color]) points (sampled above the cap),
 * so this renders a scatter rather than pivoting into aligned series like the cartesian plots.
 */
export const lag = definePlot({
  key: "lag",
  label: "Lag plot",
  family: "timeseries",
  description:
    "Plot each value against an earlier value of the same series to spot autocorrelation.",
  encodingsSchema: [
    timeChannel("Time (ordering)"),
    { ...valueChannel(), multiple: false, hint: undefined, aggregate: false, required: true },
    colorChannel(),
  ],
  paramsSchema: [
    {
      name: "lag",
      affectsData: true,
      type: "int",
      label: "Lag (k)",
      min: 1,
      max: 366,
      default: 1,
    },
  ],
  styleSections: ["title", "legend", "axes", "tooltip", "numberFormat", "palette"],
  defaults: { params: { lag: 1 } },
  render(spec, data, theme, ctx) {
    const { columns, meta, kinds } = data;
    const xs = (columns.x ?? []) as (number | null)[];
    const ys = (columns.y0 ?? []) as (number | null)[];
    const colors = columns.color;
    const groups = new Map<string, [number, number][]>();
    for (let i = 0; i < xs.length; i++) {
      const x = xs[i];
      const y = ys[i];
      if (typeof x !== "number" || typeof y !== "number") continue;
      const key = colors ? categoryLabel(colors[i], kinds.color) : "";
      const list = groups.get(key);
      if (list) list.push([x, y]);
      else groups.set(key, [[x, y]]);
    }
    const series: Obj[] = [...groups.entries()].map(([name, points]) => ({
      name: name || meta.fields.y0 || "Points",
      type: "scatter",
      data: points,
      symbolSize: 6,
      emphasis: { focus: "series" },
    }));
    const option: Obj = {
      xAxis: { type: "value", name: meta.fields.x },
      yAxis: { type: "value", name: meta.fields.y0 },
      series,
      tooltip: { trigger: "item" },
    };
    return applyStyle(option, spec, theme, ctx, { defaultTrigger: "item" });
  },
  warnings: serverWarnings,
  recommend(columns) {
    const temporal = columns.some((c) => c.semantic === "temporal");
    const measure = columns.some((c) => c.semantic === "quantitative");
    return temporal && measure ? 0.3 : 0.05;
  },
});
