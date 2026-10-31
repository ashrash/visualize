import type { QueryClient } from "@tanstack/react-query";
import type { ChartSpec } from "../api/types";
import type { DrillValue } from "../charts";
import { dataIdentity } from "../charts/core/data";
import { legendValues } from "../charts/core/events";
import type { ChartData } from "../charts/core/types";

/**
 * The most recent chart data TanStack Query holds for this chart (whatever filters and zoom it
 * was fetched with): the picture the user is looking at. No request is made.
 */
export function cachedChartData(client: QueryClient, spec: ChartSpec): ChartData | undefined {
  const entries = client
    .getQueryCache()
    .findAll({ queryKey: ["chart-data", dataIdentity(spec)] })
    .filter((q) => q.state.data !== undefined);
  entries.sort((a, b) => b.state.dataUpdatedAt - a.state.dataUpdatedAt);
  return entries[0]?.state.data as ChartData | undefined;
}

/**
 * The raw colour-column value behind a clicked series name, or undefined when it cannot be told.
 * `ChartPointClick` carries the series name but not the raw colour value.
 * TODO(ui-charts): add the raw colour value to `ChartPointClick`; this lookup can then go.
 */
export function colorValueFor(
  client: QueryClient,
  spec: ChartSpec,
  seriesName: string,
): DrillValue | undefined {
  const data = cachedChartData(client, spec);
  if (!data || seriesName === "") return undefined;
  const [value] = legendValues(data, [seriesName]);
  return value;
}
