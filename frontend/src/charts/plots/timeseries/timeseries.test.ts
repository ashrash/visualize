import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../../../api/types";
import { makeData, makeSpec, makeTheme } from "../../core/testing";
import type { PlotEntry } from "../../core/types";
import entries from "./index";
import { periodLabel } from "./seasonal";

const byKey = (key: string): PlotEntry => {
  const entry = entries.find((e) => e.key === key);
  if (!entry) throw new Error(`no ${key}`);
  return entry;
};

const theme = makeTheme();
// biome-ignore lint/suspicious/noExplicitAny: tests probe deep into the ECharts option
type Opt = Record<string, any>;

const render = (key: string, spec: Partial<ChartSpec>, data: ReturnType<typeof makeData>) =>
  byKey(key).render(makeSpec({ plotType: key as never, ...spec }), data, theme, undefined) as Opt;

describe("registry", () => {
  it("registers all eight Sec4 plot types", () => {
    expect(entries.map((e) => e.key).sort()).toEqual(
      [
        "acf",
        "calendar_heatmap",
        "candlestick",
        "lag",
        "moving_average",
        "pacf",
        "seasonal",
        "timeseries_line",
      ].sort(),
    );
  });
});

describe("timeseries_line", () => {
  const time = makeData(
    {
      x: [Date.UTC(2024, 0, 1), Date.UTC(2024, 0, 2), Date.UTC(2024, 0, 3)],
      y0: [1, 3, 2],
    },
    { x: "day", y0: "Orders" },
    { kinds: { x: "temporal" } },
  );

  it("draws a time axis line from aggregated x/y0 columns", () => {
    const o = render("timeseries_line", {}, time);
    expect(o.xAxis.type).toBe("time");
    expect(o.series).toHaveLength(1);
    expect(o.series[0]).toMatchObject({ type: "line", name: "Orders" });
    expect(o.series[0].data[1]).toEqual([Date.UTC(2024, 0, 2), 3]);
    expect(o.dataZoom[0].type).toBe("inside");
  });

  it("pivots a color channel into one series per group", () => {
    const grouped = makeData(
      { x: [Date.UTC(2024, 0, 1), Date.UTC(2024, 0, 1)], color: ["a", "b"], y0: [1, 2] },
      { x: "day", color: "g", y0: "v" },
      { kinds: { x: "temporal" } },
    );
    const o = render("timeseries_line", {}, grouped);
    expect(o.series.map((s: Opt) => s.name)).toEqual(["a", "b"]);
    expect(o.legend.show).toBe(true);
  });

  it("surfaces server reduction warnings (auto-bucketing, truncation)", () => {
    const withWarning = makeData(
      { x: [1], y0: [1] },
      {},
      { meta: { warnings: ["Bucketed to week intervals..."] } },
    );
    expect(byKey("timeseries_line").warnings?.(makeSpec(), withWarning)).toEqual([
      "Bucketed to week intervals...",
    ]);
  });

  it("recommends itself for temporal + quantitative data", () => {
    const recommend = byKey("timeseries_line").recommend;
    expect(recommend?.([{ semantic: "temporal" }, { semantic: "quantitative" }] as never)).toBe(
      0.85,
    );
    expect(recommend?.([{ semantic: "nominal" }] as never)).toBe(0.05);
  });

  it("connects across downsampling gaps (each series can keep a different x subset)", () => {
    // MinMaxLTTB runs independently per series, so a shared x axis across several series can
    // end up with each series missing some of the merged x values (cartesianSeries pivots
    // those onto real `null` entries); without connectNulls the line would visibly fragment.
    const o = render("timeseries_line", {}, time);
    for (const s of o.series) expect(s.connectNulls).toBe(true);
  });
});

describe("seasonal", () => {
  it("has no color/series encoding channel (the cycle comes from a param, not a field)", () => {
    const names = byKey("seasonal").encodingsSchema.map((c) => c.name);
    expect(names).toEqual(["x", "y"]);
  });

  it("maps month codes to labels and keeps one series per year", () => {
    const data = makeData(
      { x: [1, 2, 1, 2], color: [2023, 2023, 2024, 2024], y0: [10, 20, 30, 40] },
      { x: "Month", y0: "v" },
      { meta: { params: { period: "month", cycle: "year" } } },
    );
    const o = render("seasonal", {}, data);
    expect(o.xAxis.type).toBe("category");
    // x is shared (deduped) across both series, like the basic line chart's color pivot.
    expect(o.xAxis.data).toEqual(["Jan", "Feb"]);
    // core/series.ts's categoryLabel formats a numeric group value with "auto" (grouping
    // separators included), the same as any other plot's numeric color column.
    expect(o.series.map((s: Opt) => s.name)).toEqual(["2,023", "2,024"]);
    expect(o.series[0].data).toEqual([10, 20]);
    expect(o.series[1].data).toEqual([30, 40]);
  });

  it("labels every supported period", () => {
    expect(periodLabel("month", 3)).toBe("Mar");
    expect(periodLabel("weekday", 0)).toBe("Sun");
    expect(periodLabel("hour", 9)).toBe("9:00");
    expect(periodLabel("quarter", 2)).toBe("Q2");
    expect(periodLabel("week", 5)).toBe("Wk 5");
    expect(periodLabel("day_of_month", 14)).toBe("14");
    expect(periodLabel(undefined, 7)).toBe("7");
  });

  it("falls back to a single line with no color column", () => {
    const data = makeData({ x: [1, 2, 3], y0: [1, 2, 3] }, { x: "Month", y0: "v" });
    const o = render("seasonal", {}, data);
    expect(o.series).toHaveLength(1);
  });
});

describe("lag", () => {
  const data = makeData(
    { x: [10, 20, 30], y0: [20, 30, 40] },
    { x: "v (lag 1)", y0: "v" },
    { kinds: { x: "number", y0: "number" } },
  );

  it("renders a scatter of (lag value, value)", () => {
    const o = render("lag", {}, data);
    expect(o.xAxis.type).toBe("value");
    expect(o.yAxis.type).toBe("value");
    expect(o.series).toHaveLength(1);
    expect(o.series[0].type).toBe("scatter");
    expect(o.series[0].data).toEqual([
      [10, 20],
      [20, 30],
      [30, 40],
    ]);
  });

  it("splits into one scatter series per color group", () => {
    const grouped = makeData(
      { x: [1, 2, 3, 4], y0: [1, 2, 3, 4], color: ["a", "a", "b", "b"] },
      { x: "x", y0: "y" },
      { kinds: { x: "number", y0: "number", color: "string" } },
    );
    const o = render("lag", {}, grouped);
    expect(o.series).toHaveLength(2);
    expect(o.series.map((s: Opt) => s.name).sort()).toEqual(["a", "b"]);
  });

  it("drops non-finite points", () => {
    const withNulls = makeData(
      { x: [1, null, 3], y0: [1, 2, null] },
      { x: "x", y0: "y" },
      { kinds: { x: "number", y0: "number" } },
    );
    const o = render("lag", {}, withNulls);
    expect(o.series[0].data).toEqual([[1, 1]]);
  });
});

describe("acf / pacf", () => {
  const data = makeData(
    { x: [0, 1, 2], y0: [1, 0.5, 0.2], y0_lo: [1, 0.3, 0.0], y0_hi: [1, 0.7, 0.4] },
    { x: "Lag", y0: "Autocorrelation" },
    { kinds: { x: "number", y0: "number" }, meta: { params: { ciLevel: 0.95 } } },
  );

  it("draws a bar per lag with a shaded confidence band and a zero line", () => {
    const o = render("acf", {}, data);
    expect(o.xAxis.data).toEqual(["0", "1", "2"]);
    const bar = o.series.find((s: Opt) => s.type === "bar");
    expect(bar.data).toEqual([1, 0.5, 0.2]);
    expect(bar.markLine.data[0]).toMatchObject({ yAxis: 0 });
    const band = o.series.filter((s: Opt) => s.type === "line");
    expect(band).toHaveLength(2);
    expect(band[1].name).toBe("95% confidence band");
  });

  it("pacf uses the same renderer", () => {
    const o = render("pacf", {}, data);
    expect(o.series.find((s: Opt) => s.type === "bar").data).toEqual([1, 0.5, 0.2]);
  });
});

describe("moving_average", () => {
  const data = makeData(
    { x: [Date.UTC(2024, 0, 1), Date.UTC(2024, 0, 2)], y0: [1, 3], y1: [1, 2] },
    { x: "day", y0: "Orders", y1: "7-point moving average" },
    { kinds: { x: "temporal" }, meta: { params: { window: 7 } } },
  );

  it("draws the raw series thin and the moving average bold", () => {
    const o = render("moving_average", {}, data);
    expect(o.series).toHaveLength(2);
    // applyStyle strips yIndex from the output; y0 (raw) sorts before y1 (average).
    const [raw, avg] = o.series;
    expect(raw.lineStyle.opacity).toBeLessThan(1);
    expect(avg.lineStyle.width).toBeGreaterThan(raw.lineStyle.width);
    expect(avg.name).toContain("7-pt avg");
  });

  it("connects both series across downsampling gaps", () => {
    const o = render("moving_average", {}, data);
    for (const s of o.series) expect(s.connectNulls).toBe(true);
  });
});

describe("candlestick", () => {
  const data = makeData(
    {
      x: [Date.UTC(2024, 0, 1), Date.UTC(2024, 0, 2)],
      open: [10, 11],
      high: [12, 13],
      low: [9, 10],
      close: [11, 12],
    },
    { x: "date", open: "Open", high: "High", low: "Low", close: "Close" },
    { kinds: { x: "temporal" } },
  );

  it("maps columns to echarts' [open, close, low, high] order", () => {
    const o = render("candlestick", {}, data);
    const series = o.series.find((s: Opt) => s.type === "candlestick");
    expect(series.data).toEqual([
      [10, 11, 9, 12],
      [11, 12, 10, 13],
    ]);
    expect(o.xAxis.type).toBe("category");
  });

  it("adds a linked volume grid when volume is present", () => {
    const withVolume = makeData(
      {
        x: [Date.UTC(2024, 0, 1), Date.UTC(2024, 0, 2)],
        open: [10, 11],
        high: [12, 13],
        low: [9, 10],
        close: [11, 9],
        volume: [100, 200],
      },
      { x: "date", open: "Open", high: "High", low: "Low", close: "Close", volume: "Volume" },
      { kinds: { x: "temporal" } },
    );
    const o = render("candlestick", {}, withVolume);
    expect(o.xAxis).toHaveLength(2);
    expect(o.grid).toHaveLength(2);
    const volumeSeries = o.series.find((s: Opt) => s.type === "bar");
    expect(volumeSeries.data.map((d: Opt) => d.value)).toEqual([100, 200]);
    // Day 2 closes below its open: the down color is used.
    expect(volumeSeries.data[1].itemStyle.color).toBe(theme.palette[5] ?? "#d55e00");
  });

  it("recommends itself when open/high/low/close are present", () => {
    const recommend = byKey("candlestick").recommend;
    expect(
      recommend?.([
        { name: "open" },
        { name: "high" },
        { name: "low" },
        { name: "close" },
      ] as never),
    ).toBe(0.9);
    expect(recommend?.([{ name: "value" }] as never)).toBe(0.02);
  });
});

describe("calendar_heatmap", () => {
  it("builds one calendar and heatmap series per year present", () => {
    const data = makeData(
      {
        x: [Date.UTC(2023, 11, 31), Date.UTC(2024, 0, 1), Date.UTC(2024, 0, 2)],
        y0: [1, 2, 3],
      },
      { x: "day", y0: "v" },
      { kinds: { x: "temporal" } },
    );
    const o = render("calendar_heatmap", {}, data);
    expect(o.calendar).toHaveLength(2);
    expect(o.calendar[0].range).toBe("2023");
    expect(o.calendar[1].range).toBe("2024");
    expect(o.series).toHaveLength(2);
    expect(o.series[0].data).toEqual([["2023-12-31", 1]]);
    expect(o.series[1].data).toEqual([
      ["2024-01-01", 2],
      ["2024-01-02", 3],
    ]);
    expect(o.visualMap.min).toBe(1);
    expect(o.visualMap.max).toBe(3);
  });

  it("caps the number of calendars drawn and warns about the hidden years", () => {
    const years = Array.from({ length: 20 }, (_, i) => 2000 + i); // 20 years, 2000..2019
    const x = years.map((y) => Date.UTC(y, 0, 1));
    const y0 = years.map((_, i) => i);
    const data = makeData({ x, y0 }, { x: "day", y0: "v" }, { kinds: { x: "temporal" } });
    const o = render("calendar_heatmap", {}, data);
    expect(o.calendar).toHaveLength(15);
    // The most recent 15 years are kept.
    expect(o.calendar[0].range).toBe("2005");
    expect(o.calendar[14].range).toBe("2019");
    const entry = byKey("calendar_heatmap");
    expect(entry.warnings?.(makeSpec(), data)).toEqual([
      "Showing the 15 most recent years; 5 earlier year(s) not shown.",
    ]);
  });
});
