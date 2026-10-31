import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowRight, ArrowUpRight } from "lucide-react";
import { useMemo } from "react";
import { describeFields } from "../../api/stats";
import type { ChartSpec, FieldSummary } from "../../api/types";
import { makeFormatter, useChartData } from "../../charts";
import { useDashboardEnv, useWidgetFilters } from "../env";
import {
  computeDelta,
  describeRequest,
  headlineValue,
  type KpiDelta,
  sparklinePoints,
  sparklineSpec,
  summaryValue,
} from "../kpi";
import type { KpiWidgetValue } from "../types";

const SPARK_W = 120;
const SPARK_H = 32;

function deltaLabel(delta: KpiDelta, format: (v: unknown) => string): string {
  const pct =
    delta.fraction === null
      ? null
      : `${delta.fraction > 0 ? "+" : ""}${(delta.fraction * 100).toFixed(1)}%`;
  const abs = `${delta.change > 0 ? "+" : ""}${format(delta.change)}`;
  const base = delta.kind === "target" ? "vs target" : "vs previous period";
  return pct ? `${pct} (${abs}) ${base}` : `${abs} ${base}`;
}

export function KpiWidget({ widgetId, widget }: { widgetId: string; widget: KpiWidgetValue }) {
  const env = useDashboardEnv();
  const spark = useMemo(() => sparklineSpec(widgetId, widget), [widgetId, widget]);
  // A KPI is in no link group: it follows every group's selection plus the dashboard filters.
  const stand: ChartSpec = useMemo(
    () =>
      spark ??
      ({
        id: `kpi:${widgetId}`,
        name: widget.title,
        plotType: "bar",
        source: widget.source,
        encodings: {},
      } as ChartSpec),
    [spark, widgetId, widget],
  );
  const filters = useWidgetFilters(widgetId, stand, { followAllGroups: true });

  const columns = env.columnsOf(widget.source.id);
  const fallbackField = columns ? [...columns][0] : undefined;
  const sourcePipelines = widget.source.kind === "pipeline" ? env.pipelines : [];
  const request = describeRequest(widget, filters, sourcePipelines, fallbackField);
  const total = useQuery({
    queryKey: ["kpi-describe", request],
    queryFn: ({ signal }) => describeFields(request as NonNullable<typeof request>, signal),
    enabled: request !== null,
    placeholderData: keepPreviousData,
  });
  const series = useChartData(stand, filters, spark !== null, { pipelines: env.pipelines });

  const summaries = (total.data?.values as { summaries?: FieldSummary[] } | undefined)?.summaries;
  const overall = summaries?.find((s) => s.group === null) ?? summaries?.[0];
  const agg = widget.measure.agg ?? (widget.measure.field ? "sum" : "count");
  const totalValue = overall ? summaryValue(overall, agg, Boolean(widget.measure.field)) : null;
  const points = series.data?.columns.y0 ?? [];
  const value = headlineValue({
    comparison: widget.comparison ?? "none",
    series: points,
    total: totalValue,
  });
  const delta = computeDelta({
    comparison: widget.comparison ?? "none",
    series: points,
    value,
    target: widget.target,
  });
  const format = makeFormatter(widget.format ?? "compact:1");
  const polyline = sparklinePoints(points, SPARK_W, SPARK_H);
  const error = total.error ?? series.error;

  const Icon =
    delta?.direction === "up"
      ? ArrowUpRight
      : delta?.direction === "down"
        ? ArrowDownRight
        : ArrowRight;
  return (
    <div className="flex h-full flex-col justify-between gap-1 p-3">
      <p className="truncate text-xs font-medium text-muted">{widget.title}</p>
      {error && value === null ? (
        <p role="alert" className="text-sm text-danger">
          {error.message}
        </p>
      ) : (
        <p className="text-3xl font-semibold tabular-nums text-fg" aria-live="polite">
          {value === null ? "–" : format(value)}
        </p>
      )}
      <div className="flex items-end justify-between gap-2">
        {delta ? (
          // Direction is spelled out (icon + sign + words), not colour alone.
          <p className="inline-flex items-center gap-1 text-xs text-fg">
            <Icon size={14} aria-hidden="true" />
            <span>{deltaLabel(delta, format)}</span>
          </p>
        ) : (
          <span />
        )}
        {polyline ? (
          <svg
            width={SPARK_W}
            height={SPARK_H}
            viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
            role="img"
            aria-label={`${widget.title} trend`}
            className="shrink-0 text-accent"
          >
            <polyline points={polyline} fill="none" stroke="currentColor" strokeWidth="1.5" />
          </svg>
        ) : null}
      </div>
    </div>
  );
}
