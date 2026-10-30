import { type ColumnarResult, fetchArrow } from "./arrow";
import { enc } from "./http";
import { postArrow } from "./httpArrow";
import type { ImportPreviewMeta, ImportPreviewRequest, RowsPageMeta, RowsRequest } from "./types";

// Arrow-returning dataset endpoints. Separate from datasets.ts so the Arrow reader loads only
// with the data and chart routes, not with the app shell.

/** Legacy GET rows: offset and limit only. `query` comes from `buildRowsQuery`. */
export function fetchRowsPage(
  datasetId: string,
  query: URLSearchParams,
  signal?: AbortSignal,
): Promise<ColumnarResult<RowsPageMeta>> {
  return fetchArrow<RowsPageMeta>(`/api/datasets/${enc(datasetId)}/rows?${query}`, { signal });
}

/** POST /api/datasets/{id}/rows -> Arrow, meta = RowsPageMeta (totalRows is after filters). */
export const postRows = (id: string, body: RowsRequest, signal?: AbortSignal) =>
  postArrow<RowsRequest, RowsPageMeta>(`/api/datasets/${enc(id)}/rows`, body, signal);

/** POST /api/datasets/preview -> Arrow, meta = ImportPreviewMeta */
export const previewImport = (body: ImportPreviewRequest, signal?: AbortSignal) =>
  postArrow<ImportPreviewRequest, ImportPreviewMeta>("/api/datasets/preview", body, signal);
