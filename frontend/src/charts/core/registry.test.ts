import { describe, expect, it } from "vitest";
import openapi from "../../api/openapi.json";
import { getPlot, listPlots } from "./registry";
import { validateEntry } from "./validate";

const plotTypes: string[] = openapi.components.schemas.PlotType.enum;

describe("plot registry", () => {
  it("only registers plot types the backend knows", () => {
    for (const entry of listPlots()) expect(plotTypes).toContain(entry.key);
  });

  it("every registered entry is valid", () => {
    const problems = listPlots().flatMap(validateEntry);
    expect(problems).toEqual([]);
  });

  it("registers the whole basic family", () => {
    for (const key of [
      "bar",
      "horizontal_bar",
      "grouped_bar",
      "stacked_bar",
      "line",
      "area",
      "pie",
      "donut",
    ] as const) {
      expect(getPlot(key)?.family).toBe("basic");
    }
  });

  // Turned on at the Wave 2 exit gate (CLAUDE.md §10): every Plots.md chart has a renderer.
  it.skipIf(!process.env.VIZ_REQUIRE_ALL_PLOTS)("covers every plot type", () => {
    const registered = new Set(listPlots().map((entry) => entry.key));
    expect(plotTypes.filter((type) => !registered.has(type as never))).toEqual([]);
  });
});

describe("validateEntry", () => {
  const base = getPlot("bar");
  it("flags a showIf that points nowhere and a bad default", () => {
    if (!base) throw new Error("bar missing");
    const broken = {
      ...base,
      paramsSchema: [
        { name: "a", type: "number" as const, label: "A", min: 0, max: 1, default: 5 },
        {
          name: "b",
          type: "boolean" as const,
          label: "B",
          default: false,
          showIf: { param: "nope", equals: true },
        },
      ],
      defaults: { params: { a: 2, zzz: 1 } },
    };
    const problems = validateEntry(broken);
    expect(problems.join("\n")).toContain('showIf references unknown "nope"');
    expect(problems.join("\n")).toContain('param "a" default does not match');
    expect(problems.join("\n")).toContain('unknown param "zzz"');
  });
});
