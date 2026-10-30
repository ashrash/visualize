import type { ECharts, EChartsCoreOption } from "echarts/core";
import { useEffect, useRef } from "react";
import { echarts } from "./echarts";
import { buildEChartsTheme } from "./theme";
import type { ChartTheme } from "./types";

export type XRange = [number, number];

interface EChartProps {
  option: EChartsCoreOption;
  className?: string;
  ariaLabel?: string;
  /** Resolved tokens: the chart re-themes in place (`setTheme`) when this changes. */
  theme?: ChartTheme;
  /** Receives the instance once created (for export, `echarts.connect`, ...). */
  onReady?(chart: ECharts | null): void;
  /**
   * Fired (debounced) when the user zooms a numeric/time x axis, with the visible range, or
   * null when the zoom is reset. Category axes zoom client-side and never fire.
   */
  onViewport?(range: XRange | null): void;
}

type DataZoomState = { startValue?: unknown; endValue?: unknown; start?: number; end?: number };

/** Visible x range of the first dataZoom, if the x axis is numeric or time. */
export function visibleRange(chart: ECharts): XRange | null | undefined {
  const option = chart.getOption() as {
    xAxis?: { type?: string }[];
    dataZoom?: DataZoomState[];
  };
  const type = option.xAxis?.[0]?.type;
  if (type !== "value" && type !== "time" && type !== "log") return undefined;
  const zoom = option.dataZoom?.[0];
  if (!zoom) return undefined;
  if ((zoom.start ?? 0) <= 0 && (zoom.end ?? 100) >= 100) return null;
  const { startValue: a, endValue: b } = zoom;
  return typeof a === "number" && typeof b === "number" ? [a, b] : undefined;
}

export function EChart({ option, className, ariaLabel, theme, onReady, onViewport }: EChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ECharts | null>(null);
  // Latest callbacks and initial theme, read inside the mount-once effect.
  const latest = useRef({ onReady, onViewport, theme });
  latest.current = { onReady, onViewport, theme };
  const themeKey = useRef<string>("");

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const initial = latest.current.theme;
    const chart = echarts.init(container, initial ? buildEChartsTheme(initial) : undefined, {
      renderer: "canvas",
    });
    themeKey.current = JSON.stringify(initial ?? null);
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
    chart.setOption(option, { notMerge: true });
  }, [option, theme]);

  return <div ref={containerRef} className={className} role="img" aria-label={ariaLabel} />;
}
