import { Image, Link2, Plus, RotateCcw, Save, Type } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChartSpec, Pipeline, Predicate } from "../api/types";
import { Button } from "../components/ui/button";
import { cn } from "../components/ui/cn";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { Input } from "../components/ui/input";
import { DashboardGrid } from "./DashboardGrid";
import { describePredicate } from "./describe";
import {
  type DashboardEnv,
  DashboardEnvProvider,
  type DashboardMode,
  useSourceColumns,
} from "./env";
import {
  addFilter,
  addTab,
  addWidget,
  applyLayout,
  chartItems,
  removeTab,
  removeWidget,
  renameTab,
  setChartLinkGroup,
} from "./model";
import { useDashboardRuntime } from "./runtimeStore";
import type { Dashboard, DashboardFilter, DrillTarget } from "./types";
import { type NewWidget, type NewWidgetKind, WidgetDialog } from "./WidgetDialog";
import { FilterControl, useFilterValue } from "./widgets/FilterControl";

export interface DashboardViewProps {
  dashboard: Dashboard;
  charts: ChartSpec[];
  pipelines: Pipeline[];
  /** Project-wide filters, shown next to the dashboard's own. */
  globalFilters?: DashboardFilter[];
  mode: DashboardMode;
  /** Edit the dashboard document (edit mode). One call is one undo step. */
  onChange?: (edit: (d: Dashboard) => Dashboard) => void;
  onDrillThrough?: (target: DrillTarget, predicates: Predicate[]) => void;
  otherDashboards?: { id: string; name: string }[];
  className?: string;
}

/** The control a filter shows in the toolbar: its own, or a date range for a date-like filter. */
const hasControl = (f: DashboardFilter) => f.control != null;

function FilterItem({
  filter,
  scope,
  onSaveDefault,
}: {
  filter: DashboardFilter;
  scope: "dashboard" | "global";
  onSaveDefault?: (filterId: string, predicate: Predicate | null) => void;
}) {
  const live = useFilterValue(filter, scope);
  const dirty = JSON.stringify(live) !== JSON.stringify(filter.predicate ?? null);
  if (!filter.control) return null;
  return (
    <div className="flex items-end gap-1">
      <div className="flex flex-col gap-0.5">
        <span className="text-xs font-medium text-muted">{filter.label}</span>
        <FilterControl filter={filter} control={filter.control} scope={scope} />
      </div>
      {onSaveDefault && dirty ? (
        <Button
          size="icon"
          variant="ghost"
          aria-label={`Save ${filter.label} as default`}
          title="Save as default"
          onClick={() => onSaveDefault(filter.id, live)}
        >
          <Save size={14} aria-hidden="true" />
        </Button>
      ) : null}
    </div>
  );
}

/** Everything a dashboard shows: tabs, filters, the grid; editable when `mode` is "edit". */
export function DashboardView({
  dashboard,
  charts,
  pipelines,
  globalFilters = [],
  mode,
  onChange,
  onDrillThrough,
  otherDashboards,
  className,
}: DashboardViewProps) {
  const editing = mode === "edit";
  const embed = mode === "embed";
  const columnsOf = useSourceColumns(charts, pipelines);
  const open = useDashboardRuntime((s) => s.open);
  const openId = useDashboardRuntime((s) => s.dashboardId);
  const activeTabId = useDashboardRuntime((s) => s.activeTabId);
  const setTab = useDashboardRuntime((s) => s.setTab);
  const bus = useDashboardRuntime((s) => s.bus);
  const drill = useDashboardRuntime((s) => s.drill);
  const setDrill = useDashboardRuntime((s) => s.setDrill);
  const resetAll = useDashboardRuntime((s) => s.resetAll);
  const clearSelection = useDashboardRuntime((s) => s.clearSelection);
  const prune = useDashboardRuntime((s) => s.prune);
  const [adding, setAdding] = useState<NewWidgetKind | null>(null);
  // The widget dialogs open from a menu item, so there is no trigger for them to give focus back to.
  const addButton = useRef<HTMLButtonElement>(null);

  // A dashboard opened without going through a drill-through starts clean.
  useEffect(() => {
    if (openId !== dashboard.id) open(dashboard.id);
  }, [dashboard.id, openId, open]);

  const tabs = dashboard.tabs ?? [];
  // A chart removed from the document stops filtering the others. Judged from the document, not
  // from what is mounted, so switching tabs keeps every chart's selection.
  const widgetKey = tabs.flatMap((t) => (t.items ?? []).map((i) => i.i)).join("|");
  useEffect(() => {
    prune(new Set(widgetKey === "" ? [] : widgetKey.split("|")));
  }, [widgetKey, prune]);
  const tab = tabs.find((t) => t.id === activeTabId) ?? tabs[0];

  const saveFilterDefault = useCallback(
    (filterId: string, predicate: Predicate | null) =>
      onChange?.((d) => ({
        ...d,
        filters: (d.filters ?? []).map((f) => (f.id === filterId ? { ...f, predicate } : f)),
      })),
    [onChange],
  );

  const env: DashboardEnv = useMemo(
    () => ({
      dashboard,
      charts,
      pipelines,
      globalFilters,
      mode,
      columnsOf,
      onDrillThrough,
      otherDashboards,
      onSaveFilterDefault: editing ? saveFilterDefault : undefined,
    }),
    [
      dashboard,
      charts,
      pipelines,
      globalFilters,
      mode,
      columnsOf,
      onDrillThrough,
      otherDashboards,
      editing,
      saveFilterDefault,
    ],
  );

  if (!tab) return <p className="p-4 text-sm text-muted">This dashboard has no tabs.</p>;

  const selections = Object.entries(bus).flatMap(([group, widgets]) =>
    Object.entries(widgets).flatMap(([widgetId, s]) =>
      s.predicates.map((p) => ({ group, widgetId, predicate: p })),
    ),
  );
  // A filter placed on the grid as a widget is not repeated in the toolbar.
  const placed = new Set(
    tabs.flatMap((t) =>
      (t.items ?? []).flatMap((i) => (i.widget.kind === "filter" ? [i.widget.filterId] : [])),
    ),
  );
  const filterItems = [
    ...globalFilters
      .filter((f) => hasControl(f) && !placed.has(f.id))
      .map((f) => ({ f, scope: "global" as const })),
    ...(dashboard.filters ?? [])
      .filter((f) => hasControl(f) && !placed.has(f.id))
      .map((f) => ({ f, scope: "dashboard" as const })),
  ];
  const anyActive = selections.length > 0 || drill.length > 0;

  const addNew = ({ widget, filter }: NewWidget) =>
    onChange?.((d) => {
      const withFilter = filter ? addFilter(d, filter) : d;
      return addWidget(withFilter, tab.id, widget).dashboard;
    });
  const linkAll = () =>
    onChange?.((d) =>
      chartItems(d).reduce((acc, { item }) => {
        const chartId = item.widget.kind === "chart" ? item.widget.chartId : "";
        return setChartLinkGroup(acc, chartId, "all");
      }, d),
    );
  const allLinked = chartItems(dashboard).every(
    ({ item }) =>
      item.widget.kind === "chart" &&
      dashboard.linkGroups?.some((g) =>
        g.chartIds?.includes((item.widget as { chartId: string }).chartId),
      ),
  );

  return (
    <DashboardEnvProvider value={env}>
      <div className={cn("dashboard flex flex-col gap-3", className)}>
        {!embed || filterItems.length > 0 ? (
          <div className="dashboard-chrome flex flex-wrap items-end gap-x-4 gap-y-2">
            {tabs.length > 1 || editing ? (
              <div className="flex flex-wrap items-center gap-1">
                {/* Only tabs inside the tablist (the ARIA content model); editing sits beside it. */}
                <div role="tablist" aria-label="Dashboard tabs" className="flex flex-wrap gap-1">
                  {tabs.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      role="tab"
                      aria-selected={t.id === tab.id}
                      onClick={() => setTab(t.id)}
                      className={cn(
                        "rounded-md px-2.5 py-1 text-sm focus-visible:outline-2 focus-visible:outline-accent",
                        t.id === tab.id
                          ? "bg-surface-2 font-medium"
                          : "text-muted hover:bg-surface-2",
                      )}
                    >
                      {t.name}
                    </button>
                  ))}
                </div>
                {editing ? (
                  <>
                    <Input
                      aria-label="Tab name"
                      value={tab.name}
                      onChange={(e) => onChange?.((d) => renameTab(d, tab.id, e.target.value))}
                      className="h-7 w-28"
                    />
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label="Add tab"
                      onClick={() => {
                        const added = addTab(dashboard);
                        onChange?.(() => added.dashboard);
                        setTab(added.tab.id);
                      }}
                    >
                      <Plus size={14} aria-hidden="true" />
                    </Button>
                    {tabs.length > 1 ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          onChange?.((d) => removeTab(d, tab.id));
                          setTab(null);
                        }}
                      >
                        Remove tab
                      </Button>
                    ) : null}
                  </>
                ) : null}
              </div>
            ) : null}
            {filterItems.map(({ f, scope }) => (
              <FilterItem
                key={`${scope}:${f.id}`}
                filter={f}
                scope={scope}
                onSaveDefault={editing && scope === "dashboard" ? saveFilterDefault : undefined}
              />
            ))}
            {editing ? (
              <div className="ml-auto flex items-center gap-1">
                <DropdownMenu>
                  <DropdownMenuTrigger
                    ref={addButton}
                    className="inline-flex h-8 items-center gap-1.5 rounded-md bg-accent px-3 text-sm font-medium text-accent-fg hover:brightness-90 focus-visible:outline-2 focus-visible:outline-accent"
                  >
                    <Plus size={14} aria-hidden="true" />
                    Add widget
                  </DropdownMenuTrigger>
                  <DropdownMenuContent>
                    <DropdownMenuLabel>Chart</DropdownMenuLabel>
                    {charts.length === 0 ? (
                      <p className="px-2 py-1 text-xs text-muted">No charts in this project yet.</p>
                    ) : (
                      charts.map((c) => (
                        <DropdownMenuItem
                          key={c.id}
                          onSelect={() =>
                            onChange?.(
                              (d) =>
                                addWidget(d, tab.id, { kind: "chart", chartId: c.id }).dashboard,
                            )
                          }
                        >
                          {c.name}
                        </DropdownMenuItem>
                      ))
                    )}
                    <DropdownMenuSeparator />
                    <DropdownMenuItem onSelect={() => setAdding("kpi")}>KPI card</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => setAdding("filter")}>Filter</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => setAdding("text")}>
                      <Type size={14} aria-hidden="true" /> Text
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => setAdding("image")}>
                      <Image size={14} aria-hidden="true" /> Image
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                <Button
                  onClick={linkAll}
                  disabled={allLinked}
                  title="Clicking or brushing one chart filters all the others"
                >
                  <Link2 size={14} aria-hidden="true" />
                  Cross-filter all charts
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}

        {anyActive ? (
          <div
            className="dashboard-chrome flex flex-wrap items-center gap-1.5 text-xs"
            role="status"
            aria-label="Active filters"
          >
            <span className="text-muted">Filtered by</span>
            {drill.map((p, i) => (
              <Chip
                // biome-ignore lint/suspicious/noArrayIndexKey: predicates have no identity of their own
                key={`drill:${i}:${p.field}:${p.op}`}
                label={describePredicate(p)}
                onClear={() => setDrill(drill.filter((q) => q !== p))}
              />
            ))}
            {selections.map(({ group, widgetId, predicate }, i) => (
              <Chip
                // biome-ignore lint/suspicious/noArrayIndexKey: predicates have no identity of their own
                key={`${group}:${widgetId}:${i}:${predicate.field}:${predicate.op}`}
                label={describePredicate(predicate)}
                onClear={() => clearSelection(group, widgetId)}
              />
            ))}
            <Button size="sm" variant="ghost" onClick={resetAll}>
              <RotateCcw size={12} aria-hidden="true" />
              Clear all
            </Button>
          </div>
        ) : null}

        <DashboardGrid
          tab={tab}
          onLayout={
            editing ? (layout) => onChange?.((d) => applyLayout(d, tab.id, layout)) : undefined
          }
          onRemove={editing ? (id) => onChange?.((d) => removeWidget(d, tab.id, id)) : undefined}
        />
        {editing && (tab.items ?? []).length === 0 ? (
          <p className="text-center text-sm text-muted">
            This tab is empty. Use Add widget to place a chart, KPI card, filter, text or image.
          </p>
        ) : null}
      </div>
      <WidgetDialog
        kind={adding}
        onClose={() => setAdding(null)}
        onAdd={addNew}
        returnFocusTo={addButton}
      />
    </DashboardEnvProvider>
  );
}

function Chip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-2 py-px text-fg">
      {label}
      <button
        type="button"
        aria-label={`Remove filter ${label}`}
        onClick={onClear}
        className="rounded px-0.5 text-muted hover:text-fg focus-visible:outline-2 focus-visible:outline-accent"
      >
        ×
      </button>
    </span>
  );
}
