import { useState } from "react";
import type { Widget } from "../types";

type ImageWidgetValue = Extract<Widget, { kind: "image" }>;

/**
 * TODO(platform): there is no assets endpoint yet. `/api/assets/{id}` is the assumed URL for an
 * uploaded image (the data dir already has an `assets/` folder); update `assetUrl` when the real
 * route lands. Until then the widget shows its alt text.
 */
export const assetUrl = (assetId: string) => `/api/assets/${encodeURIComponent(assetId)}`;

export function ImageWidget({ widget }: { widget: ImageWidgetValue }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <p className="flex h-full items-center justify-center p-3 text-center text-sm text-muted">
        {widget.alt || "Image unavailable"}
      </p>
    );
  }
  return (
    <img
      src={assetUrl(widget.assetId)}
      alt={widget.alt}
      onError={() => setFailed(true)}
      className="h-full w-full"
      style={{ objectFit: widget.fit ?? "contain" }}
    />
  );
}
