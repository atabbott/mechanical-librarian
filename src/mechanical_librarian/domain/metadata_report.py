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


REPORT_SCHEMA = "metadata-report-v2"
COMPARISON_VERSION = "same-kind-v1"
# F09j's report contract admits rich, source-attributed candidates.  The
# physical SQLite schema separately fingerprints this exact shape, so an F09i
# database cannot be opened as this schema despite the stable document label.
MAX_REPORT_BYTES = 2_097_152
MAX_CANDIDATES = 5
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


class Comparison(StrEnum):
    AGREEMENT = "agreement"
    DISAGREEMENT = "disagreement"
    INCOMPATIBLE = "incompatible"
    NOT_COMPARABLE = "not_comparable"


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
    current_title: str

    def __post_init__(self) -> None:
        _name(self.connection, "connection")
        _name(self.library, "library")
        _text(self.item, "item", 512)
        validate_observed_title(self.current_title)

    def to_data(self) -> dict[str, str]:
        return {
            "connection": self.connection,
            "current_title": self.current_title,
            "item": self.item,
            "library": self.library,
        }


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
class ProviderCandidate:
    identity: str
    claims: tuple[ProviderClaim, ...]

    def __post_init__(self) -> None:
        _text(self.identity, "provider candidate identity", 128)
        if type(self.claims) is not tuple or any(type(value) is not ProviderClaim for value in self.claims):
            raise ReportValueError("provider candidate claims are invalid")
        references = tuple(value.reference for value in self.claims)
        if len(set(references)) != len(references):
            raise ReportValueError("provider candidate claim references are duplicated")
        if tuple(value.raw_order for value in self.claims) != tuple(sorted(value.raw_order for value in self.claims)):
            raise ReportValueError("provider candidate claims are not in raw source order")

    def to_data(self) -> dict[str, object]:
        return {"claims": [value.to_data() for value in self.claims], "identity": self.identity}


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    provider: str
    status: ProviderStatus
    input_references: tuple[str, ...] = ()
    candidates: tuple[ProviderCandidate, ...] = ()

    def __post_init__(self) -> None:
        _name(self.provider, "provider")
        if type(self.status) is not ProviderStatus:
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
            "status": self.status.value,
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
    ) -> Self:
        local_status = "candidate" if any(value.basis == "opening_matter" for value in local_claims) else "no_candidate"
        results = field_results if field_results is not None else derive_field_results(local_claims)
        data = _report_data("", target, file, local_claims, local_status, local_reasons, providers, results)
        data.pop("report_id")
        identity = f"report_{hashlib.sha256(canonical_json(data).encode('utf-8')).hexdigest()}"
        return cls(identity, target, file, local_claims, local_status, local_reasons, providers, results)

    def _identity_data(self) -> dict[str, object]:
        value = self.to_data()
        value.pop("report_id")
        return value

    def to_data(self) -> dict[str, object]:
        return _report_data(self.report_id, self.target, self.file, self.local_claims, self.local_status, self.local_reasons, self.providers, self.field_results)

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


def _report_data(report_id: str, target: TargetFacts, file: FileFacts, local_claims: tuple[LocalClaim, ...], local_status: str, local_reasons: tuple[str, ...], providers: tuple[ProviderObservation, ...], field_results: tuple[FieldResult, ...]) -> dict[str, object]:
    return {
        "file": file.to_data(),
        "field_results": [value.to_data() for value in field_results],
        "local": {
            "claims": [value.to_data() for value in local_claims],
            "reasons": list(local_reasons),
            "status": local_status,
        },
        "providers": [value.to_data() for value in providers],
        "report_id": report_id,
        "schema_version": REPORT_SCHEMA,
        "comparison_version": COMPARISON_VERSION,
        "target": target.to_data(),
    }


def _report_from_data(value: object) -> MetadataReport:
    root = _object(value, {"comparison_version", "field_results", "file", "local", "providers", "report_id", "schema_version", "target"})
    target = _object(root["target"], {"connection", "current_title", "item", "library"})
    file = _object(root["file"], {"content_digest", "format", "identity", "selected_index"})
    local = _object(root["local"], {"claims", "reasons", "status"})
    claims = tuple(_local_claim_from_data(item) for item in _list(local["claims"]))
    providers = tuple(_provider_from_data(item) for item in _list(root["providers"]))
    field_results = tuple(_field_result_from_data(item) for item in _list(root["field_results"]))
    reasons = tuple(_strings(local["reasons"]))
    return MetadataReport(
        root["report_id"],
        TargetFacts(target["connection"], target["library"], target["item"], target["current_title"]),
        FileFacts(file["selected_index"], file["identity"], file["content_digest"], file["format"]),
        claims,
        local["status"],
        reasons,
        providers,
        field_results,
        root["schema_version"], root["comparison_version"],
    )


def _field_result_from_data(value: object) -> FieldResult:
    item = _object(value, {"kind", "state", "sources"})
    return FieldResult(ClaimKind(item["kind"]), FieldState(item["state"]), tuple(FieldSourceResult(FieldSource(source["source"]), FieldState(source["state"])) for source in _list(item["sources"])))


def _local_claim_from_data(value: object) -> LocalClaim:
    item = _object(value, {"applicability", "applicability_basis", "basis", "canonical", "checksum", "excerpt", "kind", "observed", "reference", "state", "scope", "role", "rear_provenance", "occurrence"})
    provenance = item["rear_provenance"]
    return LocalClaim(item["reference"], ClaimKind(item["kind"]), item["observed"], item["canonical"], item["excerpt"], item["applicability"], item["basis"], item["checksum"], item["applicability_basis"], ClaimScope(item["scope"]), FieldState(item["state"]), item["role"], None if provenance is None else RearClaimProvenance(**_object(provenance, {"citation", "marker", "resource_identity", "spine_ordinal", "tail_slot"})), item["occurrence"])


def _provider_from_data(value: object) -> ProviderObservation:
    item = _object(value, {"candidates", "input_references", "provider", "status"})
    candidates = tuple(_candidate_from_data(candidate) for candidate in _list(item["candidates"]))
    return ProviderObservation(item["provider"], ProviderStatus(item["status"]), tuple(_strings(item["input_references"])), candidates)


def _candidate_from_data(value: object) -> ProviderCandidate:
    item = _object(value, {"claims", "identity"})
    return ProviderCandidate(item["identity"], tuple(_provider_claim_from_data(claim) for claim in _list(item["claims"])))


def _provider_claim_from_data(value: object) -> ProviderClaim:
    item = _object(value, {"applicability", "kind", "links", "raw_order", "reference", "role", "scope", "state", "value"})
    links = tuple(ComparisonLink(_object(link, {"local_reference", "outcome"})["local_reference"], Comparison(_object(link, {"local_reference", "outcome"})["outcome"])) for link in _list(item["links"]))
    return ProviderClaim(item["reference"], ClaimKind(item["kind"]), item["value"], FieldState(item["state"]), ClaimScope(item["scope"]), item["applicability"], item["role"], item["raw_order"], links)


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
    "MAX_REPORT_BYTES", "MetadataReport", "ProviderCandidate", "ProviderClaim",
    "ProviderObservation", "ProviderStatus", "REPORT_SCHEMA", "ReportValueError",
    "TargetFacts", "canonical_json", "claim_scope", "comparison_links", "normalize_title",
    "stable_reference", "validate_observed_title",
]
