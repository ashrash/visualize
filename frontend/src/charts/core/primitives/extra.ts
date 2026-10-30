import type { ChartDataMeta } from "../../../api/types";

/**
 * Typed reads from `meta.params` (binWidth, hexRadius, nOut, truncatedGroups, ...). Read
 * defensively: the field may be absent in older responses, and values may be malformed.
 */
export function chartParams(meta: ChartDataMeta) {
  const params = (meta as { params?: Record<string, unknown> }).params ?? {};
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

/** The full x extent of the data ignoring any zoom (`meta.xDomain`), if the server sent it. */
export function chartXDomain(meta: ChartDataMeta): [number, number] | undefined {
  const domain = (meta as { xDomain?: unknown }).xDomain;
  if (
    Array.isArray(domain) &&
    domain.length === 2 &&
    typeof domain[0] === "number" &&
    typeof domain[1] === "number"
  ) {
    return [domain[0], domain[1]];
  }
  return undefined;
}
