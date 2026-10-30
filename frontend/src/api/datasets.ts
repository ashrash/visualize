import { type ColumnarResult, fetchArrow } from "./arrow";
import { ApiError, api, detailOf, unwrap } from "./client";
import type { Dataset, ImportFromPathRequest, RowsPageMeta } from "./types";

export async function listDatasets(signal?: AbortSignal): Promise<Dataset[]> {
  return unwrap(await api.GET("/api/datasets", { signal }));
}

export async function importFromPath(
  body: ImportFromPathRequest,
  signal?: AbortSignal,
): Promise<Dataset> {
  return unwrap(await api.POST("/api/datasets/import-path", { body, signal }));
}

/** Multipart upload. The form is built by `buildUploadForm` (data/requests.ts). */
export async function uploadDataset(form: FormData, signal?: AbortSignal): Promise<Dataset> {
  const response = await fetch("/api/datasets/upload", { method: "POST", body: form, signal });
  const body: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    throw new ApiError(detailOf(body) ?? `Upload failed (${response.status})`, response.status);
  }
  return body as Dataset;
}

/** `query` comes from `buildRowsQuery` (data/requests.ts). */
export function fetchRowsPage(
  datasetId: string,
  query: URLSearchParams,
  signal?: AbortSignal,
): Promise<ColumnarResult<RowsPageMeta>> {
  return fetchArrow<RowsPageMeta>(`/api/datasets/${encodeURIComponent(datasetId)}/rows?${query}`, {
    signal,
  });
}
