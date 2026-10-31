import { cartesianSeries, type Obj, seriesData } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import { CARTESIAN_SECTIONS, metaParams, serverWarnings, timeChannel } from "./shared";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** A period code (as the server returns it) to a short, human label. */
export function periodLabel(period: string | undefined, value: number): string {
  switch (period) {
    case "month":
      return MONTHS[value - 1] ?? String(value);
    case "weekday":
      return WEEKDAYS[value] ?? String(value);
    case "hour":
      return `${value}:00`;
    case "quarter":
      return `Q${value}`;
    case "week":
      return `Wk ${value}`;
    default:
      return String(value);
  }
}

/**
 * Seasonal Plot (Plots.md Sec4): one line per cycle (year, by default), aggregated by a
 * within-cycle period (month, weekday, hour, quarter, day of month or week).
 */
export const seasonal = definePlot({
  key: "seasonal",
  label: "Seasonal plot",
  family: "timeseries",
  description: "Compare a value across years (or months) by month, weekday, hour or other period.",
  encodingsSchema: [
    timeChannel(),
    {
      name: "y",
      label: "Value",
      required: false,
      multiple: false,
      accepts: ["quantitative"],
      aggregate: true,
      hint: "Leave empty to count rows.",
    },
    // No color channel: the cycle (one line per year, by default) comes from the "cycle" param
    // below, computed from the same x field -- the backend has no separate color/series
    // channel for this plot, so exposing one here would silently do nothing.
  ],
  paramsSchema: [
    {
      name: "period",
      affectsData: true,
      type: "select",
      label: "Period (x axis)",
      default: "month",
      options: [
        { value: "month", label: "Month" },
        { value: "weekday", label: "Day of week" },
        { value: "hour", label: "Hour of day" },
        { value: "quarter", label: "Quarter" },
        { value: "day_of_month", label: "Day of month" },
        { value: "week", label: "Week of year" },
      ],
    },
    {
      name: "cycle",
      affectsData: true,
      type: "select",
      label: "Cycle (one line per)",
      default: "year",
      options: [
        { value: "year", label: "Year" },
        { value: "month", label: "Month" },
        { value: "none", label: "None (single line)" },
      ],
    },
  ],
  styleSections: CARTESIAN_SECTIONS,
  defaults: { params: { period: "month", cycle: "year" } },
  render(spec, data, theme, ctx) {
    const period = metaParams(data).string("period");
    const cd = cartesianSeries(data);
    const categories = cd.xs.map((x) => periodLabel(period, Number(x)));
    const dataFor = (values: (number | null)[]) => seriesData(categories, values, "category");
    const series: Obj[] = cd.series.map((s) => ({
      name: s.name,
      yIndex: s.yIndex,
      group: s.group,
      type: "line",
      data: dataFor(s.values),
      emphasis: { focus: "series" },
    }));
    const single = cd.series.length === 1 ? cd.series[0]?.name : undefined;
    const option: Obj = {
      xAxis: { type: "category", data: categories, name: cd.xLabel },
      yAxis: { type: "value", name: single },
      series,
      tooltip: { trigger: "axis" },
    };
    return applyStyle(option, spec, theme, ctx);
  },
  warnings: serverWarnings,
  recommend(columns) {
    const temporal = columns.some((c) => c.semantic === "temporal");
    const measure = columns.some((c) => c.semantic === "quantitative");
    return temporal && measure ? 0.4 : 0.05;
  },
});
