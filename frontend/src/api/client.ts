import createClient from "openapi-fetch";
import type { paths } from "./schema";

/** Typed JSON client generated from the backend's OpenAPI schema (`npm run gen:api`). */
// An absolute origin, because Request (used by the client) cannot parse a relative URL outside a
// browser document, e.g. in tests. In the browser this is the same origin as before.
export const api = createClient<paths>({
  baseUrl: typeof location === "undefined" ? "" : location.origin,
  // Look fetch up per call (not once at import) so tests can stub it.
  fetch: (request) => fetch(request),
});

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function detailOf(body: unknown): string | undefined {
  if (body && typeof body === "object" && "detail" in body) {
    const { detail } = body as { detail: unknown };
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  }
  return undefined;
}

/** Return the data of an openapi-fetch result, or throw an ApiError with the server's detail. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error === undefined && result.data !== undefined) return result.data;
  const status = result.response.status;
  throw new ApiError(detailOf(result.error) ?? `Request failed (${status})`, status);
}
