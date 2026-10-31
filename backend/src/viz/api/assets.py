"""Image assets for dashboard Image widgets: upload one image, fetch it back by id."""

from __future__ import annotations

import shutil
from pathlib import Path

from anyio.to_thread import run_sync
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from viz.api.deps import SettingsDep, StorageDep
from viz.contracts.assets import ImageAsset
from viz.ingest.upload import UPLOAD_OPENAPI, receive_upload
from viz.storage.assets import (
    EXTENSIONS,
    find_image,
    incoming_dir,
    store_image,
)
from viz.storage.local import LocalStorage

router = APIRouter(prefix="/assets", tags=["assets"])

# Stored images never change (a new upload gets a new id). The CSP keeps an SVG opened directly
# at its URL from running scripts on this origin; inside <img> scripts never run anyway.
_HEADERS = {
    "Cache-Control": "private, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
}


def _accept_any_name(_name: str) -> None:
    """The type is sniffed from the bytes after upload; the file name is ignored."""


def _store(storage: LocalStorage, upload_dir_id: str, uploaded: Path) -> ImageAsset:
    try:
        asset = store_image(storage, uploaded)
    finally:
        shutil.rmtree(incoming_dir(storage) / upload_dir_id, ignore_errors=True)
    return ImageAsset(
        id=asset.id,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        url=storage.asset_url(asset.id),
    )


@router.post("", response_model=ImageAsset, status_code=201, openapi_extra=UPLOAD_OPENAPI)
async def upload_image(request: Request, storage: StorageDep, settings: SettingsDep) -> ImageAsset:
    """Upload one image (multipart field `file`): PNG, JPEG, GIF, WebP or SVG, at most
    `settings.max_asset_mb` (default 10 MB). The type is sniffed from the file's bytes (415 if it
    isn't one of those); 413 if too big."""
    upload = await receive_upload(
        request,
        storage,
        settings,
        max_bytes=settings.max_asset_mb * 1024 * 1024,
        accept=_accept_any_name,
        stage_dir=incoming_dir(storage),
        stage_name="upload",
    )
    return await run_sync(_store, storage, upload.staged_id, upload.path)


@router.get(
    "/{asset_id}",
    response_class=FileResponse,
    responses={200: {"content": {ctype: {} for ctype in EXTENSIONS}}},
)
def get_image(asset_id: str, storage: StorageDep) -> FileResponse:
    """The stored image with its sniffed content type; 404 if the id is unknown."""
    asset = find_image(storage, asset_id)
    return FileResponse(asset.path, media_type=asset.content_type, headers=_HEADERS)
