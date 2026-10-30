// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ExportSinkProvider } from "../../export/sinkContext";
import { makeData, makeSpec } from "./testing";

const useChartData = vi.fn();
const useChartOverlays = vi.fn(() => ({ data: undefined }));
vi.mock("./data", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./data")>()),
  useChartData: (...a: unknown[]) => useChartData(...a),
  useChartOverlays: () => useChartOverlays(),
}));

let lastEChartProps: { onViewport?(r: [number, number] | null): void; resetKey?: string } = {};
vi.mock("./EChart", () => ({
  EChart: (props: {
    onReady?(c: unknown): void;
    onViewport?(r: [number, number] | null): void;
    resetKey?: string;
  }) => {
    lastEChartProps = props;
    useEffect(() => {
      props.onReady?.({});
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
