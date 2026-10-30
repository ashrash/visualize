import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../../../api/types";
import type { ChartData, ChartTheme } from "../../core/types";
import { bar } from "./bar";

const theme: ChartTheme = {
  palette: ["#111", "#222"],
  text: "#000",
  textMuted: "#555",
  border: "#ccc",
  fontFamily: "sans-serif",
};

const spec: ChartSpec = {
  id: "c1",
  name: "Sales",
  plotType: "bar",
  source: { kind: "dataset", id: "d1" },
};

function data(columns: ChartData["columns"], fields: Record<string, string>): ChartData {
  return {
    columns,
    kinds: { x: "string", color: "string", y0: "number" },
    rowCount: columns.x?.length ?? 0,
    meta: {
      rowsScanned: 6,
      rowsReturned: columns.x?.length ?? 0,
      reduction: "aggregated",
      method: "group_by",
      fields,
      cached: false,
      ms: 1,
      warnings: [],
      rowsMatched: null,
      params: {},
      xDomain: null,
    },
  };
}

type Option = {
  xAxis: { data: string[] };
  yAxis: { type: string };
  series: { name: string; data: unknown[]; stack?: string }[];
  legend?: unknown;
};

describe("bar renderer", () => {
  it("draws one series per measure", () => {
    const option = bar.render(
      spec,
      data({ x: ["North", "South"], y0: [330.5, 135.25] }, { x: "region", y0: "Sum of sales" }),
      theme,
    ) as Option;
    expect(option.xAxis.data).toEqual(["North", "South"]);
    expect(option.series).toHaveLength(1);
    expect(option.series[0]).toMatchObject({ name: "Sum of sales", data: [330.5, 135.25] });
    expect(option.legend).toBeUndefined();
  });

  it("pivots the color channel into stacked series", () => {
    const option = bar.render(
      { ...spec, style: { stack: "stack", orientation: "vertical" } as ChartSpec["style"] },
      data(
        { x: ["N", "N", "S"], color: ["A", "B", "A"], y0: [1, 2, 3] },
        { x: "region", color: "product", y0: "Sum of sales" },
      ),
      theme,
    ) as Option;
    expect(option.xAxis.data).toEqual(["N", "S"]);
    expect(option.series.map((s) => [s.name, s.data, s.stack])).toEqual([
      ["A", [1, 3], "total"],
      ["B", [2, null], "total"],
    ]);
  });
});
