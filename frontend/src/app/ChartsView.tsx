import type { ChartSpec, Column, Dataset } from "../api/types";
import { ChartBuilder, ChartView } from "../charts";
import { Button } from "../components/ui/button";
import { useActiveDataset } from "../data/useActiveDataset";
import { useProjectStore } from "../state/projectStore";

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

export default function ChartsView() {
  const { dataset, columns, loading } = useActiveDataset();
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const chart = dataset ? project?.charts?.find((c) => c.source.id === dataset.id) : undefined;

  if (!dataset) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted">
        {loading ? "Loading…" : "Import a file to start charting."}
      </div>
    );
  }

  const addChart = () =>
    update((p) => ({
      ...p,
      datasetIds: [...new Set([...(p.datasetIds ?? []), dataset.id])],
      charts: [...(p.charts ?? []), defaultBarChart(dataset, columns)],
    }));
  const changeChart = (spec: ChartSpec) =>
    update((p) => ({ ...p, charts: (p.charts ?? []).map((c) => (c.id === spec.id ? spec : c)) }));

  return (
    <section
      className="flex min-h-[260px] flex-1 flex-col gap-2 rounded-md border border-line p-3"
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
          <Button variant="primary" onClick={addChart} disabled={!project}>
            Create a bar chart
          </Button>
        </div>
      )}
    </section>
  );
}
