"""Pure, fixed-policy proposal evaluation for one exact metadata report."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Mapping, Self, cast

from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.field_outcomes import (
    language_equivalence_class,
    language_identity,
    language_outcomes_compatible,
    publisher_statement_valid,
)
from mechanical_librarian.domain.field_value_comparison import (
    canonical_text,
    values_match,
)
from mechanical_librarian.domain.metadata_policy import OPTION_CATALOGUE
from mechanical_librarian.domain.metadata_report import (
    AUDIO_FILE_FORMATS,
    AUDIOBOOK_PROVIDER_AUTHORITIES,
    ClaimKind,
    ClaimScope,
    Comparison,
    FieldState,
    IdentifierEvidence,
    IdentifierRelation,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidenceKind,
    ProviderIdentityBinding,
    ProviderLaneObservation,
    ProviderObservation,
    ProviderQueryKind,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
    WorkIdentityContradiction,
    canonical_asin,
    canonical_json,
    complete_creator_identity_bindings,
    creator_comparison_key,
    isbn_family,
    manifestation_publication_compatible,
    normalize_date_value,
    normalize_title,
    provider_candidate_series_identity_matches,
    provider_candidate_set_exact_work_identity_admission,
    provider_candidate_set_work_identity_admission,
    provider_candidate_work_identity_contradiction,
    publication_date_is_voting,
    target_work_identity_context,
)
from mechanical_librarian.domain.work_identity import (
    WorkTitleElectionMatch,
    WorkTitleTransformationFamily,
    direct_work_title_election_matches,
    work_title_election_matches,
)

if TYPE_CHECKING:
    from mechanical_librarian.domain.cover_evidence import CoverArtifact

PROPOSAL_SCHEMA = "proposal-set-v7"
# What the proposals in one set were formed for. F47 owns the writes, so this
# is what tells a decision recorded against an audiobook apart from one that
# may be applied, and it is bound into the set's identity rather than derived
# beside it (#966).
MEDIA_KINDS = ("ebook", "audiobook")
POLICY_VERSION = "fixed-conservative-v24"
BIBLIOGRAPHIC_RELATIONSHIP_SCHEMA = "bibliographic-relationships-v1"
MAX_BIBLIOGRAPHIC_COMBINATIONS = 256
MAX_PROPOSAL_BYTES = 2_097_152
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REPORT_ID = re.compile(r"^report_[0-9a-f]{64}$")
_PROPOSAL_SET_ID = re.compile(r"^proposal_set_[0-9a-f]{64}$")
_ALTERNATIVE_ID = re.compile(r"^alternative_[0-9a-f]{64}$")
_COMBINATION_ID = re.compile(r"^bibliographic_combination_[0-9a-f]{64}$")
_REPRESENTATION_ID = re.compile(r"^representation_[0-9a-f]{64}$")
_EVIDENCE_REFERENCE = re.compile(
    r"^(?:local|provider|evidence|abs|epub|hardcover|open_library|inventaire)_[0-9a-f]{64}$"
)
_CORRELATION = re.compile(r"^correlation_[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_GENERIC_PROVIDER_SUBTITLES = frozenset({"a novel", "stories"})


class ProposalValueError(ValueError):
    """A ProposalSet value is outside the fixed contract."""


class ProposalOutcome(StrEnum):
    PROPOSE_CHANGE = "propose_change"
    ACCEPTABLE_CHOICE = "acceptable_choice"
    UNRESOLVED_CHOICE = "unresolved_choice"
    KEEP = "keep"
    REJECT = "reject"
    DEFER = "defer"
    INELIGIBLE = "ineligible"


class ProposalValueKind(StrEnum):
    TEXT = "text"
    BOOLEAN = "boolean"
    LIST = "list"
    SERIES = "series"
    COVER = "cover"


class ProposalScope(StrEnum):
    WORK = "work"
    EDITION = "edition"
    COVER = "cover"


class ProposalAssessment(StrEnum):
    PRESERVES_TARGET = "preserves_target"
    FILLS_EMPTY = "fills_empty"
    REPLACES_TARGET = "replaces_target"


class BibliographicSelectionKind(StrEnum):
    CANDIDATE = "candidate"
    PRESERVE_TARGET = "preserve_target"


_BIBLIOGRAPHIC_DESTINATIONS = (
    TargetDestination.TITLE,
    TargetDestination.SUBTITLE,
    TargetDestination.SERIES,
)


_DESTINATION_VALUE_SCOPES: dict[
    TargetDestination, frozenset[tuple[ProposalValueKind, ProposalScope]]
] = {
    TargetDestination.TITLE: frozenset({(ProposalValueKind.TEXT, ProposalScope.WORK)}),
    TargetDestination.SUBTITLE: frozenset(
        {
            (ProposalValueKind.TEXT, ProposalScope.WORK),
            (ProposalValueKind.TEXT, ProposalScope.EDITION),
        }
    ),
    TargetDestination.AUTHORS: frozenset(
        {(ProposalValueKind.LIST, ProposalScope.WORK)}
    ),
    TargetDestination.NARRATORS: frozenset(
        {(ProposalValueKind.LIST, ProposalScope.EDITION)}
    ),
    TargetDestination.SERIES: frozenset(
        {(ProposalValueKind.SERIES, ProposalScope.WORK)}
    ),
    TargetDestination.GENRES: frozenset({(ProposalValueKind.LIST, ProposalScope.WORK)}),
    # Published year is retained target context for the single canonical
    # publication-date result.  It must never carry an independent candidate.
    TargetDestination.PUBLISHED_YEAR: frozenset(),
    TargetDestination.PUBLISHED_DATE: frozenset(
        {
            (ProposalValueKind.TEXT, ProposalScope.WORK),
            (ProposalValueKind.TEXT, ProposalScope.EDITION),
        }
    ),
    TargetDestination.PUBLISHER: frozenset(
        {(ProposalValueKind.TEXT, ProposalScope.EDITION)}
    ),
    TargetDestination.DESCRIPTION: frozenset(
        {(ProposalValueKind.TEXT, ProposalScope.WORK)}
    ),
    TargetDestination.ISBN: frozenset(
        {(ProposalValueKind.TEXT, ProposalScope.EDITION)}
    ),
    TargetDestination.ASIN: frozenset(
        {(ProposalValueKind.TEXT, ProposalScope.EDITION)}
    ),
    TargetDestination.LANGUAGE: frozenset(
        {(ProposalValueKind.TEXT, ProposalScope.EDITION)}
    ),
    TargetDestination.EXPLICIT: frozenset(
        {(ProposalValueKind.BOOLEAN, ProposalScope.EDITION)}
    ),
    TargetDestination.TAGS: frozenset({(ProposalValueKind.LIST, ProposalScope.WORK)}),
    TargetDestination.COVER: frozenset(
        {(ProposalValueKind.COVER, ProposalScope.COVER)}
    ),
}

_DESTINATION_DATE_SEMANTICS: dict[
    TargetDestination,
    frozenset[tuple[DateEvent | None, DatePrecision | None]],
] = {
    destination: (
        frozenset(
            {
                (DateEvent.MANIFESTATION_PUBLICATION, DatePrecision.YEAR),
                (DateEvent.MANIFESTATION_PUBLICATION, DatePrecision.MONTH),
                (DateEvent.MANIFESTATION_PUBLICATION, DatePrecision.DAY),
            }
        )
        if destination is TargetDestination.PUBLISHED_DATE
        else frozenset({(None, None)})
    )
    for destination in TargetDestination
}

_TITLE_CONSENSUS_FAMILIES = (
    WorkTitleTransformationFamily.BRACKET_SOURCE_TAG,
    WorkTitleTransformationFamily.EXPLICIT_IDENTIFIER,
    WorkTitleTransformationFamily.PARENTHESIZED_BARE_NUMBER,
    WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME,
    WorkTitleTransformationFamily.PARENTHESIZED_YEAR,
    WorkTitleTransformationFamily.SUBTITLE_SEGMENT,
)
PROVIDER_CONSENSUS_TITLE_REASONS = frozenset(
    f"provider_consensus_title_{family.value}" for family in _TITLE_CONSENSUS_FAMILIES
)
DECORATION_EQUIVALENT_TITLE_REASONS = frozenset(
    f"decoration_equivalent_{family.value}" for family in _TITLE_CONSENSUS_FAMILIES
)
DECORATION_TIDY_TITLE_REASONS = frozenset(
    f"decoration_tidy_{family.value}" for family in _TITLE_CONSENSUS_FAMILIES
)
DECORATION_TIDY_SERIES_UNPRESERVED_REASON = "decoration_tidy_series_unpreserved"
DECORATION_EQUIVALENCE_PROVIDER_DISSENT_REASON = (
    "decoration_equivalence_provider_dissent"
)
DECORATION_EQUIVALENCE_DECORATED_CONSENSUS_REASON = (
    "decoration_equivalence_decorated_consensus"
)
SUBTITLE_SEGMENT_UNCORROBORATED_REASON = "subtitle_segment_uncorroborated"
SUBTITLE_SEGMENT_PROVIDER_DISSENT_REASON = "subtitle_segment_provider_dissent"
SUBTITLE_SEGMENT_DECORATED_CONSENSUS_REASON = "subtitle_segment_decorated_consensus"
PROVIDER_CONSENSUS_CONTRIBUTOR_DISPLAY_REASON = "provider_consensus_contributor_display"
EVIDENCE_LED_TITLE_REASON = "isbn_creator_supported_title"
VERIFIED_MANIFESTATION_ISBN_REASON = "verified_manifestation_isbn"
MANIFESTATION_ISBN_VERIFICATION_REQUIRED_REASON = "manifestation_verification_required"

_OUTCOME_REASON_SETS: dict[ProposalOutcome, frozenset[tuple[str, ...]]] = {
    ProposalOutcome.PROPOSE_CHANGE: frozenset(
        {
            ("unique_safe_cover",),
            (EVIDENCE_LED_TITLE_REASON,),
            ("unique_typed_candidate",),
            ("explicit_author_corroborates_untyped_creator",),
            ("local_attested_subtitle",),
            (VERIFIED_MANIFESTATION_ISBN_REASON,),
            *((reason,) for reason in PROVIDER_CONSENSUS_TITLE_REASONS),
            *((reason,) for reason in DECORATION_TIDY_TITLE_REASONS),
            (PROVIDER_CONSENSUS_CONTRIBUTOR_DISPLAY_REASON,),
        }
    ),
    ProposalOutcome.ACCEPTABLE_CHOICE: frozenset({("field_policy_acceptable_choice",)}),
    ProposalOutcome.UNRESOLVED_CHOICE: frozenset(
        {
            ("competing_evidence",),
            ("isbn_title_material_conflict",),
            (MANIFESTATION_ISBN_VERIFICATION_REQUIRED_REASON,),
            (
                "competing_evidence",
                "provider_consensus_title_series_unpreserved",
            ),
            ("competing_evidence", "provider_consensus_title_colon_unused"),
            ("competing_evidence", DECORATION_TIDY_SERIES_UNPRESERVED_REASON),
            ("competing_evidence", DECORATION_EQUIVALENCE_PROVIDER_DISSENT_REASON),
            ("competing_evidence", DECORATION_EQUIVALENCE_DECORATED_CONSENSUS_REASON),
            ("competing_evidence", SUBTITLE_SEGMENT_UNCORROBORATED_REASON),
            ("competing_evidence", SUBTITLE_SEGMENT_PROVIDER_DISSENT_REASON),
            ("competing_evidence", SUBTITLE_SEGMENT_DECORATED_CONSENSUS_REASON),
            ("date_refinement_requires_corroboration", "unique_typed_candidate"),
            ("date_year_conflict", "unique_typed_candidate"),
            ("insufficient_independent_corroboration",),
            ("field_policy_conflict",),
            ("no_safe_bibliographic_combination",),
            ("current_cover_verified", "owner_cover_choice_required"),
            ("cover_gallery_incomplete", "owner_cover_choice_required"),
            (
                "current_cover_unavailable",
                "current_cover_unverified",
                "owner_cover_choice_required",
            ),
            ("populated_subjective_field",),
            ("work_publication_cannot_replace",),
        }
    ),
    ProposalOutcome.KEEP: frozenset(
        {
            ("current_cover_verified",),
            ("field_policy_preservation",),
            ("positive_preservation_evidence",),
            *((reason,) for reason in DECORATION_EQUIVALENT_TITLE_REASONS),
        }
    ),
    ProposalOutcome.REJECT: frozenset({("policy_reject",)}),
    ProposalOutcome.DEFER: frozenset(
        {
            ("cover_gallery_incomplete",),
            ("current_cover_unverified",),
            ("identifier_applicability_unresolved",),
            ("no_positive_preservation_evidence",),
        }
    ),
    ProposalOutcome.INELIGIBLE: frozenset(
        {
            ("generic_provider_subtitle",),
            ("identifier_validity_invalid",),
            ("no_supported_evidence", SUBTITLE_SEGMENT_UNCORROBORATED_REASON),
            ("no_supported_evidence",),
            ("policy_ignored",),
            ("audiobook_cover_withheld",),
            ("redundant_publication_context",),
            ("target_state_unusable",),
        }
    ),
}

_POLICY_DESTINATIONS: dict[str, frozenset[TargetDestination]] = {
    "title_layout": frozenset({TargetDestination.TITLE, TargetDestination.SUBTITLE}),
    "date_precision": frozenset({TargetDestination.PUBLISHED_DATE}),
    "language_rendering": frozenset({TargetDestination.LANGUAGE}),
    "contributor_names": frozenset(
        {TargetDestination.AUTHORS, TargetDestination.NARRATORS}
    ),
    "publisher_preference": frozenset({TargetDestination.PUBLISHER}),
    "subtitle": frozenset({TargetDestination.SUBTITLE}),
    "series": frozenset({TargetDestination.SERIES}),
    "genres": frozenset({TargetDestination.GENRES}),
    "tags": frozenset({TargetDestination.TAGS}),
    "description_fill": frozenset({TargetDestination.DESCRIPTION}),
    "isbn_form": frozenset({TargetDestination.ISBN}),
    "cover_order": frozenset({TargetDestination.COVER}),
    "asin": frozenset({TargetDestination.ASIN}),
    "explicit": frozenset({TargetDestination.EXPLICIT}),
}


@dataclass(frozen=True, slots=True)
class ProposalValue:
    kind: ProposalValueKind
    value: str | bool | tuple[str, ...] | tuple[TargetSeries, ...]

    def __post_init__(self) -> None:
        if type(self.kind) is not ProposalValueKind:
            raise ProposalValueError("proposal value is invalid")
        if self.kind in {ProposalValueKind.TEXT, ProposalValueKind.COVER}:
            if type(self.value) is not str:
                raise ProposalValueError("proposal value is invalid")
            if self.kind is ProposalValueKind.COVER:
                if re.fullmatch(r"^cover_[0-9a-f]{64}$", self.value) is None:
                    raise ProposalValueError("proposal cover value is invalid")
            else:
                _projection(self.value, 16_384)
        elif self.kind is ProposalValueKind.BOOLEAN:
            if type(self.value) is not bool:
                raise ProposalValueError("proposal boolean value is invalid")
        elif self.kind is ProposalValueKind.LIST:
            if (
                type(self.value) is not tuple
                or not self.value
                or len(self.value) > 32
                or any(type(item) is not str for item in self.value)
            ):
                raise ProposalValueError("proposal list value is invalid")
            for item in self.value:
                _projection(item, 512)
        elif (
            type(self.value) is not tuple
            or not self.value
            or len(self.value) > 16
            or any(type(item) is not TargetSeries for item in self.value)
        ):
            raise ProposalValueError("proposal series value is invalid")

    def to_data(self) -> dict[str, object]:
        value: object = self.value
        if self.kind is ProposalValueKind.SERIES:
            value = [item.to_data() for item in self.value]  # type: ignore[union-attr]
        elif type(value) is tuple:
            value = list(value)
        return {"kind": self.kind.value, "value": value}


@dataclass(frozen=True, slots=True)
class ProposalAlternative:
    alternative_id: str
    value: ProposalValue
    assessment: ProposalAssessment
    evidence_references: tuple[str, ...]
    correlations: tuple[str, ...]
    scope: ProposalScope
    date_event: DateEvent | None = None
    date_precision: DatePrecision | None = None

    def __post_init__(self) -> None:
        if (
            _ALTERNATIVE_ID.fullmatch(self.alternative_id) is None
            or type(self.value) is not ProposalValue
            or type(self.assessment) is not ProposalAssessment
            or type(self.scope) is not ProposalScope
            or type(self.evidence_references) is not tuple
            or not self.evidence_references
            or self.evidence_references != tuple(sorted(set(self.evidence_references)))
            or any(
                _EVIDENCE_REFERENCE.fullmatch(value) is None
                for value in self.evidence_references
            )
            or type(self.correlations) is not tuple
            or not self.correlations
            or self.correlations != tuple(sorted(set(self.correlations)))
            or any(_CORRELATION.fullmatch(value) is None for value in self.correlations)
            or (self.date_event is None) != (self.date_precision is None)
            or (
                self.date_event is not None
                and (
                    type(self.date_event) is not DateEvent
                    or type(self.date_precision) is not DatePrecision
                )
            )
        ):
            raise ProposalValueError("proposal alternative is invalid")
        if self.alternative_id != _alternative_id(
            self.value,
            self.assessment,
            self.evidence_references,
            self.correlations,
            self.scope,
            self.date_event,
            self.date_precision,
        ):
            raise ProposalValueError("proposal alternative identity is inconsistent")

    @classmethod
    def create(
        cls,
        value: ProposalValue,
        assessment: ProposalAssessment,
        evidence_references: tuple[str, ...],
        correlations: tuple[str, ...],
        scope: ProposalScope,
        date_event: DateEvent | None = None,
        date_precision: DatePrecision | None = None,
    ) -> Self:
        references = tuple(sorted(set(evidence_references)))
        grouped = tuple(sorted(set(correlations)))
        return cls(
            _alternative_id(
                value,
                assessment,
                references,
                grouped,
                scope,
                date_event,
                date_precision,
            ),
            value,
            assessment,
            references,
            grouped,
            scope,
            date_event,
            date_precision,
        )

    def to_data(self) -> dict[str, object]:
        return {
            "alternative_id": self.alternative_id,
            "assessment": self.assessment.value,
            "correlations": list(self.correlations),
            "evidence_references": list(self.evidence_references),
            "scope": self.scope.value,
            "value": self.value.to_data(),
            "date_event": (None if self.date_event is None else self.date_event.value),
            "date_precision": (
                None if self.date_precision is None else self.date_precision.value
            ),
        }


@dataclass(frozen=True, slots=True)
class BibliographicSelection:
    destination: TargetDestination
    kind: BibliographicSelectionKind
    alternative_id: str | None
    target_field_fingerprint: str | None

    def __post_init__(self) -> None:
        if (
            self.destination not in _BIBLIOGRAPHIC_DESTINATIONS
            or type(self.kind) is not BibliographicSelectionKind
            or (
                self.kind is BibliographicSelectionKind.CANDIDATE
                and (
                    type(self.alternative_id) is not str
                    or _ALTERNATIVE_ID.fullmatch(self.alternative_id) is None
                    or self.target_field_fingerprint is not None
                )
            )
            or (
                self.kind is BibliographicSelectionKind.PRESERVE_TARGET
                and (
                    self.alternative_id is not None
                    or type(self.target_field_fingerprint) is not str
                    or _DIGEST.fullmatch(self.target_field_fingerprint) is None
                )
            )
        ):
            raise ProposalValueError("bibliographic selection is invalid")

    @classmethod
    def candidate(
        cls,
        destination: TargetDestination,
        alternative_id: str,
    ) -> Self:
        return cls(
            destination,
            BibliographicSelectionKind.CANDIDATE,
            alternative_id,
            None,
        )

    @classmethod
    def preserve(
        cls,
        target: TargetField,
    ) -> Self:
        return cls(
            target.destination,
            BibliographicSelectionKind.PRESERVE_TARGET,
            None,
            _target_field_fingerprint(target),
        )

    def to_data(self) -> dict[str, object]:
        return {
            "alternative_id": self.alternative_id,
            "destination": self.destination.value,
            "kind": self.kind.value,
            "target_field_fingerprint": self.target_field_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class BibliographicCombination:
    combination_id: str
    selections: tuple[BibliographicSelection, ...]
    source_representations: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            _COMBINATION_ID.fullmatch(self.combination_id) is None
            or type(self.selections) is not tuple
            or tuple(value.destination for value in self.selections)
            != _BIBLIOGRAPHIC_DESTINATIONS
            or any(
                type(value) is not BibliographicSelection for value in self.selections
            )
            or type(self.source_representations) is not tuple
            or not self.source_representations
            or self.source_representations
            != tuple(sorted(set(self.source_representations)))
            or any(
                _REPRESENTATION_ID.fullmatch(value) is None
                for value in self.source_representations
            )
        ):
            raise ProposalValueError("bibliographic combination is invalid")
        if self.combination_id != _bibliographic_combination_id(
            self.selections,
            self.source_representations,
        ):
            raise ProposalValueError(
                "bibliographic combination identity is inconsistent"
            )

    @classmethod
    def create(
        cls,
        selections: tuple[BibliographicSelection, ...],
        source_representations: tuple[str, ...],
    ) -> Self:
        sources = tuple(sorted(set(source_representations)))
        return cls(
            _bibliographic_combination_id(selections, sources),
            selections,
            sources,
        )

    @property
    def member_key(self) -> tuple[tuple[str, str, str | None, str | None], ...]:
        return tuple(
            (
                value.destination.value,
                value.kind.value,
                value.alternative_id,
                value.target_field_fingerprint,
            )
            for value in self.selections
        )

    def to_data(self) -> dict[str, object]:
        return {
            "combination_id": self.combination_id,
            "selections": [value.to_data() for value in self.selections],
            "source_representations": list(self.source_representations),
        }


@dataclass(frozen=True, slots=True)
class ProposalResult:
    destination: TargetDestination
    outcome: ProposalOutcome
    alternatives: tuple[ProposalAlternative, ...]
    selected_alternative_id: str | None
    reasons: tuple[str, ...]
    dependencies: tuple[TargetDestination, ...] = ()

    def __post_init__(self) -> None:
        order = {value: index for index, value in enumerate(TargetDestination)}
        if (
            type(self.destination) is not TargetDestination
            or type(self.outcome) is not ProposalOutcome
            or type(self.alternatives) is not tuple
            or len(self.alternatives) > 256
            or any(
                type(value) is not ProposalAlternative for value in self.alternatives
            )
            or self.alternatives
            != tuple(sorted(self.alternatives, key=lambda value: value.alternative_id))
            or len({value.alternative_id for value in self.alternatives})
            != len(self.alternatives)
            or type(self.reasons) is not tuple
            or not self.reasons
            or self.reasons != tuple(sorted(set(self.reasons)))
            or any(_REASON.fullmatch(value) is None for value in self.reasons)
            or type(self.dependencies) is not tuple
            or self.destination in self.dependencies
            or self.dependencies
            != tuple(sorted(set(self.dependencies), key=order.__getitem__))
        ):
            raise ProposalValueError("proposal result is invalid")
        if any(
            (alternative.value.kind, alternative.scope)
            not in _DESTINATION_VALUE_SCOPES[self.destination]
            for alternative in self.alternatives
        ):
            raise ProposalValueError(
                "proposal alternative is incompatible with destination"
            )
        if self.destination is TargetDestination.LANGUAGE and len(
            {
                (
                    _observation_key(self.destination, value.value),
                    value.scope,
                    value.date_event,
                    value.date_precision,
                )
                for value in self.alternatives
            }
        ) != len(self.alternatives):
            raise ProposalValueError(
                "language alternatives are not equivalence-class grouped"
            )
        expected_dates = _DESTINATION_DATE_SEMANTICS[self.destination]
        if any(
            (alternative.date_event, alternative.date_precision) not in expected_dates
            for alternative in self.alternatives
        ):
            raise ProposalValueError(
                "proposal alternative date is incompatible with destination"
            )
        for alternative in self.alternatives:
            if alternative.date_event is None:
                continue
            if (
                alternative.value.kind is not ProposalValueKind.TEXT
                or type(alternative.value.value) is not str
            ):
                raise ProposalValueError(
                    "proposal alternative date is incompatible with destination"
                )
            try:
                normalized, precision = normalize_date_value(alternative.value.value)
            except ValueError as error:
                raise ProposalValueError(
                    "proposal alternative date is incompatible with destination"
                ) from error
            if (
                normalized != alternative.value.value
                or precision is not alternative.date_precision
            ):
                raise ProposalValueError(
                    "proposal alternative date is incompatible with destination"
                )
        if self.reasons not in _OUTCOME_REASON_SETS[self.outcome]:
            raise ProposalValueError("proposal reasons are incompatible with outcome")
        selected = {value.alternative_id for value in self.alternatives}
        if self.outcome is ProposalOutcome.PROPOSE_CHANGE:
            selected_alternative = next(
                (
                    value
                    for value in self.alternatives
                    if value.alternative_id == self.selected_alternative_id
                ),
                None,
            )
            if (
                (
                    len(self.alternatives) != 1
                    and not (
                        (
                            self.destination is TargetDestination.ISBN
                            and self.reasons == (VERIFIED_MANIFESTATION_ISBN_REASON,)
                        )
                        or (
                            self.destination is TargetDestination.TITLE
                            and self.reasons == (EVIDENCE_LED_TITLE_REASON,)
                        )
                    )
                )
                or self.selected_alternative_id is None
                or self.selected_alternative_id not in selected
                or selected_alternative is None
                or selected_alternative.assessment
                is ProposalAssessment.PRESERVES_TARGET
                or (
                    self.destination is TargetDestination.LANGUAGE
                    and selected_alternative.assessment
                    is ProposalAssessment.REPLACES_TARGET
                )
            ):
                raise ProposalValueError("proposal selection is inconsistent")
        elif self.outcome is ProposalOutcome.ACCEPTABLE_CHOICE:
            if (
                self.destination
                not in {
                    TargetDestination.LANGUAGE,
                    TargetDestination.PUBLISHER,
                }
                or len(self.alternatives) < 2
                or self.selected_alternative_id is not None
                or any(
                    value.assessment is ProposalAssessment.PRESERVES_TARGET
                    for value in self.alternatives
                )
                or (
                    self.destination is TargetDestination.LANGUAGE
                    and any(
                        value.assessment is ProposalAssessment.REPLACES_TARGET
                        for value in self.alternatives
                    )
                )
                or not _field_policy_accepts_all(self.destination, self.alternatives)
            ):
                raise ProposalValueError("proposal selection is inconsistent")
        elif self.outcome is ProposalOutcome.UNRESOLVED_CHOICE:
            if (
                not self.alternatives
                or self.selected_alternative_id is not None
                or (
                    self.reasons == ("field_policy_conflict",)
                    and (
                        self.destination
                        not in {
                            TargetDestination.LANGUAGE,
                            TargetDestination.PUBLISHER,
                        }
                        or not _field_policy_conflicts(
                            self.destination, self.alternatives
                        )
                    )
                )
            ):
                raise ProposalValueError("proposal selection is inconsistent")
        elif self.outcome is ProposalOutcome.KEEP:
            if (
                not self.alternatives
                or not any(
                    value.assessment is ProposalAssessment.PRESERVES_TARGET
                    for value in self.alternatives
                )
                or self.selected_alternative_id is not None
                or (
                    self.destination is TargetDestination.LANGUAGE
                    and self.reasons != ("field_policy_preservation",)
                )
                or (
                    self.reasons == ("field_policy_preservation",)
                    and (
                        self.destination
                        not in {
                            TargetDestination.LANGUAGE,
                            TargetDestination.PUBLISHER,
                        }
                        or not _field_policy_accepts_all(
                            self.destination, self.alternatives
                        )
                        or (
                            self.destination is TargetDestination.LANGUAGE
                            and any(
                                value.assessment
                                is not ProposalAssessment.PRESERVES_TARGET
                                for value in self.alternatives
                            )
                        )
                    )
                )
                or (
                    self.reasons != ("field_policy_preservation",)
                    and (
                        len(self.alternatives) != 1
                        or self.alternatives[0].assessment
                        is not ProposalAssessment.PRESERVES_TARGET
                    )
                )
            ):
                raise ProposalValueError("proposal selection is inconsistent")
        elif self.alternatives or self.selected_alternative_id is not None:
            raise ProposalValueError("proposal selection is inconsistent")

    def to_data(self) -> dict[str, object]:
        return {
            "alternatives": [value.to_data() for value in self.alternatives],
            "dependencies": [value.value for value in self.dependencies],
            "destination": self.destination.value,
            "outcome": self.outcome.value,
            "reasons": list(self.reasons),
            "selected_alternative_id": self.selected_alternative_id,
        }


@dataclass(frozen=True, slots=True)
class ProposalSet:
    proposal_set_id: str
    report_id: str
    target_fingerprint: str
    results: tuple[ProposalResult, ...]
    resolved_policy_id: str
    bibliographic_combinations: tuple[BibliographicCombination, ...] = ()
    media_kind: str = "ebook"
    schema_version: str = PROPOSAL_SCHEMA
    policy_version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        if (
            _PROPOSAL_SET_ID.fullmatch(self.proposal_set_id) is None
            or _REPORT_ID.fullmatch(self.report_id) is None
            or _DIGEST.fullmatch(self.target_fingerprint) is None
            or self.media_kind not in MEDIA_KINDS
            or self.schema_version != PROPOSAL_SCHEMA
            or self.policy_version != POLICY_VERSION
            or type(self.resolved_policy_id) is not str
            or re.fullmatch(r"resolved_policy_[0-9a-f]{64}", self.resolved_policy_id)
            is None
            or type(self.results) is not tuple
            or tuple(value.destination for value in self.results)
            != tuple(TargetDestination)
            or type(self.bibliographic_combinations) is not tuple
            or len(self.bibliographic_combinations) > MAX_BIBLIOGRAPHIC_COMBINATIONS
            or any(
                type(value) is not BibliographicCombination
                for value in self.bibliographic_combinations
            )
            or self.bibliographic_combinations
            != tuple(
                sorted(
                    self.bibliographic_combinations,
                    key=lambda value: value.combination_id,
                )
            )
            or len({value.member_key for value in self.bibliographic_combinations})
            != len(self.bibliographic_combinations)
        ):
            raise ProposalValueError("proposal set is invalid")
        alternatives = {
            result.destination: {
                alternative.alternative_id for alternative in result.alternatives
            }
            for result in self.results
        }
        if any(
            selection.kind is BibliographicSelectionKind.CANDIDATE
            and selection.alternative_id not in alternatives[selection.destination]
            for combination in self.bibliographic_combinations
            for selection in combination.selections
        ):
            raise ProposalValueError("bibliographic combination candidate is invalid")
        results = {value.destination: value for value in self.results}
        generic_subtitle_preservation = (
            results[TargetDestination.SUBTITLE].outcome is ProposalOutcome.INELIGIBLE
            and results[TargetDestination.SUBTITLE].reasons
            == ("generic_provider_subtitle",)
            and results[TargetDestination.TITLE].outcome is ProposalOutcome.KEEP
            and any(
                value.assessment is ProposalAssessment.PRESERVES_TARGET
                for value in results[TargetDestination.TITLE].alternatives
            )
        )
        if any(
            not any(
                selection.kind is BibliographicSelectionKind.CANDIDATE
                for selection in combination.selections
            )
            and not generic_subtitle_preservation
            for combination in self.bibliographic_combinations
        ):
            raise ProposalValueError("bibliographic combination is invalid")
        expected = (
            "proposal_set_"
            + hashlib.sha256(
                canonical_json(self._identity_data()).encode("utf-8")
            ).hexdigest()
        )
        if self.proposal_set_id != expected:
            raise ProposalValueError("proposal set identity is inconsistent")
        if len(self.to_json().encode("utf-8")) > MAX_PROPOSAL_BYTES:
            raise ProposalValueError("proposal set exceeds its byte limit")

    @classmethod
    def create(
        cls,
        *,
        report_id: str,
        target_fingerprint: str,
        results: tuple[ProposalResult, ...],
        resolved_policy_id: str,
        bibliographic_combinations: tuple[BibliographicCombination, ...] = (),
        media_kind: str = "ebook",
    ) -> Self:
        combinations = tuple(
            sorted(
                bibliographic_combinations,
                key=lambda value: value.combination_id,
            )
        )
        identity = (
            "proposal_set_"
            + hashlib.sha256(
                canonical_json(
                    _proposal_data(
                        report_id,
                        target_fingerprint,
                        results,
                        resolved_policy_id,
                        combinations,
                        media_kind,
                    )
                ).encode("utf-8")
            ).hexdigest()
        )
        return cls(
            identity,
            report_id,
            target_fingerprint,
            results,
            resolved_policy_id,
            combinations,
            media_kind,
        )

    def _identity_data(self) -> dict[str, object]:
        return self.to_data()

    def to_data(self) -> dict[str, object]:
        return _proposal_data(
            self.report_id,
            self.target_fingerprint,
            self.results,
            self.resolved_policy_id,
            self.bibliographic_combinations,
            self.media_kind,
        )

    def to_json(self) -> str:
        return canonical_json(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> ProposalSet:
        try:
            size = len(serialized.encode("utf-8"))
        except (AttributeError, UnicodeError) as error:
            raise ProposalValueError("proposal document is invalid") from error
        if type(serialized) is not str or not serialized or size > MAX_PROPOSAL_BYTES:
            raise ProposalValueError("proposal document is invalid")
        try:
            raw = json.loads(
                serialized,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_number,
            )
            value = _proposal_from_data(raw)
        except ProposalValueError:
            raise
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
            json.JSONDecodeError,
        ) as error:
            raise ProposalValueError("proposal document is invalid") from error
        if value.to_json() != serialized:
            raise ProposalValueError("proposal document is not canonical")
        return value


@dataclass(frozen=True, slots=True)
class _Observed:
    value: ProposalValue
    references: tuple[str, ...]
    correlation: str
    scope: ProposalScope
    representation: str
    date_event: DateEvent | None = None
    date_precision: DatePrecision | None = None
    corroborated_untyped_creator: bool = False


def _ignored_policy_targets(
    destinations: Mapping[str, str] | None,
) -> frozenset[TargetDestination]:
    if destinations is None:
        return frozenset()
    if (
        type(destinations) is not dict
        or set(destinations) != set(OPTION_CATALOGUE)
        or any(
            type(value) is not str
            or value not in {"ask", "ignore", "automatic", "unsupported"}
            for value in destinations.values()
        )
    ):
        raise ProposalValueError("proposal policy destinations are invalid")
    return frozenset(
        destination
        for destination in TargetDestination
        if (
            owners := tuple(
                field
                for field, values in _POLICY_DESTINATIONS.items()
                if destination in values
            )
        )
        and all(destinations[field] == "ignore" for field in owners)
    )


def _apply_policy_destination(
    result: ProposalResult, ignored: frozenset[TargetDestination]
) -> ProposalResult:
    if result.destination not in ignored:
        return result
    return ProposalResult(
        result.destination,
        ProposalOutcome.INELIGIBLE,
        (),
        None,
        ("policy_ignored",),
        result.dependencies,
    )


def canonical_target_fields(
    fields: tuple[TargetField, ...],
) -> dict[TargetDestination, TargetField]:
    """Project the one canonical publication current without changing its source.

    ``publishedYear`` remains a retained compatibility field in the report, but
    the review contract has one publication fact. Consumers use this view for
    current-value comparison while report fingerprints still bind the exact
    provider snapshot.
    """
    targets = {value.destination: value for value in fields}
    date_target = targets[TargetDestination.PUBLISHED_DATE]
    year_target = targets[TargetDestination.PUBLISHED_YEAR]
    if (
        date_target.state is not TargetFieldState.VALUE
        and year_target.state is TargetFieldState.VALUE
        and type(year_target.value) is str
    ):
        targets[TargetDestination.PUBLISHED_DATE] = TargetField(
            TargetDestination.PUBLISHED_DATE,
            TargetFieldState.VALUE,
            year_target.value,
        )
    return targets


def evaluate_report(
    report: MetadataReport,
    resolved_policy_id: str | None = None,
    policy_destinations: Mapping[str, str] | None = None,
) -> ProposalSet:
    """Evaluate one exact report without I/O, configuration, or provider calls."""
    if type(report) is not MetadataReport:
        raise ProposalValueError("proposal report is invalid")
    ignored = _ignored_policy_targets(policy_destinations)
    targets = canonical_target_fields(report.target.fields)
    results: dict[TargetDestination, ProposalResult] = {}
    observed_by_destination: dict[TargetDestination, tuple[_Observed, ...]] = {}
    title_associations: dict[str, str] = {}
    for destination in TargetDestination:
        if destination in {
            TargetDestination.PUBLISHED_YEAR,
            TargetDestination.PUBLISHED_DATE,
            TargetDestination.COVER,
        }:
            continue
        observations = _observations(report, destination)
        if destination in _BIBLIOGRAPHIC_DESTINATIONS:
            observed_by_destination[destination] = observations
        result = _evaluate(
            destination,
            targets[destination],
            observations,
            report,
            _dependencies(destination),
        )
        if destination is TargetDestination.SERIES:
            result = _resolve_series_subset(
                result, targets[destination], observations, report
            )
        if (
            destination is TargetDestination.ISBN
            and not observations
            and result.outcome is ProposalOutcome.INELIGIBLE
            and result.reasons == ("no_supported_evidence",)
            and (reason := _isbn_rejection_reason(report.identifier_evidence))
            is not None
        ):
            result = ProposalResult(
                destination,
                (
                    ProposalOutcome.DEFER
                    if reason == "identifier_applicability_unresolved"
                    else ProposalOutcome.INELIGIBLE
                ),
                (),
                None,
                (reason,),
                _dependencies(destination),
            )
        if (
            destination is TargetDestination.SUBTITLE
            and not observations
            and result.outcome is ProposalOutcome.INELIGIBLE
            and result.reasons == ("no_supported_evidence",)
            and _has_generic_provider_subtitle(report)
        ):
            result = ProposalResult(
                destination,
                ProposalOutcome.INELIGIBLE,
                (),
                None,
                ("generic_provider_subtitle",),
                _dependencies(destination),
            )
        if (
            destination is TargetDestination.SUBTITLE
            and result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
            and result.reasons == ("competing_evidence",)
            and targets[destination].state
            in {
                TargetFieldState.MISSING,
                TargetFieldState.NULL,
                TargetFieldState.EMPTY,
            }
            and (elected := _segment_corroborated_subtitle(report, result.alternatives))
            != (None, None)
        ):
            attested, elected_reason = elected
            result = (
                ProposalResult(
                    destination,
                    ProposalOutcome.PROPOSE_CHANGE,
                    (attested,),
                    attested.alternative_id,
                    (cast(str, elected_reason),),
                    _dependencies(destination),
                )
                if attested is not None
                # A withheld corroboration states its own guard on the same
                # seam the uncorroborated diagnostic uses.
                else _unresolved(result, cast(str, elected_reason))
            )
        elif (
            destination is TargetDestination.SUBTITLE
            and (
                result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
                and result.reasons == ("competing_evidence",)
                or result.outcome is ProposalOutcome.INELIGIBLE
                and result.reasons == ("no_supported_evidence",)
            )
            and targets[destination].state
            in {
                TargetFieldState.MISSING,
                TargetFieldState.NULL,
                TargetFieldState.EMPTY,
            }
            and _has_uncorroborated_subtitle_segment(report, result.alternatives)
        ):
            result = ProposalResult(
                destination,
                result.outcome,
                result.alternatives,
                None,
                tuple(
                    sorted({*result.reasons, SUBTITLE_SEGMENT_UNCORROBORATED_REASON})
                ),
                result.dependencies,
            )
        if (
            destination is TargetDestination.ISBN
            and targets[destination].state
            in {
                TargetFieldState.MISSING,
                TargetFieldState.NULL,
                TargetFieldState.EMPTY,
            }
            and result.alternatives
        ):
            result = _verified_manifestation_isbn_election(report, result)
        if destination is TargetDestination.TITLE:
            result = _evidence_led_title_election(report, result)
        if (
            destination is TargetDestination.TITLE
            and result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
            and result.reasons == ("competing_evidence",)
        ):
            result = _provider_consensus_title_election(report, targets, result)
        if (
            destination is TargetDestination.TITLE
            and result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
            and result.reasons == ("competing_evidence",)
        ):
            result, title_associations = _decoration_equivalence_election(
                report, targets, result, observations
            )
        if destination in {TargetDestination.AUTHORS, TargetDestination.NARRATORS} and (
            (
                result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
                and result.reasons == ("competing_evidence",)
            )
            or result.outcome is ProposalOutcome.KEEP
        ):
            result = _provider_consensus_contributor_display_election(
                report, targets, destination, result
            )
        if destination is TargetDestination.NARRATORS and result.outcome in {
            ProposalOutcome.PROPOSE_CHANGE,
            ProposalOutcome.ACCEPTABLE_CHOICE,
        }:
            result = _narrator_corroboration_guard(report, result)
        results[destination] = _apply_policy_destination(result, ignored)
    publication = _publication_results(report, targets)
    results.update(
        (destination, _apply_policy_destination(result, ignored))
        for destination, result in publication.items()
    )
    results[TargetDestination.COVER] = _apply_policy_destination(
        _cover_result(report, targets[TargetDestination.COVER]), ignored
    )
    combinations, unresolved = _bibliographic_combinations(
        targets,
        results,
        observed_by_destination,
        title_associations,
    )
    for destination in unresolved:
        result = results[destination]
        if result.alternatives and result.reasons != (EVIDENCE_LED_TITLE_REASON,):
            results[destination] = _bibliographic_unresolved(result)
    ordered = tuple(results[value] for value in TargetDestination)
    if resolved_policy_id is None:
        # The production default is a real resolved built-in profile for this
        # exact report library.  Higher layers pass an explicit resolved id.
        from mechanical_librarian.domain.metadata_policy import (
            MetadataPolicyProfile,
            resolve_policy,
        )

        resolved_policy_id = resolve_policy(
            MetadataPolicyProfile.builtin(), report.target.library
        ).resolved_id
    return ProposalSet.create(
        report_id=report.report_id,
        target_fingerprint=report.target.fingerprint,
        results=ordered,
        resolved_policy_id=resolved_policy_id,
        bibliographic_combinations=combinations,
        media_kind=(
            "audiobook" if report.file.format in AUDIO_FILE_FORMATS else "ebook"
        ),
    )


def _observations(
    report: MetadataReport,
    destination: TargetDestination,
) -> tuple[_Observed, ...]:
    if destination is TargetDestination.EXPLICIT:
        return ()
    if destination in {
        TargetDestination.AUTHORS,
        TargetDestination.NARRATORS,
        TargetDestination.GENRES,
        TargetDestination.TAGS,
    }:
        return _list_observations(report, destination)
    if destination is TargetDestination.SERIES:
        return _series_observations(report)
    mapping = {
        TargetDestination.TITLE: (ClaimKind.TITLE, ClaimScope.WORK),
        TargetDestination.PUBLISHER: (ClaimKind.PUBLISHER, ClaimScope.EDITION),
        TargetDestination.DESCRIPTION: (ClaimKind.DESCRIPTION, ClaimScope.WORK),
        TargetDestination.ISBN: (ClaimKind.ISBN, ClaimScope.EDITION),
        TargetDestination.ASIN: (ClaimKind.ASIN, ClaimScope.EDITION),
        TargetDestination.LANGUAGE: (ClaimKind.LANGUAGE, ClaimScope.EDITION),
    }
    kind, scope = mapping.get(destination, (ClaimKind.SUBTITLE, ClaimScope.WORK))
    kinds = (
        {ClaimKind.PUBLISHER, ClaimKind.IMPRINT}
        if destination is TargetDestination.PUBLISHER
        else {kind}
    )
    local_scope = (
        ClaimScope.WORK if destination is TargetDestination.SUBTITLE else scope
    )
    provider_scope = (
        ClaimScope.EDITION if destination is TargetDestination.SUBTITLE else scope
    )
    values: list[_Observed] = []
    claim: LocalClaim | ProviderClaim
    for claim in report.local_claims:
        if (
            claim.kind in kinds
            and claim.scope is local_scope
            and claim.state is FieldState.VALID
            and claim.canonical is not None
            and claim.basis != "target_metadata"
            and _eligible_identifier(destination, claim.canonical, claim.applicability)
            and _eligible_identifier_reference(report, destination, claim.reference)
            and _eligible_description(destination, claim.canonical)
            and _eligible_field_value(destination, claim.canonical)
            and (
                destination is not TargetDestination.TITLE
                or _local_title_is_unambiguous(report, claim)
            )
        ):
            values.append(
                _Observed(
                    _value(destination, claim.canonical),
                    (claim.reference,),
                    _local_correlation(report),
                    _scope(claim.scope),
                    _local_representation(claim.basis),
                )
            )
    for provider in report.providers:
        for candidate in provider.candidates:
            for claim in candidate.claims:
                if (
                    claim.kind in kinds
                    and claim.scope is provider_scope
                    and claim.state is FieldState.VALID
                    and claim.value is not None
                    and _eligible_identifier(
                        destination, claim.value, claim.applicability
                    )
                    and _eligible_identifier_reference(
                        report, destination, claim.reference
                    )
                    and _eligible_provider_manifestation(
                        destination, claim, provider, candidate
                    )
                    and _eligible_provider_description(
                        destination, claim.value, candidate
                    )
                    and not _generic_provider_subtitle(report, destination, claim.value)
                    and _eligible_field_value(destination, claim.value)
                    and (
                        destination is not TargetDestination.TITLE
                        or _provider_title_is_unambiguous(
                            candidate,
                            claim,
                        )
                    )
                    and (
                        destination is not TargetDestination.PUBLISHER
                        or manifestation_publication_compatible(
                            candidate,
                            report.local_claims,
                        )
                        and _accepted_provider_manifestation_anchor(
                            report,
                            provider.provider,
                            candidate,
                        )
                    )
                ):
                    values.append(
                        _Observed(
                            _value(destination, claim.value),
                            (claim.reference,),
                            _provider_correlation(provider),
                            _scope(claim.scope),
                            _provider_representation(provider, candidate),
                        )
                    )
    return tuple(values)


def _generic_provider_subtitle(
    report: MetadataReport, destination: TargetDestination, value: str
) -> bool:
    return (
        destination is TargetDestination.SUBTITLE
        and next(
            field.state
            in {
                TargetFieldState.MISSING,
                TargetFieldState.NULL,
                TargetFieldState.EMPTY,
            }
            for field in report.target.fields
            if field.destination is destination
        )
        and _comparison(destination, value) in _GENERIC_PROVIDER_SUBTITLES
    )


@dataclass(frozen=True, slots=True)
class SubtitleSegmentEvidence:
    """One colon segment stripped from a WORK-scope title claim.

    A segment is never an observation of its own: it inherits the source
    correlation and the WORK scope of the claim it was stripped from and can
    only corroborate a subtitle alternative that a different source states.
    """

    reference: str
    correlation: str
    value: str
    clean_title: str
    scope: ProposalScope = ProposalScope.WORK


def subtitle_segment_evidence(
    report: MetadataReport, value: str | None = None
) -> tuple[SubtitleSegmentEvidence, ...]:
    """Return every colon segment of a WORK title claim, local or provider."""
    sought = None if value is None else normalize_title(value).casefold()
    segments: list[SubtitleSegmentEvidence] = []
    sources: list[tuple[str, str, str | None]] = [
        (
            claim.reference,
            _local_correlation(report),
            claim.canonical or claim.observed,
        )
        for claim in report.local_claims
        if claim.kind is ClaimKind.TITLE
        and claim.scope is ClaimScope.WORK
        and claim.state is FieldState.VALID
    ]
    sources.extend(
        (claim.reference, _provider_correlation(provider), claim.value)
        for provider in report.providers
        for candidate in provider.candidates
        for claim in candidate.claims
        if claim.kind is ClaimKind.TITLE
        and claim.scope is ClaimScope.WORK
        and claim.state is FieldState.VALID
    )
    for reference, correlation, observed in sources:
        if type(observed) is not str or not observed:
            continue
        for match in work_title_election_matches(observed, ()):
            if (
                match.family is not WorkTitleTransformationFamily.SUBTITLE_SEGMENT
                or match.preserved_component is None
            ):
                continue
            component = match.preserved_component
            if sought is not None and normalize_title(component).casefold() != sought:
                continue
            segments.append(
                SubtitleSegmentEvidence(
                    reference, correlation, component, match.clean_title
                )
            )
    return tuple(segments)


@dataclass(frozen=True, slots=True)
class SegmentCorroboration:
    """The cross-source segments that attest a subtitle, or why they cannot."""

    segments: tuple[SubtitleSegmentEvidence, ...] = ()
    withheld_reason: str | None = None


def segment_corroboration(
    report: MetadataReport,
    value: str,
    candidate_correlations: tuple[str, ...],
) -> SegmentCorroboration:
    """Decide whether stripped title segments corroborate one subtitle value.

    Both the election and the automatic projection call this, so the two
    surfaces cannot drift. Corroboration needs a segment from a source other
    than the one stating the subtitle, no provider stating a different
    subtitle, and at least as many provider authorities on the plain forms the
    segments were stripped from as on the decorated forms carrying the segment.
    """
    segments = subtitle_segment_evidence(report, value)
    if not segments:
        return SegmentCorroboration()
    cross = tuple(
        segment
        for segment in segments
        if segment.correlation not in candidate_correlations
    )
    if not cross:
        # A segment from the candidate's own source is not corroboration.
        return SegmentCorroboration()
    sought = _comparison(TargetDestination.SUBTITLE, value)
    if any(
        _comparison(TargetDestination.SUBTITLE, claim.value) != sought
        for provider in report.providers
        for candidate in provider.candidates
        for claim in candidate.claims
        if claim.kind is ClaimKind.SUBTITLE
        and claim.state is FieldState.VALID
        and claim.value is not None
    ):
        return SegmentCorroboration(
            withheld_reason=SUBTITLE_SEGMENT_PROVIDER_DISSENT_REASON
        )
    local_correlation = _local_correlation(report)
    decorated = frozenset(
        segment.correlation
        for segment in segments
        if segment.correlation != local_correlation
    )
    plain_forms = frozenset(
        _comparison(TargetDestination.TITLE, segment.clean_title)
        for segment in segments
        if segment.correlation != local_correlation
    )
    authorities = _provider_work_title_authorities(report)
    plain = frozenset(
        authority
        for form in plain_forms
        for authority in authorities.get(form, frozenset())
    )
    local = (
        1
        if any(segment.correlation == local_correlation for segment in segments)
        else 0
    )
    if not decorated or len(decorated) + local > len(plain):
        # The segment reads as integral title text: no provider carries it, or
        # the form carrying it is stated at least as widely as the form without.
        return SegmentCorroboration(
            withheld_reason=SUBTITLE_SEGMENT_DECORATED_CONSENSUS_REASON
        )
    return SegmentCorroboration(segments=cross)


def _segment_corroborated_subtitle(
    report: MetadataReport, alternatives: tuple[ProposalAlternative, ...]
) -> tuple[ProposalAlternative | None, str | None]:
    """Elect the one competing subtitle the book's own metadata attests.

    Provider catalogues disagree on how a combined title splits; the packaged
    subtitle claim records the printed form, so among competing candidates the
    one it matches exactly is the safe election. Zero or multiple attested
    values keep the choice with the owner, as does a colon segment: on this seam
    it only names the corroboration guard that closed the choice.
    """
    attested = {
        normalize_title(observed).casefold()
        for claim in report.local_claims
        if claim.kind is ClaimKind.SUBTITLE
        and claim.state is FieldState.VALID
        and type(observed := claim.canonical or claim.observed) is str
        and observed
    }
    attested.update(
        normalize_title(segment.value).casefold()
        for segment in subtitle_segment_evidence(report)
    )
    if not attested:
        return None, None
    matching = tuple(
        alternative
        for alternative in alternatives
        if alternative.value.kind is ProposalValueKind.TEXT
        and type(alternative.value.value) is str
        and normalize_title(alternative.value.value).casefold() in attested
    )
    distinct = {
        normalize_title(cast(str, alternative.value.value)).casefold()
        for alternative in matching
    }
    if len(distinct) != 1:
        return None, None
    # Elect only a provider-backed candidate: subtitle observations built from
    # the book's own packaged claims carry work scope, and the local claim is
    # the corroboration, never the elected provider value itself.
    for alternative in matching:
        if alternative.scope is not ProposalScope.EDITION:
            continue
        if not any(
            reference.startswith("provider_")
            for reference in alternative.evidence_references
        ):
            continue
        value = cast(str, alternative.value.value)
        if _explicit_subtitle_references(report, value):
            return alternative, "local_attested_subtitle"
        # A segment never elects a competing subtitle: every distinct competing
        # value is a provider subtitle claim, which the dissent guard closes.
        # Corroboration runs only to report the guard that closed it; the fill
        # itself belongs to the automatic projection.
        corroboration = segment_corroboration(report, value, alternative.correlations)
        if corroboration.withheld_reason is not None:
            return None, corroboration.withheld_reason
    return None, None


def _explicit_subtitle_references(report: MetadataReport, value: str) -> frozenset[str]:
    """Return local subtitle claims that independently attest one value."""
    sought = normalize_title(value).casefold()
    return frozenset(
        claim.reference
        for claim in report.local_claims
        if claim.kind is ClaimKind.SUBTITLE
        and claim.state is FieldState.VALID
        and type(observed := claim.canonical or claim.observed) is str
        and observed
        and normalize_title(observed).casefold() == sought
    )


def _has_uncorroborated_subtitle_segment(
    report: MetadataReport, alternatives: tuple[ProposalAlternative, ...]
) -> bool:
    """Keep a title suffix visible when no independent subtitle uses it."""
    segments = subtitle_segment_evidence(report)
    if not segments:
        return False
    return not any(
        alternative.value.kind is ProposalValueKind.TEXT
        and type(alternative.value.value) is str
        and alternative.scope is ProposalScope.EDITION
        and all(
            reference.startswith("provider_")
            for reference in alternative.evidence_references
        )
        and any(
            segment.correlation not in alternative.correlations
            for segment in subtitle_segment_evidence(report, alternative.value.value)
        )
        for alternative in alternatives
    )


def _evidence_led_title_election(
    report: MetadataReport, result: ProposalResult
) -> ProposalResult:
    """Test the ISBN-linked title boundary independently of companion elections."""
    if report.file.format in AUDIO_FILE_FORMATS or not result.alternatives:
        return result
    local_authors = {
        creator_comparison_key(claim.canonical)
        for claim in report.local_claims
        if claim.kind is ClaimKind.CREATOR
        and claim.role == "author"
        and claim.state is FieldState.VALID
        and claim.canonical
    }
    if not local_authors:
        return result
    supported: set[str] = set()
    isbn_bound = False
    conflict = False
    titles: set[str] = set()
    variant_claims: dict[str, set[frozenset[str]]] = {}
    for provider in report.providers:
        works: set[str] = set()
        for candidate in provider.candidates:
            claims = tuple(c for c in candidate.claims if c.state is FieldState.VALID)
            candidate_variants: dict[str, set[str]] = {}
            for claim in claims:
                if type(claim.value) is not str:
                    continue
                if claim.kind is ClaimKind.LANGUAGE:
                    language = language_identity(claim.value)
                    if language is not None:
                        candidate_variants.setdefault("language", set()).add(
                            language.base
                        )
                elif claim.kind in {
                    ClaimKind.CREATOR,
                    ClaimKind.CONTRIBUTOR,
                } and claim.role in {"translator", "adapter", "abridger"}:
                    candidate_variants.setdefault(claim.role, set()).add(
                        creator_comparison_key(claim.value)
                    )
            for role, identities in candidate_variants.items():
                variant_claims.setdefault(role, set()).add(frozenset(identities))
            titles.update(
                c.value
                for c in claims
                if c.kind is ClaimKind.TITLE and type(c.value) is str
            )
            authors = {
                creator_comparison_key(c.value)
                for c in claims
                if c.kind is ClaimKind.CREATOR
                and c.role == "author"
                and type(c.value) is str
            }
            # Missing evidence does not dissent, but cannot support election.
            if authors and authors != local_authors:
                conflict = True
            if candidate.work_identity != "unresolved":
                works.add(candidate.work_identity)
            for lane in provider.lanes:
                if (
                    lane.kind is not ProviderQueryKind.ISBN
                    or not _target_backed_lookup_contains(
                        report, provider, candidate, required_lane=lane
                    )
                ):
                    continue
                if not any(
                    c.state is FieldState.VALID
                    and c.checksum in {"valid_isbn_10", "valid_isbn_13"}
                    and c.basis in {"package_metadata", "opening_matter"}
                    and c.applicability in {"ebook", "print", "unknown"}
                    for c in _isbn_family_claims(report, lane.matched_family)
                ):
                    continue
                if not any(
                    c.kind is ClaimKind.ISBN
                    and type(c.value) is str
                    and isbn_family(c.value) == lane.matched_family
                    for c in claims
                ):
                    continue
                isbn_bound = True
                if (
                    provider.authority is None
                    or authors != local_authors
                    or candidate.work_identity == "unresolved"
                ):
                    continue
                supported.update(
                    c.reference
                    for c in claims
                    if c.kind is ClaimKind.TITLE and c.scope is ClaimScope.WORK
                )
        conflict = conflict or len(works) > 1
    if not isbn_bound:
        return result
    conflict = (
        conflict
        or len(titles) > 1
        or any(len(values) > 1 for values in variant_claims.values())
    )
    title = next(iter(titles)) if len(titles) == 1 else ""
    # Content title disagreement is material; package and target strings are
    # correctable observations rather than work-identity vetoes.
    conflict = conflict or any(
        c.kind is ClaimKind.TITLE
        and c.state is FieldState.VALID
        and c.basis == "opening_matter"
        and c.canonical
        and normalize_title(c.canonical).casefold() != normalize_title(title).casefold()
        for c in report.local_claims
    )
    if conflict:
        return ProposalResult(
            result.destination,
            ProposalOutcome.UNRESOLVED_CHOICE,
            result.alternatives,
            None,
            ("isbn_title_material_conflict",),
            result.dependencies,
        )
    if not supported or not title:
        return result
    selected = tuple(
        a
        for a in result.alternatives
        if a.value.value == title
        and supported.intersection(a.evidence_references)
        and a.assessment is not ProposalAssessment.PRESERVES_TARGET
    )
    if len(selected) != 1:
        return result
    return ProposalResult(
        TargetDestination.TITLE,
        ProposalOutcome.PROPOSE_CHANGE,
        result.alternatives,
        selected[0].alternative_id,
        (EVIDENCE_LED_TITLE_REASON,),
        result.dependencies,
    )


def _provider_consensus_title_election(
    report: MetadataReport,
    targets: Mapping[TargetDestination, TargetField],
    result: ProposalResult,
) -> ProposalResult:
    """Elect one provider-only clean title from one reversible local decoration."""
    target = targets[TargetDestination.TITLE]
    if target.state is not TargetFieldState.VALUE or type(target.value) is not str:
        return result
    local_titles = {
        claim.canonical
        for claim in report.local_claims
        if claim.kind is ClaimKind.TITLE
        and claim.scope is ClaimScope.WORK
        and claim.state is FieldState.VALID
        and claim.canonical is not None
        and claim.basis != "target_metadata"
    }
    if len(local_titles) != 1:
        return result
    local_title = next(iter(local_titles))
    if target.value != local_title:
        return result
    matches = work_title_election_matches(local_title, ())
    if len(matches) != 1:
        return result
    match = matches[0]
    provider_title_claims = tuple(
        (provider, claim.value)
        for provider in report.providers
        for candidate in provider.candidates
        for claim in candidate.claims
        if claim.kind is ClaimKind.TITLE
        and claim.state is FieldState.VALID
        and claim.value is not None
    )
    provider_titles = tuple(
        (provider, claim.value)
        for provider in report.providers
        if provider.authority is not None
        for candidate in provider.candidates
        for claim in candidate.claims
        if claim.kind is ClaimKind.TITLE
        and claim.scope is ClaimScope.WORK
        and claim.state is FieldState.VALID
        and claim.value is not None
    )
    if (
        len(provider_titles) < 2
        or any(value != match.clean_title for _, value in provider_title_claims)
        or len({_provider_correlation(provider) for provider, _ in provider_titles}) < 2
    ):
        return result
    # Abstain only where this election would otherwise have fired: a report
    # whose providers do not agree on the clean form is not this election's,
    # and its decoration is decided by the equivalence class instead.
    if (
        match.family is WorkTitleTransformationFamily.SUBTITLE_SEGMENT
        and not _subtitle_component_is_preserved(
            match.preserved_component, report, targets
        )
    ):
        return _unresolved(result, "provider_consensus_title_colon_unused")
    if (
        match.family is WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME
        and not _series_component_is_preserved(
            match.preserved_component, report, targets
        )
    ):
        return _unresolved(result, "provider_consensus_title_series_unpreserved")
    candidates = tuple(
        alternative
        for alternative in result.alternatives
        if alternative.value.kind is ProposalValueKind.TEXT
        and alternative.value.value == match.clean_title
        and alternative.scope is ProposalScope.WORK
        and alternative.assessment is ProposalAssessment.REPLACES_TARGET
        and all(
            reference.startswith("provider_")
            for reference in alternative.evidence_references
        )
        and len(alternative.correlations) >= 2
    )
    if len(candidates) != 1:
        return result
    candidate = candidates[0]
    return ProposalResult(
        TargetDestination.TITLE,
        ProposalOutcome.PROPOSE_CHANGE,
        (candidate,),
        candidate.alternative_id,
        (f"provider_consensus_title_{match.family.value}",),
        _dependencies(TargetDestination.TITLE),
    )


def _decoration_equivalence_election(
    report: MetadataReport,
    targets: Mapping[TargetDestination, TargetField],
    result: ProposalResult,
    observations: tuple[_Observed, ...],
) -> tuple[ProposalResult, dict[str, str]]:
    """Collapse two provider-backed titles equal up to one closed decoration.

    Both forms are attested by providers, so neither is local noise; the class
    has one canonical member, the plain form. The current value decides the
    direction: holding the plain form is a keep with no writes, holding the
    decorated form is the tidy tranche. Anything the closed grammar does not
    match in exactly one step stays the owner's decision.
    """
    target = targets[TargetDestination.TITLE]
    if target.state is not TargetFieldState.VALUE or type(target.value) is not str:
        return result, {}
    if len(result.alternatives) != 2:
        # A class is exactly one plain and one decorated provider-backed form.
        return result, {}
    if any(
        alternative.value.kind is not ProposalValueKind.TEXT
        or type(alternative.value.value) is not str
        or alternative.scope is not ProposalScope.WORK
        or not any(
            reference.startswith("provider_")
            for reference in alternative.evidence_references
        )
        for alternative in result.alternatives
    ):
        return result, {}
    preserving = tuple(
        alternative
        for alternative in result.alternatives
        if alternative.assessment is ProposalAssessment.PRESERVES_TARGET
    )
    if len(preserving) != 1:
        return result, {}
    if target.value != preserving[0].value.value:
        # Preservation folds case and whitespace; the class does not. A current
        # value that is not literally a class member is neither kept as
        # equivalent nor tidied, since the write would recase the owner's title.
        return result, {}
    pair = _decoration_equivalence_pair(result.alternatives)
    if pair is None:
        return result, {}
    plain, decorated, match = pair
    if (
        match.family is WorkTitleTransformationFamily.PARENTHESIZED_SERIES_NAME
        and decorated.assessment is ProposalAssessment.PRESERVES_TARGET
        and not _series_component_is_preserved(
            match.preserved_component, report, targets
        )
    ):
        return _unresolved(result, DECORATION_TIDY_SERIES_UNPRESERVED_REASON), {}
    plain_key = _comparison(TargetDestination.TITLE, cast(str, plain.value.value))
    decorated_key = _comparison(
        TargetDestination.TITLE, cast(str, decorated.value.value)
    )
    # Width and dissent both read the raw provider claims, not the admitted
    # alternatives: a decorated form stated by a candidate WORK admission
    # rejected still attests that form, and counting an alternative's
    # correlations would miss it.
    authorities = _provider_work_title_authorities(report)
    if set(authorities) - {plain_key, decorated_key}:
        return _unresolved(result, DECORATION_EQUIVALENCE_PROVIDER_DISSENT_REASON), {}
    if len(authorities.get(plain_key, frozenset())) < len(
        authorities.get(decorated_key, frozenset())
    ):
        return _unresolved(
            result, DECORATION_EQUIVALENCE_DECORATED_CONSENSUS_REASON
        ), {}
    # The election proves tuple membership only, not additional title evidence.
    associations = {
        observation.representation: plain.alternative_id
        for observation in observations
        if observation.scope is decorated.scope
        and _observation_key(TargetDestination.TITLE, observation.value)
        == _observation_key(TargetDestination.TITLE, decorated.value)
    }
    if plain.assessment is ProposalAssessment.PRESERVES_TARGET:
        return ProposalResult(
            TargetDestination.TITLE,
            ProposalOutcome.KEEP,
            (plain,),
            None,
            (f"decoration_equivalent_{match.family.value}",),
            _dependencies(TargetDestination.TITLE),
        ), associations
    return ProposalResult(
        TargetDestination.TITLE,
        ProposalOutcome.PROPOSE_CHANGE,
        (plain,),
        plain.alternative_id,
        (f"decoration_tidy_{match.family.value}",),
        _dependencies(TargetDestination.TITLE),
    ), associations


def _decoration_equivalence_pair(
    alternatives: tuple[ProposalAlternative, ...],
) -> tuple[ProposalAlternative, ProposalAlternative, WorkTitleElectionMatch] | None:
    """Return (plain, decorated, match) for a one-step decoration pair."""
    pairs: list[
        tuple[ProposalAlternative, ProposalAlternative, WorkTitleElectionMatch]
    ] = []
    for decorated in alternatives:
        plain = next(value for value in alternatives if value is not decorated)
        matches = direct_work_title_election_matches(
            cast(str, decorated.value.value), ()
        )
        if len(matches) != 1:
            # Stacked decorations never collapse: the grammar must reach the
            # plain form in exactly one closed step.
            continue
        # The grammar step is exact: removing the decoration must yield the
        # plain alternative byte for byte. Folding case or whitespace here
        # would tidy a form the review grammar never adjudicated, and the
        # write would carry a recasing no reviewer approved. The dissent and
        # width guards below still count under the closed comparison.
        if matches[0].clean_title == cast(str, plain.value.value):
            pairs.append((plain, decorated, matches[0]))
    return pairs[0] if len(pairs) == 1 else None


def _provider_work_title_authorities(
    report: MetadataReport,
) -> dict[str, frozenset[str]]:
    """Map every valid provider WORK title claim to the authorities stating it.

    Every candidate counts, admitted or not: a title claim is an attestation of
    that form regardless of whether WORK admission kept its candidate.
    """
    values: dict[str, set[str]] = {}
    for provider in report.providers:
        for candidate in provider.candidates:
            for claim in candidate.claims:
                if (
                    claim.kind is ClaimKind.TITLE
                    and claim.scope is ClaimScope.WORK
                    and claim.state is FieldState.VALID
                    and claim.value is not None
                ):
                    values.setdefault(
                        _comparison(TargetDestination.TITLE, claim.value), set()
                    ).add(_provider_correlation(provider))
    return {key: frozenset(value) for key, value in values.items()}


def _provider_consensus_contributor_display_election(
    report: MetadataReport,
    targets: Mapping[TargetDestination, TargetField],
    destination: TargetDestination,
    result: ProposalResult,
) -> ProposalResult:
    """Elect an exact provider display only from existing contributor agreement."""
    target = targets[destination]
    if (
        target.state is not TargetFieldState.VALUE
        or type(target.value) is not tuple
        or any(type(value) is not str for value in target.value)
    ):
        return result
    observations = _list_observations(report, destination)
    local_displays = _local_contributor_displays(report, destination, observations)
    if len(local_displays) != 1:
        return result
    local_claims = local_displays[0]
    local_value = tuple(cast("str", claim.canonical) for claim in local_claims)
    if target.value != local_value:
        return result
    provider_displays = _provider_contributor_displays(
        report, destination, observations
    )
    authority_displays = tuple(
        display for display in provider_displays if display[0].authority is not None
    )
    if (
        len(authority_displays) < 2
        or len({value for _, _, value in provider_displays}) != 1
        or len(
            {_provider_correlation(provider) for provider, _, _ in authority_displays}
        )
        < 2
    ):
        return result
    provider_value = authority_displays[0][2]
    if provider_value == local_value:
        return result
    if not all(
        _provider_contributor_claims_agree(provider_claims, local_claims)
        for _, provider_claims, _ in authority_displays
    ):
        return result
    elected = ProposalAlternative.create(
        _value(destination, provider_value),
        ProposalAssessment.REPLACES_TARGET,
        (
            *(
                claim.reference
                for _, claims, _ in provider_displays
                for claim in claims
            ),
            *(claim.reference for claim in local_claims),
        ),
        (
            *(_provider_correlation(provider) for provider, _, _ in provider_displays),
            _local_correlation(report),
        ),
        _contributor_scope(destination),
    )
    return ProposalResult(
        destination,
        ProposalOutcome.PROPOSE_CHANGE,
        (elected,),
        elected.alternative_id,
        (PROVIDER_CONSENSUS_CONTRIBUTOR_DISPLAY_REASON,),
        _dependencies(destination),
    )


def _narrator_corroboration_guard(
    report: MetadataReport, result: ProposalResult
) -> ProposalResult:
    """Hold a narrator cast that only one source ever stated.

    Who read a recording is the fact an audiobook item is most often wrong
    about, and one provider naming a cast the file itself never mentions is a
    single source rather than agreement. The owner still sees the alternative
    and can accept it; the proposal only stops calling it settled (#966).
    """
    if any(
        claim.kind is ClaimKind.CONTRIBUTOR
        and claim.role == "narrator"
        and claim.state is FieldState.VALID
        for claim in report.local_claims
    ) or any(len(value.correlations) > 1 for value in result.alternatives):
        return result
    return ProposalResult(
        result.destination,
        ProposalOutcome.UNRESOLVED_CHOICE,
        result.alternatives,
        None,
        ("insufficient_independent_corroboration",),
        result.dependencies,
    )


def _contributor_scope(destination: TargetDestination) -> ProposalScope:
    return (
        ProposalScope.WORK
        if destination is TargetDestination.AUTHORS
        else ProposalScope.EDITION
    )


def _contributor_claim_shape(
    destination: TargetDestination,
) -> tuple[ClaimKind, ClaimScope, str]:
    if destination is TargetDestination.AUTHORS:
        return ClaimKind.CREATOR, ClaimScope.WORK, "author"
    # Every source states a narrator as a WORK-scope contributor, because that
    # is the scope `claim_scope` gives ClaimKind.CONTRIBUTOR, and the runtime
    # corroboration band retains exactly those claims as statements about this
    # recording. Reading EDITION here observed nothing at all (#966).
    return ClaimKind.CONTRIBUTOR, ClaimScope.WORK, "narrator"


def _local_contributor_displays(
    report: MetadataReport,
    destination: TargetDestination,
    observations: tuple[_Observed, ...],
) -> tuple[tuple[LocalClaim, ...], ...]:
    kind, scope, role = _contributor_claim_shape(destination)
    displays: list[tuple[LocalClaim, ...]] = []
    for basis in sorted(
        {
            claim.basis
            for claim in report.local_claims
            if claim.basis != "target_metadata"
        }
    ):
        claims = tuple(
            claim
            for claim in report.local_claims
            if claim.basis == basis
            and claim.kind is kind
            and claim.scope is scope
            and claim.role == role
        )
        if not claims or any(
            claim.state is not FieldState.VALID or claim.canonical is None
            for claim in claims
        ):
            continue
        value = _value(
            destination, tuple(cast("str", claim.canonical) for claim in claims)
        )
        if any(
            observed.representation == _local_representation(basis)
            and observed.value == value
            and observed.references == tuple(claim.reference for claim in claims)
            for observed in observations
        ):
            displays.append(claims)
    return tuple(displays)


def _provider_contributor_displays(
    report: MetadataReport,
    destination: TargetDestination,
    observations: tuple[_Observed, ...],
) -> tuple[tuple[ProviderObservation, tuple[ProviderClaim, ...], tuple[str, ...]], ...]:
    kind, scope, role = _contributor_claim_shape(destination)
    displays: list[
        tuple[ProviderObservation, tuple[ProviderClaim, ...], tuple[str, ...]]
    ] = []
    for provider in report.providers:
        for candidate in provider.candidates:
            claims = tuple(
                claim
                for claim in candidate.claims
                if claim.kind is kind and claim.scope is scope and claim.role == role
            )
            if not claims or any(
                claim.state is not FieldState.VALID or claim.value is None
                for claim in claims
            ):
                continue
            value = tuple(cast("str", claim.value) for claim in claims)
            proposal_value = _value(destination, value)
            if any(
                observed.representation == _provider_representation(provider, candidate)
                and observed.value == proposal_value
                and observed.references == tuple(claim.reference for claim in claims)
                for observed in observations
            ):
                displays.append((provider, claims, value))
    return tuple(displays)


def _provider_contributor_claims_agree(
    provider_claims: tuple[ProviderClaim, ...],
    local_claims: tuple[LocalClaim, ...],
) -> bool:
    return len(provider_claims) == len(local_claims) and all(
        any(
            link.local_reference == local.reference
            and link.outcome is Comparison.AGREEMENT
            for link in provider.links
        )
        for provider, local in zip(provider_claims, local_claims, strict=True)
    )


def _series_component_is_preserved(
    component: str | None,
    report: MetadataReport,
    targets: Mapping[TargetDestination, TargetField],
) -> bool:
    if component is None:
        return False
    target = targets[TargetDestination.SERIES]
    if target.state is TargetFieldState.VALUE and type(target.value) is tuple:
        if any(
            type(value) is TargetSeries
            and normalize_title(value.name).casefold() == component.casefold()
            for value in target.value
        ):
            return True
    return any(
        observed.value.kind is ProposalValueKind.SERIES
        and any(
            normalize_title(value.name).casefold() == component.casefold()
            for value in cast("tuple[TargetSeries, ...]", observed.value.value)
        )
        for observed in _series_observations(report)
    )


def _subtitle_component_is_preserved(
    component: str | None,
    report: MetadataReport,
    targets: Mapping[TargetDestination, TargetField],
) -> bool:
    if component is None:
        return False
    target = targets[TargetDestination.SUBTITLE]
    if target.state is TargetFieldState.VALUE and target.value == component:
        return True
    alternatives = _alternatives(
        TargetDestination.SUBTITLE,
        target,
        _observations(report, TargetDestination.SUBTITLE),
        report,
    )
    return any(
        alternative.value.kind is ProposalValueKind.TEXT
        and alternative.value.value == component
        and alternative.scope is ProposalScope.EDITION
        and all(
            reference.startswith("provider_")
            for reference in alternative.evidence_references
        )
        for alternative in alternatives
    )


def _has_generic_provider_subtitle(report: MetadataReport) -> bool:
    return any(
        claim.kind is ClaimKind.SUBTITLE
        and claim.scope is ClaimScope.EDITION
        and claim.state is FieldState.VALID
        and claim.value is not None
        and claim.applicability == "ebook"
        and _generic_provider_subtitle(report, TargetDestination.SUBTITLE, claim.value)
        for provider in report.providers
        for candidate in provider.candidates
        for claim in candidate.claims
    )


def _list_observations(
    report: MetadataReport, destination: TargetDestination
) -> tuple[_Observed, ...]:
    if destination is TargetDestination.AUTHORS:
        kind, role, scope = ClaimKind.CREATOR, "author", ClaimScope.WORK
    elif destination is TargetDestination.NARRATORS:
        kind, role, scope = ClaimKind.CONTRIBUTOR, "narrator", ClaimScope.WORK
    elif destination is TargetDestination.GENRES:
        kind, role, scope = ClaimKind.GENRE, None, ClaimScope.WORK
    else:
        kind, role, scope = ClaimKind.TAG, None, ClaimScope.WORK
    # A narrator claim is stated at WORK scope and read as EDITION evidence:
    # who read this recording is a fact about the recording, and the allowed
    # narrator value shape says so (#966).
    observed_scope = (
        ProposalScope.EDITION
        if destination is TargetDestination.NARRATORS
        else _scope(scope)
    )
    values: list[_Observed] = []
    members: tuple[LocalClaim, ...] | tuple[ProviderClaim, ...]
    claims: tuple[LocalClaim, ...] | tuple[ProviderClaim, ...]
    bases = tuple(
        sorted(
            {
                claim.basis
                for claim in report.local_claims
                if claim.basis != "target_metadata"
            }
        )
    )
    for basis in bases:
        basis_claims = tuple(
            claim for claim in report.local_claims if claim.basis == basis
        )
        if destination is TargetDestination.AUTHORS and any(
            claim.scope is ClaimScope.WORK
            and (
                (
                    claim.kind is ClaimKind.CREATOR
                    and claim.state is FieldState.VALID
                    and claim.role != "author"
                )
                or (
                    claim.kind is ClaimKind.CONTRIBUTOR
                    and claim.role in {None, "author", "pseudonym", "collective"}
                )
            )
            for claim in basis_claims
        ):
            continue
        if destination is TargetDestination.NARRATORS and any(
            claim.kind is ClaimKind.CONTRIBUTOR
            and claim.state is FieldState.VALID
            and claim.role is None
            for claim in basis_claims
        ):
            continue
        members = tuple(
            claim
            for claim in basis_claims
            if claim.kind is kind
            and claim.scope is scope
            and claim.role == role
            and not (
                destination is TargetDestination.AUTHORS
                and _controlled_creator_placeholder(claim)
            )
        )
        if any(
            claim.state is not FieldState.VALID or claim.canonical is None
            for claim in members
        ):
            continue
        claims = tuple(
            claim
            for claim in members
            if claim.state is FieldState.VALID and claim.canonical is not None
        )
        # Every member of `claims` was filtered on `claim.canonical is not None`
        # just above, so each canonical value here is a string.
        if destination is TargetDestination.AUTHORS and len(
            {creator_comparison_key(cast("str", claim.canonical)) for claim in claims}
        ) != len(claims):
            continue
        if claims:
            values.append(
                _Observed(
                    _value(
                        destination,
                        tuple(cast("str", claim.canonical) for claim in claims),
                    ),
                    tuple(claim.reference for claim in claims),
                    _local_correlation(report),
                    observed_scope,
                    _local_representation(basis),
                )
            )
    for provider in report.providers:
        for candidate in provider.candidates:
            if destination is TargetDestination.AUTHORS and _mixed_author_creator_roles(
                candidate
            ):
                continue
            if destination is TargetDestination.AUTHORS and any(
                claim.kind is ClaimKind.CONTRIBUTOR
                and claim.scope is ClaimScope.WORK
                and claim.role in {None, "author", "pseudonym", "collective"}
                for claim in candidate.claims
            ):
                continue
            if destination is TargetDestination.NARRATORS and any(
                claim.kind is ClaimKind.CONTRIBUTOR
                and claim.state is FieldState.VALID
                and claim.role is None
                for claim in candidate.claims
            ):
                continue
            members = tuple(
                claim
                for claim in candidate.claims
                if claim.kind is kind and claim.scope is scope and claim.role == role
            )
            if any(
                claim.state is not FieldState.VALID or claim.value is None
                for claim in members
            ):
                continue
            claims = tuple(
                claim
                for claim in members
                if claim.state is FieldState.VALID and claim.value is not None
            )
            if (
                destination is TargetDestination.AUTHORS
                and not _provider_author_bindings_allow_observation(candidate, claims)
            ):
                continue
            # Every member of `claims` was filtered on `claim.value is not None`
            # just above, so each provider value here is a string.
            if destination is TargetDestination.AUTHORS and len(
                {creator_comparison_key(cast("str", claim.value)) for claim in claims}
            ) != len(claims):
                continue
            if claims:
                values.append(
                    _Observed(
                        _value(
                            destination,
                            tuple(cast("str", claim.value) for claim in claims),
                        ),
                        tuple(claim.reference for claim in claims),
                        _provider_correlation(provider),
                        observed_scope,
                        _provider_representation(provider, candidate),
                    )
                )
    if destination is TargetDestination.AUTHORS:
        values.extend(_corroborated_untyped_author_observations(report))
    return tuple(values)


def _corroborated_untyped_author_observations(
    report: MetadataReport,
) -> tuple[_Observed, ...]:
    """Pair only one complete neutral creator set with a distinct author set."""
    authors = tuple(
        (provider, candidate, claims, bindings)
        for provider in report.providers
        for candidate in provider.candidates
        if _target_backed_lookup_contains(report, provider, candidate)
        if (set_value := _complete_provider_creator_set(candidate, "author"))
        is not None
        for claims, bindings in (set_value,)
    )
    untyped = tuple(
        (provider, candidate, claims, bindings)
        for provider in report.providers
        for candidate in provider.candidates
        if _target_backed_lookup_contains(report, provider, candidate)
        if (set_value := _complete_provider_creator_set(candidate, None)) is not None
        for claims, bindings in (set_value,)
        if bindings is not None
    )
    observations: list[_Observed] = []
    for author_provider, author_candidate, author_claims, author_bindings in authors:
        for (
            neutral_provider,
            neutral_candidate,
            neutral_claims,
            neutral_bindings,
        ) in untyped:
            if (
                author_provider.authority is None
                or neutral_provider.authority is None
                or author_provider.authority == neutral_provider.authority
                or author_provider.provider == neutral_provider.provider
            ):
                continue
            if not _creator_sets_match(
                author_claims,
                author_bindings,
                neutral_claims,
                neutral_bindings,
            ):
                continue
            observations.append(
                _Observed(
                    _value(
                        TargetDestination.AUTHORS,
                        tuple(
                            claim.value
                            for claim in author_claims
                            if claim.value is not None
                        ),
                    ),
                    tuple(
                        claim.reference for claim in (*author_claims, *neutral_claims)
                    ),
                    _correlation(
                        "creator-corroboration:"
                        + ":".join(
                            sorted(
                                (author_provider.authority, neutral_provider.authority)
                            )
                        )
                    ),
                    ProposalScope.WORK,
                    _representation(
                        canonical_json(
                            {
                                "author": _provider_representation(
                                    author_provider, author_candidate
                                ),
                                "untyped": _provider_representation(
                                    neutral_provider, neutral_candidate
                                ),
                            }
                        )
                    ),
                    corroborated_untyped_creator=True,
                )
            )
    return tuple(observations)


def _complete_provider_creator_set(
    candidate: ProviderCandidate,
    role: str | None,
) -> (
    tuple[tuple[ProviderClaim, ...], tuple[ProviderIdentityBinding, ...] | None] | None
):
    creators = tuple(
        claim
        for claim in candidate.claims
        if claim.kind is ClaimKind.CREATOR and claim.scope is ClaimScope.WORK
    )
    if (
        not creators
        or any(
            claim.role != role
            or claim.state is not FieldState.VALID
            or claim.value is None
            for claim in creators
        )
        or any(
            claim.kind is ClaimKind.CONTRIBUTOR and claim.scope is ClaimScope.WORK
            for claim in candidate.claims
        )
        or len({creator_comparison_key(claim.value or "") for claim in creators})
        != len(creators)
    ):
        return None
    bindings = complete_creator_identity_bindings(candidate, creators)
    if (
        any(
            binding.scope is ClaimScope.WORK and binding.role == role
            for binding in candidate.identity_bindings
        )
        and bindings is None
    ):
        return None
    if bindings is not None and len({binding.identity for binding in bindings}) != len(
        bindings
    ):
        return None
    return creators, bindings


def _mixed_author_creator_roles(candidate: ProviderCandidate) -> bool:
    """Never project an author subset from a mixed WORK creator collection."""
    roles = {
        claim.role
        for claim in candidate.claims
        if claim.scope is ClaimScope.WORK and claim.kind is ClaimKind.CREATOR
    }
    return "author" in roles and len(roles) > 1


def _target_backed_lookup_contains(
    report: MetadataReport,
    provider: ProviderObservation,
    candidate: ProviderCandidate,
    *,
    required_lane: ProviderLaneObservation | None = None,
) -> bool:
    """Require a successful candidate lane backed by the exact report target."""
    local_claims = {claim.reference: claim for claim in report.local_claims}
    targets = {field.destination: field for field in report.target.fields}
    title = targets[TargetDestination.TITLE]
    authors = targets[TargetDestination.AUTHORS]
    expected_title = (
        normalize_title(title.value)
        if title.state is TargetFieldState.VALUE and type(title.value) is str
        else None
    )
    # The `all(type(value) is str ...)` guard below establishes that this AUTHORS
    # field holds a tuple of strings rather than a tuple of TargetSeries.
    expected_authors = (
        tuple(
            normalize_title(value) for value in cast("tuple[str, ...]", authors.value)
        )
        if authors.state is TargetFieldState.VALUE
        and type(authors.value) is tuple
        and authors.value
        and all(type(value) is str for value in authors.value)
        and len(
            {
                normalize_title(value).casefold()
                for value in cast("tuple[str, ...]", authors.value)
            }
        )
        == len(authors.value)
        else ()
    )
    target_title_references = tuple(
        sorted(
            claim.reference
            for claim in report.local_claims
            if claim.kind is ClaimKind.TITLE and claim.basis == "target_metadata"
        )
    )
    target_titles_are_exact = (
        all(
            (claim := local_claims.get(reference)) is not None
            and claim.kind is ClaimKind.TITLE
            and claim.basis == "target_metadata"
            and claim.state is FieldState.VALID
            and type(claim.canonical) is str
            and normalize_title(claim.canonical).casefold() == expected_title.casefold()
            for reference in target_title_references
        )
        if expected_title is not None
        else False
    )
    return any(
        lane.status is ProviderStatus.CANDIDATES
        and any(
            member.edition_identity == candidate.identity
            and member.work_identity == candidate.work_identity
            for member in lane.candidates
        )
        and (
            (
                lane.kind is ProviderQueryKind.ISBN
                and lane.input_references
                == tuple(
                    sorted(
                        claim.reference
                        for claim in _isbn_family_claims(
                            report,
                            lane.matched_family,
                        )
                    )
                )
                and lane.identity
                == _discovery_lane_identity(
                    {
                        # `_isbn_family_claims` only yields claims whose
                        # canonical value is not None.
                        "canonical_isbns": sorted(
                            cast(
                                "set[str]",
                                {
                                    claim.canonical
                                    for claim in _isbn_family_claims(
                                        report,
                                        lane.matched_family,
                                    )
                                },
                            )
                        ),
                        "input_references": list(lane.input_references),
                        "kind": ProviderQueryKind.ISBN.value,
                        "matched_family": lane.matched_family,
                    }
                )
            )
            or (
                lane.kind is ProviderQueryKind.WORK
                and expected_title is not None
                and expected_authors
                and lane.input_references == target_title_references
                and target_titles_are_exact
                and _candidate_set_replays_work_qualification(
                    provider,
                    lane,
                    candidate,
                    expected_title,
                    expected_authors,
                )
                and lane.identity
                == _discovery_lane_identity(
                    {
                        "authors": list(expected_authors),
                        "input_references": list(target_title_references),
                        "kind": ProviderQueryKind.WORK.value,
                        "title": expected_title,
                    }
                )
            )
        )
        for lane in provider.lanes
        if required_lane is None or lane == required_lane
    )


def _discovery_lane_identity(payload: dict[str, object]) -> str:
    return "lane_" + hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _isbn_family_claims(
    report: MetadataReport,
    family: str | None,
) -> tuple[LocalClaim, ...]:
    return tuple(
        claim
        for claim in report.local_claims
        if claim.kind is ClaimKind.ISBN
        and claim.canonical is not None
        and isbn_family(claim.canonical) == family
    )


def _candidate_set_replays_work_qualification(
    provider: ProviderObservation,
    lane: ProviderLaneObservation,
    candidate: ProviderCandidate,
    expected_title: str,
    expected_authors: tuple[str, ...],
) -> bool:
    """Replay one unambiguous WORK candidate set before granting authority."""
    candidates_by_identity = {value.identity: value for value in provider.candidates}
    if any(
        value.edition_identity not in candidates_by_identity
        for value in lane.candidates
    ):
        return False
    lane_candidates = tuple(
        candidates_by_identity[value.edition_identity] for value in lane.candidates
    )
    admission = provider_candidate_set_work_identity_admission(
        lane_candidates,
        expected_title,
        expected_authors,
        lane.dispatch_lineage,
    )
    return (
        admission.admitted
        and admission.candidates == lane_candidates
        and candidate in admission.candidates
        and admission.transformation == lane.transformation_lineage
    )


def provider_candidate_represents_target_work(
    report: MetadataReport,
    provider: ProviderObservation,
    candidate: ProviderCandidate,
) -> bool:
    """Prove one retained candidate describes the target work through its lane."""
    context = target_work_identity_context(report.target)
    if context is None or not _target_backed_lookup_contains(
        report, provider, candidate
    ):
        return False
    expected_title, expected_authors = context
    candidates_by_identity = {value.identity: value for value in provider.candidates}
    for lane in provider.lanes:
        if (
            lane.status is not ProviderStatus.CANDIDATES
            or not any(
                member.edition_identity == candidate.identity
                and member.work_identity == candidate.work_identity
                for member in lane.candidates
            )
            or any(
                value.edition_identity not in candidates_by_identity
                for value in lane.candidates
            )
        ):
            continue
        lane_candidates = tuple(
            candidates_by_identity[value.edition_identity] for value in lane.candidates
        )
        if lane.kind is ProviderQueryKind.ISBN:
            provider_isbn_families = tuple(
                family
                for claim in candidate.claims
                if claim.kind is ClaimKind.ISBN
                and claim.state is FieldState.VALID
                and claim.scope is ClaimScope.EDITION
                and type(claim.value) is str
                and (family := isbn_family(claim.value)) is not None
            )
            if not (
                lane.matched_family is not None
                and provider_isbn_families
                and all(
                    family == lane.matched_family for family in provider_isbn_families
                )
            ):
                continue
            admission = provider_candidate_set_exact_work_identity_admission(
                lane_candidates,
                expected_title,
                expected_authors,
            )
            lineage_matches = True
        elif lane.kind is ProviderQueryKind.WORK:
            admission = provider_candidate_set_work_identity_admission(
                lane_candidates,
                expected_title,
                expected_authors,
                lane.dispatch_lineage,
            )
            lineage_matches = admission.transformation == lane.transformation_lineage
        else:
            continue
        if (
            admission.admitted
            and admission.candidates == lane_candidates
            and candidate in admission.candidates
            and lineage_matches
        ):
            return True
    return False


def provider_candidate_represents_target_series_work(
    report: MetadataReport,
    provider: ProviderObservation,
    candidate: ProviderCandidate,
) -> bool:
    """Keep strict WORK admission first, with a series-only ISBN fallback."""
    if provider_candidate_represents_target_work(report, provider, candidate):
        return True
    targets = {field.destination: field for field in report.target.fields}
    title = targets[TargetDestination.TITLE]
    authors = targets[TargetDestination.AUTHORS]
    if (
        title.state is not TargetFieldState.VALUE
        or type(title.value) is not str
        or not title.value
        or authors.state is not TargetFieldState.VALUE
        or type(authors.value) is not tuple
        or not authors.value
        or not all(type(value) is str and value for value in authors.value)
    ):
        return False
    by_identity = {value.identity: value for value in provider.candidates}
    for lane in provider.lanes:
        if (
            lane.kind is not ProviderQueryKind.ISBN
            or lane.status is not ProviderStatus.CANDIDATES
            or lane.matched_family is None
            or not lane.input_references
            or not lane.candidates
            or any(
                member.edition_identity not in by_identity for member in lane.candidates
            )
        ):
            continue
        sources = tuple(
            by_identity[member.edition_identity] for member in lane.candidates
        )
        if (
            candidate not in sources
            or len({source.work_identity for source in sources}) != 1
            or any(
                source.work_identity in {"", "unresolved"}
                or member.work_identity != source.work_identity
                for member, source in zip(lane.candidates, sources, strict=True)
            )
        ):
            continue
        if not all(
            _target_backed_lookup_contains(report, provider, source, required_lane=lane)
            and (
                families := tuple(
                    isbn_family(claim.value)
                    for claim in source.claims
                    if claim.kind is ClaimKind.ISBN
                    and claim.scope is ClaimScope.EDITION
                    and claim.state is FieldState.VALID
                    and type(claim.value) is str
                )
            )
            and all(family == lane.matched_family for family in families)
            and provider_candidate_series_identity_matches(
                source, title.value, cast("tuple[str, ...]", authors.value)
            )
            for source in sources
        ):
            continue
        return True
    return False


def _creator_sets_match(
    authors: tuple[ProviderClaim, ...],
    author_bindings: tuple[ProviderIdentityBinding, ...] | None,
    untyped: tuple[ProviderClaim, ...],
    untyped_bindings: tuple[ProviderIdentityBinding, ...] | None,
) -> bool:
    if len(authors) != len(untyped) or untyped_bindings is None:
        return False
    names_match = tuple(
        creator_comparison_key(claim.value or "") for claim in authors
    ) == tuple(creator_comparison_key(claim.value or "") for claim in untyped)
    if author_bindings is not None:
        identities_match = tuple(
            binding.identity for binding in author_bindings
        ) == tuple(binding.identity for binding in untyped_bindings)
        if identities_match:
            return names_match
        # Provider adapters retain their opaque, provider-namespaced person
        # identifiers.  Different namespaces are not a cross-provider identity
        # registry, so the closed name comparison is the permitted fallback.
    return names_match


def _provider_author_bindings_allow_observation(
    candidate: ProviderCandidate,
    claims: tuple[ProviderClaim, ...],
) -> bool:
    bindings = complete_creator_identity_bindings(candidate, claims)
    if bindings is None:
        return True
    return len({binding.identity for binding in bindings}) == len(bindings)


def _controlled_creator_placeholder(claim: LocalClaim) -> bool:
    """Recognize the exact package-author sentinel without changing evidence."""
    return (
        claim.kind is ClaimKind.CREATOR
        and claim.scope is ClaimScope.WORK
        and claim.role == "author"
        and claim.basis == "package_metadata"
        and claim.state is FieldState.VALID
        and claim.canonical is not None
        and " ".join(unicodedata.normalize("NFKC", claim.canonical).split()).casefold()
        == "unknown"
    )


def _series_bundle_dominates(candidate: ProposalValue, other: ProposalValue) -> bool:
    """Compare source bundles without rewriting their names or positions."""
    if (
        candidate.kind is not ProposalValueKind.SERIES
        or other.kind is not ProposalValueKind.SERIES
    ):
        return False
    # ProposalValue validates the correlated TargetSeries tuple for this kind.
    candidate_memberships = cast("tuple[TargetSeries, ...]", candidate.value)
    other_memberships = cast("tuple[TargetSeries, ...]", other.value)
    memberships = {
        series_name_equivalence_key(item.name): item.sequence
        for item in candidate_memberships
    }
    if len(memberships) != len(candidate_memberships):
        return False
    for item in other_memberships:
        name = series_name_equivalence_key(item.name)
        if name not in memberships:
            return False
        position = memberships[name]
        if item.sequence is None or item.sequence == position:
            continue
        if (
            position is None
            or not all(
                re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value)
                for value in (position, item.sequence)
            )
            or Decimal(position) != Decimal(item.sequence)
        ):
            return False
    return True


def _resolve_series_subset(
    result: ProposalResult,
    target: TargetField,
    observations: tuple[_Observed, ...],
    report: MetadataReport,
) -> ProposalResult:
    """Elect only a unique existing bundle that covers every alternative."""
    if (
        result.outcome is not ProposalOutcome.UNRESOLVED_CHOICE
        or len(result.alternatives) < 2
    ):
        return result
    dominant = tuple(
        candidate
        for candidate in result.alternatives
        if all(
            _series_bundle_dominates(candidate.value, other.value)
            for other in result.alternatives
        )
    )
    if len(dominant) != 1:
        return result
    references = set(dominant[0].evidence_references)
    return _evaluate(
        TargetDestination.SERIES,
        target,
        tuple(
            observed
            for observed in observations
            if set(observed.references) <= references
        ),
        report,
        result.dependencies,
    )


def _series_observations(report: MetadataReport) -> tuple[_Observed, ...]:
    values: list[_Observed] = []
    source_claims: tuple[LocalClaim, ...] | tuple[ProviderClaim, ...]
    for basis in tuple(
        sorted(
            {
                claim.basis
                for claim in report.local_claims
                if claim.basis != "target_metadata"
            }
        )
    ):
        source_claims = tuple(
            claim
            for claim in report.local_claims
            if claim.basis == basis
            and claim.scope is ClaimScope.WORK
            and claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
        )
        observed = _series_from_claims(source_claims)
        if observed is not None:
            values.append(
                _Observed(
                    ProposalValue(ProposalValueKind.SERIES, observed),
                    tuple(claim.reference for claim in source_claims),
                    _local_correlation(report),
                    ProposalScope.WORK,
                    _local_representation(basis),
                )
            )
    for provider in report.providers:
        for candidate in provider.candidates:
            source_claims = tuple(
                claim
                for claim in candidate.claims
                if claim.scope is ClaimScope.WORK
                and claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
            )
            observed = _series_from_claims(source_claims)
            if observed is not None:
                values.append(
                    _Observed(
                        ProposalValue(ProposalValueKind.SERIES, observed),
                        tuple(claim.reference for claim in source_claims),
                        _provider_correlation(provider),
                        ProposalScope.WORK,
                        _provider_representation(provider, candidate),
                    )
                )
    return tuple(values)


def _series_from_claims(
    claims: tuple[LocalClaim | ProviderClaim, ...],
) -> tuple[TargetSeries, ...] | None:
    series_count = sum(claim.kind is ClaimKind.SERIES for claim in claims)
    if not series_count:
        return None
    result: list[TargetSeries] = []
    index = 0
    while index < len(claims):
        sequence_claim: LocalClaim | ProviderClaim | None = None
        if claims[index].kind is ClaimKind.SERIES_SEQUENCE:
            sequence_claim = claims[index]
            index += 1
            if index >= len(claims):
                return None
        name_claim = claims[index]
        # `claims` only ever holds LocalClaim or ProviderClaim, so failing the
        # exact LocalClaim test leaves a ProviderClaim.
        name = (
            name_claim.canonical
            if type(name_claim) is LocalClaim
            else cast("ProviderClaim", name_claim).value
        )
        if (
            name_claim.kind is not ClaimKind.SERIES
            or name_claim.state is not FieldState.VALID
            or name is None
        ):
            return None
        index += 1
        sequence: str | None = None
        if (
            sequence_claim is None
            and index < len(claims)
            and claims[index].kind is ClaimKind.SERIES_SEQUENCE
        ):
            sequence_claim = claims[index]
            index += 1
        if sequence_claim is not None:
            sequence_value = (
                sequence_claim.canonical
                if type(sequence_claim) is LocalClaim
                else cast("ProviderClaim", sequence_claim).value
            )
            if sequence_claim.state is FieldState.VALID:
                if sequence_value is None:
                    return None
                sequence = sequence_value
            elif not (
                sequence_claim.state is FieldState.ABSENT and sequence_value is None
            ):
                return None
        elif series_count > 1:
            # Multiple names are only safe when every source occurrence has
            # its own explicit sequence slot, including an absent one.
            return None
        result.append(TargetSeries(name, sequence))
    return tuple(result)


def _date_observations(report: MetadataReport) -> tuple[_Observed, ...]:
    values: list[_Observed] = []
    work_context = target_work_identity_context(report.target)
    claim: LocalClaim | ProviderClaim
    for claim in report.local_claims:
        if (
            claim.kind is ClaimKind.PUBLICATION_DATE
            and claim.scope is ClaimScope.EDITION
            and claim.state is FieldState.VALID
            and claim.canonical is not None
            and claim.date_event is DateEvent.MANIFESTATION_PUBLICATION
            and type(claim.date_precision) is DatePrecision
        ):
            try:
                parsed, precision = normalize_date_value(claim.canonical)
            except ValueError:
                continue
            if precision is claim.date_precision and publication_date_is_voting(
                claim.observed, precision
            ):
                values.append(
                    _Observed(
                        ProposalValue(ProposalValueKind.TEXT, parsed),
                        (claim.reference,),
                        _local_correlation(report),
                        ProposalScope.EDITION,
                        _local_representation(claim.basis),
                        claim.date_event,
                        precision,
                    )
                )
    for provider in report.providers:
        for candidate in provider.candidates:
            if (
                work_context is not None
                and provider_candidate_work_identity_contradiction(
                    candidate, *work_context
                )
                is WorkIdentityContradiction.EXPLICIT
            ):
                continue
            for claim in candidate.claims:
                if (
                    claim.kind is ClaimKind.PUBLICATION_DATE
                    and claim.scope is ClaimScope.EDITION
                    and claim.state is FieldState.VALID
                    and claim.value is not None
                    and claim.date_event is DateEvent.MANIFESTATION_PUBLICATION
                    and type(claim.date_precision) is DatePrecision
                    and type(claim.date_lexical) is str
                    and publication_date_is_voting(
                        claim.date_lexical, claim.date_precision
                    )
                ):
                    values.append(
                        _Observed(
                            ProposalValue(ProposalValueKind.TEXT, claim.value),
                            (claim.reference,),
                            _provider_correlation(provider),
                            ProposalScope.EDITION,
                            _provider_representation(provider, candidate),
                            claim.date_event,
                            claim.date_precision,
                        )
                    )
    return tuple(values)


def _publication_results(
    report: MetadataReport, targets: dict[TargetDestination, TargetField]
) -> dict[TargetDestination, ProposalResult]:
    date_target = targets[TargetDestination.PUBLISHED_DATE]
    year_target = targets[TargetDestination.PUBLISHED_YEAR]
    publication_values = _date_observations(report)
    direct_dates = tuple(
        _Observed(
            ProposalValue(
                ProposalValueKind.TEXT,
                str(value.value.value)[:10],
            ),
            value.references,
            value.correlation,
            value.scope,
            value.representation,
            value.date_event,
            DatePrecision.DAY,
        )
        for value in publication_values
        if (
            type(value.value.value) is str
            and value.date_precision
            in {
                DatePrecision.DAY,
                DatePrecision.TIMESTAMP,
            }
        )
    )
    month_dates = tuple(
        _Observed(
            ProposalValue(
                ProposalValueKind.TEXT,
                str(value.value.value)[:7],
            ),
            value.references,
            value.correlation,
            value.scope,
            value.representation,
            value.date_event,
            DatePrecision.MONTH,
        )
        for value in publication_values
        if (
            type(value.value.value) is str
            and value.date_precision
            in {
                DatePrecision.MONTH,
                DatePrecision.DAY,
                DatePrecision.TIMESTAMP,
            }
        )
    )
    years = tuple(
        _Observed(
            ProposalValue(ProposalValueKind.TEXT, str(value.value.value)[:4]),
            value.references,
            value.correlation,
            value.scope,
            value.representation,
            value.date_event,
            DatePrecision.YEAR,
        )
        for value in publication_values
    )
    dates = _strongest_independently_corroborated_date(
        publication_values,
        direct_dates,
        month_dates,
        years,
        frozenset(
            _provider_correlation(provider)
            for provider in report.providers
            if provider.authority is not None
        ),
        _local_correlation(report),
    )
    date_result = _evaluate(
        TargetDestination.PUBLISHED_DATE,
        date_target,
        dates,
        report,
    )
    year_result = ProposalResult(
        TargetDestination.PUBLISHED_YEAR,
        ProposalOutcome.INELIGIBLE,
        (),
        None,
        ("redundant_publication_context",),
    )
    if (
        date_result.selected_alternative_id is not None
        and year_target.state is TargetFieldState.VALUE
        and type(year_target.value) is str
    ):
        selected = next(
            value
            for value in date_result.alternatives
            if value.alternative_id == date_result.selected_alternative_id
        )
        if str(selected.value.value)[:4] != year_target.value:
            date_result = _unresolved(date_result, "date_year_conflict")
    return {
        TargetDestination.PUBLISHED_DATE: date_result,
        TargetDestination.PUBLISHED_YEAR: year_result,
    }


def _strongest_independently_corroborated_date(
    source: tuple[_Observed, ...],
    days: tuple[_Observed, ...],
    months: tuple[_Observed, ...],
    years: tuple[_Observed, ...],
    provider_authorities: frozenset[str],
    local_correlation: str,
) -> tuple[_Observed, ...]:
    possible: list[tuple[_Observed, ...]] = []
    for precision, observations, length in (
        (DatePrecision.DAY, days, 10),
        (DatePrecision.MONTH, months, 7),
        (DatePrecision.YEAR, years, 4),
    ):
        projected = {str(value.value.value)[:length] for value in observations}
        if len(projected) != 1:
            continue
        candidate = next(iter(projected))
        if not all(
            _date_compatible_with_projection(value, candidate, precision)
            for value in source
        ):
            continue
        correlations = {value.correlation for value in observations}
        if (
            local_correlation in correlations
            and len(correlations.intersection(provider_authorities)) >= 2
        ):
            possible.append(observations)
    return possible[0] if possible else ()


def _date_compatible_with_projection(
    observation: _Observed,
    candidate: str,
    precision: DatePrecision,
) -> bool:
    if (
        type(observation.value.value) is not str
        or type(observation.date_precision) is not DatePrecision
    ):
        return False
    shared_length = min(
        {
            DatePrecision.YEAR: 4,
            DatePrecision.MONTH: 7,
            DatePrecision.DAY: 10,
            DatePrecision.TIMESTAMP: 10,
        }[observation.date_precision],
        {
            DatePrecision.YEAR: 4,
            DatePrecision.MONTH: 7,
            DatePrecision.DAY: 10,
        }[precision],
    )
    return observation.value.value[:shared_length] == candidate[:shared_length]


def _cover_result(report: MetadataReport, target: TargetField) -> ProposalResult:
    if report.file.format in AUDIO_FILE_FORMATS:
        # F45 forms text proposals only. An audiobook's artwork stays where it
        # is until the cover field is unlocked for recordings, so the owner is
        # never asked to choose one here (#966).
        return ProposalResult(
            TargetDestination.COVER,
            ProposalOutcome.INELIGIBLE,
            (),
            None,
            ("audiobook_cover_withheld",),
        )
    retrieved = [
        source
        for source in report.cover_sources
        if source.state is CoverSourceState.RETRIEVED and source.artifact is not None
    ]
    grouped: dict[str, list[Any]] = {}
    for source in retrieved:
        # `retrieved` was filtered on `source.artifact is not None`.
        grouped.setdefault(
            cast("CoverArtifact", source.artifact).original_digest, []
        ).append(source)
    alternatives: list[ProposalAlternative] = []
    for digest in sorted(grouped):
        sources = grouped[digest]
        artifact = sources[0].artifact
        if artifact is None:
            continue
        preserves = any(source.kind is CoverSourceKind.ABS for source in sources)
        assessment = (
            ProposalAssessment.PRESERVES_TARGET
            if target.state is TargetFieldState.VALUE and preserves
            else ProposalAssessment.FILLS_EMPTY
            if target.state is not TargetFieldState.VALUE
            else ProposalAssessment.REPLACES_TARGET
        )
        alternatives.append(
            ProposalAlternative.create(
                ProposalValue(ProposalValueKind.COVER, artifact.artifact_id),
                assessment,
                tuple(source.source_id for source in sources),
                tuple(
                    _correlation(
                        "cover:"
                        + (
                            source.provenance.provider
                            if source.provenance is not None
                            else source.kind.value
                        )
                    )
                    for source in sources
                ),
                ProposalScope.COVER,
            )
        )
    ordered = tuple(sorted(alternatives, key=lambda value: value.alternative_id))
    if target.state in {
        TargetFieldState.UNLOADED,
        TargetFieldState.MALFORMED,
        TargetFieldState.OVERSIZED,
    }:
        return ProposalResult(
            TargetDestination.COVER,
            ProposalOutcome.INELIGIBLE,
            (),
            None,
            ("target_state_unusable",),
        )
    current = next(
        (
            value
            for value in ordered
            if value.assessment is ProposalAssessment.PRESERVES_TARGET
        ),
        None,
    )
    if current is not None:
        alternatives_for_review = tuple(
            value
            for value in ordered
            if value.assessment is not ProposalAssessment.PRESERVES_TARGET
        )
        if alternatives_for_review:
            return ProposalResult(
                TargetDestination.COVER,
                ProposalOutcome.UNRESOLVED_CHOICE,
                ordered,
                None,
                ("current_cover_verified", "owner_cover_choice_required"),
            )
        return ProposalResult(
            TargetDestination.COVER,
            ProposalOutcome.KEEP,
            (current,),
            None,
            ("current_cover_verified",),
        )
    if target.state is TargetFieldState.VALUE:
        candidates = tuple(
            value
            for value in ordered
            if value.assessment is not ProposalAssessment.PRESERVES_TARGET
        )
        if candidates:
            return ProposalResult(
                TargetDestination.COVER,
                ProposalOutcome.UNRESOLVED_CHOICE,
                candidates,
                None,
                (
                    "current_cover_unavailable",
                    "current_cover_unverified",
                    "owner_cover_choice_required",
                ),
            )
        return ProposalResult(
            TargetDestination.COVER,
            ProposalOutcome.DEFER,
            (),
            None,
            ("current_cover_unverified",),
        )
    candidates = tuple(
        value
        for value in ordered
        if value.assessment is not ProposalAssessment.PRESERVES_TARGET
    )
    incomplete = any(
        source.kind is not CoverSourceKind.ABS
        and source.state not in {CoverSourceState.RETRIEVED, CoverSourceState.ABSENT}
        for source in report.cover_sources
    )
    if len(candidates) == 1 and not incomplete:
        return ProposalResult(
            TargetDestination.COVER,
            ProposalOutcome.PROPOSE_CHANGE,
            candidates,
            candidates[0].alternative_id,
            ("unique_safe_cover",),
        )
    if not candidates and not incomplete:
        return ProposalResult(
            TargetDestination.COVER,
            ProposalOutcome.INELIGIBLE,
            (),
            None,
            ("no_supported_evidence",),
        )
    if incomplete:
        if candidates:
            return ProposalResult(
                TargetDestination.COVER,
                ProposalOutcome.UNRESOLVED_CHOICE,
                candidates,
                None,
                ("cover_gallery_incomplete", "owner_cover_choice_required"),
            )
        return ProposalResult(
            TargetDestination.COVER,
            ProposalOutcome.DEFER,
            (),
            None,
            ("cover_gallery_incomplete",),
        )
    return ProposalResult(
        TargetDestination.COVER,
        ProposalOutcome.UNRESOLVED_CHOICE,
        candidates,
        None,
        ("competing_evidence",),
    )


def _evaluate(
    destination: TargetDestination,
    target: TargetField,
    observations: tuple[_Observed, ...],
    report: MetadataReport,
    dependencies: tuple[TargetDestination, ...] = (),
) -> ProposalResult:
    alternatives = _alternatives(destination, target, observations, report)
    if target.state in {
        TargetFieldState.UNLOADED,
        TargetFieldState.MALFORMED,
        TargetFieldState.OVERSIZED,
    }:
        return ProposalResult(
            destination,
            ProposalOutcome.INELIGIBLE,
            (),
            None,
            ("target_state_unusable",),
            dependencies,
        )
    if not alternatives:
        outcome = (
            ProposalOutcome.DEFER
            if target.state is TargetFieldState.VALUE
            else ProposalOutcome.INELIGIBLE
        )
        reason = (
            "no_positive_preservation_evidence"
            if outcome is ProposalOutcome.DEFER
            else "no_supported_evidence"
        )
        return ProposalResult(destination, outcome, (), None, (reason,), dependencies)
    if destination in {
        TargetDestination.LANGUAGE,
        TargetDestination.PUBLISHER,
    } and _field_policy_conflicts(destination, alternatives):
        return ProposalResult(
            destination,
            ProposalOutcome.UNRESOLVED_CHOICE,
            alternatives,
            None,
            ("field_policy_conflict",),
            dependencies,
        )
    if len(alternatives) != 1:
        if destination in {
            TargetDestination.LANGUAGE,
            TargetDestination.PUBLISHER,
        }:
            if target.state is TargetFieldState.VALUE and any(
                value.assessment is ProposalAssessment.PRESERVES_TARGET
                for value in alternatives
            ):
                return ProposalResult(
                    destination,
                    ProposalOutcome.KEEP,
                    alternatives,
                    None,
                    ("field_policy_preservation",),
                    dependencies,
                )
            return ProposalResult(
                destination,
                ProposalOutcome.ACCEPTABLE_CHOICE,
                alternatives,
                None,
                ("field_policy_acceptable_choice",),
                dependencies,
            )
        return ProposalResult(
            destination,
            ProposalOutcome.UNRESOLVED_CHOICE,
            alternatives,
            None,
            ("competing_evidence",),
            dependencies,
        )
    alternative = alternatives[0]
    if alternative.assessment is ProposalAssessment.PRESERVES_TARGET:
        reason = (
            "field_policy_preservation"
            if destination is TargetDestination.LANGUAGE
            else "positive_preservation_evidence"
        )
        return ProposalResult(
            destination,
            ProposalOutcome.KEEP,
            alternatives,
            None,
            (reason,),
            dependencies,
        )
    subjective = destination in {
        TargetDestination.GENRES,
        TargetDestination.TAGS,
        TargetDestination.DESCRIPTION,
    }
    if target.state is TargetFieldState.VALUE:
        if subjective:
            return ProposalResult(
                destination,
                ProposalOutcome.UNRESOLVED_CHOICE,
                alternatives,
                None,
                ("populated_subjective_field",),
                dependencies,
            )
        if alternative.scope is ProposalScope.WORK and destination in {
            TargetDestination.PUBLISHED_DATE,
            TargetDestination.PUBLISHED_YEAR,
        }:
            return ProposalResult(
                destination,
                ProposalOutcome.UNRESOLVED_CHOICE,
                alternatives,
                None,
                ("work_publication_cannot_replace",),
                dependencies,
            )
        if len(alternative.correlations) < 2:
            return ProposalResult(
                destination,
                ProposalOutcome.UNRESOLVED_CHOICE,
                alternatives,
                None,
                ("insufficient_independent_corroboration",),
                dependencies,
            )
    return ProposalResult(
        destination,
        ProposalOutcome.PROPOSE_CHANGE,
        alternatives,
        alternative.alternative_id,
        (
            "explicit_author_corroborates_untyped_creator"
            if destination is TargetDestination.AUTHORS
            and any(
                observed.corroborated_untyped_creator
                and set(observed.references) <= set(alternative.evidence_references)
                for observed in observations
            )
            else "unique_typed_candidate",
        ),
        dependencies,
    )


def _field_policy_accepts_all(
    destination: TargetDestination,
    alternatives: tuple[ProposalAlternative, ...],
) -> bool:
    values = tuple(
        value.value.value
        for value in alternatives
        if value.value.kind is ProposalValueKind.TEXT and type(value.value.value) is str
    )
    if len(values) != len(alternatives):
        return False
    if destination is TargetDestination.PUBLISHER:
        return all(publisher_statement_valid(value) for value in values)
    if destination is TargetDestination.LANGUAGE:
        return all(language_identity(value) is not None for value in values) and all(
            language_outcomes_compatible(left, right)
            for index, left in enumerate(values)
            for right in values[index + 1 :]
        )
    return False


def _field_policy_conflicts(
    destination: TargetDestination,
    alternatives: tuple[ProposalAlternative, ...],
) -> bool:
    return not _field_policy_accepts_all(destination, alternatives) or (
        destination is TargetDestination.LANGUAGE
        and any(
            value.assessment is ProposalAssessment.REPLACES_TARGET
            for value in alternatives
        )
    )


def _alternatives(
    destination: TargetDestination,
    target: TargetField,
    observations: tuple[_Observed, ...],
    report: MetadataReport,
) -> tuple[ProposalAlternative, ...]:
    grouped: dict[
        tuple[
            str,
            ProposalScope,
            DateEvent | None,
            DatePrecision | None,
        ],
        list[_Observed],
    ] = {}
    for value in observations:
        grouped.setdefault(
            (
                _observation_key(destination, value.value),
                value.scope,
                value.date_event,
                value.date_precision,
            ),
            [],
        ).append(value)
    alternatives: list[ProposalAlternative] = []
    for key in sorted(
        grouped,
        key=lambda item: (
            item[0],
            item[1].value,
            "" if item[2] is None else item[2].value,
            "" if item[3] is None else item[3].value,
        ),
    ):
        values = grouped[key]
        proposal_values = tuple(value.value for value in values)
        if destination is TargetDestination.DESCRIPTION:
            ordinary_provider_values = _ordinary_description_values(report, values)
            if ordinary_provider_values:
                proposal_values = ordinary_provider_values
        if destination is TargetDestination.ISBN:
            isbn13_values = tuple(
                value
                for value in proposal_values
                if type(value.value) is str and isbn_family(value.value) == value.value
            )
            if isbn13_values:
                proposal_values = isbn13_values
        proposal_value = min(
            proposal_values,
            key=lambda value: canonical_json(value.to_data()),
        )
        if target.state is TargetFieldState.VALUE and _matches(
            destination, target.value, proposal_value
        ):
            assessment = ProposalAssessment.PRESERVES_TARGET
        elif target.state is TargetFieldState.VALUE:
            assessment = ProposalAssessment.REPLACES_TARGET
        else:
            assessment = ProposalAssessment.FILLS_EMPTY
        scope = values[0].scope
        date_event = values[0].date_event
        date_precision = values[0].date_precision
        alternatives.append(
            ProposalAlternative.create(
                proposal_value,
                assessment,
                tuple(reference for value in values for reference in value.references),
                tuple(value.correlation for value in values),
                scope,
                date_event,
                date_precision,
            )
        )
    return tuple(sorted(alternatives, key=lambda value: value.alternative_id))


def _value(
    destination: TargetDestination, value: str | tuple[str, ...]
) -> ProposalValue:
    if type(value) is tuple:
        normalized = tuple(normalize_title(item) for item in value)
        return ProposalValue(ProposalValueKind.LIST, normalized)
    # The tuple branch above returned, so only the `str` half of the parameter
    # union reaches here.
    if destination in {
        TargetDestination.TITLE,
        TargetDestination.SUBTITLE,
    }:
        value = normalize_title(cast("str", value))
    elif destination is TargetDestination.ASIN:
        value = cast("str", value).upper()
    return ProposalValue(ProposalValueKind.TEXT, value)


def _matches(
    destination: TargetDestination,
    target: object,
    candidate: ProposalValue,
) -> bool:
    if destination is TargetDestination.SERIES:
        return _series_values_match(target, candidate.value)
    return values_match(destination, target, candidate.value)


def _series_values_match(target: object, candidate: object) -> bool:
    if type(target) is not tuple or type(candidate) is not tuple:
        return False
    return tuple(
        (series_name_equivalence_key(value.name), value.sequence)
        for value in cast("tuple[TargetSeries, ...]", target)
    ) == tuple(
        (series_name_equivalence_key(value.name), value.sequence)
        for value in cast("tuple[TargetSeries, ...]", candidate)
    )


def _comparison(destination: TargetDestination, value: str) -> str:
    return canonical_text(destination, value)


def _observation_key(destination: TargetDestination, value: ProposalValue) -> str:
    # `ProposalValue.__post_init__` binds each kind to its value shape: a tuple
    # value is TargetSeries for the SERIES destination and str everywhere else.
    if destination is TargetDestination.SERIES:
        normalized: object = tuple(
            (series_name_equivalence_key(item.name), item.sequence)
            for item in cast("tuple[TargetSeries, ...]", value.value)
        )
    elif type(value.value) is tuple:
        normalized = tuple(
            _comparison(destination, item)
            for item in cast("tuple[str, ...]", value.value)
        )
    elif destination is TargetDestination.LANGUAGE and type(value.value) is str:
        normalized = language_equivalence_class(value.value) or value.value
    elif destination is TargetDestination.PUBLISHER and type(value.value) is str:
        normalized = value.value
    elif destination is TargetDestination.ISBN and type(value.value) is str:
        normalized = isbn_family(value.value) or value.value
    elif type(value.value) is str:
        normalized = _comparison(destination, value.value)
    else:
        normalized = value.value
    return canonical_json({"kind": value.kind.value, "value": normalized})


def series_name_equivalence_key(value: str) -> str:
    """Collapse only the recorded display decorations for series election."""
    words = _comparison(TargetDestination.SERIES, value).split()
    if len(words) > 1 and words[:1] == ["the"]:
        words = words[1:]
    if len(words) > 1 and words[-1:] in (["trilogy"], ["series"]):
        words = words[:-1]
    return " ".join(words)


def _eligible_identifier(
    destination: TargetDestination, value: str, applicability: str
) -> bool:
    if destination is TargetDestination.ISBN:
        return applicability in {"ebook", "unknown"}
    if destination is TargetDestination.ASIN:
        # One ASIN grammar for the whole system: the proposal path asks the
        # same question the claim path already answers (#957, #966).
        return canonical_asin(value) is not None
    return True


def _eligible_provider_manifestation(
    destination: TargetDestination,
    claim: ProviderClaim,
    provider: ProviderObservation,
    candidate: ProviderCandidate,
) -> bool:
    """Whether one provider claim is stated about this exact manifestation.

    A subtitle and an ASIN belong to a manifestation, not to a work, so a
    bibliographic provider only speaks for one when it has typed the claim as
    an ebook's. An audiobook authority types nothing, because it has a single
    product to describe and the ASIN it states is that product's identifier.
    What binds that statement to *this* recording is the runtime corroboration
    band, and a retained EDITION-scope marketplace evidence is exactly the
    band's verdict: it survives into the report only for a corroborated
    candidate, or the lane was built from the item's own ASIN, which is the
    identifier by construction (ADR-0061).  Reading it here is reusing that
    admission, not repeating it (ADR-0061, ADR-0064, #966).
    """
    if destination not in {TargetDestination.SUBTITLE, TargetDestination.ASIN}:
        return True
    if claim.applicability == "ebook":
        return True
    return (
        destination is TargetDestination.ASIN
        and claim.scope is ClaimScope.EDITION
        and provider.authority in AUDIOBOOK_PROVIDER_AUTHORITIES
        and any(
            value.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
            and value.scope is ClaimScope.EDITION
            for value in candidate.evidence
        )
    )


def _eligible_identifier_reference(
    report: MetadataReport,
    destination: TargetDestination,
    reference: str,
) -> bool:
    if destination is not TargetDestination.ISBN:
        return True
    return any(
        evidence.reference == reference
        and evidence.relation is IdentifierRelation.ACCEPTED
        for evidence in report.identifier_evidence
    )


def _isbn_rejection_reason(
    evidence: tuple[IdentifierEvidence, ...],
) -> str | None:
    """Keep a retained ISBN eligibility rejection distinct from missing evidence."""
    relations = {item.relation for item in evidence}
    if IdentifierRelation.UNRESOLVED in relations:
        return "identifier_applicability_unresolved"
    if relations == {IdentifierRelation.INVALID}:
        return "identifier_validity_invalid"
    return None


def _accepted_provider_manifestation_anchor(
    report: MetadataReport,
    provider: str,
    candidate: ProviderCandidate,
) -> bool:
    accepted_references = {
        evidence.reference
        for evidence in report.identifier_evidence
        if evidence.relation is IdentifierRelation.ACCEPTED
        and evidence.provider == provider
        and evidence.candidate_identity == candidate.identity
        and evidence.work_identity == candidate.work_identity
    }
    return any(
        claim.kind is ClaimKind.ISBN
        and claim.scope is ClaimScope.EDITION
        and claim.state is FieldState.VALID
        and claim.value is not None
        and claim.applicability == "ebook"
        and claim.reference in accepted_references
        for claim in candidate.claims
    )


def _verified_provider_manifestation_cover(
    report: MetadataReport,
    provider: ProviderObservation,
    candidate: ProviderCandidate,
) -> bool:
    """Apply the existing exact provider-cover manifestation verifier."""
    if candidate.work_identity == "unresolved":
        return False
    if any(
        evidence.provider == provider.provider
        and evidence.candidate_identity == candidate.identity
        and evidence.work_identity == candidate.work_identity
        and evidence.relation is IdentifierRelation.UNRELATED
        for evidence in report.identifier_evidence
    ):
        return False
    return manifestation_publication_compatible(candidate, report.local_claims) and any(
        source.state is CoverSourceState.RETRIEVED
        and source.provenance is not None
        and source.provenance.designation is CoverDesignation.EXACT_EDITION
        and source.provenance.provider == provider.provider
        and source.provenance.edition_identity == candidate.identity
        and source.provenance.work_identity == candidate.work_identity
        for source in report.cover_sources
    )


def _accepted_provider_manifestation_isbn_anchor(
    report: MetadataReport,
    provider: ProviderObservation,
    candidate: ProviderCandidate,
    claim: ProviderClaim,
) -> bool:
    """Require the elected provider ISBN claim's exact accepted evidence."""
    if (
        claim.kind is not ClaimKind.ISBN
        or claim.scope is not ClaimScope.EDITION
        or claim.state is not FieldState.VALID
        or claim.value is None
        or claim.applicability != "ebook"
    ):
        return False
    return _verified_provider_manifestation_cover(report, provider, candidate) and any(
        evidence.relation is IdentifierRelation.ACCEPTED
        and evidence.provider == provider.provider
        and evidence.candidate_identity == candidate.identity
        and evidence.work_identity == candidate.work_identity
        and evidence.reference == claim.reference
        for evidence in report.identifier_evidence
    )


def _verified_manifestation_isbn_election(
    report: MetadataReport,
    result: ProposalResult,
) -> ProposalResult:
    """Elect only the ISBN family whose own provider claim is accepted.

    The helper is a single-caller extraction with a directly testable contract:
    it keeps the manifestation-specific election separate from generic proposal
    uniqueness, so a local claim or an accepted sibling cannot widen it.
    """
    claims = {
        claim.reference: (provider, candidate, claim)
        for provider in report.providers
        for candidate in provider.candidates
        for claim in candidate.claims
    }
    qualified: dict[str, ProposalAlternative] = {}
    for alternative in result.alternatives:
        family = isbn_family(
            alternative.value.value
            if alternative.value.kind is ProposalValueKind.TEXT
            and type(alternative.value.value) is str
            else None
        )
        if family is None:
            continue
        if any(
            isbn_family(claim.value) == family
            and _accepted_provider_manifestation_isbn_anchor(
                report, provider, candidate, claim
            )
            for reference in alternative.evidence_references
            if (bound := claims.get(reference)) is not None
            for provider, candidate, claim in (bound,)
        ):
            qualified[family] = alternative
    if len(qualified) == 1:
        alternative = next(iter(qualified.values()))
        return ProposalResult(
            TargetDestination.ISBN,
            ProposalOutcome.PROPOSE_CHANGE,
            result.alternatives,
            alternative.alternative_id,
            (VERIFIED_MANIFESTATION_ISBN_REASON,),
            result.dependencies,
        )
    return ProposalResult(
        TargetDestination.ISBN,
        ProposalOutcome.UNRESOLVED_CHOICE,
        result.alternatives,
        None,
        (MANIFESTATION_ISBN_VERIFICATION_REQUIRED_REASON,),
        result.dependencies,
    )


def _eligible_description(destination: TargetDestination, value: str) -> bool:
    return destination is not TargetDestination.DESCRIPTION or (
        "<" not in value and ">" not in value
    )


def ordinary_provider_description_source_is_safe(
    claim: ProviderClaim,
    candidate: ProviderCandidate,
) -> bool:
    """Recognize one exact ordinary provider description source."""
    return (
        type(claim) is ProviderClaim
        and type(candidate) is ProviderCandidate
        and claim in candidate.claims
        and claim.kind is ClaimKind.DESCRIPTION
        and claim.scope is ClaimScope.WORK
        and claim.state is FieldState.VALID
        and type(claim.value) is str
        and _eligible_description(TargetDestination.DESCRIPTION, claim.value)
        and not any(
            evidence.kind is ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION
            and evidence.scope is ClaimScope.WORK
            for evidence in candidate.evidence
        )
    )


def _eligible_provider_description(
    destination: TargetDestination,
    value: str,
    candidate: ProviderCandidate,
) -> bool:
    if _eligible_description(destination, value):
        return True
    descriptions = tuple(
        claim
        for claim in candidate.claims
        if claim.kind is ClaimKind.DESCRIPTION
        and claim.scope is ClaimScope.WORK
        and claim.state is FieldState.VALID
        and claim.value is not None
    )
    return len(descriptions) == 1 and any(
        evidence.kind is ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION
        and evidence.scope is ClaimScope.WORK
        and evidence.value == "google-books-html-visible-text-v1"
        for evidence in candidate.evidence
    )


def _ordinary_description_values(
    report: MetadataReport,
    observations: list[_Observed],
) -> tuple[ProposalValue, ...]:
    references = {
        reference for observed in observations for reference in observed.references
    }
    return tuple(
        _value(TargetDestination.DESCRIPTION, claim.value)
        for provider in report.providers
        for candidate in provider.candidates
        for claim in candidate.claims
        if claim.reference in references
        and claim.value is not None
        and ordinary_provider_description_source_is_safe(claim, candidate)
        and provider_candidate_represents_target_work(report, provider, candidate)
    )


def _eligible_field_value(destination: TargetDestination, value: str) -> bool:
    return destination is not TargetDestination.PUBLISHER or publisher_statement_valid(
        value
    )


def _scope(value: ClaimScope) -> ProposalScope:
    return ProposalScope.WORK if value is ClaimScope.WORK else ProposalScope.EDITION


def _local_title_is_unambiguous(
    report: MetadataReport,
    selected: LocalClaim,
) -> bool:
    return (
        sum(
            claim.kind is ClaimKind.TITLE
            and claim.scope is selected.scope
            and claim.basis == selected.basis
            and claim.state is FieldState.VALID
            and claim.canonical is not None
            for claim in report.local_claims
        )
        == 1
    )


def _provider_title_is_unambiguous(
    candidate: ProviderCandidate,
    selected: ProviderClaim,
) -> bool:
    scoped = tuple(
        claim
        for claim in candidate.claims
        if claim.kind is ClaimKind.TITLE
        and claim.scope is selected.scope
        and claim.state is FieldState.VALID
        and claim.value is not None
    )
    manifestation = tuple(
        claim
        for claim in candidate.claims
        if claim.kind is ClaimKind.TITLE
        and claim.scope is ClaimScope.EDITION
        and claim.state is FieldState.VALID
        and claim.value is not None
    )
    return len(scoped) == 1 and (
        not manifestation
        or (
            len(manifestation) == 1
            and _comparison(
                TargetDestination.TITLE,
                manifestation[0].value or "",
            )
            == _comparison(
                TargetDestination.TITLE,
                selected.value or "",
            )
        )
    )


def _local_representation(basis: str) -> str:
    return _representation("local:" + basis)


def _provider_representation(
    provider: ProviderObservation,
    candidate: ProviderCandidate,
) -> str:
    return _representation(
        canonical_json(
            {
                "authority": provider.authority,
                "candidate": candidate.identity,
                "provider": provider.provider,
                "work": candidate.work_identity,
            }
        )
    )


def _representation(value: str) -> str:
    return "representation_" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _target_field_fingerprint(target: TargetField) -> str:
    return hashlib.sha256(canonical_json(target.to_data()).encode("utf-8")).hexdigest()


def _bibliographic_combination_id(
    selections: tuple[BibliographicSelection, ...],
    source_representations: tuple[str, ...],
) -> str:
    return (
        "bibliographic_combination_"
        + hashlib.sha256(
            canonical_json(
                {
                    "relationship_schema_version": (BIBLIOGRAPHIC_RELATIONSHIP_SCHEMA),
                    "selections": [value.to_data() for value in selections],
                    "source_representations": list(source_representations),
                }
            ).encode("utf-8")
        ).hexdigest()
    )


def _bibliographic_combinations(
    targets: dict[TargetDestination, TargetField],
    results: dict[TargetDestination, ProposalResult],
    observations: dict[TargetDestination, tuple[_Observed, ...]],
    title_associations: Mapping[str, str],
) -> tuple[
    tuple[BibliographicCombination, ...],
    tuple[TargetDestination, ...],
]:
    alternative_index = {
        destination: {
            (
                _observation_key(destination, alternative.value),
                alternative.scope,
            ): alternative
            for alternative in results[destination].alternatives
        }
        for destination in _BIBLIOGRAPHIC_DESTINATIONS
    }
    members: dict[
        str,
        dict[TargetDestination, set[str]],
    ] = {}
    sources_by_alternative: dict[
        TargetDestination,
        dict[str, set[str]],
    ] = {destination: {} for destination in _BIBLIOGRAPHIC_DESTINATIONS}
    for destination in _BIBLIOGRAPHIC_DESTINATIONS:
        for observation in observations.get(destination, ()):
            alternative = alternative_index[destination].get(
                (
                    _observation_key(destination, observation.value),
                    observation.scope,
                )
            )
            direct = alternative is not None
            if alternative is None and destination is TargetDestination.TITLE:
                associated_id = title_associations.get(observation.representation)
                alternative = next(
                    (
                        value
                        for value in results[destination].alternatives
                        if value.alternative_id == associated_id
                    ),
                    None,
                )
            if alternative is None:
                continue
            members.setdefault(observation.representation, {}).setdefault(
                destination,
                set(),
            ).add(alternative.alternative_id)
            if direct:
                sources_by_alternative[destination].setdefault(
                    alternative.alternative_id,
                    set(),
                ).add(observation.representation)

    attempted: set[TargetDestination] = set()
    combinations: dict[
        tuple[tuple[str, str, str | None, str | None], ...],
        tuple[tuple[BibliographicSelection, ...], set[str]],
    ] = {}
    generic_subtitle_rejection = (
        results[TargetDestination.SUBTITLE].outcome is ProposalOutcome.INELIGIBLE
        and results[TargetDestination.SUBTITLE].reasons
        == ("generic_provider_subtitle",)
        # The preservation branch below emits an all-PRESERVE combination, which
        # the ProposalSet invariant tolerates only when the title is kept. An
        # unresolved title with a preserving alternative otherwise raises.
        and results[TargetDestination.TITLE].outcome is ProposalOutcome.KEEP
    )
    for representation, represented in sorted(members.items()):
        component_destinations = represented.keys() & {
            TargetDestination.SUBTITLE,
            TargetDestination.SERIES,
        }
        generic_title_preservation = (
            generic_subtitle_rejection
            and not component_destinations
            and set(represented) == {TargetDestination.TITLE}
        )
        if not component_destinations and not generic_title_preservation:
            continue
        if any(len(values) != 1 for values in represented.values()):
            attempted.update(represented)
            continue
        selections = _selections_for_representation(
            targets,
            results,
            represented,
        )
        if any(
            value.kind is BibliographicSelectionKind.CANDIDATE for value in selections
        ):
            attempted.update(represented)
            _retain_safe_combination(
                combinations,
                selections,
                {representation},
                targets,
                results,
            )
        elif generic_title_preservation:
            _retain_safe_combination(
                combinations,
                selections,
                {representation},
                targets,
                results,
            )

        component_members: dict[TargetDestination, set[str]] = {
            destination: values
            for destination, values in represented.items()
            if destination in {TargetDestination.SUBTITLE, TargetDestination.SERIES}
        }
        for title in results[TargetDestination.TITLE].alternatives:
            if len(title.correlations) < 2:
                continue
            independent_sources = sources_by_alternative[TargetDestination.TITLE].get(
                title.alternative_id, set()
            )
            if len(independent_sources) < 2:
                continue
            cross_source = dict(component_members)
            cross_source[TargetDestination.TITLE] = {title.alternative_id}
            cross_selections = _selections_for_representation(
                targets,
                results,
                cross_source,
            )
            if any(
                value.kind is BibliographicSelectionKind.CANDIDATE
                for value in cross_selections
            ):
                attempted.update(cross_source)
                _retain_safe_combination(
                    combinations,
                    cross_selections,
                    {representation, *independent_sources},
                    targets,
                    results,
                )

    if len(combinations) > MAX_BIBLIOGRAPHIC_COMBINATIONS:
        return (), _BIBLIOGRAPHIC_DESTINATIONS

    values = tuple(
        sorted(
            (
                BibliographicCombination.create(
                    selections,
                    tuple(sorted(sources)),
                )
                for selections, sources in combinations.values()
            ),
            key=lambda value: value.combination_id,
        )
    )
    unresolved = () if values or not attempted else _BIBLIOGRAPHIC_DESTINATIONS
    return values, unresolved


def _selections_for_representation(
    targets: dict[TargetDestination, TargetField],
    results: dict[TargetDestination, ProposalResult],
    represented: dict[TargetDestination, set[str]],
) -> tuple[BibliographicSelection, ...]:
    return tuple(
        _selection_for_representation(
            targets[destination],
            results[destination],
            represented.get(destination),
        )
        for destination in _BIBLIOGRAPHIC_DESTINATIONS
    )


def _selection_for_representation(
    target: TargetField,
    result: ProposalResult,
    represented: set[str] | None,
) -> BibliographicSelection:
    if represented is None:
        return BibliographicSelection.preserve(target)
    alternative_id = next(iter(represented))
    alternative = next(
        value for value in result.alternatives if value.alternative_id == alternative_id
    )
    if alternative.assessment is ProposalAssessment.PRESERVES_TARGET:
        return BibliographicSelection.preserve(target)
    return BibliographicSelection.candidate(
        target.destination,
        alternative_id,
    )


def _retain_safe_combination(
    combinations: dict[
        tuple[tuple[str, str, str | None, str | None], ...],
        tuple[tuple[BibliographicSelection, ...], set[str]],
    ],
    selections: tuple[BibliographicSelection, ...],
    sources: set[str],
    targets: dict[TargetDestination, TargetField],
    results: dict[TargetDestination, ProposalResult],
) -> None:
    if _duplicates_bibliographic_component(
        selections,
        targets,
        results,
    ):
        return
    key = tuple(
        (
            value.destination.value,
            value.kind.value,
            value.alternative_id,
            value.target_field_fingerprint,
        )
        for value in selections
    )
    existing = combinations.get(key)
    if existing is None:
        combinations[key] = (selections, set(sources))
    else:
        existing[1].update(sources)


def _duplicates_bibliographic_component(
    selections: tuple[BibliographicSelection, ...],
    targets: dict[TargetDestination, TargetField],
    results: dict[TargetDestination, ProposalResult],
) -> bool:
    alternatives = {
        result.destination: {
            alternative.alternative_id: alternative
            for alternative in result.alternatives
        }
        for result in results.values()
    }
    selected = {value.destination: value for value in selections}
    title_selection = selected[TargetDestination.TITLE]
    title_value = _selected_bibliographic_value(
        title_selection,
        targets,
        alternatives,
    )
    if type(title_value) is not str:
        return False
    title_tokens = _bibliographic_tokens(title_value)
    if not title_tokens:
        return False
    for destination in (
        TargetDestination.SUBTITLE,
        TargetDestination.SERIES,
    ):
        component_selection = selected[destination]
        if (
            title_selection.kind is BibliographicSelectionKind.PRESERVE_TARGET
            and component_selection.kind is BibliographicSelectionKind.PRESERVE_TARGET
        ):
            continue
        component = _selected_bibliographic_value(
            component_selection,
            targets,
            alternatives,
        )
        component_values = (
            (component,)
            if type(component) is str
            else tuple(
                item
                for value in component
                for item in (
                    value.name,
                    *(
                        ()
                        if value.sequence is None
                        else (
                            value.sequence,
                            f"{value.name} {value.sequence}",
                        )
                    ),
                )
            )
            if (destination is TargetDestination.SERIES and type(component) is tuple)
            else ()
        )
        if any(
            _contains_exact_component(
                title_tokens,
                _bibliographic_tokens(value),
            )
            for value in component_values
        ):
            return True
    return False


def _selected_bibliographic_value(
    selection: BibliographicSelection,
    targets: dict[TargetDestination, TargetField],
    alternatives: dict[
        TargetDestination,
        dict[str, ProposalAlternative],
    ],
) -> object:
    if selection.kind is BibliographicSelectionKind.PRESERVE_TARGET:
        return targets[selection.destination].value
    alternative = alternatives[selection.destination].get(
        selection.alternative_id or ""
    )
    return None if alternative is None else alternative.value.value


def _bibliographic_tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token.casefold() for token in re.findall(r"[^\W_]+", normalize_title(value))
    )


def _contains_exact_component(
    title: tuple[str, ...],
    component: tuple[str, ...],
) -> bool:
    return bool(component) and any(
        title[index : index + len(component)] == component
        for index in range(len(title) - len(component) + 1)
    )


def _bibliographic_unresolved(result: ProposalResult) -> ProposalResult:
    return ProposalResult(
        result.destination,
        ProposalOutcome.UNRESOLVED_CHOICE,
        result.alternatives,
        None,
        ("no_safe_bibliographic_combination",),
        result.dependencies,
    )


def _dependencies(
    destination: TargetDestination,
) -> tuple[TargetDestination, ...]:
    if destination is TargetDestination.TITLE:
        return (TargetDestination.SUBTITLE, TargetDestination.SERIES)
    if destination is TargetDestination.SUBTITLE:
        return (TargetDestination.TITLE,)
    if destination is TargetDestination.SERIES:
        return (TargetDestination.TITLE,)
    return ()


def _unresolved(result: ProposalResult, reason: str) -> ProposalResult:
    return ProposalResult(
        result.destination,
        ProposalOutcome.UNRESOLVED_CHOICE,
        result.alternatives,
        None,
        tuple(sorted(set((*result.reasons, reason)))),
        result.dependencies,
    )


def _correlation(value: str) -> str:
    return "correlation_" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _local_correlation(report: MetadataReport) -> str:
    """Name the local source one report's own claims were read from.

    A correlation is how a proposal says two observations came from the same
    place, and the owner reads the label. A recording's tags are not an epub's
    metadata, so an audiobook says so (#966).
    """
    return _correlation(
        "local:selected_audio"
        if report.file.format in AUDIO_FILE_FORMATS
        else "local:selected_epub"
    )


def _provider_correlation(provider: ProviderObservation) -> str:
    return _correlation("provider:" + (provider.authority or "unknown"))


def _alternative_id(
    value: ProposalValue,
    assessment: ProposalAssessment,
    references: tuple[str, ...],
    correlations: tuple[str, ...],
    scope: ProposalScope,
    date_event: DateEvent | None,
    date_precision: DatePrecision | None,
) -> str:
    return (
        "alternative_"
        + hashlib.sha256(
            canonical_json(
                {
                    "assessment": assessment.value,
                    "correlations": list(correlations),
                    "evidence_references": list(references),
                    "scope": scope.value,
                    "value": value.to_data(),
                    "date_event": (None if date_event is None else date_event.value),
                    "date_precision": (
                        None if date_precision is None else date_precision.value
                    ),
                }
            ).encode("utf-8")
        ).hexdigest()
    )


def _proposal_data(
    report_id: str,
    target_fingerprint: str,
    results: tuple[ProposalResult, ...],
    resolved_policy_id: str,
    bibliographic_combinations: tuple[BibliographicCombination, ...],
    media_kind: str,
) -> dict[str, object]:
    return {
        "media_kind": media_kind,
        "policy_version": POLICY_VERSION,
        "resolved_policy_id": resolved_policy_id,
        "relationships": {
            "bibliographic_combinations": [
                value.to_data() for value in bibliographic_combinations
            ],
            "schema_version": BIBLIOGRAPHIC_RELATIONSHIP_SCHEMA,
        },
        "report": {
            "report_id": report_id,
            "target_fingerprint": target_fingerprint,
        },
        "results": [value.to_data() for value in results],
        "schema_version": PROPOSAL_SCHEMA,
    }


def _proposal_from_data(raw: object) -> ProposalSet:
    root = _object(
        raw,
        {
            "media_kind",
            "policy_version",
            "resolved_policy_id",
            "relationships",
            "report",
            "results",
            "schema_version",
        },
    )
    report = _object(
        root["report"],
        {"report_id", "target_fingerprint"},
    )
    relationships = _object(
        root["relationships"],
        {"bibliographic_combinations", "schema_version"},
    )
    if relationships["schema_version"] != BIBLIOGRAPHIC_RELATIONSHIP_SCHEMA:
        raise ProposalValueError("proposal document is invalid")
    results = tuple(_result_from_data(value) for value in _list(root["results"]))
    combinations = tuple(
        _bibliographic_combination_from_data(value)
        for value in _list(relationships["bibliographic_combinations"])
    )
    proposal_set = ProposalSet.create(
        report_id=report["report_id"],
        target_fingerprint=report["target_fingerprint"],
        results=results,
        resolved_policy_id=root["resolved_policy_id"],
        bibliographic_combinations=combinations,
        media_kind=root["media_kind"],
    )
    if (
        root["schema_version"] != proposal_set.schema_version
        or root["policy_version"] != proposal_set.policy_version
    ):
        raise ProposalValueError("proposal document is invalid")
    return proposal_set


def _bibliographic_combination_from_data(
    raw: object,
) -> BibliographicCombination:
    value = _object(
        raw,
        {
            "combination_id",
            "selections",
            "source_representations",
        },
    )
    return BibliographicCombination(
        value["combination_id"],
        tuple(
            _bibliographic_selection_from_data(item)
            for item in _list(value["selections"])
        ),
        tuple(_strings(value["source_representations"])),
    )


def _bibliographic_selection_from_data(
    raw: object,
) -> BibliographicSelection:
    value = _object(
        raw,
        {
            "alternative_id",
            "destination",
            "kind",
            "target_field_fingerprint",
        },
    )
    return BibliographicSelection(
        TargetDestination(value["destination"]),
        BibliographicSelectionKind(value["kind"]),
        value["alternative_id"],
        value["target_field_fingerprint"],
    )


def _result_from_data(raw: object) -> ProposalResult:
    value = _object(
        raw,
        {
            "alternatives",
            "dependencies",
            "destination",
            "outcome",
            "reasons",
            "selected_alternative_id",
        },
    )
    return ProposalResult(
        TargetDestination(value["destination"]),
        ProposalOutcome(value["outcome"]),
        tuple(_alternative_from_data(item) for item in _list(value["alternatives"])),
        value["selected_alternative_id"],
        tuple(_strings(value["reasons"])),
        tuple(TargetDestination(item) for item in _strings(value["dependencies"])),
    )


def _alternative_from_data(raw: object) -> ProposalAlternative:
    value = _object(
        raw,
        {
            "alternative_id",
            "assessment",
            "correlations",
            "date_event",
            "date_precision",
            "evidence_references",
            "scope",
            "value",
        },
    )
    return ProposalAlternative(
        value["alternative_id"],
        _value_from_data(value["value"]),
        ProposalAssessment(value["assessment"]),
        tuple(_strings(value["evidence_references"])),
        tuple(_strings(value["correlations"])),
        ProposalScope(value["scope"]),
        None if value["date_event"] is None else DateEvent(value["date_event"]),
        (
            None
            if value["date_precision"] is None
            else DatePrecision(value["date_precision"])
        ),
    )


def _value_from_data(raw: object) -> ProposalValue:
    value = _object(raw, {"kind", "value"})
    kind = ProposalValueKind(value["kind"])
    source = value["value"]
    if kind is ProposalValueKind.LIST:
        source = tuple(_strings(source))
    elif kind is ProposalValueKind.SERIES:
        source = tuple(
            TargetSeries(
                item["name"],
                item["sequence"],
            )
            for item in (
                _object(raw_item, {"name", "sequence"}) for raw_item in _list(source)
            )
        )
    return ProposalValue(kind, source)


def _object(value: object, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ProposalValueError("proposal document shape is invalid")
    return value


def _list(value: object) -> list[Any]:
    if type(value) is not list:
        raise ProposalValueError("proposal document list is invalid")
    return value


def _strings(value: object) -> list[str]:
    values = _list(value)
    if any(type(item) is not str for item in values):
        raise ProposalValueError("proposal document string list is invalid")
    return values


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ProposalValueError("proposal document has duplicate keys")
        value[key] = item
    return value


def _reject_number(value: str) -> None:
    raise ProposalValueError("proposal document number is invalid")


def _projection(value: object, maximum: int) -> str:
    try:
        return validate_projection_text(value, "proposal value", maximum)
    except EvidenceValueError as error:
        raise ProposalValueError("proposal value is invalid") from error


__all__ = [
    "BIBLIOGRAPHIC_RELATIONSHIP_SCHEMA",
    "DECORATION_EQUIVALENCE_DECORATED_CONSENSUS_REASON",
    "DECORATION_EQUIVALENCE_PROVIDER_DISSENT_REASON",
    "DECORATION_EQUIVALENT_TITLE_REASONS",
    "DECORATION_TIDY_SERIES_UNPRESERVED_REASON",
    "DECORATION_TIDY_TITLE_REASONS",
    "BibliographicCombination",
    "BibliographicSelection",
    "BibliographicSelectionKind",
    "MAX_BIBLIOGRAPHIC_COMBINATIONS",
    "MAX_PROPOSAL_BYTES",
    "POLICY_VERSION",
    "PROVIDER_CONSENSUS_CONTRIBUTOR_DISPLAY_REASON",
    "MEDIA_KINDS",
    "PROPOSAL_SCHEMA",
    "SUBTITLE_SEGMENT_DECORATED_CONSENSUS_REASON",
    "SUBTITLE_SEGMENT_PROVIDER_DISSENT_REASON",
    "SUBTITLE_SEGMENT_UNCORROBORATED_REASON",
    "SegmentCorroboration",
    "SubtitleSegmentEvidence",
    "ProposalAlternative",
    "ProposalAssessment",
    "ProposalOutcome",
    "ProposalResult",
    "ProposalScope",
    "ProposalSet",
    "ProposalValue",
    "ProposalValueError",
    "ProposalValueKind",
    "evaluate_report",
    "provider_candidate_represents_target_work",
    "segment_corroboration",
    "subtitle_segment_evidence",
]
