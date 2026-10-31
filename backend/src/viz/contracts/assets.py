"""Image assets (dashboard Image widgets): `POST /api/assets`, `GET /api/assets/{id}`."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from viz.contracts.base import Contract

# Must match `storage.assets.EXTENSIONS`'s keys.
ImageContentType = Literal["image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"]


class ImageAsset(Contract):
    """A stored image. Display it with `<img src={url}>`."""

    id: str
    content_type: ImageContentType = Field(
        description="Sniffed from the bytes, not the client's header"
    )
    size_bytes: int
    url: str
