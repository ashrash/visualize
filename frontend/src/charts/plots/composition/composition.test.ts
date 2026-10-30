import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../../../api/types";
import { makeData, makeSpec, makeTheme } from "../../core/testing";
import type { PlotEntry } from "../../core/types";
import entries from "./index";

const byKey = (key: string): PlotEntry => {
  const entry = entries.find((e) => e.key === key);
  if (!entry) throw new Error(`no ${key}`);
  return entry;
};

const theme = makeTheme();
// biome-ignore lint/suspicious/noExplicitAny: tests probe deep into the ECharts option
type Opt = Record<string, any>;

const render = (
  key: string,
  data: ReturnType<typeof makeData>,
  params: Record<string, unknown> = {},
) =>
  byKey(key).render(
    makeSpec({ plotType: key as ChartSpec["plotType"], params }),
    data,
    theme,
  ) as Opt;

describe("stacked_area", () => {
  it("stacks series from x/color/y0 like the basic area renderer", () => {
    const data = makeData(
      { x: [1, 1, 2, 2], color: ["N", "S", "N", "S"], y0: [10, 5, 20, 1] },
      { x: "month", y0: "Sum of sales" },
    );
    const o = render("stacked_area", data);
    expect(o.series).toHaveLength(2);
    expect(o.series.every((s: Opt) => s.stack === "total")).toBe(true);
    expect(o.series.map((s: Opt) => s.type)).toEqual(["line", "line"]);
  });

  it("renders multiple downsampled color series through real ECharts SSR", async () => {
    const n = 300;
    const colors = ["A", "B", "C"];
    const data = makeData(
      {
        x: Array.from({ length: n * 3 }, (_, i) => Math.floor(i / 3)),
        color: Array.from({ length: n * 3 }, (_, i) => colors[i % 3]),
        y0: Array.from({ length: n * 3 }, (_, i) => Math.sin(i / 10) * 10 + 20),
      },
      { x: "day", y0: "value" },
    );
    const o = render("stacked_area", data);
    expect(o.series).toHaveLength(3);
    const { echarts } = await import("../../core/echarts");
    const chart = echarts.init(null, undefined, {
      renderer: "svg",
      ssr: true,
      width: 500,
      height: 350,
    });
    try {
      chart.setOption({ animation: false, ...o });
      const svg = chart.renderToSVGString();
      expect(svg).toContain("<svg");
      expect(svg).not.toMatch(/NaN/);
      // 3 filled area bands should be visible, not collapsed onto one another or off-canvas.
      expect((svg.match(/<path/g) ?? []).length).toBeGreaterThanOrEqual(3);
    } finally {
      chart.dispose();
    }
  });
});

describe("percent_stacked_bar", () => {
  it("stacks already-normalized shares without re-normalizing", () => {
    const data = makeData(
      { x: ["Q1", "Q1"], color: ["A", "B"], y0: [20, 80] },
      { x: "quarter", y0: "Share of units" },
    );
    const o = render("percent_stacked_bar", data);
    expect(o.series).toHaveLength(2);
    const values = o.series.map((s: Opt) => s.data[0]);
    // Un-doubled: the server already normalized to 0-100, so the values pass through as-is.
    expect(values).toEqual([20, 80]);
    expect(o.series.every((s: Opt) => s.stack === "total")).toBe(true);
  });
});

describe("waterfall", () => {
  it("draws a base series plus one colored series per direction", () => {
    const data = makeData(
      {
        x: ["Start", "Growth", "End"],
        base: [0, 100, 0],
        y0: [100, 20, 120],
        start: [0, 100, 0],
        end: [100, 120, 120],
        direction: ["total", "increase", "total"],
      },
      { x: "item", y0: "amount" },
    );
    const o = render("waterfall", data);
    expect(o.xAxis.data).toEqual(["Start", "Growth", "End"]);
    const names = o.series.map((s: Opt) => s.name);
    expect(names).toContain("Total");
    expect(names).toContain("Increase");
    const total = o.series.find((s: Opt) => s.name === "Total");
    expect(total.data).toEqual([100, null, 120]);
    const increase = o.series.find((s: Opt) => s.name === "Increase");
    expect(increase.data).toEqual([null, 20, null]);
  });
});

describe("funnel", () => {
  it("builds one funnel item per stage in the given order", () => {
    const data = makeData(
      { x: ["Visitors", "Qualified", "Purchased"], y0: [1000, 500, 100] },
      { x: "stage", y0: "users" },
    );
    const o = render("funnel", data);
    expect(o.series[0].type).toBe("funnel");
    expect(o.series[0].sort).toBe("none");
    expect(o.series[0].data).toEqual([
      { name: "Visitors", value: 1000 },
      { name: "Qualified", value: 500 },
      { name: "Purchased", value: 100 },
    ]);
  });
});

describe("treemap / sunburst", () => {
  const data = makeData(
    { label0: ["N", "N", "S"], label1: ["Widget", "Gadget", "Widget"], size: [100, 50, 20] },
    { label0: "region", label1: "product", size: "revenue" },
  );

  it("treemap builds a tree from the label columns via hierarchyToTree", () => {
    const o = render("treemap", data);
    expect(o.series[0].type).toBe("treemap");
    const tree = o.series[0].data;
    expect(tree).toHaveLength(2); // N, S
    const north = tree.find((n: Opt) => n.name === "N");
    expect(north.children.map((c: Opt) => c.name).sort()).toEqual(["Gadget", "Widget"]);
  });

  it("sunburst builds the same tree as a sunburst series", () => {
    const o = render("sunburst", data);
    expect(o.series[0].type).toBe("sunburst");
    expect(o.series[0].data).toHaveLength(2);
  });

  it("renders a 2-level hierarchy with an (Other) rollup through real ECharts SSR", async () => {
    const regions = ["N", "S"];
    const rolled = makeData(
      {
        label0: Array.from({ length: 60 }, (_, i) => regions[i % 2]),
        label1: Array.from({ length: 60 }, (_, i) => `product${i}`),
        size: Array.from({ length: 60 }, (_, i) => 60 - i),
      },
      { label0: "region", label1: "product", size: "revenue" },
    );
    const { echarts } = await import("../../core/echarts");
    for (const key of ["treemap", "sunburst"] as const) {
      const o = render(key, rolled);
      const chart = echarts.init(null, undefined, {
        renderer: "svg",
        ssr: true,
        width: 500,
        height: 350,
      });
      try {
        chart.setOption({ animation: false, ...o });
        const svg = chart.renderToSVGString();
        expect(svg).toContain("<svg");
        expect(svg).not.toMatch(/NaN/);
      } finally {
        chart.dispose();
      }
    }
  });
});

describe("mosaic / marimekko", () => {
  const data = makeData(
    {
      x: ["North", "North", "South"],
      color: ["Online", "Store", "Online"],
      size: [60, 40, 10],
      xShare: [100 / 110, 100 / 110, 10 / 110],
      share: [0.6, 0.4, 1],
    },
    { x: "region", color: "channel", size: "sales" },
  );

  it("mosaic draws one custom rect per crosstab cell", () => {
    const o = render("mosaic", data);
    expect(o.series[0].type).toBe("custom");
    expect(o.series[0].data).toHaveLength(3);
    expect(o.xAxis.show).toBe(false);
    expect(o.yAxis.show).toBe(false);
  });

  it("marimekko uses the same crosstab rendering", () => {
    const o = render("marimekko", data);
    expect(o.series[0].type).toBe("custom");
    expect(o.series[0].data).toHaveLength(3);
  });

  it("orders columns and segments by total, not by server row order", () => {
    // The server's GROUP BY order isn't guaranteed; rows are deliberately NOT sorted by size
    // here, to check that the renderer itself sorts columns/segments stably.
    const shuffled = makeData(
      {
        x: ["South", "North", "North"],
        color: ["Store", "Online", "Store"],
        size: [10, 60, 40],
        xShare: [10 / 110, 100 / 110, 100 / 110],
        share: [1, 0.6, 0.4],
      },
      { x: "region", color: "channel", size: "sales" },
    );
    const o = render("mosaic", shuffled);
    const columnLabels = o.series[1].data.map(
      (i: number) => renderItemColumnLabel(o.series[1], i).text,
    );
    expect(columnLabels).toEqual(["North", "South"]); // North (100) before South (10)
  });
});

/** Calls a "columns" custom series' renderItem with a minimal fake `api`, returning the text
 * style of the resulting text graphic (used to read back a column's label in render order). */
function renderItemColumnLabel(series: Opt, dataIndex: number): { text: string } {
  const api = { coord: ([x, y]: [number, number]) => [x, y] };
  const result = series.renderItem({ dataIndex }, api) as { style: { text: string } };
  return result.style;
}
