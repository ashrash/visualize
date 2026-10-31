// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ECharts } from "echarts/core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ChartSpec, Predicate } from "../api/types";
import { dataIdentity } from "../charts/core/data";
import { createDashboard } from "./model";
import { useDashboardRuntime } from "./runtimeStore";
import type { Dashboard } from "./types";

// ---- stand-ins for the parts that need a browser or a server ----

type Listener = (params: unknown) => void;
const charts = new Map<string, { emit: (name: string, params?: unknown) => void }>();

function fakeChart(id: string): ECharts {
  const listeners = new Map<string, Set<Listener>>();
  charts.set(id, {
    emit: (name, params) => {
      for (const fn of listeners.get(name) ?? []) fn(params);
    },
  });
  return {
    on: (name: string, fn: Listener) => {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name)?.add(fn);
    },
    off: (name: string, fn: Listener) => listeners.get(name)?.delete(fn),
    getOption: () => ({ xAxis: [{ type: "category", data: ["North", "South", "East", "West"] }] }),
    getDom: () => document.createElement("div"),
    convertFromPixel: (_finder: unknown, x: number) => x / 10,
    setOption: () => {},
  } as unknown as ECharts;
}

// The chart wrapper: shows the filters it was asked to query with, and names its instance.
vi.mock("../charts", () => ({
  ChartView: ({ spec, filters }: { spec: ChartSpec; filters?: Predicate[] }) => (
    <div data-testid={`chart-${spec.id}`} data-chart={spec.id}>
      <output aria-label={`filters of ${spec.id}`}>{JSON.stringify(filters ?? [])}</output>
    </div>
  ),
  makeFormatter: () => (v: unknown) => String(v),
  useChartData: () => ({ data: undefined, error: null }),
  FORMAT_PRESETS: [],
}));
vi.mock("./useChartInstance", () => ({
  useChartInstance: (frame: { current: HTMLElement | null }) => {
    const id = frame.current?.querySelector("[data-chart]")?.getAttribute("data-chart");
    return id ? fakeChart(id) : null;
  },
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

const click = (id: string, name: string, extra: Record<string, unknown> = {}) =>
  act(() =>
    charts
      .get(id)
      ?.emit("click", { componentType: "series", name, seriesName: "series0", ...extra }),
  );

function renderDashboard(chartSpecs: ChartSpec[], mode: "view" | "edit" | "embed" = "view") {
  const dashboard = dashboardWith(chartSpecs.map((c) => c.id));
  return render(
    wrap(<DashboardView dashboard={dashboard} charts={chartSpecs} pipelines={[]} mode={mode} />),
  );
}

beforeEach(() => {
  queryClient = new QueryClient();
  charts.clear();
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

  it("toggles a click off, and shift-click adds to the selection", () => {
    renderDashboard([spec("a"), spec("b")]);
    click("a", "North");
    click("a", "North");
    expect(filtersOf("b")).toEqual([]);

    click("a", "North");
    click("a", "South", { event: { event: { shiftKey: true } } });
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

  it("ignores clicks while editing (the chart is being moved, not explored)", () => {
    renderDashboard([spec("a"), spec("b")], "edit");
    click("a", "North");
    expect(filtersOf("b")).toEqual([]);
  });

  it("ignores clicks on a chart with click filtering switched off", () => {
    renderDashboard([
      spec("a", { interactions: { clickFilter: false, linkGroup: "g" } as never }),
      spec("b"),
    ]);
    click("a", "North");
    expect(filtersOf("b")).toEqual([]);
  });

  it("selects a range by dragging, committing once after a pause", () => {
    vi.useFakeTimers();
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0,
      top: 0,
      right: 400,
      bottom: 200,
      width: 400,
      height: 200,
      x: 0,
      y: 0,
      toJSON() {},
    });
    renderDashboard([
      spec("a", { interactions: { brush: true, linkGroup: "g" } as never }),
      spec("b"),
    ]);
    // The tool is off until asked for, so it never gets in the way of clicking bars.
    expect(screen.queryByTestId("range-brush")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Select range" }));
    const layer = screen.getByTestId("range-brush");
    fireEvent.pointerDown(layer, { clientX: 10, pointerId: 1 });
    fireEvent.pointerMove(layer, { clientX: 20, pointerId: 1 });
    fireEvent.pointerMove(layer, { clientX: 30, pointerId: 1 });
    fireEvent.pointerUp(layer, { clientX: 30, pointerId: 1 });
    expect(filtersOf("b")).toEqual([]); // nothing yet: waiting for the pause
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toEqual([
      { op: "in", field: "region", values: ["South", "East", "West"] },
    ]);
    expect(filtersOf("a")).toEqual([]); // a chart is not filtered by its own range
    vi.restoreAllMocks();
  });

  it("does not turn a plain click on the brush layer into a range", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", { interactions: { brush: true, linkGroup: "g" } as never }),
      spec("b"),
    ]);
    fireEvent.click(screen.getByRole("button", { name: "Select range" }));
    const layer = screen.getByTestId("range-brush");
    fireEvent.pointerDown(layer, { clientX: 50, pointerId: 1 });
    fireEvent.pointerUp(layer, { clientX: 51, pointerId: 1 });
    act(() => vi.advanceTimersByTime(300));
    expect(filtersOf("b")).toEqual([]);
  });

  it("applies a legend toggle as an `in` filter on the colour field", () => {
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
      charts
        .get("a")
        ?.emit("legendselectchanged", { selected: { web: true, app: false, store: true } }),
    );
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toEqual([{ op: "in", field: "channel", values: ["web", "store"] }]);
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

  it("shows no edit tools in view or embed mode", () => {
    renderDashboard([spec("a")], "embed");
    expect(screen.queryByRole("button", { name: /add widget/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /remove/i })).toBeNull();
  });
});
