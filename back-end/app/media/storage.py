"""Private file storage for uploaded media on the local filesystem.

Bytes live under `MEDIA_ROOT` (default `back-end/.media`, git-ignored) at an object key the
server derives from UUIDs only: `<scope>/<store_id>/<media_id>`. Client file names are never
used, keys are validated against that exact shape and the resolved path must stay inside the
root, so traversal (`..`, absolute paths, symlinked escapes) is impossible. Writes go to a
temporary file in the same directory, are fsynced and atomically renamed, and files are created
with mode 0600. Keys and paths are never returned by the API.
"""

import os
import re
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.errors import ServiceUnavailable

SCOPES = ("manual", "qa")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
OBJECT_KEY = re.compile(rf"^(manual|qa)/{_UUID}/{_UUID}$")
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / ".media"


class InvalidObjectKey(ValueError):
    pass


class StorageUnavailable(ServiceUnavailable):
    """The filesystem refused a write (disk full, I/O error, read-only or unwritable volume).
    The cause is the original OSError; the partial file is already removed."""

    boundary = "media-storage"


def object_key(scope: str, store_id: str, media_id: str) -> str:
    key = f"{scope}/{store_id}/{media_id}"
    if scope not in SCOPES or not OBJECT_KEY.match(key):
        raise InvalidObjectKey("object key must be <scope>/<store uuid>/<media uuid>")
    return key


@dataclass(frozen=True)
class StoredFile:
    key: str
    size: int
    modified_at: float  # POSIX timestamp


class LocalMediaStorage:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        if not isinstance(key, str) or not OBJECT_KEY.match(key):
            raise InvalidObjectKey("invalid object key")
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise InvalidObjectKey("object key escapes the media root")
        return path

    def write(self, key: str, data: bytes) -> None:
        """Store `data` at `key` atomically (readers see the old file or the whole new one)."""
        path = self._path(key)  # InvalidObjectKey is a caller bug, not unavailability
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        except OSError as error:
            raise StorageUnavailable() from error
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except BaseException as error:
            Path(temporary).unlink(missing_ok=True)
            if isinstance(error, OSError):
                raise StorageUnavailable() from error
            raise

    def read(self, key: str) -> bytes:
        """The stored bytes; FileNotFoundError when they are gone."""
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> bool:
        """Remove the file; False when it was already gone (deleting is idempotent)."""
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            return False
        return True

    def iter_files(self, scope: str) -> Iterator[StoredFile]:
        """Every stored object of a scope (for the orphan sweep); temp files are skipped."""
        base = self.root / scope
        if not base.is_dir():
            return
        for store_dir in base.iterdir():
            if not store_dir.is_dir():
                continue
            for entry in store_dir.iterdir():
                key = f"{scope}/{store_dir.name}/{entry.name}"
                if OBJECT_KEY.match(key) and entry.is_file():
                    stat = entry.stat()
                    yield StoredFile(key, stat.st_size, stat.st_mtime)

    def remove_stale_temporaries(self, older_than_seconds: float = 3600) -> int:
        """Delete `.upload-*` leftovers of crashed writes."""
        removed = 0
        cutoff = time.time() - older_than_seconds
        for temporary in self.root.glob("*/*/.upload-*"):
            if temporary.is_file() and temporary.stat().st_mtime < cutoff:
                temporary.unlink(missing_ok=True)
                removed += 1
        return removed


_storage: LocalMediaStorage | None = None
_lock = threading.Lock()


def get_media_storage() -> LocalMediaStorage:
    global _storage
    with _lock:
        if _storage is None:
            _storage = LocalMediaStorage(os.getenv("MEDIA_ROOT", "").strip() or DEFAULT_ROOT)
        return _storage


def set_media_storage(storage: LocalMediaStorage | None) -> None:
    """Tests point storage at a temporary directory; None rebuilds from MEDIA_ROOT."""
    global _storage
    with _lock:
        _storage = storage
