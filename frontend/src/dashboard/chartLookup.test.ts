import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../api/types";
import { dataIdentity } from "../charts/core/data";
import type { ChartData } from "../charts/core/types";
import { cachedChartData, chartLookup, lookupFromData } from "./chartLookup";

const spec = {
  id: "c1",
  name: "c",
  plotType: "bar",
  source: { kind: "dataset", id: "d" },
  encodings: { x: [{ field: "year" }], color: [{ field: "tier" }] },
  style: { xAxis: { format: "number:0" } },
} as unknown as ChartSpec;

const data = (xs: unknown[]): ChartData =>
  ({
    columns: { x: xs, color: ["a", "b"], y0: [1, 2] },
    kinds: { x: "number", color: "string", y0: "number" },
    rowCount: xs.length,
    meta: {},
  }) as unknown as ChartData;

describe("chart lookup", () => {
  it("exposes the raw x and colour columns, the kinds and the axis format", () => {
    expect(lookupFromData(spec, data([2019, 2020]))).toEqual({
      xs: [2019, 2020],
      xKind: "number",
      xFormat: "number:0",
      colors: ["a", "b"],
      colorKind: "string",
    });
    expect(lookupFromData(spec, undefined)).toBeUndefined();
  });

  it("reads the newest cached data for the chart, whatever filters or zoom it was fetched with", () => {
    const client = new QueryClient();
    const id = dataIdentity(spec);
    client.setQueryData(["chart-data", id, [], null, null], data([1]), { updatedAt: 1000 });
    client.setQueryData(
      ["chart-data", id, [{ op: "eq", field: "r", value: "N" }], [0, 5], null],
      data([2, 3]),
      { updatedAt: 2000 },
    );
    client.setQueryData(["chart-data", { id: "other" }, [], null, null], data([9]), {
      updatedAt: 3000,
    });
    expect(cachedChartData(client, spec)?.columns.x).toEqual([2, 3]);
    expect(chartLookup(client, spec)?.xs).toEqual([2, 3]);
    expect(cachedChartData(new QueryClient(), spec)).toBeUndefined();
  });
});
