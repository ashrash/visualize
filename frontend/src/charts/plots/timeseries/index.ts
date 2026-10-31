import { CandlestickChart, HeatmapChart } from "echarts/charts";
import { CalendarComponent } from "echarts/components";
import { echarts } from "../../core/echarts";
import type { PlotEntry } from "../../core/types";
import { acf, pacf } from "./acfPacf";
import { calendarHeatmap } from "./calendarHeatmap";
import { candlestick } from "./candlestick";
import { lag } from "./lag";
import { movingAverage } from "./movingAverage";
import { seasonal } from "./seasonal";
import { timeseriesLine } from "./timeseriesLine";

// This family's two plots that aren't plain cartesian (bar/line/scatter, already registered in
// core/echarts.ts): candlestick and the calendar heatmap.
echarts.use([CandlestickChart, HeatmapChart, CalendarComponent]);

// Plots.md Sec4: time series line, seasonal, lag, ACF, PACF, moving average, candlestick,
// calendar heatmap.
const entries: PlotEntry[] = [
  timeseriesLine,
  seasonal,
  lag,
  acf,
  pacf,
  movingAverage,
  candlestick,
  calendarHeatmap,
];
export default entries;
