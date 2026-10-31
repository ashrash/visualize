// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import type { ECharts } from "echarts/core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const zrHandlers = new Map<string, () => void>();
const zr = {
  on: (e: string, h: () => void) => zrHandlers.set(e, h),
  off: (e: string) => zrHandlers.delete(e),
};
const chart = {
  setOption: vi.fn(),
  setTheme: vi.fn(),
  getOption: vi.fn(() => ({})),
  on: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  dispatchAction: vi.fn(),
  getZr: () => zr,
  group: "",
};
const { connect, disconnect } = vi.hoisted(() => ({ connect: vi.fn(), disconnect: vi.fn() }));

// The real list of component main types is exercised in render.test.ts; here a stand-in.
const COMPONENTS = [
  "series",
  "title",
  "legend",
  "tooltip",
  "dataZoom",
  "xAxis",
  "yAxis",
  "grid",
  "aria",
];
vi.mock("./echarts", () => ({
  echarts: {
    init: vi.fn(() => chart),
    ComponentModel: { hasClass: (type: string) => COMPONENTS.includes(type) },
    connect,
    disconnect,
  },
}));

import { EChart, visibleRange } from "./EChart";

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  for (const fn of Object.values(chart)) if (vi.isMockFunction(fn)) fn.mockClear();
  zrHandlers.clear();
  chart.group = "";
  connect.mockClear();
  disconnect.mockClear();
  chart.getOption.mockImplementation(() => ({}));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const asChart = () => chart as unknown as ECharts;

describe("visibleRange", () => {
  it("reads the x axis for a vertical chart with a numeric axis", () => {
    chart.getOption.mockImplementation(() => ({
      xAxis: [{ type: "time" }],
      yAxis: [{ type: "value" }],
      dataZoom: [{ xAxisIndex: [0], start: 10, end: 60, startValue: 100, endValue: 200 }],
    }));
    expect(visibleRange(asChart())).toEqual([100, 200]);
  });

  it("returns null when not zoomed", () => {
    chart.getOption.mockImplementation(() => ({
      xAxis: [{ type: "value" }],
      dataZoom: [{ xAxisIndex: [0], start: 0, end: 100, startValue: 0, endValue: 9 }],
    }));
    expect(visibleRange(asChart())).toBeNull();
  });

  it("never reports a range for a horizontal chart whose dataZoom targets the category y axis", () => {
    // Regression: it used to read xAxis[0] (the value axis) and send category indices as xRange.
    chart.getOption.mockImplementation(() => ({
      xAxis: [{ type: "value" }],
      yAxis: [{ type: "category" }],
      dataZoom: [{ yAxisIndex: [0], start: 20, end: 70, startValue: 1, endValue: 3 }],
    }));
    expect(visibleRange(asChart())).toBeUndefined();
  });

  it("does report a numeric y axis a dataZoom controls, and ignores category x axes", () => {
    chart.getOption.mockImplementation(() => ({
      xAxis: [{ type: "category" }],
      dataZoom: [{ xAxisIndex: [0], start: 20, end: 70, startValue: 1, endValue: 3 }],
    }));
    expect(visibleRange(asChart())).toBeUndefined();
    chart.getOption.mockImplementation(() => ({
      yAxis: [{ type: "value" }],
      dataZoom: [{ yAxisIndex: [0], start: 20, end: 70, startValue: 5, endValue: 8 }],
    }));
    expect(visibleRange(asChart())).toEqual([5, 8]);
  });

  it("returns undefined without a dataZoom", () => {
    expect(visibleRange(asChart())).toBeUndefined();
  });
});

describe("EChart", () => {
  it("enables aria for every plot and lets the description keep the container's aria-label", () => {
    const { container } = render(<EChart option={{ series: [] }} ariaLabel="Sales by region" />);
    const [option] = chart.setOption.mock.calls[0] as [Record<string, unknown>];
    expect(option.aria).toEqual({ enabled: true, label: { description: "Sales by region" } });
    expect(container.querySelector("[role=img]")?.getAttribute("aria-label")).toBe(
      "Sales by region",
    );
  });

  it("keeps a plot's own aria settings but still turns aria on", () => {
    render(<EChart option={{ aria: { decal: { show: true } } }} />);
    const [option] = chart.setOption.mock.calls[0] as [Record<string, unknown>];
    expect(option.aria).toMatchObject({ enabled: true, decal: { show: true } });
  });

  it("rebuilds without a resetKey, and merges while the resetKey is unchanged", () => {
    const { rerender } = render(
      <EChart option={{ title: { text: "a" }, dataZoom: [{}] }} resetKey="k1" />,
    );
    expect(chart.setOption.mock.calls[0]?.[1]).toEqual({ notMerge: true });
    chart.getOption.mockImplementation(() => ({ legend: [{}], dataZoom: [{}] }));
    rerender(
      <EChart option={{ title: { text: "b" }, series: [], dataZoom: [{}] }} resetKey="k1" />,
    );
    const second = chart.setOption.mock.calls[1]?.[1] as {
      notMerge: boolean;
      replaceMerge: string[];
    };
    expect(second.notMerge).toBe(false);
    // dataZoom is not replaced: that is what preserves the user's zoom.
    // Only component types in use (now or before) are listed: unregistered ones would throw.
    expect(second.replaceMerge).toEqual(expect.arrayContaining(["series", "title", "legend"]));
    expect(second.replaceMerge).not.toContain("color"); // not a component
    expect(second.replaceMerge).not.toContain("dataZoom");
    rerender(<EChart option={{ title: { text: "c" } }} resetKey="k2" />);
    expect(chart.setOption.mock.calls[2]?.[1]).toEqual({ notMerge: true });
  });

  it("replaces dataZoom only when its shape changes, so a refetch keeps the user's zoom", () => {
    const inside = [{ type: "inside", xAxisIndex: 0 }];
    const withSlider = [...inside, { type: "slider", xAxisIndex: 0 }];
    chart.getOption.mockImplementation(() => ({ dataZoom: [{}] }));
    const { rerender } = render(
      <EChart option={{ series: [], dataZoom: withSlider }} resetKey="k" />,
    );
    // Same shape, new zoom position: merged, never replaced.
    rerender(<EChart option={{ series: [], dataZoom: withSlider }} resetKey="k" />);
    const same = chart.setOption.mock.calls[1]?.[1] as { replaceMerge: string[] };
    expect(same.replaceMerge).not.toContain("dataZoom");
    // The slider goes away (fewer points after a refetch): replaced.
    rerender(<EChart option={{ series: [], dataZoom: inside }} resetKey="k" />);
    const changed = chart.setOption.mock.calls[2]?.[1] as { replaceMerge: string[] };
    expect(changed.replaceMerge).toContain("dataZoom");
  });

  it("always rebuilds when no resetKey is given", () => {
    const { rerender } = render(<EChart option={{ a: 1 }} />);
    rerender(<EChart option={{ a: 2 }} />);
    expect(chart.setOption.mock.calls[1]?.[1]).toEqual({ notMerge: true });
  });

  it("carries the user's legend toggles across a cosmetic update", () => {
    const { rerender } = render(<EChart option={{ legend: { data: ["a", "b"] } }} resetKey="k" />);
    chart.getOption.mockImplementation(() => ({ legend: [{ selected: { a: true, b: false } }] }));
    rerender(
      <EChart option={{ legend: { data: ["a", "b"] }, title: { text: "x" } }} resetKey="k" />,
    );
    const [option] = chart.setOption.mock.calls[1] as [
      { legend: { selected: Record<string, boolean> } },
    ];
    expect(option.legend.selected).toEqual({ a: true, b: false });
  });

  it("re-themes in place", () => {
    const theme = {
      palette: ["#111"],
      text: "#000",
      textMuted: "#555",
      border: "#ccc",
      fontFamily: "x",
    };
    const { rerender } = render(<EChart option={{}} theme={theme} />);
    expect(chart.setTheme).not.toHaveBeenCalled();
    rerender(<EChart option={{}} theme={{ ...theme, mode: "dark", text: "#fff" }} />);
    expect(chart.setTheme).toHaveBeenCalledTimes(1);
  });

  const handler = (name: string) =>
    (chart.on.mock.calls as unknown as [string, (e: unknown) => void][]).find(
      ([event]) => event === name,
    )?.[1];

  it("joins its link group, follows a change of group, and leaves on unmount", () => {
    const { rerender, unmount } = render(<EChart option={{}} linkGroup="g1" />);
    expect(chart.group).toBe("g1");
    expect(connect).toHaveBeenCalledWith("g1");
    rerender(<EChart option={{}} linkGroup="g2" />);
    expect(chart.group).toBe("g2");
    expect(disconnect).toHaveBeenCalledWith("g1");
    unmount();
    expect(disconnect).toHaveBeenCalledWith("g2");
  });

  it("is in no group without one", () => {
    render(<EChart option={{}} />);
    expect(connect).not.toHaveBeenCalled();
  });

  it("reports series clicks, legend toggles and brushes to its callbacks", () => {
    chart.getOption.mockImplementation(() => ({
      xAxis: [{ type: "category", data: ["a", "b", "c"] }],
    }));
    const onPointClick = vi.fn();
    const onLegendChange = vi.fn();
    const onBrush = vi.fn();
    render(
      <EChart
        option={{}}
        onPointClick={onPointClick}
        onLegendChange={onLegendChange}
        onBrush={onBrush}
      />,
    );
    // Legend and brush events only count while the pointer is on this chart.
    handler("legendselectchanged")?.({ name: "s", selected: { s: true } });
    handler("brushEnd")?.({ areas: [{ brushType: "lineX", coordRange: [0, 1] }] });
    expect(onLegendChange).not.toHaveBeenCalled();
    expect(onBrush).not.toHaveBeenCalled();
    zrHandlers.get("mousedown")?.();
    handler("click")?.({ componentType: "series", seriesName: "s", name: "b", dataIndex: 1 });
    handler("click")?.({ componentType: "title" });
    expect(onPointClick).toHaveBeenCalledTimes(1);
    expect(onPointClick).toHaveBeenCalledWith(expect.objectContaining({ name: "b", dataIndex: 1 }));

    handler("legendselectchanged")?.({ name: "s", selected: { s: false } });
    expect(onLegendChange).toHaveBeenCalledWith(expect.objectContaining({ hidden: ["s"] }));

    handler("brushEnd")?.({ areas: [{ brushType: "lineX", coordRange: [1, 2] }] });
    expect(onBrush).toHaveBeenCalledWith(expect.objectContaining({ categories: ["b", "c"] }));
    // `brush` fires while dragging: only its "removed" case counts.
    handler("brush")?.({ areas: [{ brushType: "lineX", coordRange: [0, 1] }] });
    expect(onBrush).toHaveBeenCalledTimes(1);
    handler("brush")?.({ areas: [] });
    expect(onBrush).toHaveBeenLastCalledWith(expect.objectContaining({ cleared: true }));
  });

  it("switches the brush tool on when the option has a brush", () => {
    render(<EChart option={{ brush: { brushType: "lineY" } }} />);
    expect(chart.dispatchAction).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "takeGlobalCursor",
        brushOption: expect.objectContaining({ brushType: "lineY" }),
      }),
    );
  });
});
