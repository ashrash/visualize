"""Smoke test against a running Visualize server (stdlib only, no repo imports).

    uv run python infra/smoke.py [--url http://127.0.0.1:8000] [--csv samples/sales.csv]

Imports a CSV, creates and saves a project that references it, autosaves, lists versions,
reloads, exports a .vizproj and re-imports it, and creates a share token. Used for the wheel
install check and the Docker Compose / Postgres check. Exits non-zero on the first failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


def call(
    base: str,
    method: str,
    path: str,
    body: Any = None,
    *,
    raw: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, bytes]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Content-Type": content_type or "application/json"} if data is not None else {}
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def multipart(fields: dict[str, str], filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def check(ok: bool, what: str, detail: object = "") -> None:
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        print(f"     {detail!r}"[:600])
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--csv", type=Path, default=Path(__file__).parents[1] / "samples" / "sales.csv")
    args = ap.parse_args()
    base = args.url.rstrip("/") + "/api"

    status, body = call(args.url.rstrip("/"), "GET", "/")
    check(status == 200 and b"<div id=" in body, "built UI served at /", status)

    payload, ctype = multipart({"name": "smoke sales"}, args.csv.name, args.csv.read_bytes())
    status, body = call(base, "POST", "/datasets/upload", raw=payload, content_type=ctype)
    check(status == 201, "CSV upload + import", body)
    dataset = json.loads(body)
    rows = dataset["versions"][-1]["rowCount"]
    check(rows > 0, f"dataset has {rows} rows")

    status, body = call(base, "POST", "/projects", {"name": "smoke project"})
    check(status == 201, "create project", body)
    project = json.loads(body)

    chart_id = str(uuid.uuid4())
    project["datasetIds"] = [dataset["id"]]
    project["charts"] = [
        {
            "id": chart_id,
            "plotType": "bar",
            "source": {"kind": "dataset", "id": dataset["id"]},
            "encodings": {"x": [{"field": "region"}], "y": [{"field": "sales", "agg": "sum"}]},
        }
    ]
    status, body = call(base, "PUT", f"/projects/{project['id']}", project)
    check(status == 200, "save project", body)
    saved = json.loads(body)
    check(saved["revision"] == project["revision"] + 1, "revision bumped", saved["revision"])

    status, body = call(base, "PUT", f"/projects/{project['id']}", project)
    check(status == 409, "stale revision rejected with 409", (status, body))

    status, body = call(base, "PUT", f"/projects/{project['id']}?autosave=true", saved)
    check(status == 200, "autosave", body)
    saved = json.loads(body)

    status, body = call(base, "GET", f"/projects/{project['id']}/versions")
    check(status == 200 and len(json.loads(body)) >= 1, "version history", body)

    status, body = call(base, "GET", f"/projects/{project['id']}")
    check(
        status == 200 and json.loads(body)["datasetIds"] == [dataset["id"]],
        "reload project keeps dataset",
        body,
    )

    status, body = call(base, "GET", f"/projects/{project['id']}/export")
    check(status == 200 and body[:2] == b"PK", ".vizproj export is a zip", status)
    payload, ctype = multipart({}, "smoke.vizproj", body)
    status, body = call(base, "POST", "/projects/import", raw=payload, content_type=ctype)
    check(status == 201, ".vizproj re-import", body)

    status, body = call(
        base, "POST", "/share", {"projectId": project["id"], "kind": "chart", "itemId": chart_id}
    )
    check(status == 201, "create share token", body)
    token = json.loads(body)["token"]
    status, body = call(base, "GET", f"/share/{token}")
    check(status == 200, "share token resolves", body)

    print("smoke test passed")


if __name__ == "__main__":
    main()
