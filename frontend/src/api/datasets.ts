import { api, unwrap } from "./client";
import type {
  ColumnValues,
  ColumnValuesRequest,
  Dataset,
  ExportRequest,
  ImportRequest,
  Job,
  RefreshRequest,
  StagedFile,
} from "./types";

// JSON data-core endpoints, on the typed client. Arrow-returning ones (rows, preview) are in
// datasetRows.ts so the Arrow reader stays out of the entry chunk.

export async function listDatasets(signal?: AbortSignal): Promise<Dataset[]> {
  return unwrap(await api.GET("/api/datasets", { signal }));
}

export async function getDataset(id: string, signal?: AbortSignal): Promise<Dataset> {
  return unwrap(
    await api.GET("/api/datasets/{dataset_id}", { params: { path: { dataset_id: id } }, signal }),
  );
}

/** POST /api/datasets/staged (multipart `file`) -> StagedFile */
export async function stageFile(file: File, signal?: AbortSignal): Promise<StagedFile> {
  const form = new FormData();
  form.append("file", file);
  return unwrap(
    await api.POST("/api/datasets/staged", {
      // The client would JSON-encode a multipart body; hand it the prepared form instead.
      body: { file: "" },
      bodySerializer: () => form,
      signal,
    }),
  );
}

/** DELETE /api/datasets/staged/{stagedId} */
export async function deleteStaged(stagedId: string, signal?: AbortSignal): Promise<void> {
  const result = await api.DELETE("/api/datasets/staged/{staged_id}", {
    params: { path: { staged_id: stagedId } },
    signal,
  });
  if (result.error !== undefined) unwrap(result);
}

/** POST /api/datasets/import -> 202 Job. A staged file is consumed by the import. */
export async function startImport(body: ImportRequest, signal?: AbortSignal): Promise<Job> {
  return unwrap(await api.POST("/api/datasets/import", { body, signal }));
}

/** POST /api/datasets/{id}/refresh -> 202 Job */
export async function startRefresh(
  id: string,
  body: RefreshRequest,
  signal?: AbortSignal,
): Promise<Job> {
  return unwrap(
    await api.POST("/api/datasets/{dataset_id}/refresh", {
      params: { path: { dataset_id: id } },
      body,
      signal,
    }),
  );
}

/** PATCH /api/datasets/{id} {name} -> Dataset */
export async function renameDataset(
  id: string,
  name: string,
  signal?: AbortSignal,
): Promise<Dataset> {
  return unwrap(
    await api.PATCH("/api/datasets/{dataset_id}", {
      params: { path: { dataset_id: id } },
      body: { name },
      signal,
    }),
  );
}

/** DELETE /api/datasets/{id}?force= -> 204; 409 when a project uses it and force is false. */
export async function deleteDataset(
  id: string,
  force: boolean,
  signal?: AbortSignal,
): Promise<void> {
  const result = await api.DELETE("/api/datasets/{dataset_id}", {
    params: { path: { dataset_id: id }, query: { force } },
    signal,
  });
  if (result.error !== undefined) unwrap(result);
}

/** POST /api/datasets/{id}/values -> distinct values with counts (null counts as a value). */
export async function fetchColumnValues(
  id: string,
  body: ColumnValuesRequest,
  signal?: AbortSignal,
): Promise<ColumnValues> {
  return unwrap(
    await api.POST("/api/datasets/{dataset_id}/values", {
      params: { path: { dataset_id: id } },
      body,
      signal,
    }),
  );
}

/** POST /api/exports -> 202 Job; on success `result.export.downloadUrl` is the file. */
export async function startExport(body: ExportRequest, signal?: AbortSignal): Promise<Job> {
  return unwrap(await api.POST("/api/exports", { body, signal }));
}

// ---- jobs ----

/** GET /api/jobs?active=true */
export async function listActiveJobs(signal?: AbortSignal): Promise<Job[]> {
  return unwrap(await api.GET("/api/jobs", { params: { query: { active: true } }, signal }));
}

/** POST /api/jobs/{id}/cancel */
export async function cancelJob(id: string, signal?: AbortSignal): Promise<Job> {
  return unwrap(
    await api.POST("/api/jobs/{job_id}/cancel", { params: { path: { job_id: id } }, signal }),
  );
}
