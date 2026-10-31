import type { Predicate } from "../api/types";

/** A short human label for a predicate, for selection chips and filter summaries. */
export function describePredicate(p: Predicate): string {
  switch (p.op) {
    case "in":
      return `${p.field}: ${p.values.map((v) => (v === null ? "(empty)" : String(v))).join(", ")}`;
    case "not_in":
      return `${p.field} not ${p.values.map((v) => (v === null ? "(empty)" : String(v))).join(", ")}`;
    case "eq":
      return `${p.field} = ${p.value === null ? "(empty)" : String(p.value)}`;
    case "is_null":
      return p.isNull ? `${p.field} is empty` : `${p.field} is not empty`;
    case "range":
    case "between_dates": {
      const fmt = (v: number | string | null | undefined) =>
        p.op === "between_dates" && typeof v === "string" ? v.slice(0, 10) : String(v);
      const lo = p.min === null || p.min === undefined ? "any" : fmt(p.min);
      const hi = p.max === null || p.max === undefined ? "any" : fmt(p.max);
      return `${p.field}: ${lo} to ${hi}`;
    }
  }
}
