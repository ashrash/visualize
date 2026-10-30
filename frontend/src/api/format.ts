import type { ColumnKind } from "./arrow";

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 });

/** Display text for a value from a ColumnarResult (temporal values are epoch ms). */
export function formatValue(value: unknown, kind: ColumnKind | undefined): string {
  if (value === null || value === undefined) return "";
  if (kind === "temporal" && typeof value === "number") {
    const iso = new Date(value).toISOString();
    return iso.endsWith("T00:00:00.000Z") ? iso.slice(0, 10) : iso.replace("T", " ").slice(0, 19);
  }
  // Integers plain (years, ids: "2024", not "2,024"); decimals keep digit grouping.
  if (typeof value === "number")
    return Number.isInteger(value) ? String(value) : numberFormat.format(value);
  return String(value);
}
