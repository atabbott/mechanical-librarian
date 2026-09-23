from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    TargetDestination,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalOutcome,
    ProposalSet,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionIncompatibleError,
    ReviewCase,
)
from mechanical_librarian.domain.shadow_review import (
    ShadowReviewPacket,
    ShadowReviewValueError,
    canonical_json,
)
from mechanical_librarian.domain.shadow_review_actions import (
    compile_shadow_review_actions,
)
from tests.support.proposal_evidence import (
    _claim,
    _fields,
    _provider,
    _report,
    consensus_local_title,
    consensus_provider_title,
    retained_decoration_report,
)


def _bibliographic_report(
    *,
    current_title: str | None,
    providers: tuple,
    local_claims: tuple = (),
):
    return _report(
        fields=_fields(
            current_title,
            subtitle=TargetField(
                TargetDestination.SUBTITLE,
                TargetFieldState.MISSING,
            ),
            series=TargetField(
                TargetDestination.SERIES,
                TargetFieldState.MISSING,
            ),
        ),
        local_claims=local_claims,
        providers=providers,
    )


def _source(
    name: str,
    candidate: str,
    *,
    titles: tuple[str, ...],
    edition_title: str | None = None,
    subtitle: str | None = None,
    series: str | None = None,
    sequence: str | None = None,
):
    claims = [
        _claim(
            name,
            candidate,
            ClaimKind.TITLE,
            title,
            scope=ClaimScope.WORK,
            order=index,
        )
        for index, title in enumerate(titles)
    ]
    if edition_title is not None:
        claims.append(
            _claim(
                name,
                candidate,
                ClaimKind.TITLE,
                edition_title,
                scope=ClaimScope.EDITION,
                order=len(claims),
            )
        )
    if subtitle is not None:
        claims.append(
            _claim(
                name,
                candidate,
                ClaimKind.SUBTITLE,
                subtitle,
                scope=ClaimScope.EDITION,
                order=len(claims),
                applicability="ebook",
            )
        )
    if series is not None:
        claims.append(
            _claim(
                name,
                candidate,
                ClaimKind.SERIES,
                series,
                scope=ClaimScope.WORK,
                order=len(claims),
            )
        )
    if sequence is not None:
        claims.append(
            _claim(
                name,
                candidate,
                ClaimKind.SERIES_SEQUENCE,
                sequence,
                scope=ClaimScope.WORK,
                order=len(claims),
            )
        )
    return _provider(
        name,
        candidate,
        tuple(claims),
        authority=(
            name if name in {"google_books", "hardcover", "open_library"} else None
        ),
    )


def _selections(proposal) -> list[dict[str, object]]:
    return [
        selection.to_data()
        for combination in proposal.bibliographic_combinations
        for selection in combination.selections
    ]


def test_title_to_subtitle_is_one_explicit_complete_combination() -> None:
    report = _bibliographic_report(
        current_title="Secret History: A Mistborn Novella",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History",),
                subtitle="A Mistborn Novella",
            ),
        ),
    )

    proposal = evaluate_report(report)

    assert 1 == len(proposal.bibliographic_combinations)
    selections = proposal.bibliographic_combinations[0].selections
    assert tuple(
        (
            TargetDestination.TITLE,
            TargetDestination.SUBTITLE,
            TargetDestination.SERIES,
        )
    ) == tuple(value.destination for value in selections)
    assert "candidate" == selections[0].kind.value
    assert "candidate" == selections[1].kind.value
    assert "preserve_target" == selections[2].kind.value
    assert selections[2].target_field_fingerprint is not None


def test_title_to_series_does_not_duplicate_the_component() -> None:
    report = _bibliographic_report(
        current_title="Secret History: Mistborn 3.5",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History",),
                series="Mistborn",
                sequence="3.5",
            ),
        ),
    )

    proposal = evaluate_report(report)
    selected = _selections(proposal)
    title_ids = {
        value["alternative_id"]
        for value in selected
        if value["destination"] == TargetDestination.TITLE.value
        and value["kind"] == "candidate"
    }
    title = next(
        value
        for value in proposal.results
        if value.destination is TargetDestination.TITLE
    )
    values_by_id = {
        value.alternative_id: value.value.value for value in title.alternatives
    }

    assert {"Secret History"} == {values_by_id[value] for value in title_ids}


def test_independently_supported_clean_title_can_pair_with_series() -> None:
    report = _bibliographic_report(
        current_title="Old title",
        providers=(
            _source(
                "open_library",
                "edition-1",
                titles=("Secret History: A Mistborn Novella",),
                series="Mistborn",
            ),
            _source(
                "google_books",
                "edition-2",
                titles=("Secret History",),
            ),
            _source(
                "hardcover",
                "edition-3",
                titles=("Secret History",),
            ),
        ),
    )

    proposal = evaluate_report(report)
    title = next(
        value
        for value in proposal.results
        if value.destination is TargetDestination.TITLE
    )
    values_by_id = {
        value.alternative_id: value.value.value for value in title.alternatives
    }
    selected_titles = {
        selection.alternative_id
        for combination in proposal.bibliographic_combinations
        for selection in combination.selections
        if selection.destination is TargetDestination.TITLE
        and selection.alternative_id is not None
    }

    assert "Secret History" in {values_by_id[value] for value in selected_titles}
    assert "Secret History: A Mistborn Novella" not in {
        values_by_id[value] for value in selected_titles
    }


@pytest.mark.parametrize(
    "titles",
    (
        ("Collected Stories", "Component Story"),
        ("The Shadow", "La Sombra"),
    ),
    ids=("component_of_collection", "translated_pair"),
)
def test_ambiguous_work_titles_remain_provenance_not_title_candidates(
    titles: tuple[str, ...],
) -> None:
    report = _bibliographic_report(
        current_title="Current manifestation title",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=titles,
                series="Archive",
            ),
        ),
    )
    proposal = evaluate_report(report)
    title = next(
        value
        for value in proposal.results
        if value.destination is TargetDestination.TITLE
    )

    assert all(
        alternative.value.value not in titles for alternative in title.alternatives
    )
    assert set(titles) == {
        claim.value
        for claim in report.providers[0].candidates[0].claims
        if claim.kind is ClaimKind.TITLE
    }


def test_component_work_title_cannot_replace_manifestation_title() -> None:
    report = _bibliographic_report(
        current_title="Collected Stories",
        providers=(
            _source(
                "open_library",
                "edition-1",
                titles=("Component Story",),
                edition_title="Collected Stories",
                series="Archive",
            ),
        ),
    )

    proposal = evaluate_report(report)
    title = next(
        value
        for value in proposal.results
        if value.destination is TargetDestination.TITLE
    )

    assert "Component Story" not in {value.value.value for value in title.alternatives}
    assert "Component Story" in {
        claim.value
        for claim in report.providers[0].candidates[0].claims
        if claim.kind is ClaimKind.TITLE
    }


def test_generic_subtitle_preservation_needs_a_kept_title() -> None:
    report = _report(
        title="Plain Work",
        local_claims=(consensus_local_title("Plain Work"),),
        providers=(
            consensus_provider_title("open_library", "Plain Work", subtitle="A Novel"),
            consensus_provider_title("hardcover", "Other Work"),
        ),
    )

    proposal = evaluate_report(report)

    results = {value.destination: value for value in proposal.results}
    assert ("competing_evidence",) == results[TargetDestination.TITLE].reasons
    assert ("generic_provider_subtitle",) == results[TargetDestination.SUBTITLE].reasons
    assert () == proposal.bibliographic_combinations


def test_no_safe_combination_leaves_affected_results_unresolved() -> None:
    report = _bibliographic_report(
        current_title="Mistborn Secret History",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Mistborn Secret History",),
                series="Mistborn",
            ),
        ),
    )

    proposal = evaluate_report(report)

    assert () == proposal.bibliographic_combinations
    affected = {
        value.destination: value
        for value in proposal.results
        if value.destination in {TargetDestination.TITLE, TargetDestination.SERIES}
    }
    assert all(
        value.outcome is ProposalOutcome.UNRESOLVED_CHOICE
        and value.reasons == ("no_safe_bibliographic_combination",)
        for value in affected.values()
    )


def test_sequence_only_duplication_is_also_withheld() -> None:
    report = _bibliographic_report(
        current_title="Secret History 3.5",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Secret History 3.5",),
                series="Mistborn",
                sequence="3.5",
            ),
        ),
    )

    proposal = evaluate_report(report)

    assert () == proposal.bibliographic_combinations
    assert ("no_safe_bibliographic_combination",) == next(
        value
        for value in proposal.results
        if value.destination is TargetDestination.SERIES
    ).reasons


def test_action_catalogue_partitions_candidates_by_allowed_combination() -> None:
    report = _bibliographic_report(
        current_title="Synthetic Title",
        providers=(
            _source(
                "catalogue",
                "edition-1",
                titles=("Synthetic Title",),
                subtitle="Synthetic Subtitle",
            ),
            _source(
                "other",
                "edition-2",
                titles=("Synthetic Title",),
                series="Synthetic Series",
                sequence="1",
            ),
        ),
    )
    proposal = evaluate_report(report)
    case = ReviewCase.create(proposal, report)
    packet = ShadowReviewPacket.create(case, proposal, report)
    catalogue = compile_shadow_review_actions(packet)
    constrained_candidates = [
        action
        for action in catalogue.actions
        if len(action.combination_ids) == 1
        and action.destination
        in {TargetDestination.SERIES.value, TargetDestination.SUBTITLE.value}
        and (action.recommendation == "recommend_candidate")
    ]
    assert 2 == len(constrained_candidates)
    expected = {value["combination_id"] for value in packet.bibliographic_combinations}
    assert expected == {action.combination_ids[0] for action in constrained_candidates}


def test_no_safe_combination_requires_manual_catalogue_actions() -> None:
    report = _bibliographic_report(
        current_title="Mistborn Secret History",
        providers=(_source("catalogue", "edition-1", titles=(), series="Mistborn"),),
    )
    proposal = evaluate_report(report)
    case = ReviewCase.create(proposal, report)
    title_result = next(
        (
            value
            for value in proposal.results
            if value.destination is TargetDestination.TITLE
        )
    )
    assert () == title_result.alternatives
    assert ProposalOutcome.DEFER is title_result.outcome
    assert all(
        (
            value["manual_required"]
            for value in case.proposals
            if value["destination"]
            in {
                TargetDestination.TITLE.value,
                TargetDestination.SUBTITLE.value,
                TargetDestination.SERIES.value,
            }
        )
    )
    packet = ShadowReviewPacket.create(case, proposal, report)
    assert {
        TargetDestination.TITLE.value,
        TargetDestination.SUBTITLE.value,
        TargetDestination.SERIES.value,
    } == {value["destination"] for value in packet.proposals}
    assert [] == next(
        (
            value["candidates"]
            for value in packet.proposals
            if value["destination"] == TargetDestination.TITLE.value
        )
    )
    title_packet = next(
        (
            value
            for value in packet.proposals
            if value["destination"] == TargetDestination.TITLE.value
        )
    )
    assert {
        "evidence_sufficiency": "manual",
        "target_only_preservation": 1,
    } == title_packet["decision_summary"]
    assert all(
        (
            value["outcome"] == ProposalOutcome.UNRESOLVED_CHOICE.value
            and value["reasons"] == ["no_safe_bibliographic_combination"]
            for value in packet.proposals
        )
    )
    catalogue = compile_shadow_review_actions(packet)
    affected_proposal_ids = {
        value["proposal_id"]
        for value in packet.proposals
        if "no_safe_bibliographic_combination" in cast(list[str], value["reasons"])
    }
    assert not any(
        (
            action.proposal_id in affected_proposal_ids
            and action.recommendation != "manual"
            for action in catalogue.actions
        )
    )
    assert all(
        (
            "no_safe_bibliographic_combination" in exclusion.reasons
            for exclusion in catalogue.exclusions
            if exclusion.proposal_id in affected_proposal_ids
        )
    )


def test_combination_identity_round_trips_canonically() -> None:
    report = _bibliographic_report(
        current_title="Secret History: Mistborn",
        providers=(
            _source(
                "catalogue", "edition-1", titles=("Secret History",), series="Mistborn"
            ),
        ),
    )
    proposal = evaluate_report(report)
    replay = ProposalSet.from_json(proposal.to_json())
    assert proposal == replay
    assert proposal.bibliographic_combinations == replay.bibliographic_combinations


def test_relationships_do_not_change_per_destination_proposal_ids() -> None:
    report = _bibliographic_report(
        current_title="Secret History: Mistborn",
        providers=(
            _source(
                "catalogue", "edition-1", titles=("Secret History",), series="Mistborn"
            ),
        ),
    )
    with_relationships = evaluate_report(report)
    without_relationships = ProposalSet.create(
        report_id=with_relationships.report_id,
        target_fingerprint=with_relationships.target_fingerprint,
        results=with_relationships.results,
        resolved_policy_id=with_relationships.resolved_policy_id,
    )
    assert with_relationships.proposal_set_id != without_relationships.proposal_set_id
    case_with_relationships = ReviewCase.create(with_relationships, report)
    case_without_relationships = ReviewCase.create(without_relationships, report)
    assert (
        case_with_relationships.review_case_id
        != case_without_relationships.review_case_id
    )
    assert {
        value["destination"]: value["proposal_id"]
        for value in case_with_relationships.proposals
    } == {
        value["destination"]: value["proposal_id"]
        for value in case_without_relationships.proposals
    }
    packet_with_relationships = ShadowReviewPacket.create(
        case_with_relationships, with_relationships, report
    )
    packet_without_relationships = ShadowReviewPacket.create(
        case_without_relationships, without_relationships, report
    )
    assert packet_with_relationships.to_json() != packet_without_relationships.to_json()


def test_confirmed_current_title_is_a_preserve_member() -> None:
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
    proposal = evaluate_report(report)
    assert proposal == ProposalSet.from_json(proposal.to_json())
    assert 1 == len(proposal.bibliographic_combinations)
    combination = proposal.bibliographic_combinations[0]
    title_selection = next(
        (
            value
            for value in combination.selections
            if value.destination is TargetDestination.TITLE
        )
    )
    assert "preserve_target" == title_selection.kind.value
    assert title_selection.alternative_id is None
    assert title_selection.target_field_fingerprint is not None
    assert 1 == len(combination.source_representations)
    case = ReviewCase.create(proposal, report)
    assert case == ReviewCase.from_json(case.to_json())
    packet = ShadowReviewPacket.create(case, proposal, report)
    title_packet = next(
        (
            value
            for value in packet.proposals
            if value["destination"] == TargetDestination.TITLE.value
        )
    )
    assert (
        title_packet["current"]["value"]
        != title_packet["candidates"][0]["value"]["value"]
    )
    assert {
        "evidence_sufficiency": "keep_current",
        "target_only_preservation": 0,
    } == title_packet["decision_summary"]
    assert packet == ShadowReviewPacket.from_json(packet.to_json(), report)
    catalogue = compile_shadow_review_actions(packet)
    keep = catalogue.action_for(
        title_packet["proposal_id"], "recommend_keep_current", None
    )
    assert keep is not None
    assert keep is not None
    assert (
        packet.bibliographic_combinations[0]["combination_id"],
    ) == keep.combination_ids


def test_packet_and_actions_bind_one_allowed_combination() -> None:
    report = _bibliographic_report(
        current_title="Secret History: Mistborn",
        providers=(
            _source(
                "catalogue", "edition-1", titles=("Secret History",), series="Mistborn"
            ),
        ),
    )
    proposal = evaluate_report(report)
    case = ReviewCase.create(proposal, report)
    packet = ShadowReviewPacket.create(case, proposal, report)
    assert case == ReviewCase.from_json(case.to_json())
    assert packet == ShadowReviewPacket.from_json(packet.to_json(), report)
    combination = packet.bibliographic_combinations[0]
    catalogue = compile_shadow_review_actions(packet)
    for selection in combination["selections"]:
        if selection["kind"] != "candidate":
            continue
        candidate = next(
            (
                candidate
                for proposal_entry in packet.proposals
                for candidate in proposal_entry["candidates"]
                if candidate["candidate_id"] == selection["alternative_id"]
            )
        )
        assert [combination["combination_id"]] == candidate["decision_summary"][
            "bibliographic_compatibility_ids"
        ]
    forged_packet = json.loads(packet.to_json())
    constrained_candidate = next(
        (
            candidate
            for proposal_entry in forged_packet["proposals"]
            for candidate in proposal_entry["candidates"]
            if candidate["decision_summary"]["bibliographic_compatibility_ids"]
        )
    )
    constrained_candidate["decision_summary"]["bibliographic_compatibility_ids"] = []
    with pytest.raises(ShadowReviewValueError):
        ShadowReviewPacket.from_json(canonical_json(forged_packet), report)
    candidate_actions = [
        action
        for action in catalogue.actions
        if action.recommendation == "recommend_candidate" and action.combination_ids
    ]
    assert candidate_actions
    assert all(
        (
            action.combination_ids == (combination["combination_id"],)
            for action in candidate_actions
        )
    )


@pytest.mark.parametrize(
    "corrupt",
    (
        lambda representations: [*representations, 0],
        lambda representations: list(range(len(representations))),
    ),
    ids=("mixed_members", "all_non_str_members"),
)
def test_combination_representations_reject_mixed_members_as_packet_errors(
    corrupt: Callable[[list[object]], list[object]],
) -> None:
    report = _bibliographic_report(
        current_title="Secret History: Mistborn",
        providers=(
            _source(
                "catalogue", "edition-1", titles=("Secret History",), series="Mistborn"
            ),
        ),
    )
    proposal = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )

    def rebuild(document: dict[str, object]) -> ShadowReviewPacket:
        return ShadowReviewPacket(
            document["review_case_id"],
            tuple(document["proposals"]),
            tuple(document["citations"]),
            tuple(document["correlations"]),
            document["schema_version"],
            document["bibliographic_relationship_schema_version"],
            tuple(document["bibliographic_combinations"]),
        )

    assert packet == rebuild(json.loads(packet.to_json()))
    representations = json.loads(packet.to_json())["bibliographic_combinations"][0][
        "source_representations"
    ]
    # The identity pattern already rejected a list of only non-str members,
    # so only a mixed list reached the sorted comparison and raised there.
    document = json.loads(packet.to_json())
    document["bibliographic_combinations"][0]["source_representations"] = corrupt(
        representations
    )
    with pytest.raises(ShadowReviewValueError):
        rebuild(document)


@pytest.mark.parametrize("component", ("subtitle", "series"))
def test_persistence_replay_and_currentness_include_combinations(component) -> None:
    report = retained_decoration_report(component)
    proposal = evaluate_report(report)
    without_relationship = ProposalSet.create(
        report_id=proposal.report_id,
        target_fingerprint=proposal.target_fingerprint,
        results=proposal.results,
        resolved_policy_id=proposal.resolved_policy_id,
    )
    assert proposal.proposal_set_id != without_relationship.proposal_set_id

    with tempfile.TemporaryDirectory() as directory:
        repository = open_repository(Path(directory) / "reports.sqlite")
        repository.publish(report, lambda: True)
        repository.publish_proposal(proposal)
        assert proposal.to_json() == repository.get_proposal(proposal.proposal_set_id)
        first = json.loads(repository.admit_review_case(proposal))
        replay = json.loads(repository.admit_review_case(proposal))
        assert "admitted" == first["status"]
        assert "replayed" == replay["status"]

        repository.close()
        repository = open_repository(Path(directory) / "reports.sqlite")
        assert repository.get_proposal(proposal.proposal_set_id) == proposal.to_json()
        assert (
            json.loads(repository.admit_review_case(proposal))["review_case_id"]
            == first["review_case_id"]
        )
        repository.publish_proposal(without_relationship)
        with pytest.raises(ReviewAdmissionIncompatibleError):
            repository.admit_review_case(without_relationship)
        repository.close()


@pytest.mark.parametrize("component", ("subtitle", "series"))
def test_decoration_association_preserves_evidence_and_provider_order(component):
    from mechanical_librarian.domain import proposal_set as policy

    report = retained_decoration_report(component)
    proposal = evaluate_report(report)
    reordered_report = _report(
        title=report.target.current_title,
        fields=report.target.fields,
        local_claims=report.local_claims,
        providers=tuple(reversed(report.providers)),
        covers=report.cover_sources,
    )
    assert reordered_report == report
    assert evaluate_report(reordered_report).to_json() == proposal.to_json()
    destination = TargetDestination.TITLE
    targets = {field.destination: field for field in report.target.fields}
    observations = policy._observations(report, destination)
    original = policy._evaluate(
        destination,
        targets[destination],
        observations,
        report,
        policy._dependencies(destination),
    )
    plain = next(
        value for value in original.alternatives if value.value.value == "Plain Work"
    )
    elected, associations = policy._decoration_equivalence_election(
        report, targets, original, observations
    )
    assert elected.alternatives == (plain,)
    assert len(plain.correlations) == 1
    assert associations
    assert set(associations.values()) == {plain.alternative_id}
    assert all(
        value
        in {
            observation.representation
            for observation in observations
            if observation.value.value != "Plain Work"
        }
        for value in associations
    )


@pytest.mark.parametrize("binding", ("missing", "absent_member", "direct_precedence"))
def test_combination_title_lookup_uses_only_surviving_proven_association(binding):
    # These private seams own the ephemeral association contract; no alias is serialized.
    from mechanical_librarian.domain import proposal_set as policy

    report = retained_decoration_report("subtitle")
    proposal = evaluate_report(report)
    targets = {field.destination: field for field in report.target.fields}
    results = {result.destination: result for result in proposal.results}
    observations = {
        destination: policy._observations(report, destination)
        for destination in (
            TargetDestination.TITLE,
            TargetDestination.SUBTITLE,
            TargetDestination.SERIES,
        )
    }
    title = TargetDestination.TITLE
    original = policy._evaluate(
        title, targets[title], observations[title], report, policy._dependencies(title)
    )
    elected, associations = policy._decoration_equivalence_election(
        report, targets, original, observations[title]
    )
    if binding == "missing":
        associations = {}
    elif binding == "absent_member":
        associations = dict.fromkeys(associations, "alternative_" + "0" * 64)
    else:
        results[title] = original
    combinations, _ = policy._bibliographic_combinations(
        targets, results, observations, associations
    )
    assert combinations == ()
    assert (
        elected.alternatives == results[title].alternatives
        if binding != "direct_precedence"
        else len(results[title].alternatives) == 2
    )


@pytest.mark.parametrize("ignored", ("title_layout", "subtitle"))
def test_ignored_members_are_not_resurrected_by_decoration_association(ignored):
    from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile

    destinations = dict(MetadataPolicyProfile.builtin().destinations)
    destinations[ignored] = "ignore"
    if ignored == "subtitle":
        destinations["title_layout"] = "ignore"
    proposal = evaluate_report(
        retained_decoration_report("subtitle"), policy_destinations=destinations
    )
    destination = (
        TargetDestination.TITLE
        if ignored == "title_layout"
        else TargetDestination.SUBTITLE
    )
    result = next(
        value for value in proposal.results if value.destination is destination
    )
    assert result.alternatives == ()
    assert all(
        selection.destination is not destination
        or selection.kind.value == "preserve_target"
        for combination in proposal.bibliographic_combinations
        for selection in combination.selections
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "none",
        "title_candidate",
        "title_not_keep",
        "series_preserve",
        "series_populated",
        "substring",
        "sequence_equal",
        "subtitle_equal",
    ),
)
def test_same_name_exception_is_confined_to_supported_empty_series_name(mutation):
    from dataclasses import replace

    from mechanical_librarian.domain.metadata_report import TargetSeries
    from mechanical_librarian.domain.proposal_set import (
        BibliographicSelectionKind,
        _duplicates_bibliographic_component,
    )
    from tests.support.same_name_series import same_name_series_report

    report = same_name_series_report("ebook")
    proposal = evaluate_report(report)
    results = {result.destination: result for result in proposal.results}
    targets = {field.destination: field for field in report.target.fields}
    selections = list(proposal.bibliographic_combinations[0].selections)
    if mutation == "title_candidate":
        # The guard must never lend this exemption to a replacement selection.
        title = results[TargetDestination.TITLE].alternatives[0]
        selections[0] = replace(
            selections[0],
            kind=BibliographicSelectionKind.CANDIDATE,
            alternative_id=title.alternative_id,
            target_field_fingerprint=None,
        )
    elif mutation == "title_not_keep":
        results[TargetDestination.TITLE] = replace(
            results[TargetDestination.TITLE],
            outcome=ProposalOutcome.UNRESOLVED_CHOICE,
            reasons=("competing_evidence",),
            selected_alternative_id=None,
        )
    elif mutation == "series_preserve":
        targets[TargetDestination.SERIES] = TargetField(
            TargetDestination.SERIES,
            TargetFieldState.VALUE,
            (TargetSeries("First Journey", "1"),),
        )
        selections[2] = replace(
            selections[2],
            kind=BibliographicSelectionKind.PRESERVE_TARGET,
            alternative_id=None,
            target_field_fingerprint="a" * 64,
        )
        # Both preserved values intentionally bypass the existing duplicate guard.
        assert not _duplicates_bibliographic_component(
            tuple(selections), targets, results
        )
        return
    elif mutation == "series_populated":
        targets[TargetDestination.SERIES] = TargetField(
            TargetDestination.SERIES,
            TargetFieldState.VALUE,
            (TargetSeries("Old Order", "1"),),
        )
    elif mutation == "substring":
        targets[TargetDestination.TITLE] = TargetField(
            TargetDestination.TITLE, TargetFieldState.VALUE, "First Journey 1"
        )
    elif mutation == "sequence_equal":
        targets[TargetDestination.TITLE] = TargetField(
            TargetDestination.TITLE, TargetFieldState.VALUE, "1984"
        )
        series = results[TargetDestination.SERIES]
        alternative = series.alternatives[0]
        changed = type(alternative).create(
            replace(
                alternative.value,
                value=(TargetSeries("1984", "1984"),),
            ),
            alternative.assessment,
            alternative.evidence_references,
            alternative.correlations,
            alternative.scope,
        )
        results[TargetDestination.SERIES] = replace(
            series,
            alternatives=(changed,),
            selected_alternative_id=changed.alternative_id,
        )
        selections[2] = replace(selections[2], alternative_id=changed.alternative_id)
    elif mutation == "subtitle_equal":
        targets[TargetDestination.SUBTITLE] = TargetField(
            TargetDestination.SUBTITLE, TargetFieldState.VALUE, "First Journey"
        )
        # A candidate title keeps the original subtitle duplication rule active.
        title = results[TargetDestination.TITLE].alternatives[0]
        selections[0] = replace(
            selections[0],
            kind=BibliographicSelectionKind.CANDIDATE,
            alternative_id=title.alternative_id,
            target_field_fingerprint=None,
        )
    assert _duplicates_bibliographic_component(tuple(selections), targets, results) is (
        mutation != "none"
    )
