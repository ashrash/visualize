import { formatDateAuto } from "./format";
import type { ChartData, JsonValue, StatResult } from "./types";

export interface StatTable {
  caption: string;
  headers: string[];
  rows: string[][];
  /** Rows beyond the cap. */
  truncated: number;
}

type Json = JsonValue | undefined;
type Dict = { [key: string]: JsonValue };

const isScalar = (v: Json): v is string | number | boolean | null =>
  v === null || ["string", "number", "boolean"].includes(typeof v);
const isDict = (v: Json): v is Dict => typeof v === "object" && v !== null && !Array.isArray(v);
const isScalarArray = (v: Json): v is (string | number | boolean | null)[] =>
  Array.isArray(v) && v.every(isScalar);

/** One cell of a stat table: short numbers, no JSON noise. */
function statCell(value: Json): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") {
    return Number.isInteger(value) || !Number.isFinite(value)
      ? String(value)
      : String(Number(value.toPrecision(6)));
  }
  if (isScalar(value)) return String(value);
  if (isScalarArray(value)) {
    const shown = value.slice(0, 8).map(statCell).join(", ");
    return value.length > 8 ? `${shown}, … (${value.length} values)` : shown;
  }
  return JSON.stringify(value);
}

/** Flatten nested objects into dotted keys (`ci.lo`); arrays and scalars stay as cell values. */
function flatten(value: Dict, prefix = "", depth = 0): Record<string, Json> {
  const out: Record<string, Json> = {};
  for (const [key, child] of Object.entries(value)) {
    const name = prefix ? `${prefix}.${key}` : key;
    if (isDict(child) && depth < 3) Object.assign(out, flatten(child, name, depth + 1));
    else out[name] = child;
  }
  return out;
}

function capRows(rows: string[][], cap: number) {
  return { rows: rows.slice(0, cap), truncated: Math.max(0, rows.length - cap) };
}

function tablesFor(caption: string, node: Json, cap: number, depth: number): StatTable[] {
  const make = (headers: string[], rows: string[][]): StatTable => ({
    caption,
    headers,
    ...capRows(rows, cap),
  });
  if (node === null || node === undefined || isScalar(node)) {
    return [make(["Name", "Value"], [[caption, statCell(node)]])];
  }
  if (Array.isArray(node)) {
    if (node.length === 0) return [];
    if (node.every(isScalar)) {
      return [
        make(
          ["#", caption],
          node.map((v, i) => [String(i + 1), statCell(v)]),
        ),
      ];
    }
    if (node.every(isScalarArray)) {
      const width = Math.max(...node.map((r) => (r as unknown[]).length));
      const headers = ["#", ...Array.from({ length: width }, (_, i) => String(i + 1))];
      return [
        make(
          headers,
          node.map((row, i) => [
            String(i + 1),
            ...Array.from({ length: width }, (_, j) => statCell((row as Json[])[j])),
          ]),
        ),
      ];
    }
    if (node.every(isDict)) {
      const flat = node.map((row) => flatten(row as Dict));
      const keys = [...new Set(flat.flatMap((row) => Object.keys(row)))];
      return [
        make(
          ["#", ...keys],
          flat.map((row, i) => [String(i + 1), ...keys.map((k) => statCell(row[k]))]),
        ),
      ];
    }
    return [
      make(
        ["#", caption],
        node.map((v, i) => [String(i + 1), statCell(v)]),
      ),
    ];
  }
  // A dict: scalars become key/value rows, equal-length scalar arrays one columnar table,
  // anything else its own table captioned `parent.key`.
  const tables: StatTable[] = [];
  const entries = Object.entries(node);
  const scalars = entries.filter(([, v]) => isScalar(v));
  if (scalars.length > 0) {
    tables.push(
      make(
        ["Name", "Value"],
        scalars.map(([k, v]) => [k, statCell(v)]),
      ),
    );
  }
  const columns = entries.filter(([, v]) => isScalarArray(v) && v.length > 0) as [
    string,
    JsonValue[],
  ][];
  const length = columns[0]?.[1].length;
  const aligned = columns.length >= 2 && columns.every(([, v]) => v.length === length);
  if (aligned && length !== undefined) {
    const rows = Array.from({ length }, (_, i) => [
      String(i + 1),
      ...columns.map(([, v]) => statCell(v[i])),
    ]);
    tables.push({ caption, headers: ["#", ...columns.map(([k]) => k)], ...capRows(rows, cap) });
  }
  for (const [key, value] of entries) {
    if (isScalar(value)) continue;
    if (aligned && columns.some(([k]) => k === key)) continue;
    const name = depth === 0 ? key : `${caption}.${key}`;
    tables.push(...tablesFor(name, value, cap, depth + 1));
  }
  return tables;
}

/**
 * A StatResult's `values` as real tables for the "view as table" fallback, with no per-plot
 * knowledge: scalars as name/value rows, parallel arrays as columns, arrays of objects as rows.
 */
export function buildStatTables(
  stat: StatResult,
  cap = TABLE_ROW_CAP,
): { summary: string; tables: StatTable[] } {
  const summary = `${stat.method}, n = ${stat.n}${stat.sampled ? " (sampled)" : ""}`;
  const root = tablesFor("Values", stat.values as Dict, cap, 0);
  return { summary, tables: root };
}

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
