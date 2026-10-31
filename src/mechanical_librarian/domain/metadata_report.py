"""Closed, neutral metadata-evidence report for one selected EPUB."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Callable, Mapping, Self, TypedDict, cast

from mechanical_librarian.domain.audio_inspection import (
    MAX_AUDIO_TRACKS,
    MAX_DURATION_MILLISECONDS,
    MAX_OBSERVED_CHARACTERS,
    AudioChapter,
    AudioChapterComposition,
    AudioEmbeddedCover,
    AudioEvidenceSurface,
    AudioInspectionField,
    AudioInspectionLimits,
    AudioObservation,
    AudioObservationKind,
    AudioSidecarDigest,
    AudioTrackDigest,
    normalize_duration_seconds,
)
from mechanical_librarian.domain.audio_inspection import (
    MAX_CHAPTERS as MAX_AUDIO_CHAPTERS,
)
from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverContext,
    CoverContextField,
    CoverDesignation,
    CoverEvidenceError,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    CoverTruncationReason,
    publication_ready,
)
from mechanical_librarian.domain.epub_inspection import (
    DateEvent,
    DatePrecision,
)
from mechanical_librarian.domain.epub_inspection import (
    normalize_date_value as _normalize_date_value,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_incumbent_description,
    validate_projection_text,
    validate_role_projection,
)
from mechanical_librarian.domain.failure_diagnostics import (
    FailureCategory,
    FailureComponent,
    FailureDiagnostic,
    FailureStage,
    diagnostics_are_canonical,
)
from mechanical_librarian.domain.field_outcomes import library_languages_valid
from mechanical_librarian.domain.validation import (
    bounded_integer,
    literal,
    non_negative_integer,
)
from mechanical_librarian.domain.work_identity import (
    BAND_TITLE_PRODUCTIONS,
    CORROBORATED_TITLE_APPLICATION,
    CORROBORATED_TITLE_PRODUCTION,
    WorkDispatchLineage,
    WorkDispatchRung,
    WorkIdentityAdmission,
    WorkTitleTransformationFamily,
    WorkTitleTransformationLineage,
    admit_provider_work_identity,
    closed_creator_display_possibility,
    direct_work_title_election_matches,
    fold_lone_apostrophes,
    narrowing_work_query,
    replay_provider_work_identity_admission,
    work_dispatch_lineage_from_data,
    work_query_ladder,
    work_title_has_variant_marker,
    work_title_transformation_lineage_from_data,
)
from mechanical_librarian.domain.work_identity import (
    _closed_equal as _closed_title_equal,
)

REPORT_SCHEMA = "metadata-report-v65"
COMPARISON_VERSION = "closed-title-creator-publication-work-identity-v6"
# The physical SQLite schema separately fingerprints this exact report shape,
# so any earlier development database fails closed under the clean-schema rule.
MAX_REPORT_BYTES = 2_097_152
MAX_CANDIDATES = 5
# Hardcover audio editions one observation retains (#962). Only editions that
# credit a narrator and state a runtime inside the tight band of the recording
# are kept, and a work has a handful at most.
MAX_AUDIO_EDITIONS = 16
# Band-first recording-lookup hints an item keeps (#1257). #1246 found at most 2
# refused tight-band products per item on the retained snapshot.
MAX_RECORDING_LOOKUP_HINTS = 2
_RECORDING_LOOKUP_HINT_BASIS = "runtime_within_tight_band"
MAX_PROVIDER_EVIDENCE = 128
# One provider chapter vector holds at most the chapter count the local
# inspector itself admits (ADR-0063), so neither side of a comparison can be
# longer than the other can ever be.  At five candidates a full vector costs
# roughly 30 KB of the 2 MiB document, which is why it is the one bounded
# structure on `ProviderEvidence` rather than a free-form value.
MAX_PROVIDER_CHAPTER_DURATIONS = 1_024
MAX_AUDIO_SIDECARS = AudioInspectionLimits.HARD_CAPS["sidecar_documents"]
# Whole seconds. Provider runtimes arrive minute-granular, so an agreement band
# rather than equality is the only comparison that can be true of both sides.
# This decides whether a stated duration claim links to a local one at all.
# The corroboration gate's own `RUNTIME_BAND_TIGHT_SECONDS` is a separate
# parameter that states the same number today and is retuned separately
# (ADR-0064, #967).
DURATION_AGREEMENT_BAND_SECONDS = 90
# The marketplaces an ASIN lookup may resolve in, US first. ADR-0061 makes ASIN
# a marketplace-scoped identifier, so the resolving marketplace travels with the
# evidence rather than being assumed.
AUDIOBOOK_MARKETPLACES = (
    "us",
    "uk",
    "de",
    "fr",
    "au",
    "in",
    "ca",
    "jp",
    "it",
    "es",
)
# The language bases each marketplace's catalogue is in; Canada's carries
# English and French. Read as a library's search order (#1315) and as the
# language a regional product's text is in (#1317).
AUDIOBOOK_MARKETPLACE_LANGUAGES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "us": ("en",),
        "uk": ("en",),
        "de": ("de",),
        "fr": ("fr",),
        "au": ("en",),
        "in": ("en",),
        "ca": ("en", "fr"),
        "jp": ("ja",),
        "it": ("it",),
        "es": ("es",),
    }
)
# The four authorities that observe a recording rather than a printed edition.
# Only these state a running time, a narrator, or a marketplace, so only these
# reach the corroboration gate that widens a work admission to an edition one.
AUDIOBOOK_PROVIDER_AUTHORITIES = frozenset(
    {"audible_catalog", "audiosilo", "audnexus", "itunes_search"}
)
MAX_CONTRIBUTIONS = 16
MAX_SERIES = 16
MAX_TAGS = 32
MAX_SCALAR_CHARACTERS = 512
MAX_DESCRIPTION_CHARACTERS = 16_384
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REPORT_ID = re.compile(r"^report_[0-9a-f]{64}$")
_REFERENCE = re.compile(r"^(?:local|provider)_[0-9a-f]{64}$")
_EXPLICIT_TIMESTAMP_OFFSET = re.compile(r"(?:Z|[+-]\d{2}:\d{2})$")
_LANE_IDENTITY = re.compile(r"^lane_[0-9a-f]{64}$")
_LANE_CANDIDATE_REFERENCE = re.compile(r"^lane_candidate_[0-9a-f]{64}$")
# The whole shape of a canonical ASIN and nothing else: ten upper-case ASCII
# alphanumerics.  ADR-0061 admits ISBN-10-shaped ASINs without prefix filtering
# -- 17 of the reference library's 103 known ASINs are shaped that way and all
# are genuine -- so a `B0` filter here would refuse real identifiers.
_CANONICAL_ASIN = re.compile(r"^[0-9A-Z]{10}$")
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
    if observed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
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
    DURATION = "duration"
    PERFORMANCE_TYPE = "performance_type"
    ADULT_PRODUCT = "adult_product"


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
    EDITION_WORK_RELATION_CONTRADICTION = "edition_work_relation_contradiction"
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
    TAXONOMY = "taxonomy"
    DESCRIPTION_TRANSFORMATION = "description_transformation"
    # The marketplace an ASIN lookup actually resolved in. An ASIN is
    # marketplace-scoped, so provenance that omits the marketplace names no
    # identity at all.
    ASIN_MARKETPLACE = "asin_marketplace"
    # A recording's bounded structure: chapter count, total runtime, and the
    # per-chapter duration vector the corroboration rules align against.
    CHAPTER_STRUCTURE = "chapter_structure"


class DateLinkConfidence(StrEnum):
    """Whether a provider date is safely linked to its stated event and scope."""

    EXPLICIT = "explicit"
    UNRESOLVED = "unresolved"


class WorkIdentityContradiction(StrEnum):
    """Whether complete retained work evidence contradicts the exact target."""

    EXPLICIT = "explicit_contradiction"
    NOT_CONTRADICTED = "not_contradicted"
    UNAVAILABLE = "unavailable"


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
    AUDIO_TAG = "audio_tag"
    AUDIO_SIDECAR = "audio_sidecar"
    AUDIO_PATH = "audio_path"
    PRIOR_ADMISSION = "prior_admission"


# Which local-claim bases each owner-facing source buckets.  This mapping is
# the only definition of the set: `CLAIM_BASES` is derived from it below and
# `LocalClaim` refuses any basis outside it, so a basis cannot exist without an
# owner-facing source that `derive_field_results` will bucket it under.
_SOURCE_CLAIM_BASES: Mapping[FieldSource, frozenset[str]] = MappingProxyType(
    {
        FieldSource.EPUB: frozenset({"opening_matter", "package_metadata"}),
        FieldSource.TARGET_METADATA: frozenset({"target_metadata"}),
        FieldSource.AUDIO_TAG: frozenset({"embedded_tag"}),
        FieldSource.AUDIO_SIDECAR: frozenset({"sidecar"}),
        FieldSource.AUDIO_PATH: frozenset({"path"}),
        FieldSource.PRIOR_ADMISSION: frozenset({"prior_admission"}),
    }
)
CLAIM_BASES = frozenset(
    basis for bases in _SOURCE_CLAIM_BASES.values() for basis in bases
)
# The bases whose ASIN was stated *about* a recording rather than read off it:
# the one the target asserts, and the one an earlier preparation of the same
# track set admitted and this one carries forward (#1316). Neither is one of the
# item's own identifier surfaces, so neither settles identity or is offered as
# a local value; each only names a product to look up, whose answer the runtime
# band and the narrator gate still judge (ADR-0061, ADR-0088).
ASSERTED_ASIN_BASES = frozenset({"target_metadata", "prior_admission"})


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
class RecordingCorroboration:
    """What the corroborated admission rule reads off the target (#1307).

    The recording's own narrators and series memberships as the target
    records them. Its local claims travel beside it, so this holds only what
    the claims do not.
    """

    narrators: tuple[str, ...] = ()
    series: tuple[TargetSeries, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.narrators) is not tuple
            or any(type(value) is not str or not value for value in self.narrators)
            or type(self.series) is not tuple
            or any(type(value) is not TargetSeries for value in self.series)
        ):
            raise ReportValueError("recording corroboration is invalid")


def recording_corroboration(
    fields: tuple[TargetField, ...],
) -> RecordingCorroboration:
    """Read the target's narrators and series for the corroborated rule (#1307)."""
    narrators: tuple[str, ...] = ()
    series: tuple[TargetSeries, ...] = ()
    for field in fields:
        if field.state is not TargetFieldState.VALUE or type(field.value) is not tuple:
            continue
        if field.destination is TargetDestination.NARRATORS:
            narrators = tuple(
                value for value in field.value if type(value) is str and value
            )
        elif field.destination is TargetDestination.SERIES:
            series = tuple(
                value for value in field.value if type(value) is TargetSeries
            )
    return RecordingCorroboration(narrators, series)


@dataclass(frozen=True, slots=True)
class TargetField:
    destination: TargetDestination
    state: TargetFieldState
    value: str | bool | tuple[str, ...] | tuple[TargetSeries, ...] | None = None
    # Whether writing this description's value back reproduces the form the
    # library stores it in (#1424). Only a description value can be restorable,
    # and only a description carries the flag.
    restorable: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.destination) is not TargetDestination
            or type(self.state) is not TargetFieldState
            or type(self.restorable) is not bool
            or (
                self.restorable
                and (
                    self.destination is not TargetDestination.DESCRIPTION
                    or self.state is not TargetFieldState.VALUE
                )
            )
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
            maximum = (
                MAX_CONTRIBUTIONS
                if self.destination
                in {
                    TargetDestination.AUTHORS,
                    TargetDestination.NARRATORS,
                }
                else MAX_TAGS
            )
            if (
                type(self.value) is not tuple
                or not self.value
                or len(self.value) > maximum
                or any(type(item) is not str for item in self.value)
            ):
                raise ReportValueError("target list field is invalid")
            for item in self.value:
                _projection_text(item, "target list value", MAX_SCALAR_CHARACTERS)
            return
        if type(self.value) is not str or not self.value:
            raise ReportValueError("target scalar field is invalid")
        if self.destination is TargetDestination.DESCRIPTION:
            try:
                validate_incumbent_description(
                    self.value, "target scalar value", MAX_DESCRIPTION_CHARACTERS
                )
            except EvidenceValueError as error:
                raise ReportValueError("target scalar value is invalid") from error
        else:
            _projection_text(self.value, "target scalar value", MAX_SCALAR_CHARACTERS)

    def to_data(self) -> dict[str, object]:
        value: object = self.value
        # `__post_init__` rejects a SERIES field whose value is anything but a
        # non-empty tuple of TargetSeries.
        if self.destination is TargetDestination.SERIES and self.value is not None:
            value = [
                item.to_data() for item in cast("tuple[TargetSeries, ...]", self.value)
            ]
        elif type(self.value) is tuple:
            value = list(self.value)
        data: dict[str, object] = {
            "destination": self.destination.value,
            "state": self.state.value,
            "value": value,
        }
        if self.destination is TargetDestination.DESCRIPTION:
            data["restorable"] = self.restorable
        return data


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
        if (
            _DIGEST.fullmatch(self.resource_identity) is None
            or type(self.spine_ordinal) is not int
            or self.spine_ordinal < 1
            or type(self.tail_slot) is not int
            or not 1 <= self.tail_slot <= 8
        ):
            raise ReportValueError("rear claim provenance is invalid")
        _projection_text(self.marker, "rear marker", 64)
        _projection_text(self.citation, "rear citation", 280)

    def to_data(self) -> dict[str, object]:
        return {
            "citation": self.citation,
            "marker": self.marker,
            "resource_identity": self.resource_identity,
            "spine_ordinal": self.spine_ordinal,
            "tail_slot": self.tail_slot,
        }


@dataclass(frozen=True, slots=True)
class FieldResult:
    """Closed aggregate state for one field, independent of source claims."""

    kind: ClaimKind
    state: FieldState
    sources: tuple[FieldSourceResult, ...] = ()

    def __post_init__(self) -> None:
        if type(self.kind) is not ClaimKind or type(self.state) is not FieldState:
            raise ReportValueError("field result is invalid")
        if (
            type(self.sources) is not tuple
            or any(type(value) is not FieldSourceResult for value in self.sources)
            or (
                self.sources
                and tuple(value.source for value in self.sources) != tuple(FieldSource)
            )
        ):
            raise ReportValueError("field result sources are invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "state": self.state.value,
            "sources": [value.to_data() for value in self.sources],
        }


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


# A lane that ended in one of these may answer differently when it is asked
# again, so provider recovery retries it and a carried admission survives it.
PROVIDER_RECOVERY_ELIGIBLE_STATUSES = frozenset(
    {
        ProviderStatus.AUTHENTICATION,
        ProviderStatus.AUTHORIZATION,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.UNAVAILABLE,
        ProviderStatus.INCOMPLETE,
    }
)


class DispatchOutcome(StrEnum):
    """How one audiobook lane's dispatch ended (#1258).

    An outcome rather than a stage, so it never reads as an ADR-0017 diagnostic
    stage or a stage-timing one. `empty_or_refused` is the honest outcome of a
    WORK lane whose provider does not record refusals.
    """

    ADMITTED = "admitted"
    EMPTY = "empty"
    REFUSED = "refused"
    OVERFLOW = "overflow"
    UNAVAILABLE = "unavailable"
    STARVED = "starved"
    NOT_DISPATCHED = "not_dispatched"
    EMPTY_OR_REFUSED = "empty_or_refused"


# The one outcome each lane status allows, beside the two statuses that need
# more than the status: a no-match lane's ladder and an incomplete lane's cause.
_DISPATCH_OUTCOMES = {
    ProviderStatus.CANDIDATES: DispatchOutcome.ADMITTED,
    ProviderStatus.CANDIDATE_LIMIT_EXCEEDED: DispatchOutcome.OVERFLOW,
    ProviderStatus.INCOMPLETE: DispatchOutcome.UNAVAILABLE,
    ProviderStatus.UNAVAILABLE: DispatchOutcome.UNAVAILABLE,
    ProviderStatus.RATE_LIMITED: DispatchOutcome.UNAVAILABLE,
    ProviderStatus.AUTHENTICATION: DispatchOutcome.UNAVAILABLE,
    ProviderStatus.AUTHORIZATION: DispatchOutcome.UNAVAILABLE,
    ProviderStatus.MALFORMED: DispatchOutcome.UNAVAILABLE,
    ProviderStatus.OVERSIZED: DispatchOutcome.UNAVAILABLE,
    ProviderStatus.INSUFFICIENT_INPUT: DispatchOutcome.NOT_DISPATCHED,
    ProviderStatus.DISABLED: DispatchOutcome.NOT_DISPATCHED,
}


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
            self.logical_requests,
            self.initial_dispatches,
            self.retry_dispatches,
            self.replays,
            self.undispatched_incomplete,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ReportValueError("provider request accounting is invalid")
        if self.logical_requests != (
            self.initial_dispatches + self.replays + self.undispatched_incomplete
        ):
            raise ReportValueError(
                "provider logical request accounting is inconsistent"
            )
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
    ASIN = "asin"


PROVIDER_LANE_KINDS = frozenset(
    {ProviderQueryKind.ISBN, ProviderQueryKind.WORK, ProviderQueryKind.ASIN}
)
PROVIDER_AUTHORITIES = frozenset(
    {
        None,
        "audible_catalog",
        "audiosilo",
        "audnexus",
        "google_books",
        "hardcover",
        "itunes_search",
        "inventaire",
        "open_library",
    }
)


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
    # Two running times that name the same recording within the agreement band.
    # Only a `DURATION` claim reaches it, and only an audiobook provider states
    # one, so no ebook comparison changes shape.
    DURATION_BAND_AGREEMENT = "duration_band_agreement"


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
    RECORDING_ASSOCIATED = "recording_associated"
    UNRELATED = "unrelated"
    INVALID = "invalid"
    UNRESOLVED = "unresolved"


class IdentifierEvidenceBasis(StrEnum):
    """Closed, deterministic reason for an ISBN relationship."""

    RECORDING_ASSOCIATED = "recording_associated"
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
            IdentifierRelation.RECORDING_ASSOCIATED: {
                IdentifierEvidenceBasis.RECORDING_ASSOCIATED
            },
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
            or any(
                _REFERENCE.fullmatch(value) is None for value in self.related_references
            )
        ):
            raise ReportValueError("identifier evidence is invalid")
        provider_values = (
            self.provider,
            self.candidate_identity,
            self.work_identity,
        )
        if self.reference.startswith("local_"):
            if any(value is not None for value in provider_values):
                raise ReportValueError(
                    "local identifier evidence has provider identity"
                )
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
    local_reference: str | None
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
            date_valid = type(date_relation) is PublicationDateRelation and (
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
        if (
            (
                self.local_reference is not None
                and (
                    _REFERENCE.fullmatch(self.local_reference) is None
                    or not self.local_reference.startswith("local_")
                )
            )
            or (
                self.local_reference is None
                and date_relation
                is not PublicationDateRelation.MANIFESTATION_INCOMPATIBLE
            )
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


def _comparison_reference_sort_key(value: str | None) -> tuple[int, str]:
    return (0, "") if value is None else (1, value)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise ReportValueError("report value is not serializable") from error


def stable_reference(prefix: str, value: object) -> str:
    if prefix not in {"local", "provider"}:
        raise ReportValueError("claim reference prefix is invalid")
    return (
        f"{prefix}_{hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()}"
    )


def provider_lane_candidate_reference(
    provider: str,
    lane_identity: str,
    edition_identity: str,
    work_identity: str,
) -> str:
    return (
        "lane_candidate_"
        + hashlib.sha256(
            canonical_json(
                {
                    "edition_identity": edition_identity,
                    "lane_identity": lane_identity,
                    "provider": provider,
                    "work_identity": work_identity,
                }
            ).encode("utf-8")
        ).hexdigest()
    )


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
    try:
        return _normalize_date_value(value)
    except ValueError as error:
        raise ReportValueError("date lexical value is invalid") from error


def publication_date_is_voting(value: str, precision: DatePrecision) -> bool:
    """Accept only source lexicals whose retained precision can vote."""
    if type(precision) is not DatePrecision:
        raise ReportValueError("date precision is inconsistent")
    _, actual = normalize_date_value(value)
    if actual is not precision:
        raise ReportValueError("date precision is inconsistent")
    return (
        precision is not DatePrecision.TIMESTAMP
        or _EXPLICIT_TIMESTAMP_OFFSET.search(value) is not None
    )


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
                value["connection"],
                value["library"],
                value["item"],
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


AUDIO_FILE_FORMATS = frozenset({"m4b", "m4a", "mp3"})
SUPPORTED_FILE_FORMATS = frozenset({"epub"}) | AUDIO_FILE_FORMATS
AUDIO_SET_SCHEMA = "audiobookshelf-audio-set-v1"


def audio_set_identity(identities: tuple[str, ...]) -> str:
    """Name the ordered set of audio files one report was prepared from.

    A one-track set is its own track's identity, unchanged: a single-file
    audiobook is the same preparable unit it was before multi-file items were
    admitted, and giving it a new identity would restate every existing report
    as a different one.  Beyond one track the identity is the ordered list, so
    adding, removing, reordering, or replacing any track moves it exactly once.
    """
    if type(identities) is not tuple or not identities:
        raise ReportValueError("audio set identity is invalid")
    for value in identities:
        if _DIGEST.fullmatch(value) is None:
            raise ReportValueError("audio set identity is invalid")
    if len(identities) == 1:
        return identities[0]
    return hashlib.sha256(
        canonical_json({"schema": AUDIO_SET_SCHEMA, "tracks": list(identities)}).encode(
            "utf-8"
        )
    ).hexdigest()


def audio_set_content_digest(digests: tuple[str, ...]) -> str:
    """Digest the ordered content of every track as one item content digest."""
    if type(digests) is not tuple or not digests:
        raise ReportValueError("audio set content digest is invalid")
    for value in digests:
        if _DIGEST.fullmatch(value) is None:
            raise ReportValueError("audio set content digest is invalid")
    if len(digests) == 1:
        return digests[0]
    return hashlib.sha256(canonical_json(list(digests)).encode("utf-8")).hexdigest()


# The two item surfaces a discovery title is read from, in the order a plan
# prefers them: the folder leaf, then the single-file title tag (#1255).
AUDIO_DISCOVERY_TITLE_BASES = ("path_leaf", "title_tag")


@dataclass(frozen=True, slots=True)
class AudioDiscoveryTitle:
    """One query-only alternative title read from the item's own surfaces.

    Discovery input only: it is never a local claim, a field result, or a
    proposal value, and it asserts no identity. A provider candidate it finds
    is retained only when it admits against this title and the runtime band
    corroborates it.
    """

    basis: str
    title: str

    def __post_init__(self) -> None:
        if type(self.basis) is not str or self.basis not in AUDIO_DISCOVERY_TITLE_BASES:
            raise ReportValueError("audio discovery title is invalid")
        _projection_text(self.title, "audio discovery title", MAX_OBSERVED_CHARACTERS)
        if normalize_title(self.title) != self.title:
            raise ReportValueError("audio discovery title is invalid")

    def to_data(self) -> dict[str, str]:
        return {"basis": self.basis, "title": self.title}


@dataclass(frozen=True, slots=True)
class AudioFacts:
    """The bounded structured audio section of one audiobook report.

    Chapters are structure, not claims: they name no target field, are never
    compared, and can never become a proposal.  The embedded cover is a
    presence fact for the same reason -- it grants no acquisition authority and
    is deliberately not a ``CoverSource``.

    ``sidecars`` is not evidence either: it is the currentness witness for the
    documents beside the file that evidence was read from, so an added,
    removed, or edited sidecar makes this report stale.  ``tracks`` is the same
    kind of witness for the audio files themselves, and additionally carries
    the per-file provenance of every composed claim.  It always names at least
    one file: an audio section states evidence read from the item's audio, and
    a section naming no source is a section nothing can be checked against.

    ``observations`` names what composing the set noticed about the set.  None
    of them is evidence about the work, and the vocabulary is closed.

    ``discovery_titles`` holds at most one title per surface, folder leaf
    first, no two of them the same title.  They shape one alternative WORK
    query and nothing else.
    """

    chapters: tuple[AudioChapter, ...] = ()
    chapters_truncated: bool = False
    embedded_cover: AudioEmbeddedCover = AudioEmbeddedCover(False)
    sidecars: tuple[AudioSidecarDigest, ...] = ()
    composition: AudioChapterComposition = AudioChapterComposition.SINGLE_FILE
    tracks: tuple[AudioTrackDigest, ...] = ()
    observations: tuple[AudioObservation, ...] = ()
    chapter_rows_omitted: bool = False
    discovery_titles: tuple[AudioDiscoveryTitle, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.chapters) is not tuple
            or len(self.chapters) > MAX_AUDIO_CHAPTERS
            or any(type(value) is not AudioChapter for value in self.chapters)
            or type(self.chapters_truncated) is not bool
            or type(self.chapter_rows_omitted) is not bool
            or type(self.embedded_cover) is not AudioEmbeddedCover
        ):
            raise ReportValueError("audio facts are invalid")
        if tuple(value.ordinal for value in self.chapters) != tuple(
            range(1, len(self.chapters) + 1)
        ):
            raise ReportValueError("audio chapter ordinals are invalid")
        names = tuple(value.name for value in self.sidecars)
        if (
            type(self.sidecars) is not tuple
            or len(self.sidecars) > MAX_AUDIO_SIDECARS
            or any(type(value) is not AudioSidecarDigest for value in self.sidecars)
            or names != tuple(sorted(set(names)))
        ):
            raise ReportValueError("audio sidecar manifest is invalid")
        if (
            type(self.composition) is not AudioChapterComposition
            or type(self.tracks) is not tuple
            or not 1 <= len(self.tracks) <= MAX_AUDIO_TRACKS
            or any(type(value) is not AudioTrackDigest for value in self.tracks)
            or tuple(value.ordinal for value in self.tracks)
            != tuple(range(1, len(self.tracks) + 1))
            or any(value.format not in AUDIO_FILE_FORMATS for value in self.tracks)
        ):
            raise ReportValueError("audio track manifest is invalid")
        if (self.composition is AudioChapterComposition.SINGLE_FILE) != (
            len(self.tracks) == 1
        ):
            # One file composes to `single_file` and nothing else does, so the
            # kind and the manifest can never state different set sizes.  An
            # empty manifest was refused above: an audio section is evidence
            # read from files, and one naming none of them names no source for
            # the digest and provenance checks below to hold against.
            raise ReportValueError("audio composition does not match its tracks")
        if (
            type(self.observations) is not tuple
            or len(self.observations) > len(AudioObservationKind) * MAX_AUDIO_TRACKS
            or any(type(value) is not AudioObservation for value in self.observations)
            or any(
                ordinal > len(self.tracks)
                for value in self.observations
                for ordinal in value.tracks
            )
            or any(ordinal > len(self.tracks) for ordinal in self.embedded_cover.tracks)
        ):
            raise ReportValueError("audio observations are invalid")
        titles = self.discovery_titles
        if (
            type(titles) is not tuple
            or any(type(value) is not AudioDiscoveryTitle for value in titles)
            or [AUDIO_DISCOVERY_TITLE_BASES.index(value.basis) for value in titles]
            != sorted(
                {AUDIO_DISCOVERY_TITLE_BASES.index(value.basis) for value in titles}
            )
            or any(
                _closed_title_equal(value.title, other.title)
                for index, value in enumerate(titles)
                for other in titles[index + 1 :]
            )
        ):
            raise ReportValueError("audio discovery titles are invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "chapters": [value.to_data() for value in self.chapters],
            "chapters_truncated": self.chapters_truncated,
            "chapter_rows_omitted": self.chapter_rows_omitted,
            "composition": self.composition.value,
            "discovery_titles": [value.to_data() for value in self.discovery_titles],
            "embedded_cover": self.embedded_cover.to_data(),
            "observations": [value.to_data() for value in self.observations],
            "sidecars": [value.to_data() for value in self.sidecars],
            "tracks": [value.to_data() for value in self.tracks],
        }


@dataclass(frozen=True, slots=True)
class FileFacts:
    selected_index: int
    identity: str
    content_digest: str
    format: str = "epub"

    def __post_init__(self) -> None:
        if (
            type(self.selected_index) is not int
            or not 0 <= self.selected_index <= 10_000
        ):
            raise ReportValueError("selected file index is invalid")
        if (
            _DIGEST.fullmatch(self.identity) is None
            or _DIGEST.fullmatch(self.content_digest) is None
        ):
            raise ReportValueError("selected file identity is invalid")
        if self.format not in SUPPORTED_FILE_FORMATS:
            raise ReportValueError("selected file format is unsupported")

    def to_data(self) -> dict[str, object]:
        return {
            "content_digest": self.content_digest,
            "format": self.format,
            "identity": self.identity,
            "selected_index": self.selected_index,
        }


# The claim kinds an audiobook's companion EPUB contributes (#1319): the ones
# that name the work and its publication, read as corroboration only.
COMPANION_EBOOK_CLAIM_KINDS = frozenset(
    {ClaimKind.TITLE, ClaimKind.CREATOR, ClaimKind.ISBN, ClaimKind.PUBLISHER}
)


@dataclass(frozen=True, slots=True)
class CompanionEbook:
    """The EPUB an audiobook item holds beside its tracks (#1319).

    It is corroborating local evidence about the recording's work, never the
    preparable unit and never a source of proposed values: its claims live
    here rather than among the report's local claims, so nothing that reads
    those claims -- proposals, discovery, admission -- reads an ebook's ISBN
    or title as the recording's own. `content_digest` is `None` exactly when
    the EPUB could not be inspected; the recording is prepared regardless.
    """

    identity: str
    content_digest: str | None
    claims: tuple[LocalClaim, ...] = ()

    def __post_init__(self) -> None:
        if _DIGEST.fullmatch(self.identity) is None or (
            self.content_digest is not None
            and (
                type(self.content_digest) is not str
                or _DIGEST.fullmatch(self.content_digest) is None
            )
        ):
            raise ReportValueError("companion ebook identity is invalid")
        if (
            type(self.claims) is not tuple
            or any(
                type(value) is not LocalClaim
                or value.kind not in COMPANION_EBOOK_CLAIM_KINDS
                or value.basis not in _SOURCE_CLAIM_BASES[FieldSource.EPUB]
                for value in self.claims
            )
            or (self.content_digest is None and self.claims)
        ):
            raise ReportValueError("companion ebook claims are invalid")
        references = tuple(value.reference for value in self.claims)
        if len(set(references)) != len(references):
            raise ReportValueError("companion ebook claim references are duplicated")

    def to_data(self) -> dict[str, object]:
        return {
            "claims": [value.to_data() for value in self.claims],
            "content_digest": self.content_digest,
            "identity": self.identity,
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
        if _REFERENCE.fullmatch(
            self.reference
        ) is None or not self.reference.startswith("local_"):
            raise ReportValueError("local claim reference is invalid")
        if (
            type(self.kind) is not ClaimKind
            or type(self.state) is not FieldState
            or type(self.scope) is not ClaimScope
        ):
            raise ReportValueError("local claim kind is invalid")
        # The target asserts exactly two things this product reads as claims:
        # the title it displays, and the ASIN an owner or an earlier tool bound
        # to the item. Both are assertions rather than readings of the media,
        # so nothing here settles identity on them; the ASIN is admitted so the
        # audiobook path can obtain enrichment evidence the runtime band then
        # judges (ADR-0061).
        if self.basis == "target_metadata" and self.kind not in {
            ClaimKind.TITLE,
            ClaimKind.ASIN,
        }:
            raise ReportValueError("target metadata claim kind is invalid")
        # A carried admission names one edition and nothing else (#1316).
        if self.basis == "prior_admission" and (
            self.kind is not ClaimKind.ASIN or self.scope is not ClaimScope.EDITION
        ):
            raise ReportValueError("prior admission claim kind is invalid")
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
        if (
            self.rear_provenance is not None
            and type(self.rear_provenance) is not RearClaimProvenance
        ):
            raise ReportValueError("rear claim provenance is invalid")
        rear_kinds = {
            ClaimKind.TITLE,
            ClaimKind.SUBTITLE,
            ClaimKind.CREATOR,
            ClaimKind.CONTRIBUTOR,
            ClaimKind.SERIES,
            ClaimKind.SERIES_SEQUENCE,
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
        maximum = (
            512
            if self.kind in {ClaimKind.TITLE, ClaimKind.SUBTITLE, ClaimKind.DESCRIPTION}
            else 280
        )
        _projection_text(self.observed, "observed claim", maximum)
        if self.canonical is not None:
            _projection_text(self.canonical, "canonical claim", 512)
        _projection_text(self.excerpt, "claim excerpt", maximum)
        if self.observed not in self.excerpt:
            raise ReportValueError("claim excerpt does not support its observation")
        if self.applicability not in {
            "ebook",
            "print",
            "unknown",
            "ambiguous",
            "not_applicable",
        }:
            raise ReportValueError("claim applicability is invalid")
        if self.basis not in CLAIM_BASES:
            raise ReportValueError("claim basis is invalid")
        if self.checksum not in {
            "valid_isbn_10",
            "valid_isbn_13",
            "invalid",
            "not_applicable",
        }:
            raise ReportValueError("claim checksum is invalid")
        if self.applicability_basis not in {
            "ebook",
            "paperback",
            "hardcover",
            "print",
            "unlabelled",
            "ambiguous",
            "not_applicable",
        }:
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
        if (
            self.kind is ClaimKind.ISBN
            and self.canonical is not None
            and not _valid_isbn(self.canonical, self.checksum)
        ):
            raise ReportValueError("ISBN claim checksum is inconsistent")
        is_date = self.kind in {ClaimKind.PUBLICATION_DATE, ClaimKind.DATE_EVENT}
        if is_date != (type(self.date_event) is DateEvent):
            raise ReportValueError("local date claim typing is inconsistent")
        if (
            (
                is_date
                and self.state is FieldState.VALID
                and (
                    type(self.date_precision) is not DatePrecision
                    or self.canonical is None
                )
            )
            or (
                is_date
                and self.state is not FieldState.VALID
                and (self.canonical is not None or self.date_precision is not None)
            )
            or (not is_date and self.date_precision is not None)
        ):
            raise ReportValueError("local date precision is inconsistent")
        if is_date and self.state is FieldState.VALID:
            try:
                normalized_date = normalize_date_value(self.observed)
            except ReportValueError as error:
                raise ReportValueError(
                    "local date precision is inconsistent"
                ) from error
            if normalized_date != (self.canonical, self.date_precision):
                raise ReportValueError("local date precision is inconsistent")
        if (
            self.kind is ClaimKind.PUBLICATION_DATE
            and self.date_event is not DateEvent.MANIFESTATION_PUBLICATION
        ):
            raise ReportValueError("local publication date event is inconsistent")
        if is_date and self.scope is not claim_scope(self.kind, self.date_event):
            raise ReportValueError("local date claim scope is inconsistent")
        sentinel = _sentinel_state(self.observed)
        if sentinel is not None and (
            self.state is not sentinel or self.canonical is not None
        ):
            raise ReportValueError("source sentinel claim is inconsistent")
        if (
            self.state is FieldState.VALID
            and self.kind in {ClaimKind.TITLE, ClaimKind.SUBTITLE}
            and self.canonical != normalize_title(self.observed)
        ):
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
        resolved_scope = scope if scope is not None else claim_scope(kind, date_event)
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
                resolved_scope.value if type(resolved_scope) is ClaimScope else ""
            ),
            "role": role,
            "rear_provenance": None
            if rear_provenance is None
            else rear_provenance.to_data(),
            "occurrence": occurrence,
            "date_event": None if date_event is None else date_event.value,
            "date_precision": (
                None if date_precision is None else date_precision.value
            ),
        }
        return cls(
            stable_reference("local", value),
            kind,
            observed,
            canonical,
            excerpt,
            applicability,
            basis,
            checksum,
            applicability_basis,
            resolved_scope,
            state,
            role,
            rear_provenance,
            occurrence,
            date_event,
            date_precision,
        )

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
            "rear_provenance": None
            if self.rear_provenance is None
            else self.rear_provenance.to_data(),
            "occurrence": self.occurrence,
            "date_event": (None if self.date_event is None else self.date_event.value),
            "date_precision": (
                None if self.date_precision is None else self.date_precision.value
            ),
        }

    def to_data(self) -> dict[str, object]:
        return {
            "reference": self.reference,
            "state": self.state.value,
            **self._reference_data(),
        }


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
        if _REFERENCE.fullmatch(
            self.reference
        ) is None or not self.reference.startswith("provider_"):
            raise ReportValueError("provider claim reference is invalid")
        if type(self.kind) is not ClaimKind:
            raise ReportValueError("provider claim is invalid")
        if (
            self.applicability
            not in {"ebook", "print", "unknown", "ambiguous", "not_applicable"}
            or type(self.state) is not FieldState
            or type(self.raw_order) is not int
            or self.raw_order < 0
            or type(self.scope) is not ClaimScope
            or type(self.links) is not tuple
            or any(type(link) is not ComparisonLink for link in self.links)
        ):
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
                MAX_DESCRIPTION_CHARACTERS
                if self.kind is ClaimKind.DESCRIPTION
                else MAX_SCALAR_CHARACTERS,
            )
        elif self.value is not None:
            raise ReportValueError("nonvalid provider claim has a value")
        if (
            self.kind is ClaimKind.ISBN
            and self.value is not None
            and not (
                _valid_isbn(self.value, "valid_isbn_10")
                or _valid_isbn(self.value, "valid_isbn_13")
            )
        ):
            raise ReportValueError("provider ISBN claim is invalid")
        if (
            self.kind is ClaimKind.ASIN
            and self.value is not None
            # One grammar for the whole identifier family: the same canonical
            # form the ASIN discovery lane is built from. ADR-0061 clause 4
            # admits ISBN-10-shaped ASINs, so a `B0` filter here would refuse
            # 17 of the reference library's 103 genuine identifiers.
            and canonical_asin(self.value) != self.value
        ):
            raise ReportValueError("provider ASIN claim is invalid")
        if self.kind is ClaimKind.ADULT_PRODUCT and (
            self.scope is not ClaimScope.EDITION
            or self.applicability != "not_applicable"
            or self.state is FieldState.VALID
            and self.value not in {"true", "false"}
        ):
            raise ReportValueError("provider adult product claim is invalid")
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
            (self.scope, self.date_event)
            not in {
                (ClaimScope.EDITION, DateEvent.MANIFESTATION_PUBLICATION),
                (ClaimScope.WORK, DateEvent.WORK_FIRST_PUBLICATION),
            }
            or self.date_confidence is not DateLinkConfidence.EXPLICIT
        ):
            raise ReportValueError("provider publication date linkage is inconsistent")
        references = tuple(link.local_reference for link in self.links)
        if references != tuple(
            sorted(set(references), key=_comparison_reference_sort_key)
        ):
            raise ReportValueError("provider comparison links are invalid")
        if any(
            (link.publication_date_relation is None) == is_date for link in self.links
        ):
            raise ReportValueError("provider date comparison links are inconsistent")
        if self.state is FieldState.VALID and self.kind in {
            ClaimKind.TITLE,
            ClaimKind.SUBTITLE,
        }:
            validate_observed_title(self.value)

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
        if kind is ClaimKind.ASIN and state is FieldState.VALID and type(value) is str:
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
            "date_event": (None if self.date_event is None else self.date_event.value),
            "date_precision": (
                None if self.date_precision is None else self.date_precision.value
            ),
            "date_lexical": self.date_lexical,
            "date_confidence": (
                None if self.date_confidence is None else self.date_confidence.value
            ),
        }


@dataclass(frozen=True, slots=True)
class ProviderChapterStructure:
    """One recording's bounded structure as a provider stated it.

    Durations are whole seconds because that is the coarsest granularity any
    admitted provider states and the finest either side can be trusted at; the
    vector is capped at the same chapter count the local inspector admits, with
    an explicit overflow flag, because a report document that cannot be
    published is evidence nobody has.
    """

    chapter_count: int
    runtime_seconds: int
    durations_seconds: tuple[int, ...] = ()
    durations_truncated: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.chapter_count) is not int
            or not 0 <= self.chapter_count <= 2_147_483_647
            or type(self.runtime_seconds) is not int
            or not 0 <= self.runtime_seconds <= 604_800
            or type(self.durations_truncated) is not bool
            or type(self.durations_seconds) is not tuple
            or len(self.durations_seconds) > MAX_PROVIDER_CHAPTER_DURATIONS
            or any(
                type(value) is not int or not 0 <= value <= 604_800
                for value in self.durations_seconds
            )
        ):
            raise ReportValueError("provider chapter structure is invalid")
        if self.durations_truncated:
            if len(self.durations_seconds) != MAX_PROVIDER_CHAPTER_DURATIONS:
                raise ReportValueError("provider chapter overflow is inconsistent")
        elif len(self.durations_seconds) not in {0, self.chapter_count}:
            raise ReportValueError("provider chapter vector is inconsistent")
        if self.durations_truncated and self.chapter_count <= len(
            self.durations_seconds
        ):
            raise ReportValueError("provider chapter overflow is inconsistent")

    def to_data(self) -> dict[str, object]:
        return {
            "chapter_count": self.chapter_count,
            "durations_seconds": list(self.durations_seconds),
            "durations_truncated": self.durations_truncated,
            "runtime_seconds": self.runtime_seconds,
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
    taxonomy_type: str | None = None
    chapters: ProviderChapterStructure | None = None

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not ProviderEvidenceKind
            or type(self.scope) is not ClaimScope
        ):
            raise ReportValueError("provider rich evidence is invalid")
        if (
            type(self.source_ordinal) is not int
            or not 0 <= self.source_ordinal <= 2_147_483_647
            or type(self.links) is not tuple
            or any(type(value) is not ComparisonLink for value in self.links)
            or tuple(value.local_reference for value in self.links)
            != tuple(
                sorted(
                    {value.local_reference for value in self.links},
                    key=_comparison_reference_sort_key,
                )
            )
        ):
            raise ReportValueError("provider rich evidence source ordinal is invalid")
        _projection_text(
            self.value, "provider rich evidence value", MAX_SCALAR_CHARACTERS
        )
        if self.kind is ProviderEvidenceKind.TAXONOMY:
            _projection_text(
                self.taxonomy_type, "provider taxonomy type", MAX_SCALAR_CHARACTERS
            )
        elif self.taxonomy_type is not None:
            raise ReportValueError("provider taxonomy type is invalid")
        if (
            self.kind is ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION
            and self.value != "contradictory_or_multiple"
        ):
            raise ReportValueError("provider work relation contradiction is invalid")
        if (
            self.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
            and self.value not in AUDIOBOOK_MARKETPLACES
        ):
            raise ReportValueError("provider ASIN marketplace is invalid")
        if self.kind is ProviderEvidenceKind.CHAPTER_STRUCTURE:
            if type(self.chapters) is not ProviderChapterStructure or self.value != str(
                self.chapters.runtime_seconds
            ):
                raise ReportValueError("provider chapter structure is invalid")
        elif self.chapters is not None:
            raise ReportValueError("provider chapter structure is misbound")
        if self.kind is ProviderEvidenceKind.CONTRIBUTOR_IDENTITY:
            if self.role is None:
                raise ReportValueError("provider rich evidence role is invalid")
            try:
                validate_role_projection(self.role)
            except EvidenceValueError as error:
                raise ReportValueError(
                    "provider rich evidence role is invalid"
                ) from error
        elif self.role is not None:
            raise ReportValueError("provider rich evidence role is invalid")
        date_events = {
            ProviderEvidenceKind.COPYRIGHT_DATE: DateEvent.COPYRIGHT,
            ProviderEvidenceKind.FIRST_PUBLICATION_DATE: (
                DateEvent.WORK_FIRST_PUBLICATION
            ),
            ProviderEvidenceKind.LAST_MODIFIED: DateEvent.MODIFICATION,
        }
        is_publication_text = self.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
        expected_event = date_events.get(self.kind)
        if is_publication_text and self.date_event is None:
            expected_event = DateEvent.UNKNOWN
        if expected_event is not None and self.date_event is None:
            object.__setattr__(self, "date_event", expected_event)
        if (
            expected_event is not None or is_publication_text
        ) and self.date_confidence is None:
            object.__setattr__(
                self,
                "date_confidence",
                (
                    DateLinkConfidence.UNRESOLVED
                    if is_publication_text
                    else DateLinkConfidence.EXPLICIT
                ),
            )
        if (
            expected_event is not None or is_publication_text
        ) and self.date_normalized is None:
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
                or (not is_publication_text and self.date_event is not expected_event)
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
        if any(link.publication_date_relation is None for link in self.links) or (
            not is_date and self.links
        ):
            raise ReportValueError("provider date evidence links are inconsistent")
        if any(link.local_reference is None for link in self.links) and not (
            is_publication_text
            and self.date_normalized is not None
            and type(self.date_precision) is DatePrecision
            and self.date_event is DateEvent.MANIFESTATION_PUBLICATION
            and self.date_confidence is DateLinkConfidence.UNRESOLVED
        ):
            raise ReportValueError("provider date evidence links are inconsistent")

    def to_data(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "role": self.role,
            "scope": self.scope.value,
            "value": self.value,
            "date_event": (None if self.date_event is None else self.date_event.value),
            "date_precision": (
                None if self.date_precision is None else self.date_precision.value
            ),
            "date_normalized": self.date_normalized,
            "date_confidence": (
                None if self.date_confidence is None else self.date_confidence.value
            ),
            "source_ordinal": self.source_ordinal,
            "links": [value.to_data() for value in self.links],
            "taxonomy_type": self.taxonomy_type,
            "chapters": None if self.chapters is None else self.chapters.to_data(),
        }


@dataclass(frozen=True, slots=True)
class ProviderIdentityBinding:
    """One structurally parsed provider name-to-identity relationship."""

    claim_reference: str
    identity: str
    scope: ClaimScope
    role: str | None
    source_ordinal: int = 0

    def __post_init__(self) -> None:
        if (
            _REFERENCE.fullmatch(self.claim_reference) is None
            or not self.claim_reference.startswith("provider_")
            or type(self.scope) is not ClaimScope
            or self.role not in {None, "author"}
            or type(self.source_ordinal) is not int
            or not 0 <= self.source_ordinal <= 2_147_483_647
        ):
            raise ReportValueError("provider identity binding is invalid")
        _text(self.identity, "provider identity binding identity", 128)
        if self.role is not None:
            try:
                validate_role_projection(self.role)
            except EvidenceValueError as error:
                raise ReportValueError(
                    "provider identity binding role is invalid"
                ) from error

    def to_data(self) -> dict[str, object]:
        return {
            "claim_reference": self.claim_reference,
            "identity": self.identity,
            "role": self.role,
            "scope": self.scope.value,
            "source_ordinal": self.source_ordinal,
        }


@dataclass(frozen=True, slots=True)
class EditionEnrichmentOverflow:
    """Work-only evidence retained after a bounded edition probe overflows."""

    work_identity: str
    title: str
    authors: tuple[ProviderClaim, ...]
    identity_bindings: tuple[ProviderIdentityBinding, ...]

    def __post_init__(self) -> None:
        _text(self.work_identity, "edition overflow work identity", 128)
        _projection_text(self.title, "edition overflow title", MAX_SCALAR_CHARACTERS)
        if (
            type(self.authors) is not tuple
            or not self.authors
            or any(
                type(value) is not ProviderClaim
                or value.kind is not ClaimKind.CREATOR
                or value.scope is not ClaimScope.WORK
                or value.state is not FieldState.VALID
                or value.role != "author"
                for value in self.authors
            )
            or tuple(value.raw_order for value in self.authors)
            != tuple(range(len(self.authors)))
            or type(self.identity_bindings) is not tuple
            or len(self.identity_bindings) != len(self.authors)
        ):
            raise ReportValueError("edition overflow authors are invalid")
        expected = tuple(
            ProviderIdentityBinding(
                author.reference, binding.identity, ClaimScope.WORK, "author", index
            )
            for index, (author, binding) in enumerate(
                zip(self.authors, self.identity_bindings, strict=True)
            )
        )
        if self.identity_bindings != expected:
            raise ReportValueError("edition overflow identity bindings are invalid")
        references = tuple(author.reference for author in self.authors)
        identities = tuple(binding.identity for binding in self.identity_bindings)
        names = tuple(
            normalize_title(author.value or "").casefold() for author in self.authors
        )
        if (
            len(set(references)) != len(references)
            or len(set(identities)) != len(identities)
            or len(set(names)) != len(names)
        ):
            raise ReportValueError("edition overflow authors are contradictory")

    def to_data(self) -> dict[str, object]:
        return {
            "authors": [value.to_data() for value in self.authors],
            "identity_bindings": [value.to_data() for value in self.identity_bindings],
            "title": self.title,
            "work_identity": self.work_identity,
        }


@dataclass(frozen=True, slots=True)
class ProviderCandidate:
    identity: str
    work_identity: str
    claims: tuple[ProviderClaim, ...]
    evidence: tuple[ProviderEvidence, ...] = ()
    identity_bindings: tuple[ProviderIdentityBinding, ...] = ()
    # Not a claim and not evidence: the provider stated no such thing.  This
    # records that qualification compared this candidate's stated running time
    # to the recording on the mount and found the recording more than the
    # tight band shorter -- a comparison it makes *before* demotion strips an
    # uncorroborated candidate's edition-scope `DURATION` claim, which is why
    # nothing downstream could ask the question again.  It carries no number
    # for the same reason a demoted candidate carries no runtime: the value
    # belonged to the edition scope that was not earned.
    runtime_short_of_local: bool = False

    def __post_init__(self) -> None:
        _text(self.identity, "provider candidate identity", 128)
        _text(self.work_identity, "provider candidate work identity", 128)
        if type(self.runtime_short_of_local) is not bool:
            raise ReportValueError("provider candidate runtime comparison is invalid")
        if type(self.claims) is not tuple or any(
            type(value) is not ProviderClaim for value in self.claims
        ):
            raise ReportValueError("provider candidate claims are invalid")
        references = tuple(value.reference for value in self.claims)
        if len(set(references)) != len(references):
            raise ReportValueError("provider candidate claim references are duplicated")
        if tuple(value.raw_order for value in self.claims) != tuple(
            sorted(value.raw_order for value in self.claims)
        ):
            raise ReportValueError(
                "provider candidate claims are not in raw source order"
            )
        claims_by_reference = {value.reference: value for value in self.claims}
        if (
            type(self.identity_bindings) is not tuple
            or len(self.identity_bindings) > MAX_CONTRIBUTIONS
            or any(
                type(value) is not ProviderIdentityBinding
                for value in self.identity_bindings
            )
            or len(set(self.identity_bindings)) != len(self.identity_bindings)
            or len({value.claim_reference for value in self.identity_bindings})
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
                        value.role or "",
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
            ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION,
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
            # A marketplace and a running structure are properties of one
            # recorded manifestation, exactly as `DURATION` is.
            ProviderEvidenceKind.ASIN_MARKETPLACE,
            ProviderEvidenceKind.CHAPTER_STRUCTURE,
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
            ProviderEvidenceKind.TAXONOMY,
            ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION,
        }
        work_relations = tuple(
            value
            for value in self.evidence
            if value.kind is ProviderEvidenceKind.EDITION_WORK_RELATION
        )
        work_relation_contradictions = tuple(
            value
            for value in self.evidence
            if value.kind is ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION
        )
        # One candidate is one recording, so it has one structure and resolved
        # in one marketplace. Two of either is a merge fault, and the vector is
        # also the only unbounded-looking value the document carries.
        if (
            sum(
                value.kind is ProviderEvidenceKind.CHAPTER_STRUCTURE
                for value in self.evidence
            )
            > 1
            or sum(
                value.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
                for value in self.evidence
            )
            > 1
        ):
            raise ReportValueError("provider audio structure evidence is duplicated")
        if (
            len(work_relations) > 1
            or len(work_relation_contradictions) > 1
            or (work_relation_contradictions and len(work_relations) != 1)
            or (
                work_relation_contradictions
                and (
                    any(value.scope is ClaimScope.WORK for value in self.claims)
                    or any(value.scope is ClaimScope.WORK for value in self.evidence)
                    or any(
                        value.scope is ClaimScope.WORK
                        for value in self.identity_bindings
                    )
                )
            )
            or any(
                (value.kind in edition_only and value.scope is not ClaimScope.EDITION)
                or (value.kind in work_only and value.scope is not ClaimScope.WORK)
                or (
                    value.kind is ProviderEvidenceKind.EDITION_WORK_RELATION
                    and value.value != self.work_identity
                )
                for value in self.evidence
            )
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
                        ("" if value.date_event is None else value.date_event.value),
                        (
                            ""
                            if value.date_precision is None
                            else value.date_precision.value
                        ),
                        value.date_normalized or "",
                        value.taxonomy_type or "",
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
            "identity_bindings": [value.to_data() for value in self.identity_bindings],
            "runtime_short_of_local": self.runtime_short_of_local,
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
class RecordingLookupHint:
    """One refused product's ASIN, kept only so it can be looked up (#1257).

    A WORK rung returned the product inside the tight runtime band, and
    work-identity admission refused it. The hint asserts nothing about the
    work or the recording: it records the ASIN, the runtime the product stated
    and the rung that returned it, and the identifier feed asks the per-ASIN
    lanes about it, where admission is unchanged.
    """

    asin: str
    runtime_seconds: int
    rung: WorkDispatchLineage

    def __post_init__(self) -> None:
        if (
            type(self.asin) is not str
            or canonical_asin(self.asin) != self.asin
            or not bounded_integer(1, MAX_DURATION_MILLISECONDS // 1000)(
                self.runtime_seconds
            )
            or type(self.rung) is not WorkDispatchLineage
        ):
            raise ReportValueError("recording lookup hint is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "asin": self.asin,
            "basis": _RECORDING_LOOKUP_HINT_BASIS,
            "rung": self.rung.to_data(),
            "runtime_seconds": self.runtime_seconds,
        }


@dataclass(frozen=True, slots=True)
class ProviderLaneDispatch:
    """One audiobook lane's dispatch outcome, ladder depth and near-miss (#1258).

    Pure observation: it holds a closed outcome and two integers and nothing a
    decision reads. `rungs` counts the WORK rungs the lane attempted, and
    `near_miss_seconds` is the provider-minus-local runtime of the nearest
    title-matching product the loose band contradicted. The report states
    which lanes may carry each and replays both.
    """

    outcome: DispatchOutcome
    rungs: int | None = None
    near_miss_seconds: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.outcome) is not DispatchOutcome
            or (self.rungs is not None and not non_negative_integer(self.rungs))
            or (
                self.near_miss_seconds is not None
                and type(self.near_miss_seconds) is not int
            )
        ):
            raise ReportValueError("provider lane dispatch is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "near_miss_seconds": self.near_miss_seconds,
            "outcome": self.outcome.value,
            "rungs": self.rungs,
        }


def lane_dispatch_outcome(
    lane: ProviderLaneObservation, *, starved: bool
) -> DispatchOutcome | None:
    """Map one lane onto the outcome its status, ladder and cause allow.

    Only an incomplete lane can be starved, and whether it was is the caller's
    fact. A no-match WORK lane is refused or empty only where it walked its
    whole ladder; a provider that keeps no ladder cannot say which.
    """
    if lane.status is ProviderStatus.INCOMPLETE and starved:
        return DispatchOutcome.STARVED
    if lane.status is not ProviderStatus.NO_MATCH:
        return _DISPATCH_OUTCOMES.get(lane.status)
    if lane.kind is not ProviderQueryKind.WORK:
        return DispatchOutcome.EMPTY
    if not lane.exhausted_dispatch:
        return DispatchOutcome.EMPTY_OR_REFUSED
    if any(
        value.category is FailureCategory.WORK_IDENTITY_REFUSED
        for value in lane.diagnostics
    ):
        return DispatchOutcome.REFUSED
    return DispatchOutcome.EMPTY


_STARVATION_DIAGNOSTIC = (FailureCategory.TIMEOUT, FailureStage.REQUEST, False)
# The diagnostics an aggregate-byte and a dispatch-count cause become.
_BUDGET_DIAGNOSTICS = frozenset(
    {
        (FailureCategory.OVERSIZED_RESPONSE, FailureStage.RESPONSE, False),
        (FailureCategory.LOOKUP_CONSTRUCTION, FailureStage.LOOKUP, False),
    }
)


def starvation_backed(
    provider: ProviderObservation, lane: ProviderLaneObservation
) -> bool:
    """Say whether the persisted diagnostics can back a starved lane (#1258).

    The deadline's undispatched request is a non-retryable request timeout,
    stated on the lane by a provider that keeps per-lane causes and on the
    provider otherwise. A provider that also stopped for its byte or dispatch
    budget cannot say the deadline alone stopped the lane.
    """
    provider_keys = {
        (value.category, value.stage, value.retryable) for value in provider.diagnostics
    }
    lane_keys = {
        (value.category, value.stage, value.retryable) for value in lane.diagnostics
    }
    return _STARVATION_DIAGNOSTIC in provider_keys | lane_keys and not (
        provider_keys & _BUDGET_DIAGNOSTICS
    )


def provider_dispatch_class(observation: ProviderObservation) -> DispatchOutcome:
    """Derive one provider's dispatch class from its aggregate status and lanes.

    The aggregate status already ranks the lanes, so this only splits the two
    statuses a lane outcome refines: a refusal outranks an empty ladder, and
    one starved lane makes an incomplete provider starved.
    """
    outcomes = {
        lane.dispatch.outcome for lane in observation.lanes if lane.dispatch is not None
    }
    if observation.status is ProviderStatus.NO_MATCH:
        return next(
            (
                value
                for value in (DispatchOutcome.REFUSED, DispatchOutcome.EMPTY)
                if value in outcomes
            ),
            DispatchOutcome.EMPTY_OR_REFUSED,
        )
    if observation.status is ProviderStatus.INCOMPLETE:
        return (
            DispatchOutcome.STARVED
            if DispatchOutcome.STARVED in outcomes
            else DispatchOutcome.UNAVAILABLE
        )
    if observation.status is ProviderStatus.QUERY_LIMIT_EXCEEDED:
        return DispatchOutcome.NOT_DISPATCHED
    return _DISPATCH_OUTCOMES[observation.status]


@dataclass(frozen=True, slots=True)
class ProviderLaneObservation:
    identity: str
    kind: ProviderQueryKind
    status: ProviderStatus
    input_references: tuple[str, ...] = ()
    matched_family: str | None = None
    candidates: tuple[ProviderLaneCandidate, ...] = ()
    diagnostics: tuple[FailureDiagnostic, ...] = ()
    dispatch_lineage: WorkDispatchLineage | None = None
    transformation_lineage: WorkTitleTransformationLineage | None = None
    exhausted_dispatch: tuple[WorkDispatchLineage, ...] = ()
    recording_lookup_hints: tuple[RecordingLookupHint, ...] = ()
    dispatch: ProviderLaneDispatch | None = None

    def __post_init__(self) -> None:
        self._validate_lane()
        self._validate_dispatch()

    def _validate_dispatch(self) -> None:
        """Hold one dispatch record to the outcome its lane allows (#1258)."""
        dispatch = self.dispatch
        if dispatch is not None and (
            type(dispatch) is not ProviderLaneDispatch
            or dispatch.outcome
            is not lane_dispatch_outcome(
                self, starved=dispatch.outcome is DispatchOutcome.STARVED
            )
            or (
                self.kind is not ProviderQueryKind.WORK
                and (
                    dispatch.rungs is not None or dispatch.near_miss_seconds is not None
                )
            )
        ):
            raise ReportValueError("provider lane dispatch is invalid")

    def _validate_lane(self) -> None:
        if (
            _LANE_IDENTITY.fullmatch(self.identity) is None
            or self.kind not in PROVIDER_LANE_KINDS
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
            # A WORK or ASIN lane carries no ISBN family, and every lane names
            # the local claims it was dispatched from.
            raise ReportValueError("provider lane inputs are invalid")
        if (
            type(self.candidates) is not tuple
            or any(
                type(value) is not ProviderLaneCandidate for value in self.candidates
            )
            or tuple(value.reference for value in self.candidates)
            != tuple(sorted({value.reference for value in self.candidates}))
            or (self.status is ProviderStatus.CANDIDATES) != bool(self.candidates)
            or not diagnostics_are_canonical(self.diagnostics)
            or any(
                value.component is not FailureComponent.PROVIDER
                or _PROVIDER_DIAGNOSTIC_STAGES.get(value.category) is not value.stage
                or value.current is not None
                for value in self.diagnostics
            )
            or (
                self.dispatch_lineage is not None
                and (
                    type(self.dispatch_lineage) is not WorkDispatchLineage
                    or self.kind is not ProviderQueryKind.WORK
                    or self.status
                    not in {
                        ProviderStatus.CANDIDATES,
                        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
                    }
                )
            )
            or (
                self.transformation_lineage is not None
                and (
                    type(self.transformation_lineage)
                    is not WorkTitleTransformationLineage
                    or self.dispatch_lineage is None
                    or self.kind is not ProviderQueryKind.WORK
                    or self.status
                    not in {
                        ProviderStatus.CANDIDATES,
                        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
                    }
                )
            )
        ):
            raise ReportValueError("provider lane candidates are invalid")
        if type(self.exhausted_dispatch) is not tuple or any(
            type(value) is not WorkDispatchLineage for value in self.exhausted_dispatch
        ):
            raise ReportValueError("provider lane exhausted dispatch is invalid")
        if not self.exhausted_dispatch:
            # Admission refusal is a ladder fact, so only a lane that walked
            # its whole ladder can state it.
            if any(
                value.category is FailureCategory.WORK_IDENTITY_REFUSED
                for value in self.diagnostics
            ):
                raise ReportValueError("provider lane refusal is invalid")
            self._validate_recording_lookup_hints()
            return
        # Every dispatched rung was either empty or returned products that
        # work-identity admission refused, and the lane states how many of
        # each: empty rungs, and refused products rather than refused rungs.
        empty_rungs = next(
            (
                value.count
                for value in self.diagnostics
                if value.category is FailureCategory.EMPTY_NO_MATCH
            ),
            0,
        )
        refused_products = next(
            (
                value.count
                for value in self.diagnostics
                if value.category is FailureCategory.WORK_IDENTITY_REFUSED
            ),
            0,
        )
        refused_rungs = len(self.exhausted_dispatch) - empty_rungs
        if (
            self.kind is not ProviderQueryKind.WORK
            or self.status is not ProviderStatus.NO_MATCH
            or self.dispatch_lineage is not None
            or self.transformation_lineage is not None
            or len(set(self.exhausted_dispatch)) != len(self.exhausted_dispatch)
            or refused_rungs < 0
            or (refused_rungs > 0) != (refused_products > 0)
            or refused_products < refused_rungs
            or self.diagnostics
            != work_ladder_diagnostics(empty_rungs, refused_products)
        ):
            raise ReportValueError("provider lane exhausted dispatch is invalid")
        self._validate_recording_lookup_hints()

    def _validate_recording_lookup_hints(self) -> None:
        """Keep hints on the two lanes that can state them (#1257).

        A walked-out WORK lane states them from its refused rungs, and an
        admitted one from its own rung or an earlier one.
        """
        hints = self.recording_lookup_hints
        if type(hints) is not tuple or (
            hints
            and (
                any(type(value) is not RecordingLookupHint for value in hints)
                or len(hints) > MAX_RECORDING_LOOKUP_HINTS
                or tuple(value.asin for value in hints)
                != tuple(sorted({value.asin for value in hints}))
                or self.kind is not ProviderQueryKind.WORK
                or (
                    self.status is ProviderStatus.NO_MATCH
                    and (
                        not self.exhausted_dispatch
                        or any(
                            value.rung not in self.exhausted_dispatch for value in hints
                        )
                        or not any(
                            value.category is FailureCategory.WORK_IDENTITY_REFUSED
                            for value in self.diagnostics
                        )
                    )
                )
                or (
                    self.status is ProviderStatus.CANDIDATES
                    and self.dispatch_lineage is None
                )
                or self.status
                not in {ProviderStatus.NO_MATCH, ProviderStatus.CANDIDATES}
            )
        ):
            raise ReportValueError("provider lane recording lookup hints are invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "candidates": [value.to_data() for value in self.candidates],
            "identity": self.identity,
            "input_references": list(self.input_references),
            "kind": self.kind.value,
            "matched_family": self.matched_family,
            "status": self.status.value,
            "diagnostics": [value.to_data() for value in self.diagnostics],
            "dispatch_lineage": (
                None
                if self.dispatch_lineage is None
                else self.dispatch_lineage.to_data()
            ),
            "transformation_lineage": (
                None
                if self.transformation_lineage is None
                else self.transformation_lineage.to_data()
            ),
            "exhausted_dispatch": [
                value.to_data() for value in self.exhausted_dispatch
            ],
            "recording_lookup_hints": [
                value.to_data() for value in self.recording_lookup_hints
            ],
            "dispatch": None if self.dispatch is None else self.dispatch.to_data(),
        }


def work_ladder_diagnostics(
    empty_rungs: int, refused_products: int
) -> tuple[FailureDiagnostic, ...]:
    """State one exhausted WORK ladder's empty rungs and refused products."""
    return tuple(
        value
        for value in (
            FailureDiagnostic(
                FailureCategory.EMPTY_NO_MATCH,
                FailureComponent.PROVIDER,
                FailureStage.RESPONSE,
                retryable=False,
                count=empty_rungs,
            ),
            FailureDiagnostic(
                FailureCategory.WORK_IDENTITY_REFUSED,
                FailureComponent.PROVIDER,
                FailureStage.IDENTITY,
                retryable=False,
                count=refused_products,
            ),
        )
        if value.count
    )


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
            or any(
                _LANE_IDENTITY.fullmatch(value) is None
                for value in self.lane_identities
            )
        ):
            raise ReportValueError("provider convergence is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "lane_identities": list(self.lane_identities),
            "work_identity": self.work_identity,
        }


_PROVIDER_DIAGNOSTIC_STAGES = {
    FailureCategory.ADAPTER_FAULT: FailureStage.EXECUTION,
    FailureCategory.AUTHENTICATION: FailureStage.RESPONSE,
    FailureCategory.BURST_THROTTLE: FailureStage.RESPONSE,
    FailureCategory.CANDIDATE_LIMIT_EXCEEDED: FailureStage.RESPONSE,
    FailureCategory.CANCELLATION: FailureStage.EXECUTION,
    FailureCategory.DAILY_QUOTA_EXHAUSTED: FailureStage.RESPONSE,
    FailureCategory.EMPTY_NO_MATCH: FailureStage.RESPONSE,
    FailureCategory.HTTP_NON_SUCCESS: FailureStage.RESPONSE,
    FailureCategory.IDENTITY: FailureStage.IDENTITY,
    FailureCategory.LOOKUP_CONSTRUCTION: FailureStage.LOOKUP,
    FailureCategory.MALFORMED_RESPONSE: FailureStage.RESPONSE,
    FailureCategory.OVERSIZED_RESPONSE: FailureStage.RESPONSE,
    FailureCategory.PROVIDER_DECLARED_ERROR: FailureStage.RESPONSE,
    FailureCategory.QUOTA_RATE_LIMIT: FailureStage.RESPONSE,
    FailureCategory.SCHEMA_CONSTRUCTION: FailureStage.SCHEMA,
    FailureCategory.TIMEOUT: FailureStage.REQUEST,
    FailureCategory.TRANSPORT: FailureStage.REQUEST,
    FailureCategory.UNKNOWN: FailureStage.RESPONSE,
    FailureCategory.WORK_IDENTITY_REFUSED: FailureStage.IDENTITY,
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
    edition_enrichment_overflow: EditionEnrichmentOverflow | None = None
    dispatch_lineage: WorkDispatchLineage | None = None
    transformation_lineage: WorkTitleTransformationLineage | None = None

    recording_lookup_candidates: tuple[ProviderCandidate, ...] = ()
    # A product an ASIN lane refused, retained with the claims its stub in
    # `candidates` lost (#1307). Never read for admission: it records what was
    # refused, so a report can say a tight product was refused by title.
    refused_products: tuple[ProviderCandidate, ...] = ()
    # Hardcover audio editions of an admitted work that credit a narrator and
    # state a runtime inside the tight band of the recording (#962, ADR-0089).
    # Never read for admission or as field evidence: a Hardcover edition is
    # never an independent source, since an Audible exclusive's facts can only
    # have come from Audible. It only witnesses which recording the file is.
    audio_editions: tuple[ProviderCandidate, ...] = ()

    def __post_init__(self) -> None:
        _name(self.provider, "provider")
        if (
            type(self.status) is not ProviderStatus
            or type(self.query_kind) is not ProviderQueryKind
            or self.authority not in PROVIDER_AUTHORITIES
        ):
            raise ReportValueError("provider status is invalid")
        if self.accessed_at is not None:
            validate_provider_access_time(self.accessed_at)
        if type(self.request_accounting) is not ProviderRequestAccounting:
            raise ReportValueError("provider request accounting is invalid")
        if self.dispatch_lineage is not None and (
            type(self.dispatch_lineage) is not WorkDispatchLineage
            or self.query_kind is not ProviderQueryKind.WORK
            or self.status
            not in {
                ProviderStatus.CANDIDATES,
                ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            }
            or self.lanes
        ):
            raise ReportValueError("provider work dispatch lineage is invalid")
        if self.transformation_lineage is not None and (
            type(self.transformation_lineage) is not WorkTitleTransformationLineage
            or self.dispatch_lineage is None
            or self.query_kind is not ProviderQueryKind.WORK
            or self.status
            not in {
                ProviderStatus.CANDIDATES,
                ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
            }
            or self.lanes
        ):
            raise ReportValueError("provider work transformation lineage is invalid")
        if not diagnostics_are_canonical(self.diagnostics) or any(
            value.component is not FailureComponent.PROVIDER
            or _PROVIDER_DIAGNOSTIC_STAGES.get(value.category) is not value.stage
            or value.current is not None
            for value in self.diagnostics
        ):
            raise ReportValueError("provider diagnostics are invalid")
        if (
            type(self.input_references) is not tuple
            or tuple(sorted(set(self.input_references))) != self.input_references
            or any(
                _REFERENCE.fullmatch(value) is None or not value.startswith("local_")
                for value in self.input_references
            )
        ):
            raise ReportValueError("provider input references are invalid")
        if (
            type(self.candidates) is not tuple
            or len(self.candidates) > MAX_CANDIDATES
            or any(type(value) is not ProviderCandidate for value in self.candidates)
        ):
            raise ReportValueError("provider candidates are invalid")
        identities = tuple(value.identity for value in self.candidates)
        if identities != tuple(sorted(set(identities))):
            raise ReportValueError(
                "provider candidates are not in canonical identity order"
            )
        if (
            type(self.recording_lookup_candidates) is not tuple
            or len(self.recording_lookup_candidates) > MAX_CANDIDATES
            or any(
                type(value) is not ProviderCandidate
                for value in self.recording_lookup_candidates
            )
        ):
            raise ReportValueError("recording lookup candidates are invalid")
        lookup_identities = tuple(
            value.identity for value in self.recording_lookup_candidates
        )
        if lookup_identities != tuple(sorted(set(lookup_identities))):
            raise ReportValueError("recording lookup candidates are not canonical")
        if self.recording_lookup_candidates:
            work_lanes = tuple(
                lane for lane in self.lanes if lane.kind is ProviderQueryKind.WORK
            )
            if (
                self.authority not in AUDIOBOOK_PROVIDER_AUTHORITIES
                or self.edition_enrichment_overflow is not None
                or self.dispatch_lineage is not None
                or self.transformation_lineage is not None
                or (
                    not self.lanes
                    and (
                        self.query_kind is not ProviderQueryKind.WORK
                        or self.status is not ProviderStatus.NO_MATCH
                        or self.candidates
                    )
                )
                # Hints come from the asserted lane, which the report pins;
                # an audiobook's alternative lane may stand beside it.
                or (
                    self.lanes
                    and (
                        not 1 <= len(work_lanes) <= 2
                        or not any(
                            lane.status is ProviderStatus.NO_MATCH
                            and not lane.candidates
                            and lane.dispatch_lineage is None
                            and lane.transformation_lineage is None
                            for lane in work_lanes
                        )
                    )
                )
            ):
                raise ReportValueError("recording lookup provenance is invalid")
        self._validate_refused_products()
        self._validate_audio_editions()
        if (self.status is ProviderStatus.CANDIDATES) != bool(self.candidates):
            raise ReportValueError("provider status and candidates disagree")
        if self.edition_enrichment_overflow is not None and (
            type(self.edition_enrichment_overflow) is not EditionEnrichmentOverflow
            or self.status is not ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
            or self.candidates
            or self.convergence
            or (
                self.lanes
                and len(
                    tuple(
                        lane
                        for lane in self.lanes
                        if lane.kind is ProviderQueryKind.WORK
                        and lane.status is ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
                        and not lane.candidates
                    )
                )
                != 1
            )
            or any(lane.candidates for lane in self.lanes)
        ):
            raise ReportValueError("edition overflow witness is invalid")
        if self.edition_enrichment_overflow is not None and any(
            author.links
            or author
            != ProviderClaim.create(
                provider=self.provider,
                candidate=self.edition_enrichment_overflow.work_identity,
                kind=ClaimKind.CREATOR,
                value=author.value,
                state=author.state,
                scope=ClaimScope.WORK,
                applicability=author.applicability,
                role="author",
                raw_order=author.raw_order,
            )
            for author in self.edition_enrichment_overflow.authors
        ):
            raise ReportValueError(
                "edition overflow witness references are inconsistent"
            )
        if (
            self.status in {ProviderStatus.DISABLED, ProviderStatus.INSUFFICIENT_INPUT}
            and self.input_references
        ):
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
            for candidate in (
                *self.candidates,
                *self.recording_lookup_candidates,
                *self.refused_products,
                *self.audio_editions,
            )
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
            # An inactive aggregate claims no inputs, so its lanes' inputs are
            # not its own: a disabled provider's, or one whose every lane had
            # too little to search with (#1498).
            if (
                self.status
                not in {ProviderStatus.DISABLED, ProviderStatus.INSUFFICIENT_INPUT}
                and self.input_references != expected_inputs
            ):
                raise ReportValueError("provider aggregate inputs are inconsistent")
        lane_references = tuple(
            candidate.reference for lane in self.lanes for candidate in lane.candidates
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

    def _validate_refused_products(self) -> None:
        """Bind each refused product to the ASIN-only stub it was cut to (#1307)."""
        if (
            type(self.refused_products) is not tuple
            or len(self.refused_products) > MAX_CANDIDATES
            or any(
                type(value) is not ProviderCandidate for value in self.refused_products
            )
        ):
            raise ReportValueError("refused products are invalid")
        identities = tuple(value.identity for value in self.refused_products)
        if identities != tuple(sorted(set(identities))):
            raise ReportValueError("refused products are not canonical")
        if not self.refused_products:
            return
        stubs = {value.identity: value for value in self.candidates}
        if self.authority not in AUDIOBOOK_PROVIDER_AUTHORITIES or not (
            self.query_kind is ProviderQueryKind.ASIN
            or any(lane.kind is ProviderQueryKind.ASIN for lane in self.lanes)
        ):
            raise ReportValueError("refused product provenance is invalid")
        for product in self.refused_products:
            stub = stubs.get(product.identity)
            if (
                stub is None
                # Only finalization's own publication-date text (#1307).
                or any(
                    value.kind is not ProviderEvidenceKind.PUBLICATION_DATE_TEXT
                    for value in product.evidence
                )
                or product.identity_bindings
                or product.runtime_short_of_local
                or stub.evidence
                or stub.work_identity != product.work_identity
                or not stub.claims
                or any(claim.kind is not ClaimKind.ASIN for claim in stub.claims)
                or tuple(claim.reference for claim in stub.claims)
                != tuple(
                    claim.reference
                    for claim in product.claims
                    if claim.kind is ClaimKind.ASIN
                )
                or len(product.claims) == len(stub.claims)
            ):
                raise ReportValueError("refused product does not match its stub")

    def _validate_audio_editions(self) -> None:
        """Bind each audio edition to an admitted Hardcover work (#962)."""
        if (
            type(self.audio_editions) is not tuple
            or len(self.audio_editions) > MAX_AUDIO_EDITIONS
            or any(
                type(value) is not ProviderCandidate for value in self.audio_editions
            )
        ):
            raise ReportValueError("audio editions are invalid")
        identities = tuple(value.identity for value in self.audio_editions)
        if identities != tuple(sorted(set(identities))):
            raise ReportValueError("audio editions are not canonical")
        if not self.audio_editions:
            return
        works = {
            value.work_identity
            for value in self.candidates
            if value.identity == value.work_identity
            and value.identity.startswith("hardcover-work-")
        }
        # An adapter's own answer is bound to its authority after it returns.
        if (
            self.authority not in {None, "hardcover"}
            or self.status is not ProviderStatus.CANDIDATES
        ):
            raise ReportValueError("audio edition provenance is invalid")
        for edition in self.audio_editions:
            kinds = [claim.kind for claim in edition.claims]
            if (
                not edition.identity.startswith("hardcover-edition-")
                or edition.work_identity not in works
                or edition.evidence
                or edition.identity_bindings
                or edition.runtime_short_of_local
                or any(
                    claim.state is not FieldState.VALID
                    or claim.scope is not claim_scope(claim.kind)
                    or claim.kind
                    not in {ClaimKind.ASIN, ClaimKind.DURATION, ClaimKind.CONTRIBUTOR}
                    or (claim.kind is ClaimKind.CONTRIBUTOR)
                    != (claim.role == "narrator")
                    for claim in edition.claims
                )
                or kinds.count(ClaimKind.DURATION) != 1
                or kinds.count(ClaimKind.ASIN) > 1
                or ClaimKind.CONTRIBUTOR not in kinds
            ):
                raise ReportValueError("audio edition is invalid")

    def to_data(self) -> dict[str, object]:
        return {
            "candidates": [value.to_data() for value in self.candidates],
            "audio_editions": [value.to_data() for value in self.audio_editions],
            "recording_lookup_candidates": [
                value.to_data() for value in self.recording_lookup_candidates
            ],
            "refused_products": [value.to_data() for value in self.refused_products],
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
            "edition_enrichment_overflow": (
                None
                if self.edition_enrichment_overflow is None
                else self.edition_enrichment_overflow.to_data()
            ),
            "dispatch_lineage": (
                None
                if self.dispatch_lineage is None
                else self.dispatch_lineage.to_data()
            ),
            "transformation_lineage": (
                None
                if self.transformation_lineage is None
                else self.transformation_lineage.to_data()
            ),
        }


def recording_variant_conflict(
    candidate: ProviderCandidate,
    local_claims: tuple[LocalClaim, ...],
    local_title: str,
    library_languages: tuple[str, ...] = (),
) -> str | None:
    """Compare explicit recording facts without filling unknown variant facts.

    A recording that states no language of its own is held to the languages
    its library declares (#1315): a product stating a language outside every
    one of them is refused, with its own reason so a reader can tell it from
    a claim conflict. One declared language is therefore exactly a language
    claim, and several accept any of them without choosing one.
    """
    from mechanical_librarian.domain.field_outcomes import (
        language_identity,
        language_outcomes_compatible,
    )

    def known(value: str | None) -> bool:
        return (
            value is not None
            and (identity := language_identity(value)) is not None
            and identity.base != "und"
        )

    languages = [
        claim.value
        for claim in candidate.claims
        if claim.kind is ClaimKind.LANGUAGE
        and claim.state is FieldState.VALID
        and known(claim.value)
    ]
    local_languages = [
        claim.canonical
        for claim in local_claims
        if claim.kind is ClaimKind.LANGUAGE
        and claim.state is FieldState.VALID
        and known(claim.canonical)
    ]
    if any(
        not language_outcomes_compatible(left, right)
        for left in local_languages
        for right in languages
        if left is not None and right is not None
    ):
        return "recording_language_conflict"
    if (
        not local_languages
        and library_languages
        and any(
            right is not None
            and not any(
                language_outcomes_compatible(left, right) for left in library_languages
            )
            for right in languages
        )
    ):
        return "recording_language_outside_library"
    formats = [
        e.value.strip().casefold()
        for e in candidate.evidence
        if e.kind is ProviderEvidenceKind.EDITION_STATEMENT
    ]
    known_formats = {
        value for value in formats if value in {"abridged", "unabridged", "full cast"}
    }
    local_variant = re.search(
        r"\((abridged|unabridged|full cast)\)$", local_title, re.I
    )
    if len(known_formats) > 1 or (
        local_variant
        and known_formats
        and local_variant.group(1).casefold() not in known_formats
    ):
        return "recording_variant_conflict"
    return None


def _matched_audiobook_variant(
    candidate: ProviderCandidate,
    local_title: str,
    local_authors: tuple[str, ...],
    title: str,
    authors: tuple[str, ...],
) -> bool:
    return (
        normalize_title(local_title) == normalize_title(title)
        and re.search(r"\(abridged\)$", local_title, re.I) is not None
        and len(local_authors) == len(authors)
        and sorted(creator_comparison_key(value) for value in local_authors)
        == sorted(creator_comparison_key(value) for value in authors)
        and not any(
            claim.kind is ClaimKind.CONTRIBUTOR
            and claim.role in {"abridger", "adapter", "translator"}
            for claim in candidate.claims
        )
        and not any(
            e.kind is ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION
            for e in candidate.evidence
        )
        and recording_variant_conflict(candidate, (), local_title) is None
    )


def provider_candidate_work_identity_admission(
    candidate: ProviderCandidate,
    local_title: str,
    local_authors: tuple[str, ...],
    dispatch_lineage: WorkDispatchLineage | None,
    *,
    audiobook: bool = False,
    runtime_tight: bool = False,
    leading_number: bool = False,
) -> WorkIdentityAdmission:
    """Apply the closed WORK qualification floor to one retained candidate.

    `runtime_tight` is set only on an audiobook plan's asserted lane, so
    `local_title` is then the asserted title: a band-first title production
    never admits a product that explicitly contradicts it (#1260).
    """
    claims = (
        _linked_edition_work_identity_claims(candidate, local_title)
        if audiobook
        else None
    ) or _provider_candidate_work_identity_claims(candidate)
    if claims is None:
        return WorkIdentityAdmission(False)
    title, authors, work_identity, variant_excluded = claims
    if audiobook and _matched_audiobook_variant(
        candidate, local_title, local_authors, title, authors
    ):
        return WorkIdentityAdmission(True)
    ordered = tuple(sorted(candidate.claims, key=lambda claim: claim.raw_order))
    # Each stated series name with the position claim that directly follows
    # it, `None` where that position is not a valid value.
    memberships = tuple(
        (
            name.value,
            sequence.value if sequence.state is FieldState.VALID else None,
        )
        for name, sequence in zip(ordered, ordered[1:], strict=False)
        if name.kind is ClaimKind.SERIES
        and sequence.kind is ClaimKind.SERIES_SEQUENCE
        and name.scope is ClaimScope.WORK
        and sequence.scope is ClaimScope.WORK
        and name.state is FieldState.VALID
        and name.value is not None
        and sequence.raw_order == name.raw_order + 1
    )
    admission = replay_provider_work_identity_admission(
        local_title=local_title,
        local_authors=local_authors,
        provider_title=title,
        provider_authors=authors,
        provider_work_identity=work_identity,
        dispatch_lineage=dispatch_lineage,
        variant_excluded=variant_excluded,
        provider_series=tuple(
            (name, sequence)
            for name, sequence in memberships
            if sequence is not None
            and sum(claim.kind is ClaimKind.SERIES for claim in ordered) == 1
            and not any(
                claim.kind is ClaimKind.SUBTITLE
                and claim.state is not FieldState.ABSENT
                and not (
                    claim.state is FieldState.VALID
                    and claim.value == f"{name}, Book {sequence}"
                )
                for claim in ordered
            )
        ),
        provider_series_memberships=memberships,
        audiobook=audiobook,
        runtime_tight=runtime_tight,
        leading_number=leading_number,
    )
    if (
        admission.transformation is not None
        and admission.transformation.production in BAND_TITLE_PRODUCTIONS
        and provider_candidate_work_identity_contradiction(
            candidate, local_title, local_authors
        )
        is WorkIdentityContradiction.EXPLICIT
    ):
        return WorkIdentityAdmission(False)
    return admission


def provider_candidate_exact_work_identity_admission(
    candidate: ProviderCandidate,
    local_title: str,
    local_authors: tuple[str, ...],
    *,
    audiobook: bool = False,
) -> WorkIdentityAdmission:
    """Admit one candidate against the exact target work without dispatch replay."""
    claims = _provider_candidate_work_identity_claims(candidate)
    if claims is None:
        return WorkIdentityAdmission(False)
    title, authors, work_identity, variant_excluded = claims
    if audiobook and _matched_audiobook_variant(
        candidate, local_title, local_authors, title, authors
    ):
        return WorkIdentityAdmission(True)
    return admit_provider_work_identity(
        local_title=local_title,
        local_authors=local_authors,
        provider_title=title,
        provider_authors=authors,
        provider_work_identity=work_identity,
        variant_excluded=variant_excluded,
        audiobook=audiobook,
    )


def target_work_identity_context(
    target: TargetFacts,
) -> tuple[str, tuple[str, ...]] | None:
    """Return the exact normalized target title and complete creator set."""
    if type(target) is not TargetFacts:
        return None
    fields = {field.destination: field for field in target.fields}
    title = fields[TargetDestination.TITLE]
    authors = fields[TargetDestination.AUTHORS]
    if (
        title.state is not TargetFieldState.VALUE
        or type(title.value) is not str
        or authors.state is not TargetFieldState.VALUE
        or type(authors.value) is not tuple
        or not authors.value
        or not all(type(value) is str for value in authors.value)
    ):
        return None
    normalized_title = normalize_title(title.value)
    normalized_authors = tuple(
        normalize_title(value) for value in cast("tuple[str, ...]", authors.value)
    )
    if (
        not normalized_title
        or any(not value for value in normalized_authors)
        or len({value.casefold() for value in normalized_authors})
        != len(normalized_authors)
    ):
        return None
    return normalized_title, normalized_authors


@dataclass(frozen=True, slots=True)
class WorkLanePayload:
    """One WORK lane's identity payload, the only binding a lane's title has.

    The asserted lane carries no `title_basis`, so its payload is the one
    every earlier report keyed it on. An alternative lane names the surface
    its title was read from, which keeps it a different lane from an asserted
    lane over the same text.
    """

    title: str
    authors: tuple[str, ...]
    input_references: tuple[str, ...]
    title_basis: str | None = None

    @property
    def identity(self) -> str:
        payload: dict[str, object] = {
            "authors": list(self.authors),
            "input_references": list(self.input_references),
            "kind": ProviderQueryKind.WORK.value,
            "title": self.title,
        }
        if self.title_basis is not None:
            payload["title_basis"] = self.title_basis
        return discovery_lane_identity(payload)


def surviving_discovery_titles(
    titles: tuple[AudioDiscoveryTitle, ...], fields: tuple[TargetField, ...]
) -> tuple[AudioDiscoveryTitle, ...]:
    """Drop every discovery title that would only repeat a query already made.

    A title equal to the asserted title, to an asserted author, to a series
    name the target records, or to an earlier entry adds nothing a second
    lane could ask, so it is not kept.
    """
    values = {field.destination: field for field in fields}
    excluded: list[str] = []
    for destination in (TargetDestination.TITLE, TargetDestination.AUTHORS):
        field = values.get(destination)
        if field is not None and type(field.value) is str:
            excluded.append(field.value)
        elif field is not None and type(field.value) is tuple:
            excluded.extend(value for value in field.value if type(value) is str)
    excluded.extend(_target_series_names(fields))
    kept: list[AudioDiscoveryTitle] = []
    for value in titles:
        if not any(
            _closed_title_equal(value.title, other)
            for other in (*excluded, *(entry.title for entry in kept))
        ):
            kept.append(value)
    return tuple(kept)


def _target_series_names(fields: tuple[TargetField, ...]) -> tuple[str, ...]:
    return tuple(
        series.name
        for field in fields
        if field.destination is TargetDestination.SERIES and type(field.value) is tuple
        for series in field.value
        if type(series) is TargetSeries
    )


def work_discovery_lane_payloads(
    fields: tuple[TargetField, ...],
    local_claims: tuple[LocalClaim, ...],
    discovery_titles: tuple[AudioDiscoveryTitle, ...] | None = None,
) -> tuple[WorkLanePayload, ...]:
    """Return the WORK lanes one plan dispatches and one report replays.

    The asserted lane is the target's own title and complete author set, read
    from the target-title claim. `discovery_titles` is `None` for an ebook,
    which has no alternative. On an audiobook the first surviving discovery
    title adds one alternative lane over the same authors and references, and
    when the asserted title's first rung is a series name the target records
    (series collapse) the alternative replaces the asserted lane rather than
    joining it.
    """
    values = {field.destination: field for field in fields}
    title = values[TargetDestination.TITLE]
    authors = values[TargetDestination.AUTHORS]
    complete_work = (
        title.state is TargetFieldState.VALUE
        and type(title.value) is str
        and authors.state is TargetFieldState.VALUE
        and type(authors.value) is tuple
        and bool(authors.value)
        and all(type(value) is str for value in authors.value)
        and len(
            {normalize_title(cast("str", value)).casefold() for value in authors.value}
        )
        == len(authors.value)
    )
    if not complete_work:
        return ()
    # ``complete_work`` is exactly the conjunction that proved the title is a
    # ``str`` and the authors are a non-empty tuple of ``str``.
    normalized_title = normalize_title(cast("str", title.value))
    normalized_authors = tuple(
        normalize_title(value) for value in cast("tuple[str, ...]", authors.value)
    )
    references = tuple(
        sorted(
            claim.reference
            for claim in local_claims
            if claim.kind is ClaimKind.TITLE and claim.basis == "target_metadata"
        )
    )
    if not references:
        return ()
    asserted = WorkLanePayload(normalized_title, normalized_authors, references)
    surviving = (
        ()
        if discovery_titles is None
        else surviving_discovery_titles(discovery_titles, fields)
    )
    if not surviving:
        return (asserted,)
    alternative = WorkLanePayload(
        surviving[0].title, normalized_authors, references, surviving[0].basis
    )
    first = work_query_ladder(normalized_title, normalized_authors, audiobook=True)[:1]
    # A title with no query rung (a bare position such as "Part II") cannot
    # be asked at all, so it collapses exactly as a series-equal one does.
    collapsed = not first or any(
        query.rung is WorkDispatchRung.NORMALIZED
        and _closed_title_equal(query.title, name)
        for query in first
        for name in _target_series_names(fields)
    )
    return (alternative,) if collapsed else (asserted, alternative)


def provider_candidate_work_identity_contradiction(
    candidate: ProviderCandidate,
    target_title: str,
    target_authors: tuple[str, ...],
) -> WorkIdentityContradiction:
    """Classify only explicit contradiction from one complete work identity."""
    claims = _provider_candidate_work_identity_claims(candidate)
    if (
        claims is None
        or type(target_title) is not str
        or not target_title
        or type(target_authors) is not tuple
        or not target_authors
        or any(type(value) is not str or not value for value in target_authors)
        or len({normalize_title(value).casefold() for value in target_authors})
        != len(target_authors)
        or work_title_has_variant_marker(target_title)
    ):
        return WorkIdentityContradiction.UNAVAILABLE
    title, authors, work_identity, variant_excluded = claims
    if variant_excluded or len(
        {normalize_title(value).casefold() for value in authors}
    ) != len(authors):
        return WorkIdentityContradiction.UNAVAILABLE
    admission = admit_provider_work_identity(
        local_title=target_title,
        local_authors=target_authors,
        provider_title=title,
        provider_authors=authors,
        provider_work_identity=work_identity,
        allow_reordered_authors=True,
    )
    if admission.admitted:
        return WorkIdentityContradiction.NOT_CONTRADICTED
    target_queries = {
        query.title.casefold()
        for query in work_query_ladder(target_title, target_authors)
    }
    candidate_queries = {
        query.title.casefold() for query in work_query_ladder(title, authors)
    }
    return (
        WorkIdentityContradiction.UNAVAILABLE
        if target_queries & candidate_queries
        and (
            creator_sequence_methods(authors, target_authors)
            or closed_creator_display_possibility(target_authors, authors)
        )
        else WorkIdentityContradiction.EXPLICIT
    )


def provider_candidate_series_identity_matches(
    candidate: ProviderCandidate,
    local_title: str,
    local_authors: tuple[str, ...],
) -> bool:
    """Compare ISBN-backed series identity, without granting WORK admission.

    The caller must independently prove the complete target-backed ISBN lane.
    Decorations are attested by this candidate only; they create no claims,
    values, dispatch lineage, or authority for another metadata destination.
    """
    claims = _provider_candidate_work_identity_claims(candidate)
    if claims is None:
        return False
    title, authors, _work_identity, variant_excluded = claims
    if (
        candidate.work_identity in {"", "unresolved"}
        or variant_excluded
        or work_title_has_variant_marker(local_title)
        or any(
            claim.state is not FieldState.VALID or not claim.value
            for claim in candidate.claims
            if claim.scope is ClaimScope.WORK and claim.kind is ClaimKind.TITLE
        )
        or not creator_sequence_methods(authors, local_authors)
    ):
        return False
    if (
        _comparison_method(ClaimKind.TITLE, title, local_title)
        is not ComparisonMethod.NONE
    ):
        return True
    edition_titles = tuple(
        claim
        for claim in candidate.claims
        if claim.scope is ClaimScope.EDITION and claim.kind is ClaimKind.TITLE
    )
    if (
        len(edition_titles) == 1
        and edition_titles[0].state is FieldState.VALID
        and edition_titles[0].value == local_title
    ):
        return True
    matches = direct_work_title_election_matches(local_title, local_authors)
    if len(matches) != 1:
        return False
    match = matches[0]
    return (
        match.family is WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME
        and match.clean_title == title
        and not direct_work_title_election_matches(match.clean_title, local_authors)
        and any(
            claim.scope is ClaimScope.WORK
            and claim.kind is ClaimKind.SERIES
            and claim.state is FieldState.VALID
            and claim.value == match.preserved_component
            for claim in candidate.claims
        )
    )


def _linked_edition_work_identity_claims(
    candidate: ProviderCandidate, local_title: str
) -> tuple[str, tuple[str, ...], str, bool] | None:
    """Use a retained edition title only through its single explicit work link."""
    work = _provider_candidate_work_identity_claims(candidate)
    work_creators = tuple(
        c
        for c in candidate.claims
        if c.scope is ClaimScope.WORK
        and c.kind is ClaimKind.CREATOR
        and c.role == "author"
    )
    bindings = complete_creator_identity_bindings(candidate, work_creators)
    author_identities = {
        e.value
        for e in candidate.evidence
        if e.scope is ClaimScope.WORK
        and e.kind is ProviderEvidenceKind.CONTRIBUTOR_IDENTITY
        and e.role == "author"
    }
    if bindings is None or {b.identity for b in bindings} != author_identities:
        return None
    if not {
        e.value
        for e in candidate.evidence
        if e.kind is ProviderEvidenceKind.AUTHOR_IDENTITY
    }.issubset(author_identities):
        return None
    relations = tuple(
        e
        for e in candidate.evidence
        if e.kind is ProviderEvidenceKind.EDITION_WORK_RELATION
    )
    titles = tuple(
        c
        for c in candidate.claims
        if c.scope is ClaimScope.EDITION and c.kind is ClaimKind.TITLE
    )
    creators = tuple(
        c
        for c in candidate.claims
        if c.scope is ClaimScope.EDITION and c.kind is ClaimKind.CREATOR
    )
    if (
        work is None
        or work[3]
        or candidate.work_identity == "unresolved"
        or len(relations) != 1
        or relations[0].value != candidate.work_identity
        or any(
            e.kind is ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION
            for e in candidate.evidence
        )
        or len(titles) != 1
        or titles[0].state is not FieldState.VALID
        or titles[0].value is None
        or normalize_title(titles[0].value).casefold()
        != normalize_title(local_title).casefold()
        or any(
            c.state not in {FieldState.VALID, FieldState.ABSENT, FieldState.UNSUPPORTED}
            for c in creators
        )
        or any(
            c.state is FieldState.VALID
            and (
                c.value is None
                or normalize_title(c.value).casefold()
                not in {normalize_title(a).casefold() for a in work[1]}
            )
            for c in creators
        )
    ):
        return None
    return titles[0].value, work[1], candidate.work_identity, False


def _provider_candidate_work_identity_claims(
    candidate: ProviderCandidate,
) -> tuple[str, tuple[str, ...], str, bool] | None:
    """Return one complete candidate work claim set, or reject it closed."""
    titles = tuple(
        claim.value
        for claim in candidate.claims
        if claim.scope is ClaimScope.WORK
        and claim.kind is ClaimKind.TITLE
        and claim.state is FieldState.VALID
        and claim.value is not None
    )
    typed = tuple(
        claim
        for claim in candidate.claims
        if claim.scope is ClaimScope.WORK
        and claim.kind is ClaimKind.CREATOR
        and claim.role == "author"
    )
    neutral = tuple(
        claim
        for claim in candidate.claims
        if claim.scope is ClaimScope.WORK
        and claim.kind is ClaimKind.CREATOR
        and claim.role is None
    )
    creators = typed if typed else neutral
    variant_excluded = _provider_candidate_has_work_identity_veto(candidate)
    if (
        type(candidate) is not ProviderCandidate
        or len(titles) != 1
        or not creators
        or bool(typed)
        and bool(neutral)
        or any(
            claim.state is not FieldState.VALID or claim.value is None
            for claim in creators
        )
        or neutral
        and complete_creator_identity_bindings(candidate, neutral) is None
    ):
        return None
    return (
        titles[0],
        tuple(cast("str", claim.value) for claim in creators),
        (
            candidate.identity
            if candidate.work_identity == "unresolved"
            else candidate.work_identity
        ),
        variant_excluded,
    )


def provider_candidate_identity_title_claims(
    candidate: ProviderCandidate,
) -> tuple[str, str | None, tuple[str, ...]] | None:
    """Read the title, one subtitle and authors the corroborated rule compares.

    The same complete work claim set exact admission reads, with the one valid
    work subtitle the product states beside it, or ``None`` for a product that
    states no complete set or carries a work-identity veto (#1307).
    """
    claims = _provider_candidate_work_identity_claims(candidate)
    if claims is None or claims[3]:
        return None
    subtitles = tuple(
        claim.value
        for claim in candidate.claims
        if claim.scope is ClaimScope.WORK
        and claim.kind is ClaimKind.SUBTITLE
        and claim.state is FieldState.VALID
        and claim.value is not None
    )
    return claims[0], subtitles[0] if len(subtitles) == 1 else None, claims[1]


def _provider_candidate_has_work_identity_veto(candidate: ProviderCandidate) -> bool:
    return any(
        claim.scope is ClaimScope.WORK
        and claim.kind is ClaimKind.CONTRIBUTOR
        and claim.role in {"abridger", "adapter", "translator"}
        for claim in candidate.claims
    ) or any(
        work_title_has_variant_marker(claim.value)
        for claim in candidate.claims
        if claim.kind in {ClaimKind.TITLE, ClaimKind.SUBTITLE}
        and claim.state is FieldState.VALID
        and claim.value is not None
    )


@dataclass(frozen=True, slots=True)
class ProviderWorkIdentitySetAdmission:
    admitted: bool
    candidates: tuple[ProviderCandidate, ...] = ()
    work_identity: str | None = None
    transformation: WorkTitleTransformationLineage | None = None


def provider_candidate_set_work_identity_admission(
    candidates: tuple[ProviderCandidate, ...],
    local_title: str,
    local_authors: tuple[str, ...],
    dispatch_lineage: WorkDispatchLineage | None,
    *,
    audiobook: bool = False,
    band_claims: tuple[LocalClaim, ...] | None = None,
    corroboration: RecordingCorroboration | None = None,
    leading_number: bool = False,
    library_languages: tuple[str, ...] = (),
) -> ProviderWorkIdentitySetAdmission:
    """Admit exactly one work identity and one lineage from a candidate set.

    `band_claims` are the item's local claims on an audiobook plan's asserted
    WORK lane from an audiobook authority, and `None` everywhere else. Only a
    set refused without them is tried again with the band-corroborated
    productions, each candidate inside the tight band of the item's runtime
    and without a recording conflict, so every set admitted without them
    keeps its candidates and lineage (#1260).

    `corroboration` is passed beside `band_claims` on the Audible catalogue's
    asserted lane alone (#1307). Only a set the band productions still refuse
    reaches it, so it never changes a set admitted without it. Where two or
    more products were admitted one by one, the band-first set rule may keep
    the one tight product; where none was, the corroborated admission rule
    may admit one. Either way the admitted set is that one product, and it
    replays as one: band-first through the path that admitted it alone, the
    rule through `band_corroborated_title_relation`.

    The band reads a provider's whole-minute runtime exactly first and as a
    floor (ADR-0064 as amended) only when that admits nothing, so a set the
    exact band admitted keeps its candidates and lineage (#1307).

    `leading_number` is Audible's asserted audiobook lane, the one lane that
    dispatches the `leading_number_stripped` rung (#1307).

    `library_languages` reach only the band's recording-conflict check, so they
    never change a set admitted without the band (#1315).
    """

    def admit(
        tight: Callable[[ProviderCandidate], bool],
    ) -> ProviderWorkIdentitySetAdmission:
        return _provider_candidate_set_work_identity_admission(
            candidates,
            lambda candidate: provider_candidate_work_identity_admission(
                candidate,
                local_title,
                local_authors,
                dispatch_lineage,
                audiobook=audiobook,
                runtime_tight=tight(candidate),
                leading_number=leading_number,
            ),
            audiobook=audiobook,
        )

    unwidened = admit(lambda _candidate: False)
    if unwidened.admitted or band_claims is None:
        return unwidened
    # Imported here: the corroboration module imports this one, and it alone
    # owns the tight band (ADR-0064).
    from mechanical_librarian.domain.audiobook_corroboration import (
        CorroborationOutcome,
        candidate_runtime_seconds,
        local_claim_runtime_seconds,
        runtime_agreement,
    )

    local = local_claim_runtime_seconds(band_claims)
    if local is None:
        return unwidened

    def tight_reading(
        provider_minutes: bool,
    ) -> Callable[[ProviderCandidate], bool]:
        def tight(candidate: ProviderCandidate) -> bool:
            return (
                runtime_agreement(
                    local,
                    candidate_runtime_seconds(candidate),
                    provider_minutes=provider_minutes,
                ).outcome
                is CorroborationOutcome.CORROBORATED
                and recording_variant_conflict(
                    candidate, band_claims, local_title, library_languages
                )
                is None
            )

        return tight

    banded = admit(tight_reading(False))
    if banded.admitted:
        return banded
    tight = tight_reading(True)
    banded = admit(tight)
    if banded.admitted or corroboration is None or not audiobook:
        return banded
    return _corroborated_set_admission(
        candidates,
        local_title,
        local_authors,
        dispatch_lineage,
        band_claims,
        corroboration,
        tight,
        library_languages,
    )


def _corroborated_set_admission(
    candidates: tuple[ProviderCandidate, ...],
    local_title: str,
    local_authors: tuple[str, ...],
    dispatch_lineage: WorkDispatchLineage | None,
    band_claims: tuple[LocalClaim, ...],
    corroboration: RecordingCorroboration,
    tight: Callable[[ProviderCandidate], bool],
    library_languages: tuple[str, ...],
) -> ProviderWorkIdentitySetAdmission:
    """Try the band-first set rule, then the corroborated rule (#1307)."""
    from mechanical_librarian.domain.audiobook_corroboration import (
        band_first_candidate,
        corroborated_recording_candidate,
    )

    refused = ProviderWorkIdentitySetAdmission(False)
    if dispatch_lineage is None or type(candidates) is not tuple:
        return refused
    if any(type(candidate) is not ProviderCandidate for candidate in candidates):
        return refused
    eligible = tuple(
        candidate
        for candidate in candidates
        if not _provider_candidate_has_work_identity_veto(candidate)
    )
    admitted = tuple(
        candidate
        for candidate in eligible
        if provider_candidate_work_identity_admission(
            candidate,
            local_title,
            local_authors,
            dispatch_lineage,
            audiobook=True,
            runtime_tight=tight(candidate),
            leading_number=True,
        ).admitted
    )
    if admitted:
        chosen = band_first_candidate(
            admitted, local_title, band_claims, corroboration, library_languages
        )
        if chosen is None:
            return refused
        # The one tight product replays alone through the path that admitted
        # it, so the lane records it and its own lineage.
        return provider_candidate_set_work_identity_admission(
            (chosen,),
            local_title,
            local_authors,
            dispatch_lineage,
            audiobook=True,
            band_claims=band_claims,
            leading_number=True,
            library_languages=library_languages,
        )
    chosen = corroborated_recording_candidate(
        eligible,
        local_title,
        local_authors,
        band_claims,
        corroboration,
        library_languages,
    )
    if chosen is None:
        return refused
    queries = work_query_ladder(
        local_title, local_authors, audiobook=True, leading_number=True
    )
    permitted = {WorkDispatchLineage.from_query(query) for query in queries} | {
        WorkDispatchLineage.from_query(narrowing_work_query(query)) for query in queries
    }
    if dispatch_lineage not in permitted:
        return refused
    return ProviderWorkIdentitySetAdmission(
        True,
        (chosen,),
        chosen.identity
        if chosen.work_identity == "unresolved"
        else chosen.work_identity,
        WorkTitleTransformationLineage((CORROBORATED_TITLE_APPLICATION,)),
    )


def _corroborated_lane(lane: ProviderLaneObservation) -> bool:
    """Say whether a lane admitted through the corroborated rule (#1307)."""
    return (
        lane.transformation_lineage is not None
        and lane.transformation_lineage.production == CORROBORATED_TITLE_PRODUCTION
    )


# The one authority whose asserted lane walks the leading-number rung and
# admits through the corroborated rule (#1307).
CORROBORATED_RULE_AUTHORITY = "audible_catalog"


def lane_work_ladder(
    authority: str | None, payload: WorkLanePayload
) -> tuple[WorkDispatchLineage, ...]:
    """The audiobook ladder one planned WORK lane walks at one authority.

    The catalogue's asserted lane alone adds the leading-number rung (#1307).
    """
    return tuple(
        WorkDispatchLineage.from_query(query)
        for query in work_query_ladder(
            payload.title,
            payload.authors,
            audiobook=True,
            leading_number=authority == CORROBORATED_RULE_AUTHORITY
            and payload.title_basis is None,
        )
    )


def provider_candidate_set_exact_work_identity_admission(
    candidates: tuple[ProviderCandidate, ...],
    local_title: str,
    local_authors: tuple[str, ...],
    *,
    audiobook: bool = False,
) -> ProviderWorkIdentitySetAdmission:
    """Admit one exact candidate work without requiring WORK dispatch lineage."""
    return _provider_candidate_set_work_identity_admission(
        candidates,
        lambda candidate: provider_candidate_exact_work_identity_admission(
            candidate, local_title, local_authors, audiobook=audiobook
        ),
        audiobook=audiobook,
    )


def _provider_candidate_set_work_identity_admission(
    candidates: tuple[ProviderCandidate, ...],
    admit_candidate: Callable[[ProviderCandidate], WorkIdentityAdmission],
    *,
    audiobook: bool = False,
) -> ProviderWorkIdentitySetAdmission:
    """Apply one semantic admission entry point to an exact candidate set."""
    if type(candidates) is not tuple or any(
        type(candidate) is not ProviderCandidate for candidate in candidates
    ):
        return ProviderWorkIdentitySetAdmission(False)
    if (
        audiobook
        and candidates
        and all(
            re.fullmatch(r"hardcover-work-[0-9]{10}", c.identity)
            and c.identity == c.work_identity
            and all(claim.scope is ClaimScope.WORK for claim in c.claims)
            for c in candidates
        )
    ):
        admissions = tuple(admit_candidate(c) for c in candidates)
        if len(candidates) <= 5 and all(
            a.admitted and a.transformation is None for a in admissions
        ):
            return ProviderWorkIdentitySetAdmission(True, candidates)
        return ProviderWorkIdentitySetAdmission(False)
    qualified: dict[
        str, list[tuple[ProviderCandidate, WorkTitleTransformationLineage | None]]
    ] = {}
    blocked_work_identities: set[str] = set()
    for candidate in candidates:
        work_identity = (
            candidate.identity
            if candidate.work_identity == "unresolved"
            else candidate.work_identity
        )
        if _provider_candidate_has_work_identity_veto(candidate):
            blocked_work_identities.add(work_identity)
            continue
        admission = admit_candidate(candidate)
        if admission.admitted:
            qualified.setdefault(work_identity, []).append(
                (candidate, admission.transformation)
            )
    for identity in blocked_work_identities:
        qualified.pop(identity, None)
    lineages = {
        transformation
        for values in qualified.values()
        for _candidate, transformation in values
    }
    if len(qualified) != 1 or len(lineages) != 1:
        return ProviderWorkIdentitySetAdmission(False)
    work_identity, values = next(iter(qualified.items()))
    return ProviderWorkIdentitySetAdmission(
        True,
        tuple(candidate for candidate, _transformation in values),
        work_identity,
        next(iter(lineages)),
    )


def _target_backed_work_lookup(
    target: TargetFacts,
    provider: ProviderObservation,
) -> bool:
    if provider.query_kind is not ProviderQueryKind.WORK or provider.input_references:
        return False
    fields = {field.destination: field for field in target.fields}
    title = fields[TargetDestination.TITLE]
    authors = fields[TargetDestination.AUTHORS]
    return (
        title.state is TargetFieldState.VALUE
        and type(title.value) is str
        and authors.state is TargetFieldState.VALUE
        and type(authors.value) is tuple
        and bool(authors.value)
        and all(type(value) is str for value in authors.value)
        # The guard above establishes that this AUTHORS field holds strings
        # rather than TargetSeries values.
        and len(
            {
                normalize_title(value).casefold()
                for value in cast("tuple[str, ...]", authors.value)
            }
        )
        == len(authors.value)
    )


def _unbound_manifestation_link_allowed(
    target: TargetFacts,
    provider: ProviderObservation,
    evidence: ProviderEvidence,
    link: ComparisonLink,
) -> bool:
    return (
        link.local_reference is None
        and link.outcome is Comparison.NOT_COMPARABLE
        and link.method is ComparisonMethod.NONE
        and link.publication_date_relation
        is PublicationDateRelation.MANIFESTATION_INCOMPATIBLE
        and link.shared_date_precision is None
        and evidence.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
        and evidence.date_normalized is not None
        and type(evidence.date_precision) is DatePrecision
        and evidence.date_event is DateEvent.MANIFESTATION_PUBLICATION
        and evidence.date_confidence is DateLinkConfidence.UNRESOLVED
        and _target_backed_work_lookup(target, provider)
    )


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
    audio: AudioFacts | None = None
    # The library's declared languages when the report was prepared (#1315).
    # Every admission read of this report applies them, so they travel with it.
    library_languages: tuple[str, ...] = ()
    companion: CompanionEbook | None = None

    def __post_init__(self) -> None:
        if (
            _REPORT_ID.fullmatch(self.report_id) is None
            or type(self.target) is not TargetFacts
            or type(self.file) is not FileFacts
        ):
            raise ReportValueError("report identity or target is invalid")
        # Only audiobook matching reads them, so an ebook report states none.
        if not library_languages_valid(self.library_languages) or (
            self.library_languages and self.file.format not in AUDIO_FILE_FORMATS
        ):
            raise ReportValueError("report library languages are invalid")
        is_audio = self.file.format in AUDIO_FILE_FORMATS
        if (
            self.audio is not None and type(self.audio) is not AudioFacts
        ) or is_audio != (self.audio is not None):
            raise ReportValueError("audio section does not match the selected file")
        if self.companion is not None and (
            type(self.companion) is not CompanionEbook or self.audio is None
        ):
            raise ReportValueError("companion ebook does not match the selected file")
        if self.schema_version != REPORT_SCHEMA:
            raise ReportValueError("report schema is unsupported")
        if self.comparison_version != COMPARISON_VERSION:
            raise ReportValueError("report comparison version is unsupported")
        if type(self.local_claims) is not tuple or any(
            type(value) is not LocalClaim for value in self.local_claims
        ):
            raise ReportValueError("local claims are invalid")
        references = tuple(value.reference for value in self.local_claims)
        if len(set(references)) != len(references):
            raise ReportValueError("local claim references are duplicated")
        if self.audio is not None:
            # Per-file provenance is only provenance if it resolves.  A track
            # naming a claim this report does not carry, or a content digest
            # that is not the composition of the tracks listed, would describe
            # an evidence set nobody could reconstruct from the document.  An
            # audio section always names at least one track, so this holds over
            # every audiobook report rather than over the ones that listed any.
            if any(
                reference not in set(references)
                for track in self.audio.tracks
                for reference in track.claims
            ):
                raise ReportValueError("audio track claim provenance is unresolved")
            if self.file.content_digest != audio_set_content_digest(
                tuple(track.digest for track in self.audio.tracks)
            ):
                raise ReportValueError("audio content digest does not compose")
        has_opening_candidate = any(
            value.basis == "opening_matter" for value in self.local_claims
        )
        if (
            self.local_status not in {"candidate", "no_candidate"}
            or (self.local_status == "candidate") != has_opening_candidate
        ):
            raise ReportValueError("local candidate status is invalid")
        if (
            type(self.local_reasons) is not tuple
            or not self.local_reasons
            or tuple(sorted(set(self.local_reasons))) != self.local_reasons
        ):
            raise ReportValueError("local reasons are invalid")
        for value in self.local_reasons:
            _name(value, "local reason")
        if type(self.providers) is not tuple or any(
            type(value) is not ProviderObservation for value in self.providers
        ):
            raise ReportValueError("provider observations are invalid")
        # F43 slice 1 refused a provider observation on an audio report because
        # no audiobook provider existed. ADR-0061 admits four, so `v32` carries
        # them: the document is the same shape for both media, and what an
        # audiobook does or does not dispatch is a pipeline decision rather than
        # a property of the retained evidence. Until the activation packet lands
        # the pipeline still stops an audiobook before any provider, so this
        # section is empty in practice rather than by refusal.
        names = tuple(value.provider for value in self.providers)
        if names != tuple(sorted(set(names))):
            raise ReportValueError("provider observations are not in canonical order")
        target_title = self.target.current_title
        target_authors_value = next(
            (
                target_field.value
                for target_field in self.target.fields
                if target_field.destination is TargetDestination.AUTHORS
                and target_field.state is TargetFieldState.VALUE
            ),
            None,
        )
        target_authors = (
            cast("tuple[str, ...]", target_authors_value)
            if type(target_authors_value) is tuple
            and all(type(value) is str for value in target_authors_value)
            else ()
        )
        # An audiobook report replays the WORK lanes its plan could carry, so
        # a lane identity names exactly the title it was dispatched with.
        planned = (
            {}
            if self.audio is None
            else {
                payload.identity: payload
                for payload in work_discovery_lane_payloads(
                    self.target.fields, self.local_claims, self.audio.discovery_titles
                )
            }
        )
        asserted = next(
            (
                identity
                for identity, value in planned.items()
                if value.title_basis is None
            ),
            None,
        )
        for provider in self.providers:
            work_identities = {
                lane.identity
                for lane in provider.lanes
                if lane.kind is ProviderQueryKind.WORK
            }
            if self.audio is not None and work_identities:
                if not work_identities.issubset(planned):
                    raise ReportValueError("provider work lane is unknown")
                # An alternative lane is absent when it was suppressed; the
                # asserted lane, where the plan has one, never is.
                if any(
                    identity not in work_identities
                    and planned[identity].title_basis is None
                    for identity in planned
                ):
                    raise ReportValueError("provider work lanes do not match the plan")
            if provider.recording_lookup_candidates:
                lookup_lanes = tuple(
                    lane
                    for lane in provider.lanes
                    if lane.kind is ProviderQueryKind.WORK
                    and (asserted is None or lane.identity == asserted)
                )
                if len(lookup_lanes) != 1 or not lookup_lanes[0].input_references:
                    raise ReportValueError("recording lookup lane is missing")
                lane = lookup_lanes[0]
                work_inputs = {
                    claim.reference
                    for claim in self.local_claims
                    if claim.kind is ClaimKind.TITLE
                    and claim.basis == "target_metadata"
                    and claim.canonical == target_title
                }
                if (
                    not set(lane.input_references).issubset(work_inputs)
                    or lane.identity != asserted
                    or lane.status is not ProviderStatus.NO_MATCH
                ):
                    raise ReportValueError("recording lookup lane is inconsistent")
            if provider.recording_lookup_candidates and (
                not is_audio
                or any(
                    not provider_candidate_exact_work_identity_admission(
                        candidate,
                        target_title or "",
                        target_authors,
                        audiobook=self.audio is not None,
                    ).admitted
                    for candidate in provider.recording_lookup_candidates
                )
            ):
                raise ReportValueError("recording lookup work identity is inconsistent")
            candidates = {value.identity: value for value in provider.candidates}
            for lane in provider.lanes:
                payload = planned.get(lane.identity)
                if (
                    payload is None
                    or payload.title_basis is None
                    or lane.status is not ProviderStatus.CANDIDATES
                ):
                    continue
                lane_candidates = tuple(
                    candidates[value.edition_identity] for value in lane.candidates
                )
                # The alternative lane retains one band-corroborated recording,
                # which is what keeps its marketplace evidence.
                if len(lane_candidates) != 1 or not any(
                    value.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
                    and value.scope is ClaimScope.EDITION
                    for value in lane_candidates[0].evidence
                ):
                    raise ReportValueError("provider alternative candidate is invalid")
            witness = provider.edition_enrichment_overflow
            if witness is not None:
                work_lanes = tuple(
                    lane
                    for lane in provider.lanes
                    if lane.kind is ProviderQueryKind.WORK
                    and lane.status is ProviderStatus.CANDIDATE_LIMIT_EXCEEDED
                )
                lineage = (
                    work_lanes[0].transformation_lineage
                    if len(work_lanes) == 1
                    else provider.transformation_lineage
                )
                dispatch = (
                    work_lanes[0].dispatch_lineage
                    if len(work_lanes) == 1
                    else provider.dispatch_lineage
                )
                admission = replay_provider_work_identity_admission(
                    local_title=target_title or "",
                    local_authors=target_authors,
                    provider_title=witness.title,
                    provider_authors=tuple(
                        author.value or "" for author in witness.authors
                    ),
                    provider_work_identity=witness.work_identity,
                    dispatch_lineage=dispatch,
                    audiobook=self.audio is not None,
                )
                if (
                    not admission.admitted
                    or admission.transformation != lineage
                    or any(
                        (payload := planned.get(lane.identity)) is not None
                        and payload.title_basis is not None
                        for lane in work_lanes
                    )
                ):
                    raise ReportValueError(
                        "edition overflow witness does not match target"
                    )
            work_lanes = tuple(
                lane
                for lane in provider.lanes
                if lane.kind is ProviderQueryKind.WORK
                and lane.status is ProviderStatus.CANDIDATES
            )
            # Band claims reach only the asserted lane of an audiobook plan at
            # an audiobook authority, so a band-corroborated lineage anywhere
            # else does not replay (#1260).
            band_claims = (
                self.local_claims
                if self.audio is not None
                and provider.authority in AUDIOBOOK_PROVIDER_AUTHORITIES
                else None
            )
            # The corroborated rule reads only the catalogue's asserted lane,
            # beside its band claims (#1307).
            corroboration = (
                recording_corroboration(self.target.fields)
                if band_claims is not None
                and provider.authority == CORROBORATED_RULE_AUTHORITY
                else None
            )
            if (
                not work_lanes
                and provider.query_kind is ProviderQueryKind.WORK
                and provider.status is ProviderStatus.CANDIDATES
            ):
                set_admission = provider_candidate_set_work_identity_admission(
                    provider.candidates,
                    target_title or "",
                    target_authors,
                    provider.dispatch_lineage,
                    audiobook=self.audio is not None,
                    band_claims=band_claims,
                    corroboration=corroboration,
                    leading_number=corroboration is not None,
                    library_languages=self.library_languages,
                )
                if (
                    not set_admission.admitted
                    or set_admission.candidates != provider.candidates
                    or set_admission.transformation != provider.transformation_lineage
                ):
                    raise ReportValueError(
                        "provider work identity lineage is inconsistent"
                    )
            for lane in work_lanes:
                lane_candidates = tuple(
                    candidates[value.edition_identity] for value in lane.candidates
                )
                # An alternative lane admits against the title it asked with.
                payload = planned.get(lane.identity)
                set_admission = provider_candidate_set_work_identity_admission(
                    lane_candidates,
                    (
                        payload.title
                        if payload is not None and payload.title_basis is not None
                        else target_title or ""
                    ),
                    target_authors,
                    lane.dispatch_lineage,
                    audiobook=self.audio is not None,
                    band_claims=(
                        band_claims
                        if payload is not None and payload.title_basis is None
                        else None
                    ),
                    corroboration=(
                        corroboration
                        if payload is not None and payload.title_basis is None
                        else None
                    ),
                    leading_number=corroboration is not None
                    and payload is not None
                    and payload.title_basis is None,
                    library_languages=self.library_languages,
                )
                if (
                    not set_admission.admitted
                    or set_admission.candidates != lane_candidates
                    or set_admission.transformation != lane.transformation_lineage
                ):
                    raise ReportValueError(
                        "provider work identity lineage is inconsistent"
                    )
            # An exhausted lane walked its own title's whole audiobook ladder,
            # in ladder order, so its lineages replay from the plan alone.
            for lane in provider.lanes:
                if not lane.exhausted_dispatch:
                    continue
                payload = planned.get(lane.identity)
                if (
                    not is_audio
                    or provider.authority not in AUDIOBOOK_PROVIDER_AUTHORITIES
                    or payload is None
                    or lane.exhausted_dispatch
                    != lane_work_ladder(provider.authority, payload)
                ):
                    raise ReportValueError(
                        "provider exhausted dispatch is inconsistent"
                    )
            # A recording-lookup hint (#1257) replays from the asserted lane's
            # own ladder and the item's own runtime, and never names a product
            # the observation already retains.
            for lane in provider.lanes:
                if not lane.recording_lookup_hints:
                    continue
                payload = planned.get(lane.identity)
                # Only Audible produces hints; any other authority's is forged.
                if (
                    self.audio is None
                    or provider.authority != "audible_catalog"
                    or payload is None
                    or payload.title_basis is not None
                ):
                    raise ReportValueError(
                        "recording lookup hint provenance is invalid"
                    )
                ladder = lane_work_ladder(provider.authority, payload)
                if any(
                    hint.rung not in ladder for hint in lane.recording_lookup_hints
                ) or (
                    lane.status is ProviderStatus.CANDIDATES
                    # A rule admission follows the whole ladder (#1307), so
                    # every rung's hints stand beside it.
                    and not _corroborated_lane(lane)
                    and (
                        lane.dispatch_lineage not in ladder
                        or any(
                            ladder.index(hint.rung)
                            > ladder.index(lane.dispatch_lineage)
                            for hint in lane.recording_lookup_hints
                        )
                    )
                ):
                    raise ReportValueError("recording lookup hint rung is inconsistent")
                held = candidate_asins(
                    (*provider.candidates, *provider.recording_lookup_candidates)
                )
                if any(hint.asin in held for hint in lane.recording_lookup_hints):
                    raise ReportValueError(
                        "recording lookup hint duplicates a retained product"
                    )
                # Imported here: the corroboration module imports this one, and
                # it alone owns the tight band (ADR-0064).
                from mechanical_librarian.domain.audiobook_corroboration import (
                    CorroborationOutcome,
                    local_claim_runtime_seconds,
                    runtime_agreement,
                )

                local = local_claim_runtime_seconds(self.local_claims)
                if local is None or any(
                    runtime_agreement(
                        local, hint.runtime_seconds, provider_minutes=True
                    ).outcome
                    is not CorroborationOutcome.CORROBORATED
                    for hint in lane.recording_lookup_hints
                ):
                    raise ReportValueError(
                        "recording lookup hint is outside the tight band"
                    )
            # One asserted lane per provider, so the bound is per observation.
            if (
                sum(len(lane.recording_lookup_hints) for lane in provider.lanes)
                > MAX_RECORDING_LOOKUP_HINTS
            ):
                raise ReportValueError("recording lookup hints exceed their bound")
        self._validate_lane_dispatch(planned)
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
            != derive_identifier_evidence(
                self.local_claims, self.providers, self.target, self.audio
            )
        ):
            raise ReportValueError("identifier evidence is inconsistent")
        try:
            if not publication_ready(self.cover_sources):
                raise ReportValueError("cover evidence is incomplete")
        except CoverEvidenceError as error:
            raise ReportValueError("cover evidence is invalid") from error
        known = set(references)
        if any(
            not set(provider.input_references).issubset(known)
            for provider in self.providers
        ):
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
        if len({claim.reference for claim in provider_claims}) != len(
            provider_claims
        ) or any(
            not {link.local_reference for link in claim.links}.issubset(known)
            for claim in provider_claims
        ):
            raise ReportValueError("provider candidate claim references are invalid")
        if any(
            link.local_reference not in known
            and not _unbound_manifestation_link_allowed(
                self.target,
                provider,
                evidence,
                link,
            )
            for provider in self.providers
            for candidate in (
                *provider.candidates,
                *provider.recording_lookup_candidates,
            )
            for evidence in candidate.evidence
            for link in evidence.links
        ):
            raise ReportValueError(
                "provider evidence comparison references are invalid"
            )
        if any(
            not {link.local_reference for link in claim.links}.issubset(known)
            for provider in self.providers
            for candidate in (
                *provider.recording_lookup_candidates,
                *provider.refused_products,
                *provider.audio_editions,
            )
            for claim in candidate.claims
        ):
            raise ReportValueError("recording lookup claim references are invalid")
        expected: ProviderCandidate | str
        for provider in self.providers:
            for candidate in (
                *provider.candidates,
                *provider.recording_lookup_candidates,
                *provider.refused_products,
                *provider.audio_editions,
            ):
                expected = finalize_candidate_comparisons(
                    candidate,
                    self.local_claims,
                    provider.provider,
                    allow_unbound_manifestation_link=(
                        _target_backed_work_lookup(self.target, provider)
                    ),
                )
                if candidate != expected:
                    raise ReportValueError("provider comparison links are inconsistent")
        # An observation that names a candidate is a comparison, and a
        # comparison is only readable against the thing it was made with. A
        # citation naming an identity this report does not retain would be
        # presented as a matched edition an owner could go and look at, so it
        # is refused here rather than read later. Demotion to work scope keeps
        # the candidate, so preparation's own records still resolve; only an
        # identity that is in no lane at all fails.
        if self.audio is not None:
            retained = {
                candidate.identity
                for provider in self.providers
                for candidate in provider.candidates
            }
            if any(
                observation.candidate is not None
                and observation.candidate not in retained
                for observation in self.audio.observations
            ):
                raise ReportValueError("audio observation cites an unknown candidate")
        if self.report_id != report_content_address(self._identity_data()):
            raise ReportValueError("report identity is inconsistent")
        if len(self.to_json().encode("utf-8")) > MAX_REPORT_BYTES:
            raise ReportValueError("report exceeds its byte limit")

    def _validate_lane_dispatch(self, planned: dict[str, WorkLanePayload]) -> None:
        """Replay every lane's dispatch record, one rule at a time (#1258).

        Each rule runs over every lane before the next starts, so the first
        rule a report breaks is the one it is refused for.
        """
        lanes = tuple(
            (provider, lane) for provider in self.providers for lane in provider.lanes
        )
        if any(
            (lane.dispatch is None) is (self.audio is not None) for _, lane in lanes
        ):
            raise ReportValueError("provider lane dispatch presence is invalid")
        if self.audio is None:
            return
        # A rule admission stands only where every exact path refused (#1307):
        # every planned alternative lane was asked and ended `no_match`, and
        # no ASIN lane answered with a product, stubs included.
        alternatives = tuple(
            identity
            for identity, payload in planned.items()
            if payload.title_basis is not None
        )
        for provider in self.providers:
            if not any(_corroborated_lane(lane) for lane in provider.lanes):
                continue
            statuses = {lane.identity: lane.status for lane in provider.lanes}
            if any(
                statuses.get(identity) is not ProviderStatus.NO_MATCH
                for identity in alternatives
            ) or any(
                lane.kind is ProviderQueryKind.ASIN
                and lane.status is ProviderStatus.CANDIDATES
                for lane in provider.lanes
            ):
                raise ReportValueError("corroborated admission precedes an exact path")
        for _, lane in lanes:
            lane._validate_dispatch()
        if any(
            cast("ProviderLaneDispatch", lane.dispatch).outcome
            is DispatchOutcome.STARVED
            and not starvation_backed(provider, lane)
            for provider, lane in lanes
        ):
            raise ReportValueError("provider lane starvation is unbacked")
        counted = tuple(
            (provider, lane, cast("ProviderLaneDispatch", lane.dispatch))
            for provider, lane in lanes
            if cast("ProviderLaneDispatch", lane.dispatch).rungs is not None
        )
        if any(
            provider.authority != "audible_catalog"
            or lane.kind is not ProviderQueryKind.WORK
            or lane.identity not in planned
            for provider, lane, _ in counted
        ):
            raise ReportValueError("provider lane rungs provenance is invalid")
        for provider, lane, dispatch in counted:
            payload = planned[lane.identity]
            ladder = lane_work_ladder(provider.authority, payload)
            rungs = cast("int", dispatch.rungs)
            if dispatch.outcome is DispatchOutcome.NOT_DISPATCHED:
                expected = rungs == 0
            elif dispatch.outcome in {DispatchOutcome.EMPTY, DispatchOutcome.REFUSED}:
                expected = rungs == len(lane.exhausted_dispatch)
            elif dispatch.outcome is DispatchOutcome.ADMITTED and _corroborated_lane(
                lane
            ):
                # The rule reads only after the whole ladder was sent (#1307).
                expected = rungs == len(ladder)
            elif dispatch.outcome is DispatchOutcome.ADMITTED:
                # A band-first admission (#1307) replays as the exact path
                # that admitted its one product, so it is recorded as that
                # path stops.
                expected = (
                    lane.dispatch_lineage in ladder
                    and rungs == ladder.index(lane.dispatch_lineage) + 1
                )
            else:
                expected = 1 <= rungs <= len(ladder)
            if not expected:
                raise ReportValueError("provider lane rungs do not replay")
        # Imported here: the corroboration module imports this one, and it
        # alone owns the loose band (ADR-0064).
        from mechanical_librarian.domain.audiobook_corroboration import (
            RUNTIME_BAND_LOOSE_SECONDS,
            local_claim_runtime_seconds,
        )

        local = local_claim_runtime_seconds(self.local_claims)
        misses = tuple(
            (provider, lane, lane.dispatch.near_miss_seconds)
            for provider, lane in lanes
            if lane.dispatch is not None and lane.dispatch.near_miss_seconds is not None
        )
        if any(
            provider.authority != "audible_catalog"
            or (asked := planned.get(lane.identity)) is None
            or asked.title_basis is not None
            or local is None
            for provider, lane, _ in misses
        ):
            raise ReportValueError("provider lane near-miss provenance is invalid")
        if any(abs(value) <= RUNTIME_BAND_LOOSE_SECONDS for _, _, value in misses):
            raise ReportValueError("provider lane near-miss is inside the loose band")
        if any(
            abs(value) > MAX_DURATION_MILLISECONDS // 1000 for _, _, value in misses
        ):
            raise ReportValueError("provider lane near-miss exceeds its bound")

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
        audio: AudioFacts | None = None,
        companion: CompanionEbook | None = None,
        library_languages: tuple[str, ...] = (),
    ) -> Self:
        local_status = (
            "candidate"
            if any(value.basis == "opening_matter" for value in local_claims)
            else "no_candidate"
        )
        providers = tuple(
            replace(
                provider,
                recording_lookup_candidates=tuple(
                    finalize_candidate_comparisons(
                        candidate,
                        local_claims,
                        provider.provider,
                        allow_unbound_manifestation_link=(
                            _target_backed_work_lookup(target, provider)
                        ),
                    )
                    for candidate in provider.recording_lookup_candidates
                ),
                refused_products=tuple(
                    finalize_candidate_comparisons(
                        candidate,
                        local_claims,
                        provider.provider,
                        allow_unbound_manifestation_link=(
                            _target_backed_work_lookup(target, provider)
                        ),
                    )
                    for candidate in provider.refused_products
                ),
                audio_editions=tuple(
                    finalize_candidate_comparisons(
                        candidate,
                        local_claims,
                        provider.provider,
                        allow_unbound_manifestation_link=(
                            _target_backed_work_lookup(target, provider)
                        ),
                    )
                    for candidate in provider.audio_editions
                ),
                candidates=tuple(
                    finalize_candidate_comparisons(
                        candidate,
                        local_claims,
                        provider.provider,
                        allow_unbound_manifestation_link=(
                            _target_backed_work_lookup(target, provider)
                        ),
                    )
                    for candidate in provider.candidates
                ),
            )
            for provider in providers
        )
        results = (
            field_results
            if field_results is not None
            else derive_field_results(local_claims)
        )
        identifiers = (
            identifier_evidence
            if identifier_evidence is not None
            else derive_identifier_evidence(local_claims, providers, target, audio)
        )
        data = _report_data(
            "",
            target,
            file,
            local_claims,
            local_status,
            local_reasons,
            providers,
            results,
            identifiers,
            cover_sources,
            audio,
            companion,
            library_languages,
        )
        data.pop("report_id")
        return cls(
            report_content_address(data),
            target,
            file,
            local_claims,
            local_status,
            local_reasons,
            providers,
            results,
            identifiers,
            cover_sources,
            audio=audio,
            companion=companion,
            library_languages=library_languages,
        )

    def _identity_data(self) -> dict[str, object]:
        value = self.to_data()
        value.pop("report_id")
        return value

    def to_data(self) -> dict[str, object]:
        return _report_data(
            self.report_id,
            self.target,
            self.file,
            self.local_claims,
            self.local_status,
            self.local_reasons,
            self.providers,
            self.field_results,
            self.identifier_evidence,
            self.cover_sources,
            self.audio,
            self.companion,
            self.library_languages,
        )

    def to_json(self) -> str:
        return canonical_json(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> MetadataReport:
        try:
            # The size guard encodes the document, so it belongs inside the
            # conversion boundary: an unpaired surrogate makes ``encode`` raise
            # ``UnicodeEncodeError``, and outside this block that escaped as
            # itself and made the declared ``ReportValueError`` contract a
            # half-truth callers could not narrow to.
            if (
                type(serialized) is not str
                or not serialized
                or len(serialized.encode("utf-8")) > MAX_REPORT_BYTES
            ):
                raise ReportValueError("report document is invalid")
            source = json.loads(
                serialized,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_number,
            )
            report = _report_from_data(source)
        except ReportValueError:
            raise
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
            json.JSONDecodeError,
        ) as error:
            raise ReportValueError("report document is invalid") from error
        if report.to_json() != serialized:
            raise ReportValueError("report document is not canonical")
        return report


def derive_field_results(
    local_claims: tuple[LocalClaim, ...],
) -> tuple[FieldResult, ...]:
    """Summarise observed field states without making claims a decision surface."""
    precedence = (
        FieldState.MALFORMED,
        FieldState.OVERSIZED,
        FieldState.UNSUPPORTED,
        FieldState.UNTYPED,
        FieldState.INAPPLICABLE,
        FieldState.VALID,
    )

    def source_state(kind: ClaimKind, source: FieldSource) -> FieldState:
        # The ABS-asserted surface carries only the kinds `LocalClaim` admits a
        # `target_metadata` basis for. Reporting every other kind as
        # `unsupported` rather than `absent` keeps "this surface cannot carry
        # the field" distinct from "this surface carried nothing". The reading
        # follows the claims rather than a second list of kinds: a surface that
        # actually stated this field is reported from what it stated. A carried
        # admission (#1316) can only ever carry an ASIN.
        bases = _SOURCE_CLAIM_BASES[source]
        if (
            source is FieldSource.TARGET_METADATA
            and kind is not ClaimKind.TITLE
            and not any(
                claim.kind is kind and claim.basis in bases for claim in local_claims
            )
        ) or (source is FieldSource.PRIOR_ADMISSION and kind is not ClaimKind.ASIN):
            return FieldState.UNSUPPORTED
        return next(
            (
                state
                for state in precedence
                if any(
                    claim.kind is kind and claim.state is state and claim.basis in bases
                    for claim in local_claims
                )
            ),
            FieldState.ABSENT,
        )

    return tuple(
        FieldResult(
            kind,
            next(
                (
                    state
                    for state in precedence
                    if any(
                        claim.kind is kind and claim.state is state
                        for claim in local_claims
                    )
                ),
                FieldState.ABSENT,
            ),
            tuple(
                FieldSourceResult(source, source_state(kind, source))
                for source in FieldSource
            ),
        )
        for kind in ClaimKind
    )


class _ProviderIdentityFields(TypedDict):
    """The provider attribution keys `IdentifierEvidence` accepts by keyword."""

    provider: str
    candidate_identity: str
    work_identity: str


def recording_association_references(
    local_claims: tuple[LocalClaim, ...],
    providers: tuple[ProviderObservation, ...],
    target: TargetFacts | None,
    audio: AudioFacts | None,
    provider_name: str,
    candidate: ProviderCandidate,
) -> tuple[str, ...]:
    """Bind associated ISBNs to item identity or independent exact work evidence.

    This field-evidence predicate does not widen recording admission. Runtime is
    never an independent work or item-identifier anchor.
    """
    provider = next(
        (
            p
            for p in providers
            if p.provider == provider_name and candidate in p.candidates
        ),
        None,
    )
    if (
        audio is None
        or target is None
        or provider is None
        or provider.authority not in AUDIOBOOK_PROVIDER_AUTHORITIES
        or provider.status is not ProviderStatus.CANDIDATES
    ):
        return ()
    recording = tuple(
        e
        for e in candidate.evidence
        if e.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
        and e.scope is ClaimScope.EDITION
    )
    asins = tuple(
        c
        for c in candidate.claims
        if c.kind is ClaimKind.ASIN
        and c.state is FieldState.VALID
        and canonical_asin(c.value) is not None
    )
    if (
        not recording
        or len(asins) != 1
        or _provider_candidate_has_work_identity_veto(candidate)
    ):
        return ()
    item = tuple(
        c.reference
        for c in local_claims
        if c.kind is ClaimKind.ASIN
        and c.state is FieldState.VALID
        and c.basis in {"embedded_tag", "filename"}
        and canonical_asin(c.canonical) == asins[0].value
    )
    if item:
        return tuple(sorted((*item, asins[0].reference)))
    context = target_work_identity_context(target)
    if context is None:
        return ()
    admission = provider_candidate_set_exact_work_identity_admission(
        provider.candidates, *context, audiobook=audio is not None
    )
    if not admission.admitted or candidate not in admission.candidates:
        return ()
    # Exact typed claims consumed by admission; report binds target and media.
    work = tuple(
        c.reference
        for c in candidate.claims
        if c.scope is ClaimScope.WORK
        and c.state is FieldState.VALID
        and (
            c.kind is ClaimKind.TITLE
            or c.kind is ClaimKind.CREATOR
            and c.role in {None, "author"}
        )
    )
    return tuple(sorted((asins[0].reference, *work)))


def derive_identifier_evidence(
    local_claims: tuple[LocalClaim, ...],
    providers: tuple[ProviderObservation, ...],
    target: TargetFacts | None = None,
    audio: AudioFacts | None = None,
) -> tuple[IdentifierEvidence, ...]:
    """Classify every ISBN claim without discarding its original evidence."""
    local_isbns = tuple(claim for claim in local_claims if claim.kind is ClaimKind.ISBN)
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
    claim: LocalClaim | ProviderClaim
    related: tuple[str, ...] | list[str]
    for claim in local_isbns:
        family = local_families.get(claim.reference)
        if family is None:
            continue
        family_sources.setdefault(family, set()).add(claim.basis)
        family_local_claims.setdefault(family, []).append(claim)

    local_acceptance: dict[str, IdentifierEvidenceBasis] = {}
    for family, claims in family_local_claims.items():
        for claim in claims:
            if claim.applicability == "ebook" and claim.applicability_basis == "ebook":
                local_acceptance[claim.reference] = (
                    IdentifierEvidenceBasis.EXPLICIT_EBOOK
                )
        if family_sources[family] >= {"package_metadata", "opening_matter"} and all(
            claim.applicability == "unknown"
            and claim.applicability_basis == "unlabelled"
            for claim in claims
        ):
            for claim in claims:
                local_acceptance[claim.reference] = (
                    IdentifierEvidenceBasis.LOCAL_SOURCE_AGREEMENT
                )

    accepted_local_references = {
        family: [
            claim.reference for claim in claims if claim.reference in local_acceptance
        ]
        for family, claims in family_local_claims.items()
    }
    accepted_local_families = {
        family for family, references in accepted_local_references.items() if references
    }

    accepted_family_works: dict[tuple[str, str], dict[str, list[str]]] = {}
    for provider, candidate, claim in provider_isbns:
        family = provider_families.get(claim.reference)
        if (
            family in accepted_local_families
            and candidate.work_identity != "unresolved"
        ):
            accepted_family_works.setdefault((provider, family), {}).setdefault(
                candidate.work_identity, []
            ).append(claim.reference)

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

    provider_by_family: dict[
        str, list[tuple[str, ProviderCandidate, ProviderClaim]]
    ] = {}
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
        identity: _ProviderIdentityFields = {
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
        elif claim.applicability == "unknown" and (
            recording_references := recording_association_references(
                local_claims, providers, target, audio, provider, candidate
            )
        ):
            evidence.append(
                IdentifierEvidence(
                    claim.reference,
                    IdentifierRelation.RECORDING_ASSOCIATED,
                    IdentifierEvidenceBasis.RECORDING_ASSOCIATED,
                    recording_references,
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


def canonical_asin(value: str | None) -> str | None:
    """Return one canonical ASIN, or ``None`` when the value is not one.

    Ten upper-case ASCII alphanumerics and nothing else. ADR-0061 admits
    ISBN-10-shaped ASINs without a prefix filter, so this deliberately does not
    look for ``B0``: on the reference library 17 of 103 known ASINs are shaped
    that way and every one of them is a genuine audiobook identifier.
    """
    if type(value) is not str:
        return None
    upper = value.strip().upper()
    return upper if _CANONICAL_ASIN.fullmatch(upper) is not None else None


def candidate_asins(candidates: tuple[ProviderCandidate, ...]) -> frozenset[str]:
    """Return the canonical ASINs these candidates' valid ASIN claims state."""
    return frozenset(
        value
        for candidate in candidates
        for claim in candidate.claims
        if claim.kind is ClaimKind.ASIN
        and claim.state is FieldState.VALID
        and (value := canonical_asin(claim.value)) is not None
    )


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
        "date_confidence": (None if date_confidence is None else date_confidence.value),
    }


def claim_scope(
    kind: ClaimKind,
    date_event: DateEvent | None = None,
) -> ClaimScope:
    if type(kind) is not ClaimKind:
        raise ReportValueError("claim kind is invalid")
    if date_event is not None and type(date_event) is not DateEvent:
        raise ReportValueError("date event is invalid")
    if kind is ClaimKind.DATE_EVENT and date_event in {
        DateEvent.WORK_FIRST_PUBLICATION,
        DateEvent.CREATION,
    }:
        return ClaimScope.WORK
    return (
        ClaimScope.EDITION
        if kind
        in {
            ClaimKind.ISBN,
            ClaimKind.ASIN,
            ClaimKind.PUBLICATION_DATE,
            ClaimKind.DATE_EVENT,
            ClaimKind.CONTEXTUAL_CREDIT,
            ClaimKind.PUBLISHER,
            ClaimKind.IMPRINT,
            ClaimKind.LANGUAGE,
            # A running time and a performance treatment are properties of one
            # recorded manifestation, never of the work it renders.
            ClaimKind.DURATION,
            ClaimKind.PERFORMANCE_TYPE,
            ClaimKind.ADULT_PRODUCT,
        }
        else ClaimScope.WORK
    )


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
    if left_precision is DatePrecision.TIMESTAMP:
        _comparison_value(ClaimKind.PUBLICATION_DATE, left_value)
    if right_precision is DatePrecision.TIMESTAMP:
        _comparison_value(ClaimKind.PUBLICATION_DATE, right_value)
    shared: DatePrecision | None = None
    if left[0] == right[0]:
        shared = DatePrecision.YEAR
        if len(left) >= 2 and len(right) >= 2 and left[1] == right[1]:
            shared = DatePrecision.MONTH
            if len(left) >= 3 and len(right) >= 3 and left[2] == right[2]:
                shared = DatePrecision.DAY
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


def _date_components(value: str, precision: DatePrecision) -> tuple[str, ...]:
    normalized, actual = normalize_date_value(value)
    if actual is not precision:
        raise ReportValueError("date precision is inconsistent")
    if precision is DatePrecision.YEAR:
        return (normalized[:4],)
    if precision is DatePrecision.MONTH:
        return (normalized[:4], normalized[5:7])
    return (normalized[:4], normalized[5:7], normalized[8:10])


def _date_comparison_link(
    local_reference: str | None,
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
    allow_unbound_manifestation_link: bool = False,
) -> tuple[ComparisonLink, ...]:
    links: list[ComparisonLink] = []
    for claim in local:
        if (
            claim.kind not in {ClaimKind.PUBLICATION_DATE, ClaimKind.DATE_EVENT}
            or claim.state is not FieldState.VALID
            or claim.canonical is None
            or type(claim.date_event) is not DateEvent
            or type(claim.date_precision) is not DatePrecision
            or not publication_date_is_voting(claim.observed, claim.date_precision)
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
        event is not DateEvent.MANIFESTATION_PUBLICATION or not manifestation_compatible
    ):
        relation = (
            PublicationDateRelation.EVENT_INCOMPATIBLE
            if event is not DateEvent.MANIFESTATION_PUBLICATION
            else PublicationDateRelation.MANIFESTATION_INCOMPATIBLE
        )
        anchors = tuple(
            claim
            for claim in local
            if claim.kind is ClaimKind.ISBN
            and claim.state is FieldState.VALID
            and claim.canonical is not None
            and claim.applicability in {"ebook", "unknown"}
        )
        if not anchors:
            anchors = tuple(
                claim
                for claim in local
                if claim.kind in {ClaimKind.TITLE, ClaimKind.CREATOR}
                and claim.state is FieldState.VALID
                and claim.canonical is not None
            )
        if anchors:
            links.extend(
                _date_comparison_link(claim.reference, relation, None)
                for claim in anchors
            )
        elif (
            allow_unbound_manifestation_link
            and relation is PublicationDateRelation.MANIFESTATION_INCOMPATIBLE
        ):
            links.append(_date_comparison_link(None, relation, None))
    return tuple(
        sorted(
            links,
            key=lambda link: _comparison_reference_sort_key(link.local_reference),
        )
    )


def comparison_links(
    provider: ProviderClaim, local: tuple[LocalClaim, ...]
) -> tuple[ComparisonLink, ...]:
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
    # Every link here was built from a local claim reference, so no link in this
    # list carries the unbound `local_reference is None` form.
    return tuple(sorted(links, key=lambda link: cast("str", link.local_reference)))


_TYPOGRAPHIC_TRANSLATION = str.maketrans(
    {
        "\u00a0": " ",
        "\u2013": "-",
        "\u2014": "-",
    }
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
    ComparisonMethod.DURATION_BAND_AGREEMENT: 6,
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
    """Bind dates through an ISBN family or retained recording admission.

    Audiobook adapters initially retain the source's typed release statement.
    Application admission removes both its date and marketplace when recording
    support is insufficient; this predicate does not establish ISBN format.
    """
    if any(
        evidence.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
        and evidence.scope is ClaimScope.EDITION
        for evidence in candidate.evidence
    ) and any(
        claim.kind is ClaimKind.ASIN
        and claim.state is FieldState.VALID
        and canonical_asin(claim.value) is not None
        for claim in candidate.claims
    ):
        return True
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
        and normalize_title(evidence.value).casefold() in _PRINT_FORMAT_CONTRADICTIONS
        for evidence in candidate.evidence
    ):
        return False
    if any(
        evidence.kind is ProviderEvidenceKind.EDITION_WORK_RELATION_CONTRADICTION
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
    *,
    allow_unbound_manifestation_link: bool = False,
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
        if (
            claim.scope is ClaimScope.WORK
            and claim.date_event is DateEvent.WORK_FIRST_PUBLICATION
        ):
            retained_claims.append(claim)
            continue
        if claim.state is not FieldState.VALID:
            retained_claims.append(claim)
            continue
        if (
            compatible
            and claim.value is not None
            and type(claim.date_event) is DateEvent
            and type(claim.date_precision) is DatePrecision
            and type(claim.date_lexical) is str
            and publication_date_is_voting(claim.date_lexical, claim.date_precision)
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
            and publication_date_is_voting(value.value, value.date_precision)
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
                    local=(
                        local
                        if publication_date_is_voting(value.value, value.date_precision)
                        else tuple(
                            claim
                            for claim in local
                            if claim.kind
                            not in {
                                ClaimKind.PUBLICATION_DATE,
                                ClaimKind.DATE_EVENT,
                            }
                        )
                    ),
                    manifestation_compatible=(
                        compatible
                        if value.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
                        else True
                    ),
                    allow_unbound_manifestation_link=(
                        allow_unbound_manifestation_link
                        and value.kind is ProviderEvidenceKind.PUBLICATION_DATE_TEXT
                    ),
                ),
            )
        elif value.links:
            value = replace(value, links=())
        promoted_evidence.append(value)

    provisional = replace(
        candidate,
        claims=tuple(sorted(retained_claims, key=lambda value: value.raw_order)),
        evidence=tuple(
            sorted(
                promoted_evidence,
                key=lambda value: (
                    value.scope.value,
                    value.kind.value,
                    value.value,
                    value.role or "",
                    ("" if value.date_event is None else value.date_event.value),
                    (
                        ""
                        if value.date_precision is None
                        else value.date_precision.value
                    ),
                    value.date_normalized or "",
                    value.taxonomy_type or "",
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
            local_groups.setdefault((claim.basis, claim.scope, claim.role), []).append(
                claim
            )
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
                methods = creator_sequence_methods(provider_values, local_values)
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
    # Every link here was built from a local claim reference, so no link in this
    # list carries the unbound `local_reference is None` form.
    return tuple(sorted(links, key=lambda link: cast("str", link.local_reference)))


def _bound_creator_sequence_match(
    candidate: ProviderCandidate,
    provider_claims: tuple[ProviderClaim, ...],
    local_values: tuple[str, ...],
) -> dict[str, tuple[int, ComparisonMethod]]:
    claims_by_reference = {claim.reference: claim for claim in provider_claims}
    bindings = complete_creator_identity_bindings(candidate, provider_claims)
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
            method is not ComparisonMethod.NONE for method in alias_methods.values()
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
                if binding.scope is claims[0].scope and binding.role == claims[0].role
            ),
            key=lambda binding: (
                binding.source_ordinal,
                binding.identity,
                binding.claim_reference,
            ),
        )
    )
    if len(bindings) != len(claims) or {
        binding.claim_reference for binding in bindings
    } != {claim.reference for claim in claims}:
        return None
    return bindings


def creator_sequence_methods(
    provider_values: tuple[str, ...],
    local_values: tuple[str, ...],
) -> tuple[ComparisonMethod, ...]:
    """Compare complete ordered creators through the closed lexical vocabulary."""
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
    if kind is ClaimKind.DURATION:
        # A running time is the one claim two sources state at different
        # granularities and still mean the same recording: an audiobook
        # provider publishes minutes, the container states milliseconds. Only
        # an audiobook provider produces a `DURATION` claim, so no ebook
        # comparison reaches this branch.
        return _duration_band_method(provider_value, local_value)
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
        work_title_has_variant_marker(provider_value)
        or work_title_has_variant_marker(local_value)
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


def duration_milliseconds(value: str) -> int | None:
    """Read one canonical duration into whole milliseconds, or ``None``."""
    try:
        canonical = normalize_duration_seconds(value)
    except (TypeError, ValueError):
        return None
    whole, _, fraction = canonical.partition(".")
    return int(whole) * 1000 + int(fraction)


def _duration_band_method(provider_value: str, local_value: str) -> ComparisonMethod:
    provider_milliseconds = duration_milliseconds(provider_value)
    local_milliseconds = duration_milliseconds(local_value)
    if provider_milliseconds is None or local_milliseconds is None:
        return ComparisonMethod.NONE
    if (
        abs(provider_milliseconds - local_milliseconds)
        <= DURATION_AGREEMENT_BAND_SECONDS * 1000
    ):
        return ComparisonMethod.DURATION_BAND_AGREEMENT
    return ComparisonMethod.NONE


def _comparison_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).casefold()
    return " ".join(part for part in re.split(r"[\t\n\r\f\v ]+", normalized) if part)


def _typographic_text(value: str) -> str | None:
    paired = {
        "\u2018": ("'", True),
        "\u2019": ("'", False),
        "\u201c": ('"', True),
        "\u201d": ('"', False),
    }
    stack: list[str] = []
    translated: list[str] = []
    # ADR-0018 step 4 as amended by #1307: an apostrophe between letters
    # needs no partner.
    for character in fold_lone_apostrophes(value):
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
    return len({creator_comparison_key(value) for value in values}) != len(values)


def _comma_inverted(value: str) -> str | None:
    if value.count(",") != 1:
        return None
    typographic = _typographic_text(value)
    if typographic is None or typographic.count(",") != 1:
        return None
    family, given = (_comparison_text(part) for part in typographic.split(",", 1))
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
        return "".join(
            character for character in value if character.isdigit() or character in "Xx"
        ).upper()
    if kind is ClaimKind.PUBLICATION_DATE:
        normalized, precision = normalize_date_value(value)
        if precision is DatePrecision.TIMESTAMP:
            instant = datetime.fromisoformat(normalized)
            if not publication_date_is_voting(value, precision):
                raise ReportValueError(
                    "comparison timestamp requires an explicit offset"
                )
            return instant.astimezone(timezone.utc).isoformat()
        return normalized
    if kind is ClaimKind.DESCRIPTION:
        _projection_text(value, "comparison description", MAX_DESCRIPTION_CHARACTERS)
        return " ".join(unicodedata.normalize("NFC", value).split()).casefold()
    if kind in {ClaimKind.DURATION, ClaimKind.PERFORMANCE_TYPE}:
        # Both already arrive in exactly one canonical spelling -- a
        # millisecond-precision seconds string and a closed token -- so the
        # title normaliser, which is written for prose, must not touch them.
        _projection_text(value, "comparison audio value", MAX_SCALAR_CHARACTERS)
        return value
    return normalize_title(value).casefold()


def report_content_address(identity_data: dict[str, object]) -> str:
    """The report id that a report's data, without that id, addresses."""
    digest = hashlib.sha256(canonical_json(identity_data).encode("utf-8"))
    return f"report_{digest.hexdigest()}"


def _report_data(
    report_id: str,
    target: TargetFacts,
    file: FileFacts,
    local_claims: tuple[LocalClaim, ...],
    local_status: str,
    local_reasons: tuple[str, ...],
    providers: tuple[ProviderObservation, ...],
    field_results: tuple[FieldResult, ...],
    identifier_evidence: tuple[IdentifierEvidence, ...],
    cover_sources: tuple[CoverSource, ...],
    audio: AudioFacts | None = None,
    companion: CompanionEbook | None = None,
    library_languages: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "audio": None if audio is None else audio.to_data(),
        "companion": None if companion is None else companion.to_data(),
        "file": file.to_data(),
        "library_languages": list(library_languages),
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
    # A predecessor document is refused for what it is before any section of
    # it is read under the current vocabulary, including the root key set,
    # which v62 widened with the library languages (#1315) and v63 with the
    # companion ebook (#1319).
    if type(value) is dict and value.get("schema_version") != REPORT_SCHEMA:
        raise ReportValueError("report schema is unsupported")
    root = _object(
        value,
        {
            "audio",
            "companion",
            "comparison_version",
            "covers",
            "field_results",
            "file",
            "identifier_evidence",
            "library_languages",
            "local",
            "providers",
            "report_id",
            "schema_version",
            "target",
        },
    )
    target = _object(
        root["target"],
        {
            "connection",
            "fields",
            "item",
            "library",
        },
    )
    file = _object(
        root["file"], {"content_digest", "format", "identity", "selected_index"}
    )
    local = _object(root["local"], {"claims", "reasons", "status"})
    claims = tuple(_local_claim_from_data(item) for item in _list(local["claims"]))
    providers = tuple(_provider_from_data(item) for item in _list(root["providers"]))
    field_results = tuple(
        _field_result_from_data(item) for item in _list(root["field_results"])
    )
    identifier_evidence = tuple(
        _identifier_evidence_from_data(item)
        for item in _list(root["identifier_evidence"])
    )
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
        FileFacts(
            file["selected_index"],
            file["identity"],
            file["content_digest"],
            file["format"],
        ),
        claims,
        local["status"],
        reasons,
        providers,
        field_results,
        identifier_evidence,
        covers,
        root["schema_version"],
        root["comparison_version"],
        _audio_facts_from_data(root["audio"]),
        tuple(_strings(root["library_languages"])),
        _companion_from_data(root["companion"]),
    )


def _companion_from_data(value: object) -> CompanionEbook | None:
    if value is None:
        return None
    item = _object(value, {"claims", "content_digest", "identity"})
    return CompanionEbook(
        item["identity"],
        item["content_digest"],
        tuple(_local_claim_from_data(claim) for claim in _list(item["claims"])),
    )


def _audio_facts_from_data(value: object) -> AudioFacts | None:
    if value is None:
        return None
    item = _object(
        value,
        {
            "chapters",
            "chapters_truncated",
            "chapter_rows_omitted",
            "composition",
            "discovery_titles",
            "embedded_cover",
            "observations",
            "sidecars",
            "tracks",
        },
    )
    cover = _object(item["embedded_cover"], {"present", "surface", "tracks"})
    try:
        return AudioFacts(
            tuple(
                _audio_chapter_from_data(chapter) for chapter in _list(item["chapters"])
            ),
            item["chapters_truncated"],
            AudioEmbeddedCover(
                cover["present"],
                None
                if cover["surface"] is None
                else AudioEvidenceSurface(cover["surface"]),
                tuple(_integers(cover["tracks"])),
            ),
            tuple(
                _audio_sidecar_from_data(sidecar) for sidecar in _list(item["sidecars"])
            ),
            AudioChapterComposition(item["composition"]),
            tuple(_audio_track_from_data(track) for track in _list(item["tracks"])),
            tuple(
                _audio_observation_from_data(observation)
                for observation in _list(item["observations"])
            ),
            item["chapter_rows_omitted"],
            tuple(
                AudioDiscoveryTitle(entry["basis"], entry["title"])
                for entry in (
                    _object(value, {"basis", "title"})
                    for value in _list(item["discovery_titles"])
                )
            ),
        )
    except ValueError as error:
        raise ReportValueError("audio facts are invalid") from error


def _integers(value: object) -> tuple[int, ...]:
    values = _list(value)
    if any(type(item) is not int for item in values):
        raise ReportValueError("audio track ordinals are invalid")
    return tuple(cast("list[int]", values))


def _audio_track_from_data(value: object) -> AudioTrackDigest:
    item = _object(
        value,
        {
            "claims",
            "digest",
            "duration_milliseconds",
            "format",
            "identity",
            "name",
            "ordinal",
            "sequence",
        },
    )
    try:
        return AudioTrackDigest(
            item["ordinal"],
            item["identity"],
            item["digest"],
            item["format"],
            item["name"],
            item["sequence"],
            item["duration_milliseconds"],
            tuple(_strings(item["claims"])),
        )
    except ValueError as error:
        raise ReportValueError("audio track manifest is invalid") from error


def _audio_observation_from_data(value: object) -> AudioObservation:
    item = _object(value, {"candidate", "field", "kind", "tracks"})
    try:
        return AudioObservation(
            AudioObservationKind(item["kind"]),
            _integers(item["tracks"]),
            None if item["field"] is None else AudioInspectionField(item["field"]),
            item["candidate"],
        )
    except ValueError as error:
        raise ReportValueError("audio observations are invalid") from error


def _audio_sidecar_from_data(value: object) -> AudioSidecarDigest:
    item = _object(value, {"digest", "name"})
    try:
        return AudioSidecarDigest(item["name"], item["digest"])
    except ValueError as error:
        raise ReportValueError("audio sidecar manifest is invalid") from error


def _audio_chapter_from_data(value: object) -> AudioChapter:
    item = _object(
        value, {"end_milliseconds", "ordinal", "start_milliseconds", "title"}
    )
    try:
        return AudioChapter(
            item["ordinal"],
            item["start_milliseconds"],
            item["end_milliseconds"],
            item["title"],
        )
    except ValueError as error:
        raise ReportValueError("audio chapter is invalid") from error


def _target_field_from_data(value: object, *, flagged: bool = True) -> TargetField:
    # A report of an earlier schema is read in its own shape, without the
    # description's restore flag (scripts/qualification_state_bridge.py).
    described = (
        flagged and type(value) is dict and value.get("destination") == "description"
    )
    item = _object(
        value,
        {"destination", "state", "value"} | ({"restorable"} if described else set()),
    )
    destination = TargetDestination(item["destination"])
    state = TargetFieldState(item["state"])
    raw = item["value"]
    parsed: str | bool | tuple[str, ...] | tuple[TargetSeries, ...] | None = None
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
    restorable = item.get("restorable", False)
    if type(restorable) is not bool:
        raise ReportValueError("target field is invalid")
    return TargetField(destination, state, parsed, restorable)


def _field_result_from_data(value: object) -> FieldResult:
    item = _object(value, {"kind", "state", "sources"})
    return FieldResult(
        ClaimKind(item["kind"]),
        FieldState(item["state"]),
        tuple(
            FieldSourceResult(
                FieldSource(source["source"]), FieldState(source["state"])
            )
            for source in _list(item["sources"])
        ),
    )


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
        artifact = CoverArtifact(
            **_object(
                item["artifact"],
                {
                    "artifact_id",
                    "height",
                    "media_type",
                    "original_digest",
                    "perceptual_hash",
                    "preview_digest",
                    "preview_variant",
                    "width",
                },
            )
        )
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
                    _object(value, {"field", "value"}) for value in source["context"]
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
    item = _object(
        value,
        {
            "applicability",
            "applicability_basis",
            "basis",
            "canonical",
            "checksum",
            "date_event",
            "date_precision",
            "excerpt",
            "kind",
            "observed",
            "reference",
            "state",
            "scope",
            "role",
            "rear_provenance",
            "occurrence",
        },
    )
    provenance = item["rear_provenance"]
    return LocalClaim(
        item["reference"],
        ClaimKind(item["kind"]),
        item["observed"],
        item["canonical"],
        item["excerpt"],
        item["applicability"],
        item["basis"],
        item["checksum"],
        item["applicability_basis"],
        ClaimScope(item["scope"]),
        FieldState(item["state"]),
        item["role"],
        None
        if provenance is None
        else RearClaimProvenance(
            **_object(
                provenance,
                {
                    "citation",
                    "marker",
                    "resource_identity",
                    "spine_ordinal",
                    "tail_slot",
                },
            )
        ),
        item["occurrence"],
        None if item["date_event"] is None else DateEvent(item["date_event"]),
        None
        if item["date_precision"] is None
        else DatePrecision(item["date_precision"]),
    )


def _provider_from_data(value: object) -> ProviderObservation:
    item = _object(
        value,
        {
            "accessed_at",
            "audio_editions",
            "authority",
            "candidates",
            "recording_lookup_candidates",
            "refused_products",
            "convergence",
            "diagnostics",
            "dispatch_lineage",
            "edition_enrichment_overflow",
            "input_references",
            "lanes",
            "provider",
            "query_kind",
            "request_accounting",
            "status",
            "transformation_lineage",
        },
    )
    candidates = tuple(
        _candidate_from_data(candidate) for candidate in _list(item["candidates"])
    )
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
        (
            None
            if item["edition_enrichment_overflow"] is None
            else _edition_enrichment_overflow_from_data(
                item["edition_enrichment_overflow"]
            )
        ),
        (
            None
            if item["dispatch_lineage"] is None
            else work_dispatch_lineage_from_data(item["dispatch_lineage"])
        ),
        (
            None
            if item["transformation_lineage"] is None
            else work_title_transformation_lineage_from_data(
                item["transformation_lineage"]
            )
        ),
        recording_lookup_candidates=tuple(
            _candidate_from_data(candidate)
            for candidate in _list(item["recording_lookup_candidates"])
        ),
        refused_products=tuple(
            _candidate_from_data(candidate)
            for candidate in _list(item["refused_products"])
        ),
        audio_editions=tuple(
            _candidate_from_data(candidate)
            for candidate in _list(item["audio_editions"])
        ),
    )


def _edition_enrichment_overflow_from_data(value: object) -> EditionEnrichmentOverflow:
    item = _object(value, {"authors", "identity_bindings", "title", "work_identity"})
    return EditionEnrichmentOverflow(
        item["work_identity"],
        item["title"],
        tuple(_provider_claim_from_data(author) for author in _list(item["authors"])),
        tuple(
            _provider_identity_binding_from_data(binding)
            for binding in _list(item["identity_bindings"])
        ),
    )


def _request_accounting(value: object) -> ProviderRequestAccounting:
    item = _object(
        value,
        {
            "initial_dispatches",
            "logical_requests",
            "replays",
            "retry_dispatches",
            "undispatched_incomplete",
        },
    )
    try:
        return ProviderRequestAccounting(**item)
    except (TypeError, ReportValueError):
        raise ReportValueError("provider request accounting is invalid") from None


def _provider_lane_from_data(value: object) -> ProviderLaneObservation:
    item = _object(
        value,
        {
            "candidates",
            "diagnostics",
            "dispatch",
            "exhausted_dispatch",
            "identity",
            "input_references",
            "kind",
            "matched_family",
            "dispatch_lineage",
            "recording_lookup_hints",
            "status",
            "transformation_lineage",
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
        tuple(
            FailureDiagnostic.from_json(canonical_json(value))
            for value in _list(item["diagnostics"])
        ),
        (
            None
            if item["dispatch_lineage"] is None
            else work_dispatch_lineage_from_data(item["dispatch_lineage"])
        ),
        (
            None
            if item["transformation_lineage"] is None
            else work_title_transformation_lineage_from_data(
                item["transformation_lineage"]
            )
        ),
        tuple(
            work_dispatch_lineage_from_data(value)
            for value in _list(item["exhausted_dispatch"])
        ),
        tuple(
            _recording_lookup_hint_from_data(value)
            for value in _list(item["recording_lookup_hints"])
        ),
        (
            None
            if item["dispatch"] is None
            else _provider_lane_dispatch_from_data(item["dispatch"])
        ),
    )


def _provider_lane_dispatch_from_data(value: object) -> ProviderLaneDispatch:
    item = _object(value, {"near_miss_seconds", "outcome", "rungs"})
    return ProviderLaneDispatch(
        DispatchOutcome(item["outcome"]), item["rungs"], item["near_miss_seconds"]
    )


def _recording_lookup_hint_from_data(value: object) -> RecordingLookupHint:
    item = _object(value, {"asin", "basis", "rung", "runtime_seconds"})
    if not literal(_RECORDING_LOOKUP_HINT_BASIS)(item["basis"]):
        raise ReportValueError("recording lookup hint is invalid")
    return RecordingLookupHint(
        item["asin"],
        item["runtime_seconds"],
        work_dispatch_lineage_from_data(item["rung"]),
    )


def _provider_lane_candidate_from_data(value: object) -> ProviderLaneCandidate:
    item = _object(value, {"edition_identity", "reference", "work_identity"})
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
            "runtime_short_of_local",
            "work_identity",
        },
    )
    return ProviderCandidate(
        item["identity"],
        item["work_identity"],
        tuple(_provider_claim_from_data(claim) for claim in _list(item["claims"])),
        tuple(
            _provider_evidence_from_data(evidence)
            for evidence in _list(item["evidence"])
        ),
        tuple(
            _provider_identity_binding_from_data(binding)
            for binding in _list(item["identity_bindings"])
        ),
        item["runtime_short_of_local"],
    )


def _provider_claim_from_data(value: object) -> ProviderClaim:
    item = _object(
        value,
        {
            "applicability",
            "date_confidence",
            "date_event",
            "date_lexical",
            "date_precision",
            "kind",
            "links",
            "raw_order",
            "reference",
            "role",
            "scope",
            "state",
            "value",
        },
    )
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
        None
        if item["date_precision"] is None
        else DatePrecision(item["date_precision"]),
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
    item = _object(
        value,
        {
            "chapters",
            "date_confidence",
            "date_event",
            "date_normalized",
            "date_precision",
            "kind",
            "links",
            "role",
            "scope",
            "source_ordinal",
            "taxonomy_type",
            "value",
        },
    )
    return ProviderEvidence(
        ProviderEvidenceKind(item["kind"]),
        item["value"],
        ClaimScope(item["scope"]),
        item["role"],
        None if item["date_event"] is None else DateEvent(item["date_event"]),
        None
        if item["date_precision"] is None
        else DatePrecision(item["date_precision"]),
        item["date_normalized"],
        (
            None
            if item["date_confidence"] is None
            else DateLinkConfidence(item["date_confidence"])
        ),
        item["source_ordinal"],
        tuple(_comparison_link_from_data(link) for link in _list(item["links"])),
        item["taxonomy_type"],
        _provider_chapter_structure_from_data(item["chapters"]),
    )


def _provider_chapter_structure_from_data(
    value: object,
) -> ProviderChapterStructure | None:
    if value is None:
        return None
    item = _object(
        value,
        {
            "chapter_count",
            "durations_seconds",
            "durations_truncated",
            "runtime_seconds",
        },
    )
    return ProviderChapterStructure(
        item["chapter_count"],
        item["runtime_seconds"],
        tuple(_list(item["durations_seconds"])),
        item["durations_truncated"],
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
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or value.strip() != value
        or "\x00" in value
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in value
        )
    ):
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
    if (
        checksum == "valid_isbn_13"
        and len(value) == 13
        and value.isascii()
        and value.isdigit()
    ):
        expected = (
            10
            - sum(
                (1 if index % 2 == 0 else 3) * int(character)
                for index, character in enumerate(value[:12])
            )
            % 10
        ) % 10
        return int(value[-1]) == expected
    if (
        checksum == "valid_isbn_10"
        and len(value) == 10
        and value[:9].isascii()
        and value[:9].isdigit()
        and (value[-1].isascii() and value[-1].isdigit() or value[-1] == "X")
    ):
        return (
            sum(
                (10 - index) * (10 if character == "X" else int(character))
                for index, character in enumerate(value)
            )
            % 11
            == 0
        )
    return False


__all__ = [
    "AUDIO_SET_SCHEMA",
    "CORROBORATED_RULE_AUTHORITY",
    "RecordingCorroboration",
    "lane_work_ladder",
    "provider_candidate_identity_title_claims",
    "recording_corroboration",
    "AUDIOBOOK_MARKETPLACES",
    "AUDIOBOOK_MARKETPLACE_LANGUAGES",
    "DURATION_AGREEMENT_BAND_SECONDS",
    "MAX_AUDIO_SIDECARS",
    "MAX_AUDIO_TRACKS",
    "audio_set_content_digest",
    "audio_set_identity",
    "MAX_PROVIDER_CHAPTER_DURATIONS",
    "ProviderChapterStructure",
    "canonical_asin",
    "duration_milliseconds",
    "ClaimKind",
    "ClaimScope",
    "COMPARISON_VERSION",
    "Comparison",
    "ComparisonLink",
    "ComparisonMethod",
    "EditionEnrichmentOverflow",
    "FieldResult",
    "FieldState",
    "FileFacts",
    "LocalClaim",
    "MAX_AUDIO_EDITIONS",
    "MAX_CANDIDATES",
    "MAX_RECORDING_LOOKUP_HINTS",
    "IdentifierEvidence",
    "IdentifierEvidenceBasis",
    "IdentifierRelation",
    "MAX_REPORT_BYTES",
    "AUDIO_FILE_FORMATS",
    "SUPPORTED_FILE_FORMATS",
    "PROVIDER_AUTHORITIES",
    "PROVIDER_LANE_KINDS",
    "report_content_address",
    "CLAIM_BASES",
    "AUDIO_DISCOVERY_TITLE_BASES",
    "AudioDiscoveryTitle",
    "AudioFacts",
    "COMPANION_EBOOK_CLAIM_KINDS",
    "CompanionEbook",
    "MetadataReport",
    "ProviderCandidate",
    "ProviderClaim",
    "ProviderEvidence",
    "ProviderEvidenceKind",
    "ProviderIdentityBinding",
    "DateLinkConfidence",
    "PublicationDateRelation",
    "ProviderConvergence",
    "ProviderLaneCandidate",
    "ProviderLaneDispatch",
    "ProviderLaneObservation",
    "ProviderObservation",
    "DispatchOutcome",
    "lane_dispatch_outcome",
    "provider_dispatch_class",
    "starvation_backed",
    "RecordingLookupHint",
    "candidate_asins",
    "provider_candidate_exact_work_identity_admission",
    "provider_candidate_work_identity_admission",
    "provider_candidate_set_exact_work_identity_admission",
    "provider_candidate_set_work_identity_admission",
    "ProviderWorkIdentitySetAdmission",
    "ProviderQueryKind",
    "ProviderStatus",
    "PROVIDER_RECOVERY_ELIGIBLE_STATUSES",
    "ASSERTED_ASIN_BASES",
    "WorkIdentityContradiction",
    "REPORT_SCHEMA",
    "ReportValueError",
    "TargetDestination",
    "TargetFacts",
    "TargetField",
    "TargetFieldState",
    "TargetSeries",
    "provider_candidate_work_identity_contradiction",
    "target_work_identity_context",
    "canonical_json",
    "claim_scope",
    "comparison_links",
    "complete_creator_identity_bindings",
    "creator_comparison_key",
    "finalize_candidate_comparisons",
    "manifestation_publication_compatible",
    "publication_date_relation",
    "default_target_fields",
    "derive_identifier_evidence",
    "isbn_family",
    "normalize_date_value",
    "normalize_title",
    "publication_date_is_voting",
    "provider_lane_candidate_reference",
    "stable_reference",
    "validate_observed_title",
    "WorkLanePayload",
    "surviving_discovery_titles",
    "work_discovery_lane_payloads",
    "work_ladder_diagnostics",
]


def discovery_lane_identity(payload: dict[str, object]) -> str:
    """Bind discovery and retained-evidence admission to the same lane payload."""
    return "lane_" + hashlib.sha256(canonical_json(payload).encode()).hexdigest()
