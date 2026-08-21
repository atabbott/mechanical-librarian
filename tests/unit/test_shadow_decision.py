from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest

from mechanical_librarian.domain import shadow_decision as shadow_decision_module
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FieldState,
    LocalClaim,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_decision import (
    DECISION_POLICY_SHA256,
    DECISION_POLICY_VERSION,
    ShadowDecisionError,
    ShadowDecisionSet,
    decide_shadow_packet,
)
from mechanical_librarian.domain.shadow_review import (
    ShadowReviewPacket,
    ShadowReviewValueError,
)
from mechanical_librarian.domain.shadow_review_actions import (
    ACTION_POLICY_VERSION,
    PolicyAction,
    PolicyActionCatalogue,
    PolicyActionCompilationError,
    _action_id,
    _exclude,
    compile_shadow_review_actions,
)
from tests.unit.shadow_packet_fixtures import synthetic_packet
from tests.unit.test_bibliographic_bundles import _bibliographic_report, _source


def _packet(
    destination: str = "title",
    *,
    sufficiency: str = "candidate",
    provider_support: int = 2,
    local_support: int = 1,
    target_only: int = 0,
    candidate_count: int = 1,
) -> ShadowReviewPacket:
    data = json.loads(synthetic_packet().to_json())
    proposal = data["proposals"][0]
    proposal["destination"] = destination
    proposal["decision_summary"] = {
        "evidence_sufficiency": sufficiency,
        "target_only_preservation": target_only,
    }
    candidate = proposal["candidates"][0]
    work_field = destination in {"title", "authors"}
    candidate["scope"] = "work" if work_field else "edition"
    candidate["decision_summary"]["evidence_scope"] = candidate["scope"]
    candidate["decision_summary"]["selected_manifestation_binding"] = (
        "associated_work_only" if work_field else "established"
    )
    candidate["decision_summary"]["support"] = {
        "independent_provider_correlations": provider_support,
        "local_evidence": local_support,
        "target_only_preservation": 0,
    }
    if destination == "publishedDate":
        precision = "day"
        candidate["date_event"] = "manifestation_publication"
        candidate["date_precision"] = precision
        candidate["value"] = {
            "kind": "text",
            "value": "2024-08-04" if precision == "day" else "2024",
        }
        data["citations"][0]["fact"].update(
            {
                "date_event": "manifestation_publication",
                "date_precision": precision,
            }
        )
        candidate["decision_summary"]["publication"] = {
            "destination": destination,
            "event": "manifestation_publication",
            "logical_field": "publication",
            "paired_destination_consistency": "consistent",
            "precision": precision,
            "report_only": True,
            "write_eligibility": "unsupported",
        }
    else:
        candidate["date_event"] = None
        candidate["date_precision"] = None
        candidate["decision_summary"]["publication"] = None
    if candidate_count == 0:
        proposal["candidates"] = []
        data["correlations"] = []
    elif candidate_count == 2:
        second = json.loads(json.dumps(candidate))
        second["candidate_id"] = "alternative_5" + "5" * 63
        proposal["candidates"].append(second)
        data["correlations"][0]["candidate_ids"].append(second["candidate_id"])
    return ShadowReviewPacket(
        data["review_case_id"],
        tuple(data["proposals"]),
        tuple(data["citations"]),
        tuple(data["correlations"]),
        data["schema_version"],
        data["bibliographic_relationship_schema_version"],
        tuple(data["bibliographic_combinations"]),
    )


def _rebuild(data: dict[str, object]) -> ShadowReviewPacket:
    return ShadowReviewPacket(
        data["review_case_id"],
        tuple(data["proposals"]),
        tuple(data["citations"]),
        tuple(data["correlations"]),
        data["schema_version"],
        data["bibliographic_relationship_schema_version"],
        tuple(data["bibliographic_combinations"]),
    )


def _local_title(value: str) -> LocalClaim:
    return LocalClaim.create(
        kind=ClaimKind.TITLE,
        observed=value,
        canonical=value,
        state=FieldState.VALID,
        scope=ClaimScope.WORK,
        basis="package_metadata",
        excerpt=value,
        applicability="not_applicable",
        checksum="not_applicable",
        applicability_basis="not_applicable",
        occurrence=0,
    )


def _combination_packet(
    *,
    current_title: str | None = None,
    providers: tuple | None = None,
) -> ShadowReviewPacket:
    report = _bibliographic_report(
        current_title=current_title,
        local_claims=(_local_title("Synthetic Work"),),
        providers=(
            providers
            if providers is not None
            else (
                _source(
                    "hardcover",
                    "edition-1",
                    titles=("Synthetic Work",),
                    series="Synthetic Series",
                ),
            )
        ),
    )
    proposal = evaluate_report(report)
    case = ReviewCase.create(proposal, report)
    return ShadowReviewPacket.create(case, proposal, report)


def _catalogue_with_witness_variant(
    packet: ShadowReviewPacket,
    *,
    destination: str,
    variant: str,
) -> PolicyActionCatalogue:
    catalogue = compile_shadow_review_actions(packet)
    proposal = next(
        value for value in packet.proposals if value["destination"] == destination
    )
    combination_id = packet.bibliographic_combinations[0]["combination_id"]
    witness = next(
        action
        for action in catalogue.actions
        if action.proposal_id == proposal["proposal_id"]
        and action.recommendation != "manual"
        and combination_id in action.combination_ids
    )
    actions = [value for value in catalogue.actions if value is not witness]
    exclusions = list(catalogue.exclusions)
    if variant == "excluded":
        exclusions.append(_exclude(witness, {"synthetic_witness_exclusion"}))
    elif variant == "unbound":
        actions.append(replace(witness, combination_ids=()))
    elif variant in {"wrong_kind", "wrong_candidate"}:
        candidate_id = "alternative_" + "f" * 64
        recommendation = "recommend_candidate"
        actions.append(
            PolicyAction(
                _action_id(proposal["proposal_id"], recommendation, candidate_id),
                proposal["proposal_id"],
                destination,
                recommendation,
                candidate_id,
                witness.citation_ids,
                witness.combination_ids,
            )
        )
    elif variant != "missing":
        raise AssertionError("unknown witness variant")
    return PolicyActionCatalogue(
        tuple(sorted(actions, key=lambda value: value.action_id)),
        tuple(sorted(exclusions, key=lambda value: value.exclusion_id)),
    )


def _combination_seam(
    packet: ShadowReviewPacket,
    title_actions: tuple[PolicyAction, ...],
    *,
    combinations: tuple[dict[str, object], ...] | None = None,
) -> tuple[object, object, dict[str, tuple[PolicyAction, ...]]]:
    catalogue = compile_shadow_review_actions(packet)
    title_proposal = next(
        value for value in packet.proposals if value["destination"] == "title"
    )
    actions = (
        tuple(
            value
            for value in catalogue.actions
            if value.proposal_id != title_proposal["proposal_id"]
        )
        + title_actions
    )
    seam_packet = SimpleNamespace(
        proposals=packet.proposals,
        bibliographic_combinations=(
            combinations
            if combinations is not None
            else packet.bibliographic_combinations
        ),
    )
    return (
        seam_packet,
        SimpleNamespace(actions=actions),
        {cast(str, title_proposal["proposal_id"]): title_actions},
    )


@pytest.mark.parametrize(
    "destination",
    (
        "title",
        "authors",
        "language",
        "publisher",
    ),
)
def test_supported_fields_select_the_unique_eligible_candidate(destination) -> None:
    packet = _packet(destination)
    result = decide_shadow_packet(packet)
    assert 1 == len(result.decisions)
    decision = result.decisions[0]
    assert "recommend_candidate" == decision.recommendation
    assert "unique_supported_candidate" == decision.reason_code
    assert (
        compile_shadow_review_actions(packet).action_for(
            decision.proposal_id,
            decision.recommendation,
            decision.candidate_id,
        )
        is not None
    )


@pytest.mark.parametrize(
    "destination",
    (
        "authors",
        "language",
        "publisher",
        "publishedDate",
    ),
)
def test_report_only_source_evidence_is_not_blocked_by_write_sufficiency(
    destination,
) -> None:
    packet = _packet(
        destination,
        sufficiency="manual",
        provider_support=2,
        target_only=1,
    )
    decision = decide_shadow_packet(packet).decisions[0]
    assert "recommend_candidate" == decision.recommendation
    assert "unique_supported_candidate" == decision.reason_code


@pytest.mark.parametrize(
    "destination",
    (
        "authors",
        "language",
        "publisher",
        "publishedDate",
    ),
)
def test_report_only_source_evidence_still_requires_local_support(destination) -> None:
    decision = decide_shadow_packet(
        _packet(destination, provider_support=1, local_support=0)
    ).decisions[0]
    assert "manual" == decision.recommendation
    assert "insufficient_support" == decision.reason_code


def test_ambiguous_report_only_source_candidates_abstain() -> None:
    decision = decide_shadow_packet(
        _packet("publisher", provider_support=0, candidate_count=2)
    ).decisions[0]
    assert "manual" == decision.recommendation
    assert "ambiguous_eligible_actions" == decision.reason_code


@pytest.mark.parametrize(
    "destination",
    (
        "subtitle",
        "narrators",
        "series",
        "genres",
        "description",
        "isbn",
        "asin",
        "explicit",
        "tags",
        "cover",
    ),
)
def test_every_unsupported_field_selects_exact_manual_action(destination) -> None:
    result = decide_shadow_packet(_packet(destination))
    decision = result.decisions[0]
    assert "manual" == decision.recommendation
    assert "unsupported_destination" == decision.reason_code


@pytest.mark.parametrize(
    ("packet", "reason"),
    (
        pytest.param(
            _packet(sufficiency="manual"),
            "evidence_sufficiency_manual",
            id="manual_sufficiency",
        ),
        pytest.param(
            _packet(provider_support=0),
            "insufficient_support",
            id="no_provider_support",
        ),
        pytest.param(
            _packet(local_support=0), "insufficient_support", id="no_local_support"
        ),
        pytest.param(
            _packet(target_only=1), "target_only_preservation", id="target_only"
        ),
        pytest.param(
            _packet(candidate_count=0), "no_eligible_action", id="no_candidate"
        ),
        pytest.param(
            _packet(candidate_count=2),
            "ambiguous_eligible_actions",
            id="two_candidates",
        ),
    ),
)
def test_insufficient_conflicting_and_ambiguous_inputs_abstain(packet, reason) -> None:
    decision = decide_shadow_packet(packet).decisions[0]
    assert "manual" == decision.recommendation
    assert reason == decision.reason_code


def test_keep_current_requires_one_evidence_positive_preservation() -> None:
    data = json.loads(_packet().to_json())
    proposal = data["proposals"][0]
    proposal["decision_summary"]["evidence_sufficiency"] = "keep_current"
    proposal["current"] = {"state": "value", "value": "Synthetic title"}
    proposal["candidates"][0]["assessment"] = "preserves_target"
    decision = decide_shadow_packet(_rebuild(data)).decisions[0]
    assert "recommend_keep_current" == decision.recommendation
    assert "unique_supported_preservation" == decision.reason_code

    proposal["candidates"][0]["decision_summary"]["support"][
        "independent_provider_correlations"
    ] = 0
    decision = decide_shadow_packet(_rebuild(data)).decisions[0]
    assert "manual" == decision.recommendation
    assert "insufficient_support" == decision.reason_code


def test_published_date_precision_keeps_the_strongest_qualified_shared_value() -> None:
    month = json.loads(_packet("publishedDate").to_json())
    month_candidate = month["proposals"][0]["candidates"][0]
    month_candidate["date_precision"] = "month"
    month_candidate["value"] = {"kind": "text", "value": "2024-08"}
    month_candidate["decision_summary"]["publication"]["precision"] = "month"
    assert (
        "recommend_candidate"
        == decide_shadow_packet(_rebuild(month)).decisions[0].recommendation
    )
    month["proposals"][0]["candidates"][0]["decision_summary"]["support"][
        "independent_provider_correlations"
    ] = 0
    assert "manual" == decide_shadow_packet(_rebuild(month)).decisions[0].recommendation

    timestamp = json.loads(_packet("publishedDate").to_json())
    timestamp["citations"][0]["fact"]["date_precision"] = "timestamp"
    assert (
        "recommend_candidate"
        == decide_shadow_packet(_rebuild(timestamp)).decisions[0].recommendation
    )

    assert (
        "recommend_candidate"
        == decide_shadow_packet(_rebuild(timestamp)).decisions[0].recommendation
    )


def test_supported_title_projects_through_exact_unsupported_witnesses() -> None:
    packet = _combination_packet()
    assert 1 == len(packet.bibliographic_combinations)
    catalogue = compile_shadow_review_actions(packet)
    decisions = {
        value.destination: value for value in decide_shadow_packet(packet).decisions
    }

    title = decisions["title"]
    assert "recommend_candidate" == title.recommendation
    assert "unique_supported_candidate" == title.reason_code
    selected = catalogue.action_for(
        title.proposal_id,
        title.recommendation,
        title.candidate_id,
    )
    assert selected is not None
    assert selected is not None
    assert selected.action_id == title.action_id
    assert selected.citation_ids == title.citation_ids
    assert selected.combination_ids == title.combination_ids

    for destination in ("subtitle", "series"):
        sibling = decisions[destination]
        manual = catalogue.action_for(sibling.proposal_id, "manual", None)
        assert manual is not None
        assert manual is not None
        assert manual.action_id == sibling.action_id
        assert "manual" == sibling.recommendation
        assert "unsupported_destination" == sibling.reason_code
        assert manual.citation_ids == sibling.citation_ids
        assert () == sibling.combination_ids


def test_supported_title_preservation_projects_through_exact_witnesses() -> None:
    decisions = {
        value.destination: value
        for value in decide_shadow_packet(
            _combination_packet(current_title="Synthetic Work")
        ).decisions
    }
    assert "recommend_keep_current" == decisions["title"].recommendation
    assert "unique_supported_preservation" == decisions["title"].reason_code
    for destination in ("subtitle", "series"):
        assert "manual" == decisions[destination].recommendation
        assert "unsupported_destination" == decisions[destination].reason_code


def test_evidence_backed_title_conflict_selects_only_the_supported_bundle() -> None:
    report = _bibliographic_report(
        current_title="Current Work",
        local_claims=(_local_title("Synthetic Work"),),
        providers=(
            _source(
                "current_catalogue",
                "edition-current",
                titles=("Current Work",),
                series="Synthetic Series",
            ),
            _source(
                "supported_catalogue",
                "edition-supported",
                titles=("Synthetic Work",),
                series="Synthetic Series",
            ),
        ),
    )
    proposal = evaluate_report(report)
    title_result = next(
        value for value in proposal.results if value.destination.value == "title"
    )
    assert "unresolved_choice" == title_result.outcome.value
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )

    title = next(
        value
        for value in decide_shadow_packet(packet).decisions
        if value.destination == "title"
    )

    assert "recommend_candidate" == title.recommendation
    assert "unique_supported_candidate" == title.reason_code
    selected = next(
        candidate
        for proposal_value in packet.proposals
        if proposal_value["proposal_id"] == title.proposal_id
        for candidate in proposal_value["candidates"]
        if candidate["candidate_id"] == title.candidate_id
    )
    assert {"kind": "text", "value": "Synthetic Work"} == selected["value"]


@pytest.mark.parametrize(
    ("destination", "variant"),
    (
        pytest.param("series", "missing", id="series_missing"),
        pytest.param("series", "excluded", id="series_excluded"),
        pytest.param("series", "unbound", id="series_unbound"),
        pytest.param("subtitle", "wrong_kind", id="subtitle_wrong_kind"),
        pytest.param("series", "wrong_candidate", id="series_wrong_candidate"),
    ),
)
def test_missing_excluded_wrong_or_unbound_witness_keeps_title_manual(
    destination, variant
) -> None:
    packet = _combination_packet()
    catalogue = _catalogue_with_witness_variant(
        packet,
        destination=destination,
        variant=variant,
    )
    with patch(
        "mechanical_librarian.domain.shadow_decision.compile_shadow_review_actions",
        return_value=catalogue,
    ):
        decisions = {
            value.destination: value for value in decide_shadow_packet(packet).decisions
        }
    assert "manual" == decisions["title"].recommendation
    assert "bibliographic_combination_unresolved" == decisions["title"].reason_code
    for sibling in ("subtitle", "series"):
        assert "manual" == decisions[sibling].recommendation
        assert "unsupported_destination" == decisions[sibling].reason_code


def test_unbound_eligible_title_does_not_preempt_exact_combination_match() -> None:
    packet = _combination_packet()
    catalogue = compile_shadow_review_actions(packet)
    title_proposal = next(
        value for value in packet.proposals if value["destination"] == "title"
    )
    title_action = next(
        value
        for value in catalogue.actions
        if value.proposal_id == title_proposal["proposal_id"]
        and value.recommendation == "recommend_candidate"
    )
    competing = PolicyAction(
        _action_id(
            title_action.proposal_id,
            "recommend_candidate",
            "alternative_" + "f" * 64,
        ),
        title_action.proposal_id,
        title_action.destination,
        "recommend_candidate",
        "alternative_" + "f" * 64,
        title_action.citation_ids,
        title_action.combination_ids,
    )
    original = shadow_decision_module._eligible_actions

    def ambiguous(
        packet_value: ShadowReviewPacket,
        catalogue_value: PolicyActionCatalogue,
        proposal: dict[str, object],
    ) -> tuple[PolicyAction, ...]:
        eligible = original(packet_value, catalogue_value, proposal)
        return (
            (*eligible, competing) if proposal["destination"] == "title" else eligible
        )

    with patch(
        "mechanical_librarian.domain.shadow_decision._eligible_actions",
        side_effect=ambiguous,
    ):
        title = next(
            value
            for value in decide_shadow_packet(packet).decisions
            if value.destination == "title"
        )
    assert "recommend_candidate" == title.recommendation
    assert "unique_supported_candidate" == title.reason_code


def test_identical_fully_witnessed_combinations_select_title_once() -> None:
    packet = _combination_packet(
        providers=(
            _source(
                "hardcover",
                "edition-1",
                titles=("Synthetic Work",),
                subtitle="Synthetic Subtitle",
            ),
            _source(
                "open_library",
                "edition-2",
                titles=("Synthetic Work",),
                series="Synthetic Series",
            ),
        )
    )
    assert 2 == len(packet.bibliographic_combinations)
    decisions = {
        value.destination: value for value in decide_shadow_packet(packet).decisions
    }
    assert "recommend_candidate" == decisions["title"].recommendation
    assert "unique_supported_candidate" == decisions["title"].reason_code
    assert (
        tuple(value["combination_id"] for value in packet.bibliographic_combinations)
        == decisions["title"].combination_ids
    )


def test_identical_combination_projection_is_provider_order_invariant() -> None:
    providers = (
        _source(
            "hardcover",
            "edition-1",
            titles=("Synthetic Work",),
            subtitle="Synthetic Subtitle",
        ),
        _source(
            "open_library",
            "edition-2",
            titles=("Synthetic Work",),
            series="Synthetic Series",
        ),
    )
    first = decide_shadow_packet(_combination_packet(providers=providers))
    second = decide_shadow_packet(
        _combination_packet(providers=tuple(reversed(providers)))
    )
    assert first.to_json() == second.to_json()


def test_combination_seam_requires_at_least_one_unanimous_full_action() -> None:
    assert (
        "at_least_one"
        == shadow_decision_module._RULE_TABLE["combinations"][
            "fully_witnessed_combination_requirement"
        ]
    )
    packet = _combination_packet(
        providers=(
            _source(
                "hardcover",
                "edition-1",
                titles=("Synthetic Work",),
                subtitle="Synthetic Subtitle",
            ),
            _source(
                "open_library",
                "edition-2",
                titles=("Synthetic Work",),
                series="Synthetic Series",
            ),
        )
    )
    catalogue = compile_shadow_review_actions(packet)
    title = next(
        value
        for value in catalogue.actions
        if value.destination == "title" and value.recommendation != "manual"
    )
    first_id, second_id = (
        value["combination_id"] for value in packet.bibliographic_combinations
    )

    seam_packet, seam_catalogue, eligible = _combination_seam(packet, (title,))
    selected = shadow_decision_module._combination_selections(
        seam_packet, seam_catalogue, eligible
    )
    assert selected is not None
    assert selected is not None
    assert {title.proposal_id: title} == selected[0]
    reversed_packet, reversed_catalogue, reversed_eligible = _combination_seam(
        packet,
        (title,),
        combinations=tuple(reversed(packet.bibliographic_combinations)),
    )
    assert selected == shadow_decision_module._combination_selections(
        reversed_packet, reversed_catalogue, reversed_eligible
    )

    citation_disagreement = (
        replace(title, citation_ids=("citation_a",)),
        replace(title, citation_ids=("citation_b",)),
    )
    seam_packet, seam_catalogue, eligible = _combination_seam(
        packet, citation_disagreement
    )
    assert (
        shadow_decision_module._combination_selections(
            seam_packet, seam_catalogue, eligible
        )
        is None
    )

    preservation_combinations = json.loads(
        json.dumps(packet.bibliographic_combinations)
    )
    preservation_combinations[1]["selections"][0]["kind"] = "preserve_target"
    candidate = title
    preservation = PolicyAction(
        _action_id(title.proposal_id, "recommend_keep_current", None),
        title.proposal_id,
        title.destination,
        "recommend_keep_current",
        None,
        title.citation_ids,
        title.combination_ids,
    )
    seam_packet, seam_catalogue, eligible = _combination_seam(
        packet,
        (candidate, preservation),
        combinations=tuple(preservation_combinations),
    )
    assert (
        shadow_decision_module._combination_selections(
            seam_packet, seam_catalogue, eligible
        )
        is None
    )

    combination_disagreement = (
        replace(title, combination_ids=(first_id,)),
        replace(title, combination_ids=(second_id,)),
    )
    seam_packet, seam_catalogue, eligible = _combination_seam(
        packet, combination_disagreement
    )
    assert (
        shadow_decision_module._combination_selections(
            seam_packet, seam_catalogue, eligible
        )
        is None
    )


@pytest.mark.parametrize(
    "mutate",
    (
        pytest.param(lambda selections: selections.pop(), id="dropped_selection"),
        pytest.param(
            lambda selections: selections.reverse(), id="reordered_selections"
        ),
        pytest.param(
            lambda selections: selections.__setitem__(
                1, json.loads(json.dumps(selections[0]))
            ),
            id="duplicated_selection",
        ),
        pytest.param(
            lambda selections: selections[0].__setitem__("destination", "authors"),
            id="relabelled_destination",
        ),
    ),
)
def test_packet_boundary_rejects_unreachable_combination_shapes(mutate) -> None:
    packet = _combination_packet()
    data = json.loads(packet.to_json())
    mutate(data["bibliographic_combinations"][0]["selections"])
    with pytest.raises(ShadowReviewValueError):
        _rebuild(data)


def test_catalogue_boundary_rejects_duplicate_action_keys() -> None:
    catalogue = compile_shadow_review_actions(_combination_packet())
    with pytest.raises(PolicyActionCompilationError):
        PolicyActionCatalogue(
            tuple(
                sorted(
                    (*catalogue.actions, catalogue.actions[0]),
                    key=lambda value: value.action_id,
                )
            ),
            catalogue.exclusions,
        )


def test_identity_replay_and_document_validation_are_canonical() -> None:
    packet = _packet()
    first = decide_shadow_packet(packet)
    second = decide_shadow_packet(packet)
    assert first.to_json() == second.to_json()
    assert first == ShadowDecisionSet.from_json(first.to_json(), packet)
    assert DECISION_POLICY_VERSION == first.policy_version
    assert DECISION_POLICY_SHA256 == first.policy_sha256
    assert ACTION_POLICY_VERSION == first.compiler_policy_version
    document = json.loads(first.to_json())
    document["decision_set_id"] = "shadow_decision_set_" + "0" * 64
    with pytest.raises(ShadowDecisionError):
        ShadowDecisionSet.from_json(
            json.dumps(document, separators=(",", ":"), sort_keys=True),
            packet,
        )


def test_v8_policy_identity_rejects_v7_replay() -> None:
    assert "deterministic-shadow-decision-policy-v8" == DECISION_POLICY_VERSION
    packet = _packet()
    document = json.loads(decide_shadow_packet(packet).to_json())
    document["policy_version"] = "deterministic-shadow-decision-policy-v6"
    document["policy_sha256"] = (
        "a86201443a027971fdb8943763c1911300a586ecc3fe732b01622a7596649a99"
    )
    core = {key: value for key, value in document.items() if key != "decision_set_id"}
    document["decision_set_id"] = (
        "shadow_decision_set_"
        + hashlib.sha256(
            json.dumps(
                core,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    )
    with pytest.raises(ShadowDecisionError):
        ShadowDecisionSet.from_json(
            json.dumps(
                document,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            packet,
        )


def test_bounded_parser_rejects_noncanonical_and_oversized_documents() -> None:
    packet = _packet()
    serialized = decide_shadow_packet(packet).to_json()
    with pytest.raises(ShadowDecisionError):
        ShadowDecisionSet.from_json(" " + serialized, packet)
    with pytest.raises(ShadowDecisionError):
        ShadowDecisionSet.from_json(serialized + (" " * 1_000_000), packet)
