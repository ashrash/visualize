/**
 * Turns raw ECharts events (click, brush end, legend toggle) into the reduced signals the
 * dashboard bus understands.
 *
 * TODO(ui-charts): `ChartView` should expose `onPointClick` / `onBrushSelected` /
 * `onLegendChange` (agreed in the Wave 3 plan). Until it does, `useChartInstance` finds the
 * ECharts instance through the DOM and this module attaches to it directly. When the callbacks
 * land, replace `useChartInstance` + `attachChartEvents` with them; `PointClick`, `BrushSpan`
 * and the legend map are already the callback payloads.
 */
import { BrushComponent, ToolboxComponent } from "echarts/components";
import { type ECharts, use } from "echarts/core";
import type { BrushSpan, PointClick } from "./predicates";

// The brush tool is registered here: ui-charts does not register it yet (see the TODO above).
use([BrushComponent, ToolboxComponent]);

export interface ChartEventHandlers {
  /** A click on a mark. `extend` is true with shift/ctrl/cmd held (multi-select). */
  onPoint?(click: PointClick, extend: boolean): void;
  /** A finished brush. `null` when the brush was cleared. */
  onBrush?(span: BrushSpan | null): void;
  /** The legend's selected map after a toggle. */
  onLegend?(selected: Record<string, boolean>): void;
}

interface ClickParams {
  componentType?: string;
  name?: string | number;
  seriesName?: string;
  value?: unknown;
  event?: { event?: { shiftKey?: boolean; ctrlKey?: boolean; metaKey?: boolean } };
}

interface BrushArea {
  coordRange?: [number, number] | number[];
}

type AxisOption = { type?: string; data?: unknown[] };

/** ECharts' default series names are "series0", "series1": not a real colour group. */
const isGeneratedSeriesName = (name: string | undefined) => !name || /^series\d+$/.test(name);

/** First element of an `[x, y]` value pair when it is a number or ISO date string. */
function axisValueOf(value: unknown): string | number | null {
  if (Array.isArray(value) && (typeof value[0] === "number" || typeof value[0] === "string")) {
    return value[0];
  }
  return null;
}

export function clickToPoint(params: ClickParams): { click: PointClick; extend: boolean } | null {
  if (params.componentType && params.componentType !== "series") return null;
  const e = params.event?.event;
  return {
    click: {
      name: params.name ?? null,
      seriesName: isGeneratedSeriesName(params.seriesName) ? null : (params.seriesName ?? null),
      axisValue: axisValueOf(params.value),
    },
    extend: Boolean(e?.shiftKey || e?.ctrlKey || e?.metaKey),
  };
}

/** The brushed span in axis terms: category names for a category axis, a min/max otherwise. */
export function areaToSpan(
  area: BrushArea | undefined,
  xAxis: AxisOption | undefined,
): BrushSpan | null {
  const range = area?.coordRange;
  if (!range || range.length < 2) return null;
  const [a, b] = range as [number, number];
  if (xAxis?.type === "category" && Array.isArray(xAxis.data)) {
    const lo = Math.max(0, Math.round(Math.min(a, b)));
    const hi = Math.min(xAxis.data.length - 1, Math.round(Math.max(a, b)));
    const names = xAxis.data
      .slice(lo, hi + 1)
      .map((d) => (typeof d === "object" && d !== null ? (d as { value?: unknown }).value : d))
      .filter((d): d is string | number => typeof d === "string" || typeof d === "number")
      .map(String);
    return { categories: names };
  }
  return { min: Math.min(a, b), max: Math.max(a, b) };
}

function firstXAxis(chart: ECharts): AxisOption | undefined {
  const option = chart.getOption() as { xAxis?: AxisOption[] };
  return option.xAxis?.[0];
}

/** Enable the x-range brush tool on a chart (the toolbox button is the user's way to start it). */
export function ensureBrush(chart: ECharts, enabled: boolean): void {
  const option = chart.getOption() as { brush?: unknown[] };
  const has = Array.isArray(option.brush) ? option.brush.length > 0 : Boolean(option.brush);
  if (!enabled || has) return;
  chart.setOption({
    brush: {
      toolbox: ["lineX", "clear"],
      xAxisIndex: 0,
      brushLink: "none",
      throttleType: "debounce",
      throttleDelay: 150,
    },
    toolbox: { feature: { brush: { type: ["lineX", "clear"] } }, right: 8, top: 0, itemSize: 12 },
  });
}

/** Attach the handlers; returns a function that detaches them. */
export function attachChartEvents(
  chart: ECharts,
  handlers: () => ChartEventHandlers,
  options: { brush: boolean },
): () => void {
  const onClick = (params: unknown) => {
    const point = clickToPoint(params as ClickParams);
    if (point) handlers().onPoint?.(point.click, point.extend);
  };
  const onLegend = (params: unknown) => {
    const selected = (params as { selected?: Record<string, boolean> }).selected;
    if (selected) handlers().onLegend?.(selected);
  };
  const onBrushEnd = (params: unknown) => {
    const areas = (params as { areas?: BrushArea[] }).areas ?? [];
    handlers().onBrush?.(areas.length === 0 ? null : areaToSpan(areas[0], firstXAxis(chart)));
  };
  const onFinished = () => ensureBrush(chart, options.brush);

  chart.on("click", onClick);
  chart.on("legendselectchanged", onLegend);
  if (options.brush) {
    chart.on("brushEnd", onBrushEnd);
    // A rebuild of the chart (new identity) drops merged components: put the brush back.
    chart.on("finished", onFinished);
    ensureBrush(chart, true);
  }
  return () => {
    chart.off("click", onClick);
    chart.off("legendselectchanged", onLegend);
    chart.off("brushEnd", onBrushEnd);
    chart.off("finished", onFinished);
  };
}
