// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeData, makeSpec } from "./testing";

// A plot whose `prepare` reads a param (like a choropleth's map level): readiness must follow it.
const prepares: { level: unknown; resolve(): void }[] = [];
const renders: unknown[] = [];
const fakeEntry = {
  key: "choropleth",
  label: "Choropleth",
  family: "geo",
  encodingsSchema: [],
  paramsSchema: [],
  styleSections: [],
  defaults: {},
  prepare: (spec: { params?: Record<string, unknown> }) =>
    new Promise<void>((resolve) => {
      prepares.push({ level: spec.params?.level, resolve });
    }),
  render: (spec: { params?: Record<string, unknown> }) => {
    renders.push(spec.params?.level);
    return { series: [] };
  },
};
vi.mock("./registry", () => ({ getPlot: () => fakeEntry, listPlots: () => [fakeEntry] }));

const useChartData = vi.fn();
vi.mock("./data", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./data")>()),
  useChartData: (...a: unknown[]) => useChartData(...a),
  useChartOverlays: () => ({ data: undefined }),
}));
vi.mock("./EChart", () => ({ EChart: () => <div data-testid="echart" /> }));

import { ChartView } from "./ChartView";

beforeEach(() => {
  prepares.length = 0;
  renders.length = 0;
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
  useChartData.mockReturnValue({
    data: makeData({ x: ["a"], y0: [1] }),
    error: null,
    isFetching: false,
    refetch: vi.fn(),
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ChartView asset readiness", () => {
  it("waits for the new map when a param prepare reads changes, instead of staying blank", async () => {
    const spec = (level: string) =>
      makeSpec({ plotType: "choropleth" as never, params: { level } });
    const { rerender } = render(<ChartView spec={spec("country")} />);
    expect(screen.getByText("Loading…")).toBeTruthy();
    await act(async () => prepares[0]?.resolve());
    expect(screen.getByTestId("echart")).toBeTruthy();

    rerender(<ChartView spec={spec("admin1")} />);
    // The new level's map is not loaded yet: do not draw with the old one.
    expect(screen.queryByTestId("echart")).toBeNull();
    await act(async () => prepares.at(-1)?.resolve());
    expect(screen.getByTestId("echart")).toBeTruthy();
    expect(renders.at(-1)).toBe("admin1");
  });

  it("with prepareParams, only those params wait; other param edits keep the chart", async () => {
    (fakeEntry as { prepareParams?: string[] }).prepareParams = ["level"];
    try {
      const spec = (level: string, bubbles: number) =>
        makeSpec({ plotType: "choropleth" as never, params: { level, bubbles } });
      const { rerender } = render(<ChartView spec={spec("country", 1)} />);
      await act(async () => prepares[0]?.resolve());
      rerender(<ChartView spec={spec("country", 2)} />);
      expect(screen.getByTestId("echart")).toBeTruthy();
      rerender(<ChartView spec={spec("admin1", 2)} />);
      expect(screen.queryByTestId("echart")).toBeNull();
    } finally {
      (fakeEntry as { prepareParams?: string[] }).prepareParams = undefined;
    }
  });

  it("does not drop the chart for a cosmetic edit", async () => {
    const spec = (name: string) => makeSpec({ plotType: "choropleth" as never, name, params: {} });
    const { rerender } = render(<ChartView spec={spec("a")} />);
    await act(async () => prepares[0]?.resolve());
    rerender(<ChartView spec={spec("b")} />);
    expect(screen.getByTestId("echart")).toBeTruthy();
  });
});
