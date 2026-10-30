"""Local data dir layout: sources/ · datasets/<id>/v<n>.parquet · temp/ · assets/."""

from __future__ import annotations

import re
from pathlib import Path

_UNSAFE = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def safe_file_name(name: str) -> str:
    """Keep only the final path component and drop characters invalid on Windows or macOS."""
    base = Path(name.replace("\\", "/")).name
    cleaned = _UNSAFE.sub("_", base).strip(" .")
    return cleaned or "upload"


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def datasets_dir(self) -> Path:
        return self.root / "datasets"

    @property
    def temp_dir(self) -> Path:
        return self.root / "temp"

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    def ensure(self) -> None:
        for directory in (self.sources_dir, self.datasets_dir, self.temp_dir, self.assets_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def new_source_path(self, dataset_id: str, file_name: str) -> Path:
        path = self.sources_dir / dataset_id / safe_file_name(file_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def parquet_rel(self, dataset_id: str, version: int) -> str:
        return f"datasets/{dataset_id}/v{version}.parquet"

    def resolve(self, rel: str) -> Path:
        """Turn a stored relative path into an absolute one, refusing anything outside root."""
        path = (self.root / rel).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"path escapes the data dir: {rel}")
        return path
