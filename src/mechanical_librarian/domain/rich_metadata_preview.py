"""Read-only projections of admitted rich metadata proposals.

This module does not admit new evidence or schedule effects. It turns one exact
report/proposal pair into a bounded explanation of the owner policy, with only
the option-specific retained-evidence checks needed for manual preview.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final

from mechanical_librarian.domain.metadata_policy import ResolvedMetadataPolicy
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_PASSING_AUTOMATIC_OPTIONS,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_TAGS,
    ClaimKind,
    ClaimScope,
    FieldState,
    IdentifierRelation,
    MetadataReport,
    ProviderCandidate,
    ProviderClaim,
    ProviderEvidenceKind,
    ProviderObservation,
    ProviderStatus,
    TargetDestination,
    TargetField,
    TargetFieldState,
    isbn_family,
    normalize_title,
)
from mechanical_librarian.domain.proposal_set import (
    BibliographicSelectionKind,
    ProposalAlternative,
    ProposalAssessment,
    ProposalOutcome,
    ProposalResult,
    ProposalScope,
    ProposalSet,
    ProposalValueKind,
    provider_candidate_represents_target_work,
)


_DESTINATIONS: Final = {
    "subtitle": TargetDestination.SUBTITLE,
    "series": TargetDestination.SERIES,
    "genres": TargetDestination.GENRES,
    "tags": TargetDestination.TAGS,
    "description_fill": TargetDestination.DESCRIPTION,
    "isbn_form": TargetDestination.ISBN,
    "asin": TargetDestination.ASIN,
    "explicit": TargetDestination.EXPLICIT,
}
_EMPTY: Final = frozenset(
    {TargetFieldState.MISSING, TargetFieldState.NULL, TargetFieldState.EMPTY}
)
_ISBN13: Final = re.compile(r"^97[89][0-9]{10}$")
_SUBTITLE_SEPARATORS: Final = (": ", " — ", " – ", " - ")
_TARGET_WORK_FIELDS: Final = frozenset({"description_fill", "series", "genres", "tags"})


class RichMetadataPreviewError(ValueError):
    """The exact immutable preview inputs do not belong together."""


@dataclass(frozen=True, slots=True)
class _RetainedClaim:
    claim: object
    provider: object | None
    candidate: object | None


@dataclass(frozen=True, slots=True)
class _AutomaticWorkEvidence:
    alternatives: tuple[ProposalAlternative, ...]
    references: dict[str, tuple[str, ...]]
    candidates: tuple[ProviderCandidate, ...]


def _retained_claims(report: MetadataReport) -> dict[str, _RetainedClaim]:
    """Index only evidence retained by this exact report; opaque IDs never infer."""
    values: dict[str, _RetainedClaim] = {}
    for claim in report.local_claims:
        _remember_claim(values, claim.reference, _RetainedClaim(claim, None, None))
    for provider in report.providers:
        for candidate in provider.candidates:
            for claim in candidate.claims:
                _remember_claim(
                    values, claim.reference, _RetainedClaim(claim, provider, candidate)
                )
    return values


def _remember_claim(
    values: dict[str, _RetainedClaim], reference: object, value: _RetainedClaim
) -> None:
    if type(reference) is not str or reference in values:
        raise RichMetadataPreviewError("rich preview evidence binding is invalid")
    values[reference] = value


def _automatic_work_evidence(
    report: MetadataReport,
    result: ProposalResult,
    retained_claims: dict[str, _RetainedClaim],
) -> _AutomaticWorkEvidence:
    """Filter only automatic projection to exact target-work provider evidence."""
    alternatives: list[ProposalAlternative] = []
    references: dict[str, tuple[str, ...]] = {}
    candidates: list[ProviderCandidate] = []
    for alternative in result.alternatives:
        admitted: list[str] = []
        for reference in alternative.evidence_references:
            retained = retained_claims.get(reference)
            if (
                retained is None
                or type(retained.provider) is not ProviderObservation
                or type(retained.candidate) is not ProviderCandidate
                or not provider_candidate_represents_target_work(
                    report,
                    retained.provider,
                    retained.candidate,
                )
            ):
                continue
            admitted.append(reference)
            if retained.candidate not in candidates:
                candidates.append(retained.candidate)
        if admitted:
            alternatives.append(alternative)
            references[alternative.alternative_id] = tuple(admitted)
    return _AutomaticWorkEvidence(
        tuple(alternatives),
        references,
        tuple(candidates),
    )


@dataclass(frozen=True, slots=True)
class RichFieldPreview:
    field: str
    option: str
    target_state: str
    current_value: object
    outcome: str
    desired_value: object | None
    reason: str | None
    narrowed_for_target: bool = False
    qualified_value: object | None = None
    source_candidate_id: str | None = None
    source_evidence_references: tuple[str, ...] = ()
    source_combination_ids: tuple[str, ...] = ()

    def to_data(self) -> dict[str, object]:
        return {
            "current_value": self.current_value,
            "desired_value": self.desired_value,
            "field": self.field,
            "narrowed_for_target": self.narrowed_for_target,
            "option": self.option,
            "outcome": self.outcome,
            "reason": self.reason,
            "qualified_value": self.qualified_value,
            "target_state": self.target_state,
        }


@dataclass(frozen=True, slots=True)
class RichMetadataPreview:
    report_id: str
    proposal_set_id: str
    resolved_policy_id: str
    fields: tuple[RichFieldPreview, ...]

    def to_data(self) -> dict[str, object]:
        return {
            "fields": [field.to_data() for field in self.fields],
            "proposal_set_id": self.proposal_set_id,
            "report_id": self.report_id,
            "resolved_policy_id": self.resolved_policy_id,
        }


def compile_rich_metadata_preview(
    policy: ResolvedMetadataPolicy, report: MetadataReport, proposal: ProposalSet
) -> RichMetadataPreview:
    """Compile a no-effect preview from one exact, already-admitted pair."""
    if (
        type(policy) is not ResolvedMetadataPolicy
        or type(report) is not MetadataReport
        or type(proposal) is not ProposalSet
        or proposal.report_id != report.report_id
        or proposal.target_fingerprint != report.target.fingerprint
        or proposal.resolved_policy_id != policy.resolved_id
        or policy.library != report.target.library
    ):
        raise RichMetadataPreviewError("rich preview binding is invalid")
    targets = {field.destination: field for field in report.target.fields}
    results = {result.destination: result for result in proposal.results}
    options = dict(policy.options)
    destinations = dict(policy.destinations)
    retained_claims = _retained_claims(report)
    previews = tuple(
        _project(
            field,
            options[field],
            destinations[field],
            targets[destination],
            results[destination],
            proposal,
            report,
            retained_claims,
        )
        for field, destination in _DESTINATIONS.items()
    )
    return RichMetadataPreview(
        report.report_id, proposal.proposal_set_id, policy.resolved_id, previews
    )


def _project(
    field: str,
    option: str,
    destination: str,
    target: TargetField,
    result: ProposalResult,
    proposal: ProposalSet,
    report: MetadataReport,
    retained_claims: dict[str, _RetainedClaim],
) -> RichFieldPreview:
    current = _data_value(target.value)
    if field == "explicit":
        return RichFieldPreview(
            field,
            option,
            target.state.value,
            current,
            "abstain",
            None,
            "explicit_unsupported",
        )
    if destination == "ignore":
        return RichFieldPreview(
            field,
            option,
            target.state.value,
            current,
            "abstain",
            None,
            "policy_ignored",
        )
    if (
        destination == "automatic"
        and f"{field}:{option}" not in F20_PASSING_AUTOMATIC_OPTIONS
    ):
        return RichFieldPreview(
            field,
            option,
            target.state.value,
            current,
            "abstain",
            None,
            "automatic_unqualified",
        )
    if target.state not in _EMPTY and target.state is not TargetFieldState.VALUE:
        return RichFieldPreview(
            field,
            option,
            target.state.value,
            current,
            "abstain",
            None,
            "target_state_unusable",
        )
    if field == "tags" and option == "off":
        return RichFieldPreview(
            field, option, target.state.value, current, "abstain", None, "policy_off"
        )
    if target.state is TargetFieldState.VALUE and (
        field in {"subtitle", "description_fill", "isbn_form", "asin"}
        or (field in {"genres", "tags"} and option == "fill_empty")
    ):
        return _preserved(field, option, target, current)
    automatic_work = (
        _automatic_work_evidence(report, result, retained_claims)
        if destination == "automatic" and field in _TARGET_WORK_FIELDS
        else None
    )
    if (
        automatic_work is not None
        and result.alternatives
        and not automatic_work.alternatives
    ):
        return _abstain(
            field,
            option,
            target,
            current,
            "target_work_applicability_unproved",
        )
    if field == "subtitle" and option == "corroborated_split":
        return _subtitle_bundle(field, option, target, result, proposal, report)
    if field == "description_fill":
        return _description(
            field,
            option,
            target,
            result,
            retained_claims,
            automatic_work,
        )
    if field == "isbn_form":
        return _isbn(field, option, target, result, report)
    if field == "asin":
        return _asin(field, option, target, result, retained_claims)
    candidate = (
        _combination_member(proposal, TargetDestination.SERIES)
        if field == "series" and option == "primary_narrative"
        else _series_candidate(
            result,
            None if automatic_work is None else automatic_work.alternatives,
        )
        if field == "series"
        else _one_list_candidate(
            result,
            None if automatic_work is None else automatic_work.alternatives,
        )
        if field in {"genres", "tags"}
        else _candidate(result)
    )
    if automatic_work is not None and candidate not in automatic_work.alternatives:
        candidate = None
    if field == "series" and destination == "automatic":
        reason = _automatic_series_abstention(
            report,
            result,
            candidate,
            retained_claims,
            automatic_work,
        )
        if reason is not None:
            return _abstain(field, option, target, current, reason)
    if field == "subtitle" and destination == "automatic":
        reason = _automatic_subtitle_abstention(report, candidate)
        if reason is not None:
            return _abstain(field, option, target, current, reason)
    if candidate is None:
        reason = _candidate_reason(result)
        return RichFieldPreview(
            field, option, target.state.value, current, "abstain", None, reason
        )
    references = (
        candidate.evidence_references
        if automatic_work is None
        else automatic_work.references[candidate.alternative_id]
    )
    if field == "subtitle" and destination == "automatic":
        # The corroborating local claim is the decisive eligibility evidence;
        # the durable record cites it alongside the provider claim.
        references = tuple(
            sorted(
                {
                    *references,
                    *_corroborating_subtitle_references(report, candidate),
                }
            )
        )
    desired = _data_value(candidate.value.value)
    if field in {"genres", "tags"}:
        if candidate.value.kind is not ProposalValueKind.LIST:
            return _abstain(field, option, target, current, "no_candidate")
        if option == "add" and target.state is TargetFieldState.VALUE:
            desired = tuple(
                (
                    *target.value,
                    *(
                        item
                        for item in candidate.value.value
                        if item not in target.value
                    ),
                )
            )
        elif option == "fill_empty" and target.state not in _EMPTY:
            return _preserved(field, option, target, current)
        elif option not in {"fill_empty", "add", "replace"}:
            return _abstain(field, option, target, current, "policy_option_unavailable")
        if len(desired) > MAX_TAGS:
            return _abstain(
                field, option, target, current, "target_projection_oversized"
            )
    elif field == "series":
        if candidate.value.kind is not ProposalValueKind.SERIES:
            return _abstain(field, option, target, current, "no_candidate")
        if option == "all_qualified":
            desired = (candidate.value.value[0],)
            if _data_value(desired) == current:
                return RichFieldPreview(
                    field,
                    option,
                    target.state.value,
                    current,
                    "no_op",
                    current,
                    "unchanged",
                )
            return RichFieldPreview(
                field,
                option,
                target.state.value,
                current,
                "projected",
                _data_value(desired),
                None,
                True,
                _data_value(candidate.value.value),
                candidate.alternative_id,
                references,
                _candidate_combination_ids(proposal, candidate),
            )
        if option == "primary_narrative":
            member = _combination_member(proposal, TargetDestination.SERIES)
            if member is None:
                return _abstain(
                    field, option, target, current, "atomic_bundle_unavailable"
                )
            desired = (member.value.value[0],)
        if option in {"source_order", "primary_narrative"}:
            desired = (candidate.value.value[0],)
    if _data_value(desired) == current:
        return RichFieldPreview(
            field, option, target.state.value, current, "no_op", current, "unchanged"
        )
    return RichFieldPreview(
        field,
        option,
        target.state.value,
        current,
        "projected",
        _data_value(desired),
        None,
        False,
        None,
        candidate.alternative_id,
        references,
        _candidate_combination_ids(proposal, candidate),
    )


def _candidate(
    result: ProposalResult,
    alternatives: tuple[ProposalAlternative, ...] | None = None,
) -> ProposalAlternative | None:
    if alternatives is not None:
        if (
            len(alternatives) == 1
            and alternatives[0].assessment is not ProposalAssessment.PRESERVES_TARGET
        ):
            return alternatives[0]
        return None
    if (
        result.outcome is ProposalOutcome.PROPOSE_CHANGE
        and len(result.alternatives) == 1
        and result.selected_alternative_id == result.alternatives[0].alternative_id
        and result.alternatives[0].assessment is not ProposalAssessment.PRESERVES_TARGET
    ):
        return result.alternatives[0]
    return None


def _series_candidate(
    result: ProposalResult,
    alternatives: tuple[ProposalAlternative, ...] | None = None,
) -> ProposalAlternative | None:
    """Series projection may narrow an admitted target-preserving alternative."""
    values = result.alternatives if alternatives is None else alternatives
    if len(values) != 1:
        return None
    alternative = values[0]
    if alternative.value.kind is not ProposalValueKind.SERIES:
        return None
    if alternatives is not None:
        return (
            alternative
            if alternative.assessment is not ProposalAssessment.PRESERVES_TARGET
            else None
        )
    if result.outcome is ProposalOutcome.PROPOSE_CHANGE:
        return _candidate(result)
    if (
        result.outcome is ProposalOutcome.KEEP
        and alternative.assessment is ProposalAssessment.PRESERVES_TARGET
    ):
        return alternative
    return None


def _automatic_subtitle_abstention(
    report: MetadataReport,
    candidate: ProposalAlternative | None,
) -> str | None:
    """Fill an empty subtitle automatically only when local evidence attests it.

    Provider catalogues split combined titles and carry series-affiliation
    lines as subtitles; without a matching local claim the projection cannot
    distinguish a printed subtitle from a catalogue artifact, so it stays an
    owner decision. The candidate itself must be provider-backed: subtitle
    alternatives built from the book's own packaged claims carry work scope,
    and a local claim never corroborates itself into an automatic fill.
    """
    if candidate is None or type(candidate.value.value) is not str:
        return None
    if candidate.scope is not ProposalScope.EDITION:
        return "subtitle_local_corroboration_required"
    if _corroborating_subtitle_references(report, candidate):
        return None
    return "subtitle_local_corroboration_required"


def _corroborating_subtitle_references(
    report: MetadataReport,
    candidate: ProposalAlternative,
) -> tuple[str, ...]:
    """The valid local subtitle claims that attest the candidate's exact value."""
    value = candidate.value.value
    if type(value) is not str:
        return ()
    desired = normalize_title(value).casefold()
    return tuple(
        claim.reference
        for claim in report.local_claims
        if claim.kind is ClaimKind.SUBTITLE
        and claim.state is FieldState.VALID
        and type(observed := claim.canonical or claim.observed) is str
        and observed
        and normalize_title(observed).casefold() == desired
    )


def _automatic_series_abstention(
    report: MetadataReport,
    result: ProposalResult,
    candidate: ProposalAlternative | None,
    retained_claims: dict[str, _RetainedClaim],
    automatic_work: _AutomaticWorkEvidence | None,
) -> str | None:
    """Keep automatic series authority within its existing two-lineage proof."""
    failed_statuses = {
        ProviderStatus.AUTHENTICATION,
        ProviderStatus.AUTHORIZATION,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.UNAVAILABLE,
        ProviderStatus.INCOMPLETE,
        ProviderStatus.MALFORMED,
        ProviderStatus.OVERSIZED,
        ProviderStatus.CANDIDATE_LIMIT_EXCEEDED,
        ProviderStatus.QUERY_LIMIT_EXCEEDED,
    }
    if any(provider.status in failed_statuses for provider in report.providers) or any(
        lane.status in failed_statuses
        for provider in report.providers
        for lane in provider.lanes
    ):
        return "series_provider_failure"
    if any(
        claim.kind is ClaimKind.SERIES_SEQUENCE
        and claim.state is not FieldState.VALID
        and not (claim.state is FieldState.ABSENT and claim.value is None)
        for provider in report.providers
        for source in provider.candidates
        for claim in source.claims
    ):
        return "series_membership_incomplete"
    memberships = tuple(
        membership
        for provider in report.providers
        for source in provider.candidates
        if automatic_work is None or source in automatic_work.candidates
        for membership in _series_membership_positions(
            tuple(
                claim
                for claim in source.claims
                if claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
            )
        )
    )
    if any(
        not positioned and sum(other_name == name for other_name, _ in memberships) > 1
        for name, positioned in memberships
    ):
        return "series_membership_incomplete"
    if candidate is None:
        return (
            "series_conflicting_membership"
            if result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
            else None
        )
    if candidate.value.kind is ProposalValueKind.SERIES and any(
        value.sequence is None for value in candidate.value.value
    ):
        return "series_membership_incomplete"
    lineages = {
        retained.provider.authority
        for reference in candidate.evidence_references
        if (retained := retained_claims.get(reference)) is not None
        and retained.provider is not None
        and retained.provider.authority is not None
    }
    if len(lineages) < 2:
        return "series_independent_corroboration_required"
    return None


def _series_membership_positions(
    claims: tuple[ProviderClaim, ...],
) -> tuple[tuple[str, bool], ...]:
    """Pair each series name with only its adjacent sequence slot."""
    values: list[tuple[str, bool]] = []
    index = 0
    while index < len(claims):
        prior = (
            claims[index] if claims[index].kind is ClaimKind.SERIES_SEQUENCE else None
        )
        if prior is not None:
            index += 1
        if index >= len(claims) or claims[index].kind is not ClaimKind.SERIES:
            index += 1
            continue
        series = claims[index]
        index += 1
        following = (
            claims[index]
            if index < len(claims) and claims[index].kind is ClaimKind.SERIES_SEQUENCE
            else None
        )
        if following is not None:
            index += 1
        sequence = prior or following
        if series.state is FieldState.VALID and series.value is not None:
            values.append(
                (
                    normalize_title(series.value).casefold(),
                    sequence is not None
                    and sequence.state is FieldState.VALID
                    and sequence.value is not None,
                )
            )
    return tuple(values)


def _one_list_candidate(
    result: ProposalResult,
    alternatives: tuple[ProposalAlternative, ...] | None = None,
) -> ProposalAlternative | None:
    source = result.alternatives if alternatives is None else alternatives
    values = tuple(
        value for value in source if value.value.kind is ProposalValueKind.LIST
    )
    return values[0] if len(values) == 1 else None


def _candidate_reason(result: ProposalResult) -> str:
    if (
        result.outcome
        in {ProposalOutcome.UNRESOLVED_CHOICE, ProposalOutcome.ACCEPTABLE_CHOICE}
        or len(result.alternatives) > 1
    ):
        return "ambiguous_evidence"
    return "no_candidate"


def _typed_alternatives(
    result: ProposalResult, kind: ProposalValueKind
) -> tuple[ProposalAlternative, ...]:
    return tuple(
        alternative
        for alternative in result.alternatives
        if alternative.value.kind is kind
        and alternative.assessment is not ProposalAssessment.PRESERVES_TARGET
    )


def _one_typed_candidate(
    result: ProposalResult,
    kind: ProposalValueKind,
    alternatives: tuple[ProposalAlternative, ...] | None = None,
) -> tuple[ProposalAlternative | None, str | None]:
    selected = _candidate(result, alternatives)
    if selected is not None:
        return (
            (selected, None) if selected.value.kind is kind else (None, "no_candidate")
        )
    typed = (
        _typed_alternatives(result, kind)
        if alternatives is None
        else tuple(
            alternative
            for alternative in alternatives
            if alternative.value.kind is kind
            and alternative.assessment is not ProposalAssessment.PRESERVES_TARGET
        )
    )
    if len(typed) == 1:
        return typed[0], None
    return None, "ambiguous_evidence" if len(typed) > 1 else "no_candidate"


def _description(
    field: str,
    option: str,
    target: TargetField,
    result: ProposalResult,
    retained_claims: dict[str, _RetainedClaim],
    automatic_work: _AutomaticWorkEvidence | None = None,
) -> RichFieldPreview:
    candidate, reason = _one_typed_candidate(
        result,
        ProposalValueKind.TEXT,
        None if automatic_work is None else automatic_work.alternatives,
    )
    if candidate is None:
        return _abstain(
            field, option, target, _data_value(target.value), reason or "no_candidate"
        )
    if option not in {"safe_plain_text", "google_visible_text"}:
        return _abstain(
            field,
            option,
            target,
            _data_value(target.value),
            "policy_option_unavailable",
        )
    if not candidate.evidence_references:
        return _abstain(
            field,
            option,
            target,
            _data_value(target.value),
            "option_evidence_unavailable",
        )
    admitted_references = (
        candidate.evidence_references
        if automatic_work is None
        else automatic_work.references[candidate.alternative_id]
    )
    google_references = tuple(
        reference
        for reference in admitted_references
        if _google_description_reference(reference, candidate, retained_claims)
    )
    ordinary_references = tuple(
        reference
        for reference in admitted_references
        if _ordinary_description_reference(reference, candidate, retained_claims)
    )
    if option == "google_visible_text" and not google_references:
        return _abstain(
            field,
            option,
            target,
            _data_value(target.value),
            "option_evidence_unavailable",
        )
    if option == "safe_plain_text" and not ordinary_references:
        return _abstain(
            field,
            option,
            target,
            _data_value(target.value),
            "option_evidence_unavailable",
        )
    source_references = (
        google_references if option == "google_visible_text" else ordinary_references
    )
    desired = _data_value(candidate.value.value)
    return RichFieldPreview(
        field,
        option,
        target.state.value,
        _data_value(target.value),
        "projected",
        desired,
        None,
        False,
        None,
        candidate.alternative_id,
        source_references,
        (),
    )


def _google_description_reference(
    reference: str,
    alternative: ProposalAlternative,
    retained_claims: dict[str, _RetainedClaim],
) -> bool:
    retained = retained_claims.get(reference)
    return bool(
        retained is not None
        and type(retained.claim) is ProviderClaim
        and retained.claim.kind is ClaimKind.DESCRIPTION
        and retained.claim.value == alternative.value.value
        and retained.provider is not None
        and retained.provider.authority == "google_books"
        and retained.candidate is not None
        and any(
            evidence.kind is ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION
            and evidence.scope is ClaimScope.WORK
            and evidence.value == "google-books-html-visible-text-v1"
            for evidence in retained.candidate.evidence
        )
    )


def _ordinary_description_reference(
    reference: str,
    alternative: ProposalAlternative,
    retained_claims: dict[str, _RetainedClaim],
) -> bool:
    retained = retained_claims.get(reference)
    return bool(
        retained is not None
        and type(retained.claim) is ProviderClaim
        and retained.claim.kind is ClaimKind.DESCRIPTION
        and retained.claim.scope is ClaimScope.WORK
        and retained.claim.value == alternative.value.value
        and retained.candidate is not None
        and not any(
            evidence.kind is ProviderEvidenceKind.DESCRIPTION_TRANSFORMATION
            and evidence.scope is ClaimScope.WORK
            for evidence in retained.candidate.evidence
        )
    )


def _isbn(
    field: str,
    option: str,
    target: TargetField,
    result: ProposalResult,
    report: MetadataReport,
) -> RichFieldPreview:
    alternatives = _typed_alternatives(result, ProposalValueKind.TEXT)
    candidate, reason = _one_typed_candidate(result, ProposalValueKind.TEXT)
    if candidate is None:
        return _abstain(
            field, option, target, _data_value(target.value), reason or "no_candidate"
        )
    if option not in {"preserve_exact", "prefer_isbn13"}:
        return _abstain(
            field,
            option,
            target,
            _data_value(target.value),
            "policy_option_unavailable",
        )
    value = select_isbn_representation(report, candidate, option)
    if value is None:
        return _abstain(
            field,
            option,
            target,
            _data_value(target.value),
            "option_evidence_unavailable",
        )
    return RichFieldPreview(
        field,
        option,
        target.state.value,
        _data_value(target.value),
        "projected",
        value,
        None,
        False,
        None,
        candidate.alternative_id,
        candidate.evidence_references,
    )


def select_isbn_representation(
    report: MetadataReport,
    candidate: ProposalAlternative,
    option: str,
) -> str | None:
    """Select one policy-admitted exact ISBN lexical from retained evidence."""
    accepted = {
        evidence.reference
        for evidence in report.identifier_evidence
        if evidence.relation is IdentifierRelation.ACCEPTED
    }
    retained_claims = _retained_claims(report)
    family = isbn_family(
        candidate.value.value if type(candidate.value.value) is str else None
    )
    if family is None or not candidate.evidence_references:
        return None
    values: list[tuple[str, bool]] = []
    for reference in candidate.evidence_references:
        retained = retained_claims.get(reference)
        claim = None if retained is None else retained.claim
        value = getattr(claim, "value", getattr(claim, "canonical", None))
        if (
            reference not in accepted
            or claim is None
            or claim.kind is not ClaimKind.ISBN
            or type(value) is not str
            or getattr(claim, "applicability", None) not in {"ebook", "unknown"}
            or isbn_family(value) != family
        ):
            return None
        values.append((value, retained.provider is None))
    if option == "preserve_exact":
        values = [value for value in values if value[1]]
    else:
        values = [
            value
            for value in values
            if _ISBN13.fullmatch(value[0]) is not None
            and isbn_family(value[0]) == value[0]
        ]
    return min((value[0] for value in values), default=None)


def _asin(
    field: str,
    option: str,
    target: TargetField,
    result: ProposalResult,
    retained_claims: dict[str, _RetainedClaim],
) -> RichFieldPreview:
    candidate, reason = _one_typed_candidate(result, ProposalValueKind.TEXT)
    if candidate is None:
        return _abstain(
            field, option, target, _data_value(target.value), reason or "no_candidate"
        )
    if option not in {"preserve_exact", "ebook_manifestation"}:
        return _abstain(
            field,
            option,
            target,
            _data_value(target.value),
            "policy_option_unavailable",
        )
    if option == "ebook_manifestation" and (
        not candidate.evidence_references
        or any(
            not _identifier_reference(
                reference, ClaimKind.ASIN, candidate, retained_claims, {"ebook"}
            )
            for reference in candidate.evidence_references
        )
    ):
        return _abstain(
            field,
            option,
            target,
            _data_value(target.value),
            "option_evidence_unavailable",
        )
    return RichFieldPreview(
        field,
        option,
        target.state.value,
        _data_value(target.value),
        "projected",
        candidate.value.value,
        None,
    )


def _identifier_reference(
    reference: str,
    kind: ClaimKind,
    alternative: ProposalAlternative,
    retained_claims: dict[str, _RetainedClaim],
    applicability: set[str],
) -> bool:
    retained = retained_claims.get(reference)
    claim = None if retained is None else retained.claim
    return bool(
        claim is not None
        and claim.kind is kind
        and getattr(claim, "value", getattr(claim, "canonical", None))
        == alternative.value.value
        and getattr(claim, "applicability", None) in applicability
    )


def _preserved(
    field: str, option: str, target: TargetField, current: object
) -> RichFieldPreview:
    return RichFieldPreview(
        field, option, target.state.value, current, "no_op", current, "target_preserved"
    )


def _abstain(
    field: str, option: str, target: TargetField, current: object, reason: str
) -> RichFieldPreview:
    return RichFieldPreview(
        field, option, target.state.value, current, "abstain", None, reason
    )


def _subtitle_bundle(
    field: str,
    option: str,
    target: TargetField,
    result: ProposalResult,
    proposal: ProposalSet,
    report: MetadataReport,
) -> RichFieldPreview:
    """Expose only an admitted title/subtitle combination; never parse title text."""
    alternatives = {
        value.destination: {alt.alternative_id: alt for alt in value.alternatives}
        for value in proposal.results
    }
    matches = {}
    for combination in proposal.bibliographic_combinations:
        selected = {
            selection.destination: selection for selection in combination.selections
        }
        title = selected[TargetDestination.TITLE]
        subtitle = selected[TargetDestination.SUBTITLE]
        if (
            title.kind is BibliographicSelectionKind.CANDIDATE
            and subtitle.kind is BibliographicSelectionKind.CANDIDATE
        ):
            left = alternatives[TargetDestination.TITLE].get(title.alternative_id)
            right = alternatives[TargetDestination.SUBTITLE].get(
                subtitle.alternative_id
            )
            if left is not None and right is not None:
                matches[(left.alternative_id, right.alternative_id)] = (left, right)
    if len(matches) != 1:
        return _abstain(
            field,
            option,
            target,
            _data_value(target.value),
            "atomic_bundle_unavailable" if not matches else "ambiguous_evidence",
        )
    title_candidate, candidate = next(iter(matches.values()))
    current_title = next(
        value
        for value in report.target.fields
        if value.destination is TargetDestination.TITLE
    )
    if (
        current_title.state is not TargetFieldState.VALUE
        or type(current_title.value) is not str
        or not any(
            current_title.value
            == title_candidate.value.value + separator + candidate.value.value
            for separator in _SUBTITLE_SEPARATORS
        )
    ):
        return _abstain(
            field,
            option,
            target,
            _data_value(target.value),
            "corroborated_split_unavailable",
        )
    desired = {
        "title": _data_value(title_candidate.value.value),
        "subtitle": _data_value(candidate.value.value),
    }
    return RichFieldPreview(
        field,
        option,
        target.state.value,
        _data_value(target.value),
        "projected",
        desired,
        None,
    )


def _combination_member(
    proposal: ProposalSet, destination: TargetDestination
) -> ProposalAlternative | None:
    alternatives = {
        value.destination: {alt.alternative_id: alt for alt in value.alternatives}
        for value in proposal.results
    }
    matches = {}
    for combination in proposal.bibliographic_combinations:
        selection = next(
            value
            for value in combination.selections
            if value.destination is destination
        )
        if selection.kind is BibliographicSelectionKind.CANDIDATE:
            candidate = alternatives[destination].get(selection.alternative_id)
            if candidate is not None:
                matches[candidate.alternative_id] = candidate
    return next(iter(matches.values())) if len(matches) == 1 else None


def _candidate_combination_ids(
    proposal: ProposalSet, candidate: ProposalAlternative
) -> tuple[str, ...]:
    return tuple(
        sorted(
            combination.combination_id
            for combination in proposal.bibliographic_combinations
            if any(
                selection.kind is BibliographicSelectionKind.CANDIDATE
                and selection.alternative_id == candidate.alternative_id
                for selection in combination.selections
            )
        )
    )


def project_f20_automatic_decision_value(
    field: str,
    option: str,
    candidate_value: object,
    current_value: object = None,
) -> dict[str, object]:
    """Project one qualified proposal value to its exact target shape."""
    if f"{field}:{option}" not in F20_PASSING_AUTOMATIC_OPTIONS:
        raise RichMetadataPreviewError("rich automatic projection is unavailable")
    if type(candidate_value) is not dict or set(candidate_value) != {"kind", "value"}:
        raise RichMetadataPreviewError("rich automatic projection is invalid")
    if field == "series":
        values = candidate_value["value"]
        if (
            candidate_value["kind"] != "series"
            or type(values) is not list
            or not values
        ):
            raise RichMetadataPreviewError("rich automatic projection is invalid")
        return {"kind": "series", "value": [values[0]]}
    if field == "isbn_form":
        if (
            candidate_value["kind"] != "text"
            or type(candidate_value["value"]) is not str
        ):
            raise RichMetadataPreviewError("rich automatic projection is invalid")
        return {"kind": "text", "value": candidate_value["value"]}
    if field == "subtitle":
        if (
            candidate_value["kind"] != "text"
            or type(candidate_value["value"]) is not str
        ):
            raise RichMetadataPreviewError("rich automatic projection is invalid")
        return {"kind": "text", "value": candidate_value["value"]}
    if field == "description_fill":
        if (
            candidate_value["kind"] != "text"
            or type(candidate_value["value"]) is not str
        ):
            raise RichMetadataPreviewError("rich automatic projection is invalid")
        return {"kind": "text", "value": candidate_value["value"]}
    if field in {"genres", "tags"}:
        values = candidate_value["value"]
        if (
            candidate_value["kind"] != "list"
            or type(values) is not list
            or not values
            or any(type(value) is not str for value in values)
        ):
            raise RichMetadataPreviewError("rich automatic projection is invalid")
        if option == "add" and current_value is not None:
            if type(current_value) not in {list, tuple} or any(
                type(value) is not str for value in current_value
            ):
                raise RichMetadataPreviewError("rich automatic projection is invalid")
            values = [
                *current_value,
                *(value for value in values if value not in current_value),
            ]
        if len(values) > MAX_TAGS:
            raise RichMetadataPreviewError("rich automatic projection is oversized")
        return {"kind": "list", "value": values}
    raise RichMetadataPreviewError("rich automatic projection is unavailable")


def _data_value(value: object) -> object:
    if isinstance(value, tuple):
        return tuple(
            item.to_data() if hasattr(item, "to_data") else item for item in value
        )
    return value


__all__ = [
    "RichFieldPreview",
    "RichMetadataPreview",
    "RichMetadataPreviewError",
    "compile_rich_metadata_preview",
    "project_f20_automatic_decision_value",
    "select_isbn_representation",
]
