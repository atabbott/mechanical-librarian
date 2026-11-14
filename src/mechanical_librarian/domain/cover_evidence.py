"""Closed cover-evidence facts for report-only metadata observation.

The model deliberately records validation outcomes rather than making a cover
selection.  A future write workflow must consume a separate, explicit plan.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum

from mechanical_librarian.domain.cover_rules import CoverMeasures
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_ID = re.compile(r"^cover_[0-9a-f]{64}$")
_SOURCE_ID = re.compile(
    r"^(?:abs|audible_catalog|epub|hardcover|inventaire|itunes_search|open_library|retained)"
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
    INVENTAIRE = "inventaire"
    # The two audiobook authorities that state a cover asset of their own.
    # Audnexus and AudioSilo restate Audible's CDN asset, so they carry no
    # independent signal and emit no cover source at all.
    AUDIBLE_CATALOG = "audible_catalog"
    ITUNES_SEARCH = "itunes_search"
    # The original an automatic switch replaced, read back from the private
    # retained store while its switch is listed (#1363, ADR-0093). It is the
    # owner's alternative only: never a provider's cover, never sent to a
    # reviewer, never elected, never chosen automatically.
    RETAINED = "retained"


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


class CoverFinalHost(StrEnum):
    """The closed host family an Open Library cover was finally served from.

    ``us.archive.org`` stands for any ``ia<N>.us.archive.org`` datanode: the
    datanode number varies per request, so the raw host would make identical
    evidence differ between runs, and a report never keeps a remote URL.
    """

    COVERS_OPEN_LIBRARY = "covers.openlibrary.org"
    ARCHIVE = "archive.org"
    US_ARCHIVE = "us.archive.org"


# The states in which the constrained decoder refused bytes it was given. Only
# an Audiobookshelf source in one of them records the bytes' digest, so a cover
# write can bind exactly what it replaces (#1362).
DECODER_REJECTED_STATES = frozenset(
    {
        CoverSourceState.DECODE_FAILED,
        CoverSourceState.ANIMATED_OR_MULTIFRAME,
        CoverSourceState.OVERSIZED,
        CoverSourceState.UNSUPPORTED_FORMAT,
    }
)


class CoverDesignation(StrEnum):
    EXACT_EDITION = "exact_edition"
    RELATED_EDITION = "related_edition"
    WORK = "work"
    DEFAULT = "default"
    # A cover read from an accepted book's edition list (#1380): it names the
    # edition, yet it binds by the book's work identity alone.
    LISTED_EDITION = "listed_edition"
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
    # Hardcover's edition-cover fan-out (#1380): more books or editions than
    # its bounds read, a fan-out a failed or malformed page ended, and a
    # decoded edition cover that showed the same art as a larger one.
    EDITION_LIMIT = "edition_limit"
    EDITION_FETCH_INCOMPLETE = "edition_fetch_incomplete"
    SAME_ART = "same_art"


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
                in {
                    CoverDesignation.EXACT_EDITION,
                    CoverDesignation.RELATED_EDITION,
                    CoverDesignation.LISTED_EDITION,
                }
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
    # The same-art measures of the decoded 320x480 preview (ADR-0093): pHash,
    # dHash, colour histogram and layout, computed in the same child from the
    # same pixels, never from a stated size. ``None`` means "not measured".
    measures: CoverMeasures | None = None

    def __post_init__(self) -> None:
        if self.measures is not None and type(self.measures) is not CoverMeasures:
            raise CoverEvidenceError("cover artifact is invalid")
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
        measures: CoverMeasures | None = None,
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
            measures,
        )

    def to_data(self) -> dict[str, object]:
        return {
            "artifact_id": self.artifact_id,
            "measures": None if self.measures is None else self.measures.to_data(),
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
    final_host: CoverFinalHost | None = None
    # The SHA-256 of the exact bytes Audiobookshelf served, or the retained
    # store holds, when the decoder refused them. A retrieved source's digest
    # is its artifact's; a transient or reader-refused source read no bytes
    # worth binding and records none.
    original_digest: str | None = None

    def __post_init__(self) -> None:
        if self.original_digest is not None and (
            type(self.original_digest) is not str
            or _DIGEST.fullmatch(self.original_digest) is None
            or self.kind not in {CoverSourceKind.ABS, CoverSourceKind.RETAINED}
            or self.state not in DECODER_REJECTED_STATES
        ):
            raise CoverEvidenceError("cover source digest is inconsistent")
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
        if (
            self.final_host is not None and type(self.final_host) is not CoverFinalHost
        ) or (self.final_host is not None) != (
            self.kind is CoverSourceKind.OPEN_LIBRARY
            and self.state is CoverSourceState.RETRIEVED
        ):
            raise CoverEvidenceError("cover final host is inconsistent")
        if self.kind is CoverSourceKind.RETAINED:
            # A retained original is either decoded, or kept byte for byte
            # after the decoder refused it and bound by its digest (#1362).
            if self.provenance is not None or (
                self.state is not CoverSourceState.RETRIEVED
                and self.original_digest is None
            ):
                raise CoverEvidenceError("retained cover source is invalid")
        elif self.kind in LOCAL_COVER_SOURCE_KINDS:
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
            "final_host": None if self.final_host is None else self.final_host.value,
            "original_digest": self.original_digest,
        }

    @property
    def bytes_digest(self) -> str | None:
        """The SHA-256 of the bytes this source read, decoded or refused."""
        return (
            self.artifact.original_digest
            if self.artifact is not None
            else self.original_digest
        )


class EpubCoverFallback(StrEnum):
    """A bounded cover fallback, in the fixed order the inspector tries them."""

    EPUB2_META_COVER_ITEM = "epub2_meta_cover_item"
    GUIDE_COVER_REFERENCE = "guide_cover_reference"
    COVER_IMAGE_WRAPPER = "cover_image_wrapper"


class EpubCoverRecoveryRefusal(StrEnum):
    """The closed, value-free reason a cover recovery chose no image."""

    UNRESOLVED = "cover_recovery_unresolved"
    UNSUPPORTED_TARGET = "cover_recovery_unsupported_target"
    MULTIPLE_WRAPPERS = "cover_recovery_multiple_wrappers"
    WRAPPER_UNREADABLE = "cover_wrapper_unreadable"
    WRAPPER_RESOURCE_LIMIT = "cover_wrapper_resource_limit"
    WRAPPER_TIMEOUT = "cover_wrapper_timeout"
    WRAPPER_EXTERNAL_REFERENCE = "cover_wrapper_external_reference"
    WRAPPER_OUTSIDE_PACKAGE = "cover_wrapper_outside_package"
    WRAPPER_NESTED = "cover_wrapper_nested"
    WRAPPER_NOT_MANIFEST_IMAGE = "cover_wrapper_not_manifest_image"
    WRAPPER_NO_IMAGE = "cover_wrapper_no_image"
    WRAPPER_MULTIPLE_IMAGES = "cover_wrapper_multiple_images"
    CONTRADICTORY_DECLARATION = "contradictory_cover_declaration"


_FALLBACK_ORDER = tuple(EpubCoverFallback)


@dataclass(frozen=True, slots=True)
class EpubCoverRecovery:
    """Shadow evidence of an EPUB cover recovered from a malformed declaration.

    It names only the fallbacks that resolved and, when no image was agreed,
    one closed refusal. It is never a cover source, a candidate or a
    verification of the current cover: activation is a separate decision.
    """

    fallbacks: tuple[EpubCoverFallback, ...]
    refusal: EpubCoverRecoveryRefusal | None = None

    def __post_init__(self) -> None:
        if (
            type(self.fallbacks) is not tuple
            or any(type(value) is not EpubCoverFallback for value in self.fallbacks)
            or self.fallbacks
            != tuple(value for value in _FALLBACK_ORDER if value in self.fallbacks)
            or (
                self.refusal is not None
                and type(self.refusal) is not EpubCoverRecoveryRefusal
            )
            or (self.refusal is EpubCoverRecoveryRefusal.UNRESOLVED)
            != (not self.fallbacks)
        ):
            raise CoverEvidenceError("EPUB cover recovery is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "authority": "shadow",
            "fallbacks": [value.value for value in self.fallbacks],
            "refusal": None if self.refusal is None else self.refusal.value,
        }

    @classmethod
    def from_data(cls, value: object) -> "EpubCoverRecovery":
        if (
            type(value) is not dict
            or set(value) != {"authority", "fallbacks", "refusal"}
            or value["authority"] != "shadow"
            or type(value["fallbacks"]) is not list
            or any(type(item) is not str for item in value["fallbacks"])
            or (value["refusal"] is not None and type(value["refusal"]) is not str)
        ):
            raise CoverEvidenceError("EPUB cover recovery is invalid")
        try:
            return cls(
                tuple(EpubCoverFallback(item) for item in value["fallbacks"]),
                None
                if value["refusal"] is None
                else EpubCoverRecoveryRefusal(value["refusal"]),
            )
        except ValueError as error:
            if isinstance(error, CoverEvidenceError):
                raise
            raise CoverEvidenceError("EPUB cover recovery is invalid") from None


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
    # Two local rows, one retained original, twelve admitted provider rows, and
    # at most five explicit truncation rows for each of the configuration
    # schema's 32 providers.
    if len(sources) > 2 + 1 + 12 + 5 * 32 or len(
        {source.source_id for source in sources}
    ) != len(sources):
        raise CoverEvidenceError("cover sources are invalid")
    local = [source for source in sources if source.kind in LOCAL_COVER_SOURCE_KINDS]
    retained = [source for source in sources if source.kind is CoverSourceKind.RETAINED]
    # Every kind that is neither local nor retained is a provider row, so an
    # admitted cover-bearing provider enters this arithmetic by joining the
    # enum rather than by being named again here.
    provider = [
        source
        for source in sources
        if source.kind not in LOCAL_COVER_SOURCE_KINDS
        and source.kind is not CoverSourceKind.RETAINED
    ]
    admitted_provider = [
        source for source in provider if source.state is not CoverSourceState.TRUNCATED
    ]
    if (
        len(local) > 2
        or len(retained) > 1
        or any(source.provenance is None for source in provider)
        or len(admitted_provider) > 12
        or len(
            {
                source.artifact.original_digest
                for source in provider
                if source.artifact is not None
            }
        )
        > 12
    ):
        raise CoverEvidenceError("cover sources are invalid")
    providers = {
        source.provenance.provider
        for source in provider
        if source.provenance is not None
    }
    for name in providers:
        rows = [
            source
            for source in provider
            if source.provenance is not None and source.provenance.provider == name
        ]
        admitted = [
            source for source in rows if source.state is not CoverSourceState.TRUNCATED
        ]
        # A Hardcover connection may hold eight covers: its book's edition
        # list is the only source that offers that many (#1380).
        limit = (
            8 if all(source.kind is CoverSourceKind.HARDCOVER for source in rows) else 4
        )
        if len(admitted) > limit or len(rows) - len(admitted) > 5:
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
    "CoverFinalHost",
    "CoverProvenance",
    "CoverSource",
    "CoverSourceKind",
    "CoverSourceState",
    "CoverTruncationReason",
    "DECODER_REJECTED_STATES",
    "EpubCoverFallback",
    "EpubCoverRecovery",
    "EpubCoverRecoveryRefusal",
    "PREVIEW_VARIANT",
    "cover_source_id",
    "publication_ready",
]
