import type { ECharts } from "echarts/core";
import { describe, expect, it, vi } from "vitest";
import { areaToSpan, attachChartEvents, clickToPoint, ensureBrush } from "./chartEvents";

type Listener = (params: unknown) => void;

function fakeChart(option: Record<string, unknown> = {}) {
  const listeners = new Map<string, Set<Listener>>();
  const setOption = vi.fn((o: Record<string, unknown>) => Object.assign(option, o));
  const chart = {
    on: (name: string, fn: Listener) => {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name)?.add(fn);
    },
    off: (name: string, fn: Listener) => listeners.get(name)?.delete(fn),
    getOption: () => option,
    setOption,
  } as unknown as ECharts;
  const emit = (name: string, params?: unknown) => {
    for (const fn of listeners.get(name) ?? []) fn(params);
  };
  return { chart, emit, setOption, listeners };
}

describe("clickToPoint", () => {
  it("reduces a series click, dropping ECharts' generated series names", () => {
    expect(clickToPoint({ componentType: "series", name: "North", seriesName: "series0" })).toEqual(
      {
        click: { name: "North", seriesName: null, axisValue: null },
        extend: false,
      },
    );
    expect(
      clickToPoint({
        componentType: "series",
        name: "Jan",
        seriesName: "web",
        value: [1704067200000, 5],
        event: { event: { shiftKey: true } },
      }),
    ).toEqual({
      click: { name: "Jan", seriesName: "web", axisValue: 1704067200000 },
      extend: true,
    });
  });

  it("ignores clicks on non-series components (axis, legend)", () => {
    expect(clickToPoint({ componentType: "xAxis", name: "x" })).toBeNull();
  });
});

describe("areaToSpan", () => {
  it("returns a numeric span for value axes", () => {
    expect(areaToSpan({ coordRange: [9, 3] }, { type: "value" })).toEqual({ min: 3, max: 9 });
  });
  it("maps category indexes to names", () => {
    expect(
      areaToSpan({ coordRange: [1, 2.2] }, { type: "category", data: ["a", "b", "c", "d"] }),
    ).toEqual({
      categories: ["b", "c"],
    });
    expect(areaToSpan({ coordRange: [-3, 99] }, { type: "category", data: ["a", "b"] })).toEqual({
      categories: ["a", "b"],
    });
  });
  it("returns null without a range", () => {
    expect(areaToSpan({}, undefined)).toBeNull();
  });
});

describe("attachChartEvents", () => {
  it("forwards clicks, legend changes and brushes, and detaches cleanly", () => {
    const { chart, emit, listeners } = fakeChart({ xAxis: [{ type: "value" }] });
    const handlers = { onPoint: vi.fn(), onLegend: vi.fn(), onBrush: vi.fn() };
    const detach = attachChartEvents(chart, () => handlers, { brush: true });

    emit("click", { componentType: "series", name: "N", seriesName: "web" });
    expect(handlers.onPoint).toHaveBeenCalledWith(
      { name: "N", seriesName: "web", axisValue: null },
      false,
    );

    emit("legendselectchanged", { selected: { web: false, app: true } });
    expect(handlers.onLegend).toHaveBeenCalledWith({ web: false, app: true });

    emit("brushEnd", { areas: [{ coordRange: [2, 8] }] });
    expect(handlers.onBrush).toHaveBeenCalledWith({ min: 2, max: 8 });
    emit("brushEnd", { areas: [] });
    expect(handlers.onBrush).toHaveBeenLastCalledWith(null);

    detach();
    for (const set of listeners.values()) expect(set.size).toBe(0);
  });

  it("does not listen for brushes unless the chart asks for them", () => {
    const { chart, listeners, setOption } = fakeChart();
    attachChartEvents(chart, () => ({}), { brush: false });
    expect(listeners.get("brushEnd")?.size ?? 0).toBe(0);
    expect(setOption).not.toHaveBeenCalled();
  });
});

describe("ensureBrush", () => {
  it("adds the brush tool once, and puts it back after a rebuild drops it", () => {
    const option: Record<string, unknown> = {};
    const { chart, setOption } = fakeChart(option);
    ensureBrush(chart, true);
    expect(setOption).toHaveBeenCalledTimes(1);
    ensureBrush(chart, true); // already there (option now has brush)
    expect(setOption).toHaveBeenCalledTimes(1);
    option.brush = undefined; // a rebuild
    ensureBrush(chart, true);
    expect(setOption).toHaveBeenCalledTimes(2);
  });
});
