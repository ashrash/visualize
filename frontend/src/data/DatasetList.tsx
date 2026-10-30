import type { Dataset } from "../api/types";

interface DatasetListProps {
  datasets: Dataset[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function DatasetList({ datasets, selectedId, onSelect }: DatasetListProps) {
  return (
    <section className="flex flex-col gap-1">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">Datasets</h2>
      {datasets.length === 0 ? (
        <p className="text-sm text-muted">No datasets yet. Import a file to start.</p>
      ) : (
        <ul className="flex flex-col gap-0.5">
          {datasets.map((dataset) => {
            const version = dataset.versions?.at(-1);
            const selected = dataset.id === selectedId;
            return (
              <li key={dataset.id}>
                <button
                  type="button"
                  aria-current={selected ? "true" : undefined}
                  onClick={() => onSelect(dataset.id)}
                  className={`flex w-full flex-col rounded-md px-2 py-1 text-left text-sm ${
                    selected ? "bg-surface-2 font-medium" : "hover:bg-surface-2"
                  }`}
                >
                  <span className="block truncate">{dataset.name}</span>
                  {version ? (
                    <span className="block text-xs text-muted">
                      {`${version.rowCount.toLocaleString()} rows · ${version.columns.length} columns`}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
