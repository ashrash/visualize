import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { tableFromIPC } from "apache-arrow";
import { ARROW_META_KEY, toColumns } from "../../api/arrow";
import { ApiError, detailOf } from "../../api/client";
import type { components } from "../../api/schema";
import type { ChartDataMeta, ChartDataRequest, ChartSpec, Predicate } from "../../api/types";
import type { ChartData, OverlayResult } from "./types";

type Pipeline = components["schemas"]["Pipeline"];
type Viewport = components["schemas"]["Viewport"];

async function failure(response: Response): Promise<ApiError> {
  let detail: string | undefined;
  try {
    detail = detailOf(await response.json());
  } catch {
    // Non-JSON error body; fall back to the status.
  }
  return new ApiError(detail ?? `Request failed (${response.status})`, response.status);
}

/** A newer request for the same chart replaced this one (HTTP 409): not an error to show. */
export function isSuperseded(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

/**
 * POST /api/charts/data. Tabular plots answer with an Arrow stream; stat-transform plots answer
 * with JSON `{result, meta}`, which comes back as an empty table carrying `stat`.
 */
export async function fetchChartData(
  request: ChartDataRequest,
  signal?: AbortSignal,
): Promise<ChartData> {
  const response = await fetch("/api/charts/data", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok) throw await failure(response);

  if (response.headers.get("content-type")?.includes("application/json")) {
    const body = (await response.json()) as { result: ChartData["stat"]; meta: ChartDataMeta };
    return { columns: {}, kinds: {}, rowCount: 0, meta: body.meta, stat: body.result };
  }
  const table = tableFromIPC(new Uint8Array(await response.arrayBuffer()));
  const raw = table.schema.metadata.get(ARROW_META_KEY);
  if (raw === undefined) throw new Error(`Chart data response has no ${ARROW_META_KEY}`);
  return { ...toColumns(table), meta: JSON.parse(raw) as ChartDataMeta };
}

/**
 * POST /api/charts/overlays: server-computed trendlines, regressions and confidence bands, one
 * `OverlayResult` per `style.trendlines` entry. Until the endpoint ships this resolves to [].
 */
export async function fetchOverlays(
  request: ChartDataRequest,
  signal?: AbortSignal,
): Promise<OverlayResult[]> {
  const response = await fetch("/api/charts/overlays", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  // TODO(transform): the endpoint is not live yet; 404/405/501 mean "no overlays available".
  if (response.status === 404 || response.status === 405 || response.status === 501) return [];
  if (!response.ok) throw await failure(response);
  return (await response.json()) as OverlayResult[];
}

/**
 * The part of a spec that changes the data. Cosmetic edits (title, colors, axes, name,
 * interactions) are not in the key, so they re-render without re-querying; error bars are the
 * one style field that adds columns.
 */
export function dataIdentity(spec: ChartSpec) {
  return {
    id: spec.id,
    plotType: spec.plotType,
    source: spec.source,
    encodings: spec.encodings,
    params: spec.params,
    filters: spec.filters,
    errorBars: spec.style?.errorBars ?? null,
  };
}

export interface ChartQueryOptions {
  /** Visible x range after a zoom (numeric / epoch ms), for detail-on-demand re-queries. */
  xRange?: [number, number] | null;
  /** Pixel size of the chart, read lazily when the request is sent. */
  size?: () => { widthPx: number; heightPx: number } | null;
  /** Pipelines referenced by a pipeline source, from the project doc (may be unsaved). */
  pipelines?: Pipeline[];
}

function buildRequest(
  spec: ChartSpec,
  filters: Predicate[],
  options: ChartQueryOptions,
): ChartDataRequest {
  const size = options.size?.();
  const viewport: Viewport = {
    ...(size ?? {}),
    ...(options.xRange ? { xRange: options.xRange } : {}),
  } as Viewport;
  return {
    spec,
    filters,
    ...(size || options.xRange ? { viewport } : {}),
    ...(options.pipelines ? { pipelines: options.pipelines } : {}),
  };
}

/** Chart data for a spec. A changed spec, filter or zoom cancels the in-flight request. */
export function useChartData(
  spec: ChartSpec,
  filters: Predicate[] = [],
  enabled = true,
  options: ChartQueryOptions = {},
) {
  return useQuery({
    queryKey: [
      "chart-data",
      dataIdentity(spec),
      filters,
      options.xRange ?? null,
      options.pipelines ?? null,
    ],
    queryFn: ({ signal }) => fetchChartData(buildRequest(spec, filters, options), signal),
    placeholderData: keepPreviousData,
    enabled,
    // Superseded and aborted requests are expected during zoom and cross-filtering.
    retry: false,
  });
}

/** Overlays for `style.trendlines`; disabled (empty) when none are configured. */
export function useChartOverlays(
  spec: ChartSpec,
  filters: Predicate[] = [],
  enabled = true,
  options: ChartQueryOptions = {},
) {
  const trendlines = spec.style?.trendlines ?? [];
  return useQuery({
    queryKey: [
      "chart-overlays",
      dataIdentity(spec),
      trendlines,
      filters,
      options.pipelines ?? null,
    ],
    queryFn: ({ signal }) => fetchOverlays(buildRequest(spec, filters, options), signal),
    placeholderData: keepPreviousData,
    enabled: enabled && trendlines.length > 0,
    retry: false,
  });
}
