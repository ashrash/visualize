import { ApiError } from "../api/client";
import type { ImportFromPathRequest, ImportOptions, Predicate, SourceKind } from "../api/types";

/** Drop unset, empty and NaN options so the server auto-detects those. */
export function cleanOptions(options: ImportOptions): ImportOptions {
  const out: ImportOptions = {};
  if (options.delimiter) out.delimiter = options.delimiter;
  if (options.encoding?.trim()) out.encoding = options.encoding.trim();
  if (options.header !== null && options.header !== undefined) out.header = options.header;
  if (options.sheet?.trim()) out.sheet = options.sheet.trim();
  if (typeof options.headerRow === "number" && Number.isFinite(options.headerRow)) {
    out.headerRow = options.headerRow;
  }
  return out;
}

export function hasOptions(options: ImportOptions): boolean {
  return Object.keys(cleanOptions(options)).length > 0;
}

export function buildImportPathBody(
  path: string,
  options: ImportOptions,
  extra: { name?: string; kind?: SourceKind } = {},
): ImportFromPathRequest {
  const body: ImportFromPathRequest = { path: path.trim(), options: cleanOptions(options) };
  if (extra.name?.trim()) body.name = extra.name.trim();
  if (extra.kind) body.kind = extra.kind;
  return body;
}

/**
 * Upload form. TODO(data-core): the upload endpoint ignores import options today. Proposed
 * contract: an optional form field `options` holding the ImportOptions JSON (camelCase), plus
 * optional `kind`. The field is sent only when non-empty so today's server is unaffected.
 */
export function buildUploadForm(file: File, options: ImportOptions, name?: string): FormData {
  const form = new FormData();
  form.append("file", file);
  if (name?.trim()) form.append("name", name.trim());
  const cleaned = cleanOptions(options);
  if (Object.keys(cleaned).length > 0) form.append("options", JSON.stringify(cleaned));
  return form;
}

export interface SortSpec {
  field: string;
  descending: boolean;
}

/**
 * The one place that turns grid state into a rows request.
 * TODO(data-core): the rows endpoint accepts only offset/limit today. Proposed contract:
 * `sort=<field>:<asc|desc>` (repeatable, in priority order) and `filters=<JSON Predicate[]>`.
 */
export function buildRowsQuery(params: {
  offset: number;
  limit: number;
  sort?: SortSpec[];
  filters?: Predicate[];
}): URLSearchParams {
  const query = new URLSearchParams({ offset: String(params.offset), limit: String(params.limit) });
  for (const s of params.sort ?? []) {
    query.append("sort", `${s.field}:${s.descending ? "desc" : "asc"}`);
  }
  if (params.filters && params.filters.length > 0) {
    query.set("filters", JSON.stringify(params.filters));
  }
  return query;
}

/** Sorting and filtering are shown but disabled until the rows endpoint accepts them. */
export const SERVER_SORT_FILTER_SUPPORTED = false;

/** User-facing text for an import failure; the server's `detail` is kept when present. */
export function describeImportError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 409:
        return `Conflict: ${error.message}`;
      case 413:
        return `The file is too large to upload. Import it in place with a local path instead. (${error.message})`;
      case 415:
        return `Unsupported file type. Use CSV, TSV, JSON or Excel. (${error.message})`;
      case 422:
        return `The file could not be read with these options: ${error.message}`;
      default:
        return error.message;
    }
  }
  if (error instanceof DOMException && error.name === "AbortError") return "Import cancelled.";
  return error instanceof Error ? error.message : "Import failed.";
}
