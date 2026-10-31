import { useState } from "react";
import { assetUrl } from "../assetsApi";
import type { Widget } from "../types";

type ImageWidgetValue = Extract<Widget, { kind: "image" }>;

export function ImageWidget({ widget }: { widget: ImageWidgetValue }) {
  // Remember which asset failed, so replacing the image gets a fresh try.
  const [failedAsset, setFailedAsset] = useState<string | null>(null);
  if (failedAsset === widget.assetId) {
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
      onError={() => setFailedAsset(widget.assetId)}
      className="h-full w-full"
      style={{ objectFit: widget.fit ?? "contain" }}
    />
  );
}
