import type { ECharts } from "echarts/core";
import { describe, expect, it, vi } from "vitest";
import {
  areaToSpan,
  attachChartEvents,
  brushSpanFromPixels,
  clickToPoint,
  hasXAxis,
} from "./chartEvents";

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
  it("forwards clicks and legend changes, and detaches cleanly", () => {
    const { chart, emit, listeners } = fakeChart();
    const handlers = { onPoint: vi.fn(), onLegend: vi.fn() };
    const detach = attachChartEvents(chart, () => handlers);

    emit("click", { componentType: "series", name: "N", seriesName: "web" });
    expect(handlers.onPoint).toHaveBeenCalledWith(
      { name: "N", seriesName: "web", axisValue: null },
      false,
    );

    emit("legendselectchanged", { selected: { web: false, app: true } });
    expect(handlers.onLegend).toHaveBeenCalledWith({ web: false, app: true });

    detach();
    for (const set of listeners.values()) expect(set.size).toBe(0);
  });
});

describe("brushSpanFromPixels", () => {
  const withAxis = (xAxis: unknown, convert: (x: number) => unknown) =>
    ({
      getOption: () => ({ xAxis: [xAxis] }),
      convertFromPixel: (_finder: unknown, x: number) => convert(x),
    }) as unknown as ECharts;

  it("converts a pixel drag on a value axis, whichever way it was dragged", () => {
    const chart = withAxis({ type: "value" }, (x) => x / 10);
    expect(brushSpanFromPixels(chart, 80, 20)).toEqual({ min: 2, max: 8 });
  });

  it("maps a drag across a category axis to the categories under it", () => {
    const chart = withAxis({ type: "category", data: ["N", "S", "E", "W"] }, (x) => x / 100);
    expect(brushSpanFromPixels(chart, 90, 210)).toEqual({ categories: ["S", "E"] });
  });

  it("is null when the pixels are outside the axis", () => {
    expect(
      brushSpanFromPixels(
        withAxis({ type: "value" }, () => Number.NaN),
        0,
        10,
      ),
    ).toBeNull();
    expect(
      brushSpanFromPixels(
        withAxis({ type: "value" }, () => [1, 2]),
        0,
        10,
      ),
    ).toBeNull();
  });

  it("knows whether a chart has an x axis", () => {
    expect(hasXAxis(withAxis({ type: "value" }, () => 0))).toBe(true);
    expect(hasXAxis({ getOption: () => ({}) } as unknown as ECharts)).toBe(false);
  });
});
