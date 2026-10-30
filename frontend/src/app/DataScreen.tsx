import { DataGrid } from "../data/DataGrid";
import { useActiveDataset } from "../data/useActiveDataset";

export default function DataScreen() {
  const { dataset, loading } = useActiveDataset();
  if (!dataset) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted">
        {loading ? "Loading…" : "Import a CSV, TSV, JSON or Excel file to get started."}
      </div>
    );
  }
  return (
    <section className="min-h-0 flex-1" aria-label="Data preview">
      <DataGrid key={dataset.id} dataset={dataset} />
    </section>
  );
}
