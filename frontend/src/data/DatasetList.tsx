import type { Dataset } from "../api/types";
import { DatasetActions } from "./DatasetActions";

interface DatasetListProps {
  datasets: Dataset[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDeleted?: (id: string) => void;
}

export function DatasetList({ datasets, selectedId, onSelect, onDeleted }: DatasetListProps) {
  return (
    <section className="flex flex-col gap-1" aria-labelledby="datasets-heading">
      <h2
        id="datasets-heading"
        className="text-xs font-semibold uppercase tracking-wide text-muted"
      >
        Datasets
      </h2>
      {datasets.length === 0 ? (
        <p className="text-sm text-muted">No datasets yet. Import a file to start.</p>
      ) : (
        <ul className="flex flex-col gap-0.5">
          {datasets.map((dataset) => {
            const version = dataset.versions?.at(-1);
            const selected = dataset.id === selectedId;
            return (
              <li key={dataset.id} className="flex items-start gap-0.5">
                <button
                  type="button"
                  aria-current={selected ? "true" : undefined}
                  onClick={() => onSelect(dataset.id)}
                  className={`flex min-w-0 flex-1 flex-col rounded-md px-2 py-1 text-left text-sm ${
                    selected ? "bg-surface-2 font-medium text-fg" : "hover:bg-surface-2"
                  }`}
                >
                  <span className="block truncate">{dataset.name}</span>
                  {version ? (
                    <span className="block text-xs text-muted">
                      {`${version.rowCount.toLocaleString()} rows · ${version.columns.length} columns`}
                    </span>
                  ) : null}
                </button>
                <DatasetActions dataset={dataset} onDeleted={(id) => onDeleted?.(id)} />
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
