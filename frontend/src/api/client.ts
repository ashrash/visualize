import createClient from "openapi-fetch";
import type { paths } from "./schema";

/** Typed JSON client generated from the backend's OpenAPI schema (`npm run gen:api`). */
export const api = createClient<paths>({ baseUrl: "" });

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
