import { Link2, Table2, X } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import type { ChartSpec, Predicate } from "../../api/types";
import { ChartView } from "../../charts";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { groupOf, selectionOf } from "../bus";
import { attachChartEvents } from "../chartEvents";
import { BRUSH_DEBOUNCE_MS, debounce } from "../debounce";
import { describePredicate } from "../describe";
import { useDashboardEnv, useWidgetFilters } from "../env";
import { datasetOfSource } from "../filterControls";
import { brushPredicates, legendPredicates, pointPredicates } from "../predicates";
import { useDashboardRuntime } from "../runtimeStore";
import { useChartInstance } from "../useChartInstance";

interface Props {
  widgetId: string;
  chartId: string;
}

export function ChartWidget({ widgetId, chartId }: Props) {
  const env = useDashboardEnv();
  const spec = env.charts.find((c) => c.id === chartId);
  if (!spec) {
    return (
      <div className="flex h-full items-center justify-center p-3 text-sm text-muted">
        This chart is no longer in the project.
      </div>
    );
  }
  return <LoadedChart widgetId={widgetId} spec={spec} />;
}

function LoadedChart({ widgetId, spec }: { widgetId: string; spec: ChartSpec }) {
  const env = useDashboardEnv();
  const filters = useWidgetFilters(widgetId, spec);
  const group = groupOf(env.dashboard, spec);
  const own = useDashboardRuntime((s) => selectionOf(s.bus, group, widgetId));
  const interact = useDashboardRuntime((s) => s.interact);
  const clearSelection = useDashboardRuntime((s) => s.clearSelection);
  const forgetWidget = useDashboardRuntime((s) => s.forgetWidget);

  // The chart wrapper syncs tooltips and zoom across a link group (echarts.connect) from the
  // spec; the dashboard link group decides, so hand it the effective group.
  const viewSpec = useMemo<ChartSpec>(
    () =>
      group && spec.interactions?.linkGroup !== group
        ? ({ ...spec, interactions: { ...spec.interactions, linkGroup: group } } as ChartSpec)
        : spec,
    [spec, group],
  );

  const frame = useRef<HTMLDivElement>(null);
  const chart = useChartInstance(frame);

  // Handlers read the latest spec and group without re-attaching listeners.
  const latest = useRef({ spec, group, widgetId });
  latest.current = { spec, group, widgetId };
  const publish = useMemo(() => {
    const send = (predicates: Predicate[], mode: "toggle" | "replace" | "extend") => {
      const { spec: s, group: g, widgetId: w } = latest.current;
      if (!g) return;
      interact({ groupId: g, widgetId: w, sourceId: s.source.id, predicates, mode });
    };
    return {
      send,
      // Brush drags and legend toggles can fire many times a second: commit once, after a pause.
      brush: debounce((p: Predicate[]) => send(p, "replace"), BRUSH_DEBOUNCE_MS),
    };
  }, [interact]);
  useEffect(() => () => publish.brush.cancel(), [publish]);

  const clickable = spec.interactions?.clickFilter !== false;
  const brushable = spec.interactions?.brush === true;
  useEffect(() => {
    if (!chart || env.mode === "edit") return;
    return attachChartEvents(
      chart,
      () => ({
        onPoint: (click, extend) => {
          const { spec: s } = latest.current;
          if (!clickable) return;
          const predicates = pointPredicates(s, click);
          if (predicates.length > 0) publish.send(predicates, extend ? "extend" : "toggle");
        },
        onBrush: (span) => publish.brush(span ? brushPredicates(latest.current.spec, span) : []),
        onLegend: (selected) => publish.brush(legendPredicates(latest.current.spec, selected)),
      }),
      { brush: brushable },
    );
  }, [chart, env.mode, clickable, brushable, publish]);

  // A chart that leaves the dashboard must not keep filtering the others.
  useEffect(() => () => forgetWidget(widgetId), [forgetWidget, widgetId]);

  const drillTargets = (env.onDrillThrough ? (env.otherDashboards ?? []) : []).slice(0, 12);
  const drillPredicates = [...filters, ...own];

  return (
    <div className="flex h-full flex-col">
      {own.length > 0 || (env.onDrillThrough && env.mode !== "embed") ? (
        <div className="flex flex-wrap items-center gap-1 px-1 pb-1 text-xs">
          {own.map((p) => (
            <span
              key={`${p.field}:${p.op}`}
              className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-2 py-px text-fg"
            >
              <Link2 size={12} aria-hidden="true" />
              {describePredicate(p)}
            </span>
          ))}
          {own.length > 0 && group ? (
            <button
              type="button"
              aria-label="Clear selection"
              onClick={() => clearSelection(group, widgetId)}
              className="rounded p-0.5 text-muted hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-accent"
            >
              <X size={12} aria-hidden="true" />
            </button>
          ) : null}
          {env.onDrillThrough && env.mode !== "embed" ? (
            <DropdownMenu>
              <DropdownMenuTrigger
                aria-label="Drill through"
                className="ml-auto inline-flex items-center gap-1 rounded-md border border-line bg-bg px-2 py-0.5 text-xs text-fg hover:bg-surface focus-visible:outline-2 focus-visible:outline-accent"
              >
                <Table2 size={12} aria-hidden="true" />
                Drill through
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>
                  {drillPredicates.length > 0
                    ? `With ${drillPredicates.length} filter${drillPredicates.length === 1 ? "" : "s"} applied`
                    : "No filters applied"}
                </DropdownMenuLabel>
                <DropdownMenuItem
                  onSelect={() =>
                    env.onDrillThrough?.(
                      { kind: "grid", datasetId: datasetOfSource(spec.source, env.pipelines) },
                      drillPredicates,
                    )
                  }
                >
                  Open data grid
                </DropdownMenuItem>
                {drillTargets.map((d) => (
                  <DropdownMenuItem
                    key={d.id}
                    onSelect={() =>
                      env.onDrillThrough?.(
                        { kind: "dashboard", dashboardId: d.id },
                        drillPredicates,
                      )
                    }
                  >
                    {`Open dashboard: ${d.name}`}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
        </div>
      ) : null}
      <div ref={frame} className="min-h-0 flex-1">
        {/* TODO(ui-charts): pass `slot={widgetId}` once ChartView accepts it, so two widgets that
            show one chart do not supersede each other's in-flight requests on the server. */}
        <ChartView spec={viewSpec} filters={filters} pipelines={env.pipelines} />
      </div>
    </div>
  );
}
