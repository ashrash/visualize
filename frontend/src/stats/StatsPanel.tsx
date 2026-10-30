import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import type { Column, DescribeRequest, FieldSummary } from "../api/types";
import { Button } from "../components/ui/button";
import { Field, Input, Select } from "../components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { fieldToColumn } from "../data/pipeline/schema";
import { usePipelineInspect } from "../data/pipeline/usePipelineApi";
import { useActiveDataset } from "../data/useActiveDataset";
import { useProjectStore } from "../state/projectStore";
import { useSelection } from "../state/selection";
import { DescribeTable } from "./DescribeTable";
import { FieldChecklist } from "./FieldChecklist";
import { summariesOf } from "./fieldSummaries";
import { plotFromSummary } from "./plotFromSummary";
import { TestsPanel } from "./TestsPanel";
import { useDescribe } from "./useDescribe";

/** Parse a comma-separated list of percentiles ("5, 50, 95" or "0.05, 0.5, 0.95") into [0, 1]. */
function parsePercentiles(text: string): number[] {
  const values = text
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map(Number)
    .filter((n) => Number.isFinite(n))
    .map((n) => n / 100)
    .filter((n) => n >= 0 && n <= 1);
  return [...new Set(values)].sort((a, b) => a - b).slice(0, 20);
}

export function StatsPanel() {
  const navigate = useNavigate();
  const { dataset, datasets, columns: datasetColumns, loading } = useActiveDataset();
  const project = useProjectStore((s) => s.project);
  const update = useProjectStore((s) => s.update);
  const selectChart = useSelection((s) => s.selectChart);
  const pipelines = project?.pipelines ?? [];

  const [sourceChoice, setSourceChoice] = useState<string | null>(null);
  const choice = sourceChoice ?? (dataset ? `dataset:${dataset.id}` : "");
  const [kind, sourceId] = choice.split(":") as ["dataset" | "pipeline", string];
  const pipeline = kind === "pipeline" ? pipelines.find((p) => p.id === sourceId) : undefined;
  const inspect = usePipelineInspect(pipeline, pipelines).data;
  const columns: Column[] = useMemo(() => {
    if (kind === "pipeline") return inspect ? inspect.columns.map(fieldToColumn) : [];
    return datasets.find((d) => d.id === sourceId)?.versions?.at(-1)?.columns ?? datasetColumns;
  }, [kind, sourceId, inspect, datasets, datasetColumns]);

  const [fields, setFields] = useState<string[]>([]);
  const [groupBy, setGroupBy] = useState<string[]>([]);
  const [percentilesText, setPercentilesText] = useState("5, 95");
  const [includeOverall, setIncludeOverall] = useState(true);
  const [includeMode, setIncludeMode] = useState(true);
  // Set only by "Run", so edits to the form above don't refetch until asked.
  const [request, setRequest] = useState<DescribeRequest | undefined>(undefined);

  const source =
    kind === "pipeline"
      ? pipeline
        ? { kind: "pipeline" as const, id: pipeline.id }
        : undefined
      : sourceId
        ? { kind: "dataset" as const, id: sourceId }
        : undefined;
  const canRun = source !== undefined && fields.length > 0;

  const run = () => {
    if (!canRun || !source) return;
    setRequest({
      source,
      pipelines,
      fields,
      groupBy,
      percentiles: parsePercentiles(percentilesText),
      includeOverall,
      includeMode,
      maxGroups: 50,
    });
  };

  const describe = useDescribe(request);
  const summaries = summariesOf(describe.data);

  const plotThis = (summary: FieldSummary) => {
    // Use the request's own source and groupBy - the ones the shown results actually came from -
    // not the live picker state, which may have moved on to a different source since Run.
    if (!request || !project) return;
    const spec = plotFromSummary(summary, request.source, request.groupBy);
    update((p) => ({
      ...p,
      datasetIds:
        request.source.kind === "dataset"
          ? [...new Set([...(p.datasetIds ?? []), request.source.id])]
          : p.datasetIds,
      charts: [...(p.charts ?? []), spec],
    }));
    selectChart(spec.id);
    navigate("/charts");
  };

  if (!dataset && datasets.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted">
        {loading ? "Loading…" : "Import a dataset to analyze it."}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <h2 className="text-base font-semibold">Statistics</h2>
      <Tabs defaultValue="describe">
        <TabsList aria-label="Statistics tools">
          <TabsTrigger value="describe">Descriptive statistics</TabsTrigger>
          <TabsTrigger value="tests">Tests (coming soon)</TabsTrigger>
        </TabsList>
        <TabsContent value="describe" className="min-h-0 flex-1">
          <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[280px_minmax(0,1fr)]">
            <form
              className="flex flex-col gap-3 rounded-md border border-line p-3"
              aria-label="Descriptive statistics settings"
              onSubmit={(e) => {
                e.preventDefault();
                run();
              }}
            >
              <Field label="Source">
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
                    onChange={(e) => {
                      setSourceChoice(e.target.value);
                      setFields([]);
                      setGroupBy([]);
                    }}
                  />
                )}
              </Field>
              <FieldChecklist
                legend="Fields (up to 50)"
                columns={columns}
                selected={fields}
                onChange={setFields}
                max={50}
              />
              <FieldChecklist
                legend="Group by (up to 2, optional)"
                columns={columns}
                selected={groupBy}
                onChange={setGroupBy}
                max={2}
              />
              <Field label="Percentiles (%)" hint="Comma-separated, e.g. 5, 50, 95">
                {(p) => (
                  <Input
                    {...p}
                    value={percentilesText}
                    onChange={(e) => setPercentilesText(e.target.value)}
                  />
                )}
              </Field>
              <label className="flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  className="accent-accent"
                  checked={includeOverall}
                  onChange={(e) => setIncludeOverall(e.target.checked)}
                />
                Include an overall row
              </label>
              <label className="flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  className="accent-accent"
                  checked={includeMode}
                  onChange={(e) => setIncludeMode(e.target.checked)}
                />
                Include the mode
              </label>
              <Button type="submit" variant="primary" disabled={!canRun}>
                Run
              </Button>
            </form>
            <div className="min-h-0 flex-1 overflow-auto" aria-live="polite">
              {!request ? (
                <p className="text-sm text-muted">
                  Pick a source and at least one field, then Run.
                </p>
              ) : describe.isPending ? (
                <p className="text-sm text-muted">Computing…</p>
              ) : describe.isError ? (
                <p role="alert" className="text-sm text-danger">
                  {describe.error.message}
                </p>
              ) : describe.data ? (
                <div className="flex flex-col gap-2">
                  <p className="text-xs text-muted">
                    {describe.data.method} · n = {describe.data.n.toLocaleString()}
                    {describe.data.sampled ? " · sampled" : ""}
                  </p>
                  <DescribeTable summaries={summaries} onPlot={plotThis} />
                  {(describe.data.warnings ?? []).length > 0 ? (
                    <ul className="list-disc pl-4 text-xs text-muted">
                      {(describe.data.warnings ?? []).map((w) => (
                        <li key={w}>{w}</li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        </TabsContent>
        <TabsContent value="tests" className="min-h-0 flex-1">
          <TestsPanel source={source} pipelines={pipelines} columns={columns} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
