import { describe, expect, it } from "vitest";
import type { Column, Pipeline } from "../../api/types";
import { columnsAt } from "./schema";
import {
  createPipeline,
  createStep,
  pipelineReducer,
  STEP_META,
  STEP_OPS,
  summarizeStep,
} from "./stepReducer";

const col = (name: string, semantic: Column["semantic"] = "nominal"): Column => ({
  name,
  type: semantic === "quantitative" ? "DOUBLE" : "VARCHAR",
  semantic,
  nullCount: 0,
  approxDistinct: 5,
});
const columns = [col("region"), col("units", "quantitative"), col("sales", "quantitative")];

const empty = (): Pipeline => createPipeline({ id: "d1", name: "Sales" });
const ids = (p: Pipeline) => (p.steps ?? []).map((s) => s.id);

function withSteps(n: number): Pipeline {
  let p = empty();
  for (let i = 0; i < n; i++) {
    p = pipelineReducer(p, {
      type: "add",
      step: { ...createStep("dedupe", columns), id: `s${i}` },
    });
  }
  return p;
}

describe("pipelineReducer", () => {
  it("adds at the end or at an index", () => {
    let p = withSteps(2);
    p = pipelineReducer(p, {
      type: "add",
      step: { ...createStep("sort", columns), id: "x" },
      index: 1,
    });
    expect(ids(p)).toEqual(["s0", "x", "s1"]);
    p = pipelineReducer(p, {
      type: "add",
      step: { ...createStep("sort", columns), id: "y" },
      index: 99,
    });
    expect(ids(p)).toEqual(["s0", "x", "s1", "y"]);
  });

  it("removes by id and ignores unknown ids", () => {
    const p = withSteps(3);
    expect(ids(pipelineReducer(p, { type: "remove", id: "s1" }))).toEqual(["s0", "s2"]);
    expect(ids(pipelineReducer(p, { type: "remove", id: "nope" }))).toEqual(["s0", "s1", "s2"]);
  });

  it("moves a step to an index, clamped", () => {
    const p = withSteps(3);
    expect(ids(pipelineReducer(p, { type: "move", id: "s0", to: 2 }))).toEqual(["s1", "s2", "s0"]);
    expect(ids(pipelineReducer(p, { type: "move", id: "s2", to: 0 }))).toEqual(["s2", "s0", "s1"]);
    expect(ids(pipelineReducer(p, { type: "move", id: "s2", to: -5 }))).toEqual(["s2", "s0", "s1"]);
    expect(pipelineReducer(p, { type: "move", id: "s1", to: 1 })).toBe(p);
    expect(pipelineReducer(p, { type: "move", id: "nope", to: 0 })).toBe(p);
  });

  it("toggles enabled without touching other steps", () => {
    const p = pipelineReducer(withSteps(2), { type: "toggle", id: "s1" });
    expect(p.steps?.map((s) => s.enabled)).toEqual([true, false]);
  });

  it("replaces a step in place and does not mutate the input", () => {
    const p = withSteps(2);
    const first = p.steps?.[0];
    if (first?.op !== "dedupe") throw new Error("setup");
    const next = pipelineReducer(p, { type: "replace", step: { ...first, keep: "last" } });
    expect(next.steps?.[0]).toMatchObject({ keep: "last" });
    expect(p.steps?.[0]).toMatchObject({ keep: "first" });
  });

  it("renames the pipeline", () => {
    expect(pipelineReducer(empty(), { type: "rename", name: "Clean" }).name).toBe("Clean");
  });
});

describe("createStep", () => {
  it("makes a step for each of the 15 ops with a summary", () => {
    expect(STEP_OPS).toHaveLength(15);
    for (const op of STEP_OPS) {
      const step = createStep(op, columns);
      expect(step.op).toBe(op);
      expect(step.enabled).toBe(true);
      expect(STEP_META[op].label).not.toBe("");
      expect(typeof summarizeStep(step)).toBe("string");
    }
  });

  it("works with no columns", () => {
    for (const op of STEP_OPS) expect(createStep(op, []).op).toBe(op);
  });
});

describe("columnsAt", () => {
  it("propagates select, rename and calc, and skips disabled steps", () => {
    const select = { ...createStep("select", columns), id: "a" };
    if (select.op !== "select") throw new Error("setup");
    select.fields = ["region", "sales"];
    const rename = { ...createStep("rename", columns), id: "b" };
    if (rename.op !== "rename") throw new Error("setup");
    rename.mapping = { sales: "revenue" };
    const calc = { ...createStep("calc", columns), id: "c", enabled: false };
    if (calc.op !== "calc") throw new Error("setup");
    calc.name = "double";
    const steps = [select, rename, calc];

    expect(columnsAt(columns, steps).map((c) => c.name)).toEqual(["region", "revenue"]);
    expect(columnsAt(columns, steps, "b").map((c) => c.name)).toEqual(["region", "sales"]);
    expect(columnsAt(columns, steps, "a").map((c) => c.name)).toEqual(["region", "units", "sales"]);
  });

  it("group_aggregate outputs keys and measures", () => {
    const g = createStep("group_aggregate", columns);
    if (g.op !== "group_aggregate") throw new Error("setup");
    g.by = ["region"];
    g.aggregations = [{ field: "sales", fn: "sum", asName: "total" }];
    expect(columnsAt(columns, [g]).map((c) => c.name)).toEqual(["region", "total"]);
  });
});
