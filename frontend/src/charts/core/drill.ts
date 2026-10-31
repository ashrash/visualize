import type { ChartSpec, Predicate } from "../../api/types";
import { categoryLabel } from "./series";
import type { ChartData, PlotEntry } from "./types";

/**
 * Drill-down: `interactions.drillHierarchy` lists columns from coarse to fine
 * (["country", "state", "city"]). At depth d a chart shows its category channel bound to
 * `levels[d]`, filtered to the path taken so far. Drilling stays inside one chart; opening a
 * different dashboard or grid ("drill-through") is the dashboard's job.
 */
export type DrillValue = string | number | boolean | null;
export type DrillPath = DrillValue[];

/** A drill-down level, as a short label for the breadcrumb. */
export interface DrillCrumb {
  label: string;
  /** Depth to return to when this crumb is chosen (0 = the top level). */
  depth: number;
}

/**
 * The usable hierarchy: empty unless the plot declares `drill`, and unless there are at least
 * two distinct levels (one level has nothing to drill into).
 */
export function drillLevels(spec: ChartSpec, entry: Pick<PlotEntry, "drill">): string[] {
  if (!entry.drill) return [];
  const seen = new Set<string>();
  for (const level of spec.interactions?.drillHierarchy ?? []) {
    if (level && !seen.has(level)) seen.add(level);
  }
  return seen.size >= 2 ? [...seen] : [];
}

/** Whether a click on a mark can go one level deeper. */
export function canDrillDown(levels: string[], path: DrillPath): boolean {
  return levels.length > 0 && path.length < levels.length - 1;
}

export const drillInto = (path: DrillPath, value: DrillValue): DrillPath => [...path, value];

/** The path truncated to `depth` steps (0 = back to the top). */
export const drillTo = (path: DrillPath, depth: number): DrillPath =>
  path.slice(0, Math.max(0, depth));

/** "All", then one crumb per step taken. */
export function drillCrumbs(path: DrillPath): DrillCrumb[] {
  return [
    { label: "All", depth: 0 },
    ...path.map((value, i) => ({
      label: value === null || value === "" ? "(blank)" : String(value),
      depth: i + 1,
    })),
  ];
}

const predicateFor = (field: string, value: DrillValue): Predicate =>
  value === null ? { op: "is_null", field, isNull: true } : { op: "eq", field, value };

/**
 * The spec to query for a drill path, plus the Predicates that narrow it (one per step, so the
 * server filters and numbers stay exact). The same `Predicate` type the rest of the app uses.
 * Without a usable hierarchy the spec comes back unchanged.
 */
export function applyDrill(
  spec: ChartSpec,
  entry: Pick<PlotEntry, "drill">,
  path: DrillPath,
): { spec: ChartSpec; predicates: Predicate[]; depth: number } {
  const levels = drillLevels(spec, entry);
  const channel = entry.drill?.channel;
  if (levels.length === 0 || !channel) return { spec, predicates: [], depth: 0 };
  const depth = Math.min(path.length, levels.length - 1);
  const current = spec.encodings?.[channel]?.[0];
  const encoding = { ...current, field: levels[depth] as string };
  // A time unit or aggregate belongs to the field it was set for, not to the next level.
  if (current?.field !== encoding.field) encoding.timeUnit = undefined;
  return {
    spec: { ...spec, encodings: { ...spec.encodings, [channel]: [encoding] } },
    predicates: path.slice(0, depth).map((value, i) => predicateFor(levels[i] as string, value)),
    depth,
  };
}

/**
 * The raw column value behind a clicked label. Renderers draw category names as strings
 * (formatted like `categoryLabel`), but the Predicate needs the original type. `undefined` when
 * the label matches no row (an aggregated "Other" slice, for instance).
 */
export function resolveDrillValue(
  data: ChartData,
  label: string,
  o: { column?: string; format?: string | null; locale?: string } = {},
): { value: DrillValue } | undefined {
  const column = data.columns[o.column ?? "x"];
  if (!column) return undefined;
  const kind = data.kinds[o.column ?? "x"];
  for (const raw of column) {
    if (categoryLabel(raw, kind, o.format, o.locale) !== label && String(raw) !== label) continue;
    if (raw === null || raw === undefined) return { value: null };
    if (typeof raw === "string" || typeof raw === "number" || typeof raw === "boolean") {
      return { value: raw };
    }
    if (typeof raw === "bigint" && Math.abs(Number(raw)) <= Number.MAX_SAFE_INTEGER) {
      return { value: Number(raw) };
    }
    return { value: String(raw) };
  }
  return undefined;
}
