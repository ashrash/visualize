import { DataType, type Table, tableFromIPC } from "apache-arrow";
import { ApiError, detailOf } from "./client";

/** Key of the JSON metadata (ChartDataMeta / RowsPageMeta) in the Arrow schema metadata. */
export const ARROW_META_KEY = "viz.meta";

export type ColumnKind = "number" | "string" | "boolean" | "temporal" | "other";
export type Columns = Record<string, unknown[]>;

export interface ColumnarResult<M> {
  columns: Columns;
  kinds: Record<string, ColumnKind>;
  rowCount: number;
  meta: M;
}

/** Fetch an Arrow IPC endpoint. Pass an AbortSignal so superseded requests get cancelled. */
export async function fetchArrow<M>(
  url: string,
  init: RequestInit = {},
): Promise<ColumnarResult<M>> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let detail: string | undefined;
    try {
      detail = detailOf(await response.json());
    } catch {
      // Non-JSON error body; fall back to the status.
    }
    throw new ApiError(detail ?? `Request failed (${response.status})`, response.status);
  }
  const table = tableFromIPC(new Uint8Array(await response.arrayBuffer()));
  const raw = table.schema.metadata.get(ARROW_META_KEY);
  if (raw === undefined) throw new Error(`Arrow response from ${url} has no ${ARROW_META_KEY}`);
  return { ...toColumns(table), meta: JSON.parse(raw) as M };
}

/** Plain JS value: BigInt → number (string beyond 2^53), Date → epoch milliseconds. */
export function toJsValue(value: unknown): unknown {
  if (typeof value === "bigint") {
    const safe =
      value <= BigInt(Number.MAX_SAFE_INTEGER) && value >= BigInt(Number.MIN_SAFE_INTEGER);
    return safe ? Number(value) : value.toString();
  }
  if (value instanceof Date) return value.getTime();
  return value;
}

export function kindOf(type: DataType): ColumnKind {
  // Dictionary-encoded columns (DuckDB enums, repeated strings) take their value type's kind.
  if (DataType.isDictionary(type)) return kindOf(type.dictionary);
  if (DataType.isInt(type) || DataType.isFloat(type) || DataType.isDecimal(type)) return "number";
  if (DataType.isUtf8(type) || DataType.isLargeUtf8(type)) return "string";
  if (DataType.isBool(type)) return "boolean";
  if (DataType.isDate(type) || DataType.isTimestamp(type) || DataType.isTime(type)) {
    return "temporal";
  }
  return "other";
}

export function toColumns(table: Table): Omit<ColumnarResult<never>, "meta"> {
  const columns: Columns = {};
  const kinds: Record<string, ColumnKind> = {};
  for (const field of table.schema.fields) {
    const vector = table.getChild(field.name);
    const values: unknown[] = [];
    if (vector) for (const value of vector) values.push(toJsValue(value));
    columns[field.name] = values;
    kinds[field.name] = kindOf(field.type);
  }
  return { columns, kinds, rowCount: table.numRows };
}
