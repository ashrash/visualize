import type { Column } from "../api/types";

/** A labelled, keyboard-reachable checkbox list of column names, with an optional cap. */
export function FieldChecklist({
  legend,
  columns,
  selected,
  onChange,
  max,
  hint,
}: {
  legend: string;
  columns: Column[];
  selected: string[];
  onChange: (fields: string[]) => void;
  max?: number;
  hint?: string;
}) {
  const atMax = max !== undefined && selected.length >= max;
  const toggle = (name: string, on: boolean) =>
    onChange(on ? [...selected, name] : selected.filter((f) => f !== name));
  return (
    <fieldset className="flex flex-col gap-1">
      <legend className="text-xs font-medium text-muted">{legend}</legend>
      <div className="flex max-h-40 flex-col overflow-y-auto rounded-md border border-line p-1">
        {columns.map((column) => {
          const checked = selected.includes(column.name);
          return (
            <label key={column.name} className="flex items-center gap-1.5 px-1 py-0.5 text-sm">
              <input
                type="checkbox"
                className="accent-accent"
                checked={checked}
                disabled={!checked && atMax}
                onChange={(e) => toggle(column.name, e.target.checked)}
              />
              <span className="min-w-0 flex-1 truncate">{column.name}</span>
              <span className="text-xs text-muted">{column.semantic}</span>
            </label>
          );
        })}
        {columns.length === 0 ? (
          <p className="p-1 text-xs text-muted">No columns available.</p>
        ) : null}
      </div>
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </fieldset>
  );
}
