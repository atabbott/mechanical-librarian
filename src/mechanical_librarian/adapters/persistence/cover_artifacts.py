"""Private, content-addressed storage for validated cover artifacts."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.cover_evidence import CoverArtifact


class CoverArtifactStore:
    """Stores originals and previews by digest; it never resolves arbitrary paths."""

    def __init__(self, path: Path, *, create: bool = True) -> None:
        self._path = path
        if create:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            information = path.stat()
        except OSError as error:
            raise MetadataReportError("artifact store is unavailable") from error
        if not stat.S_ISDIR(information.st_mode) or information.st_uid != os.getuid() or information.st_mode & 0o077:
            raise MetadataReportError("artifact store is unavailable")

    def stage(self, artifact: CoverArtifact, original: bytes, preview: bytes) -> None:
        """Create or verify both immutable blobs before database publication."""
        if type(artifact) is not CoverArtifact or type(original) is not bytes or type(preview) is not bytes:
            raise MetadataReportError("artifact is invalid")
        self._put(artifact.original_digest, original)
        self._put(artifact.preview_digest, preview)

    def preview(self, artifact: CoverArtifact) -> bytes:
        if type(artifact) is not CoverArtifact:
            raise MetadataReportError("artifact is invalid")
        return self._read(artifact.preview_digest, 1_048_576)

    def read_preview_digest(self, digest: str) -> bytes:
        """Read a preview only after a repository has authorized its digest."""
        return self._read(digest, 1_048_576)

    def remove_unreferenced(self, digests: tuple[str, ...]) -> None:
        """Best-effort recovery cleanup of artifacts not yet bound to a report."""
        for digest in digests:
            if type(digest) is not str or len(digest) != 64:
                continue
            try:
                (self._path / digest).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _put(self, digest: str, content: bytes) -> None:
        if hashlib.sha256(content).hexdigest() != digest:
            raise MetadataReportError("artifact digest is invalid")
        directory = self._directory()
        try:
            descriptor = os.open(digest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        except FileExistsError:
            os.close(directory)
            if self._read(digest, max(len(content), 1_048_576)) != content:
                raise MetadataReportError("artifact content disagrees")
            return
        except OSError as error:
            os.close(directory)
            raise MetadataReportError("artifact store is unavailable") from error
        try:
            written = os.write(descriptor, content)
            if written != len(content):
                raise OSError("short artifact write")
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
                content = os.read(descriptor, maximum + 1)
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
        try:
            return os.open(self._path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as error:
            raise MetadataReportError("artifact store is unavailable") from error


__all__ = ["CoverArtifactStore"]
