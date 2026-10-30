import type { ChartDataMeta } from "../../../api/types";

/**
 * Typed reads from `meta.params` (binWidth, hexRadius, nOut, truncatedGroups, ...). Values are
 * unknown JSON, so each accessor checks the type and returns undefined on a mismatch.
 */
export function chartParams(meta: ChartDataMeta) {
  const params = meta.params ?? {};
  return {
    number(key: string): number | undefined {
      const value = params[key];
      return typeof value === "number" && Number.isFinite(value) ? value : undefined;
    },
    string(key: string): string | undefined {
      const value = params[key];
      return typeof value === "string" ? value : undefined;
    },
    raw: params,
  };
}

const toMs = (v: number | string): number | undefined => {
  if (typeof v === "number") return Number.isFinite(v) ? v : undefined;
  const ms = Date.parse(v);
  return Number.isNaN(ms) ? undefined : ms;
};

/**
 * The full x extent of the data ignoring any zoom (`meta.xDomain`), as numbers (temporal
 * bounds become epoch ms), or undefined when the server sent none.
 */
export function chartXDomain(meta: ChartDataMeta): [number, number] | undefined {
  if (!meta.xDomain) return undefined;
  const lo = toMs(meta.xDomain[0]);
  const hi = toMs(meta.xDomain[1]);
  return lo !== undefined && hi !== undefined ? [lo, hi] : undefined;
}
