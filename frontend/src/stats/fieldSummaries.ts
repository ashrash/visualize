import type { FieldSummary, StatResult } from "../api/types";

/**
 * `StatResult.values` is typed as `{[key: string]: unknown}` on the wire (it is a free-form
 * JSON bag by design, per contracts/stats.py), so `values.summaries` needs a runtime check
 * before use. `/api/stats/describe` is real and the server always sends this shape; this guards
 * against a malformed or empty response rather than against an untrusted source.
 */
export function summariesOf(result: StatResult | undefined): FieldSummary[] {
  const raw = result?.values?.summaries;
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (item): item is FieldSummary =>
      !!item && typeof item === "object" && "field" in item && "kind" in item,
  );
}

/** Rows grouped first by field, each row one group (or the single overall row). */
export function groupSummaries(summaries: FieldSummary[]): Map<string, FieldSummary[]> {
  const byField = new Map<string, FieldSummary[]>();
  for (const summary of summaries) {
    const list = byField.get(summary.field) ?? [];
    list.push(summary);
    byField.set(summary.field, list);
  }
  return byField;
}

export const groupLabel = (group: FieldSummary["group"]) =>
  group === null || group.length === 0 ? "Overall" : group.map(String).join(" / ");
