from __future__ import annotations

import struct
import zlib

import httpx
import pytest
from fastapi.testclient import TestClient

from viz.config import Settings
from viz.storage.assets import MAX_ASSET_BYTES, sniff_image_type


def _png_1x1() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00\xff")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


PNG = _png_1x1()
SVG = b'<?xml version="1.0"?>\n<!-- logo -->\n<svg xmlns="http://www.w3.org/2000/svg"/>'


def _upload(client: TestClient, name: str, data: bytes, ctype: str = "image/png") -> httpx.Response:
    return client.post("/api/assets", files={"file": (name, data, ctype)})


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (PNG, "image/png"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
        (b"GIF89a\x01\x00", "image/gif"),
        (b"RIFF\x24\x00\x00\x00WEBPVP8 ", "image/webp"),
        (SVG, "image/svg+xml"),
        (b"\xef\xbb\xbf  <svg width='1'></svg>", "image/svg+xml"),
        (b"<!DOCTYPE svg PUBLIC '-//W3C//DTD SVG 1.1//EN'><svg>", "image/svg+xml"),
        (b"MZ\x90\x00\x03", None),  # Windows executable
        (b"<html><svg></svg></html>", None),
        (b"<!DOCTYPE svg [<!ENTITY a 'x'>]><svg>", None),  # internal subset refused
        (b"RIFF\x24\x00\x00\x00WAVEfmt ", None),
        (b"", None),
    ],
)
def test_sniff_image_type(head: bytes, expected: str | None) -> None:
    assert sniff_image_type(head) == expected


def test_upload_png_and_fetch_back(client: TestClient, settings: Settings) -> None:
    response = client.post(
        "/api/assets", files={"file": ("../../evil name.png", PNG, "application/octet-stream")}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["contentType"] == "image/png"
    assert body["sizeBytes"] == len(PNG)
    assert body["url"] == f"/api/assets/{body['id']}"
    # Stored under a minted id, never the client's name; nothing left in the incoming area.
    stored = list((settings.data_dir / "assets").glob("*.png"))
    assert [p.name for p in stored] == [f"{body['id']}.png"]
    assert not any((settings.data_dir / "assets" / "_incoming").iterdir())

    fetched = client.get(body["url"])
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"
    assert fetched.headers["x-content-type-options"] == "nosniff"
    assert fetched.content == PNG


def test_svg_served_with_sandbox_csp(client: TestClient) -> None:
    response = _upload(client, "logo.svg", SVG, "image/svg+xml")
    assert response.status_code == 201, response.text
    fetched = client.get(response.json()["url"])
    assert fetched.headers["content-type"].startswith("image/svg+xml")
    assert "sandbox" in fetched.headers["content-security-policy"]


def test_client_content_type_is_ignored(client: TestClient) -> None:
    """A real PNG declared as something else is still stored as PNG."""
    response = _upload(client, "picture.jpg", PNG, "image/jpeg")
    assert response.status_code == 201
    assert response.json()["contentType"] == "image/png"


def test_exe_masquerading_as_png_is_rejected(client: TestClient, settings: Settings) -> None:
    exe = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200
    response = _upload(client, "photo.png", exe, "image/png")
    assert response.status_code == 415, response.text
    assets = settings.data_dir / "assets"
    assert not [p for p in assets.iterdir() if p.is_file()]
    assert not any((assets / "_incoming").iterdir())


def test_too_large_is_413(client: TestClient, settings: Settings) -> None:
    data = PNG + b"\x00" * MAX_ASSET_BYTES
    response = _upload(client, "big.png", data)
    assert response.status_code == 413
    assert not [p for p in (settings.data_dir / "assets").iterdir() if p.is_file()]


@pytest.mark.parametrize(
    "asset_id",
    ["00000000-0000-4000-8000-000000000000", "not-an-id", "..%2F..%2Fmeta.db"],
)
def test_missing_asset_is_404(client: TestClient, asset_id: str) -> None:
    assert client.get(f"/api/assets/{asset_id}").status_code == 404


def test_upload_without_file_is_422(client: TestClient) -> None:
    response = client.post("/api/assets", data={"name": "x"}, files={"other": ("a", b"b")})
    assert response.status_code == 422
