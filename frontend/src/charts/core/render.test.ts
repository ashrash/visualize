// @vitest-environment node
import { describe, expect, it } from "vitest";
import { echarts } from "./echarts";
import { ciBand } from "./primitives/ciBand";
import { getPlot } from "./registry";
import { makeData, makeSpec, makeTheme } from "./testing";

// These tests render through real ECharts (SVG, server-side style) and assert on what it
// computed or drew, not just on the option object a plot returned.

function ssr(option: Record<string, unknown>) {
  const chart = echarts.init(null, undefined, {
    renderer: "svg",
    ssr: true,
    width: 400,
    height: 300,
  });
  chart.setOption({ animation: false, ...option });
  return chart;
}

type StackedData = {
  getCalculationInfo(key: string): string;
  get(dim: string, index: number): number;
};

function stackedTops(chart: ReturnType<typeof ssr>, seriesIndex: number, count: number) {
  const model = (
    chart as unknown as { getModel(): { getSeriesByIndex(i: number): { getData(): StackedData } } }
  ).getModel();
  const data = model.getSeriesByIndex(seriesIndex).getData();
  const dim = data.getCalculationInfo("stackResultDimension");
  return Array.from({ length: count }, (_, i) => data.get(dim, i));
}

describe("ciBand through ECharts", () => {
  it("stacks the band onto a negative lower bound (top = hi, not hi - lo)", () => {
    const x = ["a", "b", "c"];
    const chart = ssr({
      xAxis: { type: "category", data: x },
      yAxis: { type: "value" },
      series: ciBand({ x, lo: [-2, -1, 1], hi: [2, 3, 4], axis: "category" }),
    });
    // Regression: with the default "samesign" strategy this came out as [4, 4, 4].
    expect(stackedTops(chart, 1, 3)).toEqual([2, 3, 4]);
    expect(stackedTops(chart, 0, 3)).toEqual([-2, -1, 1]);
    chart.dispose();
  });

  it("control: without stackStrategy 'all' ECharts draws the wrong band (why it is set)", () => {
    const x = ["a", "b", "c"];
    const series = ciBand({ x, lo: [-2, -1, 1], hi: [2, 3, 4], axis: "category" }).map((s) => {
      const { stackStrategy: _drop, ...rest } = s;
      return rest;
    });
    const chart = ssr({ xAxis: { type: "category", data: x }, yAxis: { type: "value" }, series });
    expect(stackedTops(chart, 1, 3)).toEqual([4, 4, 4]);
    chart.dispose();
  });

  it("is correct when the whole band is below zero", () => {
    const x = ["a", "b"];
    const chart = ssr({
      xAxis: { type: "category", data: x },
      yAxis: { type: "value" },
      series: ciBand({ x, lo: [-5, -4], hi: [-1, -2], axis: "category" }),
    });
    expect(stackedTops(chart, 1, 2)).toEqual([-1, -2]);
    chart.dispose();
  });
});

describe("merge-mode updates (EChart resetKey path)", () => {
  const option = (title: string) => ({
    title: { text: title },
    xAxis: { type: "category", data: ["a", "b"] },
    yAxis: { type: "value" },
    series: [{ type: "bar", data: [1, 2] }],
  });

  it("accepts replaceMerge for the registered components in use", () => {
    const chart = ssr(option("one"));
    expect(() =>
      chart.setOption(option("two"), {
        notMerge: false,
        replaceMerge: ["series", "xAxis", "yAxis", "title"],
      }),
    ).not.toThrow();
    chart.dispose();
  });

  it("rejects unregistered component types, which is why EChart filters the list", () => {
    const chart = ssr(option("one"));
    expect(() =>
      chart.setOption(option("two"), { notMerge: false, replaceMerge: ["series", "polar"] }),
    ).toThrow(/polar/);
    chart.dispose();
  });
});

describe("donut through ECharts", () => {
  it("draws the total in the hole (GraphicComponent is registered)", () => {
    const entry = getPlot("donut");
    if (!entry) throw new Error("donut missing");
    const data = makeData({ x: ["a", "b"], y0: [1234, 4321] }, { x: "k", y0: "v" });
    const option = entry.render(makeSpec({ plotType: "donut" }), data, makeTheme()) as Record<
      string,
      unknown
    >;
    const chart = ssr(option);
    const svg = chart.renderToSVGString();
    // 1234 + 4321 = 5555, formatted by the default number formatter.
    expect(svg).toContain("5,555");
    chart.dispose();
  });
});
