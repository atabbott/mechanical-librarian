"""Owner-private originals of automatically replaced covers (#1363, ADR-0093).

Each file is the exact original an automatic switch replaced, named by its
SHA-256, so the owner can put it back. The directory is owner-only and bounded
by entries and total bytes; at the bound nothing older is dropped and the new
original is refused, so no way back is ever lost silently. The bytes are never
exported, logged, offered as a download or sent to a reviewer.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from mechanical_librarian.adapters.private_recovery import require_private_directory

MAXIMUM_RETAINED_ENTRIES = 2000
MAXIMUM_RETAINED_BYTES = 2 * 1024 * 1024 * 1024
# The largest original the decoder and the recovery copy admit.
MAXIMUM_ORIGINAL_BYTES = 8 * 1024 * 1024
_ERROR = "retained cover unavailable"


class RetainedCoverFull(ValueError):
    """Keeping one more original would pass the entry or byte bound."""


class RetainedCoverStore:
    def __init__(
        self,
        root: Path,
        maximum_entries: int = MAXIMUM_RETAINED_ENTRIES,
        maximum_bytes: int = MAXIMUM_RETAINED_BYTES,
    ) -> None:
        if (
            type(maximum_entries) is not int
            or type(maximum_bytes) is not int
            or not 1 <= maximum_entries <= MAXIMUM_RETAINED_ENTRIES
            or not 1 <= maximum_bytes <= MAXIMUM_RETAINED_BYTES
        ):
            raise ValueError("retained cover configuration")
        self._root = root
        self._maximum_entries = maximum_entries
        self._maximum_bytes = maximum_bytes
        require_private_directory(root, _ERROR)

    def keep(self, digest: str, original: bytes) -> None:
        """Durably keep one original under its digest; keeping it again is a no-op."""
        path = self._path(digest)
        if (
            type(original) is not bytes
            or not 1 <= len(original) <= MAXIMUM_ORIGINAL_BYTES
            or hashlib.sha256(original).hexdigest() != digest
        ):
            raise ValueError(_ERROR)
        require_private_directory(self._root, _ERROR)
        temporary = path.with_suffix(".retained.tmp")
        self._remove_file(temporary)
        try:
            information = path.lstat()
        except FileNotFoundError:
            information = None
        if information is not None:
            self._require_private_file(information)
            if information.st_size != len(original):
                raise ValueError(_ERROR)
            return
        entries = 0
        total = 0
        for existing in self._root.glob("*.retained"):
            existing_information = existing.lstat()
            self._require_private_file(existing_information)
            entries += 1
            total += existing_information.st_size
        if (
            entries + 1 > self._maximum_entries
            or total + len(original) > self._maximum_bytes
        ):
            raise RetainedCoverFull("retained covers are full")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            view = memoryview(original)
            while view:
                count = os.write(descriptor, view)
                if count <= 0:
                    raise OSError(_ERROR)
                view = view[count:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        self._sync_root()

    def remove(self, digest: str) -> None:
        """Remove one original and any interrupted copy; absence is success."""
        path = self._path(digest)
        require_private_directory(self._root, _ERROR)
        removed = self._remove_file(path, sync=False)
        removed = self._remove_file(path.with_suffix(".retained.tmp"), sync=False) or (
            removed
        )
        if removed:
            self._sync_root()

    def _path(self, digest: str) -> Path:
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(value not in "0123456789abcdef" for value in digest)
        ):
            raise ValueError("invalid retained cover digest")
        return self._root / f"{digest}.retained"

    def _remove_file(self, path: Path, *, sync: bool = True) -> bool:
        try:
            information = path.lstat()
        except FileNotFoundError:
            return False
        self._require_private_file(information)
        path.unlink()
        if sync:
            self._sync_root()
        return True

    def _require_private_file(self, information: os.stat_result) -> None:
        if (
            not stat.S_ISREG(information.st_mode)
            or stat.S_IMODE(information.st_mode) != 0o600
            or information.st_uid != os.geteuid()
            or information.st_size > MAXIMUM_ORIGINAL_BYTES
        ):
            raise ValueError(_ERROR)

    def _sync_root(self) -> None:
        descriptor = os.open(
            self._root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


__all__ = [
    "MAXIMUM_RETAINED_BYTES",
    "MAXIMUM_RETAINED_ENTRIES",
    "RetainedCoverFull",
    "RetainedCoverStore",
]
