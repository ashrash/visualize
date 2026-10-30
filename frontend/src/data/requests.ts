import { ApiError } from "../api/client";
import type { ImportOptions } from "../api/types";

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
