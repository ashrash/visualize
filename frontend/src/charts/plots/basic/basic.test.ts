import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../../../api/types";
import { makeData, makeSpec, makeTheme } from "../../core/testing";
import type { OverlayResult, PlotEntry } from "../../core/types";
import entries from "./index";

const byKey = (key: string): PlotEntry => {
  const entry = entries.find((e) => e.key === key);
  if (!entry) throw new Error(`no ${key}`);
  return entry;
};

const theme = makeTheme();
// biome-ignore lint/suspicious/noExplicitAny: tests probe deep into the ECharts option
type Opt = Record<string, any>;

const style = (patch: Record<string, unknown>) => ({ style: patch as ChartSpec["style"] });

const simple = makeData(
  { x: ["North", "South", "East"], y0: [330.5, 135.25, 80] },
  { x: "region", y0: "Sum of sales" },
);

const grouped = makeData(
  { x: ["N", "N", "S"], color: ["A", "B", "A"], y0: [1, 2, 3] },
  { x: "region", color: "product", y0: "Sum of sales" },
);

const render = (key: string, spec: Partial<ChartSpec>, data = simple, ctx = undefined as never) =>
  byKey(key).render(makeSpec({ plotType: key as never, ...spec }), data, theme, ctx) as Opt;

describe("bar family", () => {
  it("draws one series per measure with the category axis", () => {
    const o = render("bar", {});
    expect(o.xAxis.data).toEqual(["North", "South", "East"]);
    expect(o.xAxis.type).toBe("category");
    expect(o.series).toHaveLength(1);
    expect(o.series[0]).toMatchObject({ type: "bar", name: "Sum of sales", data: [330.5, 135.25, 80] });
    expect(o.series[0].yIndex).toBeUndefined();
    expect(o.legend).toBeUndefined();
    expect(o.yAxis.name).toBe("Sum of sales");
  });

  it("pivots the color channel into grouped series with nulls for gaps", () => {
    const o = render("grouped_bar", {}, grouped);
    expect(o.xAxis.data).toEqual(["N", "S"]);
    expect(o.series.map((s: Opt) => [s.name, s.data, s.stack])).toEqual([
      ["A", [1, 3], undefined],
      ["B", [2, null], undefined],
    ]);
    expect(o.legend.show).toBe(true);
  });

  it("stacks, and normalizes 100% stacks to fractions with a percent axis", () => {
    const stacked = render("stacked_bar", {}, grouped);
    expect(stacked.series.map((s: Opt) => s.stack)).toEqual(["total", "total"]);
    const pct = render("stacked_bar", style({ stack: "percent" }), grouped);
    expect(pct.series[0].data[0]).toBeCloseTo(1 / 3);
    expect(pct.series[1].data[0]).toBeCloseTo(2 / 3);
    expect(pct.series[0].data[1]).toBe(1);
    expect(pct.yAxis.axisLabel.formatter(0.5)).toBe("50%");
  });

  it("horizontal bars swap the axes and read top to bottom", () => {
    const o = render("horizontal_bar", {});
    expect(o.yAxis.type).toBe("category");
    expect(o.yAxis.inverse).toBe(true);
    expect(o.xAxis).toHaveLength(1);
    expect(o.xAxis[0].type).toBe("value");
  });

  it("applies per-series overrides: second y axis, color, and a line combo", () => {
    const two = makeData(
      { x: ["a", "b"], y0: [1, 2], y1: [100, 200] },
      { x: "x", y0: "Count", y1: "Revenue" },
    );
    const o = render(
      "bar",
      style({
        yAxes: [{ grid: true }, { grid: true, title: "Revenue ($)", format: "currency:USD:0" }],
        series: [{ index: 1, yAxis: 1, kind: "line", color: "#ff0000", markers: undefined }],
      }),
      two,
    );
    expect(o.yAxis).toHaveLength(2);
    expect(o.yAxis[1]).toMatchObject({ name: "Revenue ($)", position: "right" });
    expect(o.yAxis[1].axisLabel.formatter(1500)).toBe("$1,500");
    expect(o.series[1]).toMatchObject({ type: "line", yAxisIndex: 1, color: "#ff0000" });
    expect(o.series[0].type).toBe("bar");
  });

  it("supports log scale, axis titles, formats and bounds", () => {
    const o = render(
      "bar",
      style({
        xAxis: { title: "Region", grid: true },
        yAxes: [{ scale: "log", logBase: 2, min: 1, max: 1000, format: "compact:1", grid: false, title: "Sales" }],
      }),
    );
    expect(o.yAxis).toMatchObject({ type: "log", logBase: 2, min: 1, max: 1000, name: "Sales" });
    expect(o.yAxis.splitLine.show).toBe(false);
    expect(o.xAxis.name).toBe("Region");
    expect(o.yAxis.axisLabel.formatter(1200)).toBe("1.2K");
  });

  it("adds title, subtitle, legend position and labels", () => {
    const o = render(
      "grouped_bar",
      style({
        title: "Sales",
        subtitle: "by region",
        legend: { show: true, position: "bottom" },
        showLabels: true,
        labelPosition: "outside",
        numberFormat: "number:1",
      }),
      grouped,
    );
    expect(o.title).toMatchObject({ text: "Sales", subtext: "by region" });
    expect(o.legend.bottom).toBeDefined();
    expect(o.series[0].label.show).toBe(true);
    expect(o.series[0].label.position).toBe("top");
    expect(o.series[0].label.formatter({ value: 3 })).toBe("3.0");
    expect(o.grid.top).toBeGreaterThan(40);
    expect(render("grouped_bar", style({ legend: { show: false, position: "top" } }), grouped).legend).toBeUndefined();
  });

  it("draws reference lines and annotations on the first series", () => {
    const o = render(
      "bar",
      style({
        refLines: [{ axis: "y", value: 200, label: "target", lineStyle: "dotted" }],
        annotations: [
          { kind: "point", x: "North", y: 330.5, text: "peak" },
          { kind: "area", y: 100, y2: 150, text: "band" },
        ],
      }),
    );
    expect(o.series[0].markLine.data[0]).toMatchObject({ yAxis: 200, name: "target" });
    expect(o.series[0].markLine.data[0].lineStyle.type).toBe("dotted");
    expect(o.series[0].markPoint.data[0]).toMatchObject({ coord: ["North", 330.5], name: "peak" });
    expect(o.series[0].markArea.data[0][0]).toMatchObject({ yAxis: 100 });
    expect(o.series[0].markArea.data[0][1]).toMatchObject({ yAxis: 150 });
  });

  it("adds error bars when the server sends bounds", () => {
    const withBounds = makeData(
      { x: ["a", "b"], y0: [5, 6], y0_lo: [4, 5], y0_hi: [6, 7] },
      { x: "x", y0: "Mean" },
    );
    const o = render("bar", style({ errorBars: { kind: "sd", level: 0.95 } }), withBounds);
    expect(o.series.map((s: Opt) => s.type)).toEqual(["bar", "custom"]);
    expect(o.series[1].data).toEqual([
      ["a", 4, 6],
      ["b", 5, 7],
    ]);
  });

  it("applies palette and fonts from the style", () => {
    const o = render("bar", style({ palette: "tableau-10", fontFamily: "Georgia", fontSize: 14 }));
    expect(o.color[0]).toBe("#4e79a7");
    expect(o.textStyle).toMatchObject({ fontFamily: "Georgia", fontSize: 14 });
  });

  it("renders the same option for the same inputs (pure)", () => {
    const strip = (o: Opt) => JSON.stringify(o);
    expect(strip(render("bar", {}))).toBe(strip(render("bar", {})));
  });

  it("uses dark-mode colors from the theme", () => {
    const dark = makeTheme({ mode: "dark", text: "#fff", textMuted: "#aaa", border: "#333" });
    const o = byKey("bar").render(makeSpec(), simple, dark) as Opt;
    expect(o.textStyle.color).toBe("#fff");
    expect(o.xAxis.axisLabel.color).toBe("#aaa");
  });
});

describe("line and area", () => {
  const time = makeData(
    {
      x: [Date.UTC(2026, 0, 1), Date.UTC(2026, 0, 2), Date.UTC(2026, 0, 3)],
      y0: [1, 3, 2],
    },
    { x: "day", y0: "Orders" },
    { kinds: { x: "temporal" } },
  );

  it("uses a time axis with [x, y] pairs for temporal x", () => {
    const o = render("line", {}, time);
    expect(o.xAxis.type).toBe("time");
    expect(o.series[0].data[1]).toEqual([Date.UTC(2026, 0, 2), 3]);
    expect(o.useUTC).toBe(true);
    expect(o.dataZoom[0].type).toBe("inside");
  });

  it("falls back to a category axis when the scale is forced to category", () => {
    const o = render("line", style({ xAxis: { scale: "category", grid: true } }), time);
    expect(o.xAxis.type).toBe("category");
    expect(o.xAxis.data).toEqual(["2026-01-01", "2026-01-02", "2026-01-03"]);
  });

  it("keeps the zoomed range and full extent for detail-on-demand", () => {
    const o = render("line", {}, time, {
      viewport: { fullExtent: [0, 10], xRange: [2, 4] },
    } as never);
    expect(o.xAxis).toMatchObject({ min: 0, max: 10 });
    expect(o.dataZoom[0]).toMatchObject({ startValue: 2, endValue: 4 });
  });

  it("applies markers, line width/style, smoothing and step", () => {
    const o = render(
      "line",
      { ...style({ markers: true, lineWidth: 3, lineStyle: "dashed", smooth: true }), params: { step: "middle" } },
      time,
    );
    expect(o.series[0]).toMatchObject({ symbol: "circle", smooth: true, step: "middle" });
    expect(o.series[0].lineStyle).toMatchObject({ width: 3, type: "dashed" });
  });

  it("area fills with the configured opacity and stacks", () => {
    const o = render("area", { ...style({ stack: "stack", areaOpacity: 0.5 }), params: {} }, grouped);
    expect(o.series[0].areaStyle.opacity).toBe(0.5);
    expect(o.series[0].stack).toBe("total");
  });

  it("draws server trendlines with a confidence band", () => {
    const overlay: OverlayResult = {
      kind: "linear",
      series: 0,
      x: time.columns.x as number[],
      y: [1, 2, 3],
      lo: [0.5, 1.5, 2.5],
      hi: [1.5, 2.5, 3.5],
      equation: "y = x",
      r2: 0.9123,
      n: 3,
      sampled: false,
      warnings: [],
    };
    const o = render(
      "line",
      style({ trendlines: [{ kind: "linear", series: 0, degree: 2, showEquation: true, showR2: true }] }),
      time,
      { overlays: [overlay] } as never,
    );
    // base series + two band series + trend line
    expect(o.series).toHaveLength(4);
    const trend = o.series[3];
    expect(trend.name).toBe("Orders linear trend (R² 0.912, y = x)");
    expect(trend.lineStyle.type).toBe("dashed");
    expect(o.legend.show).toBe(true); // the trendline is named; the band series are not
  });
});

describe("pie and donut", () => {
  const pieData = makeData(
    { x: ["a", "b", "c", "d"], y0: [10, 30, 0, 60] },
    { x: "type", y0: "Total" },
  );

  it("sorts slices, drops non-positive ones and shows a legend", () => {
    const o = render("pie", {}, pieData);
    expect(o.series[0].data).toEqual([
      { name: "d", value: 60 },
      { name: "b", value: 30 },
      { name: "a", value: 10 },
    ]);
    expect(o.series[0].radius[0]).toBe("0%");
    expect(o.legend.data).toEqual(["d", "b", "a"]);
    expect(o.tooltip.trigger).toBe("item");
  });

  it("groups the tail into Other beyond maxSlices", () => {
    const o = render("pie", { params: { maxSlices: 2 } }, pieData);
    expect(o.series[0].data).toEqual([
      { name: "d", value: 60 },
      { name: "Other", value: 40 },
    ]);
  });

  it("donut has a hole and the total in the middle", () => {
    const o = render("donut", {}, pieData);
    expect(o.series[0].radius[0]).toBe("55%");
    expect(o.series[1].label.formatter).toBe("100");
  });

  it("formats labels by mode", () => {
    const o = render("pie", { ...style({ showLabels: true }), params: { labelMode: "value" } }, pieData);
    expect(o.series[0].label.formatter({ name: "d", value: 60, percent: 60 })).toBe("d: 60");
    const named = render("pie", { ...style({ showLabels: true }), params: { labelMode: "percent" } }, pieData);
    expect(named.series[0].label.formatter({ name: "d", value: 60, percent: 60 })).toBe("d: 60.0%");
  });

  it("uses the chosen palette for the slices", () => {
    expect(render("pie", style({ palette: "okabe-ito" }), pieData).color[0]).toBe("#0072b2");
  });
});
