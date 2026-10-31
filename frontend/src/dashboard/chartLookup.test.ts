import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import type { ChartSpec } from "../api/types";
import { dataIdentity } from "../charts/core/data";
import type { ChartData } from "../charts/core/types";
import { cachedChartData, colorValueFor } from "./chartLookup";

const spec = {
  id: "c1",
  name: "c",
  plotType: "stacked_bar",
  source: { kind: "dataset", id: "d" },
  encodings: { x: [{ field: "region" }], color: [{ field: "tier" }] },
} as unknown as ChartSpec;

const data = (colors: unknown[], kind = "number"): ChartData =>
  ({
    columns: { x: ["N"], color: colors, y0: [1] },
    kinds: { x: "string", color: kind, y0: "number" },
    rowCount: 1,
    meta: {},
  }) as unknown as ChartData;

describe("chart data cache", () => {
  it("reads the newest cached data for the chart, whatever filters or zoom it was fetched with", () => {
    const client = new QueryClient();
    const id = dataIdentity(spec);
    client.setQueryData(["chart-data", id, [], null, null], data([1]), { updatedAt: 1000 });
    client.setQueryData(
      ["chart-data", id, [{ op: "eq", field: "r", value: "N" }], [0, 5], null],
      data([2, 3]),
      {
        updatedAt: 2000,
      },
    );
    client.setQueryData(["chart-data", { id: "other" }, [], null, null], data([9]), {
      updatedAt: 3000,
    });
    expect(cachedChartData(client, spec)?.columns.color).toEqual([2, 3]);
    expect(cachedChartData(new QueryClient(), spec)).toBeUndefined();
  });

  it("maps a series name back to the raw colour value", () => {
    const client = new QueryClient();
    client.setQueryData(["chart-data", dataIdentity(spec), [], null, null], data([1, 2, 3]));
    expect(colorValueFor(client, spec, "2")).toBe(2);
    expect(colorValueFor(client, spec, "ghost")).toBeUndefined();
    expect(colorValueFor(client, spec, "")).toBeUndefined();
    expect(colorValueFor(new QueryClient(), spec, "2")).toBeUndefined();
  });
});
