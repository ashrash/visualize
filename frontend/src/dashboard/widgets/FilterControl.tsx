import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchColumnValues } from "../../api/datasets";
import { describeFields } from "../../api/stats";
import type { FieldSummary, Predicate } from "../../api/types";
import { Input } from "../../components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "../../components/ui/popover";
import { BRUSH_DEBOUNCE_MS, debounce } from "../debounce";
import { useDashboardEnv } from "../env";
import {
  datasetOfSource,
  dateRangePredicate,
  dateRangeValue,
  membershipPredicate,
  membershipValue,
  rangePredicate,
  rangeValue,
} from "../filterControls";
import { useDashboardRuntime } from "../runtimeStore";
import type { FilterControl as Control, DashboardFilter } from "../types";

export type FilterScope = "dashboard" | "global";

/** The first chart source the filter's field is known to exist in (to ask for its values). */
function useFilterDataset(filter: DashboardFilter): string | undefined {
  const env = useDashboardEnv();
  for (const chart of env.charts) {
    const field = filter.fieldMap?.[chart.source.id] ?? filter.field;
    const columns = env.columnsOf(chart.source.id);
    if (!columns || columns.has(field)) {
      return datasetOfSource(chart.source, env.pipelines);
    }
  }
  return undefined;
}

/** Live value of a filter: the runtime value if set, else the saved default. */
export function useFilterValue(filter: DashboardFilter, scope: FilterScope): Predicate | null {
  const live = useDashboardRuntime((s) =>
    scope === "global" ? s.globalValues[filter.id] : s.filterValues[filter.id],
  );
  return live === undefined ? (filter.predicate ?? null) : live;
}

function useCommit(filter: DashboardFilter, scope: FilterScope, delayMs = BRUSH_DEBOUNCE_MS) {
  const setFilter = useDashboardRuntime((s) => s.setFilter);
  const setGlobal = useDashboardRuntime((s) => s.setGlobal);
  const set = scope === "global" ? setGlobal : setFilter;
  const id = filter.id;
  // Sliders and typed dates change many times in a row: commit once, after a pause.
  const debounced = useMemo(
    () => debounce((p: Predicate | null) => set(id, p), delayMs),
    [set, id, delayMs],
  );
  useEffect(() => () => debounced.flush(), [debounced]);
  return debounced;
}

function DateRangeControl({ filter, scope }: { filter: DashboardFilter; scope: FilterScope }) {
  const value = dateRangeValue(useFilterValue(filter, scope));
  const commit = useCommit(filter, scope);
  const [draft, setDraft] = useState(value);
  // Follow outside changes (reset, drill) but not our own in-progress typing.
  const lastCommitted = useRef(value);
  if (value.from !== lastCommitted.current.from || value.to !== lastCommitted.current.to) {
    lastCommitted.current = value;
    setDraft(value);
  }
  const change = (next: { from: string; to: string }) => {
    setDraft(next);
    lastCommitted.current = next;
    commit(dateRangePredicate(filter.field, next.from, next.to));
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Input
        type="date"
        aria-label={`${filter.label} from`}
        value={draft.from}
        max={draft.to || undefined}
        onChange={(e) => change({ ...draft, from: e.target.value })}
        className="w-36"
      />
      <span aria-hidden="true" className="text-muted">
        to
      </span>
      <Input
        type="date"
        aria-label={`${filter.label} to`}
        value={draft.to}
        min={draft.from || undefined}
        onChange={(e) => change({ ...draft, to: e.target.value })}
        className="w-36"
      />
    </div>
  );
}

function DropdownControl({ filter, scope }: { filter: DashboardFilter; scope: FilterScope }) {
  const datasetId = useFilterDataset(filter);
  const predicate = useFilterValue(filter, scope);
  const selected = membershipValue(predicate);
  const commit = useCommit(filter, scope, 0);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const values = useQuery({
    queryKey: ["filter-values", datasetId, filter.field, search],
    queryFn: ({ signal }) =>
      fetchColumnValues(
        datasetId as string,
        {
          field: filter.fieldMap?.[datasetId as string] ?? filter.field,
          search: search || null,
          limit: 100,
        },
        signal,
      ),
    enabled: open && datasetId !== undefined,
    placeholderData: keepPreviousData,
  });
  const toggle = (value: string | number | boolean | null) => {
    const next = selected.includes(value)
      ? selected.filter((v) => v !== value)
      : [...selected, value];
    commit(membershipPredicate(filter.field, next));
  };
  const label =
    selected.length === 0
      ? "All"
      : selected.length === 1
        ? String(selected[0] ?? "(empty)")
        : `${selected.length} selected`;
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        aria-label={`${filter.label} filter`}
        className="inline-flex h-8 min-w-32 items-center justify-between gap-2 rounded-md border border-line bg-bg px-2 text-sm text-fg hover:bg-surface focus-visible:outline-2 focus-visible:outline-accent"
      >
        <span className="truncate">{label}</span>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64">
        <Input
          type="search"
          aria-label={`Search ${filter.label} values`}
          placeholder="Search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <ul className="mt-2 max-h-56 overflow-auto">
          {(values.data?.values ?? []).map((v) => (
            <li key={String(v.value)}>
              <label className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-sm hover:bg-surface-2">
                <input
                  type="checkbox"
                  checked={selected.includes(v.value)}
                  onChange={() => toggle(v.value)}
                />
                <span className="flex-1 truncate">
                  {v.value === null ? "(empty)" : String(v.value)}
                </span>
                <span className="text-xs tabular-nums text-muted">{v.count.toLocaleString()}</span>
              </label>
            </li>
          ))}
        </ul>
        {values.data?.truncated ? (
          <p className="mt-1 text-xs text-muted">Showing the first 100 values. Search to narrow.</p>
        ) : null}
        {values.isError ? (
          <p role="alert" className="mt-1 text-xs text-danger">
            {values.error.message}
          </p>
        ) : null}
        {selected.length > 0 ? (
          <button
            type="button"
            onClick={() => commit(null)}
            className="mt-2 text-xs text-accent underline focus-visible:outline-2 focus-visible:outline-accent"
          >
            Clear
          </button>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function SliderControl({ filter, scope }: { filter: DashboardFilter; scope: FilterScope }) {
  const datasetId = useFilterDataset(filter);
  const env = useDashboardEnv();
  const predicate = useFilterValue(filter, scope);
  const commit = useCommit(filter, scope);
  const field = filter.fieldMap?.[datasetId ?? ""] ?? filter.field;
  // The slider's domain is the field's own min/max over the whole dataset (no filters).
  const domainQuery = useQuery({
    queryKey: ["filter-domain", datasetId, field],
    queryFn: ({ signal }) =>
      describeFields(
        {
          source: { kind: "dataset", id: datasetId as string },
          fields: [field],
          includeOverall: true,
          includeMode: false,
          percentiles: [],
          maxGroups: 1,
        },
        signal,
      ),
    enabled: datasetId !== undefined && env.charts.length > 0,
    staleTime: 5 * 60_000,
  });
  const summary = (domainQuery.data?.values as { summaries?: FieldSummary[] } | undefined)
    ?.summaries?.[0];
  const domain: [number, number] | null =
    typeof summary?.min === "number" && typeof summary.max === "number" && summary.min < summary.max
      ? [summary.min, summary.max]
      : null;
  const [lo, hi] = domain ? rangeValue(predicate, domain) : [0, 0];
  const [draft, setDraft] = useState<[number, number] | null>(null);
  const shown = draft ?? [lo, hi];
  if (!domain) {
    return (
      <p className="text-xs text-muted">
        {domainQuery.isError ? "No numeric range for this field." : "Loading range…"}
      </p>
    );
  }
  const step = (domain[1] - domain[0]) / 200;
  const change = (next: [number, number]) => {
    setDraft(next);
    commit(rangePredicate(filter.field, next[0], next[1], domain));
  };
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <input
          type="range"
          aria-label={`${filter.label} minimum`}
          min={domain[0]}
          max={domain[1]}
          step={step}
          value={shown[0]}
          onChange={(e) => change([Math.min(Number(e.target.value), shown[1]), shown[1]])}
          onPointerUp={() => {
            commit.flush();
            setDraft(null);
          }}
          onKeyUp={() => {
            commit.flush();
            setDraft(null);
          }}
          className="w-32 accent-[var(--viz-accent,currentColor)]"
        />
        <input
          type="range"
          aria-label={`${filter.label} maximum`}
          min={domain[0]}
          max={domain[1]}
          step={step}
          value={shown[1]}
          onChange={(e) => change([shown[0], Math.max(Number(e.target.value), shown[0])])}
          onPointerUp={() => {
            commit.flush();
            setDraft(null);
          }}
          onKeyUp={() => {
            commit.flush();
            setDraft(null);
          }}
          className="w-32 accent-[var(--viz-accent,currentColor)]"
        />
      </div>
      <p className="text-xs tabular-nums text-muted">
        {`${shown[0].toLocaleString(undefined, { maximumFractionDigits: 2 })} to ${shown[1].toLocaleString(undefined, { maximumFractionDigits: 2 })}`}
      </p>
    </div>
  );
}

/** One filter's control (date range, dropdown or slider), writing live values to the runtime. */
export function FilterControl({
  filter,
  control,
  scope = "dashboard",
}: {
  filter: DashboardFilter;
  control: Control;
  scope?: FilterScope;
}) {
  switch (control) {
    case "date_range":
      return <DateRangeControl filter={filter} scope={scope} />;
    case "dropdown":
      return <DropdownControl filter={filter} scope={scope} />;
    case "slider":
      return <SliderControl filter={filter} scope={scope} />;
  }
}
