import { api, unwrap } from "./client";
import type { UpdateInfo } from "./types";

// Self-update endpoints (local mode only; see backend/src/viz/api/update.py).

export async function getUpdateStatus(signal?: AbortSignal): Promise<UpdateInfo> {
  return unwrap(await api.GET("/api/update/status", { signal }));
}

export async function checkForUpdate(signal?: AbortSignal): Promise<UpdateInfo> {
  return unwrap(await api.POST("/api/update/check", { signal }));
}

export async function applyUpdate(signal?: AbortSignal): Promise<UpdateInfo> {
  return unwrap(await api.POST("/api/update/apply", { signal }));
}
