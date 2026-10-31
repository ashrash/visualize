import { beforeEach, describe, expect, it } from "vitest";
import type { Predicate } from "../api/types";
import { selectionOf } from "./bus";
import { useDashboardRuntime } from "./runtimeStore";

const inP = (...values: string[]): Predicate => ({ op: "in", field: "region", values });
const s = () => useDashboardRuntime.getState();
const args = (predicates: Predicate[], mode: "toggle" | "replace" | "extend") => ({
  groupId: "g",
  widgetId: "w1",
  sourceId: "ds",
  predicates,
  mode,
});

describe("dashboard runtime store", () => {
  beforeEach(() => {
    useDashboardRuntime.setState({ globalValues: {} });
    s().open("d1");
  });

  it("toggles: the same click twice clears the selection", () => {
    s().interact(args([inP("N")], "toggle"));
    expect(selectionOf(s().bus, "g", "w1")).toEqual([inP("N")]);
    s().interact(args([inP("N")], "toggle"));
    expect(s().bus).toEqual({});
  });

  it("replaces on a different click, and extends for multi-select", () => {
    s().interact(args([inP("N")], "toggle"));
    s().interact(args([inP("S")], "toggle"));
    expect(selectionOf(s().bus, "g", "w1")).toEqual([inP("S")]);
    s().interact(args([inP("N")], "extend"));
    expect(selectionOf(s().bus, "g", "w1")).toEqual([inP("S", "N")]);
  });

  it("replace with no predicates clears (an emptied brush)", () => {
    s().interact(args([inP("N")], "replace"));
    s().interact(args([], "replace"));
    expect(s().bus).toEqual({});
  });

  it("opening a dashboard starts clean, keeping only the drill predicates it was given", () => {
    s().interact(args([inP("N")], "replace"));
    s().setFilter("f", null);
    s().open("d2", { drill: [inP("W")] });
    expect(s().bus).toEqual({});
    expect(s().filterValues).toEqual({});
    expect(s().drill).toEqual([inP("W")]);
    expect(s().dashboardId).toBe("d2");
  });

  it("does not change state (no re-render) when clearing nothing", () => {
    const before = s();
    s().clearSelection("g", "w1");
    expect(s().bus).toBe(before.bus);
  });

  it("keeps global values across dashboards", () => {
    s().setGlobal("g1", inP("N"));
    s().open("d3");
    expect(s().globalValues).toEqual({ g1: inP("N") });
  });
});
