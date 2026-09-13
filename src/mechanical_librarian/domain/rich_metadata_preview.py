"""Read-only projections of admitted rich metadata proposals.

This module does not admit new evidence or schedule effects. It turns one exact
report/proposal pair into a bounded explanation of the owner policy, with only
the option-specific retained-evidence checks needed for manual preview.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final

from mechanical_librarian.domain.audiobook_decision_qualification import (
    identity_for_effect,
    qualified_policy_bases,
)
from mechanical_librarian.domain.field_value_comparison import values_match
from mechanical_librarian.domain.metadata_policy import ResolvedMetadataPolicy
from mechanical_librarian.domain.rich_metadata_qualification import (
    F20_PASSING_AUTOMATIC_OPTIONS,
)
from mechanical_librarian.domain.metadata_report import (
    MAX_TAGS, ClaimKind, ClaimScope, FieldState, IdentifierRelation,
    MetadataReport, ProviderCandidate, ProviderClaim, ProviderEvidenceKind,
    ProviderObservation, ProviderQueryKind, ProviderStatus,
    TargetDestination, TargetField, TargetFieldState, WorkIdentityContradiction,
    isbn_family, normalize_title, provider_candidate_work_identity_contradiction,
    target_work_identity_context,
)
from mechanical_librarian.domain.proposal_set import (
    BibliographicSelectionKind, ProposalAlternative, ProposalAssessment,
    ProposalOutcome, ProposalResult, ProposalSet,
    ProposalValueKind, VERIFIED_MANIFESTATION_ISBN_REASON,
    ordinary_provider_description_source_is_safe,
    provider_candidate_represents_target_work,
    provider_candidate_represents_target_series_work,
    series_name_equivalence_key,
    segment_corroboration,
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
_EMPTY: Final = frozenset({TargetFieldState.MISSING, TargetFieldState.NULL, TargetFieldState.EMPTY})
_ISBN13: Final = re.compile(r"^97[89][0-9]{10}$")
_SUBTITLE_SEPARATORS: Final = (": ", " — ", " – ", " - ")
_DESCRIPTION_AUTHORITY_ORDER: Final = (
    "hardcover",
    "google_books",
    "open_library",
)
_DESCRIPTION_MINIMUM_CHARACTERS: Final = 80
_DESCRIPTION_MINIMUM_WORDS: Final = 16
_DESCRIPTION_VISIBLE_CUTOFFS: Final = ("...", "…", "--")


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


def _retained_claims(report: MetadataReport) -> dict[str, _RetainedClaim]:
    """Index only evidence retained by this exact report; opaque IDs never infer."""
    values: dict[str, _RetainedClaim] = {}
    for claim in report.local_claims:
        _remember_claim(values, claim.reference, _RetainedClaim(claim, None, None))
    for provider in report.providers:
        for candidate in provider.candidates:
            for claim in candidate.claims:
                _remember_claim(values, claim.reference, _RetainedClaim(claim, provider, candidate))
    return values


def _remember_claim(values: dict[str, _RetainedClaim], reference: object, value: _RetainedClaim) -> None:
    if type(reference) is not str or reference in values:
        raise RichMetadataPreviewError("rich preview evidence binding is invalid")
    values[reference] = value


def _automatic_work_evidence(
    report: MetadataReport,
    result: ProposalResult,
    retained_claims: dict[str, _RetainedClaim],
) -> _AutomaticWorkEvidence:
    """Keep automatic field evidence backed by the exact target work."""
    alternatives: list[ProposalAlternative] = []
    references: dict[str, tuple[str, ...]] = {}
    for alternative in result.alternatives:
        admitted = tuple(
            reference
            for reference in alternative.evidence_references
            if (retained := retained_claims.get(reference)) is not None
            and type(retained.provider) is ProviderObservation
            and type(retained.candidate) is ProviderCandidate
            and provider_candidate_represents_target_work(
                report, retained.provider, retained.candidate
            )
        )
        if admitted:
            alternatives.append(alternative)
            references[alternative.alternative_id] = admitted
    return _AutomaticWorkEvidence(tuple(alternatives), references)


def _automatic_genre_evidence(
    report: MetadataReport,
    result: ProposalResult,
    retained_claims: dict[str, _RetainedClaim],
) -> _AutomaticWorkEvidence:
    """Remove only genre evidence that explicitly contradicts the target work."""
    context = target_work_identity_context(report.target)
    alternatives: list[ProposalAlternative] = []
    references: dict[str, tuple[str, ...]] = {}
    for alternative in result.alternatives:
        admitted = tuple(
            reference
            for reference in alternative.evidence_references
            if (retained := retained_claims.get(reference)) is None
            or type(retained.provider) is not ProviderObservation
            or type(retained.candidate) is not ProviderCandidate
            or context is None
            or provider_candidate_work_identity_contradiction(
                retained.candidate, *context
            )
            is not WorkIdentityContradiction.EXPLICIT
        )
        if admitted:
            alternatives.append(alternative)
            references[alternative.alternative_id] = admitted
    return _AutomaticWorkEvidence(tuple(alternatives), references)


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
        return {"fields": [field.to_data() for field in self.fields], "proposal_set_id": self.proposal_set_id, "report_id": self.report_id, "resolved_policy_id": self.resolved_policy_id}


def compile_rich_metadata_preview(policy: ResolvedMetadataPolicy, report: MetadataReport, proposal: ProposalSet) -> RichMetadataPreview:
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
    return RichMetadataPreview(report.report_id, proposal.proposal_set_id, policy.resolved_id, previews)


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
        return RichFieldPreview(field, option, target.state.value, current, "abstain", None, "explicit_unsupported")
    if destination == "ignore":
        return RichFieldPreview(field, option, target.state.value, current, "abstain", None, "policy_ignored")
    if (
        destination == "automatic"
        and f"{field}:{option}" not in F20_PASSING_AUTOMATIC_OPTIONS
        # A recording's authority is its own. An option the ebook envelope
        # never qualified is projectable here only while an F46 record
        # qualifies it for audiobooks, and only on one (#967).
        and not (
            proposal.media_kind == "audiobook"
            and f"{field}:{option}" in qualified_policy_bases()
        )
    ):
        return RichFieldPreview(field, option, target.state.value, current, "abstain", None, "automatic_unqualified")
    if target.state not in _EMPTY and target.state is not TargetFieldState.VALUE:
        return RichFieldPreview(field, option, target.state.value, current, "abstain", None, "target_state_unusable")
    if field == "tags" and option == "off":
        return RichFieldPreview(field, option, target.state.value, current, "abstain", None, "policy_off")
    if target.state is TargetFieldState.VALUE and (
        field in {"subtitle", "isbn_form"}
        or (field == "description_fill" and option != "replace_safe_plain_text")
        or (field == "asin" and proposal.media_kind != "audiobook")
        or (field in {"genres", "tags"} and option == "fill_empty")
        or (field == "series" and destination == "automatic" and option not in {"add_qualified", "replace_qualified"})
    ):
        return _preserved(field, option, target, current)
    automatic_work = None
    if destination == "automatic":
        if field == "genres":
            automatic_work = _automatic_genre_evidence(
                report, result, retained_claims
            )
        elif field in {"description_fill", "tags"}:
            automatic_work = _automatic_work_evidence(
                report, result, retained_claims
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
            (
                "target_work_contradicted"
                if field == "genres"
                else "target_work_applicability_unproved"
            ),
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
        else _series_candidate(result)
        if field == "series"
        else _one_list_candidate(
            result,
            None if automatic_work is None else automatic_work.alternatives,
        )
        if field in {"genres", "tags"}
        else _candidate(result)
    )
    if field == "series" and destination == "automatic":
        reason = _automatic_series_abstention(
            report, result, candidate, retained_claims
        )
        if reason is not None:
            return _abstain(field, option, target, current, reason)
    if field == "subtitle" and destination == "automatic":
        reason = _automatic_subtitle_abstention(report, candidate)
        if reason is not None:
            return _abstain(field, option, target, current, reason)
    if candidate is None:
        reason = _candidate_reason(result)
        return RichFieldPreview(field, option, target.state.value, current, "abstain", None, reason)
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
            desired = (
                tuple(_add_audiobook_taxonomy_members(current, desired))
                if proposal.media_kind == "audiobook"
                else tuple((*target.value, *(item for item in candidate.value.value if item not in target.value)))
            )
        elif option == "fill_empty" and target.state not in _EMPTY:
            return _preserved(field, option, target, current)
        elif option not in {"fill_empty", "add", "replace"}:
            return _abstain(field, option, target, current, "policy_option_unavailable")
        if len(desired) > MAX_TAGS:
            return _abstain(field, option, target, current, "target_projection_oversized")
    elif field == "series":
        if candidate.value.kind is not ProposalValueKind.SERIES:
            return _abstain(field, option, target, current, "no_candidate")
        if option == "add_qualified" and target.state is TargetFieldState.VALUE:
            desired = (
                tuple(_add_series_associations(current, desired))
                if proposal.media_kind == "audiobook"
                else [*current, *(item for item in desired if item not in current)]
            )
        if option == "all_qualified":
            desired = (candidate.value.value[0],)
            if _data_value(desired) == current:
                return RichFieldPreview(field, option, target.state.value, current, "no_op", current, "unchanged")
            return RichFieldPreview(
                field, option, target.state.value, current, "projected",
                _data_value(desired), None, True, _data_value(candidate.value.value),
                candidate.alternative_id, candidate.evidence_references,
                _candidate_combination_ids(proposal, candidate),
            )
        if option == "primary_narrative":
            member = _combination_member(proposal, TargetDestination.SERIES)
            if member is None:
                return _abstain(field, option, target, current, "atomic_bundle_unavailable")
            desired = (member.value.value[0],)
        if option in {"source_order", "primary_narrative"}:
            desired = (candidate.value.value[0],)
    if _data_value(desired) == current:
        return RichFieldPreview(field, option, target.state.value, current, "no_op", current, "unchanged")
    return RichFieldPreview(
        field, option, target.state.value, current, "projected",
        _data_value(desired), None, False, None,
        candidate.alternative_id, references,
        _candidate_combination_ids(proposal, candidate),
    )


def _candidate(result: ProposalResult) -> ProposalAlternative | None:
    if (
        result.outcome is ProposalOutcome.PROPOSE_CHANGE
        and len(result.alternatives) == 1
        and result.selected_alternative_id == result.alternatives[0].alternative_id
        and result.alternatives[0].assessment is not ProposalAssessment.PRESERVES_TARGET
    ):
        return result.alternatives[0]
    return None


def _selected_verified_manifestation_isbn_candidate(
    result: ProposalResult,
) -> ProposalAlternative | None:
    """Return the explicitly elected ISBN while retaining competing offers."""
    if (
        result.outcome is not ProposalOutcome.PROPOSE_CHANGE
        or result.reasons != (VERIFIED_MANIFESTATION_ISBN_REASON,)
        or result.selected_alternative_id is None
    ):
        return None
    for alternative in result.alternatives:
        if (
            alternative.alternative_id == result.selected_alternative_id
            and alternative.value.kind is ProposalValueKind.TEXT
            and alternative.assessment is not ProposalAssessment.PRESERVES_TARGET
        ):
            return alternative
    return None


def _series_candidate(result: ProposalResult) -> ProposalAlternative | None:
    """Series projection may narrow an admitted target-preserving alternative."""
    if len(result.alternatives) != 1:
        return None
    alternative = result.alternatives[0]
    if alternative.value.kind is not ProposalValueKind.SERIES:
        return None
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
    """Fill an empty subtitle automatically only when a second source attests it.

    Provider catalogues split combined titles and carry series-affiliation
    lines as subtitles; without independent corroboration the projection
    cannot distinguish a printed subtitle from a catalogue artifact, so it
    stays an owner decision. The candidate itself must state a subtitle, and
    the corroboration must come from a source other than the candidate's own:
    a local subtitle claim, or a colon segment of another source's work title.
    """
    if candidate is None or type(candidate.value.value) is not str:
        return None
    retained = _retained_claims(report)
    if not any(
        reference.startswith("provider_")
        and (held := retained.get(reference)) is not None
        and held.claim.kind is ClaimKind.SUBTITLE
        for reference in candidate.evidence_references
    ):
        return "subtitle_local_corroboration_required"
    references, withheld = _subtitle_corroboration(report, candidate)
    if withheld is not None:
        return withheld
    if references:
        return None
    return "subtitle_local_corroboration_required"


def _subtitle_corroboration(
    report: MetadataReport,
    candidate: ProposalAlternative,
) -> tuple[tuple[str, ...], str | None]:
    """The independent claims attesting the candidate, or the guard withholding.

    The segment half is decided by the domain's shared `segment_corroboration`,
    the same call the subtitle election makes, so the projection and the
    election cannot drift apart.
    """
    value = candidate.value.value
    if type(value) is not str:
        return (), None
    desired = normalize_title(value).casefold()
    explicit = tuple(
        claim.reference
        for claim in report.local_claims
        if claim.kind is ClaimKind.SUBTITLE
        and claim.state is FieldState.VALID
        and type(observed := claim.canonical or claim.observed) is str
        and observed
        and normalize_title(observed).casefold() == desired
    )
    if explicit:
        return tuple(sorted(explicit)), None
    corroboration = segment_corroboration(report, value, candidate.correlations)
    if corroboration.withheld_reason is not None:
        return (), corroboration.withheld_reason
    return (
        tuple(sorted({segment.reference for segment in corroboration.segments})),
        None,
    )


def _corroborating_subtitle_references(
    report: MetadataReport,
    candidate: ProposalAlternative,
) -> tuple[str, ...]:
    """The independent claims that attest the candidate's exact value."""
    return _subtitle_corroboration(report, candidate)[0]


def _automatic_series_abstention(
    report: MetadataReport,
    result: ProposalResult,
    candidate: ProposalAlternative | None,
    retained_claims: dict[str, _RetainedClaim],
) -> str | None:
    """Reject only unsafe evidence that could have elected this series value."""
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
    if candidate is None:
        if result.outcome is not ProposalOutcome.UNRESOLVED_CHOICE and any(
            claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
            and claim.state in {FieldState.MALFORMED, FieldState.OVERSIZED}
            for provider in report.providers
            for source in provider.candidates
            for claim in source.claims
        ):
            return "series_membership_incomplete"
        return (
            "series_conflicting_membership"
            if result.outcome is ProposalOutcome.UNRESOLVED_CHOICE
            else None
        )
    relevant = tuple(
        retained
        for reference in candidate.evidence_references
        if (retained := retained_claims.get(reference)) is not None
        and type(retained.provider) is ProviderObservation
        and type(retained.candidate) is ProviderCandidate
    )
    if not relevant:
        return "series_provider_evidence_required"
    relevant_candidates = {
        (retained.provider.provider, retained.candidate.identity)
        for retained in relevant
    }
    selected_sources = tuple(
        source
        for provider in report.providers
        for source in provider.candidates
        if (provider.provider, source.identity) in relevant_candidates
    )
    if any(
        claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
        and claim.state in {FieldState.MALFORMED, FieldState.OVERSIZED}
        for source in selected_sources
        for claim in source.claims
    ) or any(
        state not in {FieldState.VALID, FieldState.ABSENT, None}
        for source in selected_sources
        for _, state in _series_membership_positions(tuple(
            claim for claim in source.claims
            if claim.kind in {ClaimKind.SERIES, ClaimKind.SERIES_SEQUENCE}
        ))
    ):
        return "series_membership_incomplete"
    contributing_dispatches = {
        (provider.provider, lane.kind, lane.input_references, lane.matched_family)
        for provider in report.providers
        for lane in provider.lanes
        if any(
            (provider.provider, lane_candidate.edition_identity) in relevant_candidates
            for lane_candidate in lane.candidates
        )
    }
    failed_kinds = sorted(
        {
            retained.provider.query_kind
            for retained in relevant
            if not retained.provider.lanes
            and retained.provider.status in failed_statuses
            and retained.provider.query_kind in {ProviderQueryKind.ISBN, ProviderQueryKind.WORK, ProviderQueryKind.ASIN}
        }
        | {
            lane.kind
            for provider in report.providers
            for lane in provider.lanes
            if lane.status in failed_statuses
            and (provider.provider, lane.kind, lane.input_references, lane.matched_family) in contributing_dispatches
        },
        key=lambda kind: kind.value,
    )
    if failed_kinds:
        return f"series_provider_failure_{failed_kinds[0].value}"
    if any(
        claim.kind is ClaimKind.SERIES_SEQUENCE
        and claim.state is not FieldState.VALID
        and not (claim.state is FieldState.ABSENT and claim.value is None)
        for retained in relevant
        if type(claim := retained.claim) is ProviderClaim
    ):
        return "series_membership_incomplete"
    if any(
        not provider_candidate_represents_target_series_work(report, provider, source)
        for provider in report.providers
        for source in provider.candidates
        if (provider.provider, source.identity) in relevant_candidates
    ):
        return "target_work_applicability_unproved"
    return None


def _series_membership_positions(
    claims: tuple[ProviderClaim, ...],
) -> tuple[tuple[str, FieldState | None], ...]:
    """Pair each series name with only its adjacent sequence slot."""
    values: list[tuple[str, FieldState | None]] = []
    index = 0
    while index < len(claims):
        prior = claims[index] if claims[index].kind is ClaimKind.SERIES_SEQUENCE else None
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
                    series_name_equivalence_key(series.value),
                    (
                        FieldState.VALID
                        if sequence is not None
                        and sequence.state is FieldState.VALID
                        and sequence.value is not None
                        else None if sequence is None else sequence.state
                    ),
                )
            )
    return tuple(values)


def _one_list_candidate(
    result: ProposalResult,
    alternatives: tuple[ProposalAlternative, ...] | None = None,
) -> ProposalAlternative | None:
    values = tuple(
        value
        for value in (
            result.alternatives if alternatives is None else alternatives
        )
        if value.value.kind is ProposalValueKind.LIST
    )
    return values[0] if len(values) == 1 else None


def _candidate_reason(result: ProposalResult) -> str:
    if result.outcome in {ProposalOutcome.UNRESOLVED_CHOICE, ProposalOutcome.ACCEPTABLE_CHOICE} or len(result.alternatives) > 1:
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
    result: ProposalResult, kind: ProposalValueKind
) -> tuple[ProposalAlternative | None, str | None]:
    selected = _candidate(result)
    if selected is not None:
        return (selected, None) if selected.value.kind is kind else (None, "no_candidate")
    alternatives = _typed_alternatives(result, kind)
    if len(alternatives) == 1:
        return alternatives[0], None
    return None, "ambiguous_evidence" if len(alternatives) > 1 else "no_candidate"


def _automatic_description_candidate(
    result: ProposalResult,
    evidence: _AutomaticWorkEvidence,
    retained_claims: dict[str, _RetainedClaim],
) -> tuple[ProposalAlternative | None, tuple[str, ...], str | None]:
    candidates: list[tuple[ProposalAlternative, tuple[str, ...]]] = []
    for alternative in evidence.alternatives:
        if alternative.value.kind is not ProposalValueKind.TEXT:
            continue
        references = tuple(
            reference
            for reference in evidence.references[alternative.alternative_id]
            if _ordinary_description_reference(reference, alternative, retained_claims)
        )
        if references:
            candidates.append((alternative, references))
    if len(candidates) <= 1:
        if len(candidates) == 1:
            candidate, references = candidates[0]
            return candidate, references, None
        return None, (), "no_candidate"
    qualified = tuple(
        (candidate, references)
        for candidate, references in candidates
        if type(candidate.value.value) is str
        and len(candidate.value.value) >= _DESCRIPTION_MINIMUM_CHARACTERS
        and len(candidate.value.value.split()) >= _DESCRIPTION_MINIMUM_WORDS
        and not candidate.value.value.rstrip().endswith(
            _DESCRIPTION_VISIBLE_CUTOFFS
        )
    )
    if not qualified:
        return None, (), "description_quality_floor_not_met"
    authority_order = _DESCRIPTION_AUTHORITY_ORDER
    if all(
        retained_claims[reference].candidate is not None
        and any(binding.kind is ProviderEvidenceKind.ASIN_MARKETPLACE
                and binding.scope is ClaimScope.EDITION
                for binding in retained_claims[reference].candidate.evidence)
        for _, references in qualified for reference in references
    ):
        authority_order = (*authority_order, "audible_catalog", "audnexus")
    ranked: list[tuple[int, ProposalAlternative, tuple[str, ...]]] = []
    for candidate, references in qualified:
        by_authority: dict[str, list[str]] = {}
        for reference in references:
            retained = retained_claims[reference]
            if (
                type(retained.provider) is ProviderObservation
                and retained.provider.authority in authority_order
            ):
                by_authority.setdefault(retained.provider.authority, []).append(
                    reference
                )
        if not by_authority:
            return None, (), "ambiguous_evidence"
        authority = min(
            by_authority,
            key=authority_order.index,
        )
        ranked.append(
            (
                authority_order.index(authority),
                candidate,
                tuple(by_authority[authority]),
            )
        )
    best_rank = min(rank for rank, _, _ in ranked)
    winners = tuple(item for item in ranked if item[0] == best_rank)
    if len(winners) != 1:
        return None, (), "ambiguous_evidence"
    _, candidate, references = winners[0]
    return candidate, references, None


def _description(
    field: str,
    option: str,
    target: TargetField,
    result: ProposalResult,
    retained_claims: dict[str, _RetainedClaim],
    automatic_evidence: _AutomaticWorkEvidence | None,
) -> RichFieldPreview:
    source_references: tuple[str, ...] | None = None
    if automatic_evidence is None:
        candidate, reason = _one_typed_candidate(result, ProposalValueKind.TEXT)
    else:
        candidate, source_references, reason = _automatic_description_candidate(
            result,
            automatic_evidence,
            retained_claims,
        )
    if candidate is None:
        return _abstain(field, option, target, _data_value(target.value), reason or "no_candidate")
    if option not in {"safe_plain_text", "google_visible_text", "replace_safe_plain_text"}:
        return _abstain(field, option, target, _data_value(target.value), "policy_option_unavailable")
    candidate_references = (
        candidate.evidence_references
        if source_references is None
        else source_references
    )
    if not candidate_references:
        return _abstain(field, option, target, _data_value(target.value), "option_evidence_unavailable")
    google_references = tuple(
        reference
        for reference in candidate_references
        if _google_description_reference(reference, candidate, retained_claims)
    )
    ordinary_references = tuple(
        reference
        for reference in candidate_references
        if _ordinary_description_reference(reference, candidate, retained_claims)
    )
    if option == "google_visible_text" and not google_references:
        return _abstain(field, option, target, _data_value(target.value), "option_evidence_unavailable")
    if option in {"safe_plain_text", "replace_safe_plain_text"} and not ordinary_references:
        return _abstain(field, option, target, _data_value(target.value), "option_evidence_unavailable")
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
        and retained.claim.value == alternative.value.value
        and type(retained.candidate) is ProviderCandidate
        and ordinary_provider_description_source_is_safe(
            retained.claim, retained.candidate
        )
    )


def _isbn(
    field: str,
    option: str,
    target: TargetField,
    result: ProposalResult,
    report: MetadataReport,
) -> RichFieldPreview:
    if (
        target.state in _EMPTY
        and result.reasons != (VERIFIED_MANIFESTATION_ISBN_REASON,)
    ):
        return _abstain(
            field,
            option,
            target,
            _data_value(target.value),
            "manifestation_verification_required",
        )
    candidate = _selected_verified_manifestation_isbn_candidate(result)
    if candidate is None:
        return _abstain(field, option, target, _data_value(target.value), "no_candidate")
    if option not in {"preserve_exact", "prefer_isbn13"}:
        return _abstain(field, option, target, _data_value(target.value), "policy_option_unavailable")
    value = select_isbn_representation(report, candidate, option)
    if value is None:
        return _abstain(field, option, target, _data_value(target.value), "option_evidence_unavailable")
    return RichFieldPreview(
        field, option, target.state.value, _data_value(target.value),
        "projected", value, VERIFIED_MANIFESTATION_ISBN_REASON, False, None,
        candidate.alternative_id, candidate.evidence_references,
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
        if reference not in accepted:
            continue
        retained = retained_claims.get(reference)
        claim = None if retained is None else retained.claim
        value = getattr(claim, "value", getattr(claim, "canonical", None))
        if (
            claim is None
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
        return _abstain(field, option, target, _data_value(target.value), reason or "no_candidate")
    if option not in {"preserve_exact", "ebook_manifestation"}:
        return _abstain(field, option, target, _data_value(target.value), "policy_option_unavailable")
    if option == "ebook_manifestation" and (
        not candidate.evidence_references
        or any(
            not _identifier_reference(reference, ClaimKind.ASIN, candidate, retained_claims, {"ebook"})
            for reference in candidate.evidence_references
        )
    ):
        return _abstain(field, option, target, _data_value(target.value), "option_evidence_unavailable")
    # The elected alternative and the evidence it cites travel with the
    # preview, because a qualified audiobook ASIN decision is minted from this
    # projection and has to name what it acted on (#967).
    return RichFieldPreview(
        field, option, target.state.value, _data_value(target.value), "projected",
        candidate.value.value, None, False, None,
        candidate.alternative_id, candidate.evidence_references, (),
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
        and getattr(claim, "value", getattr(claim, "canonical", None)) == alternative.value.value
        and getattr(claim, "applicability", None) in applicability
    )


def _preserved(field: str, option: str, target: TargetField, current: object) -> RichFieldPreview:
    return RichFieldPreview(field, option, target.state.value, current, "no_op", current, "target_preserved")


def _abstain(field: str, option: str, target: TargetField, current: object, reason: str) -> RichFieldPreview:
    return RichFieldPreview(field, option, target.state.value, current, "abstain", None, reason)


def _subtitle_bundle(
    field: str,
    option: str,
    target: TargetField,
    result: ProposalResult,
    proposal: ProposalSet,
    report: MetadataReport,
) -> RichFieldPreview:
    """Expose only an admitted title/subtitle combination; never parse title text."""
    alternatives = {value.destination: {alt.alternative_id: alt for alt in value.alternatives} for value in proposal.results}
    matches = {}
    for combination in proposal.bibliographic_combinations:
        selected = {selection.destination: selection for selection in combination.selections}
        title = selected[TargetDestination.TITLE]
        subtitle = selected[TargetDestination.SUBTITLE]
        if title.kind is BibliographicSelectionKind.CANDIDATE and subtitle.kind is BibliographicSelectionKind.CANDIDATE:
            left = alternatives[TargetDestination.TITLE].get(title.alternative_id)
            right = alternatives[TargetDestination.SUBTITLE].get(subtitle.alternative_id)
            if left is not None and right is not None:
                matches[(left.alternative_id, right.alternative_id)] = (left, right)
    if len(matches) != 1:
        return _abstain(field, option, target, _data_value(target.value), "atomic_bundle_unavailable" if not matches else "ambiguous_evidence")
    title_candidate, candidate = next(iter(matches.values()))
    current_title = next(
        value for value in report.target.fields
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
            field, option, target, _data_value(target.value),
            "corroborated_split_unavailable",
        )
    desired = {"title": _data_value(title_candidate.value.value), "subtitle": _data_value(candidate.value.value)}
    return RichFieldPreview(field, option, target.state.value, _data_value(target.value), "projected", desired, None)


def _combination_member(proposal: ProposalSet, destination: TargetDestination) -> ProposalAlternative | None:
    alternatives = {value.destination: {alt.alternative_id: alt for alt in value.alternatives} for value in proposal.results}
    matches = {}
    for combination in proposal.bibliographic_combinations:
        selection = next(value for value in combination.selections if value.destination is destination)
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
        if current_value is not None or candidate_value["kind"] != "series" or type(values) is not list or not values:
            raise RichMetadataPreviewError("rich automatic projection is invalid")
        return {"kind": "series", "value": [values[0]]}
    if field == "isbn_form":
        if candidate_value["kind"] != "text" or type(candidate_value["value"]) is not str:
            raise RichMetadataPreviewError("rich automatic projection is invalid")
        return {"kind": "text", "value": candidate_value["value"]}
    if field == "subtitle":
        if candidate_value["kind"] != "text" or type(candidate_value["value"]) is not str:
            raise RichMetadataPreviewError("rich automatic projection is invalid")
        return {"kind": "text", "value": candidate_value["value"]}
    if field == "description_fill":
        if candidate_value["kind"] != "text" or type(candidate_value["value"]) is not str:
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
            if (
                type(current_value) not in {list, tuple}
                or any(type(value) is not str for value in current_value)
            ):
                raise RichMetadataPreviewError("rich automatic projection is invalid")
            values = [*current_value, *(value for value in values if value not in current_value)]
        if len(values) > MAX_TAGS:
            raise RichMetadataPreviewError("rich automatic projection is oversized")
        return {"kind": "list", "value": values}
    raise RichMetadataPreviewError("rich automatic projection is unavailable")


def project_audiobook_automatic_decision_value(
    destination: str,
    field: str,
    qualified_option: str,
    candidate_value: object,
    current_value: object,
) -> dict[str, object]:
    """Reconstruct the operation before removing its qualification suffix."""
    option = qualified_option.rsplit(":", 1)[0]
    if type(candidate_value) is not dict or set(candidate_value) != {"kind", "value"}:
        raise RichMetadataPreviewError("rich automatic projection is invalid")
    if field == "series" and option in {"add_qualified", "replace_qualified"}:
        values = candidate_value["value"]
        if candidate_value["kind"] != "series" or type(values) is not list or not values:
            raise RichMetadataPreviewError("rich automatic projection is invalid")
        if option == "add_qualified" and current_value is not None:
            current = _data_value(current_value)
            if type(current) not in {list, tuple}:
                raise RichMetadataPreviewError("rich automatic projection is invalid")
            values = _add_series_associations(current, values)
        projected: dict[str, object] = {"kind": "series", "value": values}
    elif field in {"genres", "tags"} and option == "add" and current_value is not None:
        projected = project_f20_automatic_decision_value(
            field, "replace", {
                "kind": candidate_value["kind"],
                "value": _add_audiobook_taxonomy_members(current_value, candidate_value["value"]),
            }, None,
        )
    else:
        projected = project_f20_automatic_decision_value(
            field,
            "safe_plain_text" if option == "replace_safe_plain_text" else option,
            candidate_value,
            current_value,
        )
    identity = identity_for_effect(
        destination, field, option, _data_value(current_value), projected["value"]
    )
    if identity != f"{field}:{qualified_option}":
        raise RichMetadataPreviewError("rich automatic operation is unqualified")
    return projected


def _add_audiobook_taxonomy_members(current: object, desired: object) -> list[str]:
    """Keep incumbent taxonomy spelling when an admitted member is equivalent."""
    if (
        type(current) not in {list, tuple} or type(desired) not in {list, tuple}
        or any(type(item) is not str for item in (*current, *desired))
    ):
        raise RichMetadataPreviewError("rich automatic projection is invalid")
    return [*current, *(item for item in desired if not any(
        values_match(TargetDestination.GENRES, existing, item) for existing in current
    ))]


def _add_series_associations(current: object, desired: object) -> list[object]:
    """Retain existing displays while adding only distinct bound series pairs."""
    if type(current) not in {list, tuple} or type(desired) not in {list, tuple}:
        raise RichMetadataPreviewError("rich automatic projection is invalid")
    def identity(item: object) -> tuple[str, str | None]:
        if (
            type(item) is not dict or set(item) != {"name", "sequence"}
            or type(item["name"]) is not str
            or (item["sequence"] is not None and type(item["sequence"]) is not str)
        ):
            raise RichMetadataPreviewError("rich automatic projection is invalid")
        return series_name_equivalence_key(item["name"]), item["sequence"]
    existing = {identity(item) for item in current}
    return [*current, *(item for item in desired if identity(item) not in existing)]


def _data_value(value: object) -> object:
    if isinstance(value, tuple):
        return tuple(item.to_data() if hasattr(item, "to_data") else item for item in value)
    return value


__all__ = [
    "RichFieldPreview", "RichMetadataPreview", "RichMetadataPreviewError",
    "compile_rich_metadata_preview", "project_f20_automatic_decision_value",
    "select_isbn_representation",
]
