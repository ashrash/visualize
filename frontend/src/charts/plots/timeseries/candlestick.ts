import { formatDateAuto } from "../../core/format";
import type { Obj } from "../../core/series";
import { applyStyle } from "../../core/style";
import { definePlot } from "../../core/types";
import { serverWarnings, timeChannel, valueChannel } from "./shared";

function ohlcChannel(name: string, label: string) {
  return { ...valueChannel({ name, label }), aggregate: false, required: true, hint: undefined };
}

/**
 * Candlestick Chart (Plots.md Sec4): OHLC bars, with optional volume bars on a linked grid
 * below. The server aggregates with arg_min/max when a time bucket is needed; by default it
 * groups by the raw x value (one bar per row), matching data that is already daily OHLC.
 */
export const candlestick = definePlot({
  key: "candlestick",
  label: "Candlestick chart",
  family: "timeseries",
  description: "OHLC bars for financial time series, with optional volume.",
  encodingsSchema: [
    timeChannel("Date"),
    ohlcChannel("open", "Open"),
    ohlcChannel("high", "High"),
    ohlcChannel("low", "Low"),
    ohlcChannel("close", "Close"),
    { ...valueChannel({ name: "volume", label: "Volume" }), aggregate: false, hint: undefined },
  ],
  paramsSchema: [],
  styleSections: ["title", "legend", "tooltip", "numberFormat", "palette"],
  defaults: {},
  render(spec, data, theme) {
    const { columns, meta } = data;
    const categories = (columns.x ?? []).map((v) => formatDateAuto(v) || String(v));
    const open = (columns.open ?? []) as number[];
    const high = (columns.high ?? []) as number[];
    const low = (columns.low ?? []) as number[];
    const close = (columns.close ?? []) as number[];
    const volume = columns.volume as number[] | undefined;
    const up = theme.palette[2] ?? "#009e73";
    const down = theme.palette[5] ?? "#d55e00";
    const ohlc = open.map((o, i) => [o, close[i] ?? null, low[i] ?? null, high[i] ?? null]);

    const hasVolume = Boolean(volume);
    const series: Obj[] = [
      {
        type: "candlestick",
        name: meta.fields.close ?? "Price",
        data: ohlc,
        itemStyle: { color: up, color0: down, borderColor: up, borderColor0: down },
        xAxisIndex: 0,
        yAxisIndex: 0,
      },
    ];
    if (volume) {
      series.push({
        type: "bar",
        name: meta.fields.volume ?? "Volume",
        data: volume.map((v, i) => ({
          value: v,
          itemStyle: { color: (close[i] ?? 0) >= (open[i] ?? 0) ? up : down },
        })),
        xAxisIndex: 1,
        yAxisIndex: 1,
      });
    }

    const option: Obj = hasVolume
      ? {
          grid: [
            { left: 56, right: 24, top: 40, height: "58%" },
            { left: 56, right: 24, top: "74%", height: "16%" },
          ],
          xAxis: [
            { type: "category", data: categories, gridIndex: 0, axisLabel: { show: false } },
            { type: "category", data: categories, gridIndex: 1, name: meta.fields.x },
          ],
          yAxis: [
            { type: "value", gridIndex: 0, name: meta.fields.close, scale: true },
            { type: "value", gridIndex: 1, name: meta.fields.volume, splitNumber: 2 },
          ],
          series,
          dataZoom: [
            { type: "inside", xAxisIndex: [0, 1], filterMode: "none" },
            { type: "slider", xAxisIndex: [0, 1], filterMode: "none", height: 16, bottom: 4 },
          ],
          tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
        }
      : {
          xAxis: { type: "category", data: categories, name: meta.fields.x },
          yAxis: { type: "value", name: meta.fields.close, scale: true },
          series,
          dataZoom: [
            { type: "inside", filterMode: "none" },
            { type: "slider", filterMode: "none", height: 16, bottom: 4 },
          ],
          tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
        };
    return applyStyle(option, spec, theme, undefined, { defaultTrigger: "axis" });
  },
  warnings: serverWarnings,
  recommend(columns) {
    const names = new Set(columns.map((c) => c.name.toLowerCase()));
    const hasOhlc = ["open", "high", "low", "close"].every((n) => names.has(n));
    return hasOhlc ? 0.9 : 0.02;
  },
});
