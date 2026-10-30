import { Plus, X } from "lucide-react";
import type { ReactNode } from "react";
import type { Column } from "../../api/types";
import { Button } from "../../components/ui/button";
import { Field, Select, type SelectOption } from "../../components/ui/input";

export function columnOptions(columns: Column[], empty?: string): SelectOption[] {
  const options = columns.map((c) => ({ value: c.name, label: c.name }));
  return empty === undefined ? options : [{ value: "", label: empty }, ...options];
}

/** A column that no longer exists (renamed upstream) stays selectable so it can be seen and fixed. */
function withCurrent(options: SelectOption[], value: string): SelectOption[] {
  return value === "" || options.some((o) => o.value === value)
    ? options
    : [...options, { value, label: `${value} (missing)` }];
}

export function ColumnSelect({
  label,
  columns,
  value,
  onChange,
  empty,
  exclude,
}: {
  label: string;
  columns: Column[];
  value: string;
  onChange: (value: string) => void;
  empty?: string;
  /** Columns already taken elsewhere (e.g. other renames of the same column). */
  exclude?: string[];
}) {
  return (
    <Field label={label}>
      {(p) => (
        <Select
          {...p}
          value={value}
          options={withCurrent(
            columnOptions(
              exclude
                ? columns.filter((c) => c.name === value || !exclude.includes(c.name))
                : columns,
              empty,
            ),
            value,
          )}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
    </Field>
  );
}

/** Pick any number of columns; order follows the dataset unless `ordered` keeps click order. */
export function ColumnChecklist({
  legend,
  columns,
  value,
  onChange,
  ordered = false,
}: {
  legend: string;
  columns: Column[];
  value: string[];
  onChange: (value: string[]) => void;
  ordered?: boolean;
}) {
  const toggle = (name: string, checked: boolean) => {
    if (!checked) return onChange(value.filter((v) => v !== name));
    if (ordered) return onChange([...value, name]);
    const wanted = new Set([...value, name]);
    onChange(columns.map((c) => c.name).filter((n) => wanted.has(n)));
  };
  return (
    <fieldset className="flex min-w-0 flex-col gap-1">
      <legend className="mb-1 text-xs font-medium text-muted">{legend}</legend>
      <div className="grid max-h-40 grid-cols-1 gap-x-3 gap-y-0.5 overflow-y-auto rounded-md border border-line p-2 sm:grid-cols-2">
        {columns.map((c) => (
          <label key={c.name} className="flex min-w-0 items-center gap-1.5 text-sm">
            <input
              type="checkbox"
              className="accent-accent"
              checked={value.includes(c.name)}
              onChange={(e) => toggle(c.name, e.target.checked)}
            />
            <span className="truncate">{c.name}</span>
          </label>
        ))}
        {columns.length === 0 ? <span className="text-sm text-muted">No columns</span> : null}
      </div>
    </fieldset>
  );
}

/** A growable list of rows, each with a remove button. */
export function RowList<T>({
  legend,
  rows,
  addLabel,
  onAdd,
  onRemove,
  renderRow,
}: {
  legend: string;
  rows: T[];
  addLabel: string;
  onAdd: () => void;
  onRemove: (index: number) => void;
  renderRow: (row: T, index: number) => ReactNode;
}) {
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-xs font-medium text-muted">{legend}</legend>
      {rows.map((row, index) => (
        // Rows have no stable id; they're edited in place, so the index is the identity.
        // biome-ignore lint/suspicious/noArrayIndexKey: see above.
        <div key={index} className="flex items-end gap-2">
          <div className="grid min-w-0 flex-1 grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {renderRow(row, index)}
          </div>
          <Button
            size="icon"
            variant="ghost"
            aria-label={`Remove ${legend.toLowerCase()} ${index + 1}`}
            onClick={() => onRemove(index)}
          >
            <X size={14} aria-hidden="true" />
          </Button>
        </div>
      ))}
      <Button size="sm" className="self-start" onClick={onAdd}>
        <Plus size={14} aria-hidden="true" />
        {addLabel}
      </Button>
    </fieldset>
  );
}

export function replaceAt<T>(list: T[], index: number, value: T): T[] {
  return list.map((item, i) => (i === index ? value : item));
}

export function removeAt<T>(list: T[], index: number): T[] {
  return list.filter((_, i) => i !== index);
}
