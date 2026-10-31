import { describe, expect, it } from "vitest";
import {
  applyDrill,
  canDrillDown,
  drillCrumbs,
  drillInto,
  drillLevels,
  drillTo,
  resolveDrillValue,
  toDrillValue,
} from "./drill";
import { makeData, makeSpec } from "./testing";
import type { PlotEntry } from "./types";

const entry = { drill: { channel: "x" } } as Pick<PlotEntry, "drill">;
const levels = ["country", "state", "city"];
const spec = (drillHierarchy: string[] = levels) =>
  makeSpec({
    encodings: {
      x: [{ field: "country", sort: "desc", timeUnit: "month" }],
      y: [{ field: "sales", agg: "sum" }],
    },
    interactions: { zoom: true, brush: false, clickFilter: true, drillHierarchy },
  });

describe("drillLevels", () => {
  it("is empty when the plot cannot drill or the hierarchy has fewer than two levels", () => {
    expect(drillLevels(spec(), {})).toEqual([]);
    expect(drillLevels(spec(["country"]), entry)).toEqual([]);
    expect(drillLevels(spec(), entry)).toEqual(levels);
  });

  it("drops blanks and repeats", () => {
    expect(drillLevels(spec(["country", "", "country", "state"]), entry)).toEqual([
      "country",
      "state",
    ]);
  });
});

describe("applyDrill", () => {
  it("shows the first level at the top and adds no predicate", () => {
    const out = applyDrill(spec(), entry, []);
    expect(out.spec.encodings?.x?.[0]?.field).toBe("country");
    expect(out.predicates).toEqual([]);
  });

  it("swaps the channel field per depth and filters on the path", () => {
    const out = applyDrill(spec(), entry, ["Germany", 7]);
    expect(out.spec.encodings?.x?.[0]?.field).toBe("city");
    expect(out.predicates).toEqual([
      { op: "eq", field: "country", value: "Germany" },
      { op: "eq", field: "state", value: 7 },
    ]);
    // y and the rest of the spec stay as they were; the time unit belonged to the old field
    expect(out.spec.encodings?.y).toEqual(spec().encodings?.y);
    expect(out.spec.encodings?.x?.[0]?.timeUnit).toBeUndefined();
    expect(out.spec.encodings?.x?.[0]?.sort).toBe("desc");
  });

  it("uses is_null for a null step and clamps a path that is too deep", () => {
    const out = applyDrill(spec(), entry, [null, "x", "y", "z"]);
    expect(out.spec.encodings?.x?.[0]?.field).toBe("city");
    expect(out.predicates).toEqual([
      { op: "is_null", field: "country", isNull: true },
      { op: "eq", field: "state", value: "x" },
    ]);
  });

  it("returns the spec untouched when drilling does not apply", () => {
    const s = spec();
    expect(applyDrill(s, {}, ["Germany"])).toEqual({ spec: s, predicates: [], depth: 0 });
  });
});

describe("path helpers", () => {
  it("drills in until the last level, and back to any ancestor", () => {
    expect(canDrillDown(levels, [])).toBe(true);
    expect(canDrillDown(levels, ["a"])).toBe(true);
    expect(canDrillDown(levels, ["a", "b"])).toBe(false);
    expect(drillInto(["a"], "b")).toEqual(["a", "b"]);
    expect(drillTo(["a", "b"], 1)).toEqual(["a"]);
    expect(drillTo(["a", "b"], 0)).toEqual([]);
  });

  it("builds breadcrumbs: All, then one per step", () => {
    expect(drillCrumbs(["Germany", null])).toEqual([
      { label: "All", depth: 0 },
      { label: "Germany", depth: 1 },
      { label: "(blank)", depth: 2 },
    ]);
  });
});

describe("resolveDrillValue", () => {
  it("maps a clicked label back to the raw column value", () => {
    const data = makeData({ x: [2024, 2025], y0: [1, 2] });
    expect(resolveDrillValue(data, "2025")).toEqual({ value: 2025 });
    expect(resolveDrillValue(data, "2026")).toBeUndefined();
  });

  it("matches the label as the chart formatted it", () => {
    const data = makeData({ x: [Date.UTC(2026, 0, 1)], y0: [1] }, {}, { kinds: { x: "temporal" } });
    // A Predicate on a date column takes ISO text; an epoch number cannot be cast to a TIMESTAMP.
    expect(resolveDrillValue(data, "2026-01-01")).toEqual({ value: "2026-01-01" });
  });

  it("gives temporal values as ISO text, date-only at UTC midnight", () => {
    expect(toDrillValue(Date.UTC(2026, 0, 1), "temporal")).toBe("2026-01-01");
    expect(toDrillValue(Date.UTC(2026, 0, 1, 13, 30), "temporal")).toBe("2026-01-01T13:30:00.000Z");
    expect(toDrillValue(new Date(Date.UTC(2026, 5, 2)), "temporal")).toBe("2026-06-02");
    expect(toDrillValue(Number.NaN, "temporal")).toBeNull();
    // Numbers in a non-temporal column stay numbers, and bigints become numbers.
    expect(toDrillValue(1767225600000, "number")).toBe(1767225600000);
    expect(toDrillValue(10n, "number")).toBe(10);
  });

  it("handles blanks", () => {
    const data = makeData({ x: ["a", null], y0: [1, 2] });
    expect(resolveDrillValue(data, "(blank)")).toEqual({ value: null });
  });
});
