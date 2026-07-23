"""Closed cover-evidence facts for report-only metadata observation.

The model deliberately records validation outcomes rather than making a cover
selection.  A future write workflow must consume a separate, explicit plan.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum


_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID = re.compile(r"^cover_[0-9a-f]{64}$")
_SOURCE_ID = re.compile(r"^(?:epub|hardcover)_[0-9a-f]{64}$")


class CoverEvidenceError(ValueError):
    """A cover evidence value is outside the closed report contract."""


class CoverSourceKind(StrEnum):
    EPUB = "epub"
    HARDCOVER = "hardcover"


class CoverSourceState(StrEnum):
    ABSENT = "absent"
    DISABLED = "disabled"
    DECLARED = "declared"
    RETRIEVED = "retrieved"
    INVALID_DECLARATION = "invalid_declaration"
    UNSUPPORTED_FORMAT = "unsupported_format"
    OVERSIZED = "oversized"
    DECODE_FAILED = "decode_failed"
    ANIMATED_OR_MULTIFRAME = "animated_or_multiframe"
    UNSAFE_REMOTE = "unsafe_remote"
    UNAVAILABLE = "unavailable"
    SOURCE_CHANGED = "source_changed"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class CoverArtifact:
    """One content-addressed original and its deterministic preview."""

    artifact_id: str
    original_digest: str
    preview_digest: str
    media_type: str
    width: int
    height: int

    def __post_init__(self) -> None:
        if (
            _ARTIFACT_ID.fullmatch(self.artifact_id) is None
            or _DIGEST.fullmatch(self.original_digest) is None
            or _DIGEST.fullmatch(self.preview_digest) is None
            or self.media_type not in {"image/jpeg", "image/png", "image/webp"}
            or type(self.width) is not int
            or type(self.height) is not int
            or not 1 <= self.width <= 8192
            or not 1 <= self.height <= 8192
            or self.width * self.height > 16_777_216
        ):
            raise CoverEvidenceError("cover artifact is invalid")
        expected = "cover_" + hashlib.sha256(
            f"{self.original_digest}:{self.preview_digest}".encode("ascii")
        ).hexdigest()
        if self.artifact_id != expected:
            raise CoverEvidenceError("cover artifact identity is inconsistent")

    @classmethod
    def create(
        cls, original: bytes, preview: bytes, media_type: str, width: int, height: int
    ) -> "CoverArtifact":
        if type(original) is not bytes or type(preview) is not bytes:
            raise CoverEvidenceError("cover artifact bytes are invalid")
        original_digest = hashlib.sha256(original).hexdigest()
        preview_digest = hashlib.sha256(preview).hexdigest()
        return cls(
            "cover_" + hashlib.sha256(
                f"{original_digest}:{preview_digest}".encode("ascii")
            ).hexdigest(),
            original_digest,
            preview_digest,
            media_type,
            width,
            height,
        )

    def to_data(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "media_type": self.media_type,
            "original_digest": self.original_digest,
            "preview_digest": self.preview_digest,
            "height": self.height,
            "width": self.width,
        }


@dataclass(frozen=True, slots=True)
class CoverSource:
    """Outcome for exactly one admitted source, without remote URL retention."""

    source_id: str
    kind: CoverSourceKind
    state: CoverSourceState
    artifact: CoverArtifact | None = None
    timeout_scope: str | None = None

    def __post_init__(self) -> None:
        if _SOURCE_ID.fullmatch(self.source_id) is None or type(self.kind) is not CoverSourceKind or type(self.state) is not CoverSourceState:
            raise CoverEvidenceError("cover source is invalid")
        if not self.source_id.startswith(f"{self.kind.value}_"):
            raise CoverEvidenceError("cover source identity is inconsistent")
        if (self.state is CoverSourceState.RETRIEVED) != (self.artifact is not None):
            raise CoverEvidenceError("cover source artifact is inconsistent")
        if self.timeout_scope is not None and self.timeout_scope not in {"attempted", "not_started_due_to_aggregate_deadline"}:
            raise CoverEvidenceError("cover timeout scope is invalid")
        if (self.state is CoverSourceState.TIMEOUT) != (self.timeout_scope is not None):
            raise CoverEvidenceError("cover timeout scope is inconsistent")

    def to_data(self) -> dict[str, object]:
        return {
            "artifact": None if self.artifact is None else self.artifact.to_data(),
            "kind": self.kind.value,
            "source_id": self.source_id,
            "state": self.state.value,
            "timeout_scope": self.timeout_scope,
        }


def cover_source_id(kind: CoverSourceKind, identity: str) -> str:
    if type(kind) is not CoverSourceKind or type(identity) is not str:
        raise CoverEvidenceError("cover source identity is invalid")
    return f"{kind.value}_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def publication_ready(sources: tuple[CoverSource, ...]) -> bool:
    """True only when every source has left the declaration intermediate state."""
    if type(sources) is not tuple or any(type(source) is not CoverSource for source in sources):
        raise CoverEvidenceError("cover sources are invalid")
    if len(sources) > 7 or len({source.source_id for source in sources}) != len(sources):
        raise CoverEvidenceError("cover sources are invalid")
    return all(source.state is not CoverSourceState.DECLARED for source in sources)


__all__ = [
    "CoverArtifact", "CoverEvidenceError", "CoverSource", "CoverSourceKind",
    "CoverSourceState", "cover_source_id", "publication_ready",
]
