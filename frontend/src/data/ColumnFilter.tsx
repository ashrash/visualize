import { useQuery } from "@tanstack/react-query";
import { Filter } from "lucide-react";
import { useEffect, useState } from "react";
import type { Column, ColumnValues, Predicate } from "../api/types";
import { Button } from "../components/ui/button";
import { Field, Input, Select } from "../components/ui/input";
import { Popover, PopoverClose, PopoverContent, PopoverTrigger } from "../components/ui/popover";
import {
  draftFromPredicate,
  emptyDraft,
  type FilterDraft,
  type FilterMode,
  isRangeColumn,
  predicateFromDraft,
  type Scalar,
} from "./filters";

export type LoadValues = (search: string, signal: AbortSignal) => Promise<ColumnValues>;

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

const label = (v: Scalar) => (v === null ? "(empty)" : String(v));

function ValuesChecklist({
  column,
  selected,
  onChange,
  loadValues,
}: {
  column: Column;
  selected: Scalar[];
  onChange: (values: Scalar[]) => void;
  loadValues: LoadValues;
}) {
  const [search, setSearch] = useState("");
  const term = useDebounced(search, 250);
  const query = useQuery({
    queryKey: ["column-values", column.name, term],
    queryFn: ({ signal }) => loadValues(term, signal),
  });
  const toggle = (value: Scalar, on: boolean) =>
    onChange(on ? [...selected, value] : selected.filter((v) => v !== value));
  return (
    <div className="flex flex-col gap-2">
      <Field label="Search values">
        {(p) => <Input {...p} value={search} onChange={(e) => setSearch(e.target.value)} />}
      </Field>
      <fieldset className="max-h-48 overflow-y-auto rounded-md border border-line p-1">
        <legend className="sr-only">Values of {column.name}</legend>
        {query.isPending ? <p className="p-1 text-xs text-muted">Loading…</p> : null}
        {query.isError ? (
          <p role="alert" className="p-1 text-xs text-danger">
            {query.error.message}
          </p>
        ) : null}
        {query.data?.values.map((v) => (
          <label key={String(v.value)} className="flex items-center gap-1.5 px-1 py-0.5 text-sm">
            <input
              type="checkbox"
              className="accent-accent"
              checked={selected.includes(v.value)}
              onChange={(e) => toggle(v.value, e.target.checked)}
            />
            <span className="min-w-0 flex-1 truncate">{label(v.value)}</span>
            <span className="text-xs text-muted">{v.count.toLocaleString()}</span>
          </label>
        ))}
        {query.data?.values.length === 0 ? (
          <p className="p-1 text-xs text-muted">No matching values</p>
        ) : null}
      </fieldset>
      {query.data?.truncated ? (
        <p className="text-xs text-muted">Showing the first values; search to narrow the list.</p>
      ) : null}
    </div>
  );
}

/** The filter editor itself (also rendered directly in tests). */
export function FilterBody({
  column,
  predicate,
  loadValues,
  onApply,
  onClose,
}: {
  column: Column;
  predicate?: Predicate;
  loadValues: LoadValues;
  onApply: (predicate: Predicate | null) => void;
  onClose?: () => void;
}) {
  const [draft, setDraft] = useState<FilterDraft>(() => draftFromPredicate(predicate, column));
  const range = isRangeColumn(column);
  const modes: { value: FilterMode; label: string }[] = [
    range ? { value: "range", label: "Range" } : { value: "values", label: "Pick values" },
    { value: "null", label: "Empty or not" },
  ];
  const apply = (next: Predicate | null) => {
    onApply(next);
    onClose?.();
  };
  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        apply(predicateFromDraft(column, draft));
      }}
    >
      <Field label={`Filter ${column.name}`}>
        {(p) => (
          <Select
            {...p}
            value={draft.mode}
            options={modes}
            onChange={(e) => setDraft(emptyDraft(e.target.value as FilterMode))}
          />
        )}
      </Field>
      {draft.mode === "values" ? (
        <ValuesChecklist
          column={column}
          selected={draft.values}
          loadValues={loadValues}
          onChange={(values) => setDraft({ mode: "values", values })}
        />
      ) : null}
      {draft.mode === "range" ? (
        <div className="grid grid-cols-2 gap-2">
          {(["min", "max"] as const).map((bound) => (
            <Field key={bound} label={bound === "min" ? "From" : "To"}>
              {(p) => (
                <Input
                  {...p}
                  type={column.semantic === "temporal" ? "date" : "number"}
                  value={draft[bound]}
                  onChange={(e) => setDraft({ ...draft, [bound]: e.target.value })}
                />
              )}
            </Field>
          ))}
        </div>
      ) : null}
      {draft.mode === "null" ? (
        <Field label="Keep rows that are">
          {(p) => (
            <Select
              {...p}
              value={draft.isNull ? "null" : "notnull"}
              options={[
                { value: "null", label: "empty" },
                { value: "notnull", label: "not empty" },
              ]}
              onChange={(e) => setDraft({ mode: "null", isNull: e.target.value === "null" })}
            />
          )}
        </Field>
      ) : null}
      <div className="flex justify-between gap-2">
        <Button size="sm" onClick={() => apply(null)}>
          Clear
        </Button>
        <Button size="sm" variant="primary" type="submit">
          Apply
        </Button>
      </div>
    </form>
  );
}

/** Header button + popover. */
export function ColumnFilter({
  column,
  predicate,
  loadValues,
  onApply,
}: {
  column: Column;
  predicate?: Predicate;
  loadValues: LoadValues;
  onApply: (predicate: Predicate | null) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Filter ${column.name}`}
          className={`shrink-0 rounded p-0.5 hover:bg-surface-2 ${predicate ? "text-accent" : "text-muted"}`}
        >
          <Filter size={13} aria-hidden="true" fill={predicate ? "currentColor" : "none"} />
        </button>
      </PopoverTrigger>
      <PopoverContent>
        <FilterBody
          column={column}
          predicate={predicate}
          loadValues={loadValues}
          onApply={onApply}
          onClose={() => setOpen(false)}
        />
        <PopoverClose className="sr-only">Close</PopoverClose>
      </PopoverContent>
    </Popover>
  );
}
