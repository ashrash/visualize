import type { Predicate } from "../api/types";
import type { BusState } from "./bus";
import type { Dashboard, DashboardFilter } from "./types";

/** A short human label for a predicate, for selection chips and filter summaries. */
export function describePredicate(p: Predicate): string {
  switch (p.op) {
    case "in":
      return `${p.field}: ${p.values.map((v) => (v === null ? "(empty)" : String(v))).join(", ")}`;
    case "not_in":
      return `${p.field} not ${p.values.map((v) => (v === null ? "(empty)" : String(v))).join(", ")}`;
    case "eq":
      return `${p.field} = ${p.value === null ? "(empty)" : String(p.value)}`;
    case "is_null":
      return p.isNull ? `${p.field} is empty` : `${p.field} is not empty`;
    case "range":
    case "between_dates": {
      const fmt = (v: number | string | null | undefined) =>
        p.op === "between_dates" && typeof v === "string" ? v.slice(0, 10) : String(v);
      const lo = p.min === null || p.min === undefined ? "any" : fmt(p.min);
      const hi = p.max === null || p.max === undefined ? "any" : fmt(p.max);
      return `${p.field}: ${lo} to ${hi}`;
    }
  }
}

/**
 * Every filter in effect on a dashboard, as labels: global and dashboard filter controls (the
 * viewer's live value, else the saved default), drill-through predicates and chart selections.
 * This is the "Filtered by" line of a report.
 */
export function activeFilterLabels(args: {
  dashboard: Dashboard;
  globalFilters: DashboardFilter[];
  filterValues: Record<string, Predicate | null>;
  globalValues: Record<string, Predicate | null>;
  drill: Predicate[];
  bus: BusState;
}): string[] {
  const labels: string[] = [];
  const controls = (filters: DashboardFilter[], live: Record<string, Predicate | null>) => {
    for (const f of filters) {
      const p = live[f.id] === undefined ? (f.predicate ?? null) : live[f.id];
      if (p) labels.push(describePredicate(p));
    }
  };
  controls(args.globalFilters, args.globalValues);
  controls(args.dashboard.filters ?? [], args.filterValues);
  labels.push(...args.drill.map(describePredicate));
  for (const group of Object.values(args.bus)) {
    for (const selection of Object.values(group)) {
      labels.push(...selection.predicates.map(describePredicate));
    }
  }
  return [...new Set(labels)];
}
