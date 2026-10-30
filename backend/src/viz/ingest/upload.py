"""Streaming multipart uploads straight into `sources/_staged/<id>/`.

Starlette's `UploadFile` spools the whole body to a temp file before the endpoint runs, so a
size limit is only checked after the full upload, and the file is written twice. Here:

- `Content-Length` is checked first (413 before any byte is read), and every body byte is
  counted against `max_upload_mb` plus a small envelope while streaming (413), whatever part
  it belongs to;
- the first `file` part is streamed to its staged path; later `file` parts are ignored;
- at most `_MAX_PARTS` parts, part headers capped by the parser (`max_header_size`);
- small text fields (name, kind, options) are kept in memory, capped at 64 KiB each;
- a malformed body, or one without the closing boundary (a cut-off upload), is 422;
- the file name comes from `filename=` or RFC 2231 `filename*=`.
"""

from __future__ import annotations

import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any
from urllib.parse import unquote

from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import Request

from viz.config import Settings
from viz.errors import InvalidSpecError, PayloadTooLargeError
from viz.ingest import readers
from viz.storage import LocalStorage
from viz.storage.local import safe_file_name

_FIELD_LIMIT = 64 * 1024
# Multipart framing (boundaries, part headers, small fields) on top of the file itself.
_ENVELOPE = 256 * 1024
_MAX_PARTS = 32
_RFC2231 = re.compile(rb"filename\*\s*=\s*([^';]*)'[^']*'([^;\r\n]+)", re.IGNORECASE)

# OpenAPI for endpoints that use `receive_upload` (they take the raw Request).
UPLOAD_OPENAPI: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}


def upload_openapi(**fields: dict[str, Any]) -> dict[str, Any]:
    """`UPLOAD_OPENAPI` plus extra optional text fields."""
    schema = UPLOAD_OPENAPI["requestBody"]["content"]["multipart/form-data"]["schema"]
    props = {**schema["properties"], **fields}
    return {
        "requestBody": {
            "required": True,
            "content": {"multipart/form-data": {"schema": {**schema, "properties": props}}},
        }
    }


@dataclass
class Upload:
    staged_id: str
    path: Path
    file_name: str
    size: int
    fields: dict[str, str] = field(default_factory=dict)


def _too_large(max_bytes: int, hint: str) -> PayloadTooLargeError:
    return PayloadTooLargeError(f"file is larger than {max_bytes / 2**20:,.0f} MB{hint}")


def part_file_name(disposition_header: bytes, params: dict[bytes, bytes]) -> str | None:
    """The part's file name: RFC 2231 `filename*=charset''pct-encoded` wins over `filename=`."""
    match = _RFC2231.search(disposition_header)
    if match:
        charset = match.group(1).decode("ascii", "replace").strip() or "utf-8"
        try:
            return unquote(match.group(2).decode("ascii", "replace").strip(), encoding=charset)
        except LookupError:
            return unquote(match.group(2).decode("ascii", "replace").strip())
    raw = params.get(b"filename")
    return None if raw is None else raw.decode("utf-8", "replace")


async def receive_upload(
    request: Request,
    storage: LocalStorage,
    settings: Settings,
    *,
    max_bytes: int | None = None,
    accept: Callable[[str], None] | None = None,
    stage_dir: Path | None = None,
) -> Upload:
    """Stream the request's `file` part to `<stage_dir>/<id>/<name>`; deletes that folder on
    any error.

    - `max_bytes`: file size cap (default `settings.max_upload_mb`); 413 above it.
    - `accept(file_name)`: checks the name before anything is written and raises the API
      error itself (415/422). Default: a dataset file type (`readers.detect_kind`).
    - `stage_dir`: parent folder for the upload (default `storage.staged_dir`, the dataset
      staging area with its expiry sweep). Callers using another folder clean it up.
    The returned `Upload.staged_id` is the folder name under `stage_dir`.
    """
    content_type, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        raise InvalidSpecError("send the file as multipart/form-data with a 'file' field")
    limit = settings.max_upload_mb * 1024 * 1024 if max_bytes is None else max_bytes
    hint = "; import it from its path instead" if stage_dir is None else ""
    check_name: Callable[[str], object] = accept or readers.detect_kind
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit + _ENVELOPE:
        raise _too_large(limit, hint)

    staged_id = str(uuid.uuid4())
    folder = (stage_dir or storage.staged_dir) / staged_id

    def cleanup() -> None:
        shutil.rmtree(folder, ignore_errors=True)

    state: dict[str, Any] = {"headers": {}, "field": b"", "value": b"", "part": None}
    fields: dict[str, str] = {}
    result: dict[str, Any] = {"size": 0, "path": None, "name": None, "parts": 0}
    flags = {"file_done": False, "ended": False}
    out: list[IO[bytes]] = []
    text: list[bytes] = []

    def on_part_begin() -> None:
        result["parts"] += 1
        if result["parts"] > _MAX_PARTS:
            raise InvalidSpecError(f"the upload has more than {_MAX_PARTS} parts")
        state["headers"] = {}
        state["part"] = None
        text.clear()

    def on_header_field(data: bytes, start: int, end: int) -> None:
        state["field"] += data[start:end]

    def on_header_value(data: bytes, start: int, end: int) -> None:
        state["value"] += data[start:end]

    def on_header_end() -> None:
        state["headers"][state["field"].lower()] = state["value"]
        state["field"], state["value"] = b"", b""

    def on_headers_finished() -> None:
        header = state["headers"].get(b"content-disposition", b"")
        _, disposition = parse_options_header(header)
        name = disposition.get(b"name", b"").decode("utf-8", "replace")
        filename = part_file_name(header, disposition)
        if name != "file" or filename is None:
            state["part"] = name or None
            return
        if result["path"] is not None:
            state["part"] = "__ignored_file"  # only the first file part is kept
            return
        state["part"] = "file"
        file_name = filename or "upload.csv"
        check_name(file_name)  # 415/422 before writing anything
        target = folder / safe_file_name(file_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        result["path"], result["name"] = target, target.name
        out.append(target.open("wb"))

    def on_part_data(data: bytes, start: int, end: int) -> None:
        part = state["part"]
        if part == "file":
            chunk = data[start:end]
            result["size"] += len(chunk)
            if result["size"] > limit:
                raise _too_large(limit, hint)
            out[0].write(chunk)
        elif part is not None and part != "__ignored_file":
            text.append(data[start:end])
            if sum(len(t) for t in text) > _FIELD_LIMIT:
                raise InvalidSpecError(f"form field {part!r} is too large")

    def on_part_end() -> None:
        part = state["part"]
        if part == "file" and out:
            out[0].close()
            flags["file_done"] = True
        elif part is not None and part != "__ignored_file":
            fields[part] = b"".join(text).decode("utf-8", "replace")

    def on_end() -> None:
        flags["ended"] = True

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
            "on_end": on_end,
        },
    )
    received = 0
    try:
        try:
            async for chunk in request.stream():
                received += len(chunk)
                if received > limit + _ENVELOPE:
                    raise _too_large(limit, hint)
                parser.write(chunk)
            parser.finalize()
        except MultipartParseError as exc:
            raise InvalidSpecError(f"the upload isn't valid multipart data: {exc}") from exc
        if result["path"] is None:
            raise InvalidSpecError("the upload has no 'file' field")
        if not (flags["ended"] and flags["file_done"]):
            raise InvalidSpecError("the upload was cut off before it finished; try again")
    except BaseException:
        for handle in out:
            handle.close()
        cleanup()
        raise
    return Upload(staged_id, result["path"], result["name"], result["size"], fields)
