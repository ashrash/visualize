import { ApiError, api, detailOf, unwrap } from "./client";
import type { components } from "./schema";
import type { Project, ProjectSummary, ProjectVersion } from "./types";

type Schemas = components["schemas"];
export type ProjectImportResult = Schemas["ProjectImportResult"];

/**
 * Platform's rename/duplicate/delete/restore/import endpoints are specified but not merged yet,
 * so the menu items stay disabled until this flips.
 * TODO(platform): set true once their branch is merged and schema.d.ts has the paths.
 */
export const PLATFORM_PROJECT_ENDPOINTS = false;

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

// Endpoints below are not in the generated paths yet; they use plain fetch with the same errors.

async function send<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    throw new ApiError(detailOf(body) ?? `Request failed (${response.status})`, response.status);
  }
  return body as T;
}

const json = (method: string, body: unknown, signal?: AbortSignal): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
  signal,
});

const enc = encodeURIComponent;

/** TODO(platform): PATCH /api/projects/{id} {name, revision} -> Project */
export const renameProject = (id: string, name: string, revision: number, signal?: AbortSignal) =>
  send<Project>(`/api/projects/${enc(id)}`, json("PATCH", { name, revision }, signal));

/** TODO(platform): POST /api/projects/{id}/duplicate {name?} -> Project (201) */
export const duplicateProject = (id: string, name: string | null, signal?: AbortSignal) =>
  send<Project>(`/api/projects/${enc(id)}/duplicate`, json("POST", { name }, signal));

/** TODO(platform): DELETE /api/projects/{id} -> 204 */
export const deleteProject = (id: string, signal?: AbortSignal) =>
  send<void>(`/api/projects/${enc(id)}`, { method: "DELETE", signal });

/** TODO(platform): POST /api/projects/{id}/versions/{versionId}/restore {revision} -> Project */
export const restoreVersion = (
  id: string,
  versionId: number,
  revision: number,
  signal?: AbortSignal,
) =>
  send<Project>(
    `/api/projects/${enc(id)}/versions/${versionId}/restore`,
    json("POST", { revision }, signal),
  );

/** TODO(platform): GET /api/projects/{id}/export?includeData=bool; use as a plain link. */
export const exportProjectUrl = (id: string, includeData: boolean) =>
  `/api/projects/${enc(id)}/export?includeData=${includeData}`;
