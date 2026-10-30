import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { toColumns } from "../../api/arrow";
import { ApiError } from "../../api/client";
import { postJson } from "../../api/http";
import { postArrowOrJson } from "../../api/httpArrow";
import type { components } from "../../api/schema";
import type { ChartDataMeta, ChartDataRequest, ChartSpec, Predicate } from "../../api/types";
import { getPlot } from "./registry";
import type { ChartData, OverlayResult } from "./types";

type Pipeline = components["schemas"]["Pipeline"];
type Viewport = components["schemas"]["Viewport"];
type SourceRef = components["schemas"]["SourceRef"];
type ChartStatResponse = components["schemas"]["ChartStatResponse"];

/** A newer request for the same chart replaced this one (HTTP 409): not an error to show. */
export function isSuperseded(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

/**
 * POST /api/charts/data. Tabular plots answer with an Arrow stream; stat-transform plots answer
 * with JSON `{result, meta}` (returned as an empty table carrying `stat`). The response content
 * type decides, so no plot needs to declare which it is.
 */
export async function fetchChartData(
  request: ChartDataRequest,
  signal?: AbortSignal,
): Promise<ChartData> {
  const response = await postArrowOrJson<ChartStatResponse>("/api/charts/data", request, signal);
  if (response.kind === "json") {
    return {
      columns: {},
      kinds: {},
      rowCount: 0,
      meta: response.body.meta,
      stat: response.body.result,
    };
  }
  if (!response.meta) throw new Error("The chart data response has no metadata.");
  return { ...toColumns(response.table), meta: response.meta as ChartDataMeta };
}

/**
 * POST /api/charts/overlays: server-computed trendlines, regressions and confidence bands, one
 * `OverlayResult` per `style.trendlines` entry.
 */
export async function fetchOverlays(
  request: ChartDataRequest,
  signal?: AbortSignal,
): Promise<OverlayResult[]> {
  try {
    return await postJson<ChartDataRequest, OverlayResult[]>(
      "/api/charts/overlays",
      request,
      signal,
    );
  } catch (error) {
    // 501: the server has no overlay support for this chart (yet): draw without.
    if (error instanceof ApiError && error.status === 501) return [];
    throw error;
  }
}

/**
 * The part of a spec that changes the data. Cosmetic edits (title, colors, axes, name,
 * interactions) are not in the key, so they re-render without re-querying. Error bars are the
 * one style field that adds columns, and only params the plot marks `affectsData` count (a plot
 * without a registry entry keys on all of them, to be safe).
 */
export function dataIdentity(spec: ChartSpec) {
  const entry = getPlot(spec.plotType);
  const params: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(spec.params ?? {})) {
    if (!entry || entry.paramsSchema.some((p) => p.name === name && p.affectsData)) {
      params[name] = value;
    }
  }
  return {
    id: spec.id,
    plotType: spec.plotType,
    source: spec.source,
    encodings: spec.encodings,
    params,
    filters: spec.filters,
    errorBars: spec.style?.errorBars ?? null,
  };
}

/**
 * The pipelines a chart's source actually depends on: the source pipeline plus any pipeline it
 * joins or unions, transitively. Editing an unrelated pipeline must not re-query this chart.
 */
export function usedPipelines(source: SourceRef, pipelines: Pipeline[] | undefined): Pipeline[] {
  if (!pipelines || source.kind !== "pipeline") return [];
  const byId = new Map(pipelines.map((p) => [p.id, p]));
  const used = new Map<string, Pipeline>();
  const visit = (ref: SourceRef) => {
    if (ref.kind !== "pipeline" || used.has(ref.id)) return;
    const pipeline = byId.get(ref.id);
    if (!pipeline) return;
    used.set(ref.id, pipeline);
    visit(pipeline.source);
    for (const step of pipeline.steps ?? []) {
      if (step.op === "join") visit(step.right);
      if (step.op === "union") for (const other of step.others) visit(other);
    }
  };
  visit(source);
  return [...used.values()];
}

export interface ChartQueryOptions {
  /** Visible x range after a zoom (numeric / epoch ms), for detail-on-demand re-queries. */
  xRange?: [number, number] | null;
  /** Pixel size of the chart, read lazily when the request is sent. */
  size?: () => { widthPx: number; heightPx: number } | null;
  /** The project's pipelines; only those the chart's source uses are sent (and keyed on). */
  pipelines?: Pipeline[];
}

function buildRequest(
  spec: ChartSpec,
  filters: Predicate[],
  options: ChartQueryOptions,
  pipelines: Pipeline[],
): ChartDataRequest {
  const size = options.size?.();
  const viewport = {
    ...(size ?? {}),
    ...(options.xRange ? { xRange: options.xRange } : {}),
  } as Viewport;
  return {
    spec,
    filters,
    ...(size || options.xRange ? { viewport } : {}),
    ...(pipelines.length > 0 ? { pipelines } : {}),
  };
}

/** Chart data for a spec. A changed spec, filter or zoom cancels the in-flight request. */
export function useChartData(
  spec: ChartSpec,
  filters: Predicate[] = [],
  enabled = true,
  options: ChartQueryOptions = {},
) {
  const pipelines = usedPipelines(spec.source, options.pipelines);
  return useQuery({
    queryKey: [
      "chart-data",
      dataIdentity(spec),
      filters,
      options.xRange ?? null,
      pipelines.length > 0 ? pipelines : null,
    ],
    queryFn: ({ signal }) =>
      fetchChartData(buildRequest(spec, filters, options, pipelines), signal),
    placeholderData: keepPreviousData,
    enabled,
    // A superseded request (409) means a newer one is already running: retry a couple of times
    // rather than leaving the chart stuck. Everything else fails fast so the message shows.
    retry: (count, error) => isSuperseded(error) && count < 2,
    retryDelay: 200,
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
  const pipelines = usedPipelines(spec.source, options.pipelines);
  return useQuery({
    queryKey: [
      "chart-overlays",
      dataIdentity(spec),
      trendlines,
      filters,
      pipelines.length > 0 ? pipelines : null,
    ],
    queryFn: ({ signal }) => fetchOverlays(buildRequest(spec, filters, options, pipelines), signal),
    placeholderData: keepPreviousData,
    enabled: enabled && trendlines.length > 0,
    retry: (count, error) => isSuperseded(error) && count < 2,
    retryDelay: 200,
  });
}
