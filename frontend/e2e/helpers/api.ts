import { readFileSync } from "node:fs";
import path from "node:path";
import type { APIRequestContext } from "@playwright/test";
import type {
  Dataset,
  ImportOptions,
  Job,
  Project,
  ProjectSummary,
  ProjectVersion,
} from "../../src/api/types";

const MIME: Record<string, string> = {
  ".csv": "text/csv",
  ".tsv": "text/tab-separated-values",
  ".json": "application/json",
  ".ndjson": "application/x-ndjson",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
};

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

export class ApiFailure extends Error {
  constructor(
    readonly status: number,
    readonly body: string,
    what: string,
  ) {
    super(`${what} -> HTTP ${status}: ${body.slice(0, 500)}`);
  }
}

/**
 * Thin typed wrapper over the backend REST API, for test setup and for checking what the UI
 * saved. UI steps never go through this class except where a test says "API setup".
 */
export class Api {
  constructor(private readonly request: APIRequestContext) {}

  private async json<T>(
    method: "get" | "post" | "put" | "patch" | "delete",
    url: string,
    options: { data?: unknown; multipart?: Record<string, unknown>; timeout?: number } = {},
    expected: number[] = [200, 201, 202],
  ): Promise<T> {
    const response = await this.request[method](url, options as Record<string, never>);
    const text = await response.text();
    if (!expected.includes(response.status())) {
      throw new ApiFailure(response.status(), text, `${method.toUpperCase()} ${url}`);
    }
    return (text ? JSON.parse(text) : undefined) as T;
  }

  // -- jobs ---------------------------------------------------------------------------------

  async waitForJob(jobId: string, timeoutMs = 600_000): Promise<Job> {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const job = await this.json<Job>("get", `/api/jobs/${jobId}`);
      if (TERMINAL.has(job.status)) return job;
      if (Date.now() > deadline) throw new Error(`job ${jobId} still ${job.status} after timeout`);
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
  }

  // -- datasets -----------------------------------------------------------------------------

  /** Import a file by server-side path and wait for the job. Returns the new dataset. */
  async importPath(
    filePath: string,
    options: { name?: string; options?: ImportOptions; kind?: string; timeoutMs?: number } = {},
  ): Promise<Dataset> {
    const job = await this.json<Job>("post", "/api/datasets/import", {
      data: { path: filePath, name: options.name, kind: options.kind, options: options.options },
    });
    const done = await this.waitForJob(job.id, options.timeoutMs);
    if (done.status !== "succeeded" || !done.result?.datasetId) {
      throw new Error(`import of ${filePath} ${done.status}: ${done.error ?? "no dataset"}`);
    }
    return this.dataset(done.result.datasetId);
  }

  /** Upload bytes (as the import dialog does) and import. Returns the new dataset. */
  async uploadFile(
    filePath: string,
    options: { name?: string; options?: ImportOptions; kind?: string } = {},
  ): Promise<Dataset> {
    const fields: Record<string, unknown> = {
      file: {
        name: path.basename(filePath),
        mimeType: MIME[path.extname(filePath).toLowerCase()] ?? "application/octet-stream",
        buffer: readFileSync(filePath),
      },
    };
    if (options.name) fields.name = options.name;
    if (options.kind) fields.kind = options.kind;
    if (options.options) fields.options = JSON.stringify(options.options);
    return this.json<Dataset>("post", "/api/datasets/upload", {
      multipart: fields,
      timeout: 120_000,
    });
  }

  dataset(id: string): Promise<Dataset> {
    return this.json<Dataset>("get", `/api/datasets/${id}`);
  }

  datasets(): Promise<Dataset[]> {
    return this.json<Dataset[]>("get", "/api/datasets");
  }

  async deleteDataset(id: string): Promise<void> {
    await this.json<void>("delete", `/api/datasets/${id}?force=true`, {}, [200, 204]);
  }

  /** Best-effort cleanup by name (the catalog is shared between parallel tests). */
  async deleteDatasetByName(name: string): Promise<void> {
    for (const dataset of await this.datasets()) {
      if (dataset.name === name) await this.deleteDataset(dataset.id).catch(() => undefined);
    }
  }

  // -- projects -----------------------------------------------------------------------------

  createProject(name: string): Promise<Project> {
    return this.json<Project>("post", "/api/projects", { data: { name } });
  }

  project(id: string): Promise<Project> {
    return this.json<Project>("get", `/api/projects/${id}`);
  }

  projects(): Promise<ProjectSummary[]> {
    return this.json<ProjectSummary[]>("get", "/api/projects");
  }

  versions(projectId: string): Promise<ProjectVersion[]> {
    return this.json<ProjectVersion[]>("get", `/api/projects/${projectId}/versions`);
  }

  async deleteProject(id: string): Promise<void> {
    await this.json<void>("delete", `/api/projects/${id}`, {}, [200, 204]);
  }

  // -- misc ---------------------------------------------------------------------------------

  /** Paths present in the server's OpenAPI document (to detect endpoints that don't exist yet). */
  async openapiPaths(): Promise<string[]> {
    const doc = await this.json<{ paths: Record<string, unknown> }>("get", "/openapi.json");
    return Object.keys(doc.paths);
  }
}
