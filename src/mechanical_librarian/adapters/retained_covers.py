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
from typing import Protocol

from mechanical_librarian.adapters.private_recovery import require_private_directory
from mechanical_librarian.application.metadata_report import MetadataReportError

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
            if information.st_size == len(original) and self._digest(path) == digest:
                return
            # A file under this name that is not these bytes is never trusted:
            # it is replaced atomically below, taking no new place in the bound.
        entries = 0
        total = 0
        for existing in self._root.glob("*.retained"):
            if existing == path:
                continue
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

    def read(self, digest: str) -> bytes:
        """Return one kept original, verified against its digest.

        Only the guarded writer that puts it back and the report's decoder
        read it; a missing, altered or non-private file is refused, never
        repaired here.
        """
        path = self._path(digest)
        require_private_directory(self._root, _ERROR)
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as error:
            raise ValueError(_ERROR) from error
        try:
            self._require_private_file(os.fstat(descriptor))
            chunks: list[bytes] = []
            size = 0
            while chunk := os.read(descriptor, 1024 * 1024):
                size += len(chunk)
                if size > MAXIMUM_ORIGINAL_BYTES:
                    raise ValueError(_ERROR)
                chunks.append(chunk)
        finally:
            os.close(descriptor)
        original = b"".join(chunks)
        if not original or hashlib.sha256(original).hexdigest() != digest:
            raise ValueError(_ERROR)
        return original

    def require(self, digest: str) -> None:
        """Refuse unless the kept original is present, private and intact."""
        self.read(digest)

    def _digest(self, path: Path) -> str:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            hasher = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                hasher.update(chunk)
            return hasher.hexdigest()
        finally:
            os.close(descriptor)

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


class _OriginalReader(Protocol):
    def read_original_digest(self, digest: str) -> bytes: ...


class ContentAddressedOriginals:
    """Read an original by digest from the artifact store, else the retained one.

    Both stores verify SHA-256, so either answer is the same original. The
    guarded cover writer uses it, so putting a retained original back needs
    no copy into the artifact store (#1363, D4).
    """

    def __init__(self, artifacts: _OriginalReader, retained_root: Path) -> None:
        self._artifacts = artifacts
        self._retained_root = retained_root

    def read_original_digest(self, digest: str) -> bytes:
        try:
            return self._artifacts.read_original_digest(digest)
        except MetadataReportError:
            try:
                return RetainedCoverStore(self._retained_root).read(digest)
            except ValueError as error:
                raise MetadataReportError("artifact is unavailable") from error


__all__ = [
    "MAXIMUM_RETAINED_BYTES",
    "MAXIMUM_RETAINED_ENTRIES",
    "ContentAddressedOriginals",
    "RetainedCoverFull",
    "RetainedCoverStore",
]
