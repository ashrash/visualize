import { describe, expect, it } from "vitest";
import openapi from "../../api/openapi.json";
import { getPlot, listPlots } from "./registry";

const plotTypes: string[] = openapi.components.schemas.PlotType.enum;

describe("plot registry", () => {
  it("only registers plot types the backend knows", () => {
    for (const entry of listPlots()) expect(plotTypes).toContain(entry.key);
  });

  it("finds the bar chart", () => {
    expect(getPlot("bar")?.family).toBe("basic");
  });

  // Turned on at the Wave 2 exit gate (CLAUDE.md §10): every Plots.md chart has a renderer.
  it.skipIf(!process.env.VIZ_REQUIRE_ALL_PLOTS)("covers every plot type", () => {
    const registered = new Set(listPlots().map((entry) => entry.key));
    expect(plotTypes.filter((type) => !registered.has(type as never))).toEqual([]);
  });
});
