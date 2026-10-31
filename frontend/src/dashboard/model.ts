/**
 * Pure edit helpers for the dashboard document (`Project.dashboards[]`). Each takes and returns
 * plain data so it can be passed to `projectStore.update` (one undo step per call).
 */
import { moveElement, verticalCompactor } from "react-grid-layout/core";
import type { Project } from "../api/types";
import type { Dashboard, DashboardFilter, DashboardTab, LayoutItem, Widget } from "./types";

export const GRID_COLUMNS = 12;

export const newId = (): string => crypto.randomUUID();

export function createDashboard(name = "Untitled dashboard"): Dashboard {
  return {
    id: newId(),
    name,
    tabs: [{ id: newId(), name: "Tab 1", items: [] }],
    filters: [],
    linkGroups: [],
  };
}

/** Default size of a new widget, in grid units. */
export function defaultSize(kind: Widget["kind"]): { w: number; h: number } {
  switch (kind) {
    case "kpi":
      return { w: 3, h: 3 };
    case "filter":
      return { w: 4, h: 2 };
    case "text":
      return { w: 4, h: 3 };
    case "image":
      return { w: 3, h: 4 };
    default:
      return { w: 6, h: 6 };
  }
}

/** First free row below everything already on the tab. */
export function bottomRow(items: LayoutItem[]): number {
  return items.reduce((max, item) => Math.max(max, item.y + item.h), 0);
}

export function addWidget(
  dashboard: Dashboard,
  tabId: string,
  widget: Widget,
  size = defaultSize(widget.kind),
): { dashboard: Dashboard; item: LayoutItem } {
  const tab = dashboard.tabs?.find((t) => t.id === tabId);
  if (!tab) throw new Error(`Unknown tab ${tabId}`);
  const items = tab.items ?? [];
  // Fill the right half of the last row before starting a new one.
  const last = items.at(-1);
  const fitsBeside =
    last !== undefined &&
    last.y + last.h === bottomRow(items) &&
    last.x + last.w + size.w <= GRID_COLUMNS;
  const item: LayoutItem = {
    i: newId(),
    x: fitsBeside && last ? last.x + last.w : 0,
    y: fitsBeside && last ? last.y : bottomRow(items),
    w: size.w,
    h: size.h,
    widget,
  };
  return { dashboard: mapTab(dashboard, tabId, (t) => ({ ...t, items: [...items, item] })), item };
}

function mapTab(dashboard: Dashboard, tabId: string, edit: (t: DashboardTab) => DashboardTab) {
  return { ...dashboard, tabs: (dashboard.tabs ?? []).map((t) => (t.id === tabId ? edit(t) : t)) };
}

export function removeWidget(dashboard: Dashboard, tabId: string, itemId: string): Dashboard {
  return mapTab(dashboard, tabId, (t) => ({
    ...t,
    items: (t.items ?? []).filter((item) => item.i !== itemId),
  }));
}

export function updateWidget(
  dashboard: Dashboard,
  tabId: string,
  itemId: string,
  widget: Widget,
): Dashboard {
  return mapTab(dashboard, tabId, (t) => ({
    ...t,
    items: (t.items ?? []).map((item) => (item.i === itemId ? { ...item, widget } : item)),
  }));
}

/** A layout as react-grid-layout reports it after a drag or resize. */
export interface GridPosition {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Smallest a widget can be resized to, in grid units (matches the grid's own minimum). */
export const MIN_WIDGET_SIZE = 2;

const ARROWS = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"] as const;
export const isArrowKey = (key: string): key is (typeof ARROWS)[number] =>
  (ARROWS as readonly string[]).includes(key);

/**
 * One keyboard step for a widget on the grid: an arrow moves it one cell, Shift+arrow resizes it
 * one cell (Right/Down grow, Left/Up shrink). Stays inside the grid and above the minimum size;
 * returns null when the step would not change anything (an edge was reached).
 */
export function nudge(
  pos: GridPosition,
  key: (typeof ARROWS)[number],
  resize: boolean,
  columns = GRID_COLUMNS,
): GridPosition | null {
  const next = { ...pos };
  if (resize) {
    if (key === "ArrowRight") next.w = Math.min(pos.w + 1, columns - pos.x);
    else if (key === "ArrowLeft") next.w = Math.max(pos.w - 1, MIN_WIDGET_SIZE);
    else if (key === "ArrowDown") next.h = pos.h + 1;
    else next.h = Math.max(pos.h - 1, MIN_WIDGET_SIZE);
  } else if (key === "ArrowRight") next.x = Math.min(pos.x + 1, columns - pos.w);
  else if (key === "ArrowLeft") next.x = Math.max(pos.x - 1, 0);
  else if (key === "ArrowDown") next.y = pos.y + 1;
  else next.y = Math.max(pos.y - 1, 0);
  return next.x === pos.x && next.y === pos.y && next.w === pos.w && next.h === pos.h ? null : next;
}

/**
 * The whole layout after one widget takes a new position or size: the others are pushed out of its
 * way and everything is compacted upwards, exactly as the grid itself settles a drag. Used for the
 * keyboard, so the stored layout already equals what the grid shows and nothing has to be read
 * back from the grid.
 */
export function settleLayout(
  layout: readonly GridPosition[],
  moved: GridPosition,
  columns = GRID_COLUMNS,
): GridPosition[] {
  const items = layout.map((p) => (p.i === moved.i ? { ...moved } : { ...p }));
  const mover = items.find((p) => p.i === moved.i);
  if (mover) {
    // The moved widget claims its place; whatever it overlaps is pushed down.
    moveElement(items, mover, moved.x, moved.y, true, false, "vertical", columns);
  }
  return verticalCompactor.compact(items, columns).map(({ i, x, y, w, h }) => ({ i, x, y, w, h }));
}

/** Apply a grid layout to the tab's items; the same object is returned when nothing moved. */
export function applyLayout(
  dashboard: Dashboard,
  tabId: string,
  layout: readonly GridPosition[],
): Dashboard {
  const tab = dashboard.tabs?.find((t) => t.id === tabId);
  if (!tab) return dashboard;
  const byId = new Map(layout.map((p) => [p.i, p]));
  let changed = false;
  const items = (tab.items ?? []).map((item) => {
    const p = byId.get(item.i);
    if (!p || (p.x === item.x && p.y === item.y && p.w === item.w && p.h === item.h)) return item;
    changed = true;
    return { ...item, x: p.x, y: p.y, w: p.w, h: p.h };
  });
  return changed ? mapTab(dashboard, tabId, (t) => ({ ...t, items })) : dashboard;
}

export function addTab(
  dashboard: Dashboard,
  name?: string,
): { dashboard: Dashboard; tab: DashboardTab } {
  const tabs = dashboard.tabs ?? [];
  const tab: DashboardTab = { id: newId(), name: name ?? `Tab ${tabs.length + 1}`, items: [] };
  return { dashboard: { ...dashboard, tabs: [...tabs, tab] }, tab };
}

export function renameTab(dashboard: Dashboard, tabId: string, name: string): Dashboard {
  return mapTab(dashboard, tabId, (t) => ({ ...t, name }));
}

/** Remove a tab; the last remaining tab cannot be removed. */
export function removeTab(dashboard: Dashboard, tabId: string): Dashboard {
  const tabs = dashboard.tabs ?? [];
  if (tabs.length <= 1) return dashboard;
  return { ...dashboard, tabs: tabs.filter((t) => t.id !== tabId) };
}

export function addFilter(dashboard: Dashboard, filter: DashboardFilter): Dashboard {
  return { ...dashboard, filters: [...(dashboard.filters ?? []), filter] };
}

export function updateFilter(dashboard: Dashboard, filter: DashboardFilter): Dashboard {
  return {
    ...dashboard,
    filters: (dashboard.filters ?? []).map((f) => (f.id === filter.id ? filter : f)),
  };
}

/** Remove a filter, and any filter widgets that showed it. */
export function removeFilter(dashboard: Dashboard, filterId: string): Dashboard {
  return {
    ...dashboard,
    filters: (dashboard.filters ?? []).filter((f) => f.id !== filterId),
    tabs: (dashboard.tabs ?? []).map((t) => ({
      ...t,
      items: (t.items ?? []).filter(
        (item) => !(item.widget.kind === "filter" && item.widget.filterId === filterId),
      ),
    })),
  };
}

/** Put a chart in a link group (creating it), or take it out when `groupId` is null. */
export function setChartLinkGroup(
  dashboard: Dashboard,
  chartId: string,
  groupId: string | null,
): Dashboard {
  const groups = (dashboard.linkGroups ?? [])
    .map((g) => ({ ...g, chartIds: (g.chartIds ?? []).filter((id) => id !== chartId) }))
    .filter((g) => g.chartIds.length > 0 || g.id === groupId);
  if (groupId === null) return { ...dashboard, linkGroups: groups };
  const existing = groups.find((g) => g.id === groupId);
  return {
    ...dashboard,
    linkGroups: existing
      ? groups.map((g) => (g === existing ? { ...g, chartIds: [...g.chartIds, chartId] } : g))
      : [...groups, { id: groupId, chartIds: [chartId] }],
  };
}

export function updateDashboard(
  project: Project,
  dashboardId: string,
  edit: (d: Dashboard) => Dashboard,
): Project {
  return {
    ...project,
    dashboards: (project.dashboards ?? []).map((d) => (d.id === dashboardId ? edit(d) : d)),
  };
}

export function addDashboardToProject(project: Project, dashboard: Dashboard): Project {
  return { ...project, dashboards: [...(project.dashboards ?? []), dashboard] };
}

export function removeDashboardFromProject(project: Project, dashboardId: string): Project {
  return { ...project, dashboards: (project.dashboards ?? []).filter((d) => d.id !== dashboardId) };
}

/** All chart widgets of a dashboard (every tab), as `{tabId, item}`. */
export function chartItems(dashboard: Dashboard): { tabId: string; item: LayoutItem }[] {
  return (dashboard.tabs ?? []).flatMap((tab) =>
    (tab.items ?? [])
      .filter((item) => item.widget.kind === "chart")
      .map((item) => ({ tabId: tab.id, item })),
  );
}

/** A copy with fresh ids for the dashboard, tabs, widgets and filters (charts are shared). */
export function duplicateDashboard(source: Dashboard, name = `${source.name} copy`): Dashboard {
  const filterIds = new Map((source.filters ?? []).map((f) => [f.id, newId()]));
  return {
    ...source,
    id: newId(),
    name,
    filters: (source.filters ?? []).map((f) => ({ ...f, id: filterIds.get(f.id) ?? newId() })),
    tabs: (source.tabs ?? []).map((tab) => ({
      ...tab,
      id: newId(),
      items: (tab.items ?? []).map((item) => ({
        ...item,
        i: newId(),
        widget:
          item.widget.kind === "filter"
            ? {
                ...item.widget,
                filterId: filterIds.get(item.widget.filterId) ?? item.widget.filterId,
              }
            : item.widget,
      })),
    })),
  };
}
