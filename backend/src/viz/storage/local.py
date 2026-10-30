"""Local data dir layout. The only module that knows where files live (S3 comes later).

sources/<datasetId>/<file>        copies of imported originals
sources/_staged/<stagedId>/<file> uploads waiting for preview/import (expire)
datasets/<datasetId>/v<n>.parquet immutable dataset versions
derived/<key>.parquet             materialized pipeline steps (transform)
temp/                             DuckDB spill; temp/exports/<exportId>/<file> (expire)
assets/                           images and report assets
"""

from __future__ import annotations

import os
import re
import shutil
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from viz.errors import ConflictError, NotFoundError

_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_ID = re.compile(r"[0-9a-f\-]{8,64}")
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)*$")
STAGED = "_staged"


def safe_file_name(name: str) -> str:
    """Keep only the final path component and drop characters invalid on Windows or macOS."""
    base = Path(name.replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", base).strip(" .")
    return cleaned or "upload"


def _replace_with_retry(src: Path, dst: Path, retry_s: float) -> None:
    deadline = time.monotonic() + retry_s
    while True:
        try:
            os.replace(src, dst)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def staged_dir(self) -> Path:
        return self.sources_dir / STAGED

    @property
    def datasets_dir(self) -> Path:
        return self.root / "datasets"

    @property
    def derived_dir(self) -> Path:
        return self.root / "derived"

    @property
    def temp_dir(self) -> Path:
        return self.root / "temp"

    @property
    def exports_dir(self) -> Path:
        return self.temp_dir / "exports"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    def ensure(self) -> None:
        for directory in (
            self.sources_dir,
            self.staged_dir,
            self.datasets_dir,
            self.derived_dir,
            self.temp_dir,
            self.exports_dir,
            self.assets_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    # -- relative paths (what specs store) ----------------------------------------------

    def rel(self, path: Path) -> str:
        """Data-dir-relative POSIX path for storing in a spec."""
        return path.resolve().relative_to(self.root).as_posix()

    def resolve(self, rel: str) -> Path:
        """Turn a stored relative path into an absolute one, refusing anything outside root."""
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"path escapes the data dir: {rel}")
        return path

    def parquet_rel(self, dataset_id: str, version: int) -> str:
        return f"datasets/{dataset_id}/v{version}.parquet"

    def derived_rel(self, key: str) -> str:
        """Where a materialized pipeline step lives. `key` is a safe slug (may contain '/')."""
        # TODO(data-core): garbage-collect derived files no pipeline references any more.
        if not _SAFE_KEY.match(key) or ".." in key:
            raise ValueError(f"unsafe derived key: {key!r}")
        return f"derived/{key}.parquet"

    # -- sources --------------------------------------------------------------------------

    def new_source_path(self, dataset_id: str, file_name: str) -> Path:
        """A fresh path under sources/<datasetId>/; never overwrites an earlier source copy."""
        directory = self.sources_dir / dataset_id
        directory.mkdir(parents=True, exist_ok=True)
        name = safe_file_name(file_name)
        path = directory / name
        if path.exists():
            stem, suffix = Path(name).stem, Path(name).suffix
            path = directory / f"{stem}.{uuid.uuid4().hex[:8]}{suffix}"
        return path

    def staged_path(self, staged_id: str, file_name: str | None = None) -> Path:
        """The staged upload's directory, or the file in it when `file_name` is given."""
        if not _ID.fullmatch(staged_id):
            raise NotFoundError("the uploaded file has expired or doesn't exist; upload it again")
        directory = self.staged_dir / staged_id
        return directory / safe_file_name(file_name) if file_name else directory

    def find_staged(self, staged_id: str) -> Path | None:
        """The staged file, or None if the id is unknown, malformed or expired."""
        if not _ID.fullmatch(staged_id):
            return None
        directory = self.staged_dir / staged_id
        if not directory.is_dir():
            return None
        files = [p for p in directory.iterdir() if p.is_file() and not p.name.endswith(".tmp")]
        return files[0] if files else None

    def delete_staged(self, staged_id: str) -> None:
        shutil.rmtree(self.staged_path(staged_id), ignore_errors=True)

    # -- writes ---------------------------------------------------------------------------

    @contextmanager
    def atomic_write(self, rel: str) -> Iterator[Path]:
        """Yield a temp path next to `rel`; rename it into place only if the block succeeds."""
        final = self.resolve(rel)
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = final.with_name(f".{final.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            yield tmp
            os.replace(tmp, final)
        finally:
            tmp.unlink(missing_ok=True)

    def size(self, rel: str) -> int:
        return self.resolve(rel).stat().st_size

    # -- deletes and expiry ---------------------------------------------------------------

    @property
    def trash_dir(self) -> Path:
        return self.temp_dir / "trash"

    def _dataset_dirs(self, dataset_id: str) -> list[Path]:
        if not _ID.fullmatch(dataset_id) and not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", dataset_id):
            raise ValueError(f"invalid dataset id {dataset_id!r}")
        return [self.datasets_dir / dataset_id, self.sources_dir / dataset_id]

    def trash_dataset(self, dataset_id: str, *, retry_s: float = 1.0) -> None:
        """Move a dataset's Parquet versions and source copies to the trash, all or nothing.

        On Windows a directory with an open file can't be renamed. Each rename is retried for
        `retry_s` (a query reading the file usually finishes by then); if it still fails,
        nothing is moved and `ConflictError` names the folder, so the caller can keep the
        catalog row."""
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        moved: list[tuple[Path, Path]] = []
        for directory in self._dataset_dirs(dataset_id):
            if not directory.exists():
                continue
            target = self.trash_dir / f"{directory.parent.name}-{dataset_id}-{uuid.uuid4().hex[:8]}"
            try:
                _replace_with_retry(directory, target, retry_s)
            except OSError as exc:
                for original, trashed in reversed(moved):
                    os.replace(trashed, original)
                raise ConflictError(
                    f"this dataset's files are still in use ({self.rel(directory)}), possibly "
                    "by a query or grid that is reading it; try again in a moment"
                ) from exc
            moved.append((directory, target))
        self.empty_trash()

    def empty_trash(self) -> list[Path]:
        """Delete trashed directories; returns those that couldn't be removed (still in use)."""
        left: list[Path] = []
        if not self.trash_dir.is_dir():
            return left
        for child in self.trash_dir.iterdir():
            shutil.rmtree(child, ignore_errors=True)
            if child.exists():
                left.append(child)
        return left

    def delete_dataset(self, dataset_id: str) -> None:
        """Remove a dataset's Parquet versions and source copies (best effort)."""
        for directory in self._dataset_dirs(dataset_id):
            shutil.rmtree(directory, ignore_errors=True)

    def export_path(self, export_id: str, file_name: str) -> Path:
        directory = self.exports_dir / export_id
        directory.mkdir(parents=True, exist_ok=True)
        return directory / safe_file_name(file_name)

    def find_export(self, export_id: str, *, max_age_s: float | None = None) -> Path | None:
        """The export file, or None if unknown, unfinished or older than `max_age_s`."""
        if not _ID.fullmatch(export_id):
            return None
        directory = self.exports_dir / export_id
        if not directory.is_dir():
            return None
        files = [p for p in directory.iterdir() if p.is_file() and not p.name.endswith(".tmp")]
        if not files:
            return None
        if max_age_s is not None and time.time() - files[0].stat().st_mtime > max_age_s:
            shutil.rmtree(directory, ignore_errors=True)
            return None
        return files[0]

    def download_url(self, export_id: str) -> str:
        """Locally a route on this server; on AWS this becomes a presigned S3 URL."""
        return f"/api/exports/{export_id}/download"

    def sweep(self, *, staged_max_age_s: float, exports_max_age_s: float) -> int:
        """Delete expired staged uploads and export files. Returns how many dirs were removed."""
        now = time.time()
        removed = 0
        for parent, max_age in (
            (self.staged_dir, staged_max_age_s),
            (self.exports_dir, exports_max_age_s),
        ):
            if not parent.is_dir():
                continue
            for child in parent.iterdir():
                try:
                    age = now - child.stat().st_mtime
                except OSError:
                    continue
                if child.is_dir() and age > max_age:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
        return removed
