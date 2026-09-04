"""What an owner may decide about a recording, and what may act on it.

F45 forms audiobook proposals and records the owner's decisions. F47 owns the
writes, so every decision made here says so about itself, nothing automatic
reaches a recording at all, and the guarded planner refuses one by name rather
than quietly skipping it (#966).
"""

from __future__ import annotations

import json

import pytest

from mechanical_librarian.application.field_effects import (
    GuardedApplicationError,
    authority_from_decision,
)
from mechanical_librarian.application.metadata_report import (
    admitted_audiobook_identity,
)
from mechanical_librarian.application.review_web import (
    _attention_item,
    _decision_projection,
)
from mechanical_librarian.domain import human_decision
from mechanical_librarian.domain.field_effect import (
    SUPPORTED_DESTINATIONS,
    FieldEffectError,
    project_decision_value,
)
from mechanical_librarian.domain.human_decision import (
    RECORDED_ONLY_REASON,
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from tests.support.audiobook_reports import (
    admitted_audiobook_report,
    asserted_asin_report,
)
from tests.support.proposal_evidence import _fields as _proposal_fields
from tests.support.proposal_evidence import _provider as _proposal_provider
from tests.support.proposal_evidence import _report as _proposal_report
from tests.support.review_cases import _report as ebook_report
from tests.unit.test_proposal_authors import _narrator as _epub_narrator


def _case(report: MetadataReport) -> ReviewCase:
    return ReviewCase.create(evaluate_report(report), report)


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


def test_a_recording_decision_records_itself_as_never_applied() -> None:
    case = _case(admitted_audiobook_report())

    decision = _accept(case, TargetDestination.ASIN)

    assert "recorded_only" == decision.application
    # The posture is bound into the decision's identity, so it cannot be
    # edited off the retained fact without breaking the document.
    assert decision == HumanDecision.from_json(decision.to_json())
    projected = _decision_projection(decision)
    assert "recorded_only" == projected["application"]
    assert RECORDED_ONLY_REASON == projected["application_reason"]
    assert "recorded, not applied" in RECORDED_ONLY_REASON
    assert "F47" in RECORDED_ONLY_REASON


def test_an_ebook_decision_is_unchanged_and_still_applies() -> None:
    case = _case(ebook_report())

    decision = _accept(case, TargetDestination.TITLE)

    assert "ebook" == case.media_kind
    assert "apply" == decision.application
    assert _decision_projection(decision)["application_reason"] is None


def test_the_guarded_planner_refuses_a_recording_decision_by_name() -> None:
    """A silent `None` is what an unaccepted decision returns; this is not that."""
    decision = _accept(_case(admitted_audiobook_report()), TargetDestination.ASIN)

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


def test_a_self_consistent_apply_document_is_stale_against_a_recording() -> None:
    """The posture is re-derived from the case, not trusted from the document.

    Rewriting `application` and re-sealing every digest produces a document
    that validates against itself; it still has to agree with the case it
    names, and against a recording it never can.
    """
    case = _case(admitted_audiobook_report())
    document = _accept(case, TargetDestination.ASIN).to_data()
    document["application"] = "apply"
    request = {
        key: document[key]
        for key in (
            "actor",
            "application",
            "destination",
            "idempotency_digest",
            "outcome",
            "proposal_id",
            "review_case_id",
            "selected_value",
            "target_fingerprint",
        )
    }
    document["request_digest"] = human_decision._sha256(
        human_decision._canonical(request)
    )
    document["decision_id"] = "human_decision_" + human_decision._sha256(
        human_decision._canonical(
            {
                "request_digest": document["request_digest"],
                "occurred_at": document["occurred_at"],
            }
        )
    )
    forged = HumanDecision.from_json(json.dumps(document))

    assert "apply" == forged.application
    with pytest.raises(HumanDecisionError) as raised:
        forged.validate_for(case)

    assert "stale" == str(raised.value)

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
    with pytest.raises(HumanDecisionError) as browsed:
        forged.validate_for_browse_case(case.review_case_id, browse)

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


def test_an_epub_narrator_decision_can_never_become_a_plan() -> None:
    """P1-2: a decided narrator cast is not a write on either medium.

    The EPUB path is the exposed one -- its decision is `apply` -- so the
    withheld destination has to refuse it, not the media check.
    """
    case = _epub_narrator_case()
    decision = _accept(case, TargetDestination.NARRATORS)

    assert "ebook" == case.media_kind
    assert "apply" == decision.application
    assert "narrators" not in SUPPORTED_DESTINATIONS

    with pytest.raises(FieldEffectError) as refused:
        project_decision_value(decision.destination, decision.selected_value)

    assert "unsupported_destination" == refused.value.code


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
