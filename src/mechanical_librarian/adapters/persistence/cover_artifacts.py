"""Private, content-addressed storage for validated cover artifacts."""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path

from mechanical_librarian.application.metadata_report import (
    ArtifactAuthorityError,
    MetadataReportError,
)
from mechanical_librarian.domain.cover_evidence import CoverArtifact

_MAX_ORIGINAL_BYTES = 8 * 1024 * 1024


class CoverArtifactStore:
    """Stores originals and previews by digest; it never resolves arbitrary paths."""

    def __init__(self, path: Path, *, create: bool = True, expected_identity: tuple[int, int] | None = None) -> None:
        self._path = path
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = self._directory()
            information = os.fstat(descriptor)
            if expected_identity is not None and (information.st_dev, information.st_ino) != expected_identity:
                raise ArtifactAuthorityError("artifact store authority changed")
            self.identity = (information.st_dev, information.st_ino)
            os.close(descriptor)
        except OSError as error:
            raise MetadataReportError("artifact store is unavailable") from error

    def stage(self, artifact: CoverArtifact, original: bytes, preview: bytes) -> None:
        """Create or verify both immutable blobs before database publication."""
        if type(artifact) is not CoverArtifact or type(original) is not bytes or type(preview) is not bytes:
            raise MetadataReportError("artifact is invalid")
        self._put(artifact.original_digest, original)
        self._put(artifact.preview_digest, preview)

    def require_current(self) -> None:
        descriptor = self._directory()
        os.close(descriptor)

    def preview(self, artifact: CoverArtifact) -> bytes:
        if type(artifact) is not CoverArtifact:
            raise MetadataReportError("artifact is invalid")
        return self._read(artifact.preview_digest, 1_048_576)

    def require_artifact(self, artifact: CoverArtifact) -> None:
        """Verify both immutable blobs without exposing the original."""
        if type(artifact) is not CoverArtifact:
            raise MetadataReportError("artifact is invalid")
        self._read(artifact.original_digest, _MAX_ORIGINAL_BYTES)
        self._read(artifact.preview_digest, 1_048_576)

    def read_preview_digest(self, digest: str) -> bytes:
        """Read a preview only after a repository has authorized its digest."""
        return self._read(digest, 1_048_576)

    def read_original_digest(self, digest: str) -> bytes:
        """Return one verified original for the guarded Audiobookshelf cover writer.

        Callers must already hold the immutable plan whose ``cover_`` identity
        binds this digest.  This intentionally exposes no paths or enumeration
        capability and never persists the returned bytes.
        """
        return self._read(digest, _MAX_ORIGINAL_BYTES)

    def recover_unreferenced(self, is_referenced: Callable[[str], bool]) -> None:
        """Remove candidate crash orphans after one targeted reference check."""
        if not callable(is_referenced):
            raise MetadataReportError("artifact references are invalid")
        try:
            directory = self._directory()
            try:
                for name in os.listdir(directory):
                    if (
                        len(name) != 64
                        or any(character not in "0123456789abcdef" for character in name)
                    ):
                        continue
                    try:
                        information = os.stat(name, dir_fd=directory, follow_symlinks=False)
                        if stat.S_ISREG(information.st_mode) and not is_referenced(name):
                            os.unlink(name, dir_fd=directory)
                    except FileNotFoundError:
                        pass
                    except OSError as error:
                        raise MetadataReportError("artifact store is unavailable") from error
                os.fsync(directory)
            finally:
                os.close(directory)
        except MetadataReportError:
            raise
        except OSError as error:
            raise MetadataReportError("artifact store is unavailable") from error

    def _put(self, digest: str, content: bytes) -> None:
        if hashlib.sha256(content).hexdigest() != digest:
            raise MetadataReportError("artifact digest is invalid")
        directory = self._directory()
        try:
            descriptor = os.open(digest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        except FileExistsError as error:
            os.close(directory)
            if self._read(digest, max(len(content), 1_048_576)) != content:
                raise MetadataReportError("artifact content disagrees") from error
            return
        except OSError as error:
            os.close(directory)
            raise MetadataReportError("artifact store is unavailable") from error
        try:
            written = 0
            while written < len(content):
                amount = os.write(descriptor, content[written:])
                if amount <= 0:
                    raise OSError("short artifact write")
                written += amount
            os.fsync(descriptor)
            os.fsync(directory)
        except OSError as error:
            try:
                os.unlink(digest, dir_fd=directory)
            except OSError:
                pass
            raise MetadataReportError("artifact store is unavailable") from error
        finally:
            os.close(descriptor)
            os.close(directory)

    def _read(self, digest: str, maximum: int) -> bytes:
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise MetadataReportError("artifact is invalid")
        try:
            directory = self._directory()
            descriptor = os.open(digest, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
            try:
                information = os.fstat(descriptor)
                if not stat.S_ISREG(information.st_mode) or information.st_size > maximum:
                    raise MetadataReportError("artifact is unavailable")
                chunks: list[bytes] = []
                remaining = maximum + 1
                while remaining:
                    chunk = os.read(descriptor, min(65_536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                content = b"".join(chunks)
            finally:
                os.close(descriptor)
                os.close(directory)
        except MetadataReportError:
            raise
        except OSError as error:
            raise MetadataReportError("artifact is unavailable") from error
        if len(content) > maximum or hashlib.sha256(content).hexdigest() != digest:
            raise MetadataReportError("artifact is unavailable")
        return content

    def _directory(self) -> int:
        descriptors: list[int] = []
        try:
            absolute = (
                self._path.resolve(strict=True)
                if sys.platform == "darwin"
                else self._path.absolute()
            )
            descriptor = os.open(
                "/", os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptors.append(descriptor)
            for name in absolute.parts[1:]:
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptors[-1],
                )
                descriptors.append(descriptor)
            result = os.dup(descriptors[-1])
            information = os.fstat(result)
            if hasattr(self, "identity") and (information.st_dev, information.st_ino) != self.identity:
                os.close(result)
                raise ArtifactAuthorityError("artifact store authority changed")
            if information.st_uid != os.getuid() or information.st_mode & 0o077:
                os.close(result)
                raise MetadataReportError("artifact store is unavailable")
            return result
        except OSError as error:
            raise MetadataReportError("artifact store is unavailable") from error
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


__all__ = ["CoverArtifactStore"]
