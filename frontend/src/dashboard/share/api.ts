/**
 * Share, embed and report endpoints (platform, `projects/share.py`), on the typed client.
 *
 * A link's token gates the specs (what a view contains), not the data: locally the auth stub lets
 * any viewer on this machine or network load chart data; real token-scoped data access arrives
 * with hosted mode (CLAUDE.md section 11).
 */
import { api, unwrap } from "../../api/client";
import type { components } from "../../api/schema";

type Schemas = components["schemas"];

export type ShareLink = Schemas["ShareLink"];
export type ShareCreate = Schemas["ShareCreate"];
export type SharedView = Schemas["SharedView"];
export type ShareKind = ShareCreate["kind"];

/** POST /api/share: mint a link (authenticated). */
export async function createShare(body: ShareCreate, signal?: AbortSignal): Promise<ShareLink> {
  return unwrap(await api.POST("/api/share", { body, signal }));
}

/** GET /api/share?projectId=: every link of a project, revoked ones included. */
export async function listShares(projectId: string, signal?: AbortSignal): Promise<ShareLink[]> {
  return unwrap(await api.GET("/api/share", { params: { query: { projectId } }, signal }));
}

/** DELETE /api/share/{token}: revoke. */
export async function revokeShare(token: string, signal?: AbortSignal): Promise<void> {
  unwrap(await api.DELETE("/api/share/{token}", { params: { path: { token } }, signal }));
}

/** GET /api/share/{token} or /api/embed/{token}: public, no sign-in. 404 for any bad token. */
export async function getSharedView(
  token: string,
  via: "share" | "embed",
  signal?: AbortSignal,
): Promise<SharedView> {
  const params = { path: { token } };
  return via === "share"
    ? unwrap(await api.GET("/api/share/{token}", { params, signal }))
    : unwrap(await api.GET("/api/embed/{token}", { params, signal }));
}

/** GET /api/reports/{projectId}[/dashboards/{dashboardId}]: specs for a server-built report. */
export async function getReportView(
  projectId: string,
  dashboardId?: string,
  signal?: AbortSignal,
): Promise<SharedView> {
  return dashboardId
    ? unwrap(
        await api.GET("/api/reports/{project_id}/dashboards/{dashboard_id}", {
          params: { path: { project_id: projectId, dashboard_id: dashboardId } },
          signal,
        }),
      )
    : unwrap(
        await api.GET("/api/reports/{project_id}", {
          params: { path: { project_id: projectId } },
          signal,
        }),
      );
}

/** Full URL of a link's page, for copying. */
export const linkUrl = (path: string, origin = window.location.origin) => `${origin}${path}`;

/** The `<iframe>` snippet that embeds a link's read-only view. */
export function iframeSnippet(embedPath: string, origin = window.location.origin): string {
  return `<iframe src="${origin}${embedPath}" width="100%" height="600" style="border:0" loading="lazy" title="Embedded dashboard"></iframe>`;
}
