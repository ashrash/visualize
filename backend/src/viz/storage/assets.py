"""Image assets (dashboard Image widgets): `assets/<id>.<ext>` in the data dir.

The type is sniffed from the file's leading bytes; the client's file name and declared
content type are ignored. Ids are minted here, never taken from the request.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from viz.errors import NotFoundError, UnsupportedFormatError
from viz.storage.local import LocalStorage

# TODO(lead): make this `VIZ_MAX_ASSET_MB` in config.py; `max_upload_mb` (4 GB) is for datasets.
MAX_ASSET_BYTES = 10 * 1024 * 1024
INCOMING = "_incoming"

# Sniffed content type -> stored extension.
EXTENSIONS: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
_CONTENT_TYPES = {ext: ctype for ctype, ext in EXTENSIONS.items()}
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SNIFF_BYTES = 4096
# Optional BOM / XML declaration, then comments or a DOCTYPE without an internal subset
# (no entity definitions), then the <svg> root element.
_SVG = re.compile(
    rb"^(?:\xef\xbb\xbf)?\s*(?:<\?xml[^>]*\?>\s*)?(?:<!--.*?-->\s*|<!doctype\s+svg[^>\[]*>\s*)*<svg[\s>/]",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class StoredAsset:
    id: str
    path: Path
    content_type: str
    size_bytes: int


def sniff_image_type(head: bytes) -> str | None:
    """The image content type from a file's first bytes, or None if it isn't an allowed image."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if _SVG.match(head):
        return "image/svg+xml"
    return None


def incoming_dir(storage: LocalStorage) -> Path:
    """Where uploads stream to before they are sniffed (cleaned up by `store_image`)."""
    return storage.assets_dir / INCOMING


def store_image(storage: LocalStorage, uploaded: Path) -> StoredAsset:
    """Sniff `uploaded` and move it to `assets/<new id>.<ext>`; 415 if it isn't an allowed image.

    The uploaded file is always consumed (moved or deleted)."""
    try:
        with uploaded.open("rb") as fh:
            head = fh.read(_SNIFF_BYTES)
        content_type = sniff_image_type(head)
        if content_type is None:
            raise UnsupportedFormatError(
                "the file isn't a supported image; use PNG, JPEG, GIF, WebP or SVG"
            )
        asset_id = str(uuid.uuid4())
        storage.assets_dir.mkdir(parents=True, exist_ok=True)
        target = storage.assets_dir / f"{asset_id}{EXTENSIONS[content_type]}"
        size = uploaded.stat().st_size
        os.replace(uploaded, target)
        return StoredAsset(asset_id, target, content_type, size)
    finally:
        uploaded.unlink(missing_ok=True)


def find_image(storage: LocalStorage, asset_id: str) -> StoredAsset:
    """The stored asset for `asset_id`; 404 if the id is malformed or unknown."""
    if _ID.fullmatch(asset_id):
        for ext, content_type in _CONTENT_TYPES.items():
            path = storage.assets_dir / f"{asset_id}{ext}"
            if path.is_file():
                return StoredAsset(asset_id, path, content_type, path.stat().st_size)
    raise NotFoundError("this image doesn't exist; upload it again")
