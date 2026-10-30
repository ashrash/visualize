import { type ColumnarResult, fetchArrow } from "./arrow";
import { ApiError, api, detailOf, unwrap } from "./client";
import { deleteRequest, enc, getJson, patchJson, postArrow, postForm, postJson } from "./http";
import type {
  ColumnValues,
  ColumnValuesRequest,
  Dataset,
  DatasetUpdate,
  ExportRequest,
  ImportFromPathRequest,
  ImportPreviewMeta,
  ImportPreviewRequest,
  ImportRequest,
  Job,
  RefreshRequest,
  RowsPageMeta,
  RowsRequest,
  StagedFile,
} from "./types";

export async function listDatasets(signal?: AbortSignal): Promise<Dataset[]> {
  return unwrap(await api.GET("/api/datasets", { signal }));
}

export async function getDataset(id: string, signal?: AbortSignal): Promise<Dataset> {
  return unwrap(
    await api.GET("/api/datasets/{dataset_id}", { params: { path: { dataset_id: id } }, signal }),
  );
}

// ---- Legacy synchronous endpoints (kept by the backend for compatibility) ----

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

/** Legacy GET rows: offset and limit only. `query` comes from `buildRowsQuery`. */
export function fetchRowsPage(
  datasetId: string,
  query: URLSearchParams,
  signal?: AbortSignal,
): Promise<ColumnarResult<RowsPageMeta>> {
  return fetchArrow<RowsPageMeta>(`/api/datasets/${enc(datasetId)}/rows?${query}`, { signal });
}

// ---- data-core: staged import, preview, jobs (flags `stagedImport`, `serverRows`, ...) ----

/** POST /api/datasets/staged (multipart `file`) -> StagedFile */
export function stageFile(file: File, signal?: AbortSignal): Promise<StagedFile> {
  const form = new FormData();
  form.append("file", file);
  return postForm<StagedFile>("/api/datasets/staged", form, signal);
}

/** DELETE /api/datasets/staged/{stagedId} */
export const deleteStaged = (stagedId: string, signal?: AbortSignal) =>
  deleteRequest(`/api/datasets/staged/${enc(stagedId)}`, signal);

/** POST /api/datasets/preview -> Arrow, meta = ImportPreviewMeta */
export const previewImport = (body: ImportPreviewRequest, signal?: AbortSignal) =>
  postArrow<ImportPreviewRequest, ImportPreviewMeta>("/api/datasets/preview", body, signal);

/** POST /api/datasets/import -> 202 Job */
export const startImport = (body: ImportRequest, signal?: AbortSignal) =>
  postJson<ImportRequest, Job>("/api/datasets/import", body, signal);

/** POST /api/datasets/{id}/refresh -> 202 Job */
export const startRefresh = (id: string, body: RefreshRequest, signal?: AbortSignal) =>
  postJson<RefreshRequest, Job>(`/api/datasets/${enc(id)}/refresh`, body, signal);

/** PATCH /api/datasets/{id} {name} -> Dataset */
export const renameDataset = (id: string, name: string, signal?: AbortSignal) =>
  patchJson<DatasetUpdate, Dataset>(`/api/datasets/${enc(id)}`, { name }, signal);

/** DELETE /api/datasets/{id}?force= -> 204; 409 when a project uses it and force is false. */
export const deleteDataset = (id: string, force: boolean, signal?: AbortSignal) =>
  deleteRequest(`/api/datasets/${enc(id)}?force=${force}`, signal);

/** POST /api/datasets/{id}/rows -> Arrow, meta = RowsPageMeta (totalRows is after filters). */
export const postRows = (id: string, body: RowsRequest, signal?: AbortSignal) =>
  postArrow<RowsRequest, RowsPageMeta>(`/api/datasets/${enc(id)}/rows`, body, signal);

/** POST /api/datasets/{id}/values -> distinct values with counts, for filter checklists. */
export const fetchColumnValues = (id: string, body: ColumnValuesRequest, signal?: AbortSignal) =>
  postJson<ColumnValuesRequest, ColumnValues>(`/api/datasets/${enc(id)}/values`, body, signal);

/** POST /api/exports -> 202 Job; on success `result.export.downloadUrl` is the file. */
export const startExport = (body: ExportRequest, signal?: AbortSignal) =>
  postJson<ExportRequest, Job>("/api/exports", body, signal);

// ---- jobs ----

/** GET /api/jobs?active=true */
export const listActiveJobs = (signal?: AbortSignal) =>
  getJson<Job[]>("/api/jobs?active=true", signal);

/** POST /api/jobs/{id}/cancel */
export const cancelJob = (id: string, signal?: AbortSignal) =>
  postJson<Record<string, never>, Job>(`/api/jobs/${enc(id)}/cancel`, {}, signal);
