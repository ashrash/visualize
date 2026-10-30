import type { Column, Predicate } from "../../api/types";

export type PredicateOp = Predicate["op"];

export const PREDICATE_OPS: { value: PredicateOp; label: string }[] = [
  { value: "eq", label: "equals" },
  { value: "in", label: "is one of" },
  { value: "not_in", label: "is not one of" },
  { value: "range", label: "between (numbers)" },
  { value: "between_dates", label: "between (dates)" },
  { value: "is_null", label: "is empty / not empty" },
];

export function createPredicate(op: PredicateOp, field: string): Predicate {
  switch (op) {
    case "eq":
      return { op, field, value: null };
    case "in":
    case "not_in":
      return { op, field, values: [] };
    case "range":
    case "between_dates":
      return { op, field, min: null, max: null, includeMin: true, includeMax: true };
    case "is_null":
      return { op, field, isNull: true };
  }
}

/**
 * Switch operator, keeping the field and any values that still make sense: eq <-> in/not_in
 * keep the value(s), range <-> between_dates keep the bounds.
 */
export function changePredicateOp(predicate: Predicate, op: PredicateOp): Predicate {
  if (predicate.op === op) return predicate;
  const next = createPredicate(op, predicate.field);
  const values: Scalar[] | null =
    predicate.op === "in" || predicate.op === "not_in"
      ? predicate.values
      : predicate.op === "eq" && predicate.value !== null
        ? [predicate.value]
        : null;
  if (values && (next.op === "in" || next.op === "not_in")) return { ...next, values };
  if (values && next.op === "eq") return { ...next, value: values[0] ?? null };
  const isRange = predicate.op === "range" || predicate.op === "between_dates";
  if (isRange && (next.op === "range" || next.op === "between_dates")) {
    const bound = (v: string | number | null | undefined) =>
      v === null || v === undefined || v === ""
        ? null
        : next.op === "range"
          ? Number(v)
          : String(v);
    const min = bound(predicate.min);
    const max = bound(predicate.max);
    return {
      ...next,
      min: typeof min === "number" && Number.isNaN(min) ? null : min,
      max: typeof max === "number" && Number.isNaN(max) ? null : max,
    };
  }
  return next;
}

/** Text typed into a value box -> JSON scalar; numbers for numeric columns, else strings. */
export function parseScalar(text: string, column?: Column): string | number | boolean | null {
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (column?.semantic === "quantitative") {
    const n = Number(trimmed);
    if (Number.isFinite(n)) return n;
  }
  return trimmed;
}

export function parseList(text: string, column?: Column): (string | number | boolean | null)[] {
  return text
    .split(",")
    .map((part) => parseScalar(part, column))
    .filter((v) => v !== null);
}

export type Scalar = string | number | boolean | null;

export function formatScalar(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}
