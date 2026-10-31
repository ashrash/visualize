import { describe, expect, it } from "vitest";
import type { Column, Predicate } from "../api/types";
import { drillFilters } from "./drillFilters";

const columns = [{ name: "region" }, { name: "amount" }] as Column[];

describe("drillFilters", () => {
  it("keeps predicates on existing columns and skips the rest", () => {
    const p: Predicate[] = [
      { op: "in", field: "region", values: ["N"] },
      { op: "in", field: "calc_col", values: [1] },
    ];
    expect(drillFilters(p, columns)).toEqual({ region: p[0] });
  });
  it("returns an empty map for no predicates", () => {
    expect(drillFilters([], columns)).toEqual({});
  });
});
