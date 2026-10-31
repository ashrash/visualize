// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useEffect, useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChartSpec, Predicate } from "../api/types";
import { dataIdentity } from "../charts/core/data";
import { createDashboard } from "./model";
import { useDashboardRuntime } from "./runtimeStore";
import type { Dashboard } from "./types";

// ---- stand-ins for the parts that need a browser or a server ----

import type { ChartBrush, ChartLegendChange, ChartPointClick } from "../charts";

interface ViewProps {
  spec: ChartSpec;
  filters?: Predicate[];
  slot?: string;
  onPointClick?: (hit: ChartPointClick) => void;
  onBrushSelected?: (brush: ChartBrush) => void;
  onLegendChange?: (change: ChartLegendChange) => void;
  drill?: unknown[];
  onDrillChange?: (path: unknown[], predicates: Predicate[]) => void;
}
// Latest props of each rendered chart, by chart id.
const views = new Map<string, ViewProps>();
// How many times each chart has been mounted, and which ones have a brush band drawn.
const mounts = new Map<string, number>();
const banded = new Set<string>();

// The chart wrapper: shows the filters it was asked to query with and keeps its callbacks.
vi.mock("../charts", () => ({
  ChartView: (props: ViewProps) => {
    views.set(props.spec.id, props);
    const latest = useRef(props);
    latest.current = props;
    useEffect(() => {
      const id = props.spec.id;
      mounts.set(id, (mounts.get(id) ?? 0) + 1);
      // The real ChartView reports a drawn band as cleared when it unmounts.
      return () => {
        if (banded.has(id)) {
          banded.delete(id);
          latest.current.onBrushSelected?.({
            axis: "x",
            kind: "category",
            ranges: [],
            categories: [],
            cleared: true,
            field: "region",
            values: [],
          });
        }
      };
    }, [props.spec.id]);
    return (
      <div data-testid={`chart-${props.spec.id}`}>
        <output aria-label={`filters of ${props.spec.id}`}>
          {JSON.stringify(props.filters ?? [])}
        </output>
      </div>
    );
  },
  makeFormatter: () => (v: unknown) => String(v),
  useChartData: () => ({ data: undefined, error: null }),
  FORMAT_PRESETS: [],
}));
vi.mock("react-grid-layout", () => ({
  useContainerWidth: () => ({ width: 1200, containerRef: { current: null }, mounted: true }),
  Responsive: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("react-grid-layout/css/styles.css", () => ({}));
vi.mock("../data/hooks", () => ({ useDatasets: () => ({ data: [] }) }));
vi.mock("../api/pipelines", () => ({ inspectPipeline: vi.fn() }));

import { DashboardView } from "./DashboardView";

// One query client per test. Each chart spec made by `spec()` gets cached data, like a chart
// that has already been drawn: clicks are mapped back to raw values through it.
let queryClient = new QueryClient();
const wrap = (ui: React.ReactNode) => (
  <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
);
function seed(chart: ChartSpec): ChartSpec {
  queryClient.setQueryData(["chart-data", dataIdentity(chart), [], null, null], {
    columns: { x: ["North", "South", "East", "West"], color: ["web", "app", "store"] },
    kinds: { x: "string", color: "string" },
    rowCount: 4,
  });
  return chart;
}

const spec = (id: string, over: Partial<ChartSpec> = {}): ChartSpec =>
  seed({
    id,
    name: `Chart ${id}`,
    plotType: "bar",
    source: { kind: "dataset", id: "ds1" },
    encodings: { x: [{ field: "region" }], y: [{ field: "sales", agg: "sum" }] },
    interactions: {
      zoom: true,
      brush: false,
      clickFilter: true,
      drillHierarchy: [],
      linkGroup: "g",
    },
    ...over,
  } as ChartSpec);

function dashboardWith(chartIds: string[]): Dashboard {
  const d = createDashboard("Test");
  const tab = d.tabs?.[0];
  if (!tab) throw new Error("no tab");
  return {
    ...d,
    tabs: [
      {
        ...tab,
        items: chartIds.map((id, i) => ({
          i: `w-${id}`,
          x: (i % 2) * 6,
          y: Math.floor(i / 2) * 4,
          w: 6,
          h: 4,
          widget: { kind: "chart" as const, chartId: id },
        })),
      },
    ],
  };
}

const filtersOf = (id: string): Predicate[] =>
  JSON.parse(screen.getByLabelText(`filters of ${id}`).textContent ?? "[]");

/** A click as ChartView reports it: the raw value is what is in the data, not the label. */
const hit = (
  rawValue: ChartPointClick["rawValue"],
  over: Partial<ChartPointClick> = {},
): ChartPointClick => ({
  seriesIndex: 0,
  seriesName: "",
  name: String(rawValue),
  dataIndex: 0,
  value: 1,
  shiftKey: false,
  field: "region",
  rawValue,
  drilled: false,
  ...over,
});

const click = (
  id: string,
  rawValue: ChartPointClick["rawValue"],
  over: Partial<ChartPointClick> = {},
) => act(() => views.get(id)?.onPointClick?.(hit(rawValue, over)));

const brush = (id: string, over: Partial<ChartBrush> = {}) =>
  act(() => {
    if (over.cleared) banded.delete(id);
    else banded.add(id);
    views.get(id)?.onBrushSelected?.({
      axis: "x",
      kind: "category",
      ranges: [],
      categories: [],
      cleared: false,
      field: "region",
      values: [],
      ...over,
    });
  });

function renderDashboard(chartSpecs: ChartSpec[], mode: "view" | "edit" | "embed" = "view") {
  const dashboard = dashboardWith(chartSpecs.map((c) => c.id));
  return render(
    wrap(<DashboardView dashboard={dashboard} charts={chartSpecs} pipelines={[]} mode={mode} />),
  );
}

beforeEach(() => {
  queryClient = new QueryClient();
  views.clear();
  mounts.clear();
  banded.clear();
  useDashboardRuntime.setState({ globalValues: {}, gridDrill: null });
  useDashboardRuntime.getState().open("fresh");
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("cross-filtering a dashboard", () => {
  it("filters the other charts in the link group by a click, but never the chart clicked", () => {
    renderDashboard([
      spec("a"),
      spec("b"),
      spec("c", { interactions: { linkGroup: "other" } as never }),
    ]);
    expect(filtersOf("a")).toEqual([]);

    click("a", "North");
    expect(filtersOf("a")).toEqual([]); // its own selection is excluded
    expect(filtersOf("b")).toEqual([{ op: "in", field: "region", values: ["North"] }]);
    expect(filtersOf("c")).toEqual([]); // a different group is untouched
  });

  it("filters by the raw value in the data: a number stays a number, blank is NULL", () => {
    renderDashboard([spec("a", { encodings: { x: [{ field: "year" }] } }), spec("b")]);
    click("a", 2020, { field: "year", name: "2,020" });
    expect(filtersOf("b")).toEqual([{ op: "in", field: "year", values: [2020] }]);
    click("a", null, { field: "year", name: "(blank)" });
    expect(filtersOf("b")).toEqual([{ op: "in", field: "year", values: [null] }]);
  });

  it("filters a time bucket by the whole bucket, from the bucket's start", () => {
    renderDashboard([
      spec("a", { encodings: { x: [{ field: "order_date", timeUnit: "month" }] } }),
      spec("b"),
    ]);
    click("a", "2024-02-01T00:00:00.000Z", { field: "order_date", name: "Feb 2024" });
    expect(filtersOf("b")).toEqual([
      {
        op: "between_dates",
        field: "order_date",
        min: "2024-02-01T00:00:00.000Z",
        max: "2024-03-01T00:00:00.000Z",
        includeMin: true,
        includeMax: false,
      },
    ]);
  });

  it("adds the colour group of a clicked stacked segment", () => {
    renderDashboard([
      spec("a", {
        encodings: {
          x: [{ field: "region" }],
          y: [{ agg: "count" }],
          color: [{ field: "channel" }],
        },
      }),
      spec("b"),
    ]);
    click("a", "North", { seriesName: "web" });
    expect(filtersOf("b")).toEqual([
      { op: "in", field: "region", values: ["North"] },
      { op: "in", field: "channel", values: ["web"] },
    ]);
  });

  it("does nothing for a click it cannot resolve, or one that drilled the chart deeper", () => {
    renderDashboard([spec("a"), spec("b")]);
    click("a", undefined);
    expect(filtersOf("b")).toEqual([]);
    click("a", "North", { drilled: true });
    expect(filtersOf("b")).toEqual([]);
  });

  it("toggles a click off, and shift-click adds to the selection", () => {
    renderDashboard([spec("a"), spec("b")]);
    click("a", "North");
    click("a", "North");
    expect(filtersOf("b")).toEqual([]);

    click("a", "North");
    click("a", "South", { shiftKey: true });
    expect(filtersOf("b")).toEqual([{ op: "in", field: "region", values: ["North", "South"] }]);
  });

  it("shows the active selection as a chip and clears everything from it", () => {
    renderDashboard([spec("a"), spec("b")]);
    click("a", "North");
    const status = screen.getByRole("status", { name: "Active filters" });
    expect(within(status).getByText("region: North")).toBeTruthy();
    fireEvent.click(within(status).getByRole("button", { name: "Clear all" }));
    expect(filtersOf("b")).toEqual([]);
    expect(screen.queryByRole("status", { name: "Active filters" })).toBeNull();
  });

  it("drops a chart's selection when the chart leaves the dashboard", () => {
    const view = renderDashboard([spec("a"), spec("b")]);
    click("a", "North");
    expect(filtersOf("b")).toHaveLength(1);
    const next = dashboardWith(["b"]);
    view.rerender(
      wrap(
        <DashboardView
          dashboard={next}
          charts={[spec("a"), spec("b")]}
          pipelines={[]}
          mode="view"
        />,
      ),
    );
    expect(filtersOf("b")).toEqual([]);
  });

  it("gives charts no interaction callbacks while editing (they are being moved)", () => {
    renderDashboard(
      [spec("a", { interactions: { brush: true, linkGroup: "g" } as never }), spec("b")],
      "edit",
    );
    expect(views.get("a")?.onPointClick).toBeUndefined();
    expect(views.get("a")?.onBrushSelected).toBeUndefined();
    expect(views.get("a")?.onLegendChange).toBeUndefined();
    // and no brush band is drawn while arranging
    expect(views.get("a")?.spec.interactions?.brush).toBe(false);
  });

  it("gives no click callback to a chart with click filtering off, or a plot that cannot use it", () => {
    renderDashboard([
      spec("a", { interactions: { clickFilter: false, linkGroup: "g" } as never }),
      spec("s", { plotType: "histogram" }),
    ]);
    expect(views.get("a")?.onPointClick).toBeUndefined();
    expect(views.get("s")?.onPointClick).toBeUndefined();
    expect(views.get("s")?.onBrushSelected).toBeUndefined(); // a histogram axis is not in data units
  });

  it("gives each widget its own request slot, and the dashboard's link group", () => {
    renderDashboard([spec("a", { interactions: { linkGroup: undefined } as never }), spec("b")]);
    expect(views.get("a")?.slot).toBe("w-a");
    expect(views.get("b")?.slot).toBe("w-b");
  });

  it("filters the others by a brush, committing once after a pause", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", { interactions: { brush: true, linkGroup: "g" } as never }),
      spec("b"),
    ]);
    const picked = { categories: ["South", "East"], values: ["South", "East"] };
    brush("a", { categories: ["South"], values: ["South"] });
    brush("a", picked);
    expect(filtersOf("b")).toEqual([]); // nothing yet: waiting for the pause
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toEqual([{ op: "in", field: "region", values: ["South", "East"] }]);
    expect(filtersOf("a")).toEqual([]); // a chart is not filtered by its own brush
    // removing the band clears the selection
    brush("a", { cleared: true });
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toEqual([]);
  });

  it("filters by a numeric range from a value-axis brush", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", { interactions: { brush: true, linkGroup: "g" } as never }),
      spec("b"),
    ]);
    brush("a", { kind: "value", field: "price", ranges: [[3, 9]] });
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toEqual([
      { op: "range", field: "price", min: 3, max: 9, includeMin: true, includeMax: true },
    ]);
  });

  it("applies a legend toggle as an `in` filter of the colour values still shown", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", {
        encodings: {
          x: [{ field: "region" }],
          y: [{ agg: "count" }],
          color: [{ field: "channel" }],
        },
      }),
      spec("b"),
    ]);
    act(() =>
      views.get("a")?.onLegendChange?.({
        toggled: "app",
        selected: { web: true, app: false, store: true },
        shown: ["web", "store"],
        hidden: ["app"],
        field: "channel",
        shownValues: ["web", "store"],
        hiddenValues: ["app"],
      }),
    );
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toEqual([{ op: "in", field: "channel", values: ["web", "store"] }]);
  });

  it("filters nothing for a legend entry it cannot resolve (fails open)", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", {
        encodings: {
          x: [{ field: "region" }],
          y: [{ agg: "count" }],
          color: [{ field: "channel" }],
        },
      }),
      spec("b"),
    ]);
    act(() =>
      views.get("a")?.onLegendChange?.({
        toggled: "ghost",
        selected: {},
        shown: ["web"],
        hidden: ["app", "ghost"],
        field: "channel",
        shownValues: ["web"],
        hiddenValues: ["app"], // 'ghost' maps to no value
      }),
    );
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toEqual([]);
  });

  it("gives no brush to plots whose x axis is not in data units (Q-Q, lag, scores...)", () => {
    renderDashboard([
      spec("q", { plotType: "qq", interactions: { brush: true, linkGroup: "g" } as never }),
      spec("a", { interactions: { brush: true, linkGroup: "g" } as never }),
      spec("s", { plotType: "scatter", interactions: { brush: true, linkGroup: "g" } as never }),
    ]);
    expect(views.get("q")?.onBrushSelected).toBeUndefined();
    expect(views.get("q")?.spec.interactions?.brush).toBe(false); // and no band is drawn
    expect(views.get("a")?.onBrushSelected).toBeDefined();
    expect(views.get("a")?.spec.interactions?.brush).toBe(true);
    expect(views.get("s")?.onBrushSelected).toBeDefined();
  });

  it("keeps the drill path in a leaf click and in the drill-through", () => {
    const onDrillThrough = vi.fn();
    const dashboard = dashboardWith(["a", "b"]);
    render(
      wrap(
        <DashboardView
          dashboard={dashboard}
          charts={[spec("a"), spec("b")]}
          pipelines={[]}
          mode="view"
          onDrillThrough={onDrillThrough}
        />,
      ),
    );
    const path: Predicate = { op: "eq", field: "country", value: "US" };
    act(() => views.get("a")?.onDrillChange?.(["US"], [path]));
    click("a", "Texas", { field: "state" });
    expect(filtersOf("b")).toEqual([path, { op: "in", field: "state", values: ["Texas"] }]);
  });

  it("takes the brush band off the chart when the selection is cleared from the dashboard", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", { interactions: { brush: true, linkGroup: "g" } as never }),
      spec("b"),
    ]);
    brush("a", { categories: ["South"], values: ["South"] });
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toHaveLength(1);
    expect(mounts.get("a")).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    // remounted: a fresh chart has no band, so there is no band without a filter
    expect(mounts.get("a")).toBe(2);
    act(() => vi.advanceTimersByTime(300));
    expect(filtersOf("b")).toEqual([]);
  });

  it("does not remount a chart for a selection that was a plain click, or one it cleared itself", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", { interactions: { brush: true, linkGroup: "g" } as never }),
      spec("b"),
    ]);
    click("a", "North");
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(mounts.get("a")).toBe(1);
    brush("a", { categories: ["South"], values: ["South"] });
    act(() => vi.advanceTimersByTime(200));
    brush("a", { cleared: true }); // the chart's own "clear selection"
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toEqual([]);
    expect(mounts.get("a")).toBe(1);
  });

  it("a click that replaces a brush keeps its filter: the old chart's cleared report must not wipe it", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", { interactions: { brush: true, linkGroup: "g" } as never }),
      spec("b"),
    ]);
    brush("a", { categories: ["South"], values: ["South"] });
    act(() => vi.advanceTimersByTime(200));
    click("a", "North");
    // the chart was remounted to drop the band, and the old one reported its band cleared
    expect(mounts.get("a")).toBe(2);
    expect(filtersOf("b")).toEqual([{ op: "in", field: "region", values: ["North"] }]);
    // ...and nothing arrives later to overwrite the click
    act(() => vi.advanceTimersByTime(500));
    expect(filtersOf("b")).toEqual([{ op: "in", field: "region", values: ["North"] }]);
  });

  it("a click within the brush's own pause wins over the brush still waiting to settle", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", { interactions: { brush: true, linkGroup: "g" } as never }),
      spec("b"),
    ]);
    brush("a", { categories: ["South"], values: ["South"] });
    // 50 ms later, before the brush has settled
    act(() => vi.advanceTimersByTime(50));
    click("a", "North");
    act(() => vi.advanceTimersByTime(500));
    expect(filtersOf("b")).toEqual([{ op: "in", field: "region", values: ["North"] }]);
  });

  it("shift-click on a brushed chart extends the selection, and it survives the pause", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", { interactions: { brush: true, linkGroup: "g" } as never }),
      spec("b"),
    ]);
    brush("a", { categories: ["South"], values: ["South"] });
    act(() => vi.advanceTimersByTime(200));
    click("a", "North", { shiftKey: true });
    act(() => vi.advanceTimersByTime(500));
    expect(filtersOf("b")).toEqual([{ op: "in", field: "region", values: ["South", "North"] }]);
  });

  it("drops a band that cannot be a filter, and says why", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", {
        encodings: { x: [{ field: "d", timeUnit: "month" }], y: [{ agg: "count" }] },
        interactions: { brush: true, linkGroup: "g" } as never,
      }),
      spec("b"),
    ]);
    // Jan and Jun: bands that do not touch
    brush("a", {
      field: "d",
      categories: ["Jan", "Jun"],
      values: ["2024-01-01T00:00:00.000Z", "2024-06-01T00:00:00.000Z"],
    });
    act(() => vi.advanceTimersByTime(300));
    expect(filtersOf("b")).toEqual([]);
    expect(mounts.get("a")).toBe(2); // remounted: no dead band left on screen
    expect(screen.getByText(/can't be used as a filter/i)).toBeTruthy();
    // a later valid click works, and clears the message
    click("a", "2024-02-01T00:00:00.000Z", { field: "d" });
    expect(screen.queryByText(/can't be used as a filter/i)).toBeNull();
    expect(filtersOf("b")).toHaveLength(1);
  });

  it("an unusable band replaces an earlier valid selection rather than leaving it half-applied", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", {
        encodings: { x: [{ field: "d", timeUnit: "month" }], y: [{ agg: "count" }] },
        interactions: { brush: true, linkGroup: "g" } as never,
      }),
      spec("b"),
    ]);
    brush("a", { field: "d", categories: ["Jan"], values: ["2024-01-01T00:00:00.000Z"] });
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toHaveLength(1);
    brush("a", {
      field: "d",
      categories: ["Jan", "Jun"],
      values: ["2024-01-01T00:00:00.000Z", "2024-06-01T00:00:00.000Z"],
    });
    act(() => vi.advanceTimersByTime(300));
    expect(filtersOf("b")).toEqual([]);
  });

  it("holds the drill position, so a remount does not send the chart back to the top", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", {
        encodings: { x: [{ field: "country" }], y: [{ agg: "count" }] },
        interactions: {
          brush: true,
          linkGroup: "g",
          drillHierarchy: ["country", "state"],
        } as never,
      }),
      spec("b"),
    ]);
    expect(views.get("a")?.drill).toEqual([]);
    const path: Predicate = { op: "eq", field: "country", value: "US" };
    act(() => views.get("a")?.onDrillChange?.(["US"], [path]));
    expect(views.get("a")?.drill).toEqual(["US"]);
    brush("a", { field: "state", categories: ["TX"], values: ["TX"] });
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toEqual([path, { op: "in", field: "state", values: ["TX"] }]);
    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));
    expect(mounts.get("a")).toBe(2); // remounted to drop the band
    expect(views.get("a")?.drill).toEqual(["US"]); // but still drilled into the US
    click("a", "CA", { field: "state" });
    // the click carries the path the chart is really at, once
    expect(filtersOf("b")).toEqual([path, { op: "in", field: "state", values: ["CA"] }]);
  });

  it("clears the chart's selection when it drills to another level", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", {
        interactions: { linkGroup: "g", drillHierarchy: ["country", "state"] } as never,
      }),
      spec("b"),
    ]);
    click("a", "North");
    expect(filtersOf("b")).toHaveLength(1);
    act(() =>
      views.get("a")?.onDrillChange?.(["US"], [{ op: "eq", field: "country", value: "US" }]),
    );
    expect(filtersOf("b")).toEqual([]);
  });

  it("starts at the top again when the chart's hierarchy changes", () => {
    const base = spec("a", {
      interactions: { linkGroup: "g", drillHierarchy: ["country", "state"] } as never,
    });
    const dash = dashboardWith(["a"]);
    const view = render(
      wrap(<DashboardView dashboard={dash} charts={[base]} pipelines={[]} mode="view" />),
    );
    act(() =>
      views.get("a")?.onDrillChange?.(["US"], [{ op: "eq", field: "country", value: "US" }]),
    );
    expect(views.get("a")?.drill).toEqual(["US"]);
    const edited = spec("a", {
      interactions: { linkGroup: "g", drillHierarchy: ["region", "city"] } as never,
    });
    view.rerender(
      wrap(<DashboardView dashboard={dash} charts={[edited]} pipelines={[]} mode="view" />),
    );
    expect(views.get("a")?.drill).toEqual([]);
  });
});

describe("dashboard filters", () => {
  it("pass a live filter value to every chart, mapped through fieldMap", () => {
    const base = dashboardWith(["a"]);
    const dashboard: Dashboard = {
      ...base,
      filters: [
        {
          id: "f1",
          label: "Date",
          field: "order_date",
          fieldMap: { ds1: "created" },
          predicate: null,
          control: "date_range",
        },
      ],
    };
    render(
      wrap(<DashboardView dashboard={dashboard} charts={[spec("a")]} pipelines={[]} mode="view" />),
    );
    expect(filtersOf("a")).toEqual([]);
    act(() =>
      useDashboardRuntime.getState().setFilter("f1", {
        op: "between_dates",
        field: "order_date",
        min: "2024-01-01",
        max: "2024-02-01",
        includeMin: true,
        includeMax: false,
      }),
    );
    expect(filtersOf("a")).toEqual([
      expect.objectContaining({ op: "between_dates", field: "created", min: "2024-01-01" }),
    ]);
  });

  it("applies a saved filter default until the viewer changes it", () => {
    const base = dashboardWith(["a"]);
    const saved: Predicate = { op: "eq", field: "region", value: "West" };
    const dashboard: Dashboard = {
      ...base,
      filters: [{ id: "f1", label: "Region", field: "region", fieldMap: {}, predicate: saved }],
    };
    render(
      wrap(<DashboardView dashboard={dashboard} charts={[spec("a")]} pipelines={[]} mode="view" />),
    );
    expect(filtersOf("a")).toEqual([saved]);
    act(() => useDashboardRuntime.getState().setFilter("f1", null));
    expect(filtersOf("a")).toEqual([]);
  });
});

describe("tabs and embed", () => {
  it("switches tabs without touching the cross-filter state", () => {
    const d = dashboardWith(["a"]);
    const first = d.tabs?.[0];
    if (!first) throw new Error("no tab");
    const dashboard: Dashboard = {
      ...d,
      tabs: [first, { id: "t2", name: "Second", items: [] }],
    };
    render(
      wrap(<DashboardView dashboard={dashboard} charts={[spec("a")]} pipelines={[]} mode="view" />),
    );
    fireEvent.click(screen.getByRole("tab", { name: "Second" }));
    expect(screen.getByRole("tab", { name: "Second" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByTestId("chart-a")).toBeNull();
  });

  it("keeps a chart's selection while another tab is showing", () => {
    const base = dashboardWith(["a"]);
    const first = base.tabs?.[0];
    if (!first) throw new Error("no tab");
    const second = {
      id: "t2",
      name: "Second",
      items: [
        { i: "w-b", x: 0, y: 0, w: 6, h: 4, widget: { kind: "chart" as const, chartId: "b" } },
      ],
    };
    const dashboard: Dashboard = { ...base, tabs: [first, second] };
    render(
      wrap(
        <DashboardView
          dashboard={dashboard}
          charts={[spec("a"), spec("b")]}
          pipelines={[]}
          mode="view"
        />,
      ),
    );
    click("a", "North");
    fireEvent.click(screen.getByRole("tab", { name: "Second" }));
    // 'a' is unmounted now, but it is still on the dashboard: its click still filters 'b'.
    expect(filtersOf("b")).toEqual([{ op: "in", field: "region", values: ["North"] }]);
    fireEvent.click(screen.getByRole("tab", { name: "Tab 1" }));
    expect(screen.getByRole("status", { name: "Active filters" })).toBeTruthy();
  });

  it("keeps a brush filter across a tab switch, though the chart reports its band cleared on unmount", () => {
    vi.useFakeTimers();
    const base = dashboardWith(["a"]);
    const first = base.tabs?.[0];
    if (!first) throw new Error("no tab");
    const second = {
      id: "t2",
      name: "Second",
      items: [
        { i: "w-b", x: 0, y: 0, w: 6, h: 4, widget: { kind: "chart" as const, chartId: "b" } },
      ],
    };
    const dashboard: Dashboard = { ...base, tabs: [first, second] };
    render(
      wrap(
        <DashboardView
          dashboard={dashboard}
          charts={[
            spec("a", { interactions: { brush: true, linkGroup: "g" } as never }),
            spec("b"),
          ]}
          pipelines={[]}
          mode="view"
        />,
      ),
    );
    brush("a", { categories: ["South"], values: ["South"] });
    act(() => vi.advanceTimersByTime(200));
    fireEvent.click(screen.getByRole("tab", { name: "Second" }));
    // 'a' unmounted and reported cleared; that must not reach the bus, now or after the debounce
    act(() => vi.advanceTimersByTime(500));
    expect(filtersOf("b")).toEqual([{ op: "in", field: "region", values: ["South"] }]);
    expect(screen.getByRole("status", { name: "Active filters" })).toBeTruthy();
  });

  it("shows no edit tools in view or embed mode", () => {
    renderDashboard([spec("a")], "embed");
    expect(screen.queryByRole("button", { name: /add widget/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /remove/i })).toBeNull();
  });
});
