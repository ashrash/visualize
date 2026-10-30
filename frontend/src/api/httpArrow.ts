import { type ColumnarResult, fetchArrow } from "./arrow";
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
