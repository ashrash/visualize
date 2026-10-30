import { api, unwrap } from "./client";
import { deleteRequest, enc, getJson, patchJson, postForm, postJson } from "./http";
import type {
  Project,
  ProjectDuplicate,
  ProjectImportResult,
  ProjectRename,
  ProjectRestore,
  ProjectSummary,
  ProjectVersion,
} from "./types";

export async function listProjects(signal?: AbortSignal): Promise<ProjectSummary[]> {
  return unwrap(await api.GET("/api/projects", { signal }));
}

export async function getProject(id: string, signal?: AbortSignal): Promise<Project> {
  return unwrap(
    await api.GET("/api/projects/{project_id}", { params: { path: { project_id: id } }, signal }),
  );
}

export async function createProject(name: string, signal?: AbortSignal): Promise<Project> {
  return unwrap(await api.POST("/api/projects", { body: { name }, signal }));
}

/** PUT the project. `autosave` snapshots are the cheap, frequent kind. */
export async function saveProject(
  project: Project,
  options: { autosave: boolean; signal?: AbortSignal },
): Promise<Project> {
  return unwrap(
    await api.PUT("/api/projects/{project_id}", {
      params: { path: { project_id: project.id }, query: { autosave: options.autosave } },
      body: project,
      signal: options.signal,
    }),
  );
}

export async function listVersions(id: string, signal?: AbortSignal): Promise<ProjectVersion[]> {
  return unwrap(
    await api.GET("/api/projects/{project_id}/versions", {
      params: { path: { project_id: id } },
      signal,
    }),
  );
}

// Platform endpoints (flag `platformProjects`). Paths are not generated yet; see api/http.ts.

/** PATCH /api/projects/{id} {name, revision} -> Project */
export const renameProject = (id: string, name: string, revision: number, signal?: AbortSignal) =>
  patchJson<ProjectRename, Project>(`/api/projects/${enc(id)}`, { name, revision }, signal);

/** POST /api/projects/{id}/duplicate {name?} -> Project (201) */
export const duplicateProject = (id: string, name: string | null, signal?: AbortSignal) =>
  postJson<ProjectDuplicate, Project>(`/api/projects/${enc(id)}/duplicate`, { name }, signal);

/** DELETE /api/projects/{id} -> 204 */
export const deleteProject = (id: string, signal?: AbortSignal) =>
  deleteRequest(`/api/projects/${enc(id)}`, signal);

/** GET /api/projects/{id}/versions/{versionId} -> the snapshot Project */
export const getVersionSnapshot = (id: string, versionId: number, signal?: AbortSignal) =>
  getJson<Project>(`/api/projects/${enc(id)}/versions/${versionId}`, signal);

/** POST /api/projects/{id}/versions/{versionId}/restore {revision} -> Project */
export const restoreVersion = (
  id: string,
  versionId: number,
  revision: number,
  signal?: AbortSignal,
) =>
  postJson<ProjectRestore, Project>(
    `/api/projects/${enc(id)}/versions/${versionId}/restore`,
    { revision },
    signal,
  );

/** GET /api/projects/{id}/export?includeData=bool; used as a plain download link. */
export const exportProjectUrl = (id: string, includeData: boolean) =>
  `/api/projects/${enc(id)}/export?includeData=${includeData}`;

/** POST /api/projects/import (multipart `file`) -> ProjectImportResult (201) */
export function importProjectFile(file: File, signal?: AbortSignal): Promise<ProjectImportResult> {
  const form = new FormData();
  form.append("file", file);
  return postForm<ProjectImportResult>("/api/projects/import", form, signal);
}
