import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../../api/types";
import {
  applyStyle,
  dropNonPositive,
  dropNonPositiveX,
  hasSeveralGrids,
  niceTickLabels,
} from "./style";
import { makeSpec, makeTheme } from "./testing";

// biome-ignore lint/suspicious/noExplicitAny: tests probe deep into the ECharts option
type Opt = Record<string, any>;
const theme = makeTheme();
const style = (patch: Record<string, unknown>): Partial<ChartSpec> => ({
  style: {
    orientation: "vertical",
    stack: "none",
    showLabels: false,
    ...patch,
  } as ChartSpec["style"],
});

describe("applyStyle with several grids (S4)", () => {
  // A candlestick with a volume pane: two grids, two x axes, two y axes.
  const option = (): Opt => ({
    grid: [
      { left: 40, right: 20, top: 30, height: "55%" },
      { left: 40, right: 20, top: "72%", height: "16%" },
    ],
    xAxis: [
      { type: "category", gridIndex: 0, data: ["a", "b"] },
      { type: "category", gridIndex: 1, data: ["a", "b"] },
    ],
    yAxis: [
      { type: "value", gridIndex: 0 },
      { type: "value", gridIndex: 1, splitNumber: 2 },
    ],
    series: [
      { type: "bar", name: "Price", data: [1, 2] },
      { type: "bar", name: "Volume", data: [5, 6], xAxisIndex: 1, yAxisIndex: 1 },
    ],
  });

  it("keeps the plot's grids and axes, applying only theme colors", () => {
    const out = applyStyle(option(), makeSpec(style({ title: "OHLC" })), theme) as Opt;
    expect(out.grid).toEqual(option().grid);
    expect(out.xAxis).toHaveLength(2);
    expect(out.xAxis[1]).toMatchObject({ gridIndex: 1, data: ["a", "b"] });
    expect(out.yAxis[1]).toMatchObject({ gridIndex: 1, splitNumber: 2 });
    expect(out.yAxis[0].axisLabel.color).toBe(theme.textMuted);
    expect(out.series[1]).toMatchObject({ xAxisIndex: 1, yAxisIndex: 1 });
  });

  it("still applies title, legend, palette, tooltip and aria", () => {
    const out = applyStyle(
      option(),
      makeSpec(style({ title: "OHLC", palette: "tableau-10" })),
      theme,
    ) as Opt;
    expect(out.title.text).toBe("OHLC");
    expect(out.legend.show).toBe(true);
    expect(out.color[0]).toBe("#4e79a7");
    expect(out.tooltip.show).toBe(true);
    expect(out.aria.enabled).toBe(true);
  });

  it("ignores orientation and multi-axis settings for such plots", () => {
    const out = applyStyle(
      option(),
      makeSpec(style({ orientation: "horizontal", yAxes: [{ grid: true }, { grid: true }] })),
      theme,
    ) as Opt;
    expect(out.xAxis[0].type).toBe("category");
    expect(out.yAxis).toHaveLength(2);
  });

  it("axes: false opts a single-grid plot out of axis management", () => {
    const out = applyStyle(
      { xAxis: { type: "value", name: "mine" }, yAxis: { type: "value" }, series: [] },
      makeSpec(style({ xAxis: { grid: true, title: "ignored" } })),
      theme,
      undefined,
      { axes: false },
    ) as Opt;
    expect(out.xAxis.name).toBe("mine");
  });

  it("layout: false keeps the plot's own grid but still styles axes", () => {
    const grid = { left: 5, right: 5, top: 5, bottom: 5 };
    const out = applyStyle(
      { grid, xAxis: { type: "category", data: ["a"] }, yAxis: { type: "value" }, series: [] },
      makeSpec(style({ yAxes: [{ grid: true, scale: "log" }] })),
      theme,
      undefined,
      { layout: false },
    ) as Opt;
    expect(out.grid).toBe(grid);
    expect(out.yAxis.type).toBe("log");
  });
});

describe("multi-grid detection (W1)", () => {
  const line = (): Opt => ({
    xAxis: { type: "category", data: ["a", "b", "c"] },
    yAxis: { type: "value" },
    series: [{ type: "line", name: "v", data: [10, 0, -5] }],
  });

  it("a single grid passed as a one-element array stays fully managed", () => {
    const option = { ...line(), grid: [{ left: 10 }] };
    const out = applyStyle(
      option,
      makeSpec(style({ title: "T", yAxes: [{ grid: true, scale: "log" }] })),
      theme,
    ) as Opt;
    expect(hasSeveralGrids(option)).toBe(false);
    expect(out.yAxis.type).toBe("log"); // log scale applied
    expect(out.series[0].data).toEqual([10, null, null]); // non-positive dropped
    expect(out.grid.top).toBeGreaterThan(30); // title inset applied
    expect(Array.isArray(out.grid)).toBe(false);
  });

  it("one grid with x axes on both top and bottom keeps its title inset and managed axes", () => {
    const option = {
      ...line(),
      xAxis: [
        { type: "category", data: ["a", "b", "c"] },
        { type: "category", data: ["a", "b", "c"], position: "top", gridIndex: 0 },
      ],
    };
    const out = applyStyle(option, makeSpec(style({ title: "T", subtitle: "S" })), theme) as Opt;
    expect(hasSeveralGrids(option)).toBe(false);
    expect(out.grid.top).toBeGreaterThan(40); // title and subtitle space reserved
    expect(out.xAxis).toHaveLength(2);
    expect(out.xAxis[0].nameLocation).toBe("middle"); // managed
    expect(out.xAxis[1].position).toBe("top"); // second axis passes through
    expect(out.xAxis[1].axisLabel.color).toBe(theme.textMuted);
  });

  it("detects real multi-grid by grid count or distinct gridIndex", () => {
    expect(hasSeveralGrids({ grid: [{}, {}], xAxis: {}, yAxis: {} })).toBe(true);
    expect(
      hasSeveralGrids({
        xAxis: [{ gridIndex: 0 }, { gridIndex: 1 }],
        yAxis: [{ gridIndex: 0 }, { gridIndex: 1 }],
      }),
    ).toBe(true);
    expect(hasSeveralGrids({ xAxis: [{}, {}], yAxis: {} })).toBe(false);
    expect(hasSeveralGrids({ grid: { left: 1 }, xAxis: {}, yAxis: {} })).toBe(false);
  });
});

describe("log x axis (nit)", () => {
  it("drops points whose x is zero or negative", () => {
    const out = applyStyle(
      {
        xAxis: { type: "log" },
        yAxis: { type: "value" },
        series: [
          {
            type: "line",
            name: "v",
            data: [
              [0, 1],
              [-2, 2],
              [10, 3],
              [100, 4],
            ],
          },
        ],
      },
      makeSpec(),
      theme,
    ) as Opt;
    expect(out.series[0].data).toEqual([
      [10, 3],
      [100, 4],
    ]);
    expect(
      dropNonPositiveX([
        [0, 1],
        [5, 2],
      ]),
    ).toEqual([[5, 2]]);
  });
});

describe("applyStyle details", () => {
  const base = (): Opt => ({
    xAxis: { type: "category", data: ["a", "b", "c"] },
    yAxis: { type: "value" },
    series: [{ type: "bar", name: "v", data: [10, 0, -5] }],
  });

  it("drops zero and negative values on a log axis instead of drawing them", () => {
    const out = applyStyle(
      base(),
      makeSpec(style({ yAxes: [{ grid: true, scale: "log" }] })),
      theme,
    ) as Opt;
    expect(out.series[0].data).toEqual([10, null, null]);
    expect(
      dropNonPositive([
        [1, 0],
        [2, 3],
      ]),
    ).toEqual([
      [1, null],
      [2, 3],
    ]);
  });

  it("formats axis ticks and labels in the given locale", () => {
    const spec = makeSpec(style({ yAxes: [{ grid: true, format: "number:1" }] }));
    const de = applyStyle(base(), spec, theme, { locale: "de-DE" }) as Opt;
    const en = applyStyle(base(), spec, theme, { locale: "en-US" }) as Opt;
    expect(de.yAxis.axisLabel.formatter(1234.5)).toBe("1.234,5");
    expect(en.yAxis.axisLabel.formatter(1234.5)).toBe("1,234.5");
  });

  it("reserves room at the right for a vertical visualMap", () => {
    const plain = applyStyle(base(), makeSpec(), theme) as Opt;
    const withMap = applyStyle(
      { ...base(), visualMap: { type: "continuous", orient: "vertical" } },
      makeSpec(),
      theme,
    ) as Opt;
    expect(withMap.grid.right - plain.grid.right).toBe(90);
  });

  it("gives each trendline band its own stack id", () => {
    const x = [1, 2];
    const overlay = (series: number) => ({
      kind: "linear" as const,
      series,
      group: null,
      x,
      y: [1, 2],
      lo: [0, 1],
      hi: [2, 3],
      equation: null,
      r2: null,
      n: 2,
      sampled: false,
      warnings: [],
    });
    const out = applyStyle(
      {
        xAxis: { type: "value" },
        yAxis: { type: "value" },
        series: [
          { type: "line", name: "a", yIndex: 0, data: [[1, 1]] },
          { type: "line", name: "b", yIndex: 1, data: [[1, 1]] },
        ],
      },
      makeSpec(),
      theme,
      { overlays: [overlay(0), overlay(1)] },
    ) as Opt;
    const stacks = new Set(out.series.map((s: Opt) => s.stack).filter(Boolean));
    expect(stacks.size).toBe(2);
    expect(out.series.filter((s: Opt) => s.stackStrategy === "all")).toHaveLength(4);
  });
});

describe("log x axis with non-x first dimensions", () => {
  const run = (series: Opt[]) =>
    applyStyle(
      { xAxis: { type: "log" }, yAxis: { type: "category", data: ["a", "b"] }, series },
      makeSpec(),
      theme,
    ) as Opt;

  it("leaves custom series alone: their first dim may be a category index", () => {
    const data = [
      [0, 1, 4],
      [1, 2, 8],
    ];
    const out = run([{ type: "custom", name: "ci", encode: { x: [1, 2], y: 0 }, data }]);
    expect(out.series[0].data).toEqual(data);
  });

  it("honours encode.x on other series types", () => {
    const out = run([
      {
        type: "scatter",
        name: "p",
        encode: { x: 1, y: 0 },
        data: [
          [0, 5],
          [1, -3],
          [2, 7],
        ],
      },
    ]);
    expect(out.series[0].data).toEqual([
      [0, 5],
      [2, 7],
    ]);
  });

  it("dropNonPositiveX takes explicit x dims", () => {
    expect(
      dropNonPositiveX(
        [
          [0, 5],
          [1, 0],
        ],
        [1],
      ),
    ).toEqual([[0, 5]]);
  });
});

describe("niceTickLabels", () => {
  it("gives the labels a linear axis draws, not the raw data maximum", () => {
    expect(niceTickLabels([0, 1607.42])).toEqual(["0", "500", "1000", "1500", "2000"]);
    expect(niceTickLabels([0, 44.53])).toEqual(["0", "10", "20", "30", "40", "50"]);
  });

  it("uses the formatter when one is given", () => {
    expect(niceTickLabels([0, 1], { format: (v) => `${Number(v) * 100}%` })).toContain("100%");
  });

  it("lists powers of the base on a log axis", () => {
    expect(niceTickLabels([3, 4200], { log: true })).toEqual(["1", "10", "100", "1000", "10000"]);
  });

  it("handles an empty or degenerate extent", () => {
    expect(niceTickLabels([5, 5]).length).toBeGreaterThan(0);
  });
});

describe("y axis title gap", () => {
  it("is sized from the tick labels, so an unrounded maximum does not push the title away", () => {
    const build = (v: number) =>
      applyStyle(
        {
          xAxis: { type: "category", data: ["a", "b"] },
          yAxis: { type: "value" },
          series: [{ type: "bar", name: "v", data: [v / 3, v] }],
        },
        makeSpec(style({ yAxes: [{ grid: true, title: "Total" }] })),
        theme,
      ) as Opt;
    expect(build(1607.42).yAxis.nameGap).toBe(build(1600).yAxis.nameGap);
    expect(build(44.53).yAxis.nameGap).toBe(build(40).yAxis.nameGap);
  });
});
