"""Closed, neutral metadata-evidence report for one selected EPUB."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
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
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
    diagnostics_are_canonical,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision


REPORT_SCHEMA = "metadata-report-v21"
COMPARISON_VERSION = "closed-title-creator-publication-v4"
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
_ASIN = re.compile(r"^B[0-9A-Z]{9}$")
_LANE_IDENTITY = re.compile(r"^lane_[0-9a-f]{64}$")
_LANE_CANDIDATE_REFERENCE = re.compile(r"^lane_candidate_[0-9a-f]{64}$")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_PROVIDER_ACCESS_TIME = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{6}Z$"
)


class ReportValueError(ValueError):
    """A report or report input is outside the closed 0.4 contract."""


def provider_access_time(timestamp: float) -> str:
    """Return one canonical UTC access time from an injected epoch clock."""
    if type(timestamp) not in {int, float} or isinstance(timestamp, bool):
        raise ReportValueError("provider access clock is invalid")
    try:
        finite = math.isfinite(timestamp)
    except OverflowError:
        finite = False
    if not finite:
        raise ReportValueError("provider access clock is invalid")
    try:
        observed = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise ReportValueError("provider access clock is invalid") from None
    return observed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def validate_provider_access_time(value: object) -> str:
    """Validate the exact persisted provider-access timestamp language."""
    if type(value) is not str or _PROVIDER_ACCESS_TIME.fullmatch(value) is None:
        raise ReportValueError("provider access time is invalid")
    try:
        observed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise ReportValueError("provider access time is invalid") from None
    if (
        observed.isoformat(timespec="microseconds").replace("+00:00", "Z")
        != value
    ):
        raise ReportValueError("provider access time is invalid")
    return value


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
    DATE_EVENT = "date_event"
    CONTEXTUAL_CREDIT = "contextual_credit"
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
    PUBLICATION_DATE_TEXT = "publication_date_text"
    EDITION_WORK_RELATION = "edition_work_relation"
    FIRST_PUBLICATION_DATE = "first_publication_date"
    EDITION_COVER_ID = "edition_cover_id"
    WORK_COVER_ID = "work_cover_id"
    RESPONSIBILITY_STATEMENT = "responsibility_statement"
    DISTRIBUTOR = "distributor"
    EDITION_STATEMENT = "edition_statement"
    TRANSLATION_STATEMENT = "translation_statement"
    EDITION_DESCRIPTION = "edition_description"
    SEARCH_PUBLISHER = "search_publisher"
    SEARCH_PUBLICATION_DATE = "search_publication_date"
    SEARCH_LANGUAGE = "search_language"
    SEARCH_EDITION = "search_edition"


class DateLinkConfidence(StrEnum):
    """Whether a provider date is safely linked to its stated event and scope."""

    EXPLICIT = "explicit"
    UNRESOLVED = "unresolved"


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
    INCOMPLETE = "incomplete"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    CANDIDATE_LIMIT_EXCEEDED = "candidate_limit_exceeded"
    QUERY_LIMIT_EXCEEDED = "query_limit_exceeded"


@dataclass(frozen=True, slots=True)
class ProviderRequestAccounting:
    """Closed aggregate-safe accounting for one provider observation."""

    logical_requests: int = 0
    initial_dispatches: int = 0
    retry_dispatches: int = 0
    replays: int = 0
    undispatched_incomplete: int = 0

    def __post_init__(self) -> None:
        values = (
            self.logical_requests, self.initial_dispatches,
            self.retry_dispatches, self.replays, self.undispatched_incomplete,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ReportValueError("provider request accounting is invalid")
        if self.logical_requests != (
            self.initial_dispatches + self.replays + self.undispatched_incomplete
        ):
            raise ReportValueError("provider logical request accounting is inconsistent")
        if self.retry_dispatches and not self.initial_dispatches:
            raise ReportValueError("provider retry request accounting is inconsistent")

    def to_data(self) -> dict[str, int]:
        return {
            "initial_dispatches": self.initial_dispatches,
            "logical_requests": self.logical_requests,
            "replays": self.replays,
            "retry_dispatches": self.retry_dispatches,
            "undispatched_incomplete": self.undispatched_incomplete,
        }


class ProviderQueryKind(StrEnum):
    NONE = "none"
    DISCOVERY = "discovery"
    ISBN = "isbn"
    WORK = "work"


class Comparison(StrEnum):
    AGREEMENT = "agreement"
    DISAGREEMENT = "disagreement"
    INCOMPATIBLE = "incompatible"
    NOT_COMPARABLE = "not_comparable"


class ComparisonMethod(StrEnum):
    """Closed explanation for one deterministic comparison outcome."""

    NONE = "none"
    EXACT = "exact"
    CLOSED_FIELD_NORMALIZATION = "closed_field_normalization"
    UNICODE_CASE_WHITESPACE = "unicode_case_whitespace"
    TYPOGRAPHIC_PUNCTUATION = "typographic_punctuation"
    CONSERVATIVE_COMMA_INVERSION = "conservative_comma_inversion"
    STRUCTURAL_IDENTITY_BINDING = "structural_identity_binding"
    DATE_PRECISION_COMPATIBILITY = "date_precision_compatibility"


class PublicationDateRelation(StrEnum):
    """Closed relation between two bounded publication-date observations."""

    EXACT = "exact"
    MONTH_COMPATIBLE = "month_compatible"
    YEAR_COMPATIBLE = "year_compatible"
    FINER_CONFLICT = "finer_conflict"
    EVENT_INCOMPATIBLE = "event_incompatible"
    MANIFESTATION_INCOMPATIBLE = "manifestation_incompatible"


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
    method: ComparisonMethod = ComparisonMethod.NONE
    publication_date_relation: PublicationDateRelation | None = None
    shared_date_precision: DatePrecision | None = None

    def __post_init__(self) -> None:
        date_relation = self.publication_date_relation
        date_precision = self.shared_date_precision
        if date_relation is None:
            date_valid = date_precision is None
        else:
            date_valid = (
                type(date_relation) is PublicationDateRelation
                and (
                    (
                        date_relation is PublicationDateRelation.EXACT
                        and date_precision
                        in {
                            DatePrecision.YEAR,
                            DatePrecision.MONTH,
                            DatePrecision.DAY,
                        }
                    )
                    or (
                        date_relation is PublicationDateRelation.FINER_CONFLICT
                        and date_precision
                        in {
                            None,
                            DatePrecision.YEAR,
                            DatePrecision.MONTH,
                            DatePrecision.DAY,
                        }
                    )
                    or (
                        date_relation is PublicationDateRelation.MONTH_COMPATIBLE
                        and date_precision is DatePrecision.MONTH
                    )
                    or (
                        date_relation is PublicationDateRelation.YEAR_COMPATIBLE
                        and date_precision is DatePrecision.YEAR
                    )
                    or (
                        date_relation
                        in {
                            PublicationDateRelation.EVENT_INCOMPATIBLE,
                            PublicationDateRelation.MANIFESTATION_INCOMPATIBLE,
                        }
                        and date_precision is None
                    )
                )
            )
        if (
            _REFERENCE.fullmatch(self.local_reference) is None
            or not self.local_reference.startswith("local_")
            or type(self.outcome) is not Comparison
            or type(self.method) is not ComparisonMethod
            or not date_valid
            or (
                self.outcome is Comparison.AGREEMENT
                and self.method is ComparisonMethod.NONE
            )
            or (
                self.outcome is not Comparison.AGREEMENT
                and self.method is not ComparisonMethod.NONE
            )
            or (
                date_relation
                in {
                    PublicationDateRelation.EXACT,
                    PublicationDateRelation.MONTH_COMPATIBLE,
                    PublicationDateRelation.YEAR_COMPATIBLE,
                }
                and self.outcome is not Comparison.AGREEMENT
            )
            or (
                date_relation is PublicationDateRelation.FINER_CONFLICT
                and self.outcome is not Comparison.DISAGREEMENT
            )
            or (
                date_relation
                in {
                    PublicationDateRelation.EVENT_INCOMPATIBLE,
                    PublicationDateRelation.MANIFESTATION_INCOMPATIBLE,
                }
                and self.outcome is not Comparison.NOT_COMPARABLE
            )
        ):
            raise ReportValueError("comparison link is invalid")

    def to_data(self) -> dict[str, str | None]:
        return {
            "local_reference": self.local_reference,
            "method": self.method.value,
            "outcome": self.outcome.value,
            "publication_date_relation": (
                None
                if self.publication_date_relation is None
                else self.publication_date_relation.value
            ),
            "shared_date_precision": (
                None
                if self.shared_date_precision is None
                else self.shared_date_precision.value
            ),
        }


def canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError, RecursionError) as error:
        raise ReportValueError("report value is not serializable") from error


def stable_reference(prefix: str, value: object) -> str:
    if prefix not in {"local", "provider"}:
        raise ReportValueError("claim reference prefix is invalid")
    return f"{prefix}_{hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()}"


def provider_lane_candidate_reference(
    provider: str,
    lane_identity: str,
    edition_identity: str,
    work_identity: str,
) -> str:
    return "lane_candidate_" + hashlib.sha256(
        canonical_json(
            {
                "edition_identity": edition_identity,
                "lane_identity": lane_identity,
                "provider": provider,
                "work_identity": work_identity,
            }
        ).encode("utf-8")
    ).hexdigest()


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


def normalize_date_value(value: str) -> tuple[str, DatePrecision]:
    """Validate one ISO-like lexical date without increasing its precision."""
    _projection_text(value, "date lexical value", MAX_SCALAR_CHARACTERS)
    if re.fullmatch(r"\d{4}", value):
        year = int(value)
        if not 1 <= year <= 9999:
            raise ReportValueError("date lexical value is invalid")
        return value, DatePrecision.YEAR
    if re.fullmatch(r"\d{4}-\d{2}", value):
        try:
            date(int(value[:4]), int(value[5:7]), 1)
        except ValueError as error:
            raise ReportValueError("date lexical value is invalid") from error
        return value, DatePrecision.MONTH
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value).isoformat(), DatePrecision.DAY
        except ValueError as error:
            raise ReportValueError("date lexical value is invalid") from error
    if "T" not in value:
        raise ReportValueError("date lexical value is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReportValueError("date lexical value is invalid") from error
    offset = parsed.utcoffset()
    if offset is not None and abs(offset) > timedelta(hours=14):
        raise ReportValueError("date lexical value is invalid")
    return parsed.isoformat(), DatePrecision.TIMESTAMP


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

    @classmethod
    def from_data(cls, raw: object) -> Self:
        """Strictly reconstruct the public target envelope from canonical data."""
        try:
            value = _object(raw, {"connection", "fields", "item", "library"})
            return cls(
                value["connection"], value["library"], value["item"],
                tuple(_target_field_from_data(item) for item in _list(value["fields"])),
            )
        except (KeyError, TypeError, ValueError, ReportValueError) as error:
            raise ReportValueError("target facts are invalid") from error


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
    date_event: DateEvent | None = None
    date_precision: DatePrecision | None = None

    def __post_init__(self) -> None:
        if _REFERENCE.fullmatch(self.reference) is None or not self.reference.startswith("local_"):
            raise ReportValueError("local claim reference is invalid")
        if type(self.kind) is not ClaimKind or type(self.state) is not FieldState or type(self.scope) is not ClaimScope:
            raise ReportValueError("local claim kind is invalid")
        if self.basis == "target_metadata" and self.kind is not ClaimKind.TITLE:
            raise ReportValueError("target metadata claim kind is invalid")
        if self.role is not None:
            if self.kind not in {
                ClaimKind.CREATOR,
                ClaimKind.CONTRIBUTOR,
                ClaimKind.CONTEXTUAL_CREDIT,
            }:
                raise ReportValueError("local claim role is invalid")
            try:
                validate_role_projection(self.role)
            except EvidenceValueError as error:
                raise ReportValueError("local claim role is invalid") from error
        if self.rear_provenance is not None and type(self.rear_provenance) is not RearClaimProvenance:
            raise ReportValueError("rear claim provenance is invalid")
        rear_kinds = {
            ClaimKind.TITLE,
            ClaimKind.SUBTITLE,
            ClaimKind.CREATOR,
            ClaimKind.CONTRIBUTOR,
            ClaimKind.ISBN,
            ClaimKind.PUBLICATION_DATE,
            ClaimKind.DATE_EVENT,
            ClaimKind.CONTEXTUAL_CREDIT,
            ClaimKind.PUBLISHER,
            ClaimKind.IMPRINT,
        }
        if self.rear_provenance is not None and (
            self.basis != "opening_matter" or self.kind not in rear_kinds
        ):
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
        is_date = self.kind in {ClaimKind.PUBLICATION_DATE, ClaimKind.DATE_EVENT}
        if is_date != (type(self.date_event) is DateEvent):
            raise ReportValueError("local date claim typing is inconsistent")
        if (
            is_date
            and self.state is FieldState.VALID
            and type(self.date_precision) is not DatePrecision
        ) or (not is_date and self.date_precision is not None):
            raise ReportValueError("local date precision is inconsistent")
        if (
            self.kind is ClaimKind.PUBLICATION_DATE
            and self.date_event is not DateEvent.MANIFESTATION_PUBLICATION
        ):
            raise ReportValueError("local publication date event is inconsistent")
        if is_date and self.scope is not claim_scope(self.kind, self.date_event):
            raise ReportValueError("local date claim scope is inconsistent")
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
        date_event: DateEvent | None = None,
        date_precision: DatePrecision | None = None,
    ) -> Self:
        resolved_scope = (
            scope if scope is not None else claim_scope(kind, date_event)
        )
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
            "scope": (
                resolved_scope.value
                if type(resolved_scope) is ClaimScope
                else ""
            ),
            "role": role,
            "rear_provenance": None if rear_provenance is None else rear_provenance.to_data(),
            "occurrence": occurrence,
            "date_event": None if date_event is None else date_event.value,
            "date_precision": (
                None if date_precision is None else date_precision.value
            ),
        }
        return cls(stable_reference("local", value), kind, observed, canonical, excerpt, applicability, basis, checksum, applicability_basis, resolved_scope, state, role, rear_provenance, occurrence, date_event, date_precision)

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
            "date_event": (
                None if self.date_event is None else self.date_event.value
            ),
            "date_precision": (
                None if self.date_precision is None else self.date_precision.value
            ),
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
    date_event: DateEvent | None = None
    date_precision: DatePrecision | None = None
    date_lexical: str | None = None
    date_confidence: DateLinkConfidence | None = None

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
        if (
            self.kind is ClaimKind.ASIN
            and self.value is not None
            and _ASIN.fullmatch(self.value) is None
        ):
            raise ReportValueError("provider ASIN claim is invalid")
        is_date = self.kind is ClaimKind.PUBLICATION_DATE
        if is_date != (type(self.date_event) is DateEvent):
            raise ReportValueError("provider date claim typing is inconsistent")
        if is_date != (type(self.date_confidence) is DateLinkConfidence):
            raise ReportValueError("provider date claim confidence is inconsistent")
        if (
            is_date
            and self.state is FieldState.VALID
            and (
                type(self.date_precision) is not DatePrecision
                or type(self.date_lexical) is not str
                or (self.value, self.date_precision)
                != normalize_date_value(self.date_lexical)
            )
        ) or (
            (not is_date or self.state is not FieldState.VALID)
            and (self.date_precision is not None or self.date_lexical is not None)
        ):
            raise ReportValueError("provider date claim precision is inconsistent")
        if is_date and (
            self.date_event is not DateEvent.MANIFESTATION_PUBLICATION
            or self.scope is not ClaimScope.EDITION
            or self.date_confidence is not DateLinkConfidence.EXPLICIT
        ):
            raise ReportValueError("provider publication date linkage is inconsistent")
        if tuple(link.local_reference for link in self.links) != tuple(sorted(link.local_reference for link in self.links)) or len({link.local_reference for link in self.links}) != len(self.links):
            raise ReportValueError("provider comparison links are invalid")
        if any(
            (link.publication_date_relation is None) == is_date
            for link in self.links
        ):
            raise ReportValueError("provider date comparison links are inconsistent")

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
        date_event: DateEvent | None = None,
        date_precision: DatePrecision | None = None,
        date_lexical: str | None = None,
        date_confidence: DateLinkConfidence | None = None,
    ) -> Self:
        if (
            kind is ClaimKind.ASIN
            and state is FieldState.VALID
            and type(value) is str
        ):
            value = value.upper()
        if kind is ClaimKind.PUBLICATION_DATE:
            date_event = (
                DateEvent.MANIFESTATION_PUBLICATION
                if date_event is None
                else date_event
            )
            date_confidence = (
                DateLinkConfidence.EXPLICIT
                if date_confidence is None
                else date_confidence
            )
            if state is FieldState.VALID and value is not None:
                date_lexical = value if date_lexical is None else date_lexical
                normalized, inferred_precision = normalize_date_value(date_lexical)
                value = normalized
                date_precision = (
                    inferred_precision if date_precision is None else date_precision
                )
        data = _provider_reference_data(
            provider,
            candidate,
            kind,
            value,
            state,
            scope,
            applicability,
            role,
            raw_order,
            date_event,
            date_precision,
            date_lexical,
            date_confidence,
        )
        return cls(
            stable_reference("provider", data),
            kind,
            value,
            state,
            scope,
            applicability,
            role,
            raw_order,
            links,
            date_event,
            date_precision,
            date_lexical,
            date_confidence,
        )

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
            "date_event": (
                None if self.date_event is None else self.date_event.value
            ),
            "date_precision": (
                None if self.date_precision is None else self.date_precision.value
            ),
            "date_lexical": self.date_lexical,
            "date_confidence": (
                None if self.date_confidence is None else self.date_confidence.value
            ),
        }


@dataclass(frozen=True, slots=True)
class ProviderEvidence:
    kind: ProviderEvidenceKind
    value: str
    scope: ClaimScope
    role: str | None = None
    date_event: DateEvent | None = None
    date_precision: DatePrecision | None = None
    date_normalized: str | None = None
    date_confidence: DateLinkConfidence | None = None
    source_ordinal: int = 0
    links: tuple[ComparisonLink, ...] = ()

    def __post_init__(self) -> None:
        if type(self.kind) is not ProviderEvidenceKind or type(self.scope) is not ClaimScope:
            raise ReportValueError("provider rich evidence is invalid")
        if (
            type(self.source_ordinal) is not int
            or not 0 <= self.source_ordinal <= 2_147_483_647
            or type(self.links) is not tuple
            or any(type(value) is not ComparisonLink for value in self.links)
            or tuple(value.local_reference for value in self.links)
            != tuple(sorted({value.local_reference for value in self.links}))
        ):
            raise ReportValueError("provider rich evidence source ordinal is invalid")
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
        date_events = {
            ProviderEvidenceKind.COPYRIGHT_DATE: DateEvent.COPYRIGHT,
            ProviderEvidenceKind.FIRST_PUBLICATION_DATE: (
                DateEvent.WORK_FIRST_PUBLICATION
            ),
            ProviderEvidenceKind.LAST_MODIFIED: DateEvent.MODIFICATION,
        }
        is_publication_text = (
            self.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
        )
        expected_event = date_events.get(self.kind)
        if is_publication_text and self.date_event is None:
            expected_event = DateEvent.UNKNOWN
        if expected_event is not None and self.date_event is None:
            object.__setattr__(self, "date_event", expected_event)
        if (expected_event is not None or is_publication_text) and self.date_confidence is None:
            object.__setattr__(
                self,
                "date_confidence",
                (
                    DateLinkConfidence.UNRESOLVED
                    if is_publication_text
                    else DateLinkConfidence.EXPLICIT
                ),
            )
        if (expected_event is not None or is_publication_text) and self.date_normalized is None:
            try:
                normalized, precision = normalize_date_value(self.value)
            except ReportValueError:
                pass
            else:
                object.__setattr__(self, "date_normalized", normalized)
                if self.date_precision is None:
                    object.__setattr__(self, "date_precision", precision)
        is_date = expected_event is not None or is_publication_text
        if is_date != (type(self.date_event) is DateEvent):
            raise ReportValueError("provider date evidence typing is inconsistent")
        if is_date:
            expected_confidence = (
                DateLinkConfidence.UNRESOLVED
                if is_publication_text
                else DateLinkConfidence.EXPLICIT
            )
            if (
                (
                    is_publication_text
                    and self.date_event
                    not in {
                        DateEvent.UNKNOWN,
                        DateEvent.MANIFESTATION_PUBLICATION,
                    }
                )
                or (
                    not is_publication_text
                    and self.date_event is not expected_event
                )
                or self.date_confidence is not expected_confidence
                or (self.date_precision is None) != (self.date_normalized is None)
                or (
                    self.date_normalized is not None
                    and normalize_date_value(self.value)
                    != (self.date_normalized, self.date_precision)
                )
            ):
                raise ReportValueError("provider date evidence is inconsistent")
        elif any(
            value is not None
            for value in (
                self.date_precision,
                self.date_normalized,
                self.date_confidence,
            )
        ):
            raise ReportValueError("provider date evidence is inconsistent")
        if any(
            link.publication_date_relation is None for link in self.links
        ) or (not is_date and self.links):
            raise ReportValueError("provider date evidence links are inconsistent")

    def to_data(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "role": self.role,
            "scope": self.scope.value,
            "value": self.value,
            "date_event": (
                None if self.date_event is None else self.date_event.value
            ),
            "date_precision": (
                None if self.date_precision is None else self.date_precision.value
            ),
            "date_normalized": self.date_normalized,
            "date_confidence": (
                None if self.date_confidence is None else self.date_confidence.value
            ),
            "source_ordinal": self.source_ordinal,
            "links": [value.to_data() for value in self.links],
        }


@dataclass(frozen=True, slots=True)
class ProviderIdentityBinding:
    """One structurally parsed provider name-to-identity relationship."""

    claim_reference: str
    identity: str
    scope: ClaimScope
    role: str
    source_ordinal: int = 0

    def __post_init__(self) -> None:
        if (
            _REFERENCE.fullmatch(self.claim_reference) is None
            or not self.claim_reference.startswith("provider_")
            or type(self.scope) is not ClaimScope
            or self.role != "author"
            or type(self.source_ordinal) is not int
            or not 0 <= self.source_ordinal <= 2_147_483_647
        ):
            raise ReportValueError("provider identity binding is invalid")
        _text(self.identity, "provider identity binding identity", 128)
        try:
            validate_role_projection(self.role)
        except EvidenceValueError as error:
            raise ReportValueError("provider identity binding role is invalid") from error

    def to_data(self) -> dict[str, object]:
        return {
            "claim_reference": self.claim_reference,
            "identity": self.identity,
            "role": self.role,
            "scope": self.scope.value,
            "source_ordinal": self.source_ordinal,
        }


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    identity: str
    work_identity: str
    claims: tuple[ProviderClaim, ...]
    evidence: tuple[ProviderEvidence, ...] = ()
    identity_bindings: tuple[ProviderIdentityBinding, ...] = ()

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
        claims_by_reference = {value.reference: value for value in self.claims}
        if (
            type(self.identity_bindings) is not tuple
            or len(self.identity_bindings) > MAX_CONTRIBUTIONS
            or any(
                type(value) is not ProviderIdentityBinding
                for value in self.identity_bindings
            )
            or len(set(self.identity_bindings)) != len(self.identity_bindings)
            or len(
                {value.claim_reference for value in self.identity_bindings}
            )
            != len(self.identity_bindings)
            or len(
                {
                    (value.scope, value.role, value.source_ordinal)
                    for value in self.identity_bindings
                }
            )
            != len(self.identity_bindings)
            or self.identity_bindings
            != tuple(
                sorted(
                    self.identity_bindings,
                    key=lambda value: (
                        value.scope.value,
                        value.role,
                        value.source_ordinal,
                        value.identity,
                        value.claim_reference,
                    ),
                )
            )
        ):
            raise ReportValueError("provider identity bindings are invalid")
        for binding in self.identity_bindings:
            claim = claims_by_reference.get(binding.claim_reference)
            if (
                claim is None
                or claim.kind is not ClaimKind.CREATOR
                or claim.state is not FieldState.VALID
                or claim.value is None
                or claim.scope is not binding.scope
                or claim.role != binding.role
            ):
                raise ReportValueError("provider identity binding is misbound")
        # Evidence is only meaningful when it remains bound to this exact
        # edition/work pair.  Keep these invariants here so replay and every
        # downstream consumer reject a forged cross-candidate attachment.
        edition_only = {
            ProviderEvidenceKind.EDITION_WORK_RELATION,
            ProviderEvidenceKind.EDITION_COVER_ID,
            ProviderEvidenceKind.PAGE_COUNT,
            ProviderEvidenceKind.PAGINATION,
            ProviderEvidenceKind.PHYSICAL_FORMAT,
            ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
            ProviderEvidenceKind.COPYRIGHT_DATE,
            ProviderEvidenceKind.RESPONSIBILITY_STATEMENT,
            ProviderEvidenceKind.DISTRIBUTOR,
            ProviderEvidenceKind.EDITION_STATEMENT,
            ProviderEvidenceKind.TRANSLATION_STATEMENT,
            ProviderEvidenceKind.EDITION_DESCRIPTION,
        }
        work_only = {
            ProviderEvidenceKind.WORK_COVER_ID,
            ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
            ProviderEvidenceKind.AUTHOR_IDENTITY,
            ProviderEvidenceKind.CONTRIBUTOR_IDENTITY,
            ProviderEvidenceKind.SEARCH_PUBLISHER,
            ProviderEvidenceKind.SEARCH_PUBLICATION_DATE,
            ProviderEvidenceKind.SEARCH_LANGUAGE,
            ProviderEvidenceKind.SEARCH_EDITION,
        }
        if any(
            (value.kind in edition_only and value.scope is not ClaimScope.EDITION)
            or (value.kind in work_only and value.scope is not ClaimScope.WORK)
            or (
                value.kind is ProviderEvidenceKind.EDITION_WORK_RELATION
                and value.value != self.work_identity
            )
            for value in self.evidence
        ):
            raise ReportValueError("provider rich evidence is misbound")
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
                        (
                            ""
                            if value.date_event is None
                            else value.date_event.value
                        ),
                        (
                            ""
                            if value.date_precision is None
                            else value.date_precision.value
                        ),
                        value.date_normalized or "",
                        (
                            ""
                            if value.date_confidence is None
                            else value.date_confidence.value
                        ),
                        value.source_ordinal,
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
            "identity_bindings": [
                value.to_data() for value in self.identity_bindings
            ],
            "work_identity": self.work_identity,
        }


@dataclass(frozen=True, slots=True)
class ProviderLaneCandidate:
    reference: str
    edition_identity: str
    work_identity: str

    def __post_init__(self) -> None:
        if _LANE_CANDIDATE_REFERENCE.fullmatch(self.reference) is None:
            raise ReportValueError("provider lane candidate reference is invalid")
        _text(self.edition_identity, "provider candidate identity", 128)
        _text(self.work_identity, "provider candidate work identity", 128)

    def to_data(self) -> dict[str, str]:
        return {
            "edition_identity": self.edition_identity,
            "reference": self.reference,
            "work_identity": self.work_identity,
        }


@dataclass(frozen=True, slots=True)
class ProviderLaneObservation:
    identity: str
    kind: ProviderQueryKind
    status: ProviderStatus
    input_references: tuple[str, ...] = ()
    matched_family: str | None = None
    candidates: tuple[ProviderLaneCandidate, ...] = ()

    def __post_init__(self) -> None:
        if (
            _LANE_IDENTITY.fullmatch(self.identity) is None
            or self.kind not in {ProviderQueryKind.ISBN, ProviderQueryKind.WORK}
            or type(self.status) is not ProviderStatus
        ):
            raise ReportValueError("provider lane is invalid")
        if (
            type(self.input_references) is not tuple
            or tuple(sorted(set(self.input_references))) != self.input_references
            or any(
                _REFERENCE.fullmatch(value) is None or not value.startswith("local_")
                for value in self.input_references
            )
        ):
            raise ReportValueError("provider lane input references are invalid")
        if self.kind is ProviderQueryKind.ISBN:
            if (
                type(self.matched_family) is not str
                or re.fullmatch(r"97[89][0-9]{10}", self.matched_family) is None
                or not self.input_references
            ):
                raise ReportValueError("provider ISBN lane is invalid")
        elif self.matched_family is not None or not self.input_references:
            raise ReportValueError("provider work lane is invalid")
        if (
            type(self.candidates) is not tuple
            or any(type(value) is not ProviderLaneCandidate for value in self.candidates)
            or tuple(value.reference for value in self.candidates)
            != tuple(sorted({value.reference for value in self.candidates}))
            or (self.status is ProviderStatus.CANDIDATES) != bool(self.candidates)
        ):
            raise ReportValueError("provider lane candidates are invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "candidates": [value.to_data() for value in self.candidates],
            "identity": self.identity,
            "input_references": list(self.input_references),
            "kind": self.kind.value,
            "matched_family": self.matched_family,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class ProviderConvergence:
    work_identity: str
    lane_identities: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.work_identity, "provider convergence work identity", 128)
        if (
            type(self.lane_identities) is not tuple
            or len(self.lane_identities) < 2
            or tuple(sorted(set(self.lane_identities))) != self.lane_identities
            or any(_LANE_IDENTITY.fullmatch(value) is None for value in self.lane_identities)
        ):
            raise ReportValueError("provider convergence is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "lane_identities": list(self.lane_identities),
            "work_identity": self.work_identity,
        }


_PROVIDER_DIAGNOSTIC_STAGES = {
    FailureCategory.AUTHENTICATION: FailureStage.RESPONSE,
    FailureCategory.CANCELLATION: FailureStage.EXECUTION,
    FailureCategory.EMPTY_NO_MATCH: FailureStage.RESPONSE,
    FailureCategory.HTTP_NON_SUCCESS: FailureStage.RESPONSE,
    FailureCategory.LOOKUP_CONSTRUCTION: FailureStage.LOOKUP,
    FailureCategory.MALFORMED_OVER_BOUND_RESPONSE: FailureStage.RESPONSE,
    FailureCategory.PROVIDER_DECLARED_ERROR: FailureStage.RESPONSE,
    FailureCategory.QUOTA_RATE_LIMIT: FailureStage.RESPONSE,
    FailureCategory.SCHEMA_CONSTRUCTION: FailureStage.SCHEMA,
    FailureCategory.TIMEOUT: FailureStage.REQUEST,
    FailureCategory.TRANSPORT: FailureStage.REQUEST,
    FailureCategory.UNKNOWN: FailureStage.RESPONSE,
}


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    provider: str
    status: ProviderStatus
    input_references: tuple[str, ...] = ()
    candidates: tuple[ProviderCandidate, ...] = ()
    query_kind: ProviderQueryKind = ProviderQueryKind.NONE
    authority: str | None = None
    lanes: tuple[ProviderLaneObservation, ...] = ()
    convergence: tuple[ProviderConvergence, ...] = ()
    accessed_at: str | None = None
    diagnostics: tuple[FailureDiagnostic, ...] = ()
    request_accounting: ProviderRequestAccounting = ProviderRequestAccounting()

    def __post_init__(self) -> None:
        _name(self.provider, "provider")
        if (
            type(self.status) is not ProviderStatus
            or type(self.query_kind) is not ProviderQueryKind
            or self.authority not in {
                None,
                "google_books",
                "hardcover",
                "open_library",
            }
        ):
            raise ReportValueError("provider status is invalid")
        if self.accessed_at is not None:
            validate_provider_access_time(self.accessed_at)
        if type(self.request_accounting) is not ProviderRequestAccounting:
            raise ReportValueError("provider request accounting is invalid")
        if not diagnostics_are_canonical(self.diagnostics) or any(
            value.component is not FailureComponent.PROVIDER
            or _PROVIDER_DIAGNOSTIC_STAGES.get(value.category) is not value.stage
            or value.current is not None
            for value in self.diagnostics
        ):
            raise ReportValueError("provider diagnostics are invalid")
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
                _provider_reference_data(
                    self.provider,
                    candidate.identity,
                    claim.kind,
                    claim.value,
                    claim.state,
                    claim.scope,
                    claim.applicability,
                    claim.role,
                    claim.raw_order,
                    claim.date_event,
                    claim.date_precision,
                    claim.date_lexical,
                    claim.date_confidence,
                ),
            )
            for candidate in self.candidates
            for claim in candidate.claims
        ):
            raise ReportValueError("provider candidate claim reference is inconsistent")
        if (
            type(self.lanes) is not tuple
            or any(type(value) is not ProviderLaneObservation for value in self.lanes)
            or tuple(value.identity for value in self.lanes)
            != tuple(sorted({value.identity for value in self.lanes}))
        ):
            raise ReportValueError("provider lanes are invalid")
        if self.lanes:
            expected_kind = (
                self.lanes[0].kind
                if len(self.lanes) == 1
                else ProviderQueryKind.DISCOVERY
            )
            if self.query_kind is not expected_kind:
                raise ReportValueError("provider aggregate query kind is inconsistent")
            expected_inputs = tuple(
                sorted(
                    {
                        reference
                        for lane in self.lanes
                        for reference in lane.input_references
                    }
                )
            )
            if (
                self.status is not ProviderStatus.DISABLED
                and self.input_references != expected_inputs
            ):
                raise ReportValueError("provider aggregate inputs are inconsistent")
        lane_references = tuple(
            candidate.reference
            for lane in self.lanes
            for candidate in lane.candidates
        )
        if len(set(lane_references)) != len(lane_references):
            raise ReportValueError("provider lane candidate references are duplicated")
        if any(
            candidate.reference
            != provider_lane_candidate_reference(
                self.provider,
                lane.identity,
                candidate.edition_identity,
                candidate.work_identity,
            )
            for lane in self.lanes
            for candidate in lane.candidates
        ):
            raise ReportValueError("provider lane candidate reference is inconsistent")
        candidates_by_identity = {
            candidate.identity: candidate for candidate in self.candidates
        }
        if any(
            candidate.edition_identity not in candidates_by_identity
            or candidates_by_identity[candidate.edition_identity].work_identity
            != candidate.work_identity
            for lane in self.lanes
            for candidate in lane.candidates
        ):
            raise ReportValueError("provider lane candidates are inconsistent")
        lanes_by_work: dict[str, set[str]] = {}
        for lane in self.lanes:
            for candidate in lane.candidates:
                if candidate.work_identity == "unresolved":
                    continue
                lanes_by_work.setdefault(candidate.work_identity, set()).add(
                    lane.identity
                )
        expected_convergence = tuple(
            ProviderConvergence(work_identity, tuple(sorted(lane_identities)))
            for work_identity, lane_identities in sorted(lanes_by_work.items())
            if len(lane_identities) > 1
        )
        if self.convergence:
            if self.convergence != expected_convergence:
                raise ReportValueError("provider convergence is inconsistent")
        elif expected_convergence:
            object.__setattr__(self, "convergence", expected_convergence)

    def to_data(self) -> dict[str, object]:
        return {
            "candidates": [value.to_data() for value in self.candidates],
            "input_references": list(self.input_references),
            "provider": self.provider,
            "query_kind": self.query_kind.value,
            "status": self.status.value,
            "authority": self.authority,
            "accessed_at": self.accessed_at,
            "convergence": [value.to_data() for value in self.convergence],
            "diagnostics": [value.to_data() for value in self.diagnostics],
            "request_accounting": self.request_accounting.to_data(),
            "lanes": [value.to_data() for value in self.lanes],
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
        if any(
            not set(lane.input_references).issubset(known)
            for provider in self.providers
            for lane in provider.lanes
        ):
            raise ReportValueError("provider lane inputs do not reference local claims")
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
        if any(
            not {link.local_reference for link in evidence.links}.issubset(known)
            for provider in self.providers
            for candidate in provider.candidates
            for evidence in candidate.evidence
        ):
            raise ReportValueError("provider evidence comparison references are invalid")
        for provider in self.providers:
            for candidate in provider.candidates:
                expected = finalize_candidate_comparisons(
                    candidate, self.local_claims, provider.provider
                )
                if candidate != expected:
                    raise ReportValueError(
                        "provider comparison links are inconsistent"
                    )
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
        providers = tuple(
            replace(
                provider,
                candidates=tuple(
                    finalize_candidate_comparisons(
                        candidate,
                        local_claims,
                        provider.provider,
                    )
                    for candidate in provider.candidates
                ),
            )
            for provider in providers
        )
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
        if (
            family in accepted_local_families
            and candidate.work_identity != "unresolved"
        ):
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


def _provider_reference_data(
    provider: str,
    candidate: str,
    kind: ClaimKind,
    value: str | None,
    state: FieldState,
    scope: ClaimScope,
    applicability: str,
    role: str | None,
    raw_order: int,
    date_event: DateEvent | None,
    date_precision: DatePrecision | None,
    date_lexical: str | None,
    date_confidence: DateLinkConfidence | None,
) -> dict[str, object]:
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
        "date_event": None if date_event is None else date_event.value,
        "date_precision": None if date_precision is None else date_precision.value,
        "date_lexical": date_lexical,
        "date_confidence": (
            None if date_confidence is None else date_confidence.value
        ),
    }


def claim_scope(
    kind: ClaimKind,
    date_event: DateEvent | None = None,
) -> ClaimScope:
    if type(kind) is not ClaimKind:
        raise ReportValueError("claim kind is invalid")
    if date_event is not None and type(date_event) is not DateEvent:
        raise ReportValueError("date event is invalid")
    if (
        kind is ClaimKind.DATE_EVENT
        and date_event
        in {
            DateEvent.WORK_FIRST_PUBLICATION,
            DateEvent.CREATION,
        }
    ):
        return ClaimScope.WORK
    return ClaimScope.EDITION if kind in {ClaimKind.ISBN, ClaimKind.ASIN, ClaimKind.PUBLICATION_DATE, ClaimKind.DATE_EVENT, ClaimKind.CONTEXTUAL_CREDIT, ClaimKind.PUBLISHER, ClaimKind.IMPRINT, ClaimKind.LANGUAGE} else ClaimScope.WORK


def publication_date_relation(
    *,
    left_value: str,
    left_event: DateEvent,
    left_precision: DatePrecision,
    right_value: str,
    right_event: DateEvent,
    right_precision: DatePrecision,
    manifestation_compatible: bool = True,
) -> tuple[PublicationDateRelation, DatePrecision | None]:
    """Compare two typed dates without manufacturing missing precision."""
    if (
        left_event is not DateEvent.MANIFESTATION_PUBLICATION
        or right_event is not DateEvent.MANIFESTATION_PUBLICATION
    ):
        return PublicationDateRelation.EVENT_INCOMPATIBLE, None
    if not manifestation_compatible:
        return PublicationDateRelation.MANIFESTATION_INCOMPATIBLE, None
    left = _date_components(left_value, left_precision)
    right = _date_components(right_value, right_precision)
    shared: DatePrecision | None = None
    if left[0] == right[0]:
        shared = DatePrecision.YEAR
        if len(left) >= 2 and len(right) >= 2 and left[1] == right[1]:
            shared = DatePrecision.MONTH
            if len(left) >= 3 and len(right) >= 3 and left[2] == right[2]:
                shared = DatePrecision.DAY
    if (
        left_precision is DatePrecision.TIMESTAMP
        or right_precision is DatePrecision.TIMESTAMP
    ):
        if (
            left_precision is DatePrecision.TIMESTAMP
            and right_precision is DatePrecision.TIMESTAMP
            and _comparison_value(ClaimKind.PUBLICATION_DATE, left_value)
            == _comparison_value(ClaimKind.PUBLICATION_DATE, right_value)
        ):
            return PublicationDateRelation.EXACT, DatePrecision.DAY
        return PublicationDateRelation.FINER_CONFLICT, shared
    minimum = min(len(left), len(right))
    compatible = left[:minimum] == right[:minimum]
    if not compatible:
        return PublicationDateRelation.FINER_CONFLICT, shared
    if len(left) == len(right):
        return PublicationDateRelation.EXACT, shared
    if shared is DatePrecision.MONTH:
        return PublicationDateRelation.MONTH_COMPATIBLE, shared
    if shared is DatePrecision.YEAR:
        return PublicationDateRelation.YEAR_COMPATIBLE, shared
    return PublicationDateRelation.FINER_CONFLICT, shared


def _date_components(
    value: str, precision: DatePrecision
) -> tuple[str, ...]:
    normalized, actual = normalize_date_value(value)
    if actual is not precision:
        raise ReportValueError("date precision is inconsistent")
    if precision is DatePrecision.YEAR:
        return (normalized[:4],)
    if precision is DatePrecision.MONTH:
        return (normalized[:4], normalized[5:7])
    return (normalized[:4], normalized[5:7], normalized[8:10])


def _date_comparison_link(
    local_reference: str,
    relation: PublicationDateRelation,
    shared_precision: DatePrecision | None,
) -> ComparisonLink:
    if relation in {
        PublicationDateRelation.EXACT,
        PublicationDateRelation.MONTH_COMPATIBLE,
        PublicationDateRelation.YEAR_COMPATIBLE,
    }:
        outcome = Comparison.AGREEMENT
        method = (
            ComparisonMethod.EXACT
            if relation is PublicationDateRelation.EXACT
            else ComparisonMethod.DATE_PRECISION_COMPATIBILITY
        )
    elif relation is PublicationDateRelation.FINER_CONFLICT:
        outcome = Comparison.DISAGREEMENT
        method = ComparisonMethod.NONE
    else:
        outcome = Comparison.NOT_COMPARABLE
        method = ComparisonMethod.NONE
    return ComparisonLink(
        local_reference,
        outcome,
        method,
        relation,
        shared_precision,
    )


def _publication_date_links(
    *,
    value: str,
    event: DateEvent,
    precision: DatePrecision,
    local: tuple[LocalClaim, ...],
    manifestation_compatible: bool,
) -> tuple[ComparisonLink, ...]:
    links: list[ComparisonLink] = []
    for claim in local:
        if (
            claim.kind not in {ClaimKind.PUBLICATION_DATE, ClaimKind.DATE_EVENT}
            or claim.state is not FieldState.VALID
            or claim.canonical is None
            or type(claim.date_event) is not DateEvent
            or type(claim.date_precision) is not DatePrecision
        ):
            continue
        relation, shared = publication_date_relation(
            left_value=value,
            left_event=event,
            left_precision=precision,
            right_value=claim.canonical,
            right_event=claim.date_event,
            right_precision=claim.date_precision,
            manifestation_compatible=manifestation_compatible,
        )
        links.append(_date_comparison_link(claim.reference, relation, shared))
    if not links and (
        event is not DateEvent.MANIFESTATION_PUBLICATION
        or not manifestation_compatible
    ):
        relation = (
            PublicationDateRelation.EVENT_INCOMPATIBLE
            if event is not DateEvent.MANIFESTATION_PUBLICATION
            else PublicationDateRelation.MANIFESTATION_INCOMPATIBLE
        )
        links.extend(
            _date_comparison_link(claim.reference, relation, None)
            for claim in local
            if claim.kind is ClaimKind.ISBN
            and claim.state is FieldState.VALID
            and claim.canonical is not None
            and claim.applicability in {"ebook", "unknown"}
        )
    return tuple(sorted(links, key=lambda link: link.local_reference))


def comparison_links(provider: ProviderClaim, local: tuple[LocalClaim, ...]) -> tuple[ComparisonLink, ...]:
    """Compute one immutable comparison outcome for each same-kind local claim."""
    if provider.kind is ClaimKind.PUBLICATION_DATE:
        if (
            provider.state is not FieldState.VALID
            or provider.value is None
            or type(provider.date_event) is not DateEvent
            or type(provider.date_precision) is not DatePrecision
        ):
            return ()
        return _publication_date_links(
            value=provider.value,
            event=provider.date_event,
            precision=provider.date_precision,
            local=local,
            manifestation_compatible=True,
        )
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
            method = ComparisonMethod.NONE
        elif (
            method := _comparison_method(
                provider.kind,
                provider.value,
                claim.observed,
            )
        ) is not ComparisonMethod.NONE:
            outcome = Comparison.AGREEMENT
        elif provider.kind in {ClaimKind.ISBN, ClaimKind.ASIN}:
            outcome = Comparison.INCOMPATIBLE
            method = ComparisonMethod.NONE
        else:
            outcome = Comparison.DISAGREEMENT
            method = ComparisonMethod.NONE
        links.append(ComparisonLink(claim.reference, outcome, method))
    return tuple(sorted(links, key=lambda link: link.local_reference))


_TYPOGRAPHIC_TRANSLATION = str.maketrans(
    {
        "\u00a0": " ",
        "\u2013": "-",
        "\u2014": "-",
    }
)
_TITLE_VARIANT_MARKER = re.compile(
    r"(?:^|[\s(\[{:;,'\"\-\u2013\u2014\u2018-\u201d])"
    r"(?:abridged|abridgement|abridgment|adapted|adaptation|translated|translation|variant)"
    r"(?:$|[\s)\]}:;,'\",\-\u2013\u2014\u2018-\u201d])",
    re.IGNORECASE,
)
_NAME_SUFFIX = re.compile(
    r"^(?:jr|sr|i{2,3}|iv|v|esq|mbe|obe|cbe|kbe|qc|kc|ph\.?d|m\.?d|dphil|dsc)$",
    re.IGNORECASE,
)
_ORGANIZATION_MARKER = re.compile(
    r"\b(?:association|committee|company|corporation|foundation|group|inc\.?|"
    r"house|institute|librar(?:y|ies)|ltd\.?|llc|media|orchestra|press|"
    r"productions?|publishing|records|society|studios?|team|university)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_NAME_JOIN = re.compile(r"(?:\s(?:and|&)\s|[;/])", re.IGNORECASE)
_METHOD_STRENGTH = {
    ComparisonMethod.EXACT: 0,
    ComparisonMethod.CLOSED_FIELD_NORMALIZATION: 1,
    ComparisonMethod.UNICODE_CASE_WHITESPACE: 1,
    ComparisonMethod.TYPOGRAPHIC_PUNCTUATION: 2,
    ComparisonMethod.CONSERVATIVE_COMMA_INVERSION: 3,
    ComparisonMethod.STRUCTURAL_IDENTITY_BINDING: 4,
    ComparisonMethod.DATE_PRECISION_COMPATIBILITY: 5,
}
_PRINT_FORMAT_CONTRADICTIONS = frozenset(
    {
        "board book",
        "hardback",
        "hardcover",
        "library binding",
        "mass market paperback",
        "paperback",
        "print",
        "print book",
        "softback",
        "softcover",
    }
)


def manifestation_publication_compatible(
    candidate: ProviderCandidate,
    local: tuple[LocalClaim, ...],
) -> bool:
    """Require an exact usable local ISBN family and no closed contradiction."""
    local_families = {
        family
        for claim in local
        if claim.kind is ClaimKind.ISBN
        and claim.state is FieldState.VALID
        and claim.canonical is not None
        and claim.applicability in {"ebook", "unknown"}
        and (family := isbn_family(claim.canonical)) is not None
    }
    provider_families = {
        family
        for claim in candidate.claims
        if claim.kind is ClaimKind.ISBN
        and claim.state is FieldState.VALID
        and claim.value is not None
        and claim.scope is ClaimScope.EDITION
        and claim.applicability in {"ebook", "unknown"}
        and (family := isbn_family(claim.value)) is not None
    }
    if not local_families.intersection(provider_families):
        return False
    if any(
        evidence.kind is ProviderEvidenceKind.PHYSICAL_FORMAT
        and evidence.scope is ClaimScope.EDITION
        and normalize_title(evidence.value).casefold()
        in _PRINT_FORMAT_CONTRADICTIONS
        for evidence in candidate.evidence
    ):
        return False
    return not any(
        evidence.kind is ProviderEvidenceKind.EDITION_WORK_RELATION
        and evidence.scope is ClaimScope.EDITION
        and evidence.value != candidate.work_identity
        for evidence in candidate.evidence
    )


def finalize_candidate_comparisons(
    candidate: ProviderCandidate,
    local: tuple[LocalClaim, ...],
    provider: str | None = None,
) -> ProviderCandidate:
    """Finalize candidate-aware links and safely bind publication observations."""
    compatible = manifestation_publication_compatible(candidate, local)
    retained_claims = [
        claim
        for claim in candidate.claims
        if claim.kind is not ClaimKind.PUBLICATION_DATE
    ]
    evidence = list(candidate.evidence)
    next_source_ordinal = (
        max((value.source_ordinal for value in evidence), default=-1) + 1
    )
    for claim in candidate.claims:
        if claim.kind is not ClaimKind.PUBLICATION_DATE:
            continue
        if claim.state is not FieldState.VALID:
            retained_claims.append(claim)
            continue
        if (
            compatible
            and claim.value is not None
            and type(claim.date_event) is DateEvent
            and type(claim.date_precision) is DatePrecision
        ):
            retained_claims.append(claim)
            continue
        if claim.date_lexical is not None:
            evidence.append(
                ProviderEvidence(
                    ProviderEvidenceKind.PUBLICATION_DATE_TEXT,
                    claim.date_lexical,
                    ClaimScope.EDITION,
                    date_event=DateEvent.MANIFESTATION_PUBLICATION,
                    date_confidence=DateLinkConfidence.UNRESOLVED,
                    source_ordinal=next_source_ordinal,
                )
            )
            next_source_ordinal += 1

    promoted_evidence: list[ProviderEvidence] = []
    for value in evidence:
        if (
            value.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
            and value.date_normalized is not None
            and type(value.date_precision) is DatePrecision
            and compatible
            and provider is not None
        ):
            retained_claims.append(
                ProviderClaim.create(
                    provider=provider,
                    candidate=candidate.identity,
                    kind=ClaimKind.PUBLICATION_DATE,
                    value=value.date_normalized,
                    state=FieldState.VALID,
                    scope=ClaimScope.EDITION,
                    applicability="not_applicable",
                    raw_order=max(
                        (claim.raw_order for claim in retained_claims),
                        default=-1,
                    )
                    + 1,
                    date_event=DateEvent.MANIFESTATION_PUBLICATION,
                    date_precision=value.date_precision,
                    date_lexical=value.value,
                    date_confidence=DateLinkConfidence.EXPLICIT,
                )
            )
            continue
        if (
            value.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
            and value.date_normalized is not None
        ):
            value = replace(
                value,
                date_event=DateEvent.MANIFESTATION_PUBLICATION,
                date_confidence=DateLinkConfidence.UNRESOLVED,
            )
        if (
            value.date_normalized is not None
            and type(value.date_event) is DateEvent
            and type(value.date_precision) is DatePrecision
        ):
            value = replace(
                value,
                links=_publication_date_links(
                    value=value.date_normalized,
                    event=value.date_event,
                    precision=value.date_precision,
                    local=local,
                    manifestation_compatible=(
                        compatible
                        if value.kind
                        is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
                        else True
                    ),
                ),
            )
        promoted_evidence.append(value)

    provisional = replace(
        candidate,
        claims=tuple(
            sorted(retained_claims, key=lambda value: value.raw_order)
        ),
        evidence=tuple(
            sorted(
                promoted_evidence,
                key=lambda value: (
                    value.scope.value,
                    value.kind.value,
                    value.value,
                    value.role or "",
                    (
                        ""
                        if value.date_event is None
                        else value.date_event.value
                    ),
                    (
                        ""
                        if value.date_precision is None
                        else value.date_precision.value
                    ),
                    value.date_normalized or "",
                    (
                        ""
                        if value.date_confidence is None
                        else value.date_confidence.value
                    ),
                    value.source_ordinal,
                ),
            )
        ),
    )
    claims = tuple(
        replace(
            claim,
            links=(
                _creator_comparison_links(provisional, claim, local)
                if claim.kind is ClaimKind.CREATOR
                else comparison_links(claim, local)
            ),
        )
        for claim in provisional.claims
    )
    return replace(provisional, claims=claims)


def _creator_comparison_links(
    candidate: ProviderCandidate,
    provider: ProviderClaim,
    local: tuple[LocalClaim, ...],
) -> tuple[ComparisonLink, ...]:
    compatible_provider = tuple(
        claim
        for claim in candidate.claims
        if claim.kind is ClaimKind.CREATOR
        and claim.scope is provider.scope
        and claim.role == provider.role
    )
    local_groups: dict[tuple[str, ClaimScope, str | None], list[LocalClaim]] = {}
    for claim in local:
        if claim.kind is ClaimKind.CREATOR:
            local_groups.setdefault(
                (claim.basis, claim.scope, claim.role), []
            ).append(claim)
    agreements: dict[str, ComparisonMethod] = {}
    if (
        provider.role == "author"
        and compatible_provider
        and all(
            claim.state is FieldState.VALID and claim.value is not None
            for claim in compatible_provider
        )
    ):
        provider_values = tuple(
            claim.value for claim in compatible_provider if claim.value is not None
        )
        for (_, scope, role), group in local_groups.items():
            if (
                scope is not provider.scope
                or role != provider.role
                or not group
                or any(
                    claim.state is not FieldState.VALID
                    or claim.canonical is None
                    or claim.applicability != provider.applicability
                    for claim in group
                )
            ):
                continue
            local_values = tuple(
                claim.observed for claim in group if claim.canonical is not None
            )
            complete_bindings = complete_creator_identity_bindings(
                candidate, compatible_provider
            )
            if complete_bindings is not None:
                bound_matches = _bound_creator_sequence_match(
                    candidate,
                    compatible_provider,
                    local_values,
                )
                bound_match = bound_matches.get(provider.reference)
                if bound_match is not None:
                    local_index, method = bound_match
                    agreements[group[local_index].reference] = method
            else:
                methods = _creator_sequence_methods(
                    provider_values, local_values
                )
                if methods:
                    provider_index = compatible_provider.index(provider)
                    agreements[group[provider_index].reference] = methods[
                        provider_index
                    ]

    links: list[ComparisonLink] = []
    for claim in local:
        if claim.kind is not ClaimKind.CREATOR:
            continue
        method = agreements.get(claim.reference, ComparisonMethod.NONE)
        if method is not ComparisonMethod.NONE:
            outcome = Comparison.AGREEMENT
        elif (
            provider.state is not FieldState.VALID
            or provider.value is None
            or claim.state is not FieldState.VALID
            or claim.canonical is None
            or claim.scope is not provider.scope
            or claim.applicability != provider.applicability
            or claim.role != provider.role
        ):
            outcome = Comparison.NOT_COMPARABLE
        else:
            outcome = Comparison.DISAGREEMENT
        links.append(ComparisonLink(claim.reference, outcome, method))
    return tuple(sorted(links, key=lambda link: link.local_reference))


def _bound_creator_sequence_match(
    candidate: ProviderCandidate,
    provider_claims: tuple[ProviderClaim, ...],
    local_values: tuple[str, ...],
) -> dict[str, tuple[int, ComparisonMethod]]:
    claims_by_reference = {
        claim.reference: claim for claim in provider_claims
    }
    bindings = complete_creator_identity_bindings(
        candidate, provider_claims
    )
    if bindings is None:
        return {}
    ordered_identities: list[str] = []
    aliases: dict[str, list[ProviderClaim]] = {}
    for binding in bindings:
        claim = claims_by_reference[binding.claim_reference]
        if (
            binding.scope is not claim.scope
            or binding.role != claim.role
            or claim.value is None
        ):
            return {}
        if binding.identity not in aliases:
            ordered_identities.append(binding.identity)
            aliases[binding.identity] = []
        aliases[binding.identity].append(claim)
    if len(ordered_identities) != len(local_values):
        return {}
    if _has_creator_duplicates(local_values):
        return {}
    if any(
        len(claims)
        != len(
            {
                creator_comparison_key(claim.value)
                for claim in claims
                if claim.value is not None
            }
        )
        for claims in aliases.values()
    ):
        return {}
    identities_by_value: dict[str, str] = {}
    for identity, claims in aliases.items():
        for claim in claims:
            if claim.value is None:
                return {}
            key = creator_comparison_key(claim.value)
            owner = identities_by_value.setdefault(key, identity)
            if owner != identity:
                return {}
    matches: dict[str, tuple[int, ComparisonMethod]] = {}
    for local_index, (identity, local_value) in enumerate(
        zip(ordered_identities, local_values, strict=True)
    ):
        alias_methods = {
            claim.reference: _comparison_method(
                ClaimKind.CREATOR,
                claim.value,
                local_value,
                allow_comma_inversion=len(local_values) == 1,
            )
            for claim in aliases[identity]
            if claim.value is not None
        }
        if not any(
            method is not ComparisonMethod.NONE
            for method in alias_methods.values()
        ):
            return {}
        matches.update(
            {
                reference: (
                    local_index,
                    method
                    if method is not ComparisonMethod.NONE
                    else ComparisonMethod.STRUCTURAL_IDENTITY_BINDING,
                )
                for reference, method in alias_methods.items()
            }
        )
    return matches


def complete_creator_identity_bindings(
    candidate: ProviderCandidate,
    claims: tuple[ProviderClaim, ...],
) -> tuple[ProviderIdentityBinding, ...] | None:
    """Return authoritative bindings only for one completely covered claim set."""
    if not claims:
        return None
    bindings = tuple(
        sorted(
            (
                binding
                for binding in candidate.identity_bindings
                if binding.scope is claims[0].scope
                and binding.role == claims[0].role
            ),
            key=lambda binding: (
                binding.source_ordinal,
                binding.identity,
                binding.claim_reference,
            ),
        )
    )
    if (
        len(bindings) != len(claims)
        or {binding.claim_reference for binding in bindings}
        != {claim.reference for claim in claims}
    ):
        return None
    return bindings


def _creator_sequence_methods(
    provider_values: tuple[str, ...],
    local_values: tuple[str, ...],
) -> tuple[ComparisonMethod, ...]:
    if (
        len(provider_values) != len(local_values)
        or not provider_values
        or _has_creator_duplicates(provider_values)
        or _has_creator_duplicates(local_values)
    ):
        return ()
    methods = tuple(
        _comparison_method(
            ClaimKind.CREATOR,
            provider_value,
            local_value,
            allow_comma_inversion=len(provider_values) == 1,
        )
        for provider_value, local_value in zip(
            provider_values, local_values, strict=True
        )
    )
    if ComparisonMethod.NONE in methods:
        return ()
    return methods


def _comparison_method(
    kind: ClaimKind,
    provider_value: str,
    local_value: str,
    *,
    allow_comma_inversion: bool = False,
) -> ComparisonMethod:
    if provider_value == local_value:
        return ComparisonMethod.EXACT
    if kind not in {ClaimKind.TITLE, ClaimKind.SUBTITLE, ClaimKind.CREATOR}:
        return (
            ComparisonMethod.CLOSED_FIELD_NORMALIZATION
            if _comparison_value(kind, provider_value)
            == _comparison_value(kind, local_value)
            else ComparisonMethod.NONE
        )
    provider_typographic = _typographic_text(provider_value)
    local_typographic = _typographic_text(local_value)
    if kind in {ClaimKind.TITLE, ClaimKind.SUBTITLE} and (
        _has_title_variant_marker(provider_value, provider_typographic)
        or _has_title_variant_marker(local_value, local_typographic)
    ):
        return ComparisonMethod.NONE
    provider_normalized = _comparison_text(provider_value)
    local_normalized = _comparison_text(local_value)
    if provider_normalized == local_normalized:
        return ComparisonMethod.UNICODE_CASE_WHITESPACE
    if (
        provider_typographic is not None
        and local_typographic is not None
        and provider_typographic == local_typographic
    ):
        return ComparisonMethod.TYPOGRAPHIC_PUNCTUATION
    if (
        allow_comma_inversion
        and kind is ClaimKind.CREATOR
        and provider_typographic is not None
        and local_typographic is not None
        and (
            _comma_inverted(provider_value) == local_typographic
            or _comma_inverted(local_value) == provider_typographic
        )
    ):
        return ComparisonMethod.CONSERVATIVE_COMMA_INVERSION
    return ComparisonMethod.NONE


def _has_title_variant_marker(
    value: str, typographic: str | None
) -> bool:
    return (
        _TITLE_VARIANT_MARKER.search(value) is not None
        or typographic is not None
        and _TITLE_VARIANT_MARKER.search(typographic) is not None
    )


def _comparison_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).casefold()
    return " ".join(
        part
        for part in re.split(r"[\t\n\r\f\v ]+", normalized)
        if part
    )


def _typographic_text(value: str) -> str | None:
    paired = {
        "\u2018": ("'", True),
        "\u2019": ("'", False),
        "\u201c": ('"', True),
        "\u201d": ('"', False),
    }
    stack: list[str] = []
    translated: list[str] = []
    for character in value:
        quote = paired.get(character)
        if quote is None:
            translated.append(character.translate(_TYPOGRAPHIC_TRANSLATION))
            continue
        mark, opening = quote
        if opening:
            stack.append(mark)
        elif not stack or stack.pop() != mark:
            return None
        translated.append(mark)
    if stack:
        return None
    return _comparison_text("".join(translated))


def creator_comparison_key(value: str) -> str:
    """Return the closed comparison key used to reject creator duplicates."""
    return _typographic_text(value) or _comparison_text(value)


def _has_creator_duplicates(values: tuple[str, ...]) -> bool:
    return len({creator_comparison_key(value) for value in values}) != len(
        values
    )


def _comma_inverted(value: str) -> str | None:
    if value.count(",") != 1:
        return None
    typographic = _typographic_text(value)
    if typographic is None or typographic.count(",") != 1:
        return None
    family, given = (
        _comparison_text(part) for part in typographic.split(",", 1)
    )
    if (
        not family
        or not given
        or len(family) > 128
        or len(given) > 128
        or _ORGANIZATION_MARKER.search(value)
        or _AMBIGUOUS_NAME_JOIN.search(value)
        or not _personal_name_side(family)
        or not _personal_name_side(given)
        or any(
            _NAME_SUFFIX.fullmatch(token.rstrip("."))
            for token in (*family.split(), *given.split())
        )
    ):
        return None
    return f"{given} {family}"


def _personal_name_side(value: str) -> bool:
    tokens = value.split()
    return (
        1 <= len(tokens) <= 8
        and any(unicodedata.category(character).startswith("L") for character in value)
        and all(
            unicodedata.category(character)[0] in {"L", "M"}
            or character in {" ", ".", "'", "-"}
            for character in value
        )
    )


def _comparison_value(kind: ClaimKind, value: str) -> str:
    if kind is ClaimKind.ISBN:
        return "".join(character for character in value if character.isdigit() or character in "Xx").upper()
    if kind is ClaimKind.PUBLICATION_DATE:
        normalized, precision = normalize_date_value(value)
        if precision is DatePrecision.TIMESTAMP:
            instant = datetime.fromisoformat(normalized)
            if instant.utcoffset() is None:
                raise ReportValueError(
                    "comparison timestamp requires an explicit offset"
                )
            return instant.astimezone(timezone.utc).isoformat()
        return normalized
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
    item = _object(value, {"applicability", "applicability_basis", "basis", "canonical", "checksum", "date_event", "date_precision", "excerpt", "kind", "observed", "reference", "state", "scope", "role", "rear_provenance", "occurrence"})
    provenance = item["rear_provenance"]
    return LocalClaim(item["reference"], ClaimKind(item["kind"]), item["observed"], item["canonical"], item["excerpt"], item["applicability"], item["basis"], item["checksum"], item["applicability_basis"], ClaimScope(item["scope"]), FieldState(item["state"]), item["role"], None if provenance is None else RearClaimProvenance(**_object(provenance, {"citation", "marker", "resource_identity", "spine_ordinal", "tail_slot"})), item["occurrence"], None if item["date_event"] is None else DateEvent(item["date_event"]), None if item["date_precision"] is None else DatePrecision(item["date_precision"]))


def _provider_from_data(value: object) -> ProviderObservation:
    item = _object(
        value,
        {
            "accessed_at",
            "authority",
            "candidates",
            "convergence",
            "diagnostics",
            "input_references",
            "lanes",
            "provider",
            "query_kind",
            "request_accounting",
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
        tuple(_provider_lane_from_data(value) for value in _list(item["lanes"])),
        tuple(
            _provider_convergence_from_data(value)
            for value in _list(item["convergence"])
        ),
        item["accessed_at"],
        tuple(
            FailureDiagnostic.from_json(canonical_json(value))
            for value in _list(item["diagnostics"])
        ),
        _request_accounting(item["request_accounting"]),
    )


def _request_accounting(value: object) -> ProviderRequestAccounting:
    item = _object(value, {
        "initial_dispatches", "logical_requests", "replays", "retry_dispatches",
        "undispatched_incomplete",
    })
    try:
        return ProviderRequestAccounting(**item)
    except (TypeError, ReportValueError):
        raise ReportValueError("provider request accounting is invalid") from None


def _provider_lane_from_data(value: object) -> ProviderLaneObservation:
    item = _object(
        value,
        {
            "candidates",
            "identity",
            "input_references",
            "kind",
            "matched_family",
            "status",
        },
    )
    return ProviderLaneObservation(
        item["identity"],
        ProviderQueryKind(item["kind"]),
        ProviderStatus(item["status"]),
        tuple(_strings(item["input_references"])),
        item["matched_family"],
        tuple(
            _provider_lane_candidate_from_data(candidate)
            for candidate in _list(item["candidates"])
        ),
    )


def _provider_lane_candidate_from_data(value: object) -> ProviderLaneCandidate:
    item = _object(
        value, {"edition_identity", "reference", "work_identity"}
    )
    return ProviderLaneCandidate(
        item["reference"], item["edition_identity"], item["work_identity"]
    )


def _provider_convergence_from_data(value: object) -> ProviderConvergence:
    item = _object(value, {"lane_identities", "work_identity"})
    return ProviderConvergence(
        item["work_identity"], tuple(_strings(item["lane_identities"]))
    )


def _candidate_from_data(value: object) -> ProviderCandidate:
    item = _object(
        value,
        {
            "claims",
            "evidence",
            "identity",
            "identity_bindings",
            "work_identity",
        },
    )
    return ProviderCandidate(
        item["identity"],
        item["work_identity"],
        tuple(_provider_claim_from_data(claim) for claim in _list(item["claims"])),
        tuple(_provider_evidence_from_data(evidence) for evidence in _list(item["evidence"])),
        tuple(
            _provider_identity_binding_from_data(binding)
            for binding in _list(item["identity_bindings"])
        ),
    )


def _provider_claim_from_data(value: object) -> ProviderClaim:
    item = _object(value, {"applicability", "date_confidence", "date_event", "date_lexical", "date_precision", "kind", "links", "raw_order", "reference", "role", "scope", "state", "value"})
    links = tuple(_comparison_link_from_data(link) for link in _list(item["links"]))
    return ProviderClaim(
        item["reference"],
        ClaimKind(item["kind"]),
        item["value"],
        FieldState(item["state"]),
        ClaimScope(item["scope"]),
        item["applicability"],
        item["role"],
        item["raw_order"],
        links,
        None if item["date_event"] is None else DateEvent(item["date_event"]),
        None if item["date_precision"] is None else DatePrecision(item["date_precision"]),
        item["date_lexical"],
        (
            None
            if item["date_confidence"] is None
            else DateLinkConfidence(item["date_confidence"])
        ),
    )


def _provider_identity_binding_from_data(
    value: object,
) -> ProviderIdentityBinding:
    item = _object(
        value,
        {
            "claim_reference",
            "identity",
            "role",
            "scope",
            "source_ordinal",
        },
    )
    return ProviderIdentityBinding(
        item["claim_reference"],
        item["identity"],
        ClaimScope(item["scope"]),
        item["role"],
        item["source_ordinal"],
    )


def _provider_evidence_from_data(value: object) -> ProviderEvidence:
    item = _object(value, {"date_confidence", "date_event", "date_normalized", "date_precision", "kind", "links", "role", "scope", "source_ordinal", "value"})
    return ProviderEvidence(
        ProviderEvidenceKind(item["kind"]),
        item["value"],
        ClaimScope(item["scope"]),
        item["role"],
        None if item["date_event"] is None else DateEvent(item["date_event"]),
        None if item["date_precision"] is None else DatePrecision(item["date_precision"]),
        item["date_normalized"],
        (
            None
            if item["date_confidence"] is None
            else DateLinkConfidence(item["date_confidence"])
        ),
        item["source_ordinal"],
        tuple(_comparison_link_from_data(link) for link in _list(item["links"])),
    )


def _comparison_link_from_data(value: object) -> ComparisonLink:
    item = _object(
        value,
        {
            "local_reference",
            "method",
            "outcome",
            "publication_date_relation",
            "shared_date_precision",
        },
    )
    return ComparisonLink(
        item["local_reference"],
        Comparison(item["outcome"]),
        ComparisonMethod(item["method"]),
        (
            None
            if item["publication_date_relation"] is None
            else PublicationDateRelation(item["publication_date_relation"])
        ),
        (
            None
            if item["shared_date_precision"] is None
            else DatePrecision(item["shared_date_precision"])
        ),
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
    "ClaimKind", "ClaimScope", "COMPARISON_VERSION", "Comparison", "ComparisonLink", "ComparisonMethod", "FieldResult", "FieldState", "FileFacts", "LocalClaim", "MAX_CANDIDATES",
    "IdentifierEvidence", "IdentifierEvidenceBasis", "IdentifierRelation",
    "MAX_REPORT_BYTES", "MetadataReport", "ProviderCandidate", "ProviderClaim",
    "ProviderEvidence", "ProviderEvidenceKind", "ProviderIdentityBinding", "DateLinkConfidence", "PublicationDateRelation",
    "ProviderConvergence", "ProviderLaneCandidate", "ProviderLaneObservation",
    "ProviderObservation", "ProviderQueryKind", "ProviderStatus",
    "REPORT_SCHEMA", "ReportValueError",
    "TargetDestination", "TargetFacts", "TargetField", "TargetFieldState",
    "TargetSeries", "canonical_json", "claim_scope", "comparison_links",
    "complete_creator_identity_bindings",
    "creator_comparison_key",
    "finalize_candidate_comparisons",
    "manifestation_publication_compatible",
    "publication_date_relation",
    "default_target_fields",
    "derive_identifier_evidence", "isbn_family", "normalize_date_value", "normalize_title",
    "provider_lane_candidate_reference", "stable_reference",
    "validate_observed_title",
]
