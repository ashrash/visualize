import type { ChartSpec, Column, ColumnSemantic } from "../../../api/types";
import { paletteColors } from "../../core/palettes";
import type { ChartData, ChartTheme, JsonValue } from "../../core/types";

/**
 * Typed reads from a StatResult's `values` (JSON from the server). Every reader checks the type
 * and falls back, so a renderer never throws on a partial or older result.
 */
export type Values = Record<string, JsonValue>;

export function statValues(data: ChartData): Values | null {
  const values = data.stat?.values;
  return values && typeof values === "object" ? (values as Values) : null;
}

export function num(value: JsonValue | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function str(value: JsonValue | undefined): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return null;
}

export function list(value: JsonValue | undefined): JsonValue[] {
  return Array.isArray(value) ? value : [];
}

export function nums(value: JsonValue | undefined): (number | null)[] {
  return list(value).map((v) => num(v));
}

export function strs(value: JsonValue | undefined): (string | null)[] {
  return list(value).map((v) => str(v));
}

export function obj(value: JsonValue | undefined): Values {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Values) : {};
}

export function objs(value: JsonValue | undefined): Values[] {
  return list(value).map((v) => obj(v));
}

/** Series colors for the chart's palette, enough for `count` series. */
export function colorsFor(spec: ChartSpec, theme: ChartTheme, count: number): string[] {
  const colors = paletteColors(spec.style?.palette, theme, Math.max(count, 8));
  return Array.from({ length: Math.max(count, 1) }, (_, i) => colors[i % colors.length] as string);
}

/** Fixed-precision number for labels and tooltips (no trailing noise like 0.30000000004). */
export function fmt(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  const abs = Math.abs(value);
  if (abs !== 0 && (abs >= 1e6 || abs < 1e-3)) return value.toExponential(2);
  return Number(value.toFixed(digits)).toString();
}

export function pValue(p: number | null | undefined): string {
  if (p === null || p === undefined || !Number.isFinite(p)) return "–";
  return p < 0.001 ? "< 0.001" : p.toFixed(3);
}

/** Recommendation helpers over the dataset's columns. */
export function count(columns: Column[], semantic: ColumnSemantic): number {
  return columns.filter((c) => c.semantic === semantic).length;
}

export function hasName(columns: Column[], pattern: RegExp): boolean {
  return columns.some((c) => pattern.test(c.name));
}

export function binaryColumn(columns: Column[]): boolean {
  return columns.some((c) => c.approxDistinct === 2);
}

/** Every channel semantic, for channels the server accepts any field on. */
export const ANY: ColumnSemantic[] = ["quantitative", "nominal", "ordinal", "temporal"];
export const CATEGORICAL: ColumnSemantic[] = ["nominal", "ordinal"];
export const QUANT: ColumnSemantic[] = ["quantitative"];

/** An empty chart that still carries title/theme through applyStyle. */
export const EMPTY = { series: [] as Record<string, unknown>[] };
