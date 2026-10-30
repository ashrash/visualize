import type { ECharts, EChartsCoreOption } from "echarts/core";
import { useEffect, useRef } from "react";
import { echarts } from "./echarts";
import { buildEChartsTheme } from "./theme";
import type { ChartTheme } from "./types";

export type XRange = [number, number];

interface EChartProps {
  option: EChartsCoreOption;
  className?: string;
  /** Accessible description of the chart; ECharts' aria module uses it instead of auto text. */
  ariaLabel?: string;
  /** Resolved tokens: the chart re-themes in place (`setTheme`) when this changes. */
  theme?: ChartTheme;
  /**
   * Identity of the chart (spec, plot type, orientation, ...). While it is unchanged, option
   * updates are merged so a cosmetic edit keeps the user's zoom and legend toggles; when it
   * changes the chart is rebuilt from scratch. Omit it to always rebuild.
   */
  resetKey?: string;
  /** Receives the instance once created (for export, `echarts.connect`, ...). */
  onReady?(chart: ECharts | null): void;
  /**
   * Fired (debounced) when the user zooms a numeric/time x axis, with the visible range, or
   * null when the zoom is reset. Category axes zoom client-side and never fire.
   */
  onViewport?(range: XRange | null): void;
}

type DataZoomState = {
  startValue?: unknown;
  endValue?: unknown;
  start?: number;
  end?: number;
  xAxisIndex?: number | number[];
  yAxisIndex?: number | number[];
};
type AxisState = { type?: string };

const firstIndex = (value: number | number[] | undefined): number | undefined =>
  Array.isArray(value) ? value[0] : value;

/**
 * Visible range of the first dataZoom on the axis it actually controls (the category axis of a
 * horizontal chart is a y axis). Only numeric, time and log axes have a meaningful range;
 * returns undefined for category axes (nothing to re-query), null when not zoomed.
 */
export function visibleRange(chart: ECharts): XRange | null | undefined {
  const option = chart.getOption() as {
    xAxis?: AxisState[];
    yAxis?: AxisState[];
    dataZoom?: DataZoomState[];
  };
  const zoom = option.dataZoom?.[0];
  if (!zoom) return undefined;
  const yIndex = firstIndex(zoom.yAxisIndex);
  const xIndex = firstIndex(zoom.xAxisIndex);
  // A dataZoom bound to a y axis (and not an x axis) controls that y axis.
  const axis =
    yIndex !== undefined && xIndex === undefined
      ? option.yAxis?.[yIndex]
      : option.xAxis?.[xIndex ?? 0];
  const type = axis?.type;
  if (type !== "value" && type !== "time" && type !== "log") return undefined;
  if ((zoom.start ?? 0) <= 0 && (zoom.end ?? 100) >= 100) return null;
  const { startValue: a, endValue: b } = zoom;
  return typeof a === "number" && typeof b === "number" ? [a, b] : undefined;
}

// Components that are replaced (not merged) on a same-chart update, so removed titles, legends
// and series disappear. dataZoom is deliberately merged: it holds the user's zoom.
const REPLACED = [
  "series",
  "xAxis",
  "yAxis",
  "grid",
  "title",
  "legend",
  "visualMap",
  "graphic",
  "polar",
  "angleAxis",
  "radiusAxis",
  "radar",
  "geo",
  "parallel",
  "parallelAxis",
  "singleAxis",
  "calendar",
];

/** What kind of zoom controls an option has (types and axes), ignoring the zoom position. */
function dataZoomShape(dataZoom: unknown): string {
  const list = Array.isArray(dataZoom) ? dataZoom : dataZoom ? [dataZoom] : [];
  return JSON.stringify(
    list.map((z: DataZoomState & { type?: string }) => [z.type, z.xAxisIndex, z.yAxisIndex]),
  );
}

/** Keep which legend entries the user switched off, for the names that still exist. */
function carryLegendSelection(chart: ECharts, option: Record<string, unknown>) {
  const previous = (chart.getOption() as { legend?: { selected?: Record<string, boolean> }[] })
    .legend?.[0]?.selected;
  const legend = option.legend;
  if (!previous || !legend || Array.isArray(legend)) return option;
  const current = legend as { selected?: Record<string, boolean> };
  return { ...option, legend: { ...current, selected: { ...previous, ...current.selected } } };
}

export function EChart({
  option,
  className,
  ariaLabel,
  theme,
  resetKey,
  onReady,
  onViewport,
}: EChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ECharts | null>(null);
  // Latest callbacks and initial theme, read inside the mount-once effect.
  const latest = useRef({ onReady, onViewport, theme });
  latest.current = { onReady, onViewport, theme };
  const themeKey = useRef<string>("");
  const appliedKey = useRef<string | undefined>(undefined);
  const appliedZoomShape = useRef<string>("");

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const initial = latest.current.theme;
    const chart = echarts.init(container, initial ? buildEChartsTheme(initial) : undefined, {
      renderer: "canvas",
    });
    themeKey.current = JSON.stringify(initial ?? null);
    appliedKey.current = undefined;
    chartRef.current = chart;
    latest.current.onReady?.(chart);

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container);

    let timer: ReturnType<typeof setTimeout> | undefined;
    chart.on("datazoom", () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        const range = visibleRange(chart);
        if (range !== undefined) latest.current.onViewport?.(range);
      }, 150);
    });

    return () => {
      clearTimeout(timer);
      observer.disconnect();
      latest.current.onReady?.(null);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    // Switch light/dark in place; the option below is re-rendered with the new tokens too.
    const key = JSON.stringify(theme ?? null);
    if (theme && key !== themeKey.current) {
      themeKey.current = key;
      chart.setTheme(buildEChartsTheme(theme));
    }
    // Accessibility for every plot, not only those that go through applyStyle. A description
    // keeps ECharts from replacing the container's aria-label with generated text.
    const base = option as Record<string, unknown>;
    const aria = {
      enabled: true,
      ...(base.aria as Record<string, unknown> | undefined),
      ...(ariaLabel ? { label: { description: ariaLabel } } : {}),
    };
    const withAria = { ...base, aria };
    const zoomShape = dataZoomShape(base.dataZoom);
    const zoomChanged = zoomShape !== appliedZoomShape.current;
    appliedZoomShape.current = zoomShape;
    const rebuild = resetKey === undefined || resetKey !== appliedKey.current;
    appliedKey.current = resetKey;
    if (rebuild) {
      chart.setOption(withAria, { notMerge: true });
    } else {
      // replaceMerge only accepts registered component types. Those in use (now or before) are
      // registered by definition, so restrict the list to them ("polar" etc. would throw).
      const present = new Set([...Object.keys(chart.getOption()), ...Object.keys(withAria)]);
      chart.setOption(carryLegendSelection(chart, withAria), {
        notMerge: false,
        // dataZoom holds the user's zoom, so it is only replaced when its shape changes
        // (a slider appears or goes away), not on every update.
        replaceMerge: [...REPLACED, ...(zoomChanged ? ["dataZoom"] : [])].filter((type) =>
          present.has(type),
        ),
      });
    }
  }, [option, theme, ariaLabel, resetKey]);

  return <div ref={containerRef} className={className} role="img" aria-label={ariaLabel} />;
}
