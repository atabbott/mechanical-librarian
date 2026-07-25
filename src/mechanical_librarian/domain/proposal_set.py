"""Pure, fixed-policy proposal evaluation for one exact metadata report."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
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
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidenceKind,
    TargetDestination,
    TargetField,
    TargetFieldState,
    TargetSeries,
    canonical_json,
    normalize_title,
)


PROPOSAL_SCHEMA = "proposal-set-v1"
POLICY_VERSION = "fixed-conservative-v1"
CONTRACT_VERSION = "explainable-ebook-proposal-v1"
TEMPLATE_VERSION = "proposal-result-v1"
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
    NEEDS_REVIEW = "needs_review"
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
    MIXED = "mixed"


class ProposalAssessment(StrEnum):
    PRESERVES_TARGET = "preserves_target"
    FILLS_EMPTY = "fills_empty"
    REPLACES_TARGET = "replaces_target"


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
        ):
            raise ProposalValueError("proposal alternative is invalid")
        if self.alternative_id != _alternative_id(
            self.value,
            self.assessment,
            self.evidence_references,
            self.correlations,
            self.scope,
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
    ) -> Self:
        references = tuple(sorted(set(evidence_references)))
        grouped = tuple(sorted(set(correlations)))
        return cls(
            _alternative_id(value, assessment, references, grouped, scope),
            value,
            assessment,
            references,
            grouped,
            scope,
        )

    def to_data(self) -> dict[str, object]:
        return {
            "alternative_id": self.alternative_id,
            "assessment": self.assessment.value,
            "correlations": list(self.correlations),
            "evidence_references": list(self.evidence_references),
            "scope": self.scope.value,
            "value": self.value.to_data(),
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
        selected = {
            value.alternative_id for value in self.alternatives
        }
        if self.outcome is ProposalOutcome.NEEDS_REVIEW:
            if (
                self.selected_alternative_id is None
                or self.selected_alternative_id not in selected
            ):
                raise ProposalValueError("proposal selection is inconsistent")
        elif self.selected_alternative_id is not None:
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
    report_digest: str
    target_fingerprint: str
    results: tuple[ProposalResult, ...]
    schema_version: str = PROPOSAL_SCHEMA
    policy_version: str = POLICY_VERSION
    contract_version: str = CONTRACT_VERSION
    template_version: str = TEMPLATE_VERSION

    def __post_init__(self) -> None:
        if (
            _PROPOSAL_SET_ID.fullmatch(self.proposal_set_id) is None
            or _REPORT_ID.fullmatch(self.report_id) is None
            or _DIGEST.fullmatch(self.report_digest) is None
            or _DIGEST.fullmatch(self.target_fingerprint) is None
            or self.schema_version != PROPOSAL_SCHEMA
            or self.policy_version != POLICY_VERSION
            or self.contract_version != CONTRACT_VERSION
            or self.template_version != TEMPLATE_VERSION
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
        report_digest: str,
        target_fingerprint: str,
        results: tuple[ProposalResult, ...],
    ) -> Self:
        data = _proposal_data(
            "",
            report_id,
            report_digest,
            target_fingerprint,
            results,
        )
        data.pop("proposal_set_id")
        identity = "proposal_set_" + hashlib.sha256(
            canonical_json(data).encode("utf-8")
        ).hexdigest()
        return cls(
            identity,
            report_id,
            report_digest,
            target_fingerprint,
            results,
        )

    def _identity_data(self) -> dict[str, object]:
        value = self.to_data()
        value.pop("proposal_set_id")
        return value

    def to_data(self) -> dict[str, object]:
        return _proposal_data(
            self.proposal_set_id,
            self.report_id,
            self.report_digest,
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
        results[destination] = _evaluate(
            destination,
            targets[destination],
            observations,
            _dependencies(destination),
        )
    publication = _publication_results(report, targets)
    results.update(publication)
    results[TargetDestination.COVER] = _cover_result(
        report, targets[TargetDestination.COVER]
    )
    ordered = tuple(results[value] for value in TargetDestination)
    serialized_report = report.to_json()
    return ProposalSet.create(
        report_id=report.report_id,
        report_digest=hashlib.sha256(serialized_report.encode("utf-8")).hexdigest(),
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
        TargetDestination.SUBTITLE: (ClaimKind.SUBTITLE, ClaimScope.WORK),
        TargetDestination.PUBLISHER: (ClaimKind.PUBLISHER, ClaimScope.EDITION),
        TargetDestination.DESCRIPTION: (ClaimKind.DESCRIPTION, ClaimScope.WORK),
        TargetDestination.ISBN: (ClaimKind.ISBN, ClaimScope.EDITION),
        TargetDestination.ASIN: (ClaimKind.ASIN, ClaimScope.EDITION),
        TargetDestination.LANGUAGE: (ClaimKind.LANGUAGE, ClaimScope.EDITION),
    }
    kind, scope = mapping[destination]
    values: list[_Observed] = []
    for claim in report.local_claims:
        if (
            claim.kind is kind
            and claim.scope is scope
            and claim.state is FieldState.VALID
            and claim.canonical is not None
            and claim.basis != "target_metadata"
            and _eligible_identifier(destination, claim.canonical, claim.applicability)
            and _eligible_description(destination, claim.canonical)
        ):
            values.append(
                _Observed(
                    _value(destination, claim.canonical),
                    (claim.reference,),
                    _correlation("local:selected_epub"),
                    _scope(scope),
                )
            )
    for provider in report.providers:
        for candidate in provider.candidates:
            for claim in candidate.claims:
                if (
                    claim.kind is kind
                    and claim.scope is scope
                    and claim.state is FieldState.VALID
                    and claim.value is not None
                    and _eligible_identifier(
                        destination, claim.value, claim.applicability
                    )
                    and _eligible_description(destination, claim.value)
                ):
                    values.append(
                        _Observed(
                            _value(destination, claim.value),
                            (claim.reference,),
                            _correlation(f"provider:{provider.provider}"),
                            _scope(scope),
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
            and claim.state is FieldState.VALID
            and (
                (
                    claim.kind is ClaimKind.CREATOR
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
        claims = tuple(
            claim
            for claim in basis_claims
            if claim.kind is kind
            and claim.scope is scope
            and claim.role == role
            and claim.state is FieldState.VALID
            and claim.canonical is not None
        )
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
                and claim.state is FieldState.VALID
                for claim in candidate.claims
            ):
                continue
            claims = tuple(
                claim
                for claim in candidate.claims
                if claim.kind is kind
                and claim.scope is scope
                and claim.role == role
                and claim.state is FieldState.VALID
                and claim.value is not None
            )
            if claims:
                values.append(
                    _Observed(
                        _value(destination, tuple(claim.value for claim in claims)),
                        tuple(claim.reference for claim in claims),
                        _correlation(f"provider:{provider.provider}"),
                        _scope(scope),
                    )
                )
    return tuple(values)


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
        claims = tuple(
            claim
            for claim in report.local_claims
            if claim.basis == basis
            and claim.scope is ClaimScope.WORK
            and claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
            and claim.state is FieldState.VALID
            and claim.canonical is not None
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
            claims = tuple(
                claim
                for claim in candidate.claims
                if claim.scope is ClaimScope.WORK
                and claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
                and claim.state is FieldState.VALID
                and claim.value is not None
            )
            observed = _series_from_claims(claims)
            if observed is not None:
                values.append(
                    _Observed(
                        ProposalValue(ProposalValueKind.SERIES, observed),
                        tuple(claim.reference for claim in claims),
                        _correlation(f"provider:{provider.provider}"),
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


def _date_observations(
    report: MetadataReport, target: TargetField
) -> tuple[_Observed, ...]:
    values: list[_Observed] = []
    for claim in report.local_claims:
        if (
            claim.kind is ClaimKind.PUBLICATION_DATE
            and claim.scope is ClaimScope.EDITION
            and claim.state is FieldState.VALID
            and claim.canonical is not None
        ):
            parsed = _date(claim.canonical)
            if parsed is not None:
                values.append(
                    _Observed(
                        ProposalValue(ProposalValueKind.TEXT, parsed),
                        (claim.reference,),
                        _correlation("local:selected_epub"),
                        ProposalScope.EDITION,
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
                ):
                    parsed = _date(claim.value)
                    if parsed is not None:
                        values.append(
                            _Observed(
                                ProposalValue(ProposalValueKind.TEXT, parsed),
                                (claim.reference,),
                                _correlation(f"provider:{provider.provider}"),
                                ProposalScope.EDITION,
                            )
                        )
    if values or target.state is TargetFieldState.VALUE:
        return tuple(values)
    for provider in report.providers:
        for candidate in provider.candidates:
            for evidence in candidate.evidence:
                if (
                    evidence.kind is ProviderEvidenceKind.FIRST_PUBLICATION_DATE
                    and evidence.scope is ClaimScope.WORK
                ):
                    parsed = _date(evidence.value)
                    if parsed is not None:
                        values.append(
                            _Observed(
                                ProposalValue(ProposalValueKind.TEXT, parsed),
                                (
                                    _rich_reference(
                                        provider.provider, candidate, evidence.to_data()
                                    ),
                                ),
                                _correlation(f"provider:{provider.provider}"),
                                ProposalScope.WORK,
                            )
                        )
    return tuple(values)


def _publication_results(
    report: MetadataReport, targets: dict[TargetDestination, TargetField]
) -> dict[TargetDestination, ProposalResult]:
    date_target = targets[TargetDestination.PUBLISHED_DATE]
    year_target = targets[TargetDestination.PUBLISHED_YEAR]
    dates = _date_observations(report, date_target)
    years = tuple(
        _Observed(
            ProposalValue(ProposalValueKind.TEXT, str(value.value.value)[:4]),
            value.references,
            value.correlation,
            value.scope,
        )
        for value in dates
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
            date_result = _deferred(date_result, "date_year_conflict")
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
            year_result = _deferred(year_result, "date_year_conflict")
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
            ordered,
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
            ordered,
            None,
            ("current_cover_verified",),
        )
    if target.state is TargetFieldState.VALUE:
        return ProposalResult(
            TargetDestination.COVER,
            ProposalOutcome.DEFER,
            ordered,
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
            ProposalOutcome.NEEDS_REVIEW,
            ordered,
            candidates[0].alternative_id,
            ("unique_safe_cover",),
        )
    if not candidates and not incomplete:
        return ProposalResult(
            TargetDestination.COVER,
            ProposalOutcome.INELIGIBLE,
            ordered,
            None,
            ("no_supported_evidence",),
        )
    return ProposalResult(
        TargetDestination.COVER,
        ProposalOutcome.DEFER,
        ordered,
        None,
        ("cover_gallery_incomplete" if incomplete else "competing_evidence",),
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
            alternatives,
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
            ProposalOutcome.DEFER,
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
                ProposalOutcome.DEFER,
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
                ProposalOutcome.DEFER,
                alternatives,
                None,
                ("work_publication_cannot_replace",),
                dependencies,
            )
        if len(alternative.correlations) < 2:
            return ProposalResult(
                destination,
                ProposalOutcome.DEFER,
                alternatives,
                None,
                ("insufficient_independent_corroboration",),
                dependencies,
            )
    return ProposalResult(
        destination,
        ProposalOutcome.NEEDS_REVIEW,
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
    grouped: dict[str, list[_Observed]] = {}
    for value in observations:
        grouped.setdefault(canonical_json(value.value.to_data()), []).append(value)
    alternatives: list[ProposalAlternative] = []
    for key in sorted(grouped):
        values = grouped[key]
        proposal_value = values[0].value
        if target.state is TargetFieldState.VALUE and _matches(
            destination, target.value, proposal_value
        ):
            assessment = ProposalAssessment.PRESERVES_TARGET
        elif target.state is TargetFieldState.VALUE:
            assessment = ProposalAssessment.REPLACES_TARGET
        else:
            assessment = ProposalAssessment.FILLS_EMPTY
        scopes = {value.scope for value in values}
        scope = next(iter(scopes)) if len(scopes) == 1 else ProposalScope.MIXED
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
        return target == candidate.value
    if type(target) is tuple and type(candidate.value) is tuple:
        return tuple(_comparison(item) for item in target) == tuple(
            _comparison(item) for item in candidate.value
        )
    if type(target) is str and type(candidate.value) is str:
        return _comparison(target) == _comparison(candidate.value)
    return target == candidate.value


def _comparison(value: str) -> str:
    return normalize_title(value).casefold()


def _eligible_identifier(
    destination: TargetDestination, value: str, applicability: str
) -> bool:
    if destination is TargetDestination.ISBN:
        return applicability in {"ebook", "unknown"}
    if destination is TargetDestination.ASIN:
        return _ASIN.fullmatch(value.upper()) is not None
    return True


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


def _date(value: str) -> str | None:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed.isoformat()


def _deferred(result: ProposalResult, reason: str) -> ProposalResult:
    return ProposalResult(
        result.destination,
        ProposalOutcome.DEFER,
        result.alternatives,
        None,
        tuple(sorted(set((*result.reasons, reason)))),
        result.dependencies,
    )


def _rich_reference(
    provider: str, candidate: ProviderCandidate, evidence: dict[str, object]
) -> str:
    return "evidence_" + hashlib.sha256(
        canonical_json(
            {
                "candidate": candidate.identity,
                "evidence": evidence,
                "provider": provider,
            }
        ).encode("utf-8")
    ).hexdigest()


def _correlation(value: str) -> str:
    return "correlation_" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _alternative_id(
    value: ProposalValue,
    assessment: ProposalAssessment,
    references: tuple[str, ...],
    correlations: tuple[str, ...],
    scope: ProposalScope,
) -> str:
    return "alternative_" + hashlib.sha256(
        canonical_json(
            {
                "assessment": assessment.value,
                "correlations": list(correlations),
                "evidence_references": list(references),
                "scope": scope.value,
                "value": value.to_data(),
            }
        ).encode("utf-8")
    ).hexdigest()


def _proposal_data(
    proposal_set_id: str,
    report_id: str,
    report_digest: str,
    target_fingerprint: str,
    results: tuple[ProposalResult, ...],
) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "policy_version": POLICY_VERSION,
        "proposal_set_id": proposal_set_id,
        "report": {
            "document_digest": report_digest,
            "report_id": report_id,
            "target_fingerprint": target_fingerprint,
        },
        "results": [value.to_data() for value in results],
        "schema_version": PROPOSAL_SCHEMA,
        "template_version": TEMPLATE_VERSION,
    }


def _proposal_from_data(raw: object) -> ProposalSet:
    root = _object(
        raw,
        {
            "contract_version",
            "policy_version",
            "proposal_set_id",
            "report",
            "results",
            "schema_version",
            "template_version",
        },
    )
    report = _object(
        root["report"],
        {"document_digest", "report_id", "target_fingerprint"},
    )
    results = tuple(_result_from_data(value) for value in _list(root["results"]))
    return ProposalSet(
        root["proposal_set_id"],
        report["report_id"],
        report["document_digest"],
        report["target_fingerprint"],
        results,
        root["schema_version"],
        root["policy_version"],
        root["contract_version"],
        root["template_version"],
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
    "CONTRACT_VERSION",
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
    "TEMPLATE_VERSION",
    "evaluate_report",
]
