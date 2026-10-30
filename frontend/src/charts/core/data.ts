import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { fetchArrow } from "../../api/arrow";
import type { ChartDataMeta, ChartDataRequest, ChartSpec, Predicate } from "../../api/types";
import type { ChartData } from "./types";

export function fetchChartData(
  request: ChartDataRequest,
  signal?: AbortSignal,
): Promise<ChartData> {
  return fetchArrow<ChartDataMeta>("/api/charts/data", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
}

/** Chart data for a spec. A changed spec or filter cancels the in-flight request. */
export function useChartData(spec: ChartSpec, filters: Predicate[] = [], enabled = true) {
  return useQuery({
    queryKey: ["chart-data", spec, filters],
    queryFn: ({ signal }) => fetchChartData({ spec, filters }, signal),
    placeholderData: keepPreviousData,
    enabled,
  });
}
