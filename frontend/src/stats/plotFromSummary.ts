import type { ChartSpec, FieldSummary, SourceRef } from "../api/types";

/**
 * A chart spec for the "plot this" shortcut on a numeric field's descriptive statistics: a
 * histogram of its distribution, or (when the describe request was grouped) a box plot across
 * groups. Both plot types are registered keys (Plots.md §2) with real encodings the server-side
 * chartdata endpoint understands; only their renderers are still being built (Wave 2, the
 * `plots-distribution-relationship` agent) — until that lands, ChartView shows a "not available
 * yet" notice for them rather than failing, so the shortcut is real and will light up on its own.
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
