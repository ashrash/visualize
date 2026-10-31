import type { ECharts, EChartsCoreOption } from "echarts/core";
import { useEffect, useRef } from "react";
import { echarts } from "./echarts";
import {
  type BrushPayload,
  brushPayloadFrom,
  type LegendChange,
  legendChangeFrom,
  type PointHit,
  pointHitFrom,
} from "./events";
import { createGestureGate } from "./gesture";
import { joinLinkGroup } from "./linkGroup";
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
  /**
   * Charts with the same group sync tooltips, zoom, legend toggles and highlights
   * (`echarts.connect`). Pass `interactions.linkGroup`.
   */
  linkGroup?: string | null;
  /** A click on a series mark. */
  onPointClick?(hit: PointHit): void;
  /**
   * A committed brush (the option has a `brush`): the covered range or categories on the x
   * channel's axis, or `cleared`. Brushing is turned on whenever the option carries `brush`.
   */
  onBrush?(brush: BrushPayload): void;
  /** The legend state after an entry was toggled. */
  onLegendChange?(change: LegendChange): void;
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

// Components that are merged, never replaced, on a same-chart update because they hold state the
// user built up: dataZoom is the zoom (replaced only when its shape changes, see below); brush is
// the selection. EVERYTHING else present in the old or the new option is replaced, so a removed
// title, legend, tooltip formatter, dataset, matrix, timeline or toolbox cannot linger and plot
// agents never need to edit this file for a new component type.
const KEEP_STATE = ["dataZoom", "brush"];

/**
 * The component types to pass as `replaceMerge` on a same-chart update: every component present
 * in the old or the new option, except state-holding ones (`KEEP_STATE`; dataZoom is included
 * when `zoomChanged`).
 */
export function replaceMergeTypes(previous: object, next: object, zoomChanged: boolean): string[] {
  const present = new Set([...Object.keys(previous), ...Object.keys(next)]);
  return [...present].filter(
    (key) =>
      isComponentType(key) && (!KEEP_STATE.includes(key) || (key === "dataZoom" && zoomChanged)),
  );
}

/** Whether an option key is an ECharts component main type (and so valid in replaceMerge). */
function isComponentType(key: string): boolean {
  const model = echarts.ComponentModel as unknown as { hasClass?(type: string): boolean };
  return model.hasClass?.(key) === true;
}

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
  linkGroup,
  onPointClick,
  onBrush,
  onLegendChange,
}: EChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ECharts | null>(null);
  // Latest callbacks and initial theme, read inside the mount-once effect.
  const latest = useRef({ onReady, onViewport, theme, onPointClick, onBrush, onLegendChange });
  latest.current = { onReady, onViewport, theme, onPointClick, onBrush, onLegendChange };
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
    chart.on("click", (params: unknown) => {
      const hit = pointHitFrom(params);
      if (hit) latest.current.onPointClick?.(hit);
    });
    // Linked charts replay each other's brush and legend actions as if the user had made them.
    // Only a gesture on THIS chart is reported (see gesture.ts).
    const gate = createGestureGate(chart.getZr());
    chart.on("legendselectchanged", (event: unknown) => {
      if (gate.isLocal()) latest.current.onLegendChange?.(legendChangeFrom(event));
    });
    // A committed brush; `brush` also fires while dragging, so only its "removed" case is used.
    chart.on("brushEnd", (event: unknown) => {
      if (!gate.isLocal()) return;
      const payload = brushPayloadFrom(event, chart.getOption() as Record<string, unknown>);
      if (payload) latest.current.onBrush?.(payload);
    });
    chart.on("brush", (event: unknown) => {
      if (!gate.isLocal()) return;
      if (((event as { areas?: unknown[] }).areas ?? []).length > 0) return;
      const payload = brushPayloadFrom(event, chart.getOption() as Record<string, unknown>);
      if (payload) latest.current.onBrush?.(payload);
    });

    return () => {
      clearTimeout(timer);
      gate.dispose();
      observer.disconnect();
      latest.current.onReady?.(null);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !linkGroup) return;
    return joinLinkGroup(chart, linkGroup);
  }, [linkGroup]);

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
      // replaceMerge only accepts component main types: keep to option keys that are one.
      const replaced = replaceMergeTypes(chart.getOption(), withAria, zoomChanged);
      chart.setOption(carryLegendSelection(chart, withAria), {
        notMerge: false,
        // dataZoom holds the user's zoom, so it is only replaced when its shape changes
        // (a slider appears or goes away), not on every update.
        replaceMerge: replaced,
      });
    }
    // A `brush` component draws nothing until a brush tool is active: switch it on so the user
    // can drag a band over the chart without a toolbox.
    const brush = base.brush as { brushType?: string; brushMode?: string } | undefined;
    if (brush) {
      chart.dispatchAction({
        type: "takeGlobalCursor",
        key: "brush",
        brushOption: {
          brushType: brush.brushType ?? "lineX",
          brushMode: brush.brushMode ?? "multiple",
        },
      });
    }
  }, [option, theme, ariaLabel, resetKey]);

  return <div ref={containerRef} className={className} role="img" aria-label={ariaLabel} />;
}
