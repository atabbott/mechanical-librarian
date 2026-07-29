"""Pure, fixed-policy proposal evaluation for one exact metadata report."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from mechanical_librarian.domain.cover_evidence import (
    CoverSourceKind,
    CoverSourceState,
)
from mechanical_librarian.domain.evidence_bounds import (
    EvidenceValueError,
    validate_projection_text,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    IdentifierRelation,
    LocalClaim,
    MAX_DESCRIPTION_CHARACTERS,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
    canonical_json,
    complete_creator_identity_bindings,
    creator_comparison_key,
    normalize_date_value,
    normalize_title,
    publication_date_is_voting,
)


PROPOSAL_SCHEMA = "proposal-set-v2"
POLICY_VERSION = "fixed-conservative-v3"
MAX_PROPOSAL_BYTES = 2_097_152
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REPORT_ID = re.compile(r"^report_[0-9a-f]{64}$")
_PROPOSAL_SET_ID = re.compile(r"^proposal_set_[0-9a-f]{64}$")
_ALTERNATIVE_ID = re.compile(r"^alternative_[0-9a-f]{64}$")
_EVIDENCE_REFERENCE = re.compile(
    r"^(?:local|provider|evidence|abs|epub|hardcover|open_library)_[0-9a-f]{64}$"
)
_CORRELATION = re.compile(r"^correlation_[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ASIN = re.compile(r"^B[0-9A-Z]{9}$")


class ProposalValueError(ValueError):
    """A ProposalSet value is outside the fixed contract."""


class ProposalOutcome(StrEnum):
    PROPOSE_CHANGE = "propose_change"
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
    TargetDestination.AUTHORS: frozenset({(ProposalValueKind.LIST, ProposalScope.WORK)}),
    TargetDestination.NARRATORS: frozenset(
        {(ProposalValueKind.LIST, ProposalScope.EDITION)}
    ),
    TargetDestination.SERIES: frozenset({(ProposalValueKind.SERIES, ProposalScope.WORK)}),
    TargetDestination.GENRES: frozenset({(ProposalValueKind.LIST, ProposalScope.WORK)}),
    TargetDestination.PUBLISHED_YEAR: frozenset(
        {
            (ProposalValueKind.TEXT, ProposalScope.WORK),
            (ProposalValueKind.TEXT, ProposalScope.EDITION),
        }
    ),
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
    TargetDestination.ISBN: frozenset({(ProposalValueKind.TEXT, ProposalScope.EDITION)}),
    TargetDestination.ASIN: frozenset({(ProposalValueKind.TEXT, ProposalScope.EDITION)}),
    TargetDestination.LANGUAGE: frozenset(
        {(ProposalValueKind.TEXT, ProposalScope.EDITION)}
    ),
    TargetDestination.EXPLICIT: frozenset(
        {(ProposalValueKind.BOOLEAN, ProposalScope.EDITION)}
    ),
    TargetDestination.TAGS: frozenset({(ProposalValueKind.LIST, ProposalScope.WORK)}),
    TargetDestination.COVER: frozenset({(ProposalValueKind.COVER, ProposalScope.COVER)}),
}

_DESTINATION_DATE_SEMANTICS: dict[
    TargetDestination, tuple[DateEvent | None, DatePrecision | None]
] = {
    destination: (
        (DateEvent.MANIFESTATION_PUBLICATION, DatePrecision.YEAR)
        if destination is TargetDestination.PUBLISHED_YEAR
        else (DateEvent.MANIFESTATION_PUBLICATION, DatePrecision.DAY)
        if destination is TargetDestination.PUBLISHED_DATE
        else (None, None)
    )
    for destination in TargetDestination
}

_OUTCOME_REASON_SETS: dict[ProposalOutcome, frozenset[tuple[str, ...]]] = {
    ProposalOutcome.PROPOSE_CHANGE: frozenset(
        {("unique_safe_cover",), ("unique_typed_candidate",)}
    ),
    ProposalOutcome.UNRESOLVED_CHOICE: frozenset(
        {
            ("competing_evidence",),
            ("date_refinement_requires_corroboration", "unique_typed_candidate"),
            ("date_year_conflict", "unique_typed_candidate"),
            ("insufficient_independent_corroboration",),
            ("populated_subjective_field",),
            ("work_publication_cannot_replace",),
        }
    ),
    ProposalOutcome.KEEP: frozenset(
        {("current_cover_verified",), ("positive_preservation_evidence",)}
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
        {("no_supported_evidence",), ("target_state_unusable",)}
    ),
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
            or self.evidence_references
            != tuple(sorted(set(self.evidence_references)))
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
            "date_event": (
                None if self.date_event is None else self.date_event.value
            ),
            "date_precision": (
                None if self.date_precision is None else self.date_precision.value
            ),
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
            or any(type(value) is not ProposalAlternative for value in self.alternatives)
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
            raise ProposalValueError("proposal alternative is incompatible with destination")
        expected_date = _DESTINATION_DATE_SEMANTICS[self.destination]
        if any(
            (alternative.date_event, alternative.date_precision) != expected_date
            for alternative in self.alternatives
        ):
            raise ProposalValueError(
                "proposal alternative date is incompatible with destination"
            )
        if self.reasons not in _OUTCOME_REASON_SETS[self.outcome]:
            raise ProposalValueError("proposal reasons are incompatible with outcome")
        selected = {value.alternative_id for value in self.alternatives}
        if self.outcome is ProposalOutcome.PROPOSE_CHANGE:
            if (
                len(self.alternatives) != 1
                or self.selected_alternative_id is None
                or self.selected_alternative_id not in selected
                or self.alternatives[0].assessment
                is ProposalAssessment.PRESERVES_TARGET
            ):
                raise ProposalValueError("proposal selection is inconsistent")
        elif self.outcome is ProposalOutcome.UNRESOLVED_CHOICE:
            if not self.alternatives or self.selected_alternative_id is not None:
                raise ProposalValueError("proposal selection is inconsistent")
        elif self.outcome is ProposalOutcome.KEEP:
            if (
                len(self.alternatives) != 1
                or self.alternatives[0].assessment
                is not ProposalAssessment.PRESERVES_TARGET
                or self.selected_alternative_id is not None
            ):
                raise ProposalValueError("proposal selection is inconsistent")
        elif (
            self.alternatives
            or self.selected_alternative_id is not None
        ):
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
    schema_version: str = PROPOSAL_SCHEMA
    policy_version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        if (
            _PROPOSAL_SET_ID.fullmatch(self.proposal_set_id) is None
            or _REPORT_ID.fullmatch(self.report_id) is None
            or _DIGEST.fullmatch(self.target_fingerprint) is None
            or self.schema_version != PROPOSAL_SCHEMA
            or self.policy_version != POLICY_VERSION
            or type(self.results) is not tuple
            or tuple(value.destination for value in self.results)
            != tuple(TargetDestination)
        ):
            raise ProposalValueError("proposal set is invalid")
        expected = "proposal_set_" + hashlib.sha256(
            canonical_json(self._identity_data()).encode("utf-8")
        ).hexdigest()
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
    ) -> Self:
        identity = "proposal_set_" + hashlib.sha256(
            canonical_json(
                _proposal_data(report_id, target_fingerprint, results)
            ).encode("utf-8")
        ).hexdigest()
        return cls(
            identity,
            report_id,
            target_fingerprint,
            results,
        )

    def _identity_data(self) -> dict[str, object]:
        return self.to_data()

    def to_data(self) -> dict[str, object]:
        return _proposal_data(
            self.report_id,
            self.target_fingerprint,
            self.results,
        )

    def to_json(self) -> str:
        return canonical_json(self.to_data())

    @classmethod
    def from_json(cls, serialized: str) -> Self:
        try:
            size = len(serialized.encode("utf-8"))
        except (AttributeError, UnicodeError) as error:
            raise ProposalValueError("proposal document is invalid") from error
        if (
            type(serialized) is not str
            or not serialized
            or size > MAX_PROPOSAL_BYTES
        ):
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
    date_event: DateEvent | None = None
    source_precision: DatePrecision | None = None


def evaluate_report(report: MetadataReport) -> ProposalSet:
    """Evaluate one exact report without I/O, configuration, or provider calls."""
    if type(report) is not MetadataReport:
        raise ProposalValueError("proposal report is invalid")
    targets = {value.destination: value for value in report.target.fields}
    results: dict[TargetDestination, ProposalResult] = {}
    for destination in TargetDestination:
        if destination in {
            TargetDestination.PUBLISHED_YEAR,
            TargetDestination.PUBLISHED_DATE,
            TargetDestination.COVER,
        }:
            continue
        observations = _observations(report, destination)
        result = _evaluate(
            destination,
            targets[destination],
            observations,
            _dependencies(destination),
        )
        if (
            destination is TargetDestination.ISBN
            and not observations
            and any(
                evidence.relation is IdentifierRelation.UNRESOLVED
                for evidence in report.identifier_evidence
            )
        ):
            result = ProposalResult(
                destination,
                ProposalOutcome.DEFER,
                (),
                None,
                ("identifier_applicability_unresolved",),
                _dependencies(destination),
            )
        results[destination] = result
    publication = _publication_results(report, targets)
    results.update(publication)
    results[TargetDestination.COVER] = _cover_result(
        report, targets[TargetDestination.COVER]
    )
    ordered = tuple(results[value] for value in TargetDestination)
    return ProposalSet.create(
        report_id=report.report_id,
        target_fingerprint=report.target.fingerprint,
        results=ordered,
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
    local_scope = ClaimScope.WORK if destination is TargetDestination.SUBTITLE else scope
    provider_scope = ClaimScope.EDITION if destination is TargetDestination.SUBTITLE else scope
    values: list[_Observed] = []
    for claim in report.local_claims:
        if (
            claim.kind is kind
            and claim.scope is local_scope
            and claim.state is FieldState.VALID
            and claim.canonical is not None
            and claim.basis != "target_metadata"
            and _eligible_identifier(destination, claim.canonical, claim.applicability)
            and _eligible_identifier_reference(
                report, destination, claim.reference
            )
            and _eligible_description(destination, claim.canonical)
        ):
            values.append(
                _Observed(
                    _value(destination, claim.canonical),
                    (claim.reference,),
                    _correlation("local:selected_epub"),
                    _scope(claim.scope),
                )
            )
    for provider in report.providers:
        for candidate in provider.candidates:
            for claim in candidate.claims:
                if (
                    claim.kind is kind
                    and claim.scope is provider_scope
                    and claim.state is FieldState.VALID
                    and claim.value is not None
                    and _eligible_identifier(
                        destination, claim.value, claim.applicability
                    )
                    and _eligible_identifier_reference(
                        report, destination, claim.reference
                    )
                    and _eligible_description(destination, claim.value)
                ):
                    values.append(
                        _Observed(
                            _value(destination, claim.value),
                            (claim.reference,),
                            _provider_correlation(provider),
                            _scope(claim.scope),
                        )
                    )
    return tuple(values)


def _list_observations(
    report: MetadataReport, destination: TargetDestination
) -> tuple[_Observed, ...]:
    if destination is TargetDestination.AUTHORS:
        kind, role, scope = ClaimKind.CREATOR, "author", ClaimScope.WORK
    elif destination is TargetDestination.NARRATORS:
        kind, role, scope = ClaimKind.CONTRIBUTOR, "narrator", ClaimScope.EDITION
    elif destination is TargetDestination.GENRES:
        kind, role, scope = ClaimKind.GENRE, None, ClaimScope.WORK
    else:
        kind, role, scope = ClaimKind.TAG, None, ClaimScope.WORK
    values: list[_Observed] = []
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
            and claim.scope is ClaimScope.EDITION
            and claim.state is FieldState.VALID
            and claim.role is None
            for claim in basis_claims
        ):
            continue
        members = tuple(
            claim
            for claim in basis_claims
            if claim.kind is kind and claim.scope is scope and claim.role == role
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
        if (
            destination is TargetDestination.AUTHORS
            and len(
                {
                    creator_comparison_key(claim.canonical)
                    for claim in claims
                }
            )
            != len(claims)
        ):
            continue
        if claims:
            values.append(
                _Observed(
                    _value(destination, tuple(claim.canonical for claim in claims)),
                    tuple(claim.reference for claim in claims),
                    _correlation("local:selected_epub"),
                    _scope(scope),
                )
            )
    for provider in report.providers:
        for candidate in provider.candidates:
            if destination is TargetDestination.AUTHORS and any(
                claim.kind is ClaimKind.CONTRIBUTOR
                and claim.scope is ClaimScope.WORK
                and claim.role in {None, "author", "pseudonym", "collective"}
                for claim in candidate.claims
            ):
                continue
            if destination is TargetDestination.NARRATORS and any(
                claim.kind is ClaimKind.CONTRIBUTOR
                and claim.scope is ClaimScope.EDITION
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
                and not _provider_author_bindings_allow_observation(
                    candidate, claims
                )
            ):
                continue
            if (
                destination is TargetDestination.AUTHORS
                and len(
                    {
                        creator_comparison_key(claim.value)
                        for claim in claims
                    }
                )
                != len(claims)
            ):
                continue
            if claims:
                values.append(
                    _Observed(
                        _value(destination, tuple(claim.value for claim in claims)),
                        tuple(claim.reference for claim in claims),
                        _provider_correlation(provider),
                        _scope(scope),
                    )
                )
    return tuple(values)


def _provider_author_bindings_allow_observation(
    candidate: ProviderCandidate,
    claims: tuple[ProviderClaim, ...],
) -> bool:
    bindings = complete_creator_identity_bindings(
        candidate, claims
    )
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
        and " ".join(
            unicodedata.normalize("NFKC", claim.canonical).split()
        ).casefold()
        == "unknown"
    )


def _series_observations(report: MetadataReport) -> tuple[_Observed, ...]:
    values: list[_Observed] = []
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
        if any(
            claim.state is not FieldState.VALID or claim.canonical is None
            for claim in source_claims
        ):
            continue
        claims = tuple(
            claim
            for claim in source_claims
            if claim.state is FieldState.VALID and claim.canonical is not None
        )
        observed = _series_from_claims(claims)
        if observed is not None:
            values.append(
                _Observed(
                    ProposalValue(ProposalValueKind.SERIES, observed),
                    tuple(claim.reference for claim in claims),
                    _correlation("local:selected_epub"),
                    ProposalScope.WORK,
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
            if any(
                claim.state is not FieldState.VALID or claim.value is None
                for claim in source_claims
            ):
                continue
            claims = tuple(
                claim
                for claim in source_claims
                if claim.state is FieldState.VALID and claim.value is not None
            )
            observed = _series_from_claims(claims)
            if observed is not None:
                values.append(
                    _Observed(
                        ProposalValue(ProposalValueKind.SERIES, observed),
                        tuple(claim.reference for claim in claims),
                        _provider_correlation(provider),
                        ProposalScope.WORK,
                    )
                )
    return tuple(values)


def _series_from_claims(
    claims: tuple[LocalClaim | ProviderClaim, ...],
) -> tuple[TargetSeries, ...] | None:
    names = [
        normalize_title(
            claim.canonical
            if type(claim) is LocalClaim
            else claim.value
        )
        for claim in claims
        if claim.kind is ClaimKind.SERIES
    ]
    sequences = [
        claim.canonical if type(claim) is LocalClaim else claim.value
        for claim in claims
        if claim.kind is ClaimKind.SERIES_SEQUENCE
    ]
    if (
        not names
        or len(sequences) > len(names)
        or (len(names) > 1 and len(sequences) != len(names))
    ):
        return None
    return tuple(
        TargetSeries(name, sequences[index] if index < len(sequences) else None)
        for index, name in enumerate(names)
    )


def _date_observations(report: MetadataReport) -> tuple[_Observed, ...]:
    values: list[_Observed] = []
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
            if (
                precision is claim.date_precision
                and publication_date_is_voting(parsed, precision)
            ):
                values.append(
                    _Observed(
                        ProposalValue(ProposalValueKind.TEXT, parsed),
                        (claim.reference,),
                        _correlation("local:selected_epub"),
                        ProposalScope.EDITION,
                        claim.date_event,
                        precision,
                    )
                )
    for provider in report.providers:
        for candidate in provider.candidates:
            for claim in candidate.claims:
                if (
                    claim.kind is ClaimKind.PUBLICATION_DATE
                    and claim.scope is ClaimScope.EDITION
                    and claim.state is FieldState.VALID
                    and claim.value is not None
                    and claim.date_event is DateEvent.MANIFESTATION_PUBLICATION
                    and type(claim.date_precision) is DatePrecision
                    and publication_date_is_voting(
                        claim.value, claim.date_precision
                    )
                ):
                    values.append(
                        _Observed(
                            ProposalValue(ProposalValueKind.TEXT, claim.value),
                            (claim.reference,),
                            _provider_correlation(provider),
                            ProposalScope.EDITION,
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
    dates = tuple(
        _Observed(
            ProposalValue(
                ProposalValueKind.TEXT,
                str(value.value.value)[:10],
            ),
            value.references,
            value.correlation,
            value.scope,
            value.date_event,
            value.source_precision,
        )
        for value in publication_values
        if (
            type(value.value.value) is str
            and value.source_precision in {
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
            value.date_event,
            value.source_precision,
        )
        for value in publication_values
    )
    date_result = _evaluate(
        TargetDestination.PUBLISHED_DATE,
        date_target,
        dates,
        (TargetDestination.PUBLISHED_YEAR,),
    )
    year_result = _evaluate(
        TargetDestination.PUBLISHED_YEAR,
        year_target,
        years,
        (TargetDestination.PUBLISHED_DATE,),
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
    if (
        year_result.selected_alternative_id is not None
        and date_target.state is TargetFieldState.VALUE
        and type(date_target.value) is str
    ):
        selected = next(
            value
            for value in year_result.alternatives
            if value.alternative_id == year_result.selected_alternative_id
        )
        if str(selected.value.value) != date_target.value[:4]:
            year_result = _unresolved(year_result, "date_year_conflict")
    if date_result.selected_alternative_id is not None:
        selected = next(
            value
            for value in date_result.alternatives
            if value.alternative_id == date_result.selected_alternative_id
        )
        if len(selected.correlations) < 2:
            date_result = _unresolved(
                date_result,
                "date_refinement_requires_corroboration",
            )
    return {
        TargetDestination.PUBLISHED_DATE: date_result,
        TargetDestination.PUBLISHED_YEAR: year_result,
    }


def _cover_result(report: MetadataReport, target: TargetField) -> ProposalResult:
    retrieved = [
        source
        for source in report.cover_sources
        if source.state is CoverSourceState.RETRIEVED and source.artifact is not None
    ]
    grouped: dict[str, list[Any]] = {}
    for source in retrieved:
        grouped.setdefault(source.artifact.original_digest, []).append(source)
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
        return ProposalResult(
            TargetDestination.COVER,
            ProposalOutcome.KEEP,
            (current,),
            None,
            ("current_cover_verified",),
        )
    if target.state is TargetFieldState.VALUE:
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
    dependencies: tuple[TargetDestination, ...] = (),
) -> ProposalResult:
    alternatives = _alternatives(destination, target, observations)
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
        return ProposalResult(
            destination, outcome, (), None, (reason,), dependencies
        )
    if len(alternatives) != 1:
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
        return ProposalResult(
            destination,
            ProposalOutcome.KEEP,
            alternatives,
            None,
            ("positive_preservation_evidence",),
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
        if (
            alternative.scope is ProposalScope.WORK
            and destination
            in {
                TargetDestination.PUBLISHED_DATE,
                TargetDestination.PUBLISHED_YEAR,
            }
        ):
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
        ("unique_typed_candidate",),
        dependencies,
    )


def _alternatives(
    destination: TargetDestination,
    target: TargetField,
    observations: tuple[_Observed, ...],
) -> tuple[ProposalAlternative, ...]:
    grouped: dict[tuple[str, ProposalScope], list[_Observed]] = {}
    for value in observations:
        grouped.setdefault(
            (_observation_key(destination, value.value), value.scope), []
        ).append(value)
    alternatives: list[ProposalAlternative] = []
    for key in sorted(grouped, key=lambda item: (item[0], item[1].value)):
        values = grouped[key]
        proposal_value = min(
            (value.value for value in values),
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
        date_event, date_precision = _DESTINATION_DATE_SEMANTICS[destination]
        alternatives.append(
            ProposalAlternative.create(
                proposal_value,
                assessment,
                tuple(
                    reference
                    for value in values
                    for reference in value.references
                ),
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
    if destination in {
        TargetDestination.TITLE,
        TargetDestination.SUBTITLE,
        TargetDestination.PUBLISHER,
    }:
        value = normalize_title(value)
    elif destination is TargetDestination.LANGUAGE:
        value = value.casefold()
    elif destination is TargetDestination.ASIN:
        value = value.upper()
    return ProposalValue(ProposalValueKind.TEXT, value)


def _matches(
    destination: TargetDestination,
    target: object,
    candidate: ProposalValue,
) -> bool:
    if destination is TargetDestination.SERIES:
        return (
            type(target) is tuple
            and type(candidate.value) is tuple
            and tuple(
                (_comparison(TargetDestination.SERIES, value.name), value.sequence)
                for value in target
            )
            == tuple(
                (_comparison(TargetDestination.SERIES, value.name), value.sequence)
                for value in candidate.value
            )
        )
    if type(target) is tuple and type(candidate.value) is tuple:
        return tuple(_comparison(destination, item) for item in target) == tuple(
            _comparison(destination, item) for item in candidate.value
        )
    if type(target) is str and type(candidate.value) is str:
        return _comparison(destination, target) == _comparison(
            destination, candidate.value
        )
    return target == candidate.value


def _comparison(destination: TargetDestination, value: str) -> str:
    if destination is TargetDestination.DESCRIPTION:
        return " ".join(
            unicodedata.normalize(
                "NFC", _projection(value, MAX_DESCRIPTION_CHARACTERS)
            ).split()
        ).casefold()
    return normalize_title(value).casefold()


def _observation_key(destination: TargetDestination, value: ProposalValue) -> str:
    if destination is TargetDestination.SERIES:
        normalized: object = tuple(
            (_comparison(TargetDestination.SERIES, item.name), item.sequence)
            for item in value.value
        )
    elif type(value.value) is tuple:
        normalized = tuple(_comparison(destination, item) for item in value.value)
    elif type(value.value) is str:
        normalized = _comparison(destination, value.value)
    else:
        normalized = value.value
    return canonical_json({"kind": value.kind.value, "value": normalized})


def _eligible_identifier(
    destination: TargetDestination, value: str, applicability: str
) -> bool:
    if destination is TargetDestination.ISBN:
        return applicability in {"ebook", "unknown"}
    if destination is TargetDestination.ASIN:
        return _ASIN.fullmatch(value.upper()) is not None
    return True


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


def _eligible_description(
    destination: TargetDestination, value: str
) -> bool:
    return destination is not TargetDestination.DESCRIPTION or (
        "<" not in value and ">" not in value
    )


def _scope(value: ClaimScope) -> ProposalScope:
    return (
        ProposalScope.WORK
        if value is ClaimScope.WORK
        else ProposalScope.EDITION
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
    return "alternative_" + hashlib.sha256(
        canonical_json(
            {
                "assessment": assessment.value,
                "correlations": list(correlations),
                "evidence_references": list(references),
                "scope": scope.value,
                "value": value.to_data(),
                "date_event": (
                    None if date_event is None else date_event.value
                ),
                "date_precision": (
                    None if date_precision is None else date_precision.value
                ),
            }
        ).encode("utf-8")
    ).hexdigest()


def _proposal_data(
    report_id: str,
    target_fingerprint: str,
    results: tuple[ProposalResult, ...],
) -> dict[str, object]:
    return {
        "policy_version": POLICY_VERSION,
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
            "policy_version",
            "report",
            "results",
            "schema_version",
        },
    )
    report = _object(
        root["report"],
        {"report_id", "target_fingerprint"},
    )
    results = tuple(_result_from_data(value) for value in _list(root["results"]))
    proposal_set = ProposalSet.create(
        report_id=report["report_id"],
        target_fingerprint=report["target_fingerprint"],
        results=results,
    )
    if (
        root["schema_version"] != proposal_set.schema_version
        or root["policy_version"] != proposal_set.policy_version
    ):
        raise ProposalValueError("proposal document is invalid")
    return proposal_set


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
        tuple(
            _alternative_from_data(item)
            for item in _list(value["alternatives"])
        ),
        value["selected_alternative_id"],
        tuple(_strings(value["reasons"])),
        tuple(
            TargetDestination(item)
            for item in _strings(value["dependencies"])
        ),
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
                _object(raw_item, {"name", "sequence"})
                for raw_item in _list(source)
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
    "MAX_PROPOSAL_BYTES",
    "POLICY_VERSION",
    "PROPOSAL_SCHEMA",
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
]
