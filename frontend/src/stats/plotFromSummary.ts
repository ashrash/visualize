import type { ChartSpec, FieldSummary, SourceRef } from "../api/types";

/**
 * A chart spec for the "plot this" shortcut on a numeric field's descriptive statistics: a
 * histogram of its distribution, or (when the describe request was grouped) a box plot across
 * groups. Both renderers are real (plots-distribution-relationship, Plots.md §2): histogram takes
 * the value on `x`; box takes the value on `y` and the grouping field on `x`.
 */
export function plotFromSummary(
  summary: FieldSummary,
  source: SourceRef,
  groupBy: string[] = [],
): ChartSpec {
  const grouped = summary.group !== null && summary.group.length > 0 && groupBy.length > 0;
  return {
    id: crypto.randomUUID(),
    name: grouped ? `${summary.field} by ${groupBy[0]}` : `${summary.field} distribution`,
    plotType: grouped ? "box" : "histogram",
    source,
    encodings: grouped
      ? { x: [{ field: groupBy[0] }], y: [{ field: summary.field }] }
      : { x: [{ field: summary.field }] },
  };
}
