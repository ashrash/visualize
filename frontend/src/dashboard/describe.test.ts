import { describe, expect, it } from "vitest";
import type { Predicate } from "../api/types";
import { activeFilterLabels, describePredicate } from "./describe";
import type { Dashboard, DashboardFilter } from "./types";

const eq = (field: string, value: string): Predicate => ({ op: "eq", field, value });
const filter = (
  id: string,
  field: string,
  predicate: Predicate | null = null,
): DashboardFilter => ({
  id,
  label: id,
  field,
  fieldMap: {},
  predicate,
  control: "dropdown",
});
const dashboard = (filters: DashboardFilter[]) =>
  ({ id: "d", name: "d", tabs: [], filters, linkGroups: [] }) as Dashboard;

describe("describePredicate", () => {
  it("labels each predicate kind", () => {
    expect(describePredicate({ op: "in", field: "r", values: ["N", null] })).toBe("r: N, (empty)");
    expect(describePredicate(eq("r", "N"))).toBe("r = N");
    expect(
      describePredicate({
        op: "between_dates",
        field: "d",
        min: "2024-01-01T00:00:00Z",
        max: null,
        includeMin: true,
        includeMax: true,
      }),
    ).toBe("d: 2024-01-01 to any");
  });
});

describe("activeFilterLabels", () => {
  const base = { globalFilters: [], filterValues: {}, globalValues: {}, drill: [], bus: {} };

  it("includes dashboard filter controls: the live value, or the saved default", () => {
    const d = dashboard([filter("a", "region"), filter("b", "tier", eq("tier", "gold"))]);
    expect(
      activeFilterLabels({ ...base, dashboard: d, filterValues: { a: eq("region", "North") } }),
    ).toEqual(["region = North", "tier = gold"]);
  });

  it("leaves out a filter the viewer cleared, even if it has a saved default", () => {
    const d = dashboard([filter("b", "tier", eq("tier", "gold"))]);
    expect(activeFilterLabels({ ...base, dashboard: d, filterValues: { b: null } })).toEqual([]);
  });

  it("includes global filters, drill predicates and chart selections, without repeats", () => {
    const labels = activeFilterLabels({
      ...base,
      dashboard: dashboard([]),
      globalFilters: [filter("g", "year", eq("year", "2024"))],
      globalValues: {},
      drill: [eq("region", "West")],
      bus: {
        grp: {
          w1: { sourceId: "d", predicates: [eq("region", "West")] },
          w2: { sourceId: "d", predicates: [{ op: "in", field: "tier", values: ["gold"] }] },
        },
      },
    });
    expect(labels).toEqual(["year = 2024", "region = West", "tier: gold"]);
  });
});
