import { formatDateAuto } from "./format";
import type { ChartData } from "./types";

export const TABLE_ROW_CAP = 1000;

export interface TableView {
  headers: string[];
  rows: string[][];
  /** Rows in the data beyond the cap. */
  truncated: number;
}

/** Text for one cell of the "view as table" fallback. */
export function cellText(value: unknown, kind: string | undefined): string {
  if (value === null || value === undefined) return "";
  if (kind === "temporal") return formatDateAuto(value);
  return String(value);
}

/**
 * The chart's data as a plain table, using the display labels from `meta.fields`.
 * Capped so a 50k-mark chart does not put 50k rows in the DOM.
 */
export function buildTable(data: ChartData, cap = TABLE_ROW_CAP): TableView {
  const names = Object.keys(data.columns);
  const count = Math.min(data.rowCount, cap);
  const rows: string[][] = [];
  for (let i = 0; i < count; i++) {
    rows.push(names.map((name) => cellText(data.columns[name]?.[i], data.kinds[name])));
  }
  return {
    headers: names.map((name) => data.meta.fields[name] ?? name),
    rows,
    truncated: Math.max(0, data.rowCount - count),
  };
}
