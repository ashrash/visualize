"""Streaming multipart uploads straight into `sources/_staged/<id>/`.

Starlette's `UploadFile` spools the whole body to a temp file before the endpoint runs, so a
size limit is only checked after the full upload, and the file is written twice. Here the
`Content-Length` is checked first (413 before any byte is read), and the `file` part is
streamed to its staged path while the byte count is enforced. Small text fields (name,
kind, options) are collected in memory, capped at 64 KiB each.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.requests import Request

from viz.config import Settings
from viz.errors import InvalidSpecError, PayloadTooLargeError
from viz.ingest import readers
from viz.storage import LocalStorage

_FIELD_LIMIT = 64 * 1024
# Multipart framing (boundaries, part headers) on top of the file itself.
_ENVELOPE = 64 * 1024

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


def _too_large(settings: Settings) -> PayloadTooLargeError:
    return PayloadTooLargeError(
        f"file is larger than {settings.max_upload_mb} MB; import it from its path instead"
    )


async def receive_upload(request: Request, storage: LocalStorage, settings: Settings) -> Upload:
    """Stream the request's `file` part to a new staged folder. Deletes it on any error."""
    content_type, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        raise InvalidSpecError("send the file as multipart/form-data with a 'file' field")
    limit = settings.max_upload_mb * 1024 * 1024
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit + _ENVELOPE:
        raise _too_large(settings)

    staged_id = str(uuid.uuid4())
    state: dict[str, Any] = {"headers": {}, "field": b"", "value": b"", "part": None}
    fields: dict[str, str] = {}
    result: dict[str, Any] = {"size": 0, "path": None, "name": None}
    out: list[IO[bytes]] = []
    text: list[bytes] = []

    def on_part_begin() -> None:
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
        _, disposition = parse_options_header(state["headers"].get(b"content-disposition", b""))
        name = disposition.get(b"name", b"").decode("utf-8", "replace")
        filename = disposition.get(b"filename")
        state["part"] = name
        if name == "file" and filename is not None and result["path"] is None:
            file_name = filename.decode("utf-8", "replace") or "upload.csv"
            readers.detect_kind(file_name)  # 415 before writing anything
            target = storage.staged_path(staged_id, file_name)
            target.parent.mkdir(parents=True, exist_ok=True)
            result["path"], result["name"] = target, target.name
            out.append(target.open("wb"))

    def on_part_data(data: bytes, start: int, end: int) -> None:
        chunk = data[start:end]
        if state["part"] == "file" and out:
            result["size"] += len(chunk)
            if result["size"] > limit:
                raise _too_large(settings)
            out[0].write(chunk)
        else:
            text.append(chunk)
            if sum(len(t) for t in text) > _FIELD_LIMIT:
                raise InvalidSpecError(f"form field {state['part']!r} is too large")

    def on_part_end() -> None:
        if state["part"] == "file" and out:
            out[0].close()
        elif state["part"]:
            fields[state["part"]] = b"".join(text).decode("utf-8", "replace")

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
        },
    )
    try:
        async for chunk in request.stream():
            parser.write(chunk)
        parser.finalize()
        if result["path"] is None:
            raise InvalidSpecError("the upload has no 'file' field")
    except BaseException:
        for handle in out:
            handle.close()
        storage.delete_staged(staged_id)
        raise
    return Upload(staged_id, result["path"], result["name"], result["size"], fields)
