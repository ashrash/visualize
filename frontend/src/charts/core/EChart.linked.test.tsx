// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Two EChart instances in one link group. `echarts.connect` replays brush and legend actions of
// one member on the others, which then emit the same events; the fakes below do the same.
interface FakeChart {
  handlers: Map<string, ((e: unknown) => void)[]>;
  zr: Map<string, (() => void)[]>;
  group: string;
  options: unknown;
  on(event: string, handler: (e: unknown) => void): void;
  getZr(): { on(e: string, h: () => void): void; off(e: string, h: () => void): void };
  getOption(): unknown;
  setOption(o: unknown): void;
  setTheme(): void;
  resize(): void;
  dispose(): void;
  dispatchAction(): void;
}

const { charts } = vi.hoisted(() => ({ charts: [] as unknown[] }));
const all = () => charts as FakeChart[];

vi.mock("./echarts", () => {
  const make = (): FakeChart => {
    const chart: FakeChart = {
      handlers: new Map(),
      zr: new Map(),
      group: "",
      options: { xAxis: [{ type: "category", data: ["a", "b", "c", "d"] }] },
      on(event, handler) {
        chart.handlers.set(event, [...(chart.handlers.get(event) ?? []), handler]);
      },
      getZr: () => ({
        on: (e, h) => chart.zr.set(e, [...(chart.zr.get(e) ?? []), h]),
        off: (e, h) =>
          chart.zr.set(
            e,
            (chart.zr.get(e) ?? []).filter((x) => x !== h),
          ),
      }),
      getOption: () => chart.options,
      setOption: (o) => {
        chart.options = { ...(chart.options as object), ...(o as object) };
      },
      setTheme() {},
      resize() {},
      dispose() {},
      dispatchAction() {},
    };
    charts.push(chart);
    return chart;
  };
  return {
    echarts: {
      init: () => make(),
      connect() {},
      disconnect() {},
      ComponentModel: { hasClass: () => false },
    },
  };
});

import { EChart } from "./EChart";

/** What echarts.connect does: the event fires on the source and on every other group member. */
function forwarded(source: FakeChart, event: string, payload: unknown) {
  for (const chart of all()) {
    if (chart !== source && chart.group !== source.group) continue;
    for (const handler of chart.handlers.get(event) ?? []) handler(payload);
  }
}
const press = (chart: FakeChart) => {
  for (const handler of chart.zr.get("mousedown") ?? []) handler();
  for (const handler of chart.zr.get("mouseup") ?? []) handler();
};

beforeEach(() => {
  charts.length = 0;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("linked EChart instances", () => {
  const setup = () => {
    const a = { brush: vi.fn(), legend: vi.fn() };
    const b = { brush: vi.fn(), legend: vi.fn() };
    render(
      <>
        <EChart option={{}} linkGroup="g" onBrush={a.brush} onLegendChange={a.legend} />
        <EChart option={{}} linkGroup="g" onBrush={b.brush} onLegendChange={b.legend} />
      </>,
    );
    const [chartA, chartB] = all();
    if (!chartA || !chartB) throw new Error("two charts expected");
    return { a, b, chartA, chartB };
  };

  it("reports a brush only on the chart the user brushed, not its linked neighbour", () => {
    const { a, b, chartA, chartB } = setup();
    expect(chartA.group).toBe("g");
    expect(chartB.group).toBe("g");
    press(chartA);
    // A's band spans categories 0..1 of A's own axis; the replay lands on B's different axis.
    forwarded(chartA, "brushEnd", { areas: [{ brushType: "lineX", coordRange: [0, 1] }] });
    expect(a.brush).toHaveBeenCalledTimes(1);
    expect(a.brush).toHaveBeenCalledWith(expect.objectContaining({ categories: ["a", "b"] }));
    expect(b.brush).not.toHaveBeenCalled();
    // And the other way round.
    press(chartB);
    forwarded(chartB, "brushEnd", { areas: [{ brushType: "lineX", coordRange: [2, 3] }] });
    expect(b.brush).toHaveBeenCalledTimes(1);
    expect(a.brush).toHaveBeenCalledTimes(1);
  });

  it("reports a legend toggle only on the chart whose legend was clicked", () => {
    const { a, b, chartA } = setup();
    press(chartA);
    forwarded(chartA, "legendselectchanged", { name: "s", selected: { s: false } });
    expect(a.legend).toHaveBeenCalledTimes(1);
    expect(b.legend).not.toHaveBeenCalled();
  });

  it("ignores a replayed removal on the neighbour too", () => {
    const { a, b, chartA } = setup();
    press(chartA);
    forwarded(chartA, "brush", { areas: [] });
    expect(a.brush).toHaveBeenCalledWith(expect.objectContaining({ cleared: true }));
    expect(b.brush).not.toHaveBeenCalled();
  });
});
