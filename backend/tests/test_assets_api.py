from __future__ import annotations

import os
import struct
import time
import zlib
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from viz.config import Settings
from viz.errors import NotFoundError
from viz.main import create_app
from viz.storage.assets import find_image, sniff_image_type
from viz.storage.local import LocalStorage


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
        # A quoted `>` inside a SYSTEM literal must not hide an internal-subset entity block.
        (
            b'<!DOCTYPE svg SYSTEM "x><svg " [<!ENTITY a "aaaa">]><svg>&a;</svg>',
            None,
        ),
        # A "comment" must not be read as spanning all the way past a later DOCTYPE.
        (
            b'<!-- a --><!DOCTYPE svg [<!ENTITY x "y">]><!-- b --><svg>&x;</svg>',
            None,
        ),
    ],
)
def test_sniff_image_type(head: bytes, expected: str | None) -> None:
    assert sniff_image_type(head) == expected


def test_svg_sniff_does_not_backtrack_catastrophically() -> None:
    """A run of unclosed-looking comments must not make the regex hang (ReDoS).

    k=28 is deliberately small: a regressed (exponentially backtracking) regex already takes
    several seconds here, so this fails fast instead of hanging the test run for hours."""
    head = b"<!---->" * 28 + b"X"
    start = time.monotonic()
    result = sniff_image_type(head)
    elapsed = time.monotonic() - start
    assert result is None
    assert elapsed < 0.5, f"sniff took {elapsed:.2f}s, should be near-instant"


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
    data = PNG + b"\x00" * (settings.max_asset_mb * 1024 * 1024)
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


def test_file_field_without_filename_is_422(client: TestClient) -> None:
    """A 'file' part with no filename must not reach the writer with nothing to write to."""
    response = client.post(
        "/api/assets",
        data={"file": "not-a-file"},
        files={"other": ("other.png", PNG, "image/png")},
    )
    assert response.status_code == 422, response.text


def test_max_asset_mb_is_enforced(tmp_path: Path) -> None:
    """The cap comes from `settings.max_asset_mb`, not a hard-coded constant."""
    oversized = PNG + b"\x00" * (2 * 1024 * 1024)

    tight = Settings(data_dir=tmp_path / "tight", max_asset_mb=1)
    with TestClient(create_app(tight)) as c:
        assert _upload(c, "big.png", oversized).status_code == 413

    roomy = Settings(data_dir=tmp_path / "roomy", max_asset_mb=3)
    with TestClient(create_app(roomy)) as c:
        assert _upload(c, "big.png", oversized).status_code == 201


@pytest.mark.parametrize("hostile_name", ["NUL", "NUL.png", "CON", "x" * 300 + ".png"])
def test_hostile_client_filename_never_reaches_disk(client: TestClient, hostile_name: str) -> None:
    """A reserved Windows device name or an over-long name must not reach the staging path,
    since the client's filename is never used for it (`stage_name="upload"`)."""
    response = _upload(client, hostile_name, PNG)
    assert response.status_code == 201, response.text


def test_find_image_rejects_traversal_and_unknown_ids(tmp_path: Path) -> None:
    """Exercises `find_image`'s own guard directly, not just the router's path routing."""
    storage = LocalStorage(tmp_path / "data")
    bad_ids = ("../meta", "..%2F..%2Fmeta.db", "not-an-id", "00000000-0000-4000-8000-000000000000")
    for bad_id in bad_ids:
        with pytest.raises(NotFoundError):
            find_image(storage, bad_id)


def test_sweep_removes_an_orphaned_incoming_upload(tmp_path: Path) -> None:
    """A crash or a locked file mid-upload leaves `assets/_incoming/<id>/`; `sweep()` expires it
    like it does `sources/_staged` and `temp/exports`."""
    storage = LocalStorage(tmp_path / "data")
    orphan = storage.assets_incoming_dir / "orphan-upload"
    orphan.mkdir(parents=True)
    (orphan / "upload.png").write_bytes(PNG)
    old = time.time() - 100_000
    os.utime(orphan, (old, old))

    removed = storage.sweep(staged_max_age_s=3600, exports_max_age_s=3600)
    assert removed >= 1
    assert not orphan.exists()
