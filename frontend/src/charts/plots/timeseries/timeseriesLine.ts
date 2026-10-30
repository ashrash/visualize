import { cartesianSeries, type Obj, seriesData } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import {
  CARTESIAN_SECTIONS,
  colorChannel,
  serverWarnings,
  timeChannel,
  timeDataZoom,
  valueChannel,
} from "./shared";

/**
 * Time Series Line Plot (Plots.md Sec4). Like the basic line chart but x is restricted to a
 * temporal field; the server auto-picks a calendar bucket and downsamples (M4+MinMaxLTTB) when
 * the raw series is too long, reported in `meta.reduction`/`meta.params.timeUnit`.
 */
export const timeseriesLine = definePlot({
  key: "timeseries_line",
  label: "Time series line",
  family: "timeseries",
  description: "A line chart over time, with automatic bucketing and downsampling for long series.",
  encodingsSchema: [timeChannel(), valueChannel({ multiple: true }), colorChannel()],
  paramsSchema: [
    {
      name: "maxSeries",
      affectsData: true,
      type: "int",
      label: "Max series",
      min: 1,
      max: 500,
      default: 50,
    },
  ],
  styleSections: CARTESIAN_SECTIONS,
  defaults: {},
  render(spec, data, theme, ctx) {
    const cd = cartesianSeries(data);
    const dataFor = (values: (number | null)[]) => seriesData(cd.xs, values, "time");
    const series: Obj[] = cd.series.map((s) => ({
      name: s.name,
      yIndex: s.yIndex,
      group: s.group,
      type: "line",
      showSymbol: cd.xs.length <= 60,
      data: dataFor(s.values),
      emphasis: { focus: "series" },
    }));
    const single = cd.series.length === 1 ? cd.series[0]?.name : undefined;
    const option: Obj = {
      xAxis: { type: "time", name: cd.xLabel },
      yAxis: { type: "value", name: single },
      series,
      tooltip: { trigger: "axis" },
      dataZoom: timeDataZoom(spec, cd.xs.length, ctx),
    };
    return applyStyle(option, spec, theme, ctx);
  },
  warnings: serverWarnings,
  recommend(columns) {
    const temporal = columns.some((c) => c.semantic === "temporal");
    const measure = columns.some((c) => c.semantic === "quantitative");
    return temporal && measure ? 0.85 : 0.05;
  },
});
