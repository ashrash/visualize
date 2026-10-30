import { type Table, tableFromIPC } from "apache-arrow";
import { ARROW_META_KEY, type ColumnarResult, fetchArrow } from "./arrow";
import { ApiError, detailOf } from "./client";
import { jsonInit } from "./http";

/**
 * POST a JSON body and read an Arrow IPC response (its JSON metadata arrives as `meta`).
 * Kept apart from http.ts so that code which never touches Arrow (the app shell) does not pull
 * apache-arrow into the entry chunk.
 */
export function postArrow<Req, Meta>(
  path: string,
  body: Req,
  signal?: AbortSignal,
): Promise<ColumnarResult<Meta>> {
  return fetchArrow<Meta>(path, jsonInit("POST", body, signal));
}

export type ArrowOrJson<TJson> =
  | { kind: "arrow"; table: Table; meta: unknown }
  | { kind: "json"; body: TJson };

/** True for the error fetch raises when its AbortSignal fires. */
export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

/**
 * POST a JSON body to an endpoint that answers with either an Arrow IPC stream (data) or JSON
 * (a structured result such as a StatResult), chosen by the response content type. Non-2xx
 * responses become ApiError with the server's `detail`. An aborted request rejects with the
 * browser's AbortError unchanged (see `isAbortError`), so callers can tell it from a failure.
 */
export async function postArrowOrJson<TJson>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<ArrowOrJson<TJson>> {
  const response = await fetch(path, jsonInit("POST", body, signal));
  if (!response.ok) {
    const detail = detailOf(await response.json().catch(() => undefined));
    throw new ApiError(detail ?? `Request failed (${response.status})`, response.status);
  }
  if ((response.headers.get("content-type") ?? "").includes("application/json")) {
    return { kind: "json", body: (await response.json()) as TJson };
  }
  const table = tableFromIPC(new Uint8Array(await response.arrayBuffer()));
  const raw = table.schema.metadata.get(ARROW_META_KEY);
  return { kind: "arrow", table, meta: raw === undefined ? undefined : JSON.parse(raw) };
}
