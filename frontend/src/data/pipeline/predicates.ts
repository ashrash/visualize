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

/** Switch operator, keeping the field. Values that don't carry over are reset. */
export function changePredicateOp(predicate: Predicate, op: PredicateOp): Predicate {
  return predicate.op === op ? predicate : createPredicate(op, predicate.field);
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

export function formatScalar(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}
