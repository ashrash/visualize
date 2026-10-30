import { ApiError, detailOf } from "./client";

/**
 * Typed helpers for endpoints whose paths are not in the generated `paths` yet. Each endpoint
 * gets its own wrapper function elsewhere; once the backend merges and `gen:api` emits the path,
 * a wrapper switches to `api.POST(...)` without touching callers.
 */

async function parse<T>(response: Response): Promise<T> {
  if (response.status === 204) return undefined as T;
  const body: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    throw new ApiError(detailOf(body) ?? `Request failed (${response.status})`, response.status);
  }
  return body as T;
}

export const jsonInit = (method: string, body: unknown, signal?: AbortSignal): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
  signal,
});

export async function getJson<Res>(path: string, signal?: AbortSignal): Promise<Res> {
  return parse<Res>(await fetch(path, { signal }));
}

export async function postJson<Req, Res>(
  path: string,
  body: Req,
  signal?: AbortSignal,
): Promise<Res> {
  return parse<Res>(await fetch(path, jsonInit("POST", body, signal)));
}

export async function patchJson<Req, Res>(
  path: string,
  body: Req,
  signal?: AbortSignal,
): Promise<Res> {
  return parse<Res>(await fetch(path, jsonInit("PATCH", body, signal)));
}

export async function deleteRequest(path: string, signal?: AbortSignal): Promise<void> {
  return parse<void>(await fetch(path, { method: "DELETE", signal }));
}

export async function postForm<Res>(
  path: string,
  form: FormData,
  signal?: AbortSignal,
): Promise<Res> {
  return parse<Res>(await fetch(path, { method: "POST", body: form, signal }));
}

export const enc = encodeURIComponent;
