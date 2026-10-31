"""Image assets (dashboard Image widgets): `POST /api/assets`, `GET /api/assets/{id}`."""

from __future__ import annotations

from pydantic import Field

from viz.contracts.base import Contract


class ImageAsset(Contract):
    """A stored image. Display it with `<img src={url}>`."""

    id: str
    content_type: str = Field(description="Sniffed from the bytes, not the client's header")
    size_bytes: int
    url: str
