import { api, unwrap } from "./client";
import { enc, postForm } from "./http";
import type { Project, ProjectImportResult, ProjectSummary, ProjectVersion } from "./types";

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

// Platform endpoints (flag `platformProjects`).

/** PATCH /api/projects/{id} {name, revision} -> Project; 409 on a stale revision. */
export async function renameProject(
  id: string,
  name: string,
  revision: number,
  signal?: AbortSignal,
): Promise<Project> {
  return unwrap(
    await api.PATCH("/api/projects/{project_id}", {
      params: { path: { project_id: id } },
      body: { name, revision },
      signal,
    }),
  );
}

/** POST /api/projects/{id}/duplicate {name?} -> Project (201) */
export async function duplicateProject(
  id: string,
  name: string | null,
  signal?: AbortSignal,
): Promise<Project> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/duplicate", {
      params: { path: { project_id: id } },
      body: { name },
      signal,
    }),
  );
}

/** DELETE /api/projects/{id} -> 204 */
export async function deleteProject(id: string, signal?: AbortSignal): Promise<void> {
  const result = await api.DELETE("/api/projects/{project_id}", {
    params: { path: { project_id: id } },
    signal,
  });
  if (result.error !== undefined) unwrap(result);
}

/** GET /api/projects/{id}/versions/{versionId} -> the snapshot Project */
export async function getVersionSnapshot(
  id: string,
  versionId: number,
  signal?: AbortSignal,
): Promise<Project> {
  return unwrap(
    await api.GET("/api/projects/{project_id}/versions/{version_id}", {
      params: { path: { project_id: id, version_id: versionId } },
      signal,
    }),
  );
}

/** POST /api/projects/{id}/versions/{versionId}/restore {revision} -> Project */
export async function restoreVersion(
  id: string,
  versionId: number,
  revision: number,
  signal?: AbortSignal,
): Promise<Project> {
  return unwrap(
    await api.POST("/api/projects/{project_id}/versions/{version_id}/restore", {
      params: { path: { project_id: id, version_id: versionId } },
      body: { revision },
      signal,
    }),
  );
}

/** GET /api/projects/{id}/export?includeData=bool; used as a plain download link. */
export const exportProjectUrl = (id: string, includeData: boolean) =>
  `/api/projects/${enc(id)}/export?includeData=${includeData}`;

/** POST /api/projects/import (multipart `file`) -> ProjectImportResult (201) */
export function importProjectFile(file: File, signal?: AbortSignal): Promise<ProjectImportResult> {
  const form = new FormData();
  form.append("file", file);
  return postForm<ProjectImportResult>("/api/projects/import", form, signal);
}
