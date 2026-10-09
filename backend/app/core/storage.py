"""Object storage for document originals (ADR-010).

Keys are content-addressed and immutable: ``<firm>/<matter>/<aa>/<sha256>``. LocalStorage is for dev and
on-premise single-node installs; an S3/MinIO/Azure Blob adapter implements the same three methods.
"""

import os
import uuid
from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


def content_key(firm_id: uuid.UUID, matter_id: uuid.UUID, sha256: str) -> str:
    return f"{firm_id}/{matter_id}/{sha256[:2]}/{sha256}"


class Storage(Protocol):
    def put_file(self, key: str, src: Path) -> None: ...
    def read(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "_tmp").mkdir(exist_ok=True)

    @property
    def tmp_dir(self) -> Path:
        return self.root / "_tmp"

    def _path(self, key: str) -> Path:
        p = (self.root / Path(*key.split("/"))).resolve()
        if self.root not in p.parents:  # never allow a key to escape the storage root
            raise ValueError("invalid storage key")
        return p

    def put_file(self, key: str, src: Path) -> None:
        dest = self._path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():  # identical content already stored
            src.unlink(missing_ok=True)
            return
        os.replace(src, dest)

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


_storage: LocalStorage | None = None


def get_storage() -> LocalStorage:
    global _storage
    root = get_settings().storage_dir
    if _storage is None or _storage.root != root.resolve():
        _storage = LocalStorage(root)
    return _storage
