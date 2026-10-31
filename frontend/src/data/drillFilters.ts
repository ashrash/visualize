import type { Column, Predicate } from "../api/types";
import type { FilterMap } from "./filters";

/**
 * Initial grid filters for predicates drilled in from a dashboard. Predicates name the pipeline
 * output's fields, so any whose field is not a column of the grid's dataset is skipped (known
 * limitation: no pipeline-to-dataset field mapping). The grid holds one predicate per column.
 */
export function drillFilters(
  predicates: readonly Predicate[],
  columns: readonly Pick<Column, "name">[],
): FilterMap {
  const names = new Set(columns.map((c) => c.name));
  const map: FilterMap = {};
  for (const p of predicates) if (names.has(p.field)) map[p.field] = p;
  return map;
}
