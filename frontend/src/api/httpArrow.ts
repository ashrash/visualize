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

/**
 * True for the error fetch raises when its AbortSignal fires. Pass the request's signal as well
 * to also recognise an abort that surfaced as another error (e.g. while reading the body).
 */
export function isAbortError(error: unknown, signal?: AbortSignal): boolean {
  return (error instanceof DOMException && error.name === "AbortError") || signal?.aborted === true;
}

/**
 * POST a JSON body to an endpoint that answers with either an Arrow IPC stream (data) or JSON
 * (a structured result such as a StatResult), chosen by the response content type. Non-2xx
 * responses become ApiError with the server's `detail`. A body that cannot be decoded (an HTML
 * proxy page, an empty 204, a corrupt `viz.meta`) is an ApiError carrying the HTTP status. An
 * aborted request rejects with the browser's AbortError unchanged (see `isAbortError`).
 */
export async function postArrowOrJson<TJson>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<ArrowOrJson<TJson>> {
  const response = await fetch(path, jsonInit("POST", body, signal));
  if (!response.ok) {
    let detail: string | undefined;
    try {
      detail = detailOf(await response.json());
    } catch (error) {
      if (isAbortError(error, signal)) throw error;
    }
    throw new ApiError(detail ?? `Request failed (${response.status})`, response.status);
  }
  try {
    if ((response.headers.get("content-type") ?? "").includes("application/json")) {
      return { kind: "json", body: (await response.json()) as TJson };
    }
    const bytes = new Uint8Array(await response.arrayBuffer());
    if (bytes.length === 0) throw new Error("empty response");
    const table = tableFromIPC(bytes);
    const raw = table.schema.metadata.get(ARROW_META_KEY);
    return { kind: "arrow", table, meta: raw === undefined ? undefined : JSON.parse(raw) };
  } catch (error) {
    if (isAbortError(error, signal)) throw error;
    throw new ApiError(
      `The server sent a response that could not be read (${response.status})`,
      response.status,
    );
  }
}
