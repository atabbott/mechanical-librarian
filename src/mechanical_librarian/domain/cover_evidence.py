"""Closed cover-evidence facts for report-only metadata observation.

The model deliberately records validation outcomes rather than making a cover
selection.  A future write workflow must consume a separate, explicit plan.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID = re.compile(r"^cover_[0-9a-f]{64}$")
_SOURCE_ID = re.compile(
    r"^(?:abs|audible_catalog|epub|hardcover|itunes_search|open_library)"
    r"_[0-9a-f]{64}$"
)
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
# A 64-bit difference hash, lower-case hex.  The width is the one ADR-0064
# fixes for the cover corroboration rule, and is validated here so a hash that
# rule could never read cannot enter the report in the first place.
_PERCEPTUAL_HASH = re.compile(r"^[0-9a-f]{16}$")
PREVIEW_VARIANT = "preview-320x480-rgba-png-v1"


class CoverEvidenceError(ValueError):
    """A cover evidence value is outside the closed report contract."""


class CoverSourceKind(StrEnum):
    ABS = "abs"
    EPUB = "epub"
    HARDCOVER = "hardcover"
    OPEN_LIBRARY = "open_library"
    # The two audiobook authorities that state a cover asset of their own.
    # Audnexus and AudioSilo restate Audible's CDN asset, so they carry no
    # independent signal and emit no cover source at all.
    AUDIBLE_CATALOG = "audible_catalog"
    ITUNES_SEARCH = "itunes_search"


LOCAL_COVER_SOURCE_KINDS = frozenset({CoverSourceKind.ABS, CoverSourceKind.EPUB})
AUDIOBOOK_COVER_SOURCE_KINDS = frozenset(
    {CoverSourceKind.AUDIBLE_CATALOG, CoverSourceKind.ITUNES_SEARCH}
)


class CoverSourceState(StrEnum):
    ABSENT = "absent"
    REJECTED = "rejected"
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
    TRUNCATED = "truncated"


class CoverDesignation(StrEnum):
    EXACT_EDITION = "exact_edition"
    RELATED_EDITION = "related_edition"
    WORK = "work"
    DEFAULT = "default"
    TRUNCATION = "truncation"


class CoverContextField(StrEnum):
    ISBN = "isbn"
    LANGUAGE = "language"
    COUNTRY = "country"
    PUBLICATION_PLACE = "publication_place"
    PUBLISHER = "publisher"
    PUBLICATION_DATE = "publication_date"
    FORMAT = "format"


class CoverTruncationReason(StrEnum):
    PROVIDER_LIMIT = "provider_limit"
    GALLERY_LIMIT = "gallery_limit"


@dataclass(frozen=True, slots=True)
class CoverContext:
    field: CoverContextField
    value: str

    def __post_init__(self) -> None:
        if type(self.field) is not CoverContextField:
            raise CoverEvidenceError("cover context is invalid")
        try:
            validate_projection_text(self.value, "cover context", 280)
        except EvidenceValueError as error:
            raise CoverEvidenceError("cover context is invalid") from error

    def to_data(self) -> dict[str, str]:
        return {"field": self.field.value, "value": self.value}


@dataclass(frozen=True, slots=True)
class CoverProvenance:
    provider: str
    designation: CoverDesignation
    work_identity: str | None = None
    edition_identity: str | None = None
    context: tuple[CoverContext, ...] = ()
    truncation_reason: CoverTruncationReason | None = None

    def __post_init__(self) -> None:
        if (
            _IDENTITY.fullmatch(self.provider) is None
            or type(self.designation) is not CoverDesignation
            or type(self.context) is not tuple
            or len(self.context) > 16
            or any(type(value) is not CoverContext for value in self.context)
            or self.context
            != tuple(
                sorted(
                    set(self.context),
                    key=lambda value: (value.field.value, value.value),
                )
            )
        ):
            raise CoverEvidenceError("cover provenance is invalid")
        for identity in (self.work_identity, self.edition_identity):
            if identity is not None and _IDENTITY.fullmatch(identity) is None:
                raise CoverEvidenceError("cover provenance is invalid")
        if self.designation is CoverDesignation.TRUNCATION:
            if (
                self.work_identity is not None
                or self.edition_identity is not None
                or self.context
                or type(self.truncation_reason) is not CoverTruncationReason
            ):
                raise CoverEvidenceError("cover truncation provenance is invalid")
        elif (
            self.work_identity is None
            or (
                self.designation
                in {CoverDesignation.EXACT_EDITION, CoverDesignation.RELATED_EDITION}
                and self.edition_identity is None
            )
            or self.truncation_reason is not None
        ):
            raise CoverEvidenceError("cover provenance is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "context": [value.to_data() for value in self.context],
            "designation": self.designation.value,
            "edition_identity": self.edition_identity,
            "provider": self.provider,
            "truncation_reason": (
                None if self.truncation_reason is None else self.truncation_reason.value
            ),
            "work_identity": self.work_identity,
        }


@dataclass(frozen=True, slots=True)
class CoverArtifact:
    """One content-addressed original and its deterministic preview."""

    artifact_id: str
    original_digest: str
    preview_digest: str
    preview_variant: str
    media_type: str
    width: int
    height: int
    # The 64-bit difference hash of the decoded image, computed in the isolated
    # decode child beside the preview.  It is ``None`` for every artifact
    # decoded before this field existed and for any child that could not state
    # one; a reader treats absence as "no hash", never as a hash of zero.
    perceptual_hash: str | None = None

    def __post_init__(self) -> None:
        if self.perceptual_hash is not None and (
            type(self.perceptual_hash) is not str
            or _PERCEPTUAL_HASH.fullmatch(self.perceptual_hash) is None
        ):
            raise CoverEvidenceError("cover artifact is invalid")
        if (
            _ARTIFACT_ID.fullmatch(self.artifact_id) is None
            or _DIGEST.fullmatch(self.original_digest) is None
            or _DIGEST.fullmatch(self.preview_digest) is None
            or self.preview_variant != PREVIEW_VARIANT
            or self.media_type not in {"image/jpeg", "image/png", "image/webp"}
            or type(self.width) is not int
            or type(self.height) is not int
            or not 1 <= self.width <= 8192
            or not 1 <= self.height <= 8192
            or self.width * self.height > 16_777_216
        ):
            raise CoverEvidenceError("cover artifact is invalid")
        expected = "cover_" + self.original_digest
        if self.artifact_id != expected:
            raise CoverEvidenceError("cover artifact identity is inconsistent")

    @classmethod
    def create(
        cls,
        original: bytes,
        preview: bytes,
        media_type: str,
        width: int,
        height: int,
        perceptual_hash: str | None = None,
    ) -> "CoverArtifact":
        if type(original) is not bytes or type(preview) is not bytes:
            raise CoverEvidenceError("cover artifact bytes are invalid")
        original_digest = hashlib.sha256(original).hexdigest()
        preview_digest = hashlib.sha256(preview).hexdigest()
        return cls(
            "cover_" + original_digest,
            original_digest,
            preview_digest,
            PREVIEW_VARIANT,
            media_type,
            width,
            height,
            perceptual_hash,
        )

    def to_data(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "media_type": self.media_type,
            "original_digest": self.original_digest,
            "perceptual_hash": self.perceptual_hash,
            "preview_digest": self.preview_digest,
            "preview_variant": self.preview_variant,
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
    provenance: CoverProvenance | None = None

    def __post_init__(self) -> None:
        if (
            _SOURCE_ID.fullmatch(self.source_id) is None
            or type(self.kind) is not CoverSourceKind
            or type(self.state) is not CoverSourceState
        ):
            raise CoverEvidenceError("cover source is invalid")
        if not self.source_id.startswith(f"{self.kind.value}_"):
            raise CoverEvidenceError("cover source identity is inconsistent")
        if (self.state is CoverSourceState.RETRIEVED) != (self.artifact is not None):
            raise CoverEvidenceError("cover source artifact is inconsistent")
        if self.timeout_scope is not None and self.timeout_scope not in {
            "attempted",
            "not_started_due_to_aggregate_deadline",
        }:
            raise CoverEvidenceError("cover timeout scope is invalid")
        if (self.state is CoverSourceState.TIMEOUT) != (self.timeout_scope is not None):
            raise CoverEvidenceError("cover timeout scope is inconsistent")
        if (self.state is CoverSourceState.TRUNCATED) != (
            self.provenance is not None
            and self.provenance.designation is CoverDesignation.TRUNCATION
        ):
            raise CoverEvidenceError("cover truncation is inconsistent")
        if self.kind in LOCAL_COVER_SOURCE_KINDS:
            if self.provenance is not None:
                raise CoverEvidenceError("local cover provenance is invalid")
        elif self.provenance is not None and self.provenance.provider == "":
            raise CoverEvidenceError("provider cover provenance is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "artifact": None if self.artifact is None else self.artifact.to_data(),
            "kind": self.kind.value,
            "source_id": self.source_id,
            "state": self.state.value,
            "timeout_scope": self.timeout_scope,
            "provenance": (
                None if self.provenance is None else self.provenance.to_data()
            ),
        }


def cover_source_id(kind: CoverSourceKind, identity: str) -> str:
    if type(kind) is not CoverSourceKind or type(identity) is not str:
        raise CoverEvidenceError("cover source identity is invalid")
    return f"{kind.value}_{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def publication_ready(sources: tuple[CoverSource, ...]) -> bool:
    """Validate the closed set of published terminal source outcomes."""
    if type(sources) is not tuple or any(
        type(source) is not CoverSource for source in sources
    ):
        raise CoverEvidenceError("cover sources are invalid")
    # Two local rows, eight admitted provider rows, and at most two explicit
    # truncation rows for each of the configuration schema's 32 providers.
    if len(sources) > 74 or len({source.source_id for source in sources}) != len(
        sources
    ):
        raise CoverEvidenceError("cover sources are invalid")
    local = [source for source in sources if source.kind in LOCAL_COVER_SOURCE_KINDS]
    # Every kind that is not one of the two local ones is a provider row, so an
    # admitted cover-bearing provider enters this arithmetic by joining the
    # enum rather than by being named again here.
    provider = [
        source for source in sources if source.kind not in LOCAL_COVER_SOURCE_KINDS
    ]
    admitted_provider = [
        source for source in provider if source.state is not CoverSourceState.TRUNCATED
    ]
    if (
        len(local) > 2
        or any(source.provenance is None for source in provider)
        or len(admitted_provider) > 8
        or len(
            {
                source.artifact.original_digest
                for source in provider
                if source.artifact is not None
            }
        )
        > 8
    ):
        raise CoverEvidenceError("cover sources are invalid")
    providers = {
        source.provenance.provider
        for source in provider
        if source.provenance is not None
    }
    for name in providers:
        admitted = [
            source
            for source in provider
            if source.provenance is not None
            and source.provenance.provider == name
            and source.state is not CoverSourceState.TRUNCATED
        ]
        truncations = [
            source
            for source in provider
            if source.provenance is not None
            and source.provenance.provider == name
            and source.state is CoverSourceState.TRUNCATED
        ]
        if len(admitted) > 4 or len(truncations) > 2:
            raise CoverEvidenceError("cover sources are invalid")
    return True


__all__ = [
    "AUDIOBOOK_COVER_SOURCE_KINDS",
    "LOCAL_COVER_SOURCE_KINDS",
    "CoverArtifact",
    "CoverContext",
    "CoverContextField",
    "CoverDesignation",
    "CoverEvidenceError",
    "CoverProvenance",
    "CoverSource",
    "CoverSourceKind",
    "CoverSourceState",
    "CoverTruncationReason",
    "PREVIEW_VARIANT",
    "cover_source_id",
    "publication_ready",
]
