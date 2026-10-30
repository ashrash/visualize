import type { ChartSpec } from "../../api/types";
import type { ChartData, ChartTheme } from "./types";

/** Fixtures for renderer tests (shared with plot-family agents). */
export function makeTheme(overrides: Partial<ChartTheme> = {}): ChartTheme {
  return {
    palette: ["#0072b2", "#e69f00", "#009e73", "#cc79a7"],
    text: "#000000",
    textMuted: "#555555",
    border: "#cccccc",
    fontFamily: "sans-serif",
    mode: "light",
    ...overrides,
  };
}

export function makeSpec(overrides: Partial<ChartSpec> = {}): ChartSpec {
  return {
    id: "c1",
    name: "Sales",
    plotType: "bar",
    source: { kind: "dataset", id: "d1" },
    ...overrides,
  };
}

/** Chart data from columns. `kinds` default to number for y*, string otherwise. */
export function makeData(
  columns: ChartData["columns"],
  fields: Record<string, string> = {},
  options: {
    kinds?: ChartData["kinds"];
    meta?: Partial<ChartData["meta"]>;
  } = {},
): ChartData {
  const rowCount = Math.max(0, ...Object.values(columns).map((c) => c.length));
  const kinds: ChartData["kinds"] = {};
  for (const name of Object.keys(columns)) kinds[name] = /^y\d*/.test(name) ? "number" : "string";
  return {
    columns,
    kinds: { ...kinds, ...options.kinds },
    rowCount,
    meta: {
      rowsScanned: rowCount,
      rowsReturned: rowCount,
      rowsMatched: null,
      params: {},
      xDomain: null,
      reduction: "aggregated",
      method: "group_by",
      fields,
      cached: false,
      ms: 1,
      warnings: [],
      ...options.meta,
    },
  };
}
