import type { ColumnKind } from "./arrow";

const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 });

const INTEGER_TYPE = /^U?(TINY|SMALL|BIG|HUGE)?INT(EGER)?$/i;

/**
 * Display text for a value. Grouping follows the column type, not the value: integer columns
 * print plain (years, ids), while a DOUBLE column prints "1,500" and "1,500.5" alike.
 */
export function formatValue(
  value: unknown,
  kind: ColumnKind | undefined,
  columnType?: string,
): string {
  if (value === null || value === undefined) return "";
  if (kind === "temporal" && typeof value === "number") {
    const iso = new Date(value).toISOString();
    return iso.endsWith("T00:00:00.000Z") ? iso.slice(0, 10) : iso.replace("T", " ").slice(0, 19);
  }
  if (typeof value === "number") {
    const plain = columnType ? INTEGER_TYPE.test(columnType) : Number.isInteger(value);
    return plain ? String(value) : numberFormat.format(value);
  }
  return String(value);
}
