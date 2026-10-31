/**
 * Image assets for the image widget (`POST /api/assets`, `GET /api/assets/{id}`).
 *
 * TODO(ui-data): move into `src/api` next to the other endpoint wrappers; it lives here only
 * because that folder is not mine.
 */
import { api, unwrap } from "../api/client";
import type { components } from "../api/schema";

export type ImageAsset = components["schemas"]["ImageAsset"];

/** What the server accepts, for the file picker (it sniffs the real type from the bytes). */
export const IMAGE_ACCEPT = "image/png,image/jpeg,image/gif,image/webp,image/svg+xml";

/** POST /api/assets (multipart `file`): 415 if it is not a supported image, 413 if too large. */
export async function uploadImage(file: File, signal?: AbortSignal): Promise<ImageAsset> {
  const form = new FormData();
  form.append("file", file);
  return unwrap(
    await api.POST("/api/assets", {
      // The client would JSON-encode a multipart body; hand it the prepared form instead.
      body: { file: "" } as never,
      bodySerializer: () => form,
      signal,
    }),
  );
}

/** Where an uploaded image is served from. */
export const assetUrl = (assetId: string) => `/api/assets/${encodeURIComponent(assetId)}`;
