import type { Column, Predicate } from "../api/types";

export type Scalar = string | number | boolean | null;

/** What the filter popover edits. One predicate per column. */
export type FilterDraft =
  | { mode: "values"; values: Scalar[] }
  | { mode: "range"; min: string; max: string }
  | { mode: "null"; isNull: boolean };

export type FilterMode = FilterDraft["mode"];

/** Column filters keyed by field name; the request sends their values as `filters`. */
export type FilterMap = Record<string, Predicate>;

export function isRangeColumn(column: Pick<Column, "semantic">): boolean {
  return column.semantic === "quantitative" || column.semantic === "temporal";
}

/** Values checklist for categorical columns, a range for numeric and temporal ones. */
export function defaultMode(column: Pick<Column, "semantic">): FilterMode {
  return isRangeColumn(column) ? "range" : "values";
}

export function emptyDraft(mode: FilterMode): FilterDraft {
  if (mode === "values") return { mode, values: [] };
  if (mode === "range") return { mode, min: "", max: "" };
  return { mode, isNull: true };
}

export function draftFromPredicate(
  predicate: Predicate | undefined,
  column: Pick<Column, "semantic">,
): FilterDraft {
  if (!predicate) return emptyDraft(defaultMode(column));
  switch (predicate.op) {
    case "in":
      return { mode: "values", values: predicate.values };
    case "eq":
      return { mode: "values", values: [predicate.value] };
    case "range":
    case "between_dates":
      return {
        mode: "range",
        min: predicate.min === null || predicate.min === undefined ? "" : String(predicate.min),
        max: predicate.max === null || predicate.max === undefined ? "" : String(predicate.max),
      };
    case "is_null":
      return { mode: "null", isNull: predicate.isNull };
    case "not_in":
      return { mode: "values", values: [] };
  }
}

/** The predicate for a draft, or null when the draft filters nothing. */
export function predicateFromDraft(
  column: Pick<Column, "name" | "semantic">,
  draft: FilterDraft,
): Predicate | null {
  const field = column.name;
  switch (draft.mode) {
    case "values":
      return draft.values.length === 0 ? null : { op: "in", field, values: draft.values };
    case "null":
      return { op: "is_null", field, isNull: draft.isNull };
    case "range": {
      const min = draft.min.trim();
      const max = draft.max.trim();
      if (min === "" && max === "") return null;
      if (column.semantic === "temporal") {
        return {
          op: "between_dates",
          field,
          min: min || null,
          max: max || null,
          includeMin: true,
          includeMax: true,
        };
      }
      const lo = min === "" ? null : Number(min);
      const hi = max === "" ? null : Number(max);
      if ((lo !== null && Number.isNaN(lo)) || (hi !== null && Number.isNaN(hi))) return null;
      return { op: "range", field, min: lo, max: hi, includeMin: true, includeMax: true };
    }
  }
}

/** Set or clear one column's filter. */
export function setFilter(map: FilterMap, field: string, predicate: Predicate | null): FilterMap {
  if (predicate === null) {
    const { [field]: _removed, ...rest } = map;
    return rest;
  }
  return { ...map, [field]: predicate };
}

export function filterList(map: FilterMap): Predicate[] {
  return Object.values(map);
}

/** Short text for the active-filter chip, e.g. `region: 2 values`. */
export function describeFilter(predicate: Predicate): string {
  switch (predicate.op) {
    case "in":
    case "not_in":
      return `${predicate.field}: ${predicate.values.length} value${predicate.values.length === 1 ? "" : "s"}`;
    case "eq":
      return `${predicate.field} = ${String(predicate.value)}`;
    case "range":
    case "between_dates":
      return `${predicate.field}: ${predicate.min ?? "…"} to ${predicate.max ?? "…"}`;
    case "is_null":
      return `${predicate.field} is ${predicate.isNull ? "empty" : "not empty"}`;
  }
}
