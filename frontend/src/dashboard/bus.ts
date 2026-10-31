/**
 * The cross-filter bus, as pure functions over plain state (the zustand store in
 * `runtimeStore.ts` just holds a `BusState`).
 *
 * Flow: a chart interaction produces Predicates -> `publish(group, widget, ...)` replaces that
 * widget's slot -> every other widget in the group recomputes `filtersForChart`, which merges
 * global filters, dashboard filters, pinned drill predicates and the group's selections minus
 * the chart's own. A chart never filters itself, so the marks you clicked stay visible.
 */
import type { ChartSpec, Predicate } from "../api/types";
import type { Dashboard, DashboardFilter } from "./types";

export interface Selection {
  /** Id of the dataset or pipeline of the chart that made the selection. */
  sourceId: string;
  predicates: Predicate[];
}

/** groupId -> widget id -> that widget's current selection. */
export type BusState = Record<string, Record<string, Selection>>;

export const EMPTY_BUS: BusState = {};

/**
 * The link group of a chart: the dashboard link group listing its chart id wins, else the
 * chart spec's own `interactions.linkGroup` (an implicit group of that name), else none.
 */
export function groupOf(dashboard: Dashboard | undefined, spec: ChartSpec): string | null {
  const listed = dashboard?.linkGroups?.find((g) => g.chartIds?.includes(spec.id));
  if (listed) return listed.id;
  return spec.interactions?.linkGroup || null;
}

/** Replace one widget's selection in a group. An empty selection removes it. */
export function publish(
  state: BusState,
  groupId: string,
  widgetId: string,
  selection: Selection | null,
): BusState {
  const group = state[groupId] ?? {};
  const had = widgetId in group;
  if (!selection || selection.predicates.length === 0) {
    if (!had) return state;
    const { [widgetId]: _removed, ...rest } = group;
    const { [groupId]: _group, ...others } = state;
    return Object.keys(rest).length === 0 ? others : { ...others, [groupId]: rest };
  }
  return { ...state, [groupId]: { ...group, [widgetId]: selection } };
}

/** Drop one widget's selections from every group (the widget was removed or changed). */
export function forget(state: BusState, widgetId: string): BusState {
  let next = state;
  for (const groupId of Object.keys(state)) next = publish(next, groupId, widgetId, null);
  return next;
}

export function selectionOf(
  state: BusState,
  groupId: string | null,
  widgetId: string,
): Predicate[] {
  return (groupId && state[groupId]?.[widgetId]?.predicates) || [];
}

export function hasSelections(state: BusState): boolean {
  return Object.keys(state).length > 0;
}

/** Everything the filters of one chart are computed from. */
export interface FilterInputs {
  dashboard: Dashboard | undefined;
  spec: ChartSpec;
  widgetId: string;
  bus: BusState;
  /** Live dashboard filter values, by filter id. Missing = use the saved predicate. */
  filterValues: Record<string, Predicate | null>;
  /** Project-wide filters (e.g. a date range across dashboards), live values by filter id. */
  globalFilters: DashboardFilter[];
  globalValues: Record<string, Predicate | null>;
  /** Drill-through predicates pinned when the dashboard was opened from another view. */
  drill: Predicate[];
  /** Known columns of a source, or undefined when not known (a pipeline not yet inspected). */
  columnsOf: (sourceId: string) => ReadonlySet<string> | undefined;
}

function withField(p: Predicate, field: string): Predicate {
  return p.field === field ? p : ({ ...p, field } as Predicate);
}

/**
 * The predicate of a dashboard/global filter as it applies to a chart's source: the field is
 * renamed through `fieldMap`, and the filter is skipped where the source is known to lack it.
 */
function applyFilter(
  filter: DashboardFilter,
  live: Predicate | null | undefined,
  spec: ChartSpec,
  columnsOf: FilterInputs["columnsOf"],
): Predicate | null {
  const predicate = live === undefined ? (filter.predicate ?? null) : live;
  if (!predicate) return null;
  const field = filter.fieldMap?.[spec.source.id] ?? filter.field;
  const columns = columnsOf(spec.source.id);
  if (columns && !columns.has(field)) return null;
  return withField(predicate, field);
}

/**
 * The `filters` array for one chart's data request. The chart's own selection is excluded;
 * selections from other sources apply only where the target is known to have the field.
 */
export function filtersForChart(input: FilterInputs): Predicate[] {
  const { dashboard, spec, widgetId, bus, columnsOf } = input;
  const out: Predicate[] = [];

  for (const f of input.globalFilters) {
    const p = applyFilter(f, input.globalValues[f.id], spec, columnsOf);
    if (p) out.push(p);
  }
  for (const f of dashboard?.filters ?? []) {
    const p = applyFilter(f, input.filterValues[f.id], spec, columnsOf);
    if (p) out.push(p);
  }

  const columns = columnsOf(spec.source.id);
  const applicable = (p: Predicate) => !columns || columns.has(p.field);
  for (const p of input.drill) if (applicable(p)) out.push(p);

  const group = groupOf(dashboard, spec);
  const selections = group ? (bus[group] ?? {}) : {};
  for (const id of Object.keys(selections).sort()) {
    if (id === widgetId) continue; // a chart never filters itself
    const selection = selections[id];
    if (!selection) continue;
    const sameSource = selection.sourceId === spec.source.id;
    for (const p of selection.predicates) {
      // Different source: only where its columns are known to include the field.
      if (sameSource ? applicable(p) : columns?.has(p.field)) out.push(p);
    }
  }
  return out;
}
