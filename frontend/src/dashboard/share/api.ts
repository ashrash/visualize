/**
 * Share, embed and report endpoints (platform, `projects/share.py`).
 *
 * These are typed by hand because the platform branch had not merged when this was written, so
 * `schema.d.ts` has no paths for them yet. TODO(ui-data): once `gen:api` emits them, move these
 * wrappers into `src/api` on the typed client and delete the hand-written types below.
 */
import { deleteRequest, enc, getJson, postJson } from "../../api/http";
import type { ChartSpec, Pipeline } from "../../api/types";
import type { Dashboard } from "../types";

export type ShareKind = "dashboard" | "chart";

export interface ShareLink {
  token: string;
  projectId: string;
  kind: ShareKind;
  itemId: string;
  createdAt: string;
  expiresAt: string | null;
  revoked: boolean;
  /** `/share/<token>` */
  sharePath: string;
  /** `/embed/<token>` */
  embedPath: string;
}

export interface ShareCreate {
  projectId: string;
  kind: ShareKind;
  itemId: string;
  expiresInDays?: number | null;
}

/** What a share, embed or report URL resolves to: specs only, never data. */
export interface SharedView {
  kind: ShareKind | "report";
  projectId: string;
  projectName: string;
  dashboard: Dashboard | null;
  charts: ChartSpec[];
  /** Every pipeline of the project: chart data requests must carry them. */
  pipelines: Pipeline[];
  datasetIds: string[];
  themes: unknown[];
  generatedAt: string;
}

/** POST /api/share: mint a link (authenticated). */
export const createShare = (body: ShareCreate, signal?: AbortSignal) =>
  postJson<ShareCreate, ShareLink>("/api/share", body, signal);

/** GET /api/share?projectId=: every link of a project, revoked ones included. */
export const listShares = (projectId: string, signal?: AbortSignal) =>
  getJson<ShareLink[]>(`/api/share?projectId=${enc(projectId)}`, signal);

/** DELETE /api/share/{token}: revoke. */
export const revokeShare = (token: string, signal?: AbortSignal) =>
  deleteRequest(`/api/share/${enc(token)}`, signal);

/** GET /api/share/{token} or /api/embed/{token}: public, no sign-in. 404 for any bad token. */
export const getSharedView = (token: string, via: "share" | "embed", signal?: AbortSignal) =>
  getJson<SharedView>(`/api/${via}/${enc(token)}`, signal);

/** GET /api/reports/{projectId}[/dashboards/{dashboardId}]: specs for a report. */
export const getReportView = (projectId: string, dashboardId?: string, signal?: AbortSignal) =>
  getJson<SharedView>(
    dashboardId
      ? `/api/reports/${enc(projectId)}/dashboards/${enc(dashboardId)}`
      : `/api/reports/${enc(projectId)}`,
    signal,
  );

/** Full URL of a link's page, for copying. */
export const linkUrl = (path: string, origin = window.location.origin) => `${origin}${path}`;

/** The `<iframe>` snippet that embeds a link's read-only view. */
export function iframeSnippet(embedPath: string, origin = window.location.origin): string {
  return `<iframe src="${origin}${embedPath}" width="100%" height="600" style="border:0" loading="lazy" title="Embedded dashboard"></iframe>`;
}
