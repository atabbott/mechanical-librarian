from __future__ import annotations

import copy
import inspect
import json
from collections.abc import Callable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from mechanical_librarian.domain.cover_evidence import (
    CoverArtifact,
    CoverDesignation,
    CoverProvenance,
    CoverSource,
    CoverSourceKind,
    CoverSourceState,
    cover_source_id,
)
from mechanical_librarian.domain.metadata_report import TargetDestination
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_review import (
    ShadowReviewPacket,
)
from mechanical_librarian.domain.shadow_review_actions import (
    _DESTINATION_POLICIES,
    ACTION_CATALOGUE_SCHEMA,
    ACTION_POLICY_VERSION,
    PolicyActionCatalogue,
    PolicyActionCompilationError,
    compile_shadow_review_actions,
)
from tests.support.proposal_evidence import _report
from tests.unit.shadow_packet_fixtures import synthetic_packet
from tests.unit.test_bibliographic_bundles import (
    _bibliographic_report,
    _source,
)


def _packet_data() -> dict[str, object]:
    return synthetic_packet().to_data()


def _packet(data: dict[str, object]) -> ShadowReviewPacket:
    return ShadowReviewPacket(
        data["review_case_id"],
        tuple(data["proposals"]),
        tuple(data["citations"]),
        tuple(data["correlations"]),
        data["schema_version"],
        data["bibliographic_relationship_schema_version"],
        tuple(data["bibliographic_combinations"]),
    )


def test_destination_policy_is_exhaustive() -> None:
    assert {destination.value for destination in TargetDestination} == set(_DESTINATION_POLICIES)


@settings(max_examples=30, deadline=None)
@given(st.binary(min_size=32, max_size=32))
def test_action_and_exclusion_ids_ignore_opaque_case_digest(
    digest: bytes,
) -> None:
    first = _packet_data()
    first["proposals"][0]["candidates"][0]["assessment"] = "unknown"
    second = json.loads(json.dumps(first))
    second["review_case_id"] = "review_case_" + digest.hex()

    first_catalogue = compile_shadow_review_actions(_packet(first))
    second_catalogue = compile_shadow_review_actions(_packet(second))

    assert [value.action_id for value in first_catalogue.actions] == [value.action_id for value in second_catalogue.actions]
    assert [value.exclusion_id for value in first_catalogue.exclusions] == [value.exclusion_id for value in second_catalogue.exclusions]


def test_compiler_boundary_accepts_only_the_packet() -> None:
    assert ["packet"] == list(inspect.signature(compile_shadow_review_actions).parameters)


def test_catalogue_rejects_forged_contract_versions() -> None:
    catalogue = compile_shadow_review_actions(
        synthetic_packet()
    )

    with pytest.raises(PolicyActionCompilationError):
        PolicyActionCatalogue(
            catalogue.actions,
            catalogue.exclusions,
            ACTION_POLICY_VERSION,
            ACTION_CATALOGUE_SCHEMA + "-forged",
        )


def test_manual_proposal_compiles_candidate_and_explicit_manual_independently() -> None:
    packet = synthetic_packet()

    catalogue = compile_shadow_review_actions(packet)

    actions = {
        (value.recommendation, value.candidate_id)
        for value in catalogue.actions
    }
    candidate_id = packet.proposals[0]["candidates"][0]["candidate_id"]
    assert {
            ("manual", None),
            ("recommend_candidate", candidate_id),
        } == actions
    assert "manual" == packet.proposals[0]["decision_summary"]["evidence_sufficiency"]


def test_cover_candidate_uses_cover_scope_and_not_applicable_binding() -> None:
    artifact = CoverArtifact.create(
        b"cover",
        b"preview",
        "image/png",
        10,
        20,
    )
    source = CoverSource(
        cover_source_id(CoverSourceKind.HARDCOVER, "catalogue:cover"),
        CoverSourceKind.HARDCOVER,
        CoverSourceState.RETRIEVED,
        artifact,
        provenance=CoverProvenance(
            "catalogue",
            CoverDesignation.WORK,
            "catalogue-work",
        ),
    )
    report = _report(covers=(source,))
    proposal_set = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal_set, report),
        proposal_set,
        report,
    )
    cover_proposal = next(
        proposal
        for proposal in packet.proposals
        if proposal["destination"] == TargetDestination.COVER.value
    )

    catalogue = compile_shadow_review_actions(packet)

    assert catalogue.action_for(
            cover_proposal["proposal_id"],
            "recommend_candidate",
            cover_proposal["candidates"][0]["candidate_id"],
        ) is not None


def test_keep_current_rejects_unbound_preservation_evidence() -> None:
    data = _packet_data()
    proposal = data["proposals"][0]
    proposal["current"] = {
        "destination": proposal["destination"],
        "state": "value",
        "value": "Current title",
    }
    candidate = proposal["candidates"][0]
    candidate["assessment"] = "preserves_target"
    candidate["correlations"] = []

    catalogue = compile_shadow_review_actions(_packet(data))

    assert catalogue.action_for(
            proposal["proposal_id"],
            "recommend_keep_current",
            None,
        ) is None
    keep_exclusion = next(
        exclusion
        for exclusion in catalogue.exclusions
        if exclusion.recommendation == "recommend_keep_current"
    )
    assert "correlation_binding_missing" in keep_exclusion.reasons


def test_unknown_candidate_fails_closed_with_stable_exclusion_identity() -> None:
    first = _packet_data()
    first["proposals"][0]["candidates"][0]["assessment"] = {
        "confidence": "synthetic",
        "problems": [],
    }
    second = json.loads(json.dumps(first))
    second["review_case_id"] = "review_case_" + ("f" * 64)

    first_catalogue = compile_shadow_review_actions(_packet(first))
    second_catalogue = compile_shadow_review_actions(_packet(second))

    assert ["manual"] == [value.recommendation for value in first_catalogue.actions]
    first_exclusion = next(
        value
        for value in first_catalogue.exclusions
        if value.recommendation == "recommend_candidate"
    )
    second_exclusion = next(
        value
        for value in second_catalogue.exclusions
        if value.recommendation == "recommend_candidate"
    )
    assert ("assessment_unknown",) == first_exclusion.reasons
    assert first_exclusion.exclusion_id == second_exclusion.exclusion_id
    assert first_catalogue.actions[0].action_id == second_catalogue.actions[0].action_id


def test_language_keep_current_uses_compatible_cited_source_evidence() -> None:
    data = _packet_data()
    proposal = data["proposals"][0]
    proposal["destination"] = "language"
    proposal["current"] = {
        "destination": "language",
        "state": "value",
        "value": "EN-US",
    }
    candidate = proposal["candidates"][0]
    candidate["assessment"] = "replaces_target"
    candidate["scope"] = "edition"
    candidate["value"] = {"kind": "text", "value": "EN-US"}
    candidate["decision_summary"]["evidence_scope"] = "edition"
    candidate["decision_summary"]["selected_manifestation_binding"] = (
        "established"
    )

    catalogue = compile_shadow_review_actions(_packet(data))

    keep = next(
        value
        for value in catalogue.actions
        if value.recommendation == "recommend_keep_current"
    )
    assert tuple(candidate["evidence_references"]) == keep.citation_ids


def test_preserving_candidate_binds_to_preserve_combination() -> None:
    report = _bibliographic_report(
        current_title="secret history",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History",),
                subtitle="A Mistborn Novella",
            ),
        ),
    )
    proposal_set = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal_set, report),
        proposal_set,
        report,
    )

    catalogue = compile_shadow_review_actions(packet)

    candidate_action = next(
        value
        for value in catalogue.actions
        if value.destination == "title"
        and value.recommendation == "recommend_candidate"
    )
    assert (packet.bibliographic_combinations[0]["combination_id"],) == candidate_action.combination_ids


def test_catalogue_is_stable_under_equivalent_packet_ordering() -> None:
    report = _bibliographic_report(
        current_title="Secret History: Mistborn",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History",),
                series="Mistborn",
            ),
        ),
    )
    proposal_set = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal_set, report),
        proposal_set,
        report,
    )
    reordered = copy.deepcopy(packet)
    for attribute in (
        "proposals",
        "citations",
        "correlations",
        "bibliographic_combinations",
    ):
        object.__setattr__(
            reordered,
            attribute,
            tuple(reversed(getattr(reordered, attribute))),
        )

    assert compile_shadow_review_actions(packet).to_json() == compile_shadow_review_actions(reordered).to_json()


def test_excluded_candidate_compiles_only_manual_action() -> None:
    data = _packet_data()
    candidate = data["proposals"][0]["candidates"][0]
    candidate["assessment"] = {"confidence": "synthetic", "problems": []}

    catalogue = compile_shadow_review_actions(_packet(data))

    proposal_id = data["proposals"][0]["proposal_id"]
    assert catalogue.action_for(
            proposal_id, "recommend_candidate", candidate["candidate_id"]
        ) is None
    assert catalogue.action_for(proposal_id, "manual", None) is not None
    assert any(value.proposal_id == proposal_id for value in catalogue.exclusions)


def test_candidate_action_binds_complete_citation_closure() -> None:
    data = _packet_data()
    candidate = data["proposals"][0]["candidates"][0]
    second_citation = "synthetic_citation_" + ("9" * 64)
    data["citations"].append(
        {**data["citations"][0], "citation_id": second_citation}
    )
    candidate["evidence_references"].append(second_citation)

    action = compile_shadow_review_actions(_packet(data)).action_for(
        data["proposals"][0]["proposal_id"],
        "recommend_candidate",
        candidate["candidate_id"],
    )

    assert action is not None
    assert tuple(candidate["evidence_references"]) == action.citation_ids


def test_manual_action_needs_no_manufactured_citations() -> None:
    data = _packet_data()
    data["proposals"][0]["candidates"] = []
    data["citations"] = []
    data["correlations"] = []

    action = compile_shadow_review_actions(_packet(data)).action_for(
        data["proposals"][0]["proposal_id"], "manual", None
    )

    assert action is not None
    assert () == action.citation_ids


@pytest.mark.parametrize(
    ("destination", "scope", "binding"),
    (
        ("authors", "work", "associated_work_only"),
        ("language", "edition", "established"),
        ("publisher", "edition", "established"),
    ),
    ids=("authors", "language", "publisher"),
)
def test_admission_is_crossed_with_destination_not_historical_frequency(
    destination: str, scope: str, binding: str
) -> None:
    data = _packet_data()
    proposal = data["proposals"][0]
    proposal["destination"] = destination
    candidate = proposal["candidates"][0]
    candidate["scope"] = scope
    candidate["decision_summary"]["evidence_scope"] = scope
    candidate["decision_summary"]["selected_manifestation_binding"] = binding

    catalogue = compile_shadow_review_actions(_packet(data))

    assert any(
        value.recommendation == "recommend_candidate"
        for value in catalogue.actions
    )


def _unknown_assessment_control() -> dict[str, object]:
    title = _packet_data()
    title["proposals"][0]["candidates"][0]["assessment"] = "unknown"
    return title


def _absent_support_control() -> dict[str, object]:
    isbn = _packet_data()
    isbn_proposal = isbn["proposals"][0]
    isbn_proposal["destination"] = "isbn"
    isbn_candidate = isbn_proposal["candidates"][0]
    isbn_candidate["scope"] = "edition"
    isbn_candidate["decision_summary"]["evidence_scope"] = "edition"
    isbn_candidate["decision_summary"][
        "selected_manifestation_binding"
    ] = "established"
    isbn_candidate["decision_summary"]["support"]["local_evidence"] = 0
    return isbn


def _missing_correlation_control() -> dict[str, object]:
    publication = _packet_data()
    publication_proposal = publication["proposals"][0]
    publication_proposal["destination"] = "publishedDate"
    publication_candidate = publication_proposal["candidates"][0]
    publication_candidate["scope"] = "edition"
    publication_candidate["value"] = {"kind": "text", "value": "2001-02-03"}
    publication_candidate["date_event"] = "manifestation_publication"
    publication_candidate["date_precision"] = "day"
    publication_candidate["correlations"] = []
    publication_candidate["decision_summary"]["evidence_scope"] = "edition"
    publication_candidate["decision_summary"][
        "selected_manifestation_binding"
    ] = "established"
    publication_candidate["decision_summary"]["publication"] = {
        "destination": "publishedDate",
        "event": "manifestation_publication",
        "logical_field": "publication",
        "paired_destination_consistency": "consistent",
        "precision": "day",
        "report_only": True,
        "write_eligibility": "unsupported",
    }
    return publication


@pytest.mark.parametrize(
    ("control", "reason"),
    (
        (_unknown_assessment_control, "assessment_unknown"),
        (_absent_support_control, "evidence_support_absent"),
        (_missing_correlation_control, "correlation_binding_missing"),
    ),
    ids=("title", "isbn", "publishedDate"),
)
def test_negative_controls_fail_closed_in_structurally_feasible_fields(
    control: Callable[[], dict[str, object]], reason: str
) -> None:
    catalogue = compile_shadow_review_actions(_packet(control()))
    assert not any(
        value.recommendation == "recommend_candidate"
        for value in catalogue.actions
    )
    assert any(reason in value.reasons for value in catalogue.exclusions)
