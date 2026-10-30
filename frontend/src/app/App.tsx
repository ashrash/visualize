import { useState } from "react";
import { useStore } from "zustand";
import type { ChartSpec, Column, Dataset } from "../api/types";
import { ChartBuilder, ChartView } from "../charts";
import { DataGrid } from "../data/DataGrid";
import { DatasetList } from "../data/DatasetList";
import { useDatasets } from "../data/hooks";
import { ImportPanel } from "../data/ImportPanel";
import { redo, undo, useProjectStore } from "../state/projectStore";
import { applyThemeMode, storedThemeMode, type ThemeMode } from "../state/themeMode";
import { useProjectSession } from "./useProjectSession";

const buttonClass =
  "rounded-md border border-line px-2.5 py-1 text-sm hover:bg-surface-2 disabled:opacity-40 disabled:hover:bg-transparent";

function defaultBarChart(dataset: Dataset, columns: Column[]): ChartSpec {
  const category =
    columns.find((c) => c.semantic === "nominal" && c.approxDistinct <= 100) ?? columns[0];
  // Prefer a real measure over identifier columns such as order_id.
  const looksLikeId = (name: string) => /(^|[_\s-])id$/i.test(name);
  const measure = columns.find(
    (c) => c.semantic === "quantitative" && c.name !== category?.name && !looksLikeId(c.name),
  );
  return {
    id: crypto.randomUUID(),
    name: `${dataset.name} chart`,
    plotType: "bar",
    source: { kind: "dataset", id: dataset.id },
    encodings: {
      x: [{ field: category?.name ?? null }],
      y: [measure ? { field: measure.name, agg: "sum" } : { agg: "count" }],
    },
  };
}

function ThemeSwitch() {
  const [mode, setMode] = useState<ThemeMode>(() => {
    const stored = storedThemeMode();
    applyThemeMode(stored);
    return stored;
  });
  return (
    <label className="flex items-center gap-1 text-sm text-muted">
      Theme
      <select
        className="rounded-md border border-line bg-bg px-1.5 py-1 text-sm text-fg"
        value={mode}
        onChange={(event) => {
          const next = event.target.value as ThemeMode;
          applyThemeMode(next);
          setMode(next);
        }}
      >
        <option value="auto">Auto</option>
        <option value="light">Light</option>
        <option value="dark">Dark</option>
      </select>
    </label>
  );
}

export function App() {
  const { session, save } = useProjectSession();
  const datasets = useDatasets();
  const project = useProjectStore((state) => state.project);
  const dirty = useProjectStore((state) => state.dirty);
  const update = useProjectStore((state) => state.update);
  const canUndo = useStore(useProjectStore.temporal, (state) => state.pastStates.length > 0);
  const canRedo = useStore(useProjectStore.temporal, (state) => state.futureStates.length > 0);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const allDatasets = datasets.data ?? [];
  const charts = project?.charts ?? [];
  // Until the project loads we don't know which dataset its chart uses, so don't pick one yet
  // (picking the first dataset would fetch rows only to cancel them a moment later).
  const defaultId = project ? (charts[0]?.source.id ?? allDatasets[0]?.id) : undefined;
  const activeId = selectedId ?? defaultId ?? null;
  const dataset = allDatasets.find((d) => d.id === activeId);
  const columns = dataset?.versions?.at(-1)?.columns ?? [];
  const chart = dataset ? charts.find((c) => c.source.id === dataset.id) : undefined;

  const addChart = () => {
    if (!dataset) return;
    update((p) => ({
      ...p,
      datasetIds: [...new Set([...(p.datasetIds ?? []), dataset.id])],
      charts: [...(p.charts ?? []), defaultBarChart(dataset, columns)],
    }));
  };
  const changeChart = (spec: ChartSpec) =>
    update((p) => ({ ...p, charts: (p.charts ?? []).map((c) => (c.id === spec.id ? spec : c)) }));

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-line px-4 py-2">
        <h1 className="text-base font-semibold">Visualize</h1>
        <span className="truncate text-sm text-muted">
          {project ? project.name : session.isError ? "Couldn't load project" : "Loading…"}
          {dirty ? " · unsaved changes" : ""}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button type="button" className={buttonClass} onClick={undo} disabled={!canUndo}>
            Undo
          </button>
          <button type="button" className={buttonClass} onClick={redo} disabled={!canRedo}>
            Redo
          </button>
          <button
            type="button"
            className={buttonClass}
            onClick={() => save.mutate()}
            disabled={!project || !dirty || save.isPending}
          >
            {save.isPending ? "Saving…" : "Save"}
          </button>
          <ThemeSwitch />
        </div>
      </header>
      {save.error ? (
        <p role="alert" className="border-b border-line px-4 py-1 text-sm text-danger">
          {save.error.message}
        </p>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="flex flex-col gap-6 overflow-y-auto border-b border-line bg-surface p-4 md:border-r md:border-b-0">
          <ImportPanel onImported={(d) => setSelectedId(d.id)} />
          <DatasetList datasets={allDatasets} selectedId={activeId} onSelect={setSelectedId} />
        </aside>

        <main className="flex min-h-0 min-w-0 flex-col gap-3 p-4">
          {dataset ? (
            <>
              <section className="min-h-[200px] flex-[2]" aria-label="Data preview">
                <DataGrid dataset={dataset} />
              </section>
              <section
                className="flex min-h-[260px] flex-[3] flex-col gap-2 rounded-md border border-line p-3"
                aria-label="Chart"
              >
                {chart ? (
                  <>
                    <ChartBuilder spec={chart} columns={columns} onChange={changeChart} />
                    <div className="min-h-0 flex-1">
                      <ChartView spec={chart} />
                    </div>
                  </>
                ) : (
                  <div className="flex flex-1 items-center justify-center">
                    <button
                      type="button"
                      className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg"
                      onClick={addChart}
                      disabled={!project}
                    >
                      Create a bar chart
                    </button>
                  </div>
                )}
              </section>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center text-sm text-muted">
              Import a CSV, TSV or JSON file to get started.
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
