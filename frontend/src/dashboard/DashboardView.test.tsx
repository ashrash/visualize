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
// The props the grid was last given, so tests can call its callbacks the way it would.
interface GridProps {
  children: React.ReactNode;
  onLayoutChange?: unknown;
  onDragStop?: (layout: unknown[]) => void;
  onResizeStop?: (layout: unknown[]) => void;
  onBreakpointChange?: (breakpoint: string, columns: number) => void;
}
const grid: { props: GridProps | null } = { props: null };
vi.mock("react-grid-layout", () => ({
  useContainerWidth: () => ({ width: 1200, containerRef: { current: null }, mounted: true }),
  Responsive: (props: GridProps) => {
    grid.props = props;
    return <div>{props.children}</div>;
  },
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

  it("a legend toggle that cannot be a filter leaves a click's filter alone", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", {
        // two measures: legend entries are series, so a toggle never becomes a filter
        encodings: {
          x: [{ field: "region" }],
          y: [{ agg: "count" }, { field: "sales", agg: "sum" }],
          color: [{ field: "channel" }],
        },
      }),
      spec("b"),
    ]);
    click("a", "North");
    expect(filtersOf("b")).toEqual([{ op: "in", field: "region", values: ["North"] }]);
    act(() =>
      views.get("a")?.onLegendChange?.({
        toggled: "app",
        selected: { web: true, app: false },
        shown: ["web"],
        hidden: ["app"],
        field: "channel",
        shownValues: ["web"],
        hiddenValues: ["app"],
      }),
    );
    act(() => vi.advanceTimersByTime(500));
    expect(filtersOf("b")).toEqual([{ op: "in", field: "region", values: ["North"] }]);
  });

  it("showing every legend entry again after a legend filter clears it", () => {
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
    const toggle = (hidden: string[]) =>
      act(() =>
        views.get("a")?.onLegendChange?.({
          toggled: "app",
          selected: {},
          shown: ["web", "app", "store"].filter((n) => !hidden.includes(n)),
          hidden,
          field: "channel",
          shownValues: [],
          hiddenValues: hidden,
        }),
      );
    toggle(["app"]);
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toHaveLength(1);
    toggle([]); // everything on again
    act(() => vi.advanceTimersByTime(200));
    expect(filtersOf("b")).toEqual([]);
  });

  it("a shift-click right after a band is removed extends nothing, not the removed range", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", { interactions: { brush: true, linkGroup: "g" } as never }),
      spec("b"),
    ]);
    brush("a", { categories: ["South"], values: ["South"] });
    act(() => vi.advanceTimersByTime(200));
    brush("a", { cleared: true }); // the user removes the band: the clear is waiting out its pause
    act(() => vi.advanceTimersByTime(50));
    click("a", "North", { shiftKey: true });
    act(() => vi.advanceTimersByTime(500));
    expect(filtersOf("b")).toEqual([{ op: "in", field: "region", values: ["North"] }]);
  });

  it("restarts the message's timer when the same message appears again", () => {
    vi.useFakeTimers();
    renderDashboard([
      spec("a", {
        encodings: { x: [{ field: "d", timeUnit: "month" }], y: [{ agg: "count" }] },
        interactions: { brush: true, linkGroup: "g" } as never,
      }),
      spec("b"),
    ]);
    const dead = () =>
      brush("a", {
        field: "d",
        categories: ["Jan", "Jun"],
        values: ["2024-01-01T00:00:00.000Z", "2024-06-01T00:00:00.000Z"],
      });
    dead();
    expect(screen.getByText(/can't be used as a filter/i)).toBeTruthy();
    act(() => vi.advanceTimersByTime(5000));
    dead(); // the same message again, 5 s in
    act(() => vi.advanceTimersByTime(5000)); // 10 s after the first, 5 s after the second
    expect(screen.getByText(/can't be used as a filter/i)).toBeTruthy();
    act(() => vi.advanceTimersByTime(1500));
    expect(screen.queryByText(/can't be used as a filter/i)).toBeNull();
  });

  it("opens at the top when a widget is pointed at a different chart with the same hierarchy", () => {
    const hierarchy = { linkGroup: "g", drillHierarchy: ["country", "state"] } as never;
    const dash = dashboardWith(["a"]);
    const view = render(
      wrap(
        <DashboardView
          dashboard={dash}
          charts={[spec("a", { interactions: hierarchy })]}
          pipelines={[]}
          mode="view"
        />,
      ),
    );
    act(() =>
      views.get("a")?.onDrillChange?.(["US"], [{ op: "eq", field: "country", value: "US" }]),
    );
    expect(views.get("a")?.drill).toEqual(["US"]);
    // same widget, same source and hierarchy, but another chart
    const first = dash.tabs?.[0];
    if (!first) throw new Error("no tab");
    const moved: Dashboard = {
      ...dash,
      tabs: [
        {
          ...first,
          items: [
            { i: "w-a", x: 0, y: 0, w: 6, h: 4, widget: { kind: "chart" as const, chartId: "z" } },
          ],
        },
      ],
    };
    view.rerender(
      wrap(
        <DashboardView
          dashboard={moved}
          charts={[spec("z", { interactions: hierarchy })]}
          pipelines={[]}
          mode="view"
        />,
      ),
    );
    expect(views.get("z")?.drill).toEqual([]);
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

describe("moving and resizing with the keyboard", () => {
  function renderEditable() {
    let dash = dashboardWith(["a"]);
    const specs = [spec("a")];
    const ui = () =>
      wrap(
        <DashboardView
          dashboard={dash}
          charts={specs}
          pipelines={[]}
          mode="edit"
          onChange={(edit) => {
            dash = edit(dash);
            view.rerender(ui());
          }}
        />,
      );
    const view = render(ui());
    const item = () => {
      const i = dash.tabs?.[0]?.items?.[0];
      return { x: i?.x, y: i?.y, w: i?.w, h: i?.h };
    };
    return { item };
  }
  const handle = () => screen.getByRole("button", { name: /Move or resize/ });
  const press = (key: string, shiftKey = false) => fireEvent.keyDown(handle(), { key, shiftKey });

  it("has a focusable handle with a name, in the tab order", () => {
    renderEditable();
    expect(handle().getAttribute("tabindex")).not.toBe("-1");
    expect(handle().getAttribute("aria-pressed")).toBe("false");
    handle().focus();
    expect(document.activeElement).toBe(handle());
  });

  it("Space picks up, arrows move one cell, Space drops", () => {
    const { item } = renderEditable();
    expect(item()).toEqual({ x: 0, y: 0, w: 6, h: 4 });
    press("ArrowRight"); // not picked up yet: nothing happens
    expect(item().x).toBe(0);
    press(" ");
    expect(handle().getAttribute("aria-pressed")).toBe("true");
    press("ArrowRight");
    press("ArrowRight");
    expect(screen.getByRole("status", { name: "" }).textContent).toMatch(/column 3, row 1/);
    press("ArrowDown");
    // like the grid itself, a lone widget moved down settles back at the top: no step happened
    expect(item()).toEqual({ x: 2, y: 0, w: 6, h: 4 });
    expect(screen.getByRole("status", { name: "" }).textContent).toMatch(/can.t move any further/);
    press(" ");
    expect(handle().getAttribute("aria-pressed")).toBe("false");
    press("ArrowLeft"); // dropped: arrows do nothing again
    expect(item().x).toBe(2);
  });

  it("Shift and arrows resize, within the minimum and the grid", () => {
    const { item } = renderEditable();
    press("Enter");
    press("ArrowRight", true);
    press("ArrowDown", true);
    expect(item()).toEqual({ x: 0, y: 0, w: 7, h: 5 });
    for (let i = 0; i < 10; i++) press("ArrowLeft", true);
    expect(item().w).toBe(2); // never below the minimum
  });

  it("a key pressed before the last step has rendered builds on it (no re-render in between)", () => {
    // The dashboard is never updated here, so the item prop stays at its start for every key, as
    // it does for a moment in the browser when keys arrive faster than React renders.
    let dash = dashboardWith(["a"]);
    render(
      wrap(
        <DashboardView
          dashboard={dash}
          charts={[spec("a")]}
          pipelines={[]}
          mode="edit"
          onChange={(edit) => {
            dash = edit(dash);
          }}
        />,
      ),
    );
    press(" ");
    press("ArrowRight");
    press("ArrowRight", false);
    press("ArrowRight", true); // Shift: resize, from the position the two moves reached
    const item = dash.tabs?.[0]?.items?.[0];
    expect({ x: item?.x, w: item?.w }).toEqual({ x: 2, w: 7 });
  });

  it("Escape puts the widget back where it was picked up", () => {
    const { item } = renderEditable();
    press(" ");
    press("ArrowRight");
    press("ArrowDown", true);
    expect(item()).not.toEqual({ x: 0, y: 0, w: 6, h: 4 });
    press("Escape");
    expect(item()).toEqual({ x: 0, y: 0, w: 6, h: 4 });
    expect(handle().getAttribute("aria-pressed")).toBe("false");
  });

  it("losing focus drops the widget where it is", () => {
    const { item } = renderEditable();
    press(" ");
    press("ArrowRight");
    fireEvent.blur(handle());
    expect(handle().getAttribute("aria-pressed")).toBe("false");
    press("ArrowRight");
    expect(item().x).toBe(1);
  });
});

describe("saving the layout", () => {
  function renderEditable() {
    let dash = dashboardWith(["a", "b"]);
    const edits: Dashboard[] = [];
    const ui = () =>
      wrap(
        <DashboardView
          dashboard={dash}
          charts={[spec("a"), spec("b")]}
          pipelines={[]}
          mode="edit"
          onChange={(edit) => {
            dash = edit(dash);
            edits.push(dash);
            view.rerender(ui());
          }}
        />,
      );
    const view = render(ui());
    const items = () => dash.tabs?.[0]?.items?.map((i) => [i.i, i.x, i.y, i.w, i.h]);
    return { items, edits };
  }

  it("does not write the grid's own layout-change report back (it echoes, and loops)", () => {
    renderEditable();
    // The grid reports its layout whenever it is given one, including a stale one: storing that
    // sets two layouts off against each other without end (React's maximum update depth).
    expect(grid.props?.onLayoutChange).toBeUndefined();
  });

  it("stores the layout when a drag or a resize ends", () => {
    const { items, edits } = renderEditable();
    act(() =>
      grid.props?.onDragStop?.([
        { i: "w-a", x: 6, y: 0, w: 6, h: 4 },
        { i: "w-b", x: 0, y: 0, w: 6, h: 4 },
      ]),
    );
    expect(items()).toEqual([
      ["w-a", 6, 0, 6, 4],
      ["w-b", 0, 0, 6, 4],
    ]);
    act(() =>
      grid.props?.onResizeStop?.([
        { i: "w-a", x: 6, y: 0, w: 6, h: 7 },
        { i: "w-b", x: 0, y: 0, w: 6, h: 4 },
      ]),
    );
    expect(items()?.[0]).toEqual(["w-a", 6, 0, 6, 7]);
    expect(edits).toHaveLength(2);
  });

  it("stores nothing for a narrow screen's re-flow, and nothing just for loading", () => {
    const { edits } = renderEditable();
    expect(edits).toHaveLength(0); // an untouched dashboard is not marked as edited
    act(() => grid.props?.onBreakpointChange?.("xs", 4));
    act(() => grid.props?.onDragStop?.([{ i: "w-a", x: 0, y: 0, w: 4, h: 4 }]));
    expect(edits).toHaveLength(0);
  });
});

describe("moving widgets that share the grid with the keyboard", () => {
  function renderPair(second: { x: number; y: number }) {
    const base = dashboardWith(["a", "b"]);
    const tab = base.tabs?.[0];
    if (!tab) throw new Error("no tab");
    let dash: Dashboard = {
      ...base,
      tabs: [
        {
          ...tab,
          items: (tab.items ?? []).map((it, i) =>
            i === 0 ? { ...it, x: 0, y: 0 } : { ...it, x: second.x, y: second.y },
          ),
        },
      ],
    };
    const ui = () =>
      wrap(
        <DashboardView
          dashboard={dash}
          charts={[spec("a"), spec("b")]}
          pipelines={[]}
          mode="edit"
          onChange={(edit) => {
            dash = edit(dash);
            view.rerender(ui());
          }}
        />,
      );
    const view = render(ui());
    const items = () => dash.tabs?.[0]?.items?.map((i) => [i.i, i.x, i.y]);
    const handles = () => screen.getAllByRole("button", { name: /Move or resize/ });
    const press = (index: number, key: string, shiftKey = false) =>
      fireEvent.keyDown(handles()[index] as HTMLElement, { key, shiftKey });
    return { items, press };
  }

  it("swaps two stacked widgets by stepping one past the other", () => {
    const { items, press } = renderPair({ x: 0, y: 4 });
    press(0, " ");
    press(0, "ArrowDown");
    expect(items()).toEqual([
      ["w-a", 0, 4],
      ["w-b", 0, 0],
    ]);
  });

  it("pushes the neighbour a step lands on, and Escape puts both back", () => {
    const { items, press } = renderPair({ x: 6, y: 0 });
    press(0, " ");
    for (let i = 0; i < 6; i++) press(0, "ArrowRight");
    // a is now over b's column: b gives way (down), not a
    expect(items()).toEqual([
      ["w-a", 6, 0],
      ["w-b", 6, 4],
    ]);
    press(0, "Escape");
    expect(items()).toEqual([
      ["w-a", 0, 0],
      ["w-b", 6, 0],
    ]);
  });

  it("says resize when a resize cannot go further, and move when a move cannot", () => {
    const { press } = renderPair({ x: 6, y: 0 });
    press(0, " ");
    for (let i = 0; i < 8; i++) press(0, "ArrowLeft", true); // shrink to the minimum
    expect(screen.getAllByRole("status", { name: "" })[0]?.textContent).toMatch(
      /resized any further/,
    );
    press(0, "ArrowLeft"); // already at the left edge
    expect(screen.getAllByRole("status", { name: "" })[0]?.textContent).toMatch(/move any further/);
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

  it("the tablist holds only tabs; renaming and tab buttons sit beside it", () => {
    renderDashboard([spec("a")], "edit");
    const tablist = screen.getByRole("tablist", { name: "Dashboard tabs" });
    for (const child of Array.from(tablist.children))
      expect(child.getAttribute("role")).toBe("tab");
    expect(within(tablist).queryByRole("textbox")).toBeNull();
    expect(within(tablist).queryByRole("button", { name: "Add tab" })).toBeNull();
    // the rename field and the buttons still exist, outside it
    expect(screen.getByRole("textbox", { name: "Tab name" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add tab" })).toBeTruthy();
    // and the active tab is a tab like the others
    expect(within(tablist).getByRole("tab", { selected: true })).toBeTruthy();
  });

  it("renames the active tab from the field beside the tablist", () => {
    let dash = dashboardWith(["a"]);
    const ui = () =>
      wrap(
        <DashboardView
          dashboard={dash}
          charts={[spec("a")]}
          pipelines={[]}
          mode="edit"
          onChange={(edit) => {
            dash = edit(dash);
            view.rerender(ui());
          }}
        />,
      );
    const view = render(ui());
    fireEvent.change(screen.getByRole("textbox", { name: "Tab name" }), {
      target: { value: "Overview" },
    });
    expect(dash.tabs?.[0]?.name).toBe("Overview");
    expect(screen.getByRole("tab", { name: "Overview" })).toBeTruthy();
  });

  it("shows no edit tools in view or embed mode", () => {
    renderDashboard([spec("a")], "embed");
    expect(screen.queryByRole("button", { name: /add widget/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /remove/i })).toBeNull();
  });
});
