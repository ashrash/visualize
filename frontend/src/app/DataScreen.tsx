import { useState } from "react";
import { useDashboardRuntime } from "../dashboard/runtimeStore";
import { DataGrid } from "../data/DataGrid";
import { drillFilters } from "../data/drillFilters";
import { useActiveDataset } from "../data/useActiveDataset";
import { useSelection } from "../state/selection";

export default function DataScreen() {
  const { dataset, loading } = useActiveDataset();
  // A dashboard drill-through leaves its predicates in the runtime store. Take them once on
  // mount, clearing the store so a refresh or a later visit doesn't replay them, and select the
  // drilled dataset. The grid applies them as its initial filters when it mounts.
  const [drill] = useState(() => {
    const pending = useDashboardRuntime.getState().gridDrill;
    if (pending) {
      useDashboardRuntime.getState().setGridDrill(null);
      useSelection.getState().selectDataset(pending.datasetId);
    }
    return pending;
  });

  if (!dataset) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted">
        {loading ? "Loading…" : "Import a CSV, TSV, JSON or Excel file to get started."}
      </div>
    );
  }
  const initialFilters =
    drill && drill.datasetId === dataset.id
      ? drillFilters(drill.predicates, dataset.versions?.at(-1)?.columns ?? [])
      : undefined;
  return (
    <section className="min-h-0 flex-1" aria-label="Data preview">
      <DataGrid key={dataset.id} dataset={dataset} initialFilters={initialFilters} />
    </section>
  );
}
