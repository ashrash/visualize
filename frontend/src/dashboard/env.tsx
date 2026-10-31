import { useQueries } from "@tanstack/react-query";
import { createContext, type ReactNode, useContext, useMemo } from "react";
import { inspectPipeline } from "../api/pipelines";
import type { ChartSpec, Pipeline, Predicate } from "../api/types";
import { useDatasets } from "../data/hooks";
import { referencedPipelines } from "../data/pipeline/usePipelineApi";
import { filtersForChart } from "./bus";
import { useDashboardRuntime } from "./runtimeStore";
import type { Dashboard, DashboardFilter, DrillTarget } from "./types";

/** Edit lets the user rearrange; view is read-only; embed is read-only without chrome. */
export type DashboardMode = "edit" | "view" | "embed";

export interface DashboardEnv {
  dashboard: Dashboard;
  charts: ChartSpec[];
  pipelines: Pipeline[];
  /** Project-wide filters (a date range across dashboards). */
  globalFilters: DashboardFilter[];
  mode: DashboardMode;
  /** Known columns of a source, or undefined when not known yet. */
  columnsOf: (sourceId: string) => ReadonlySet<string> | undefined;
  /** Open a target with predicates applied (drill-through). Absent where it cannot go. */
  onDrillThrough?: (target: DrillTarget, predicates: Predicate[]) => void;
  /** Other dashboards of the project, offered as drill-through targets. */
  otherDashboards?: { id: string; name: string }[];
  /** Save a filter's live value as its default. Only in edit mode. */
  onSaveFilterDefault?: (filterId: string, predicate: Predicate | null) => void;
}

const Context = createContext<DashboardEnv | null>(null);

export function DashboardEnvProvider({
  value,
  children,
}: {
  value: DashboardEnv;
  children: ReactNode;
}) {
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useDashboardEnv(): DashboardEnv {
  const env = useContext(Context);
  if (!env) throw new Error("useDashboardEnv must be used inside a DashboardEnvProvider");
  return env;
}

/**
 * Columns per source, for deciding which cross-filter predicates a chart can take. Dataset
 * columns come from the dataset list; a pipeline's from the server's schema inspection.
 */
export function useSourceColumns(
  charts: ChartSpec[],
  pipelines: Pipeline[],
): DashboardEnv["columnsOf"] {
  const datasets = useDatasets().data;
  const pipelineIds = useMemo(
    () => [...new Set(charts.filter((c) => c.source.kind === "pipeline").map((c) => c.source.id))],
    [charts],
  );
  const inspected = useQueries({
    queries: pipelineIds.map((id) => {
      const pipeline = pipelines.find((p) => p.id === id);
      return {
        queryKey: [
          "dashboard-pipeline-columns",
          pipeline,
          pipeline ? referencedPipelines(pipeline, pipelines) : [],
        ],
        queryFn: ({ signal }: { signal: AbortSignal }) =>
          inspectPipeline(
            {
              pipeline: pipeline as Pipeline,
              pipelines: referencedPipelines(pipeline as Pipeline, pipelines),
              includeSql: false,
            },
            signal,
          ),
        enabled: pipeline !== undefined,
        staleTime: 60_000,
      };
    }),
  });
  // Column names per source as one string, so the callback only changes when they do.
  const key = JSON.stringify([
    (datasets ?? []).map((d) => [d.id, d.versions?.at(-1)?.columns?.map((c) => c.name) ?? null]),
    pipelineIds.map((id, i) => [id, inspected[i]?.data?.columns.map((c) => c.name) ?? null]),
  ]);
  return useMemo(() => {
    const [fromDatasets, fromPipelines] = JSON.parse(key) as [
      [string, string[] | null][],
      [string, string[] | null][],
    ];
    const byId = new Map<string, ReadonlySet<string>>();
    for (const [id, names] of [...fromDatasets, ...fromPipelines]) {
      if (names) byId.set(id, new Set(names));
    }
    return (sourceId: string) => byId.get(sourceId);
  }, [key]);
}

/** The `filters` for one chart widget: global + dashboard filters, drill, and others' selections. */
export function useWidgetFilters(
  widgetId: string,
  spec: ChartSpec,
  options: { followAllGroups?: boolean } = {},
): Predicate[] {
  const env = useDashboardEnv();
  const bus = useDashboardRuntime((s) => s.bus);
  const filterValues = useDashboardRuntime((s) => s.filterValues);
  const globalValues = useDashboardRuntime((s) => s.globalValues);
  const drill = useDashboardRuntime((s) => s.drill);
  return useMemo(
    () =>
      filtersForChart({
        dashboard: env.dashboard,
        spec,
        widgetId,
        bus,
        filterValues,
        globalFilters: env.globalFilters,
        globalValues,
        drill,
        followAllGroups: options.followAllGroups,
        columnsOf: env.columnsOf,
      }),
    [
      env.dashboard,
      env.globalFilters,
      env.columnsOf,
      spec,
      widgetId,
      bus,
      filterValues,
      globalValues,
      drill,
      options.followAllGroups,
    ],
  );
}
