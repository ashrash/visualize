import { Copy, PanelRight, Pencil, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import type { ChartSpec, Column } from "../api/types";
import { ChartConfigPanel, ChartView, createChartSpec, getPlot } from "../charts";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent } from "../components/ui/dialog";
import { Field, Input, Select } from "../components/ui/input";
import { Tooltip } from "../components/ui/tooltip";
import { fieldToColumn } from "../data/pipeline/schema";
import { usePipelineInspect } from "../data/pipeline/usePipelineApi";
import { useActiveDataset } from "../data/useActiveDataset";
import { useProjectStore } from "../state/projectStore";
import { useSelection } from "../state/selection";

/** A first bar chart for some data: a low-cardinality category against a real measure. */
function defaultBarChart(name: string, source: ChartSpec["source"], columns: Column[]): ChartSpec {
  const category =
    columns.find((c) => c.semantic === "nominal" && c.approxDistinct <= 100) ??
    columns.find((c) => c.semantic === "nominal") ??
    columns[0];
  // Prefer a real measure over identifier columns such as order_id.
  const looksLikeId = (name: string) => /(^|[_\s-])id$/i.test(name);
  const measure = columns.find(
    (c) => c.semantic === "quantitative" && c.name !== category?.name && !looksLikeId(c.name),
  );
  const entry = getPlot("bar");
  if (!entry) throw new Error("The bar plot is not registered");
  return createChartSpec(entry, {
    id: crypto.randomUUID(),
    name: `${name} chart`,
    source,
    encodings: {
      x: [{ field: category?.name ?? null }],
      y: [measure ? { field: measure.name, agg: "sum" } : { agg: "count" }],
    },
  });
}

export default function ChartsView() {
  const { dataset, datasets, columns: datasetColumns, loading } = useActiveDataset();
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const selectedId = useSelection((s) => s.chartId);
  const selectChart = useSelection((s) => s.selectChart);
  const [panelOpen, setPanelOpen] = useState(false);
  const [dialog, setDialog] = useState<"rename" | "delete" | null>(null);
  const [draftName, setDraftName] = useState("");

  const charts = project?.charts ?? [];
  const pipelines = project?.pipelines ?? [];
  const chart =
    charts.find((c) => c.id === selectedId) ??
    charts.find((c) => c.source.id === dataset?.id) ??
    charts[0];

  // Columns of the chart's own data: a dataset's columns, or a pipeline's exact output columns
  // from the inspect endpoint.
  const chartPipeline =
    chart?.source.kind === "pipeline" ? pipelines.find((p) => p.id === chart.source.id) : undefined;
  const chartInspect = usePipelineInspect(chartPipeline, pipelines).data;
  const columns = useMemo(() => {
    if (!chart) return datasetColumns;
    if (chart.source.kind === "pipeline") {
      return chartInspect ? chartInspect.columns.map(fieldToColumn) : [];
    }
    return (
      datasets.find((d) => d.id === chart.source.id)?.versions?.at(-1)?.columns ?? datasetColumns
    );
  }, [chart, chartInspect, datasets, datasetColumns]);

  // What a new chart will draw: the active dataset, or any pipeline of the project.
  const [sourceChoice, setSourceChoice] = useState<string | null>(null);
  const choice = sourceChoice ?? (dataset ? `dataset:${dataset.id}` : "");
  const [kind, sourceId] = choice.split(":") as ["dataset" | "pipeline", string];
  const newPipeline = kind === "pipeline" ? pipelines.find((p) => p.id === sourceId) : undefined;
  const newInspect = usePipelineInspect(newPipeline, pipelines).data;

  if (!dataset && charts.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted">
        {loading ? "Loading…" : "Import a file to start charting."}
      </div>
    );
  }

  const edit = (change: (charts: ChartSpec[]) => ChartSpec[]) =>
    update((p) => ({ ...p, charts: change(p.charts ?? []) }));

  const canCreate =
    kind === "pipeline"
      ? newPipeline !== undefined && newInspect !== undefined
      : dataset !== undefined;
  const create = () => {
    let spec: ChartSpec;
    if (kind === "pipeline" && newPipeline && newInspect) {
      spec = defaultBarChart(
        newPipeline.name,
        { kind: "pipeline", id: newPipeline.id },
        newInspect.columns.map(fieldToColumn),
      );
    } else if (dataset) {
      spec = defaultBarChart(dataset.name, { kind: "dataset", id: dataset.id }, datasetColumns);
    } else return;
    update((p) => ({
      ...p,
      datasetIds: dataset ? [...new Set([...(p.datasetIds ?? []), dataset.id])] : p.datasetIds,
      charts: [...(p.charts ?? []), spec],
    }));
    selectChart(spec.id);
  };
  const change = (spec: ChartSpec) => edit((all) => all.map((c) => (c.id === spec.id ? spec : c)));
  const rename = (name: string) => chart && change({ ...chart, name });
  const duplicate = () => {
    if (!chart) return;
    const copy: ChartSpec = {
      ...structuredClone(chart),
      id: crypto.randomUUID(),
      name: `${chart.name} copy`,
    };
    edit((all) => {
      const at = all.findIndex((c) => c.id === chart.id);
      return [...all.slice(0, at + 1), copy, ...all.slice(at + 1)];
    });
    selectChart(copy.id);
  };
  const remove = () => {
    if (!chart) return;
    const at = charts.findIndex((c) => c.id === chart.id);
    const next = charts[at + 1] ?? charts[at - 1];
    edit((all) => all.filter((c) => c.id !== chart.id));
    selectChart(next?.id ?? null);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex flex-wrap items-end gap-2">
        {charts.length > 0 ? (
          <Field label="Chart" className="w-56">
            {(p) => (
              <Select
                {...p}
                value={chart?.id ?? ""}
                options={charts.map((c) => ({ value: c.id, label: c.name }))}
                onChange={(e) => selectChart(e.target.value)}
              />
            )}
          </Field>
        ) : null}
        <Field label="New chart from" className="w-56">
          {(p) => (
            <Select
              {...p}
              value={choice}
              options={[
                ...datasets.map((d) => ({ value: `dataset:${d.id}`, label: d.name })),
                ...pipelines.map((x) => ({
                  value: `pipeline:${x.id}`,
                  label: `Pipeline: ${x.name}`,
                })),
              ]}
              onChange={(e) => setSourceChoice(e.target.value)}
            />
          )}
        </Field>
        <Button variant="primary" onClick={create} disabled={!project || !canCreate}>
          <Plus size={14} aria-hidden="true" />
          New chart
        </Button>
        {chart ? (
          <>
            <Tooltip content="Rename chart">
              <Button
                size="icon"
                variant="ghost"
                aria-label="Rename chart"
                onClick={() => {
                  setDraftName(chart.name);
                  setDialog("rename");
                }}
              >
                <Pencil size={14} aria-hidden="true" />
              </Button>
            </Tooltip>
            <Tooltip content="Duplicate chart">
              <Button size="icon" variant="ghost" aria-label="Duplicate chart" onClick={duplicate}>
                <Copy size={14} aria-hidden="true" />
              </Button>
            </Tooltip>
            <Tooltip content="Delete chart">
              <Button
                size="icon"
                variant="ghost"
                aria-label="Delete chart"
                onClick={() => setDialog("delete")}
              >
                <Trash2 size={14} aria-hidden="true" />
              </Button>
            </Tooltip>
            <Button
              className="ml-auto lg:hidden"
              aria-expanded={panelOpen}
              aria-controls="chart-settings"
              onClick={() => setPanelOpen((open) => !open)}
            >
              <PanelRight size={14} aria-hidden="true" />
              Chart settings
            </Button>
          </>
        ) : null}
      </div>

      {chart ? (
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_340px]">
          <section
            className="flex min-h-[320px] min-w-0 flex-col rounded-md border border-line p-3"
            aria-label="Chart"
          >
            <div className="min-h-0 flex-1">
              <ChartView key={chart.id} spec={chart} pipelines={pipelines} />
            </div>
          </section>
          <aside
            id="chart-settings"
            aria-label="Chart settings"
            className={`min-h-0 overflow-y-auto rounded-md border border-line p-3 ${panelOpen ? "block" : "hidden lg:block"}`}
          >
            <ChartConfigPanel
              spec={chart}
              columns={columns}
              onChange={change}
              templates={project?.templates ?? []}
              onSaveTemplate={(name, style) =>
                update((p) => ({
                  ...p,
                  templates: [...(p.templates ?? []), { id: crypto.randomUUID(), name, style }],
                }))
              }
            />
          </aside>
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-muted">
          No charts yet. Create one to get started.
        </div>
      )}

      <Dialog open={dialog === "rename"} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent title="Rename chart">
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (draftName.trim()) rename(draftName.trim());
              setDialog(null);
            }}
          >
            <Field label="Name">
              {(p) => (
                <Input {...p} value={draftName} onChange={(e) => setDraftName(e.target.value)} />
              )}
            </Field>
            <div className="flex justify-end gap-2">
              <Button onClick={() => setDialog(null)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={!draftName.trim()}>
                Rename
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
      <Dialog open={dialog === "delete"} onOpenChange={(open) => !open && setDialog(null)}>
        <DialogContent
          title={`Delete "${chart?.name ?? ""}"?`}
          description="You can undo this with Ctrl+Z."
        >
          <div className="flex justify-end gap-2">
            <Button onClick={() => setDialog(null)}>Cancel</Button>
            <Button
              variant="danger"
              onClick={() => {
                remove();
                setDialog(null);
              }}
            >
              Delete chart
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
