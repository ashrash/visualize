import { cartesianSeries, type Obj, seriesData } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import {
  CARTESIAN_SECTIONS,
  colorChannel,
  metaParams,
  serverWarnings,
  timeChannel,
  valueChannel,
} from "./shared";

/**
 * Moving Average Plot (Plots.md Sec4): the aggregated series (`y0`) with a trailing moving
 * average (`y1`, the user-chosen window) overlaid as a bolder line.
 */
export const movingAverage = definePlot({
  key: "moving_average",
  label: "Moving average",
  family: "timeseries",
  description: "A time series with a trailing moving average overlaid.",
  encodingsSchema: [timeChannel(), valueChannel(), colorChannel()],
  paramsSchema: [
    {
      name: "window",
      affectsData: true,
      type: "int",
      label: "Window (points)",
      min: 2,
      max: 1000,
      default: 7,
    },
  ],
  styleSections: CARTESIAN_SECTIONS,
  defaults: { params: { window: 7 } },
  render(spec, data, theme, ctx) {
    const cd = cartesianSeries(data);
    const window = metaParams(data).number("window");
    const dataFor = (values: (number | null)[]) => seriesData(cd.xs, values, "time");
    const series: Obj[] = cd.series.map((s) => {
      const isAverage = s.yIndex === 1;
      return {
        name: isAverage && window ? `${s.name} (${window}-pt avg)` : s.name,
        yIndex: s.yIndex,
        group: s.group,
        type: "line",
        data: dataFor(s.values),
        showSymbol: false,
        // Same reasoning as timeseries_line: downsampling can leave each series (and each of
        // y0/y1) with a different kept subset of x values, which would otherwise fragment.
        connectNulls: true,
        lineStyle: isAverage ? { width: 2.5 } : { width: 1, opacity: 0.45 },
        z: isAverage ? 3 : 1,
        emphasis: { focus: "series" },
      };
    });
    const option: Obj = {
      xAxis: { type: "time", name: cd.xLabel },
      yAxis: { type: "value" },
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
