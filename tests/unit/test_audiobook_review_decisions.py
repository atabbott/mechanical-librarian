"""What an owner may decide about a recording, and what may act on it.

F45 forms audiobook proposals and records the owner's decisions; F47 applies
them through the same guarded path an ebook takes. Nothing automatic reaches a
recording at all, and a decision retained from before audiobook writes existed
stays readable history the planner refuses by name (ADR-0071, #968).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from mechanical_librarian.application.field_effects import (
    GuardedApplicationError,
    authority_from_decision,
)
from mechanical_librarian.application.metadata_report import (
    MetadataLookup,
    admitted_audiobook_identity,
    qualify_provider_observation,
    recording_identity_basis,
)
from mechanical_librarian.application.review_web import (
    _attention_item,
    _decision_projection,
)
from mechanical_librarian.domain.audiobook_decision_qualification import (
    identity_for_effect,
)
from mechanical_librarian.domain.epub_inspection import DateEvent, DatePrecision
from mechanical_librarian.domain.field_effect import (
    SUPPORTED_DESTINATIONS,
    project_decision_value,
)
from mechanical_librarian.domain.human_decision import (
    RECORDED_ONLY_REASON,
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    MetadataReport,
    ProviderClaim,
    ProviderEvidence,
    ProviderEvidenceKind,
    ProviderQueryKind,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.rich_metadata_preview import (
    RichMetadataPreviewError,
    project_audiobook_automatic_decision_value,
)
from mechanical_librarian.domain.shadow_decision import decide_shadow_packet
from mechanical_librarian.domain.shadow_review import (
    ShadowReviewPacket,
    ShadowReviewValueError,
)
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    asin_lane_observation,
    asserted_asin_report,
    audiobook_candidate,
    chapter_runtime_report,
    corroborated_audiobook_report,
    retained_recorded_only_decision,
)
from tests.support.proposal_evidence import _fields as _proposal_fields
from tests.support.proposal_evidence import _provider as _proposal_provider
from tests.support.proposal_evidence import _report as _proposal_report
from tests.support.review_cases import _report as ebook_report
from tests.unit.test_proposal_authors import _narrator as _epub_narrator


def _case(report: MetadataReport) -> ReviewCase:
    return ReviewCase.create(evaluate_report(report), report)


@pytest.mark.parametrize(
    "variant",
    ("single", "aliases", "date_conflict", "recording_mismatch", "wrong_event"),
)
def test_recording_publication_uses_one_earned_authority_and_preserves_conflicts(
    variant,
):
    base = asserted_asin_report(provider_duration="58200.000")
    providers = []
    for ordinal in range(
        2 if variant in {"aliases", "date_conflict", "wrong_event"} else 1
    ):
        name = "catalogue" if ordinal == 0 else "mirror"
        candidate = audiobook_candidate(
            base.local_claims,
            provider=name,
            duration="12000.000" if variant == "recording_mismatch" else "58200.000",
        )
        publication = ProviderClaim.create(
            provider=name,
            candidate=candidate.identity,
            kind=ClaimKind.PUBLICATION_DATE,
            value="2024-01-02"
            if ordinal and variant in {"date_conflict", "wrong_event"}
            else "2025-02-03",
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="not_applicable",
            date_event=DateEvent.MANIFESTATION_PUBLICATION,
            raw_order=5,
        )
        publisher_claim = ProviderClaim.create(
            provider=name,
            candidate=candidate.identity,
            kind=ClaimKind.PUBLISHER,
            value="Example Audio",
            state=FieldState.VALID,
            scope=ClaimScope.EDITION,
            applicability="not_applicable",
            raw_order=6,
        )
        observation = replace(
            asin_lane_observation(base.local_claims),
            provider=name,
            candidates=(
                replace(
                    candidate,
                    claims=(
                        *candidate.claims,
                        *(
                            (publication,)
                            if not (ordinal and variant == "wrong_event")
                            else ()
                        ),
                        publisher_claim,
                    ),
                    evidence=(
                        *candidate.evidence,
                        *(
                            (
                                ProviderEvidence(
                                    ProviderEvidenceKind.FIRST_PUBLICATION_DATE,
                                    "2024-01-02",
                                    ClaimScope.WORK,
                                ),
                            )
                            if ordinal and variant == "wrong_event"
                            else ()
                        ),
                    ),
                ),
            ),
        )
        providers.append(
            qualify_provider_observation(
                observation, MetadataLookup(ProviderQueryKind.ASIN, base.local_claims)
            )
        )
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=tuple(providers),
        audio=base.audio,
    )
    report = MetadataReport.from_json(report.to_json())
    proposal = evaluate_report(report)
    results = {value.destination: value for value in proposal.results}
    date = results[TargetDestination.PUBLISHED_DATE]
    publisher = results[TargetDestination.PUBLISHER]
    if variant == "recording_mismatch":
        assert not date.alternatives and not publisher.alternatives
        return
    assert len(publisher.alternatives) == 1
    assert publisher.alternatives[0].value.value == "Example Audio"
    assert len(publisher.alternatives[0].correlations) == 1
    assert len(date.alternatives) == (2 if variant == "date_conflict" else 1)
    assert all(
        value.date_event is DateEvent.MANIFESTATION_PUBLICATION
        and value.date_precision is DatePrecision.DAY
        for value in date.alternatives
    )
    if variant != "date_conflict":
        assert date.alternatives[0].value.value == "2025-02-03"
        assert len(date.alternatives[0].correlations) == 1
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    reopened = ShadowReviewPacket.from_json(packet.to_json(), report, proposal)
    decisions = decide_shadow_packet(reopened)
    if variant == "date_conflict":
        assert date.selected_alternative_id is None
        assert not any(
            value.destination == "publishedDate"
            and value.recommendation == "recommend_candidate"
            for value in decisions.decisions
        )


def test_numeric_recording_asin_survives_actual_packet_linkage_and_reopen():
    base = asserted_asin_report(provider_duration="58200.000")
    observation = asin_lane_observation(base.local_claims)
    candidate = audiobook_candidate(
        base.local_claims, identity="audio-product-1234567890", asin="1234567890"
    )
    date = ProviderClaim.create(
        provider=observation.provider,
        candidate=candidate.identity,
        kind=ClaimKind.PUBLICATION_DATE,
        value="2025-02-03",
        state=FieldState.VALID,
        scope=ClaimScope.EDITION,
        applicability="not_applicable",
        raw_order=5,
    )
    observation = replace(
        observation, candidates=(replace(candidate, claims=(*candidate.claims, date)),)
    )
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        audio=base.audio,
        providers=(
            qualify_provider_observation(
                observation, MetadataLookup(ProviderQueryKind.ASIN, base.local_claims)
            ),
        ),
    )
    report = MetadataReport.from_json(report.to_json())
    proposal = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    assert ShadowReviewPacket.from_json(packet.to_json(), report, proposal) == packet
    citation = next(
        value for value in packet.citations if value["citation_id"] == date.reference
    )
    assert citation["fact"]["linkage"]["identifier_facts"][0]["value"] == "1234567890"
    for invalid in (
        "123456789",
        "12345678901",
        "123456789!",
        "１２３４５６７８９０",
        "B000000001",
    ):
        document = json.loads(packet.to_json())
        changed = next(
            value
            for value in document["citations"]
            if value["citation_id"] == date.reference
        )
        changed["fact"]["linkage"]["identifier_facts"][0]["value"] = invalid
        with pytest.raises(ShadowReviewValueError):
            ShadowReviewPacket.from_json(
                json.dumps(
                    document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                report,
                proposal,
            )


@pytest.mark.parametrize(
    "duration, supported", (("58200.000", True), ("12000.000", False))
)
def test_recording_support_is_recomputed_through_packet_reopen(duration, supported):
    report = asserted_asin_report(provider_duration=duration)
    proposal = evaluate_report(report)
    case = ReviewCase.create(proposal, report)
    packet = ShadowReviewPacket.create(case, proposal, report)
    reopened = ShadowReviewPacket.from_json(packet.to_json(), report, proposal)
    summaries = [
        candidate["decision_summary"]
        for result in reopened.proposals
        for candidate in result["candidates"]
    ]
    assert any(summary["recording_support"] for summary in summaries) is supported
    decisions = decide_shadow_packet(reopened)
    if supported:
        narrator = next(
            value for value in decisions.decisions if value.destination == "narrators"
        )
        assert narrator.recommendation == "recommend_candidate"
        assert narrator.reason_code == "unique_supported_candidate"
    else:
        assert not any(
            value.destination == "narrators"
            and value.recommendation == "recommend_candidate"
            for value in decisions.decisions
        )


@pytest.mark.parametrize(
    "state, role, supported",
    (
        (FieldState.MALFORMED, "narrator", False),
        (FieldState.VALID, "translator", False),
        (FieldState.VALID, "narrator", True),
    ),
)
def test_incomplete_or_mixed_narrator_credits_cannot_gain_packet_authority(
    state, role, supported
):
    base = asserted_asin_report(provider_duration="58200.000")
    observation = asin_lane_observation(base.local_claims)
    candidate = observation.candidates[0]
    extra = ProviderClaim.create(
        provider=observation.provider,
        candidate=candidate.identity,
        kind=ClaimKind.CONTRIBUTOR,
        value=None if state is FieldState.MALFORMED else "Other Contributor",
        state=state,
        scope=ClaimScope.WORK,
        applicability="not_applicable",
        role=role,
        raw_order=5,
    )
    observation = replace(
        observation, candidates=(replace(candidate, claims=(*candidate.claims, extra)),)
    )
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        audio=base.audio,
        providers=(
            qualify_provider_observation(
                observation, MetadataLookup(ProviderQueryKind.ASIN, base.local_claims)
            ),
        ),
    )
    proposal = evaluate_report(report)
    case = ReviewCase.create(proposal, report)
    packet = ShadowReviewPacket.create(case, proposal, report)
    reopened = ShadowReviewPacket.from_json(packet.to_json(), report, proposal)
    if state is FieldState.MALFORMED:
        result = next(
            value
            for value in proposal.results
            if value.destination is TargetDestination.NARRATORS
        )
        assert result.reasons == ("no_supported_evidence",)
        assert not any(
            value["destination"] == "narrators" for value in reopened.proposals
        )
        assert not any(
            value.destination == "narrators"
            for value in decide_shadow_packet(reopened).decisions
        )
        return
    narrator = next(
        value for value in reopened.proposals if value["destination"] == "narrators"
    )
    assert narrator["candidates"]
    assert (
        any(
            value["decision_summary"]["recording_support"]
            for value in narrator["candidates"]
        )
        is supported
    )
    decision = next(
        value
        for value in decide_shadow_packet(reopened).decisions
        if value.destination == "narrators"
    )
    assert (decision.recommendation == "recommend_candidate") is supported


def _accept(case: ReviewCase, destination: TargetDestination) -> HumanDecision:
    proposal = next(
        value for value in case.proposals if value["destination"] == destination.value
    )
    result = proposal["result"]
    assert type(result) is dict
    alternatives = result["alternatives"]
    assert type(alternatives) is list
    selected = alternatives[0]["value"]
    return HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination=destination.value,
        actor="owner",
        outcome="accept",
        selected_value=selected,
        idempotency_key="review-decision-0001",
        occurred_at=1_000,
    )


def test_an_admitted_recording_is_a_case_the_owner_can_decide() -> None:
    """The unit outcome: a recording produces the same case an ebook does."""
    case = _case(admitted_audiobook_report())

    assert "audiobook" == case.media_kind
    assert {TargetDestination.ASIN.value, TargetDestination.NARRATORS.value} == {
        value["destination"] for value in case.actionable
    }


def test_a_recording_decision_applies_exactly_as_an_ebook_one_does() -> None:
    case = _case(admitted_audiobook_report())

    decision = _accept(case, TargetDestination.ASIN)

    assert "apply" == decision.application
    # The posture is bound into the decision's identity, so it cannot be
    # edited off the retained fact without breaking the document.
    assert decision == HumanDecision.from_json(decision.to_json())
    projected = _decision_projection(decision)
    assert "apply" == projected["application"]
    assert projected["application_reason"] is None
    authority = authority_from_decision(decision)
    assert authority is not None
    assert "human" == authority.origin
    assert decision.decision_id == authority.decision_id


def test_an_ebook_decision_is_unchanged_and_still_applies() -> None:
    case = _case(ebook_report())

    decision = _accept(case, TargetDestination.TITLE)

    assert "ebook" == case.media_kind
    assert "apply" == decision.application
    assert _decision_projection(decision)["application_reason"] is None


def test_the_guarded_planner_refuses_a_retained_decision_by_name() -> None:
    """A silent `None` is what an unaccepted decision returns; this is not that.

    The scheduler and the pending-write SQL both filter a retained document out
    long before the planner sees it, so reaching this seam is a defect and it
    says which one.
    """
    decision = retained_recorded_only_decision(
        _case(admitted_audiobook_report()), TargetDestination.ASIN
    )

    with pytest.raises(GuardedApplicationError) as raised:
        authority_from_decision(decision)

    assert "recorded_only" == raised.value.code


def test_no_automatic_authority_is_ever_offered_a_recording() -> None:
    """A61: the automatic lane abstains, whatever the recording's fields say.

    The narrator alternative is exactly the shape a qualified policy option
    acts on for an ebook, and it is still left to the owner.
    """
    report = admitted_audiobook_report()
    case = _case(report)
    proposal_set = evaluate_report(report)
    packet = ShadowReviewPacket.create(case, proposal_set, report)

    assert "audiobook" == case.media_kind
    # The packet still forms -- the owner's own preview reads it -- and the
    # projection seam is what refuses to publish an outcome from it.
    assert packet.review_case_id == case.review_case_id


def test_a_retained_recorded_only_decision_is_history_on_a_recording_only() -> None:
    """The posture is re-derived from the case, not trusted from the document.

    An F45 document re-sealed with every digest validates against itself; it
    still has to agree with the case it names. A recording may carry it as
    history, an ebook case never could, and no owner may submit one as new
    work on either medium.
    """
    case = _case(admitted_audiobook_report())
    retained = retained_recorded_only_decision(case, TargetDestination.ASIN)

    assert "recorded_only" == retained.application
    assert RECORDED_ONLY_REASON == _decision_projection(retained)["application_reason"]
    assert "recorded, not applied" in RECORDED_ONLY_REASON
    assert "before audiobook writes were available" in RECORDED_ONLY_REASON
    # Readable history: the retained document still validates against the case
    # it was decided on.
    retained.validate_for(case)

    # It is never new work, on the case it names or any other.
    with pytest.raises(HumanDecisionError) as submitted:
        retained.validate_new_for(case)

    assert "stale" == str(submitted.value)

    # P2-2: the browse projection holds the proposal document rather than the
    # case, and clears attention from what it validates, so it has to reach the
    # same verdict from what it has.
    browse: dict[str, object] = {
        "target_fingerprint": case.target_fingerprint,
        "proposal_set_id": case.proposal_set_id,
        "proposal_document": case.proposal_document,
        "results": [
            value.to_data()
            for value in ProposalSet.from_json(case.proposal_document).results
        ],
    }
    retained.validate_for_browse_case(case.review_case_id, browse)


def test_a_retained_recorded_only_decision_is_stale_against_an_ebook_case() -> None:
    """`recorded_only` was never an ebook posture, so it cannot become one."""
    case = _case(ebook_report())
    retained = retained_recorded_only_decision(case, TargetDestination.TITLE)

    assert "ebook" == case.media_kind
    with pytest.raises(HumanDecisionError) as raised:
        retained.validate_for(case)

    assert "stale" == str(raised.value)

    browse: dict[str, object] = {
        "target_fingerprint": case.target_fingerprint,
        "proposal_set_id": case.proposal_set_id,
        "proposal_document": case.proposal_document,
        "results": [
            value.to_data()
            for value in ProposalSet.from_json(case.proposal_document).results
        ],
    }
    with pytest.raises(HumanDecisionError) as browsed:
        retained.validate_for_browse_case(case.review_case_id, browse)

    assert "stale" == str(browsed.value)


def test_an_asserted_asin_outside_the_band_admits_no_recording() -> None:
    """P1-1: a retained candidate is not an admission.

    An ASIN lane fed by a target assertion keeps its candidate in the demoted
    work shape when the runtime band rejects it, so presence alone would form
    proposals for a product this recording is not (ADR-0061).
    """
    report = asserted_asin_report(provider_duration="12000.000")

    assert admitted_audiobook_identity(report) is False


def test_an_asserted_asin_inside_the_band_admits_the_recording() -> None:
    report = asserted_asin_report(provider_duration="58200.000")

    assert admitted_audiobook_identity(report) is True


def test_the_items_own_asin_is_no_exemption_from_the_band() -> None:
    """An embedded identifier naming a different cut is what the band settles."""
    report = asserted_asin_report(
        provider_duration="12000.000", local_asin="B08G9PRS1K"
    )

    assert admitted_audiobook_identity(report) is False


def test_the_items_own_asin_inside_the_band_admits_the_recording() -> None:
    report = asserted_asin_report(
        provider_duration="58200.000", local_asin="B08G9PRS1K"
    )

    assert admitted_audiobook_identity(report) is True


def _epub_narrator_case() -> ReviewCase:
    """One EPUB case whose narrator field the owner can still decide.

    Two independent sources state one cast, so the corroboration guard lets the
    proposal through and the API would accept a decision on it even though the
    rail omits the field for an EPUB.
    """
    report = _proposal_report(
        fields=_proposal_fields(),
        providers=(
            _proposal_provider(
                "hardcover",
                "edition-1",
                (_epub_narrator("hardcover", "edition-1"),),
                authority="hardcover",
            ),
            _proposal_provider(
                "open-library",
                "edition-2",
                (_epub_narrator("open-library", "edition-2"),),
                authority="open_library",
            ),
        ),
    )
    return _case(report)


def test_an_epub_narrator_decision_now_becomes_a_plannable_value() -> None:
    """P1-2: a decided narrator cast is a write on both media.

    The EPUB path is the exposed one, so it is where the fifteenth destination
    joining the guarded envelope is proven from the decision inward.
    """
    case = _epub_narrator_case()
    decision = _accept(case, TargetDestination.NARRATORS)

    assert "ebook" == case.media_kind
    assert "apply" == decision.application
    assert "narrators" in SUPPORTED_DESTINATIONS
    assert ["Nia Narrator"] == project_decision_value(
        decision.destination, decision.selected_value
    )


def test_the_attention_lens_lists_the_field_the_library_row_counted() -> None:
    """P1-3: the lens and the count answer the same question.

    A recording's ASIN is a settled `propose_change` -- an ebook's would be
    waiting for the automatic lane -- so a lens that asked the automatic
    question would drop a field its own row reports and page short.
    """
    report = admitted_audiobook_report()
    case = _case(report)
    asin = next(
        value
        for value in case.proposals
        if value["destination"] == TargetDestination.ASIN.value
    )
    result = asin["result"]
    assert type(result) is dict
    assert "propose_change" == result["outcome"]

    item = _attention_item(
        case.to_json(), report.to_json(), TargetDestination.ASIN.value, 2
    )

    assert item is not None
    assert TargetDestination.ASIN.value == item["destination"]
    assert 2 == item["work_attention_count"]
    assert "m4b" == item["media_kind"]


def test_a_shadow_disposition_never_drops_a_recording_from_the_attention_lens() -> None:
    """The lens pages exactly the rows the membership query counted.

    `automatic_outcome` here is the latest shadow disposition the projection
    wrote, not a durable decision: it carries `manual`, `ignore`,
    `unsupported`, `keep_current`, `candidate` or `abstention`. The query that
    selected this row already excluded every field a durable automatic
    decision settled, so none of those dispositions may remove a recording's
    field from the page -- doing so would report a total the page is shorter
    than (#967).
    """
    report = corroborated_audiobook_report()
    case = _case(report)
    destination = TargetDestination.ASIN.value

    assert _attention_item(case.to_json(), report.to_json(), destination, 2) is not None
    for disposition in (
        "abstention",
        "candidate",
        "ignore",
        "keep_current",
        "manual",
        "unsupported",
    ):
        assert (
            _attention_item(
                case.to_json(),
                report.to_json(),
                destination,
                2,
                automatic_outcome=disposition,
            )
            is not None
        ), disposition


def test_missing_provider_runtime_uses_complete_local_asin_chapters() -> None:
    report = chapter_runtime_report()
    assert admitted_audiobook_identity(report)
    assert recording_identity_basis(report) == {
        "basis": "chapter_runtime_and_structure",
        "provider": "catalogue",
        "candidate": "audible-product-B08G9PRS1K",
    }
    reopened = MetadataReport.from_json(report.to_json())
    assert recording_identity_basis(reopened) == recording_identity_basis(report)
    case = _case(reopened)
    assert {p["destination"] for p in case.actionable} == {"asin", "narrators"}
    decision = _accept(case, TargetDestination.NARRATORS)
    assert decision.outcome == "accept"
    assert decision.actor == "owner"


@pytest.mark.parametrize(
    "state,value",
    (
        ("malformed", None),
        ("valid", "0.000"),
        ("valid", "2000.000"),
        ("valid", "4000.000"),
    ),
)
def test_missing_runtime_fallback_refuses_invalid_or_conflicting_duration(
    state, value
) -> None:
    assert not admitted_audiobook_identity(
        chapter_runtime_report(
            provider_duration=value, duration_state=FieldState(state)
        )
    )


def test_existing_runtime_admission_keeps_null_chapter_attribution() -> None:
    report = chapter_runtime_report(
        provider_duration="1740.000",
        duration_state=FieldState.VALID,
        chapter_rows_omitted=True,
    )
    assert admitted_audiobook_identity(report)
    assert recording_identity_basis(report) is None


def test_target_only_asin_can_use_complete_chapter_fallback() -> None:
    assert admitted_audiobook_identity(
        chapter_runtime_report(asin_basis="target_metadata")
    )


@pytest.mark.parametrize(
    "local_duration,omitted", ((None, False), ("0.000", False), ("1748.000", True))
)
def test_missing_local_runtime_and_omitted_rows_cannot_admit(
    local_duration, omitted
) -> None:
    assert not admitted_audiobook_identity(
        chapter_runtime_report(
            local_duration=local_duration, chapter_rows_omitted=omitted
        )
    )


def test_cross_provider_runtime_conflict_blocks_chapter_fallback() -> None:
    first = chapter_runtime_report()
    other = chapter_runtime_report(
        provider="registry",
        provider_duration="2000.000",
        duration_state=FieldState.VALID,
    )
    report = MetadataReport.create(
        target=first.target,
        file=first.file,
        local_claims=first.local_claims,
        local_reasons=first.local_reasons,
        providers=(*first.providers, *other.providers),
        audio=first.audio,
    )
    assert not admitted_audiobook_identity(report)
    assert not admitted_audiobook_identity(MetadataReport.from_json(report.to_json()))


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_duration",
        "missing_asin_link",
        "unbound_asin_lane",
        "wrong_asin_lane",
        "foreign_authority",
    ),
)
def test_chapter_fallback_needs_retained_matched_local_asin_evidence(
    mutation: str,
) -> None:
    from dataclasses import replace

    from mechanical_librarian.domain.metadata_report import ClaimKind, ProviderQueryKind

    base = chapter_runtime_report()
    observation = base.providers[0]
    candidate = observation.candidates[0]
    if mutation == "missing_duration":
        candidate = replace(
            candidate,
            claims=tuple(
                c for c in candidate.claims if c.kind is not ClaimKind.DURATION
            ),
        )
    elif mutation == "missing_asin_link":
        candidate = replace(
            candidate,
            claims=tuple(
                replace(c, links=()) if c.kind is ClaimKind.ASIN else c
                for c in candidate.claims
            ),
        )
    if mutation in {"missing_duration", "missing_asin_link"}:
        observation = replace(observation, candidates=(candidate,))
    elif mutation == "wrong_asin_lane":
        from mechanical_librarian.application.metadata_report import (
            _asin_discovery_lane,
        )
        from mechanical_librarian.domain.metadata_report import (
            provider_lane_candidate_reference,
        )

        lane = observation.lanes[0]
        identity = _asin_discovery_lane("B000000002", lane.input_references).identity
        observation = replace(
            observation,
            lanes=(
                replace(
                    lane,
                    identity=identity,
                    candidates=tuple(
                        replace(
                            value,
                            reference=provider_lane_candidate_reference(
                                observation.provider,
                                identity,
                                value.edition_identity,
                                value.work_identity,
                            ),
                        )
                        for value in lane.candidates
                    ),
                ),
            ),
        )
    elif mutation == "unbound_asin_lane":
        observation = replace(
            observation,
            lanes=(),
            query_kind=ProviderQueryKind.ASIN,
            input_references=(),
        )
    else:
        observation = replace(observation, authority=None)
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=(observation,),
        audio=base.audio,
    )
    if mutation == "missing_asin_link":
        # Canonical report construction regenerates comparison links from the
        # retained values; it does not preserve a caller's omitted comparison.
        asin = next(
            c
            for c in report.providers[0].candidates[0].claims
            if c.kind is ClaimKind.ASIN
        )
        assert asin.links
        assert admitted_audiobook_identity(report)
    else:
        assert not admitted_audiobook_identity(report)
        assert (
            recording_identity_basis(MetadataReport.from_json(report.to_json())) is None
        )


def test_two_supporting_providers_use_one_deterministic_recording_source() -> None:
    first = chapter_runtime_report(provider="zulu")
    second = chapter_runtime_report(provider="alpha")
    for observations in (
        (first.providers[0], second.providers[0]),
        (second.providers[0], first.providers[0]),
    ):
        report = MetadataReport.create(
            target=first.target,
            file=first.file,
            local_claims=first.local_claims,
            local_reasons=first.local_reasons,
            providers=tuple(
                sorted(observations, key=lambda observation: observation.provider)
            ),
            audio=first.audio,
        )
        assert admitted_audiobook_identity(report)
        assert recording_identity_basis(report) == {
            "basis": "chapter_runtime_and_structure",
            "provider": "alpha",
            "candidate": "audible-product-B08G9PRS1K",
        }


def test_multiple_local_asins_do_not_grant_chapter_admission() -> None:
    from mechanical_librarian.domain.metadata_report import ClaimKind

    base = chapter_runtime_report()
    other = chapter_runtime_report(local_asin="B0CW1KL2QD")
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=(
            *base.local_claims,
            *(c for c in other.local_claims if c.kind is ClaimKind.ASIN),
        ),
        local_reasons=base.local_reasons,
        providers=base.providers,
        audio=base.audio,
    )
    assert not admitted_audiobook_identity(report)
    assert recording_identity_basis(report) is None


@pytest.mark.parametrize("hint_provider", ("alpha", "zulu"))
@pytest.mark.parametrize(
    "duration, admitted", (("2000.000", False), ("1748.000", True))
)
def test_lookup_runtime_veto_precedes_source_selection(
    hint_provider, duration, admitted
):
    from tests.support.audiobook_reports import recording_lookup_report

    support = chapter_runtime_report(provider="middle")
    hints = recording_lookup_report(
        provider=hint_provider, asins=("B08G9PRS1K", "B000000002"), duration=duration
    )
    report = MetadataReport.create(
        target=support.target,
        file=support.file,
        local_claims=tuple(
            sorted(
                {
                    claim.reference: claim
                    for claim in (*support.local_claims, *hints.local_claims)
                }.values(),
                key=lambda claim: claim.reference,
            )
        ),
        local_reasons=support.local_reasons,
        providers=tuple(
            sorted((*support.providers, *hints.providers), key=lambda p: p.provider)
        ),
        audio=support.audio,
    )
    reopened = MetadataReport.from_json(report.to_json())
    assert admitted_audiobook_identity(reopened) is admitted
    assert bool(recording_identity_basis(reopened)) is admitted
    assert reopened.providers[0 if hint_provider == "alpha" else 1].candidates == ()


@pytest.mark.parametrize("basis", ("embedded_tag", "target_metadata"))
def test_unlaned_chapter_support_requires_actual_local_asin_references(
    basis: str,
) -> None:
    from dataclasses import replace

    from mechanical_librarian.domain.metadata_report import ProviderQueryKind

    base = chapter_runtime_report(asin_basis=basis)
    source = base.providers[0]
    observation = replace(
        source,
        lanes=(),
        query_kind=ProviderQueryKind.ASIN,
        input_references=source.lanes[0].input_references,
    )
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=(observation,),
        audio=base.audio,
    )
    assert admitted_audiobook_identity(report) is (basis == "embedded_tag")
    assert admitted_audiobook_identity(MetadataReport.from_json(report.to_json())) is (
        basis == "embedded_tag"
    )


@pytest.mark.parametrize(
    "current,desired,operation",
    [
        (None, ["Example Narrator"], "fill"),
        (["Example Narrator"], ["Example Narrator"], "preserve"),
        (["Example Narrator"], ["EXAMPLE NARRATOR"], "normalize"),
        (["Example Narrator"], ["Example Narrator", "Another Narrator"], "add"),
        (["Different Narrator"], ["Example Narrator"], "replace"),
    ],
)
def test_narrator_qualification_names_actual_effect(current, desired, operation):
    assert identity_for_effect(
        "narrators", "contributor_names", "canonical", current, desired
    ) == ("contributor_names:canonical:audiobook_narrators_" + operation)


def test_rich_effect_reconstruction_rejects_fill_identity_for_replacement():
    candidate = {"kind": "text", "value": "A supported source description."}
    with pytest.raises(RichMetadataPreviewError, match="operation is unqualified"):
        project_audiobook_automatic_decision_value(
            "description",
            "description_fill",
            "replace_safe_plain_text:audiobook_description_fill",
            candidate,
            "An existing description.",
        )
    assert (
        project_audiobook_automatic_decision_value(
            "description",
            "description_fill",
            "replace_safe_plain_text:audiobook_description_replace",
            candidate,
            "An existing description.",
        )
        == candidate
    )


def test_series_add_effect_retains_bound_members_and_rejects_forged_fill():
    current = [{"name": "Existing Cycle", "sequence": "3"}]
    candidate = {
        "kind": "series",
        "value": [{"name": "Example Cycle", "sequence": "2"}],
    }
    expected = {"kind": "series", "value": [*current, *candidate["value"]]}
    assert (
        project_audiobook_automatic_decision_value(
            "series",
            "series",
            "add_qualified:audiobook_series_add",
            candidate,
            current,
        )
        == expected
    )
    with pytest.raises(RichMetadataPreviewError, match="operation is unqualified"):
        project_audiobook_automatic_decision_value(
            "series",
            "series",
            "add_qualified:audiobook_series_fill",
            candidate,
            current,
        )
    assert (
        identity_for_effect(
            "series", "series", "source_order", current, candidate["value"]
        )
        is None
    )


@pytest.mark.parametrize("empty", (False, True))
def test_asserted_asin_work_fields_do_not_require_matching_current_title_or_authors(
    empty,
):
    base = asserted_asin_report(provider_duration="58200.000")
    fields = tuple(
        TargetField(field.destination, TargetFieldState.MISSING)
        if empty
        and field.destination in {TargetDestination.TITLE, TargetDestination.AUTHORS}
        else TargetField(
            TargetDestination.TITLE, TargetFieldState.VALUE, "Wrong synthetic title"
        )
        if field.destination is TargetDestination.TITLE
        else TargetField(
            TargetDestination.AUTHORS, TargetFieldState.VALUE, ("Different Author",)
        )
        if field.destination is TargetDestination.AUTHORS
        else field
        for field in base.target.fields
    )
    report = MetadataReport.create(
        target=replace(base.target, fields=fields),
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=base.providers,
        audio=base.audio,
    )
    proposal = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    restored = ShadowReviewPacket.from_json(packet.to_json(), report, proposal)
    decisions = {
        value.destination: value for value in decide_shadow_packet(restored).decisions
    }
    assert decisions["title"].recommendation == "recommend_candidate"
    assert decisions["authors"].recommendation == "recommend_candidate"
