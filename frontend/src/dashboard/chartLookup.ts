import type { QueryClient } from "@tanstack/react-query";
import type { ChartSpec } from "../api/types";
import { dataIdentity } from "../charts/core/data";
import type { ChartData } from "../charts/core/types";
import type { ChartLookup } from "./predicates";

/** The raw x and colour values a chart was drawn from, for mapping clicks back to the data. */
export function lookupFromData(
  spec: ChartSpec,
  data: ChartData | undefined,
): ChartLookup | undefined {
  if (!data) return undefined;
  return {
    xs: data.columns.x ?? [],
    xKind: data.kinds.x,
    xFormat: spec.style?.xAxis?.format ?? null,
    colors: data.columns.color ?? [],
    colorKind: data.kinds.color,
  };
}

/**
 * The most recent chart data TanStack Query holds for this chart (whatever filters and zoom it
 * was fetched with): the picture the user is looking at and clicking. No request is made.
 */
export function cachedChartData(client: QueryClient, spec: ChartSpec): ChartData | undefined {
  const entries = client
    .getQueryCache()
    .findAll({ queryKey: ["chart-data", dataIdentity(spec)] })
    .filter((q) => q.state.data !== undefined);
  entries.sort((a, b) => b.state.dataUpdatedAt - a.state.dataUpdatedAt);
  return entries[0]?.state.data as ChartData | undefined;
}

export function chartLookup(client: QueryClient, spec: ChartSpec): ChartLookup | undefined {
  return lookupFromData(spec, cachedChartData(client, spec));
}
