import type { Column, Dataset, Pipeline, PipelineStep, StepOp } from "../../api/types";

/** Everything the editor needs to know about a step type, in one table. */
export const STEP_META: Record<StepOp, { label: string; description: string }> = {
  filter: { label: "Filter rows", description: "Keep rows that match conditions or a formula." },
  sort: { label: "Sort", description: "Order rows by one or more columns." },
  select: { label: "Select columns", description: "Keep only the listed columns, in this order." },
  rename: { label: "Rename columns", description: "Give columns new names." },
  cast: { label: "Change type", description: "Convert columns to another data type." },
  fill_missing: { label: "Fill missing", description: "Replace nulls with a value or statistic." },
  drop_missing: { label: "Drop missing", description: "Remove rows that contain nulls." },
  dedupe: { label: "Remove duplicates", description: "Keep one row per unique key." },
  calc: { label: "Calculated column", description: "Add a column from a formula." },
  group_aggregate: { label: "Group and aggregate", description: "Summarise rows per group." },
  join: { label: "Join", description: "Combine with another dataset on matching keys." },
  union: { label: "Union", description: "Append rows from other datasets." },
  pivot: { label: "Pivot", description: "Turn row values into columns." },
  unpivot: { label: "Unpivot", description: "Turn columns into name/value rows." },
  normalize: { label: "Normalize", description: "Rescale numeric columns (z-score, min-max)." },
};

export const STEP_OPS = Object.keys(STEP_META) as StepOp[];

export function newStepId(): string {
  return crypto.randomUUID();
}

/** A new, valid-looking step with sensible defaults taken from the available columns. */
export function createStep(op: StepOp, columns: Column[]): PipelineStep {
  const base = { id: newStepId(), enabled: true, materialize: false };
  const first = columns[0]?.name ?? "";
  const numeric = columns.find((c) => c.semantic === "quantitative")?.name ?? first;
  switch (op) {
    case "filter":
      return { ...base, op, predicates: [], expression: null };
    case "sort":
      return {
        ...base,
        op,
        keys: first ? [{ field: first, descending: false, nullsLast: true }] : [],
      };
    case "select":
      return { ...base, op, fields: columns.map((c) => c.name) };
    case "rename":
      return { ...base, op, mapping: {} };
    case "cast":
      return { ...base, op, casts: first ? [{ field: first, to: "string" }] : [] };
    case "fill_missing":
      return {
        ...base,
        op,
        fills: first ? [{ field: first, strategy: "value", value: null }] : [],
      };
    case "drop_missing":
      return { ...base, op, fields: null, how: "any" };
    case "dedupe":
      return { ...base, op, fields: null, keep: "first" };
    case "calc":
      return { ...base, op, name: "new_column", expression: "" };
    case "group_aggregate":
      return {
        ...base,
        op,
        by: first ? [first] : [],
        aggregations: [{ field: null, fn: "count", asName: "count" }],
      };
    case "join":
      return {
        ...base,
        op,
        right: { kind: "dataset", id: "" },
        how: "left",
        on: [],
        suffix: "_right",
      };
    case "union":
      return { ...base, op, others: [], byName: true };
    case "pivot":
      return { ...base, op, index: [], columns: first, values: numeric, fn: "sum" };
    case "unpivot":
      return {
        ...base,
        op,
        idFields: [],
        valueFields: [],
        nameField: "variable",
        valueField: "value",
      };
    case "normalize":
      return { ...base, op, fields: numeric ? [numeric] : [], method: "zscore" };
  }
}

export function createPipeline(dataset: Pick<Dataset, "id" | "name">): Pipeline {
  return {
    id: crypto.randomUUID(),
    name: `${dataset.name} pipeline`,
    source: { kind: "dataset", id: dataset.id },
    steps: [],
  };
}

export type PipelineAction =
  | { type: "add"; step: PipelineStep; index?: number }
  | { type: "remove"; id: string }
  | { type: "move"; id: string; to: number }
  | { type: "toggle"; id: string }
  | { type: "replace"; step: PipelineStep }
  | { type: "rename"; name: string };

/** Pure reducer over a pipeline's ordered step list. Unknown ids leave the pipeline unchanged. */
export function pipelineReducer(pipeline: Pipeline, action: PipelineAction): Pipeline {
  const steps = pipeline.steps ?? [];
  switch (action.type) {
    case "add": {
      const at = clamp(action.index ?? steps.length, 0, steps.length);
      return { ...pipeline, steps: [...steps.slice(0, at), action.step, ...steps.slice(at)] };
    }
    case "remove":
      return { ...pipeline, steps: steps.filter((s) => s.id !== action.id) };
    case "move": {
      const from = steps.findIndex((s) => s.id === action.id);
      if (from < 0) return pipeline;
      const to = clamp(action.to, 0, steps.length - 1);
      if (to === from) return pipeline;
      const next = [...steps];
      const [moved] = next.splice(from, 1);
      if (!moved) return pipeline;
      next.splice(to, 0, moved);
      return { ...pipeline, steps: next };
    }
    case "toggle":
      return {
        ...pipeline,
        steps: steps.map((s) => (s.id === action.id ? { ...s, enabled: !s.enabled } : s)),
      };
    case "replace":
      return {
        ...pipeline,
        steps: steps.map((s) => (s.id === action.step.id ? action.step : s)),
      };
    case "rename":
      return { ...pipeline, name: action.name };
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** One-line description shown in the step list. */
export function summarizeStep(step: PipelineStep): string {
  const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? "" : "s"}`;
  switch (step.op) {
    case "filter": {
      const n = (step.predicates?.length ?? 0) + (step.expression?.trim() ? 1 : 0);
      return n === 0 ? "No conditions yet" : count(n, "condition");
    }
    case "sort":
      return (
        step.keys.map((k) => `${k.field} ${k.descending ? "desc" : "asc"}`).join(", ") ||
        "No keys yet"
      );
    case "select":
      return count(step.fields.length, "column");
    case "rename":
      return count(Object.keys(step.mapping).length, "rename");
    case "cast":
      return step.casts.map((c) => `${c.field} → ${c.to}`).join(", ") || "No casts yet";
    case "fill_missing":
      return step.fills.map((f) => `${f.field}: ${f.strategy}`).join(", ") || "No fills yet";
    case "drop_missing":
      return `${step.how === "all" ? "all" : "any"} null in ${step.fields?.join(", ") ?? "any column"}`;
    case "dedupe":
      return `keep ${step.keep} by ${step.fields?.join(", ") ?? "whole row"}`;
    case "calc":
      return step.name ? `${step.name} = ${step.expression || "…"}` : "Unnamed column";
    case "group_aggregate":
      return `by ${step.by.join(", ") || "(all rows)"}: ${count(step.aggregations.length, "measure")}`;
    case "join":
      return `${step.how} join on ${count(step.on.length, "key")}`;
    case "union":
      return `with ${count(step.others.length, "dataset")}`;
    case "pivot":
      return `${step.columns} → columns, ${step.fn}(${step.values})`;
    case "unpivot":
      return `${count(step.valueFields.length, "column")} → ${step.nameField}/${step.valueField}`;
    case "normalize":
      return `${step.method}: ${step.fields.join(", ") || "no columns"}`;
  }
}
