"""Closed, neutral metadata-evidence report for one selected EPUB."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Self

from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
    validate_role_projection,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact, CoverContext, CoverContextField, CoverDesignation,
    CoverEvidenceError, CoverProvenance, CoverSource, CoverSourceKind,
    CoverSourceState, CoverTruncationReason, publication_ready,
)


REPORT_SCHEMA = "metadata-report-v7"
COMPARISON_VERSION = "same-kind-v1"
# The physical SQLite schema separately fingerprints this exact report shape,
# so any earlier development database fails closed under the clean-schema rule.
MAX_REPORT_BYTES = 2_097_152
MAX_CANDIDATES = 5
MAX_PROVIDER_EVIDENCE = 128
MAX_CONTRIBUTIONS = 16
MAX_SERIES = 16
MAX_TAGS = 32
MAX_SCALAR_CHARACTERS = 512
MAX_DESCRIPTION_CHARACTERS = 16_384
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REPORT_ID = re.compile(r"^report_[0-9a-f]{64}$")
_REFERENCE = re.compile(r"^(?:local|provider)_[0-9a-f]{64}$")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class ReportValueError(ValueError):
    """A report or report input is outside the closed 0.4 contract."""


class ClaimKind(StrEnum):
    TITLE = "title"
    SUBTITLE = "subtitle"
    CREATOR = "creator"
    CONTRIBUTOR = "contributor"
    SERIES = "series"
    SERIES_SEQUENCE = "series_sequence"
    ISBN = "isbn"
    ASIN = "asin"
    PUBLICATION_DATE = "publication_date"
    PUBLISHER = "publisher"
    DESCRIPTION = "description"
    LANGUAGE = "language"
    SUBJECT = "subject"
    IMPRINT = "imprint"
    GENRE = "genre"
    TAG = "tag"


class ProviderEvidenceKind(StrEnum):
    AUTHOR_IDENTITY = "author_identity"
    CONTRIBUTOR_IDENTITY = "contributor_identity"
    REVISION = "revision"
    LAST_MODIFIED = "last_modified"
    OCLC = "oclc"
    LCCN = "lccn"
    WIKIDATA = "wikidata"
    SUBJECT = "subject"
    PERSON = "person"
    PLACE = "place"
    TIME = "time"
    DEWEY_CLASSIFICATION = "dewey_classification"
    LC_CLASSIFICATION = "lc_classification"
    PHYSICAL_FORMAT = "physical_format"
    PAGE_COUNT = "page_count"
    PAGINATION = "pagination"
    PUBLICATION_PLACE = "publication_place"
    COPYRIGHT_DATE = "copyright_date"
    EDITION_WORK_RELATION = "edition_work_relation"
    FIRST_PUBLICATION_DATE = "first_publication_date"
    EDITION_COVER_ID = "edition_cover_id"
    WORK_COVER_ID = "work_cover_id"


class FieldState(StrEnum):
    """Closed outcome vocabulary for each local metadata field."""

    VALID = "valid"
    ABSENT = "absent"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    UNSUPPORTED = "unsupported"
    UNTYPED = "untyped"
    INAPPLICABLE = "inapplicable"


class FieldSource(StrEnum):
    EPUB = "epub"
    TARGET_METADATA = "target_metadata"


class TargetDestination(StrEnum):
    TITLE = "title"
    SUBTITLE = "subtitle"
    AUTHORS = "authors"
    NARRATORS = "narrators"
    SERIES = "series"
    GENRES = "genres"
    PUBLISHED_YEAR = "publishedYear"
    PUBLISHED_DATE = "publishedDate"
    PUBLISHER = "publisher"
    DESCRIPTION = "description"
    ISBN = "isbn"
    ASIN = "asin"
    LANGUAGE = "language"
    EXPLICIT = "explicit"
    TAGS = "tags"
    COVER = "cover"


class TargetFieldState(StrEnum):
    VALUE = "value"
    MISSING = "missing"
    NULL = "null"
    EMPTY = "empty"
    UNLOADED = "unloaded"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"


@dataclass(frozen=True, slots=True)
class TargetSeries:
    name: str
    sequence: str | None = None

    def __post_init__(self) -> None:
        _projection_text(self.name, "target series name", MAX_SCALAR_CHARACTERS)
        if self.sequence is not None:
            _projection_text(
                self.sequence, "target series sequence", MAX_SCALAR_CHARACTERS
            )

    def to_data(self) -> dict[str, str | None]:
        return {"name": self.name, "sequence": self.sequence}


@dataclass(frozen=True, slots=True)
class TargetField:
    destination: TargetDestination
    state: TargetFieldState
    value: str | bool | tuple[str, ...] | tuple[TargetSeries, ...] | None = None

    def __post_init__(self) -> None:
        if (
            type(self.destination) is not TargetDestination
            or type(self.state) is not TargetFieldState
        ):
            raise ReportValueError("target field is invalid")
        if self.state is not TargetFieldState.VALUE:
            if self.value is not None:
                raise ReportValueError("nonvalue target field has a value")
            return
        if self.destination is TargetDestination.EXPLICIT:
            if type(self.value) is not bool:
                raise ReportValueError("target explicit field is invalid")
            return
        if self.destination is TargetDestination.SERIES:
            if (
                type(self.value) is not tuple
                or not self.value
                or len(self.value) > MAX_SERIES
                or any(type(item) is not TargetSeries for item in self.value)
            ):
                raise ReportValueError("target series field is invalid")
            return
        if self.destination in {
            TargetDestination.AUTHORS,
            TargetDestination.NARRATORS,
            TargetDestination.GENRES,
            TargetDestination.TAGS,
        }:
            maximum = MAX_CONTRIBUTIONS if self.destination in {
                TargetDestination.AUTHORS,
                TargetDestination.NARRATORS,
            } else MAX_TAGS
            if (
                type(self.value) is not tuple
                or not self.value
                or len(self.value) > maximum
                or any(type(item) is not str for item in self.value)
            ):
                raise ReportValueError("target list field is invalid")
            for item in self.value:
                _projection_text(
                    item, "target list value", MAX_SCALAR_CHARACTERS
                )
            return
        if type(self.value) is not str or not self.value:
            raise ReportValueError("target scalar field is invalid")
        _projection_text(
            self.value,
            "target scalar value",
            MAX_DESCRIPTION_CHARACTERS
            if self.destination is TargetDestination.DESCRIPTION
            else MAX_SCALAR_CHARACTERS,
        )

    def to_data(self) -> dict[str, object]:
        value: object = self.value
        if self.destination is TargetDestination.SERIES and self.value is not None:
            value = [item.to_data() for item in self.value]
        elif type(self.value) is tuple:
            value = list(self.value)
        return {
            "destination": self.destination.value,
            "state": self.state.value,
            "value": value,
        }


@dataclass(frozen=True, slots=True)
class FieldSourceResult:
    source: FieldSource
    state: FieldState

    def __post_init__(self) -> None:
        if type(self.source) is not FieldSource or type(self.state) is not FieldState:
            raise ReportValueError("field source result is invalid")

    def to_data(self) -> dict[str, str]:
        return {"source": self.source.value, "state": self.state.value}


@dataclass(frozen=True, slots=True)
class RearClaimProvenance:
    resource_identity: str
    spine_ordinal: int
    tail_slot: int
    marker: str
    citation: str

    def __post_init__(self) -> None:
        if (_DIGEST.fullmatch(self.resource_identity) is None or type(self.spine_ordinal) is not int or self.spine_ordinal < 1 or type(self.tail_slot) is not int or not 1 <= self.tail_slot <= 8):
            raise ReportValueError("rear claim provenance is invalid")
        _projection_text(self.marker, "rear marker", 64)
        _projection_text(self.citation, "rear citation", 280)

    def to_data(self) -> dict[str, object]:
        return {"citation": self.citation, "marker": self.marker, "resource_identity": self.resource_identity, "spine_ordinal": self.spine_ordinal, "tail_slot": self.tail_slot}


@dataclass(frozen=True, slots=True)
class FieldResult:
    """Closed aggregate state for one field, independent of source claims."""

    kind: ClaimKind
    state: FieldState
    sources: tuple[FieldSourceResult, ...] = ()

    def __post_init__(self) -> None:
        if type(self.kind) is not ClaimKind or type(self.state) is not FieldState:
            raise ReportValueError("field result is invalid")
        if type(self.sources) is not tuple or any(type(value) is not FieldSourceResult for value in self.sources) or (self.sources and tuple(value.source for value in self.sources) != tuple(FieldSource)):
            raise ReportValueError("field result sources are invalid")

    def to_data(self) -> dict[str, str]:
        return {"kind": self.kind.value, "state": self.state.value, "sources": [value.to_data() for value in self.sources]}


class ProviderStatus(StrEnum):
    DISABLED = "disabled"
    INSUFFICIENT_INPUT = "insufficient_input"
    CANDIDATES = "candidates"
    NO_MATCH = "no_match"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    CANDIDATE_LIMIT_EXCEEDED = "candidate_limit_exceeded"


class ProviderQueryKind(StrEnum):
    NONE = "none"
    ISBN = "isbn"
    WORK = "work"


class Comparison(StrEnum):
    AGREEMENT = "agreement"
    DISAGREEMENT = "disagreement"
    INCOMPATIBLE = "incompatible"
    NOT_COMPARABLE = "not_comparable"


class IdentifierRelation(StrEnum):
    """Closed relationship between an ISBN claim and the selected ebook."""

    ACCEPTED = "accepted"
    ASSOCIATED = "associated"
    UNRELATED = "unrelated"
    INVALID = "invalid"
    UNRESOLVED = "unresolved"


class IdentifierEvidenceBasis(StrEnum):
    """Closed, deterministic reason for an ISBN relationship."""

    EXPLICIT_EBOOK = "explicit_ebook"
    LOCAL_SOURCE_AGREEMENT = "local_source_agreement"
    PROVIDER_IDENTIFIER_AGREEMENT = "provider_identifier_agreement"
    PROVIDER_WORK_IDENTITY = "provider_work_identity"
    PROVIDER_OTHER_WORK = "provider_other_work"
    INVALID_IDENTIFIER = "invalid_identifier"
    INSUFFICIENT_CONTEXT = "insufficient_context"


@dataclass(frozen=True, slots=True)
class IdentifierEvidence:
    """Public-safe ISBN relationship derived from retained claim provenance."""

    reference: str
    relation: IdentifierRelation
    basis: IdentifierEvidenceBasis
    related_references: tuple[str, ...] = ()
    provider: str | None = None
    candidate_identity: str | None = None
    work_identity: str | None = None

    def __post_init__(self) -> None:
        allowed_bases = {
            IdentifierRelation.ACCEPTED: {
                IdentifierEvidenceBasis.EXPLICIT_EBOOK,
                IdentifierEvidenceBasis.LOCAL_SOURCE_AGREEMENT,
                IdentifierEvidenceBasis.PROVIDER_IDENTIFIER_AGREEMENT,
            },
            IdentifierRelation.ASSOCIATED: {
                IdentifierEvidenceBasis.PROVIDER_WORK_IDENTITY,
            },
            IdentifierRelation.UNRELATED: {
                IdentifierEvidenceBasis.PROVIDER_OTHER_WORK,
            },
            IdentifierRelation.INVALID: {
                IdentifierEvidenceBasis.INVALID_IDENTIFIER,
            },
            IdentifierRelation.UNRESOLVED: {
                IdentifierEvidenceBasis.INSUFFICIENT_CONTEXT,
            },
        }
        if (
            _REFERENCE.fullmatch(self.reference) is None
            or type(self.relation) is not IdentifierRelation
            or type(self.basis) is not IdentifierEvidenceBasis
            or self.basis not in allowed_bases.get(self.relation, set())
            or type(self.related_references) is not tuple
            or tuple(sorted(set(self.related_references))) != self.related_references
            or self.reference in self.related_references
            or any(_REFERENCE.fullmatch(value) is None for value in self.related_references)
        ):
            raise ReportValueError("identifier evidence is invalid")
        provider_values = (
            self.provider,
            self.candidate_identity,
            self.work_identity,
        )
        if self.reference.startswith("local_"):
            if any(value is not None for value in provider_values):
                raise ReportValueError("local identifier evidence has provider identity")
        elif not self.reference.startswith("provider_") or any(
            value is None for value in provider_values
        ):
            raise ReportValueError("provider identifier evidence lacks identity")
        else:
            _name(self.provider, "identifier evidence provider")
            _text(
                self.candidate_identity,
                "identifier evidence candidate identity",
                128,
            )
            _text(self.work_identity, "identifier evidence work identity", 128)

    def to_data(self) -> dict[str, object]:
        return {
            "basis": self.basis.value,
            "candidate_identity": self.candidate_identity,
            "provider": self.provider,
            "reference": self.reference,
            "related_references": list(self.related_references),
            "relation": self.relation.value,
            "work_identity": self.work_identity,
        }


class ClaimScope(StrEnum):
    WORK = "work"
    EDITION = "edition"


@dataclass(frozen=True, slots=True)
class ComparisonLink:
    local_reference: str
    outcome: Comparison

    def __post_init__(self) -> None:
        if _REFERENCE.fullmatch(self.local_reference) is None or not self.local_reference.startswith("local_") or type(self.outcome) is not Comparison:
            raise ReportValueError("comparison link is invalid")

    def to_data(self) -> dict[str, str]:
        return {"local_reference": self.local_reference, "outcome": self.outcome.value}


def canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError, RecursionError) as error:
        raise ReportValueError("report value is not serializable") from error


def stable_reference(prefix: str, value: object) -> str:
    if prefix not in {"local", "provider"}:
        raise ReportValueError("claim reference prefix is invalid")
    return f"{prefix}_{hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()}"


def normalize_title(value: str) -> str:
    if type(value) is not str:
        raise ReportValueError("title is invalid")
    normalized = " ".join(unicodedata.normalize("NFC", value).split())
    _text(normalized, "title", 512)
    try:
        validate_projection_text(normalized, "title", 512)
    except EvidenceValueError as error:
        raise ReportValueError("title is not public-safe") from error
    return normalized


def validate_observed_title(value: object) -> str:
    try:
        exact = validate_projection_text(value, "observed title", 512)
    except EvidenceValueError as error:
        raise ReportValueError("observed title is not public-safe") from error
    normalize_title(exact)
    return exact


@dataclass(frozen=True, slots=True)
class TargetFacts:
    connection: str
    library: str
    item: str
    fields: tuple[TargetField, ...]

    def __post_init__(self) -> None:
        _name(self.connection, "connection")
        _name(self.library, "library")
        _text(self.item, "item", 512)
        if (
            type(self.fields) is not tuple
            or any(type(value) is not TargetField for value in self.fields)
            or tuple(value.destination for value in self.fields)
            != tuple(TargetDestination)
        ):
            raise ReportValueError("target fields are invalid")

    @property
    def current_title(self) -> str | None:
        title = self.fields[0]
        if title.state is not TargetFieldState.VALUE:
            return None
        if type(title.value) is not str:
            raise ReportValueError("target title is inconsistent")
        return validate_observed_title(title.value)

    @property
    def fingerprint(self) -> str:
        data = {
            "connection": self.connection,
            "fields": [value.to_data() for value in self.fields],
            "item": self.item,
            "library": self.library,
        }
        return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()

    def to_data(self) -> dict[str, object]:
        return {
            "connection": self.connection,
            "fields": [value.to_data() for value in self.fields],
            "item": self.item,
            "library": self.library,
        }


def default_target_fields(title: str | None) -> tuple[TargetField, ...]:
    values: list[TargetField] = []
    for destination in TargetDestination:
        if destination is TargetDestination.TITLE and title is not None:
            values.append(
                TargetField(
                    destination,
                    TargetFieldState.VALUE,
                    validate_observed_title(title),
                )
            )
        else:
            values.append(TargetField(destination, TargetFieldState.MISSING))
    return tuple(values)


@dataclass(frozen=True, slots=True)
class FileFacts:
    selected_index: int
    identity: str
    content_digest: str
    format: str = "epub"

    def __post_init__(self) -> None:
        if type(self.selected_index) is not int or not 0 <= self.selected_index <= 10_000:
            raise ReportValueError("selected file index is invalid")
        if _DIGEST.fullmatch(self.identity) is None or _DIGEST.fullmatch(self.content_digest) is None:
            raise ReportValueError("selected file identity is invalid")
        if self.format != "epub":
            raise ReportValueError("selected file format is unsupported")

    def to_data(self) -> dict[str, object]:
        return {
            "content_digest": self.content_digest,
            "format": self.format,
            "identity": self.identity,
            "selected_index": self.selected_index,
        }


@dataclass(frozen=True, slots=True)
class LocalClaim:
    reference: str
    kind: ClaimKind
    observed: str
    canonical: str | None
    excerpt: str
    applicability: str
    basis: str
    checksum: str
    applicability_basis: str
    scope: ClaimScope
    state: FieldState = FieldState.VALID
    role: str | None = None
    rear_provenance: RearClaimProvenance | None = None
    occurrence: int = 0

    def __post_init__(self) -> None:
        if _REFERENCE.fullmatch(self.reference) is None or not self.reference.startswith("local_"):
            raise ReportValueError("local claim reference is invalid")
        if type(self.kind) is not ClaimKind or type(self.state) is not FieldState or type(self.scope) is not ClaimScope:
            raise ReportValueError("local claim kind is invalid")
        if self.basis == "target_metadata" and self.kind is not ClaimKind.TITLE:
            raise ReportValueError("target metadata claim kind is invalid")
        if self.role is not None:
            if self.kind not in {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR}:
                raise ReportValueError("local claim role is invalid")
            try:
                validate_role_projection(self.role)
            except EvidenceValueError as error:
                raise ReportValueError("local claim role is invalid") from error
        if self.rear_provenance is not None and type(self.rear_provenance) is not RearClaimProvenance:
            raise ReportValueError("rear claim provenance is invalid")
        if self.rear_provenance is not None and (self.basis != "opening_matter" or self.kind not in {ClaimKind.TITLE, ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR, ClaimKind.ISBN}):
            raise ReportValueError("rear claim provenance is inapplicable")
        if type(self.occurrence) is not int or not 0 <= self.occurrence <= 1_000_000:
            raise ReportValueError("local claim occurrence is invalid")
        maximum = 512 if self.kind in {ClaimKind.TITLE, ClaimKind.SUBTITLE, ClaimKind.DESCRIPTION} else 280
        _projection_text(self.observed, "observed claim", maximum)
        if self.canonical is not None:
            _projection_text(self.canonical, "canonical claim", 512)
        _projection_text(self.excerpt, "claim excerpt", maximum)
        if self.observed not in self.excerpt:
            raise ReportValueError("claim excerpt does not support its observation")
        if self.applicability not in {"ebook", "print", "unknown", "ambiguous", "not_applicable"}:
            raise ReportValueError("claim applicability is invalid")
        if self.basis not in {"opening_matter", "package_metadata", "target_metadata"}:
            raise ReportValueError("claim basis is invalid")
        if self.checksum not in {"valid_isbn_10", "valid_isbn_13", "invalid", "not_applicable"}:
            raise ReportValueError("claim checksum is invalid")
        if self.applicability_basis not in {"ebook", "paperback", "hardcover", "print", "unlabelled", "ambiguous", "not_applicable"}:
            raise ReportValueError("claim applicability basis is invalid")
        if self.kind is not ClaimKind.ISBN and (
            self.checksum != "not_applicable"
            or self.applicability != "not_applicable"
            or self.applicability_basis != "not_applicable"
        ):
            raise ReportValueError("non-identifier claim has identifier fields")
        if self.kind is ClaimKind.ISBN and (
            self.checksum.startswith("valid_") != (self.canonical is not None)
            or self.applicability == "not_applicable"
            or self.applicability_basis == "not_applicable"
        ):
            raise ReportValueError("ISBN claim typing is inconsistent")
        if self.kind is ClaimKind.ISBN and self.canonical is not None and not _valid_isbn(self.canonical, self.checksum):
            raise ReportValueError("ISBN claim checksum is inconsistent")
        sentinel = _sentinel_state(self.observed)
        if sentinel is not None and (self.state is not sentinel or self.canonical is not None):
            raise ReportValueError("source sentinel claim is inconsistent")
        if self.state is FieldState.VALID and self.kind in {ClaimKind.TITLE, ClaimKind.SUBTITLE} and self.canonical != normalize_title(self.observed):
            raise ReportValueError("title claim normalization is inconsistent")
        expected = stable_reference("local", self._reference_data())
        if self.reference != expected:
            raise ReportValueError("local claim reference is inconsistent")

    @classmethod
    def create(
        cls,
        *,
        kind: ClaimKind,
        observed: str,
        canonical: str | None,
        excerpt: str,
        applicability: str,
        basis: str = "opening_matter",
        checksum: str,
        applicability_basis: str,
        scope: ClaimScope | None = None,
        state: FieldState = FieldState.VALID,
        role: str | None = None,
        rear_provenance: RearClaimProvenance | None = None,
        occurrence: int = 0,
    ) -> Self:
        value = {
            "applicability": applicability,
            "basis": basis,
            "canonical": canonical,
            "checksum": checksum,
            "excerpt": excerpt,
            "kind": kind.value,
            "observed": observed,
            "applicability_basis": applicability_basis,
            "state": state.value if type(state) is FieldState else "",
            "scope": (scope if scope is not None else claim_scope(kind)).value if type(scope if scope is not None else claim_scope(kind)) is ClaimScope else "",
            "role": role,
            "rear_provenance": None if rear_provenance is None else rear_provenance.to_data(),
            "occurrence": occurrence,
        }
        resolved_scope = scope if scope is not None else claim_scope(kind)
        return cls(stable_reference("local", value), kind, observed, canonical, excerpt, applicability, basis, checksum, applicability_basis, resolved_scope, state, role, rear_provenance, occurrence)

    def _reference_data(self) -> dict[str, object]:
        return {
            "applicability": self.applicability,
            "basis": self.basis,
            "canonical": self.canonical,
            "checksum": self.checksum,
            "excerpt": self.excerpt,
            "kind": self.kind.value,
            "observed": self.observed,
            "applicability_basis": self.applicability_basis,
            "state": self.state.value,
            "scope": self.scope.value,
            "role": self.role,
            "rear_provenance": None if self.rear_provenance is None else self.rear_provenance.to_data(),
            "occurrence": self.occurrence,
        }

    def to_data(self) -> dict[str, object]:
        return {"reference": self.reference, "state": self.state.value, **self._reference_data()}


@dataclass(frozen=True, slots=True)
class ProviderClaim:
    reference: str
    kind: ClaimKind
    value: str | None
    state: FieldState
    scope: ClaimScope
    applicability: str
    role: str | None = None
    raw_order: int = 0
    links: tuple[ComparisonLink, ...] = ()

    def __post_init__(self) -> None:
        if _REFERENCE.fullmatch(self.reference) is None or not self.reference.startswith("provider_"):
            raise ReportValueError("provider claim reference is invalid")
        if type(self.kind) is not ClaimKind:
            raise ReportValueError("provider claim is invalid")
        if self.applicability not in {"ebook", "print", "unknown", "ambiguous", "not_applicable"} or type(self.state) is not FieldState or type(self.raw_order) is not int or self.raw_order < 0 or type(self.scope) is not ClaimScope or type(self.links) is not tuple or any(type(link) is not ComparisonLink for link in self.links):
            raise ReportValueError("provider claim scope is invalid")
        if self.role is not None:
            if self.kind not in {ClaimKind.CREATOR, ClaimKind.CONTRIBUTOR}:
                raise ReportValueError("provider claim role is invalid")
            try:
                validate_role_projection(self.role)
            except EvidenceValueError as error:
                raise ReportValueError("provider claim role is invalid") from error
        if self.state is FieldState.VALID:
            if self.value is None:
                raise ReportValueError("valid provider claim has no value")
            _projection_text(
                self.value,
                "provider claim value",
                MAX_DESCRIPTION_CHARACTERS if self.kind is ClaimKind.DESCRIPTION else MAX_SCALAR_CHARACTERS,
            )
        elif self.value is not None:
            raise ReportValueError("nonvalid provider claim has a value")
        if self.kind is ClaimKind.ISBN and self.value is not None and not (
            _valid_isbn(self.value, "valid_isbn_10")
            or _valid_isbn(self.value, "valid_isbn_13")
        ):
            raise ReportValueError("provider ISBN claim is invalid")
        if tuple(link.local_reference for link in self.links) != tuple(sorted(link.local_reference for link in self.links)) or len({link.local_reference for link in self.links}) != len(self.links):
            raise ReportValueError("provider comparison links are invalid")

    @classmethod
    def create(
        cls,
        *,
        provider: str,
        candidate: str,
        kind: ClaimKind,
        value: str | None,
        state: FieldState,
        scope: ClaimScope,
        applicability: str,
        role: str | None = None,
        raw_order: int = 0,
        links: tuple[ComparisonLink, ...] = (),
    ) -> Self:
        data = _provider_reference_data(provider, candidate, kind, value, state, scope, applicability, role, raw_order)
        return cls(stable_reference("provider", data), kind, value, state, scope, applicability, role, raw_order, links)

    def to_data(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "reference": self.reference,
            "applicability": self.applicability,
            "role": self.role,
            "state": self.state.value,
            "raw_order": self.raw_order,
            "scope": self.scope.value,
            "links": [link.to_data() for link in self.links],
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class ProviderEvidence:
    kind: ProviderEvidenceKind
    value: str
    scope: ClaimScope
    role: str | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not ProviderEvidenceKind or type(self.scope) is not ClaimScope:
            raise ReportValueError("provider rich evidence is invalid")
        _projection_text(self.value, "provider rich evidence value", MAX_SCALAR_CHARACTERS)
        if self.kind is ProviderEvidenceKind.CONTRIBUTOR_IDENTITY:
            if self.role is None:
                raise ReportValueError("provider rich evidence role is invalid")
            try:
                validate_role_projection(self.role)
            except EvidenceValueError as error:
                raise ReportValueError("provider rich evidence role is invalid") from error
        elif self.role is not None:
            raise ReportValueError("provider rich evidence role is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "role": self.role,
            "scope": self.scope.value,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    identity: str
    work_identity: str
    claims: tuple[ProviderClaim, ...]
    evidence: tuple[ProviderEvidence, ...] = ()

    def __post_init__(self) -> None:
        _text(self.identity, "provider candidate identity", 128)
        _text(self.work_identity, "provider candidate work identity", 128)
        if type(self.claims) is not tuple or any(type(value) is not ProviderClaim for value in self.claims):
            raise ReportValueError("provider candidate claims are invalid")
        references = tuple(value.reference for value in self.claims)
        if len(set(references)) != len(references):
            raise ReportValueError("provider candidate claim references are duplicated")
        if tuple(value.raw_order for value in self.claims) != tuple(sorted(value.raw_order for value in self.claims)):
            raise ReportValueError("provider candidate claims are not in raw source order")
        if (
            type(self.evidence) is not tuple
            or len(self.evidence) > MAX_PROVIDER_EVIDENCE
            or any(type(value) is not ProviderEvidence for value in self.evidence)
            or len(set(self.evidence)) != len(self.evidence)
            or self.evidence
            != tuple(
                sorted(
                    self.evidence,
                    key=lambda value: (
                        value.scope.value,
                        value.kind.value,
                        value.value,
                        value.role or "",
                    ),
                )
            )
        ):
            raise ReportValueError("provider rich evidence is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "claims": [value.to_data() for value in self.claims],
            "evidence": [value.to_data() for value in self.evidence],
            "identity": self.identity,
            "work_identity": self.work_identity,
        }


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    provider: str
    status: ProviderStatus
    input_references: tuple[str, ...] = ()
    candidates: tuple[ProviderCandidate, ...] = ()
    query_kind: ProviderQueryKind = ProviderQueryKind.NONE
    authority: str | None = None

    def __post_init__(self) -> None:
        _name(self.provider, "provider")
        if (
            type(self.status) is not ProviderStatus
            or type(self.query_kind) is not ProviderQueryKind
            or self.authority not in {None, "hardcover", "open_library"}
        ):
            raise ReportValueError("provider status is invalid")
        if type(self.input_references) is not tuple or tuple(sorted(set(self.input_references))) != self.input_references or any(
            _REFERENCE.fullmatch(value) is None or not value.startswith("local_")
            for value in self.input_references
        ):
            raise ReportValueError("provider input references are invalid")
        if type(self.candidates) is not tuple or len(self.candidates) > MAX_CANDIDATES or any(type(value) is not ProviderCandidate for value in self.candidates):
            raise ReportValueError("provider candidates are invalid")
        identities = tuple(value.identity for value in self.candidates)
        if identities != tuple(sorted(set(identities))):
            raise ReportValueError("provider candidates are not in canonical identity order")
        if (self.status is ProviderStatus.CANDIDATES) != bool(self.candidates):
            raise ReportValueError("provider status and candidates disagree")
        if self.status in {ProviderStatus.DISABLED, ProviderStatus.INSUFFICIENT_INPUT} and self.input_references:
            raise ReportValueError("inactive provider cannot claim inputs")
        if any(
            claim.reference
            != stable_reference(
                "provider",
                _provider_reference_data(self.provider, candidate.identity, claim.kind, claim.value, claim.state, claim.scope, claim.applicability, claim.role, claim.raw_order),
            )
            for candidate in self.candidates
            for claim in candidate.claims
        ):
            raise ReportValueError("provider candidate claim reference is inconsistent")

    def to_data(self) -> dict[str, object]:
        return {
            "candidates": [value.to_data() for value in self.candidates],
            "input_references": list(self.input_references),
            "provider": self.provider,
            "query_kind": self.query_kind.value,
            "status": self.status.value,
            "authority": self.authority,
        }


@dataclass(frozen=True, slots=True)
class MetadataReport:
    report_id: str
    target: TargetFacts
    file: FileFacts
    local_claims: tuple[LocalClaim, ...]
    local_status: str
    local_reasons: tuple[str, ...]
    providers: tuple[ProviderObservation, ...]
    field_results: tuple[FieldResult, ...]
    identifier_evidence: tuple[IdentifierEvidence, ...]
    cover_sources: tuple[CoverSource, ...] = ()
    schema_version: str = REPORT_SCHEMA
    comparison_version: str = COMPARISON_VERSION

    def __post_init__(self) -> None:
        if _REPORT_ID.fullmatch(self.report_id) is None or type(self.target) is not TargetFacts or type(self.file) is not FileFacts:
            raise ReportValueError("report identity or target is invalid")
        if self.schema_version != REPORT_SCHEMA:
            raise ReportValueError("report schema is unsupported")
        if self.comparison_version != COMPARISON_VERSION:
            raise ReportValueError("report comparison version is unsupported")
        if type(self.local_claims) is not tuple or any(type(value) is not LocalClaim for value in self.local_claims):
            raise ReportValueError("local claims are invalid")
        references = tuple(value.reference for value in self.local_claims)
        if len(set(references)) != len(references):
            raise ReportValueError("local claim references are duplicated")
        has_opening_candidate = any(value.basis == "opening_matter" for value in self.local_claims)
        if self.local_status not in {"candidate", "no_candidate"} or (self.local_status == "candidate") != has_opening_candidate:
            raise ReportValueError("local candidate status is invalid")
        if type(self.local_reasons) is not tuple or not self.local_reasons or tuple(sorted(set(self.local_reasons))) != self.local_reasons:
            raise ReportValueError("local reasons are invalid")
        for value in self.local_reasons:
            _name(value, "local reason")
        if type(self.providers) is not tuple or any(type(value) is not ProviderObservation for value in self.providers):
            raise ReportValueError("provider observations are invalid")
        names = tuple(value.provider for value in self.providers)
        if names != tuple(sorted(set(names))):
            raise ReportValueError("provider observations are not in canonical order")
        if (
            type(self.field_results) is not tuple
            or any(type(value) is not FieldResult for value in self.field_results)
            or tuple(value.kind for value in self.field_results) != tuple(ClaimKind)
            or self.field_results != derive_field_results(self.local_claims)
        ):
            raise ReportValueError("field results are inconsistent")
        if (
            type(self.identifier_evidence) is not tuple
            or any(
                type(value) is not IdentifierEvidence
                for value in self.identifier_evidence
            )
            or self.identifier_evidence
            != derive_identifier_evidence(self.local_claims, self.providers)
        ):
            raise ReportValueError("identifier evidence is inconsistent")
        try:
            if not publication_ready(self.cover_sources):
                raise ReportValueError("cover evidence is incomplete")
        except CoverEvidenceError as error:
            raise ReportValueError("cover evidence is invalid") from error
        known = set(references)
        if any(not set(provider.input_references).issubset(known) for provider in self.providers):
            raise ReportValueError("provider inputs do not reference local claims")
        provider_claims = tuple(
            claim
            for provider in self.providers
            for candidate in provider.candidates
            for claim in candidate.claims
        )
        if len({claim.reference for claim in provider_claims}) != len(provider_claims) or any(
            not {link.local_reference for link in claim.links}.issubset(known) for claim in provider_claims
        ):
            raise ReportValueError("provider candidate claim references are invalid")
        for claim in provider_claims:
            expected = comparison_links(claim, self.local_claims)
            if claim.links != expected:
                raise ReportValueError("provider comparison links are inconsistent")
        expected = f"report_{hashlib.sha256(canonical_json(self._identity_data()).encode('utf-8')).hexdigest()}"
        if self.report_id != expected:
            raise ReportValueError("report identity is inconsistent")
        if len(self.to_json().encode("utf-8")) > MAX_REPORT_BYTES:
            raise ReportValueError("report exceeds its byte limit")

    @classmethod
    def create(
        cls,
        *,
        target: TargetFacts,
        file: FileFacts,
        local_claims: tuple[LocalClaim, ...],
        local_reasons: tuple[str, ...],
        providers: tuple[ProviderObservation, ...],
        field_results: tuple[FieldResult, ...] | None = None,
        identifier_evidence: tuple[IdentifierEvidence, ...] | None = None,
        cover_sources: tuple[CoverSource, ...] = (),
    ) -> Self:
        local_status = "candidate" if any(value.basis == "opening_matter" for value in local_claims) else "no_candidate"
        results = field_results if field_results is not None else derive_field_results(local_claims)
        identifiers = identifier_evidence if identifier_evidence is not None else derive_identifier_evidence(local_claims, providers)
        data = _report_data("", target, file, local_claims, local_status, local_reasons, providers, results, identifiers, cover_sources)
        data.pop("report_id")
        identity = f"report_{hashlib.sha256(canonical_json(data).encode('utf-8')).hexdigest()}"
        return cls(identity, target, file, local_claims, local_status, local_reasons, providers, results, identifiers, cover_sources)

    def _identity_data(self) -> dict[str, object]:
        value = self.to_data()
        value.pop("report_id")
        return value

    def to_data(self) -> dict[str, object]:
        return _report_data(self.report_id, self.target, self.file, self.local_claims, self.local_status, self.local_reasons, self.providers, self.field_results, self.identifier_evidence, self.cover_sources)

    def to_json(self) -> str:
        return canonical_json(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        if type(serialized) is not str or not serialized or len(serialized.encode("utf-8")) > MAX_REPORT_BYTES:
            raise ReportValueError("report document is invalid")
        try:
            source = json.loads(
                serialized,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_number,
            )
            report = _report_from_data(source)
        except ReportValueError:
            raise
        except (KeyError, TypeError, ValueError, UnicodeError, RecursionError, json.JSONDecodeError) as error:
            raise ReportValueError("report document is invalid") from error
        if report.to_json() != serialized:
            raise ReportValueError("report document is not canonical")
        return report


def derive_field_results(local_claims: tuple[LocalClaim, ...]) -> tuple[FieldResult, ...]:
    """Summarise observed field states without making claims a decision surface."""
    precedence = (
        FieldState.MALFORMED, FieldState.OVERSIZED, FieldState.UNSUPPORTED,
        FieldState.UNTYPED, FieldState.INAPPLICABLE, FieldState.VALID,
    )
    def source_state(kind: ClaimKind, source: FieldSource) -> FieldState:
        if source is FieldSource.TARGET_METADATA and kind is not ClaimKind.TITLE:
            return FieldState.UNSUPPORTED
        basis = "target_metadata" if source is FieldSource.TARGET_METADATA else None
        return next(
            (
                state
                for state in precedence
                if any(
                    claim.kind is kind
                    and claim.state is state
                    and ((claim.basis == basis) if basis is not None else claim.basis != "target_metadata")
                    for claim in local_claims
                )
            ),
            FieldState.ABSENT,
        )

    return tuple(
        FieldResult(
            kind,
            next((state for state in precedence if any(claim.kind is kind and claim.state is state for claim in local_claims)), FieldState.ABSENT),
            tuple(FieldSourceResult(source, source_state(kind, source)) for source in FieldSource),
        )
        for kind in ClaimKind
    )


def derive_identifier_evidence(
    local_claims: tuple[LocalClaim, ...],
    providers: tuple[ProviderObservation, ...],
) -> tuple[IdentifierEvidence, ...]:
    """Classify every ISBN claim without discarding its original evidence."""
    local_isbns = tuple(
        claim for claim in local_claims if claim.kind is ClaimKind.ISBN
    )
    provider_isbns = tuple(
        (provider.provider, candidate, claim)
        for provider in providers
        for candidate in provider.candidates
        for claim in candidate.claims
        if claim.kind is ClaimKind.ISBN
    )
    local_families = {
        claim.reference: isbn_family(claim.canonical)
        for claim in local_isbns
        if claim.state is FieldState.VALID and claim.canonical is not None
    }
    provider_families = {
        claim.reference: isbn_family(claim.value)
        for _, _, claim in provider_isbns
        if claim.state is FieldState.VALID and claim.value is not None
    }

    family_sources: dict[str, set[str]] = {}
    family_local_claims: dict[str, list[LocalClaim]] = {}
    for claim in local_isbns:
        family = local_families.get(claim.reference)
        if family is None:
            continue
        family_sources.setdefault(family, set()).add(claim.basis)
        family_local_claims.setdefault(family, []).append(claim)

    local_acceptance: dict[str, IdentifierEvidenceBasis] = {}
    for family, claims in family_local_claims.items():
        for claim in claims:
            if (
                claim.applicability == "ebook"
                and claim.applicability_basis == "ebook"
            ):
                local_acceptance[claim.reference] = (
                    IdentifierEvidenceBasis.EXPLICIT_EBOOK
                )
        if (
            family_sources[family] >= {"package_metadata", "opening_matter"}
            and all(
                claim.applicability == "unknown"
                and claim.applicability_basis == "unlabelled"
                for claim in claims
            )
        ):
            for claim in claims:
                local_acceptance[claim.reference] = (
                    IdentifierEvidenceBasis.LOCAL_SOURCE_AGREEMENT
                )

    accepted_local_references = {
        family: [
            claim.reference
            for claim in claims
            if claim.reference in local_acceptance
        ]
        for family, claims in family_local_claims.items()
    }
    accepted_local_families = {
        family
        for family, references in accepted_local_references.items()
        if references
    }

    accepted_family_works: dict[
        tuple[str, str], dict[str, list[str]]
    ] = {}
    for provider, candidate, claim in provider_isbns:
        family = provider_families.get(claim.reference)
        if family in accepted_local_families:
            accepted_family_works.setdefault(
                (provider, family), {}
            ).setdefault(candidate.work_identity, []).append(claim.reference)

    accepted_works: dict[str, set[str]] = {}
    provider_work_anchor_references: dict[tuple[str, str], list[str]] = {}
    for (provider, _), work_references in accepted_family_works.items():
        if len(work_references) != 1:
            continue
        work_identity, references = next(iter(work_references.items()))
        accepted_works.setdefault(provider, set()).add(work_identity)
        provider_work_anchor_references.setdefault(
            (provider, work_identity), []
        ).extend(references)

    provider_by_family: dict[str, list[tuple[str, ProviderCandidate, ProviderClaim]]] = {}
    for provider, candidate, claim in provider_isbns:
        family = provider_families.get(claim.reference)
        if family is not None:
            provider_by_family.setdefault(family, []).append(
                (provider, candidate, claim)
            )

    evidence: list[IdentifierEvidence] = []
    for claim in local_isbns:
        family = local_families.get(claim.reference)
        if family is None:
            evidence.append(
                IdentifierEvidence(
                    claim.reference,
                    IdentifierRelation.INVALID,
                    IdentifierEvidenceBasis.INVALID_IDENTIFIER,
                )
            )
            continue
        acceptance_basis = local_acceptance.get(claim.reference)
        if acceptance_basis is not None:
            related = tuple(
                sorted(
                    other.reference
                    for other in family_local_claims[family]
                    if other.reference in local_acceptance
                    and other.reference != claim.reference
                    and other.basis != claim.basis
                )
            )
            evidence.append(
                IdentifierEvidence(
                    claim.reference,
                    IdentifierRelation.ACCEPTED,
                    acceptance_basis,
                    related,
                )
            )
            continue
        matching = provider_by_family.get(family, [])
        associated = [
            provider_claim.reference
            for provider, candidate, provider_claim in matching
            if candidate.work_identity in accepted_works.get(provider, set())
        ]
        if associated:
            evidence.append(
                IdentifierEvidence(
                    claim.reference,
                    IdentifierRelation.ASSOCIATED,
                    IdentifierEvidenceBasis.PROVIDER_WORK_IDENTITY,
                    tuple(sorted(associated)),
                )
            )
        elif any(accepted_works.get(provider) for provider, _, _ in matching):
            evidence.append(
                IdentifierEvidence(
                    claim.reference,
                    IdentifierRelation.UNRELATED,
                    IdentifierEvidenceBasis.PROVIDER_OTHER_WORK,
                    tuple(sorted(item.reference for _, _, item in matching)),
                )
            )
        else:
            evidence.append(
                IdentifierEvidence(
                    claim.reference,
                    IdentifierRelation.UNRESOLVED,
                    IdentifierEvidenceBasis.INSUFFICIENT_CONTEXT,
                )
            )

    for provider, candidate, claim in provider_isbns:
        family = provider_families.get(claim.reference)
        identity = {
            "provider": provider,
            "candidate_identity": candidate.identity,
            "work_identity": candidate.work_identity,
        }
        if family is None:
            evidence.append(
                IdentifierEvidence(
                    claim.reference,
                    IdentifierRelation.INVALID,
                    IdentifierEvidenceBasis.INVALID_IDENTIFIER,
                    **identity,
                )
            )
        elif family in accepted_local_families:
            if claim.applicability == "ebook":
                evidence.append(
                    IdentifierEvidence(
                        claim.reference,
                        IdentifierRelation.ACCEPTED,
                        IdentifierEvidenceBasis.PROVIDER_IDENTIFIER_AGREEMENT,
                        tuple(sorted(accepted_local_references[family])),
                        **identity,
                    )
                )
            else:
                evidence.append(
                    IdentifierEvidence(
                        claim.reference,
                        IdentifierRelation.ASSOCIATED,
                        IdentifierEvidenceBasis.PROVIDER_WORK_IDENTITY,
                        tuple(sorted(accepted_local_references[family])),
                        **identity,
                    )
                )
        elif candidate.work_identity in accepted_works.get(provider, set()):
            related = provider_work_anchor_references.get(
                (provider, candidate.work_identity), []
            )
            evidence.append(
                IdentifierEvidence(
                    claim.reference,
                    IdentifierRelation.ASSOCIATED,
                    IdentifierEvidenceBasis.PROVIDER_WORK_IDENTITY,
                    tuple(sorted(related)),
                    **identity,
                )
            )
        elif accepted_works.get(provider):
            related = [
                reference
                for work in accepted_works[provider]
                for reference in provider_work_anchor_references[(provider, work)]
            ]
            evidence.append(
                IdentifierEvidence(
                    claim.reference,
                    IdentifierRelation.UNRELATED,
                    IdentifierEvidenceBasis.PROVIDER_OTHER_WORK,
                    tuple(sorted(related)),
                    **identity,
                )
            )
        else:
            evidence.append(
                IdentifierEvidence(
                    claim.reference,
                    IdentifierRelation.UNRESOLVED,
                    IdentifierEvidenceBasis.INSUFFICIENT_CONTEXT,
                    **identity,
                )
            )
    return tuple(evidence)


def isbn_family(value: str | None) -> str | None:
    if value is None:
        return None
    compact = _comparison_value(ClaimKind.ISBN, value)
    if _valid_isbn(compact, "valid_isbn_13"):
        return compact
    if not _valid_isbn(compact, "valid_isbn_10"):
        return None
    stem = "978" + compact[:9]
    checksum = (
        10
        - sum(
            (1 if index % 2 == 0 else 3) * int(character)
            for index, character in enumerate(stem)
        )
        % 10
    ) % 10
    return stem + str(checksum)


def _provider_reference_data(provider: str, candidate: str, kind: ClaimKind, value: str | None, state: FieldState, scope: ClaimScope, applicability: str, role: str | None, raw_order: int) -> dict[str, object]:
    return {
        "applicability": applicability,
        "candidate": candidate,
        "kind": kind.value,
        "provider": provider,
        "raw_order": raw_order,
        "role": role,
        "scope": scope.value,
        "state": state.value,
        "value": value,
    }


def claim_scope(kind: ClaimKind) -> ClaimScope:
    if type(kind) is not ClaimKind:
        raise ReportValueError("claim kind is invalid")
    return ClaimScope.EDITION if kind in {ClaimKind.ISBN, ClaimKind.ASIN, ClaimKind.PUBLICATION_DATE, ClaimKind.PUBLISHER, ClaimKind.IMPRINT, ClaimKind.LANGUAGE} else ClaimScope.WORK


def comparison_links(provider: ProviderClaim, local: tuple[LocalClaim, ...]) -> tuple[ComparisonLink, ...]:
    """Compute one immutable comparison outcome for each same-kind local claim."""
    links: list[ComparisonLink] = []
    for claim in local:
        if claim.kind is not provider.kind:
            continue
        if (
            provider.state is not FieldState.VALID
            or provider.value is None
            or claim.state is not FieldState.VALID
            or claim.canonical is None
            or claim.scope is not provider.scope
            or claim.applicability != provider.applicability
            or claim.role != provider.role
        ):
            outcome = Comparison.NOT_COMPARABLE
        elif _comparison_value(provider.kind, provider.value) == _comparison_value(claim.kind, claim.canonical):
            outcome = Comparison.AGREEMENT
        elif provider.kind in {ClaimKind.ISBN, ClaimKind.ASIN}:
            outcome = Comparison.INCOMPATIBLE
        else:
            outcome = Comparison.DISAGREEMENT
        links.append(ComparisonLink(claim.reference, outcome))
    return tuple(sorted(links, key=lambda link: link.local_reference))


def _comparison_value(kind: ClaimKind, value: str) -> str:
    if kind is ClaimKind.ISBN:
        return "".join(character for character in value if character.isdigit() or character in "Xx").upper()
    if kind is ClaimKind.DESCRIPTION:
        _projection_text(value, "comparison description", MAX_DESCRIPTION_CHARACTERS)
        return " ".join(unicodedata.normalize("NFC", value).split()).casefold()
    return normalize_title(value).casefold()


def _report_data(report_id: str, target: TargetFacts, file: FileFacts, local_claims: tuple[LocalClaim, ...], local_status: str, local_reasons: tuple[str, ...], providers: tuple[ProviderObservation, ...], field_results: tuple[FieldResult, ...], identifier_evidence: tuple[IdentifierEvidence, ...], cover_sources: tuple[CoverSource, ...]) -> dict[str, object]:
    return {
        "file": file.to_data(),
        "field_results": [value.to_data() for value in field_results],
        "identifier_evidence": [value.to_data() for value in identifier_evidence],
        "local": {
            "claims": [value.to_data() for value in local_claims],
            "reasons": list(local_reasons),
            "status": local_status,
        },
        "providers": [value.to_data() for value in providers],
        "report_id": report_id,
        "schema_version": REPORT_SCHEMA,
        "comparison_version": COMPARISON_VERSION,
        "covers": [value.to_data() for value in cover_sources],
        "target": target.to_data(),
    }


def _report_from_data(value: object) -> MetadataReport:
    root = _object(value, {"comparison_version", "covers", "field_results", "file", "identifier_evidence", "local", "providers", "report_id", "schema_version", "target"})
    target = _object(
        root["target"],
        {
            "connection",
            "fields",
            "item",
            "library",
        },
    )
    file = _object(root["file"], {"content_digest", "format", "identity", "selected_index"})
    local = _object(root["local"], {"claims", "reasons", "status"})
    claims = tuple(_local_claim_from_data(item) for item in _list(local["claims"]))
    providers = tuple(_provider_from_data(item) for item in _list(root["providers"]))
    field_results = tuple(_field_result_from_data(item) for item in _list(root["field_results"]))
    identifier_evidence = tuple(_identifier_evidence_from_data(item) for item in _list(root["identifier_evidence"]))
    reasons = tuple(_strings(local["reasons"]))
    covers = tuple(_cover_source_from_data(item) for item in _list(root["covers"]))
    target_facts = TargetFacts(
        target["connection"],
        target["library"],
        target["item"],
        tuple(_target_field_from_data(item) for item in _list(target["fields"])),
    )
    return MetadataReport(
        root["report_id"],
        target_facts,
        FileFacts(file["selected_index"], file["identity"], file["content_digest"], file["format"]),
        claims,
        local["status"],
        reasons,
        providers,
        field_results,
        identifier_evidence,
        covers, root["schema_version"], root["comparison_version"],
    )


def _target_field_from_data(value: object) -> TargetField:
    item = _object(value, {"destination", "state", "value"})
    destination = TargetDestination(item["destination"])
    state = TargetFieldState(item["state"])
    raw = item["value"]
    parsed: (
        str | bool | tuple[str, ...] | tuple[TargetSeries, ...] | None
    ) = None
    if state is TargetFieldState.VALUE:
        if destination is TargetDestination.SERIES:
            parsed = tuple(
                TargetSeries(
                    series["name"],
                    series["sequence"],
                )
                for source in _list(raw)
                for series in [_object(source, {"name", "sequence"})]
            )
        elif destination in {
            TargetDestination.AUTHORS,
            TargetDestination.NARRATORS,
            TargetDestination.GENRES,
            TargetDestination.TAGS,
        }:
            parsed = tuple(_strings(raw))
        else:
            parsed = raw
    elif raw is not None:
        raise ReportValueError("nonvalue target field has a value")
    return TargetField(destination, state, parsed)


def _field_result_from_data(value: object) -> FieldResult:
    item = _object(value, {"kind", "state", "sources"})
    return FieldResult(ClaimKind(item["kind"]), FieldState(item["state"]), tuple(FieldSourceResult(FieldSource(source["source"]), FieldState(source["state"])) for source in _list(item["sources"])))


def _identifier_evidence_from_data(value: object) -> IdentifierEvidence:
    item = _object(
        value,
        {
            "basis",
            "candidate_identity",
            "provider",
            "reference",
            "related_references",
            "relation",
            "work_identity",
        },
    )
    return IdentifierEvidence(
        item["reference"],
        IdentifierRelation(item["relation"]),
        IdentifierEvidenceBasis(item["basis"]),
        tuple(_strings(item["related_references"])),
        item["provider"],
        item["candidate_identity"],
        item["work_identity"],
    )


def _cover_source_from_data(value: object) -> CoverSource:
    item = _object(
        value,
        {
            "artifact",
            "kind",
            "provenance",
            "source_id",
            "state",
            "timeout_scope",
        },
    )
    artifact = None
    if item["artifact"] is not None:
        artifact = CoverArtifact(**_object(item["artifact"], {"artifact_id", "height", "media_type", "original_digest", "preview_digest", "preview_variant", "width"}))
    provenance = None
    if item["provenance"] is not None:
        source = _object(
            item["provenance"],
            {
                "context",
                "designation",
                "edition_identity",
                "provider",
                "truncation_reason",
                "work_identity",
            },
        )
        provenance = CoverProvenance(
            source["provider"],
            CoverDesignation(source["designation"]),
            source["work_identity"],
            source["edition_identity"],
            tuple(
                CoverContext(
                    CoverContextField(context["field"]),
                    context["value"],
                )
                for context in (
                    _object(value, {"field", "value"})
                    for value in source["context"]
                )
            ),
            (
                None
                if source["truncation_reason"] is None
                else CoverTruncationReason(source["truncation_reason"])
            ),
        )
    return CoverSource(
        item["source_id"],
        CoverSourceKind(item["kind"]),
        CoverSourceState(item["state"]),
        artifact,
        item["timeout_scope"],
        provenance,
    )


def _local_claim_from_data(value: object) -> LocalClaim:
    item = _object(value, {"applicability", "applicability_basis", "basis", "canonical", "checksum", "excerpt", "kind", "observed", "reference", "state", "scope", "role", "rear_provenance", "occurrence"})
    provenance = item["rear_provenance"]
    return LocalClaim(item["reference"], ClaimKind(item["kind"]), item["observed"], item["canonical"], item["excerpt"], item["applicability"], item["basis"], item["checksum"], item["applicability_basis"], ClaimScope(item["scope"]), FieldState(item["state"]), item["role"], None if provenance is None else RearClaimProvenance(**_object(provenance, {"citation", "marker", "resource_identity", "spine_ordinal", "tail_slot"})), item["occurrence"])


def _provider_from_data(value: object) -> ProviderObservation:
    item = _object(
        value,
        {
            "authority",
            "candidates",
            "input_references",
            "provider",
            "query_kind",
            "status",
        },
    )
    candidates = tuple(_candidate_from_data(candidate) for candidate in _list(item["candidates"]))
    return ProviderObservation(
        item["provider"],
        ProviderStatus(item["status"]),
        tuple(_strings(item["input_references"])),
        candidates,
        ProviderQueryKind(item["query_kind"]),
        item["authority"],
    )


def _candidate_from_data(value: object) -> ProviderCandidate:
    item = _object(value, {"claims", "evidence", "identity", "work_identity"})
    return ProviderCandidate(
        item["identity"],
        item["work_identity"],
        tuple(_provider_claim_from_data(claim) for claim in _list(item["claims"])),
        tuple(_provider_evidence_from_data(evidence) for evidence in _list(item["evidence"])),
    )


def _provider_claim_from_data(value: object) -> ProviderClaim:
    item = _object(value, {"applicability", "kind", "links", "raw_order", "reference", "role", "scope", "state", "value"})
    links = tuple(ComparisonLink(_object(link, {"local_reference", "outcome"})["local_reference"], Comparison(_object(link, {"local_reference", "outcome"})["outcome"])) for link in _list(item["links"]))
    return ProviderClaim(item["reference"], ClaimKind(item["kind"]), item["value"], FieldState(item["state"]), ClaimScope(item["scope"]), item["applicability"], item["role"], item["raw_order"], links)


def _provider_evidence_from_data(value: object) -> ProviderEvidence:
    item = _object(value, {"kind", "role", "scope", "value"})
    return ProviderEvidence(
        ProviderEvidenceKind(item["kind"]),
        item["value"],
        ClaimScope(item["scope"]),
        item["role"],
    )


def _object(value: object, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ReportValueError("report object is malformed")
    return value


def _list(value: object) -> list[Any]:
    if type(value) is not list:
        raise ReportValueError("report list is malformed")
    return value


def _strings(value: object) -> list[str]:
    result = _list(value)
    if any(type(item) is not str for item in result):
        raise ReportValueError("report string list is malformed")
    return result


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReportValueError("report object contains a duplicate field")
        result[key] = value
    return result


def _reject_number(_: str) -> float:
    raise ReportValueError("report number is invalid")


def _name(value: object, label: str) -> None:
    if type(value) is not str or _NAME.fullmatch(value) is None:
        raise ReportValueError(f"{label} is invalid")


def _text(value: object, label: str, maximum: int) -> None:
    if type(value) is not str or not value or len(value) > maximum or value.strip() != value or "\x00" in value or any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value):
        raise ReportValueError(f"{label} is invalid")


def _projection_text(value: object, label: str, maximum: int) -> None:
    try:
        validate_projection_text(value, label, maximum)
    except EvidenceValueError as error:
        raise ReportValueError(f"{label} is invalid") from error


def _sentinel_state(value: str) -> FieldState | None:
    if re.fullmatch(r"unsafe-source-text-sha256:[0-9a-f]{64}", value):
        return FieldState.UNSUPPORTED
    if re.fullmatch(r"oversized-source-text-sha256:[0-9a-f]{64}", value):
        return FieldState.OVERSIZED
    return None


def _valid_isbn(value: str, checksum: str) -> bool:
    if checksum == "valid_isbn_13" and len(value) == 13 and value.isascii() and value.isdigit():
        expected = (10 - sum((1 if index % 2 == 0 else 3) * int(character) for index, character in enumerate(value[:12])) % 10) % 10
        return int(value[-1]) == expected
    if checksum == "valid_isbn_10" and len(value) == 10 and value[:9].isascii() and value[:9].isdigit() and (value[-1].isascii() and value[-1].isdigit() or value[-1] == "X"):
        return sum((10 - index) * (10 if character == "X" else int(character)) for index, character in enumerate(value)) % 11 == 0
    return False


__all__ = [
    "ClaimKind", "ClaimScope", "COMPARISON_VERSION", "Comparison", "ComparisonLink", "FieldResult", "FieldState", "FileFacts", "LocalClaim", "MAX_CANDIDATES",
    "IdentifierEvidence", "IdentifierEvidenceBasis", "IdentifierRelation",
    "MAX_REPORT_BYTES", "MetadataReport", "ProviderCandidate", "ProviderClaim",
    "ProviderEvidence", "ProviderEvidenceKind",
    "ProviderObservation", "ProviderQueryKind", "ProviderStatus",
    "REPORT_SCHEMA", "ReportValueError",
    "TargetDestination", "TargetFacts", "TargetField", "TargetFieldState",
    "TargetSeries", "canonical_json", "claim_scope", "comparison_links",
    "default_target_fields",
    "derive_identifier_evidence", "isbn_family", "normalize_title",
    "stable_reference", "validate_observed_title",
]
