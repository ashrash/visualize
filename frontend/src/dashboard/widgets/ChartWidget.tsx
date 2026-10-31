import { useQueryClient } from "@tanstack/react-query";
import { Link2, Table2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef } from "react";
import type { ChartSpec, Predicate } from "../../api/types";
import {
  type ChartBrush,
  type ChartLegendChange,
  type ChartPointClick,
  ChartView,
} from "../../charts";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { groupOf, selectionOf } from "../bus";
import { colorValueFor } from "../chartLookup";
import { BRUSH_DEBOUNCE_MS, debounce } from "../debounce";
import { describePredicate } from "../describe";
import { useDashboardEnv, useWidgetFilters } from "../env";
import { datasetOfSource } from "../filterControls";
import { brushPredicates, canClickFilter, legendPredicates, pointPredicates } from "../predicates";
import { useDashboardRuntime } from "../runtimeStore";

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
  const client = useQueryClient();
  const interact = useDashboardRuntime((s) => s.interact);
  const clearSelection = useDashboardRuntime((s) => s.clearSelection);

  // The chart wrapper syncs tooltips and zoom across a link group (echarts.connect) from the
  // spec; the dashboard link group decides, so hand it the effective group. While arranging the
  // dashboard there is no brush band: charts are being moved, not explored.
  const interactive = env.mode !== "edit";
  const viewSpec = useMemo<ChartSpec>(() => {
    const linkGroup = group ?? spec.interactions?.linkGroup;
    const brush = interactive && spec.interactions?.brush === true;
    if (linkGroup === spec.interactions?.linkGroup && brush === spec.interactions?.brush)
      return spec;
    return { ...spec, interactions: { ...spec.interactions, linkGroup, brush } } as ChartSpec;
  }, [spec, group, interactive]);

  // Handlers read the latest spec and group without being recreated on every render.
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
      // Brushes and legend toggles can fire several times in a row: commit once, after a pause.
      settle: debounce((p: Predicate[]) => send(p, "replace"), BRUSH_DEBOUNCE_MS),
    };
  }, [interact]);
  useEffect(() => () => publish.settle.cancel(), [publish]);

  const clickable = spec.interactions?.clickFilter !== false && canClickFilter(spec);

  const onPointClick = useCallback(
    (hit: ChartPointClick) => {
      const { spec: s } = latest.current;
      const color = colorValueFor(client, s, hit.seriesName);
      const predicates = pointPredicates(s, hit, color);
      if (predicates.length > 0) publish.send(predicates, hit.shiftKey ? "extend" : "toggle");
    },
    [client, publish],
  );
  const onBrushSelected = useCallback(
    (brush: ChartBrush) => publish.settle(brushPredicates(latest.current.spec, brush)),
    [publish],
  );
  const onLegendChange = useCallback(
    (change: ChartLegendChange) => publish.settle(legendPredicates(change)),
    [publish],
  );

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
      <div className="min-h-0 flex-1">
        <ChartView
          spec={viewSpec}
          filters={filters}
          pipelines={env.pipelines}
          // Each widget has its own cancellation slot: one chart shown twice must not cancel itself.
          slot={widgetId}
          onPointClick={interactive && clickable ? onPointClick : undefined}
          onBrushSelected={interactive ? onBrushSelected : undefined}
          onLegendChange={interactive ? onLegendChange : undefined}
        />
      </div>
    </div>
  );
}
