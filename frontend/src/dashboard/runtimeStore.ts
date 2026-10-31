/**
 * Live dashboard interaction state. Deliberately outside the project document: clicks, brushes
 * and filter drags must not become undo steps or autosaves (CLAUDE.md: the dashboard doc holds
 * the design; this holds what the viewer is doing right now).
 */
import { create } from "zustand";
import type { Predicate } from "../api/types";
import { type BusState, EMPTY_BUS, forget, pruneBus, publish, selectionOf } from "./bus";
import { extendSelection, sameSelection } from "./predicates";

export type InteractMode =
  /** Plain click or brush: replace this widget's selection; the same click again clears it. */
  | "toggle"
  /** Brush, legend: replace this widget's selection with exactly these predicates. */
  | "replace"
  /** Shift/ctrl-click: add to (or remove from) the current multi-selection. */
  | "extend";

interface RuntimeState {
  dashboardId: string | null;
  activeTabId: string | null;
  /** Cross-filter selections per link group. */
  bus: BusState;
  /** Live dashboard filter values by filter id (undefined = the saved predicate applies). */
  filterValues: Record<string, Predicate | null>;
  /** Live values of project-wide filters by filter id. */
  globalValues: Record<string, Predicate | null>;
  /** Predicates pinned by a drill-through into this dashboard. */
  drill: Predicate[];
  /** Open a dashboard: interaction state starts clean (optionally with drill predicates). */
  open: (dashboardId: string, options?: { drill?: Predicate[]; tabId?: string | null }) => void;
  setTab: (tabId: string | null) => void;
  interact: (args: {
    groupId: string;
    widgetId: string;
    sourceId: string;
    predicates: Predicate[];
    mode: InteractMode;
  }) => void;
  clearSelection: (groupId: string, widgetId: string) => void;
  forgetWidget: (widgetId: string) => void;
  /** Drop selections of widgets that are not in `liveWidgetIds` (removed from the document). */
  prune: (liveWidgetIds: ReadonlySet<string>) => void;
  setFilter: (filterId: string, value: Predicate | null) => void;
  setGlobal: (filterId: string, value: Predicate | null) => void;
  setDrill: (predicates: Predicate[]) => void;
  /**
   * A drill-through to the data grid: the dataset and predicates to show. The predicates use the
   * field names of the chart's own source: for a pipeline source those can be calculated or
   * renamed fields that the underlying dataset does not have (TODO(ui-data): drop or translate
   * those when applying them to the grid, or open the pipeline's preview instead). TODO(ui-data): the
   * grid should read this on mount, apply the predicates and call `setGridDrill(null)`.
   */
  gridDrill: { datasetId: string; predicates: Predicate[] } | null;
  setGridDrill: (value: RuntimeState["gridDrill"]) => void;
  /** Back to the saved defaults: no selections, live filter values or drill predicates. */
  resetAll: () => void;
}

const clean = {
  bus: EMPTY_BUS,
  filterValues: {} as Record<string, Predicate | null>,
  drill: [] as Predicate[],
};

export const useDashboardRuntime = create<RuntimeState>((set) => ({
  dashboardId: null,
  activeTabId: null,
  ...clean,
  globalValues: {},
  gridDrill: null,
  open: (dashboardId, options = {}) =>
    set({ dashboardId, activeTabId: options.tabId ?? null, ...clean, drill: options.drill ?? [] }),
  setTab: (activeTabId) => set({ activeTabId }),
  interact: ({ groupId, widgetId, sourceId, predicates, mode }) =>
    set((state) => {
      const current = selectionOf(state.bus, groupId, widgetId);
      let next: Predicate[];
      if (mode === "extend") next = extendSelection(current, predicates);
      else if (mode === "toggle" && sameSelection(current, predicates)) next = [];
      else next = predicates;
      const bus = publish(state.bus, groupId, widgetId, { sourceId, predicates: next });
      return bus === state.bus ? state : { bus };
    }),
  clearSelection: (groupId, widgetId) =>
    set((state) => {
      const bus = publish(state.bus, groupId, widgetId, null);
      return bus === state.bus ? state : { bus };
    }),
  forgetWidget: (widgetId) =>
    set((state) => {
      const bus = forget(state.bus, widgetId);
      return bus === state.bus ? state : { bus };
    }),
  prune: (liveWidgetIds) =>
    set((state) => {
      const bus = pruneBus(state.bus, liveWidgetIds);
      return bus === state.bus ? state : { bus };
    }),
  setFilter: (filterId, value) =>
    set((state) => ({ filterValues: { ...state.filterValues, [filterId]: value } })),
  setGlobal: (filterId, value) =>
    set((state) => ({ globalValues: { ...state.globalValues, [filterId]: value } })),
  setDrill: (drill) => set({ drill }),
  resetAll: () => set({ ...clean }),
  setGridDrill: (gridDrill) => set({ gridDrill }),
}));
