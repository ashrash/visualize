"""Follow-up fixes in ingest: encoding choice (S1), upload parser (S5), upload options for
other callers, view builds, delete retry."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from viz.config import Settings
from viz.contracts import ARROW_META_KEY, ImportOptions, SourceKind
from viz.errors import UnsupportedFormatError
from viz.ingest import readers
from viz.ingest.importer import choose_encoding
from viz.ingest.upload import receive_upload
from viz.storage import LocalStorage

Stage = Callable[[str, bytes], dict[str, Any]]


def read_arrow(content: bytes) -> tuple[pa.Table, dict[str, Any]]:
    table = pa.ipc.open_stream(content).read_all()
    return table, json.loads(table.schema.metadata[ARROW_META_KEY.encode()])


def import_job(client: TestClient, **body: Any) -> Any:
    response = client.post("/api/datasets/import", json=body)
    assert response.status_code == 202, response.text
    return client.app.state.jobs.wait(response.json()["id"], 120)  # type: ignore[attr-defined]


# -- S1: encoding choice ----------------------------------------------------------------


def _mostly_utf8() -> bytes:
    rows = ["id,city,name"] + [f"{i},Zürich,Jürgen Müller 北京" for i in range(30000)]
    rows.append("30000,Montr\udce9al,x")  # one stray Latin-1 byte
    return "\n".join(rows).encode("utf-8", "surrogateescape") + b"\n"


def test_mostly_utf8_file_keeps_its_text(client: TestClient, stage: Stage, tmp_path: Path) -> None:
    body = _mostly_utf8()
    staged = stage("mostly_utf8.csv", body)
    response = client.post("/api/datasets/preview", json={"stagedId": staged["stagedId"]})
    table, _meta = read_arrow(response.content)
    assert table.slice(0, 1).to_pylist()[0]["city"] == "Zürich"
    job = import_job(client, stagedId=staged["stagedId"])
    assert job.status == "succeeded"
    assert any("1 byte" in w and "replaced" in w for w in job.result.warnings)
    rows = client.post(f"/api/datasets/{job.result.dataset_id}/rows", json={"limit": 1})
    assert read_arrow(rows.content)[0].to_pylist()[0]["name"] == "Jürgen Müller 北京"

    # Read in place (over the copy threshold): same choice, same result.
    client.app.state.settings.copy_source_max_mb = 0  # type: ignore[attr-defined]
    big = tmp_path / "big mostly utf8.csv"
    big.write_bytes(body)
    job = import_job(client, path=str(big))
    assert job.status == "succeeded", job.error
    assert any("replaced" in w for w in job.result.warnings)


def test_encoding_rules(tmp_path: Path) -> None:
    def choice(content: bytes, **options: Any) -> tuple[str | None, str | None]:
        path = tmp_path / "f.csv"
        path.write_bytes(content)
        picked = choose_encoding(path, SourceKind.CSV, ImportOptions(**options))
        return picked.transcode_from, picked.recorded

    assert choice("a\nZürich\n".encode()) == (None, None)
    assert choice("a\nZürich\n".encode("cp1252")) == ("cp1252", "cp1252")  # no valid UTF-8
    assert choice(_mostly_utf8()) == ("utf-8", "utf-8")  # replace the stray byte
    assert choice("a\nZürich\n".encode("cp1252"), encoding="utf-8") == ("utf-8", "utf-8")
    assert choice("a\nZürich\n".encode("latin-1"), encoding="latin-1") == ("iso8859-1",) * 2
    assert choice("a\n\ufffd\n".encode()) == (None, None)  # a genuine U+FFFD is valid UTF-8


def test_preview_and_import_agree_on_cp1252(client: TestClient, stage: Stage) -> None:
    staged = stage("legacy.csv", "name\nJosé\n".encode("cp1252"))
    _, meta = read_arrow(
        client.post("/api/datasets/preview", json={"stagedId": staged["stagedId"]}).content
    )
    job = import_job(client, stagedId=staged["stagedId"])
    assert meta["detected"]["encoding"] == "cp1252"
    dataset = client.get(f"/api/datasets/{job.result.dataset_id}").json()
    assert dataset["versions"][0]["options"]["encoding"] == "cp1252"


# -- S5: upload parser ------------------------------------------------------------------

B = b"XyZbound4242"
CT = {"content-type": f"multipart/form-data; boundary={B.decode()}"}
FILE = b'Content-Disposition: form-data; name="file"; filename="a.csv"'


def part(headers: bytes, body: bytes) -> bytes:
    return b"--" + B + b"\r\n" + headers + b"\r\n\r\n" + body + b"\r\n"


END = b"--" + B + b"--\r\n"


def chunks(data: bytes, size: int = 65536) -> Iterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i : i + size]


def post(client: TestClient, body: bytes, chunked: bool = True) -> Any:
    return client.post(
        "/api/datasets/staged", content=chunks(body) if chunked else body, headers=CT
    )


def _staged_dirs(client: TestClient) -> list[Path]:
    return list(client.app.state.storage.staged_dir.iterdir())  # type: ignore[attr-defined]


def test_second_file_part_is_ignored(client: TestClient) -> None:
    other = FILE.replace(b"a.csv", b"b.csv")
    response = post(client, part(FILE, b"a,b\n1,2\n") + part(other, b"x,y\n9,9\n") + END)
    assert response.status_code == 201, response.text
    staged = client.app.state.storage.find_staged(response.json()["stagedId"])  # type: ignore[attr-defined]
    assert staged.read_bytes() == b"a,b\n1,2\n"


def test_malformed_and_truncated_bodies_are_422(client: TestClient) -> None:
    truncated = b"--" + B + b"\r\n" + FILE + b"\r\n\r\na,b\n1,2\n3,"
    assert post(client, truncated).status_code == 422
    huge_header = part(FILE + b"\r\nX-Pad: " + b"p" * (4 << 20), b"a\n1\n") + END
    assert post(client, huge_header).status_code in (413, 422)
    assert post(client, b"garbage without boundaries").status_code == 422
    assert _staged_dirs(client) == []


def test_every_body_byte_counts_toward_the_limit(client: TestClient) -> None:
    client.app.state.settings.max_upload_mb = 1  # type: ignore[attr-defined]
    fields = b"".join(
        part(f'Content-Disposition: form-data; name="f{i}"'.encode(), b"v" * 60000)
        for i in range(30)
    )
    assert post(client, fields + part(FILE, b"a\n1\n") + END).status_code in (413, 422)
    assert _staged_dirs(client) == []


def test_rfc2231_file_name(client: TestClient) -> None:
    header = (
        b"Content-Disposition: form-data; name=\"file\"; filename*=UTF-8''%E5%90%8D%E5%89%8D.csv"
    )
    response = post(client, part(header, b"a\n1\n") + END)
    assert response.status_code == 201, response.text
    assert response.json()["fileName"] == "名前.csv"


def test_path_parts_in_file_names_stay_inside_staging(client: TestClient) -> None:
    for name in (b"..\\..\\evil.csv", b"../../evil.csv", b"C:\\Windows\\x.csv"):
        header = b'Content-Disposition: form-data; name="file"; filename="' + name + b'"'
        response = post(client, part(header, b"a\n1\n") + END)
        assert response.status_code == 201
    root = client.app.state.storage.root  # type: ignore[attr-defined]
    assert [p for p in root.parent.rglob("evil.csv") if "_staged" not in str(p)] == []


# -- receive_upload options (platform's .vizproj uploads) ------------------------------------


def test_receive_upload_options(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "data")
    storage.ensure()
    settings = Settings(data_dir=tmp_path / "data", max_upload_mb=1)
    elsewhere = tmp_path / "data" / "temp" / "vizproj"
    app = FastAPI()

    def only_vizproj(name: str) -> None:
        if not name.lower().endswith(".vizproj"):
            raise UnsupportedFormatError("upload a .vizproj file")

    @app.post("/up")
    async def up(request: Request) -> dict[str, Any]:
        upload = await receive_upload(
            request,
            storage,
            settings,
            max_bytes=3 * 2**20,
            accept=only_vizproj,
            stage_dir=elsewhere,
        )
        return {"path": str(upload.path), "size": upload.size, "id": upload.staged_id}

    from viz.errors import VizError

    @app.exception_handler(VizError)
    async def handler(_r: Request, exc: VizError) -> Any:
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    with TestClient(app) as client:
        two_mb = b"z" * (2 << 20)  # over max_upload_mb (1) but under max_bytes (3 MB)
        header = b'Content-Disposition: form-data; name="file"; filename="p.vizproj"'
        response = client.post("/up", content=part(header, two_mb) + END, headers=CT)
        assert response.status_code == 200, response.text
        path = Path(response.json()["path"])
        assert path.parent.parent == elsewhere and path.read_bytes() == two_mb
        assert list(storage.staged_dir.iterdir()) == []

        bad = header.replace(b"p.vizproj", b"p.csv")
        assert client.post("/up", content=part(bad, b"x") + END, headers=CT).status_code == 415
        big = part(header, b"z" * (5 << 20)) + END
        assert client.post("/up", content=chunks(big), headers=CT).status_code == 413
        assert [p for p in elsewhere.iterdir() if p.name != path.parent.name] == []


# -- views and delete ----------------------------------------------------------------------


def test_two_sorts_on_one_dataset_both_get_views(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    ds = sales_dataset["id"]
    views = client.app.state.row_views  # type: ignore[attr-defined]
    views.min_rows = 0
    for _ in range(3):  # two grids alternating their scroll requests
        for field in ("sales", "units"):
            client.post(f"/api/datasets/{ds}/rows", json={"sort": [{"field": field}], "offset": 2})
    views.wait_idle()
    assert len(list(views.directory.glob("*.parquet"))) == 2


def test_view_size_is_estimated_with_a_margin(
    client: TestClient, sales_dataset: dict[str, Any]
) -> None:
    views = client.app.state.row_views  # type: ignore[attr-defined]
    views.min_rows = 0
    size = sales_dataset["versions"][0]["sizeBytes"]
    views.max_bytes = int(size * 1.2)  # the source fits, a 1.6x view wouldn't
    client.post(
        f"/api/datasets/{sales_dataset['id']}/rows",
        json={"sort": [{"field": "sales"}], "offset": 1},
    )
    views.wait_idle()
    assert list(views.directory.glob("*.parquet")) == []


def test_wait_idle_after_close_is_quiet(client: TestClient) -> None:
    views = client.app.state.row_views  # type: ignore[attr-defined]
    views.close()
    views.wait_idle()


def test_delete_retries_a_briefly_locked_folder(
    client: TestClient, sales_dataset: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ds = sales_dataset["id"]
    real = os.replace
    release_at = time.monotonic() + 0.3

    def locked_for_a_moment(src: Any, dst: Any) -> None:
        if str(src).endswith(os.path.join("datasets", ds)) and time.monotonic() < release_at:
            raise PermissionError(32, "in use")
        real(src, dst)

    monkeypatch.setattr(os, "replace", locked_for_a_moment)
    assert client.delete(f"/api/datasets/{ds}?force=true").status_code == 204


def test_delete_message_when_still_in_use(
    client: TestClient, sales_dataset: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ds = sales_dataset["id"]
    real = os.replace

    def locked(src: Any, dst: Any) -> None:
        if str(src).endswith(os.path.join("datasets", ds)):
            raise PermissionError(32, "in use")
        real(src, dst)

    monkeypatch.setattr(os, "replace", locked)
    started = time.monotonic()
    response = client.delete(f"/api/datasets/{ds}?force=true")
    assert response.status_code == 409 and time.monotonic() - started >= 0.9
    assert "try again in a moment" in response.json()["detail"]
    assert "close anything" not in response.json()["detail"]


def test_scan_counts(tmp_path: Path) -> None:
    path = tmp_path / "s.txt"
    path.write_bytes("Zürich".encode() + b"\xe9" + b"x" * 10)
    stats = readers.scan_utf8(path)
    assert stats.invalid == 1 and stats.multibyte >= 2


# -- ui-data follow-ups -------------------------------------------------------------------


def test_two_sheets_from_one_upload_in_parallel(client: TestClient, stage: Stage) -> None:
    import io

    import xlsxwriter

    buffer = io.BytesIO()
    book = xlsxwriter.Workbook(buffer, {"in_memory": True})
    for name, rows in {"North": [["a"], [1], [2]], "South": [["b"], [3]]}.items():
        sheet = book.add_worksheet(name)
        for r, row in enumerate(rows):
            sheet.write_row(r, 0, row)
    book.close()
    staged = stage("regions.xlsx", buffer.getvalue())
    jobs = client.app.state.jobs  # type: ignore[attr-defined]
    submitted = [
        client.post(
            "/api/datasets/import",
            json={"stagedId": staged["stagedId"], "options": {"sheet": sheet}},
        ).json()
        for sheet in ("North", "South")
    ]
    done = [jobs.wait(job["id"], 60) for job in submitted]
    assert [j.status for j in done] == ["succeeded", "succeeded"], [j.error for j in done]
    counts = [
        client.get(f"/api/datasets/{j.result.dataset_id}").json()["versions"][0]["rowCount"]
        for j in done
    ]
    assert counts == [2, 1]
    storage = client.app.state.storage  # type: ignore[attr-defined]
    assert storage.find_staged(staged["stagedId"]) is not None  # kept for more sheets
    # Deleting the upload doesn't touch the imported datasets' source copies.
    assert client.delete(f"/api/datasets/staged/{staged['stagedId']}").status_code == 204
    for j in done:
        version = client.get(f"/api/datasets/{j.result.dataset_id}").json()["versions"][0]
        assert (storage.root / version["sourcePath"]).is_file()
    # Expired uploads are still swept.
    again = stage("regions.xlsx", buffer.getvalue())
    assert storage.sweep(staged_max_age_s=-1, exports_max_age_s=1e9) >= 1
    assert storage.find_staged(again["stagedId"]) is None


def test_two_imports_of_one_upload_run_in_parallel(client: TestClient, stage: Stage) -> None:
    from viz.jobs import InProcessJobRunner

    staged = stage("p.csv", b"a\n1\n2\n")
    app_jobs = client.app.state.jobs  # type: ignore[attr-defined]
    runner = InProcessJobRunner(
        engine=client.app.state.engine,  # type: ignore[attr-defined]
        storage=client.app.state.storage,  # type: ignore[attr-defined]
        sessionmaker=client.app.state.sessionmaker,  # type: ignore[attr-defined]
        settings=client.app.state.settings,  # type: ignore[attr-defined]
        max_workers=2,
    )
    client.app.state.jobs = runner  # type: ignore[attr-defined]
    try:
        ids = [
            client.post("/api/datasets/import", json={"stagedId": staged["stagedId"]}).json()["id"]
            for _ in range(2)
        ]
        assert [runner.wait(i, 60).status for i in ids] == ["succeeded", "succeeded"]
    finally:
        client.app.state.jobs = app_jobs  # type: ignore[attr-defined]
        runner.close()


def test_one_shot_upload_cleans_its_staging(client: TestClient) -> None:
    response = client.post("/api/datasets/upload", files={"file": ("x.csv", b"a\n1\n")})
    assert response.status_code == 201
    assert _staged_dirs(client) == []


def test_search_is_accent_insensitive(client: TestClient, stage: Stage) -> None:
    staged = stage("cities.csv", "city\nZürich\nMontréal\nOslo\n".encode())
    job = import_job(client, stagedId=staged["stagedId"])
    ds = job.result.dataset_id
    for needle, expected in (("zur", ["Zürich"]), ("ZÜR", ["Zürich"]), ("montre", ["Montréal"])):
        response = client.post(f"/api/datasets/{ds}/rows", json={"search": needle})
        table, _ = read_arrow(response.content)
        assert table.column("city").to_pylist() == expected, needle


def test_encoding_warnings_mention_import_options() -> None:
    from viz.ingest.importer import not_utf8_warning, replaced_warning

    for text in (not_utf8_warning("cp1252"), replaced_warning(3)):
        assert "import options" in text
