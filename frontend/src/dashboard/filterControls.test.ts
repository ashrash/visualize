import { describe, expect, it } from "vitest";
import type { Pipeline } from "../api/types";
import {
  addDays,
  datasetOfSource,
  dateRangePredicate,
  dateRangeValue,
  membershipPredicate,
  membershipValue,
  rangePredicate,
  rangeValue,
} from "./filterControls";

describe("date range control", () => {
  it("makes a half-open range that keeps the whole last day", () => {
    expect(dateRangePredicate("d", "2024-01-01", "2024-01-31")).toEqual({
      op: "between_dates",
      field: "d",
      min: "2024-01-01",
      max: "2024-02-01",
      includeMin: true,
      includeMax: false,
    });
  });

  it("round-trips through the predicate, including month and leap-year ends", () => {
    for (const [from, to] of [
      ["2024-01-01", "2024-01-31"],
      ["2024-02-01", "2024-02-29"],
      ["2023-12-31", "2023-12-31"],
    ] as const) {
      expect(dateRangeValue(dateRangePredicate("d", from, to))).toEqual({ from, to });
    }
    expect(addDays("2024-02-28", 1)).toBe("2024-02-29");
    expect(addDays("2023-02-28", 1)).toBe("2023-03-01");
  });

  it("leaves a bound open when empty, and no filter when both are", () => {
    expect(dateRangePredicate("d", "2024-01-01", "")).toMatchObject({
      min: "2024-01-01",
      max: null,
    });
    expect(dateRangePredicate("d", "", "")).toBeNull();
    expect(dateRangePredicate("d", "garbage", "")).toBeNull();
    expect(dateRangeValue(null)).toEqual({ from: "", to: "" });
  });
});

describe("range control", () => {
  it("is no filter while it covers the domain", () => {
    expect(rangePredicate("p", 0, 100, [0, 100])).toBeNull();
    expect(rangePredicate("p", 10, 100, [0, 100])).toMatchObject({
      op: "range",
      min: 10,
      max: 100,
    });
  });
  it("orders reversed bounds and reads its value back", () => {
    const p = rangePredicate("p", 80, 20, [0, 100]);
    expect(p).toMatchObject({ min: 20, max: 80 });
    expect(rangeValue(p, [0, 100])).toEqual([20, 80]);
    expect(rangeValue(null, [0, 100])).toEqual([0, 100]);
  });
});

describe("dropdown control", () => {
  it("is no filter when empty", () => {
    expect(membershipPredicate("r", [])).toBeNull();
    const p = membershipPredicate("r", ["N", null]);
    expect(p).toEqual({ op: "in", field: "r", values: ["N", null] });
    expect(membershipValue(p)).toEqual(["N", null]);
  });
});

describe("datasetOfSource", () => {
  const pipe = (id: string, source: Pipeline["source"]) =>
    ({ id, source, steps: [] }) as unknown as Pipeline;
  it("follows chained pipelines to the dataset, and tolerates a missing or cyclic pipeline", () => {
    const pipelines = [
      pipe("p2", { kind: "pipeline", id: "p1" }),
      pipe("p1", { kind: "dataset", id: "d1" }),
    ];
    expect(datasetOfSource({ kind: "pipeline", id: "p2" }, pipelines)).toBe("d1");
    expect(datasetOfSource({ kind: "dataset", id: "d9" }, pipelines)).toBe("d9");
    expect(datasetOfSource({ kind: "pipeline", id: "gone" }, pipelines)).toBe("gone");
    const cyc = [
      pipe("a", { kind: "pipeline", id: "b" }),
      pipe("b", { kind: "pipeline", id: "a" }),
    ];
    expect(() => datasetOfSource({ kind: "pipeline", id: "a" }, cyc)).not.toThrow();
  });
});
