/**
 * KPI card logic. The headline value is exact: it comes from `/api/stats/describe` for one field
 * (modes and extra percentiles off). The endpoint still computes the quartiles and median of that
 * field, so a KPI costs one full scan with a sort-like step per refresh: a perf watch-item (STATUS).
 * A lighter aggregate-only request would need an ungrouped aggregate on the chart-data side. The sparkline and the period-over-period delta come from an ordinary
 * time-bucketed line chart request, so cross-filters and the 50k-mark cap apply unchanged.
 */
import type { ChartSpec, DescribeRequest, FieldSummary, Pipeline, Predicate } from "../api/types";
import type { KpiWidgetValue } from "./types";

type Agg = NonNullable<KpiWidgetValue["measure"]["agg"]>;

/** Bucket used for the sparkline when the widget has a time field but no unit. */
export const DEFAULT_KPI_TIME_UNIT = "month";

/** Sparkline chart request for a KPI widget; `null` when the widget has no time field. */
export function sparklineSpec(widgetId: string, widget: KpiWidgetValue): ChartSpec | null {
  const time = widget.time;
  if (!time?.field) return null;
  return {
    id: `kpi:${widgetId}`,
    name: widget.title,
    plotType: "line",
    source: widget.source,
    encodings: {
      x: [{ ...time, timeUnit: time.timeUnit ?? DEFAULT_KPI_TIME_UNIT }],
      y: [
        { ...widget.measure, agg: widget.measure.agg ?? (widget.measure.field ? "sum" : "count") },
      ],
    },
    params: {},
    interactions: { zoom: false, brush: false, clickFilter: false, drillHierarchy: [] },
  } as ChartSpec;
}

/** The FieldSummary key an aggregate reads. `count` without a field is rows, not non-missing. */
export function summaryValue(summary: FieldSummary, agg: Agg, hasField: boolean): number | null {
  switch (agg) {
    case "sum":
      return summary.sum;
    case "mean":
      return summary.mean;
    case "median":
      return typeof summary.median === "number" ? summary.median : null;
    case "min":
      return typeof summary.min === "number" ? summary.min : null;
    case "max":
      return typeof summary.max === "number" ? summary.max : null;
    case "std":
      return summary.std;
    case "var":
      return summary.variance;
    case "count_distinct":
      return summary.distinct;
    case "count":
      return hasField ? summary.count : summary.count + summary.missing;
  }
}

/** Describe request for the headline value, or null when there is nothing to describe yet. */
export function describeRequest(
  widget: KpiWidgetValue,
  filters: Predicate[],
  pipelines: Pipeline[],
  fallbackField: string | undefined,
): DescribeRequest | null {
  const field = widget.measure.field ?? fallbackField;
  if (!field) return null;
  return {
    source: widget.source,
    ...(pipelines.length > 0 ? { pipelines } : {}),
    fields: [field],
    filters,
    includeOverall: true,
    includeMode: false,
    percentiles: [],
    maxGroups: 1,
  };
}

export interface KpiDelta {
  kind: "previous_period" | "target";
  /** Absolute change. */
  change: number;
  /** Relative change as a fraction, or null when the base is zero. */
  fraction: number | null;
  direction: "up" | "down" | "flat";
}

function direction(change: number): KpiDelta["direction"] {
  return change > 0 ? "up" : change < 0 ? "down" : "flat";
}

const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

/**
 * The comparison under the value. `previous_period` compares the last two sparkline buckets
 * (the headline is then the last bucket too); `target` compares the value with the target.
 */
export function computeDelta(args: {
  comparison: KpiWidgetValue["comparison"];
  series: readonly unknown[];
  value: number | null;
  target: number | null | undefined;
}): KpiDelta | null {
  const { comparison, series, value, target } = args;
  if (comparison === "previous_period") {
    const last = series.at(-1);
    const previous = series.at(-2);
    if (!finite(last) || !finite(previous)) return null;
    const change = last - previous;
    return {
      kind: "previous_period",
      change,
      fraction: previous === 0 ? null : change / Math.abs(previous),
      direction: direction(change),
    };
  }
  if (comparison === "target" && finite(value) && finite(target)) {
    const change = value - target;
    return {
      kind: "target",
      change,
      fraction: target === 0 ? null : change / Math.abs(target),
      direction: direction(change),
    };
  }
  return null;
}

/** Headline value: the last bucket when comparing with the previous period, else the total. */
export function headlineValue(args: {
  comparison: KpiWidgetValue["comparison"];
  series: readonly unknown[];
  total: number | null;
}): number | null {
  if (args.comparison === "previous_period") {
    const last = args.series.at(-1);
    return finite(last) ? last : null;
  }
  return args.total;
}

/** Points for the sparkline polyline in a `width` x `height` box (y flipped, padded). */
export function sparklinePoints(
  values: readonly unknown[],
  width: number,
  height: number,
  pad = 2,
): string {
  const ys = values.filter(finite);
  if (ys.length < 2) return "";
  const lo = Math.min(...ys);
  const hi = Math.max(...ys);
  const span = hi - lo || 1;
  const step = (width - pad * 2) / (ys.length - 1);
  return ys
    .map(
      (y, i) =>
        `${(pad + i * step).toFixed(1)},${(height - pad - ((y - lo) / span) * (height - pad * 2)).toFixed(1)}`,
    )
    .join(" ");
}
