// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ExportSinkProvider } from "../../export/sinkContext";
import { makeData, makeSpec } from "./testing";

const chartDispatch = vi.fn();
const useChartData = vi.fn();
const useChartOverlays = vi.fn(() => ({ data: undefined }));
vi.mock("./data", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./data")>()),
  useChartData: (...a: unknown[]) => useChartData(...a),
  useChartOverlays: () => useChartOverlays(),
}));

interface FakeEChartProps {
  onReady?(c: unknown): void;
  onViewport?(r: [number, number] | null): void;
  resetKey?: string;
  linkGroup?: string | null;
  onPointClick?(hit: unknown): void;
  onBrush?(brush: unknown): void;
  onLegendChange?(change: unknown): void;
}
let lastEChartProps: FakeEChartProps = {};
vi.mock("./EChart", () => ({
  EChart: (props: FakeEChartProps) => {
    lastEChartProps = props;
    useEffect(() => {
      props.onReady?.({ dispatchAction: chartDispatch });
    }, [props.onReady]);
    return <div data-testid="echart" />;
  },
}));

const saveChart = vi.fn(async (..._args: unknown[]) => {});
vi.mock("../../export/saveChart", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../export/saveChart")>()),
  saveChart: (...a: unknown[]) => saveChart(...a),
}));

import { ChartView, reductionBadge } from "./ChartView";

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  useChartData.mockReset();
  saveChart.mockClear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ready = (data: ReturnType<typeof makeData>) =>
  useChartData.mockReturnValue({ data, error: null, isFetching: false, refetch: vi.fn() });

describe("ChartView states", () => {
  it("shows an empty-state notice instead of a blank canvas when there are no rows (S11)", () => {
    ready(makeData({ x: [], y0: [] }, { x: "k", y0: "v" }));
    render(<ChartView spec={makeSpec()} />);
    expect(screen.getByText(/No data to show/)).toBeTruthy();
    expect(screen.queryByTestId("echart")).toBeNull();
  });

  it("warns when a pie omits negative values, not just draws a blank (S11)", () => {
    ready(makeData({ x: ["a", "b", "c", "d"], y0: [-1, -2, -3, 5] }, { x: "k", y0: "v" }));
    render(<ChartView spec={makeSpec({ plotType: "pie" })} />);
    expect(screen.getByText(/3 negative values/)).toBeTruthy();
  });

  it("reports rows matched by the filters, not rows scanned", () => {
    ready(
      makeData(
        { x: ["a"], y0: [1] },
        {},
        { meta: { rowsScanned: 1_000_000, rowsMatched: 250, reduction: "aggregated" } },
      ),
    );
    render(<ChartView spec={makeSpec()} />);
    expect(screen.getByText("Aggregated from 250 rows")).toBeTruthy();
    expect(screen.getByText(/^250 rows/)).toBeTruthy();
    expect(
      reductionBadge(
        makeData(
          { x: [] },
          {},
          { meta: { rowsScanned: 9, rowsMatched: null, reduction: "sampled" } },
        ).meta,
      ),
    ).toContain("9");
  });

  it("shows a reload notice when a 409 is the final state and nothing is loading (S9)", () => {
    const refetch = vi.fn();
    useChartData.mockReturnValue({
      data: undefined,
      error: Object.assign(new Error("superseded"), { status: 409, name: "ApiError" }),
      isFetching: false,
      refetch,
    });
    // A real ApiError is needed for isSuperseded.
    return import("../../api/client").then(({ ApiError }) => {
      useChartData.mockReturnValue({
        data: undefined,
        error: new ApiError("superseded", 409),
        isFetching: false,
        refetch,
      });
      render(<ChartView spec={makeSpec()} />);
      fireEvent.click(screen.getByRole("button", { name: "Reload chart" }));
      expect(refetch).toHaveBeenCalled();
    });
  });

  it("shows Loading while a retry is still running", async () => {
    const { ApiError } = await import("../../api/client");
    useChartData.mockReturnValue({
      data: undefined,
      error: new ApiError("superseded", 409),
      isFetching: true,
      refetch: vi.fn(),
    });
    render(<ChartView spec={makeSpec()} />);
    expect(screen.getByText("Loading…")).toBeTruthy();
  });
});

describe("zoom reset (nit)", () => {
  it("never queries a new chart with the previous chart's range", () => {
    ready(makeData({ x: [1, 2], y0: [1, 2] }, {}, { kinds: { x: "number" } }));
    const a = makeSpec({ id: "a", plotType: "line" });
    const { rerender } = render(<ChartView spec={a} />);
    act(() => lastEChartProps.onViewport?.([10, 20]));
    expect(useChartData.mock.lastCall?.[3]).toMatchObject({ xRange: [10, 20] });
    // Same chart, different spec id: the very first render of it must already have no range.
    useChartData.mockClear();
    rerender(<ChartView spec={{ ...a, id: "b" }} />);
    expect(useChartData.mock.calls[0]?.[3]).toMatchObject({ xRange: null });
  });

  it("keeps the range across cosmetic spec edits", () => {
    ready(makeData({ x: [1, 2], y0: [1, 2] }, {}, { kinds: { x: "number" } }));
    const a = makeSpec({ id: "a", plotType: "line" });
    const { rerender } = render(<ChartView spec={a} />);
    act(() => lastEChartProps.onViewport?.([10, 20]));
    useChartData.mockClear();
    rerender(<ChartView spec={{ ...a, name: "renamed", params: { barWidth: 30 } }} />);
    expect(useChartData.mock.calls[0]?.[3]).toMatchObject({ xRange: [10, 20] });
    expect(lastEChartProps.resetKey).toBeDefined();
  });
});

const hit = (name: string, extra: Record<string, unknown> = {}) => ({
  seriesIndex: 0,
  seriesName: "v",
  name,
  dataIndex: 0,
  value: 1,
  shiftKey: false,
  ...extra,
});

describe("drill-down", () => {
  const drillSpec = () =>
    makeSpec({
      id: "d",
      encodings: { x: [{ field: "country" }], y: [{ field: "sales", agg: "sum" }] },
      interactions: {
        zoom: true,
        brush: false,
        clickFilter: true,
        drillHierarchy: ["country", "state"],
      },
    });

  it("drills into a clicked category: next level, filtered to it, with a breadcrumb back", () => {
    ready(makeData({ x: ["DE", "FR"], y0: [1, 2] }, { x: "country" }));
    const onDrillChange = vi.fn();
    const onPointClick = vi.fn();
    render(
      <ChartView spec={drillSpec()} onDrillChange={onDrillChange} onPointClick={onPointClick} />,
    );
    expect(useChartData.mock.lastCall?.[0].encodings.x[0].field).toBe("country");

    act(() => lastEChartProps.onPointClick?.(hit("DE")));
    const [querySpec, queryFilters] = useChartData.mock.lastCall ?? [];
    expect(querySpec.encodings.x[0].field).toBe("state");
    expect(queryFilters).toEqual([{ op: "eq", field: "country", value: "DE" }]);
    expect(onDrillChange).toHaveBeenCalledWith(
      ["DE"],
      [{ op: "eq", field: "country", value: "DE" }],
    );
    expect(onPointClick).toHaveBeenCalledWith(
      expect.objectContaining({ field: "country", rawValue: "DE", drilled: true }),
    );
    expect(screen.getByText("Lowest level")).toBeTruthy();

    // At the leaf level a click does not drill, but is still reported.
    act(() => lastEChartProps.onPointClick?.(hit("DE")));
    expect(onDrillChange).toHaveBeenCalledTimes(1);
    expect(onPointClick).toHaveBeenLastCalledWith(expect.objectContaining({ drilled: false }));

    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(useChartData.mock.lastCall?.[0].encodings.x[0].field).toBe("country");
    expect(onDrillChange).toHaveBeenLastCalledWith([], []);
  });

  it("follows the drill path it is given when controlled", () => {
    ready(makeData({ x: ["a"], y0: [1] }, { x: "state" }));
    const { rerender } = render(<ChartView spec={drillSpec()} drill={["DE"]} />);
    expect(useChartData.mock.lastCall?.[1]).toEqual([{ op: "eq", field: "country", value: "DE" }]);
    act(() => lastEChartProps.onPointClick?.(hit("a")));
    // Controlled: nothing changes until the parent passes a new path.
    expect(useChartData.mock.lastCall?.[1]).toEqual([{ op: "eq", field: "country", value: "DE" }]);
    rerender(<ChartView spec={drillSpec()} drill={[]} />);
    expect(useChartData.mock.lastCall?.[0].encodings.x[0].field).toBe("country");
  });

  it("can drill from the table view, which a keyboard can reach", () => {
    ready(makeData({ x: ["DE", "FR"], y0: [1, 2] }, { x: "country" }));
    render(<ChartView spec={drillSpec()} />);
    fireEvent.click(screen.getByRole("button", { name: /View as table/ }));
    fireEvent.click(screen.getByRole("button", { name: "Drill into FR" }));
    expect(useChartData.mock.lastCall?.[1]).toEqual([{ op: "eq", field: "country", value: "FR" }]);
  });

  it("shows no breadcrumb or drilling for a chart without a hierarchy", () => {
    ready(makeData({ x: ["a"], y0: [1] }));
    const onPointClick = vi.fn();
    render(<ChartView spec={makeSpec()} onPointClick={onPointClick} />);
    expect(screen.queryByRole("navigation", { name: "Drill-down" })).toBeNull();
    act(() => lastEChartProps.onPointClick?.(hit("a")));
    expect(onPointClick).toHaveBeenCalledWith(expect.objectContaining({ drilled: false }));
  });
});

describe("temporal values in signals", () => {
  const dayData = () =>
    makeData(
      { x: [Date.UTC(2026, 0, 1), Date.UTC(2026, 0, 2)], color: ["e", "w"], y0: [1, 2] },
      { x: "day" },
      { kinds: { x: "temporal", color: "string" } },
    );
  const spec = () =>
    makeSpec({
      encodings: { x: [{ field: "day" }], color: [{ field: "r" }] },
      interactions: {
        zoom: true,
        brush: false,
        clickFilter: true,
        drillHierarchy: ["day", "hour"],
      },
    });

  it("reports ISO dates, never epoch ms, and does not drill into a date bucket", () => {
    ready(dayData());
    const onPointClick = vi.fn();
    const onDrillChange = vi.fn();
    render(<ChartView spec={spec()} onPointClick={onPointClick} onDrillChange={onDrillChange} />);
    act(() => lastEChartProps.onPointClick?.(hit("2026-01-02")));
    expect(onPointClick).toHaveBeenCalledWith(
      expect.objectContaining({ rawValue: "2026-01-02", drilled: false }),
    );
    expect(onDrillChange).not.toHaveBeenCalled();
  });

  it("reports ISO dates for a click on a [x, y] point of a time axis", () => {
    ready(dayData());
    const onPointClick = vi.fn();
    render(<ChartView spec={spec()} onPointClick={onPointClick} />);
    act(() => lastEChartProps.onPointClick?.(hit("", { value: [Date.UTC(2026, 0, 1), 5] })));
    expect(onPointClick).toHaveBeenCalledWith(expect.objectContaining({ rawValue: "2026-01-01" }));
  });

  it("does not offer table drill buttons for dates", () => {
    ready(dayData());
    render(<ChartView spec={spec()} />);
    fireEvent.click(screen.getByRole("button", { name: /View as table/ }));
    expect(screen.queryByRole("button", { name: /Drill into/ })).toBeNull();
  });
});

describe("brush lifecycle", () => {
  const brushSpec = (extra: Record<string, unknown> = {}) =>
    makeSpec({
      encodings: { x: [{ field: "year" }] },
      interactions: { zoom: true, brush: true, clickFilter: true, ...extra },
    });
  const drawn = {
    axis: "x" as const,
    kind: "category" as const,
    ranges: [],
    categories: ["2025"],
    cleared: false,
  };
  const cleared = { ...drawn, categories: [], cleared: true };
  const data = () => makeData({ x: [2024, 2025], y0: [1, 2] }, { x: "year" });

  it("reports a removal once, even though ECharts fires two empty events for it", () => {
    ready(data());
    const onBrushSelected = vi.fn();
    render(<ChartView spec={brushSpec()} onBrushSelected={onBrushSelected} />);
    act(() => lastEChartProps.onBrush?.(drawn));
    act(() => lastEChartProps.onBrush?.(cleared));
    act(() => lastEChartProps.onBrush?.(cleared));
    expect(onBrushSelected).toHaveBeenCalledTimes(2);
    expect(onBrushSelected).toHaveBeenLastCalledWith(expect.objectContaining({ cleared: true }));
  });

  it("does not report clearing when nothing was brushed", () => {
    ready(data());
    const onBrushSelected = vi.fn();
    render(<ChartView spec={brushSpec()} onBrushSelected={onBrushSelected} />);
    act(() => lastEChartProps.onBrush?.(cleared));
    expect(onBrushSelected).not.toHaveBeenCalled();
  });

  it("offers a keyboard-reachable Clear selection that removes the band and reports it", () => {
    ready(data());
    const onBrushSelected = vi.fn();
    render(<ChartView spec={brushSpec()} onBrushSelected={onBrushSelected} />);
    expect(screen.queryByRole("button", { name: "Clear selection" })).toBeNull();
    act(() => lastEChartProps.onBrush?.(drawn));
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(chartDispatch).toHaveBeenCalledWith({ type: "brush", areas: [], escapeConnect: true });
    expect(onBrushSelected).toHaveBeenLastCalledWith(
      expect.objectContaining({ cleared: true, field: "year" }),
    );
    expect(screen.queryByRole("button", { name: "Clear selection" })).toBeNull();
  });

  it("reports a cleared brush before a rebuild or table view would silently drop it", () => {
    ready(data());
    const onBrushSelected = vi.fn();
    const { rerender } = render(<ChartView spec={brushSpec()} onBrushSelected={onBrushSelected} />);
    act(() => lastEChartProps.onBrush?.(drawn));
    onBrushSelected.mockClear();
    // Brushing is switched off: the chart rebuilds and the band is gone.
    rerender(
      <ChartView
        spec={makeSpec({
          encodings: { x: [{ field: "year" }] },
          interactions: { zoom: true, brush: false, clickFilter: true },
        })}
        onBrushSelected={onBrushSelected}
      />,
    );
    expect(onBrushSelected).toHaveBeenCalledTimes(1);
    expect(onBrushSelected).toHaveBeenCalledWith(expect.objectContaining({ cleared: true }));

    // Table view unmounts the chart.
    act(() => lastEChartProps.onBrush?.(drawn));
    onBrushSelected.mockClear();
    rerender(<ChartView spec={brushSpec()} onBrushSelected={onBrushSelected} />);
    onBrushSelected.mockClear();
    act(() => lastEChartProps.onBrush?.(drawn));
    onBrushSelected.mockClear();
    fireEvent.click(screen.getByRole("button", { name: /View as table/ }));
    expect(onBrushSelected).toHaveBeenCalledWith(expect.objectContaining({ cleared: true }));
  });

  it("reports a cleared brush when another branch replaces the chart (no rows after a cross-filter)", () => {
    ready(data());
    const onBrushSelected = vi.fn();
    const { rerender } = render(<ChartView spec={brushSpec()} onBrushSelected={onBrushSelected} />);
    act(() => lastEChartProps.onBrush?.(drawn));
    onBrushSelected.mockClear();
    ready(makeData({ x: [], y0: [] }, { x: "year" }));
    rerender(<ChartView spec={brushSpec()} onBrushSelected={onBrushSelected} />);
    expect(screen.queryByTestId("echart")).toBeNull();
    expect(onBrushSelected).toHaveBeenCalledTimes(1);
    expect(onBrushSelected).toHaveBeenCalledWith(expect.objectContaining({ cleared: true }));
    // Rows come back: the chart returns with no band, and nothing more is reported.
    ready(data());
    rerender(<ChartView spec={brushSpec()} onBrushSelected={onBrushSelected} />);
    expect(onBrushSelected).toHaveBeenCalledTimes(1);
  });

  it("reports a cleared brush while the chart is loading again", () => {
    ready(data());
    const onBrushSelected = vi.fn();
    const { rerender } = render(<ChartView spec={brushSpec()} onBrushSelected={onBrushSelected} />);
    act(() => lastEChartProps.onBrush?.(drawn));
    onBrushSelected.mockClear();
    useChartData.mockReturnValue({
      data: undefined,
      error: null,
      isFetching: true,
      refetch: vi.fn(),
    });
    rerender(<ChartView spec={brushSpec()} onBrushSelected={onBrushSelected} />);
    expect(onBrushSelected).toHaveBeenCalledWith(expect.objectContaining({ cleared: true }));
  });

  it("reports a cleared brush when the chart goes away", () => {
    ready(data());
    const onBrushSelected = vi.fn();
    const { unmount } = render(<ChartView spec={brushSpec()} onBrushSelected={onBrushSelected} />);
    act(() => lastEChartProps.onBrush?.(drawn));
    onBrushSelected.mockClear();
    unmount();
    expect(onBrushSelected).toHaveBeenCalledWith(expect.objectContaining({ cleared: true }));
  });

  it("clears a brush when the chart is drilled", () => {
    ready(data());
    const onBrushSelected = vi.fn();
    render(
      <ChartView
        spec={brushSpec({ drillHierarchy: ["year", "month"] })}
        onBrushSelected={onBrushSelected}
      />,
    );
    act(() => lastEChartProps.onBrush?.(drawn));
    onBrushSelected.mockClear();
    act(() => lastEChartProps.onPointClick?.(hit("2025")));
    expect(onBrushSelected).toHaveBeenCalledWith(expect.objectContaining({ cleared: true }));
  });
});

describe("interaction signals for an embedder", () => {
  it("adds the field and raw values to brush and legend events", () => {
    ready(makeData({ x: [2024, 2025], color: ["east", "west"], y0: [1, 2] }, { x: "year" }));
    const spec = makeSpec({
      encodings: {
        x: [{ field: "year" }],
        y: [{ field: "n", agg: "sum" }],
        color: [{ field: "region" }],
      },
    });
    const onBrushSelected = vi.fn();
    const onLegendChange = vi.fn();
    render(
      <ChartView spec={spec} onBrushSelected={onBrushSelected} onLegendChange={onLegendChange} />,
    );
    act(() =>
      lastEChartProps.onBrush?.({
        axis: "x",
        kind: "category",
        ranges: [],
        categories: ["2025"],
        cleared: false,
      }),
    );
    expect(onBrushSelected).toHaveBeenCalledWith(
      expect.objectContaining({ field: "year", values: [2025] }),
    );
    act(() =>
      lastEChartProps.onLegendChange?.({
        toggled: "west",
        selected: { east: true, west: false },
        shown: ["east"],
        hidden: ["west"],
      }),
    );
    expect(onLegendChange).toHaveBeenCalledWith(
      expect.objectContaining({ field: "region", shownValues: ["east"], hiddenValues: ["west"] }),
    );
  });

  it("joins the chart's link group and passes the slot through", () => {
    ready(makeData({ x: ["a"], y0: [1] }));
    const spec = makeSpec({
      interactions: { zoom: true, brush: false, clickFilter: true, linkGroup: "g1" },
    });
    render(<ChartView spec={spec} slot="widget-2" />);
    expect(lastEChartProps.linkGroup).toBe("g1");
    expect(useChartData.mock.lastCall?.[3]).toMatchObject({ slot: "widget-2" });
  });

  it("resets the chart when brushing is switched on or off", () => {
    ready(makeData({ x: ["a"], y0: [1] }));
    const { rerender } = render(<ChartView spec={makeSpec()} />);
    const before = lastEChartProps.resetKey;
    rerender(
      <ChartView
        spec={makeSpec({ interactions: { zoom: true, brush: true, clickFilter: true } })}
      />,
    );
    expect(lastEChartProps.resetKey).not.toBe(before);
  });
});

describe("view as table for stat results", () => {
  it("renders a StatResult as tables, not JSON", () => {
    const data = {
      ...makeData({}, {}),
      stat: {
        values: { auc: 0.91, fpr: [0, 1], tpr: [0, 1] },
        method: "roc",
        n: 20,
        nMissing: 0,
        sampled: false,
        warnings: [],
      },
    };
    useChartData.mockReturnValue({ data, error: null, isFetching: false, refetch: vi.fn() });
    render(<ChartView spec={makeSpec({ plotType: "roc" })} />);
    fireEvent.click(screen.getByRole("button", { name: /View as table/ }));
    expect(screen.getAllByRole("table").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByRole("columnheader", { name: "tpr" })).toBeTruthy();
    expect(screen.getByText("0.91")).toBeTruthy();
    expect(screen.getByText("roc, n = 20")).toBeTruthy();
  });
});

describe("export sink", () => {
  it("exports through the sink provided by context", async () => {
    ready(makeData({ x: ["a"], y0: [1] }));
    const sink = { save: vi.fn(), copyImage: vi.fn() };
    render(
      <ExportSinkProvider sink={sink}>
        <ChartView spec={makeSpec()} />
      </ExportSinkProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /Export/ }));
    await act(async () => {
      fireEvent.click(screen.getByRole("menuitem", { name: "PNG image" }));
    });
    expect(saveChart).toHaveBeenCalledTimes(1);
    expect((saveChart.mock.calls[0] as unknown[])[2]).toBe(sink);
  });
});
