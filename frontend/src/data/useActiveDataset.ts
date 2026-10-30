import { useMemo } from "react";
import type { Column, Dataset } from "../api/types";
import { useProjectStore } from "../state/projectStore";
import { useSelection } from "../state/selection";
import { useDatasets } from "./hooks";

/** The dataset the user is working on: explicit selection, else the first chart's, else first. */
export function useActiveDataset(): {
  dataset: Dataset | undefined;
  datasets: Dataset[];
  columns: Column[];
  loading: boolean;
} {
  const query = useDatasets();
  const project = useProjectStore((s) => s.project);
  const selectedId = useSelection((s) => s.datasetId);
  const datasets = useMemo(() => query.data ?? [], [query.data]);
  // Until the project loads we don't know which dataset its chart uses, so don't pick one yet
  // (picking the first dataset would fetch rows only to cancel them a moment later).
  // A chart may read a pipeline: its dataset is the pipeline's own source.
  const firstChart = project?.charts?.[0];
  const chartDataset =
    firstChart?.source.kind === "pipeline"
      ? project?.pipelines?.find((p) => p.id === firstChart.source.id)?.source.id
      : firstChart?.source.id;
  const defaultId = project ? (chartDataset ?? datasets[0]?.id) : undefined;
  const id = selectedId ?? defaultId;
  const dataset = datasets.find((d) => d.id === id);
  const columns = useMemo(() => dataset?.versions?.at(-1)?.columns ?? [], [dataset]);
  return { dataset, datasets, columns, loading: query.isPending || !project };
}
