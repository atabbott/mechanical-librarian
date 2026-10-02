"""ISBN-linked title election and candidate-specific decision authority."""

import json

import pytest

from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.proposal_set import (
    EVIDENCE_LED_TITLE_REASON,
    ProposalSet,
    evaluate_report,
)
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_decision import decide_shadow_packet
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from mechanical_librarian.domain.shadow_review_actions import (
    compile_shadow_review_actions,
)
from tests.support.evidence_led_titles import isbn_title_report


@pytest.mark.parametrize(
    "languages,local_language,eligible",
    [
        (("English", "en"), "UND", True),
        (("en", "und"), None, True),
        (("English", "fr"), None, False),
        (("zh-Hans", "zh-Hant"), None, False),
        (("zh-Hans-CN", "zh-Hans-SG"), None, True),
        (("zh", "zh-Hans"), None, True),
        (("zh-Hans",), "zh-Hant", False),
        (("zh-Hans-CN",), "zh-Hans-SG", True),
        (("zh-Hans",), "zh", True),
    ],
)
def test_title_variant_preserves_explicit_script_but_not_missing_script_dissent(
    tmp_path, languages, local_language, eligible
) -> None:
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from tests.support.evidence_led_titles import publish_title_case

    report = MetadataReport.from_json(
        isbn_title_report(
            titles=tuple("Example Title" for _ in languages),
            provider_languages=languages,
            local_language=local_language,
        ).to_json()
    )
    repository = open_repository(tmp_path / "state.sqlite3")
    try:
        _, proposal = publish_title_case(repository, report)
        title = next(r for r in proposal.results if r.destination.value == "title")
        assert title.reasons == (
            EVIDENCE_LED_TITLE_REASON if eligible else "isbn_title_material_conflict",
        )
        packet = ShadowReviewPacket.create(
            ReviewCase.create(proposal, report), proposal, report
        )
        packet = ShadowReviewPacket.from_json(packet.to_json(), report, proposal)
        decision = next(
            d
            for d in decide_shadow_packet(packet).decisions
            if d.destination == "title"
        )
        assert decision.recommendation == (
            "recommend_candidate" if eligible else "manual"
        )
        assert repository._connection.execute(
            "SELECT count(*) FROM automatic_decisions WHERE destination='title'"
        ).fetchone()[0] == int(eligible)
    finally:
        repository.close()


@pytest.mark.parametrize("provider_title", ["omitted", "malformed"])
@pytest.mark.parametrize("content_title", [None, "Example Title"])
def test_absent_valid_provider_title_retains_existing_local_preservation(
    tmp_path, provider_title, content_title
) -> None:
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase
    from mechanical_librarian.domain.metadata_report import FieldState
    from mechanical_librarian.domain.proposal_set import ProposalOutcome
    from tests.support.evidence_led_titles import publish_title_case

    report = MetadataReport.from_json(
        isbn_title_report(
            current="Example Title",
            titles=(None,) if provider_title == "omitted" else ("Example Title",),
            title_state=FieldState.MALFORMED
            if provider_title == "malformed"
            else FieldState.VALID,
            content_title=content_title,
        ).to_json()
    )
    repository = open_repository(tmp_path / "state.sqlite3")
    try:
        case_id, proposal = publish_title_case(repository, report)
        title = next(r for r in proposal.results if r.destination.value == "title")
        assert title.outcome is ProposalOutcome.KEEP
        assert title.reasons == ("positive_preservation_evidence",)
        assert title.selected_alternative_id is None
        assert not repository._connection.execute(
            "SELECT document FROM automatic_decisions WHERE destination='title'"
        ).fetchall()
        assert any(
            field["destination"] == "title"
            for field in InspectCurrentReviewCase(repository).run(case_id)["fields"]
        )
    finally:
        repository.close()


@pytest.mark.parametrize(
    "options,eligible",
    [
        ({"empty_target_title": True}, False),
        ({"target_authors": ("Other Author",)}, False),
        ({"target_authors": ("Ada Author", "Ada Author")}, False),
        (
            {
                "local_authors": ("Ada Author", "Bea Author"),
                "provider_authors": ("Bea Author", "Ada Author"),
                "target_authors": ("Ada Author", "Bea Author"),
            },
            False,
        ),
        (
            {
                "local_authors": ("Ada Author", "Bea Author"),
                "provider_authors": ("Ada Author", "Bea Author"),
                "target_authors": ("Bea Author", "Ada Author"),
            },
            False,
        ),
        ({"provider_authors": ("Ada Author", "Ada Author")}, False),
        ({"local_authors": ("Ada Author", "Ada Author")}, False),
        ({"local_authors": ()}, False),
        ({"opening_authors": ("Other Author",)}, False),
        ({"local_authors": (), "opening_authors": ("Ada Author",)}, False),
        ({"opening_authors": ("ADA AUTHOR", "Ada Author")}, True),
        ({"target_authors": ()}, True),
        ({"target_authors": ("ADA  AUTHOR",)}, True),
        ({"target_authors": ("Author, Ada",)}, True),
        ({"provider_authors": ("Author, Ada",)}, True),
        (
            {
                "local_authors": ("Ada Author", "Bea Author"),
                "provider_authors": ("Ada Author", "Bea Author"),
                "target_authors": ("Ada Author", "Bea Author"),
            },
            True,
        ),
    ],
)
def test_title_replacement_preserves_complete_creator_identity(
    tmp_path, options, eligible
):
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase
    from mechanical_librarian.domain.automatic_decision import AutomaticDecision
    from tests.support.evidence_led_titles import publish_title_case

    report = MetadataReport.from_json(isbn_title_report(**options).to_json())
    repository = open_repository(tmp_path / "state.sqlite3")
    try:
        case_id, proposal = publish_title_case(repository, report)
        title = next(r for r in proposal.results if r.destination.value == "title")
        assert (EVIDENCE_LED_TITLE_REASON in title.reasons) is eligible
        rows = repository._connection.execute(
            "SELECT document FROM automatic_decisions WHERE destination='title'"
        ).fetchall()
        assert len(rows) == int(eligible)
        if eligible:
            assert (
                AutomaticDecision.from_json(rows[0][0]).policy_option
                == "replace_target:isbn_creator_supported"
            )
        assert any(
            f["destination"] == "title"
            for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
        )
    finally:
        repository.close()


@pytest.mark.parametrize("mode", ["automatic", "ask", "ignore"])
@pytest.mark.parametrize("owner_outcome", ["keep_current", "accept"])
def test_title_policy_reaches_durable_authority_and_owner_choice(
    tmp_path, mode, owner_outcome
):
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.application.review_web import (
        InspectCurrentReviewCase,
        RecordHumanDecision,
    )
    from mechanical_librarian.domain.automatic_decision import AutomaticDecision
    from tests.support.evidence_led_titles import publish_title_case

    report = isbn_title_report()
    repository = open_repository(tmp_path / "state.sqlite3")
    try:
        case_id, proposal = publish_title_case(repository, report, mode)
        rows = repository._connection.execute(
            "SELECT document FROM automatic_decisions WHERE destination='title'"
        ).fetchall()
        if mode == "automatic":
            assert len(rows) == 1
            decision = AutomaticDecision.from_json(rows[0][0])
            assert (
                decision.policy_category,
                decision.policy_option,
                decision.bundle_id,
            ) == ("title", "replace_target:isbn_creator_supported", None)
            source = repository.field_effect_authority_source(decision)
            assert source["decision_value"] == {
                "kind": "text",
                "value": "Example Title",
            }
            assert source["destination"] == "title"
            repository.persist_shadow_outcome(case_id, 5)
            assert (
                repository._connection.execute(
                    "SELECT document FROM automatic_decisions WHERE destination='title'"
                ).fetchall()
                == rows
            )
        else:
            assert rows == []
        if mode == "ignore":
            assert next(
                r for r in proposal.results if r.destination.value == "title"
            ).reasons == ("policy_ignored",)
        else:
            field = next(
                f
                for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
                if f["destination"] == "title"
            )
            RecordHumanDecision(repository, lambda: 6).run(
                case_id,
                proposal_id=field["proposal_id"],
                target_fingerprint=field["target_fingerprint"],
                destination="title",
                actor="owner",
                outcome=owner_outcome,
                selected_value={"kind": "text", "value": "Example Title"}
                if owner_outcome == "accept"
                else None,
                idempotency_key="title-owner-final-0001",
            )
            repository.persist_shadow_outcome(case_id, 7)
            after = next(
                f
                for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
                if f["destination"] == "title"
            )
            assert after["human_decision"]["outcome"] == owner_outcome
            if mode == "automatic":
                from mechanical_librarian.application.field_effects import (
                    authority_from_decision,
                )

                authority = authority_from_decision(decision)
                assert authority is not None
                assert not repository.field_effect_authority_is_current(authority)
                assert all(
                    decision.decision_id not in document
                    for document in repository.pending_field_effect_decision_documents(
                        100
                    )
                )
    finally:
        repository.close()


@pytest.mark.parametrize("paused", [True, False])
def test_title_automatic_admission_respects_pause_and_title_layout_suspension(
    tmp_path, paused
):
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )
    from mechanical_librarian.domain.automatic_decision import AutomaticSafetyState
    from tests.support.evidence_led_titles import publish_title_case

    repository = open_repository(tmp_path / "state.sqlite3")
    try:
        safety = AutomaticSafetyState(
            paused, None, () if paused else (("title_layout", "test"),)
        )
        publish_title_case(repository, isbn_title_report(), safety=safety)
        assert repository._connection.execute(
            "SELECT COUNT(*) FROM automatic_decisions WHERE destination='title'"
        ).fetchone() == (0,)
    finally:
        repository.close()


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"current": "Arbitrary local title without a recognized grammar"},
        {"isbns": (("9780306406157", "ebook"), ("9780140328721", "print"))},
        {"titles": ("Example Title", "Example Title")},
        {"failed": True},
        {"titles": ("Example Title", "Example Title"), "reverse": True},
        {"role": "translator"},
        {"role": "adapter"},
        {"role": "abridger"},
    ],
)
def test_supported_title_is_independent_and_retains_alternatives(options):
    report = MetadataReport.from_json(isbn_title_report(**options).to_json())
    proposal = evaluate_report(report)
    assert ProposalSet.from_json(proposal.to_json()) == proposal
    result = next(r for r in proposal.results if r.destination.value == "title")
    assert result.reasons == (EVIDENCE_LED_TITLE_REASON,)
    assert len(result.alternatives) == 2
    assert not proposal.bibliographic_combinations
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    assert ShadowReviewPacket.from_json(packet.to_json(), report, proposal) == packet
    title = next(p for p in packet.proposals if p["destination"] == "title")
    elected = [
        c for c in title["candidates"] if c["decision_summary"]["independent_title"]
    ]
    assert len(elected) == 1
    assert elected[0]["candidate_id"] == result.selected_alternative_id
    actions = compile_shadow_review_actions(packet)
    action = actions.action_for(
        title["proposal_id"], "recommend_candidate", result.selected_alternative_id
    )
    assert action is not None and not action.combination_ids and action.citation_ids
    decision = next(
        d for d in decide_shadow_packet(packet).decisions if d.destination == "title"
    )
    assert decision.recommendation == "recommend_candidate"
    assert decision.candidate_id == result.selected_alternative_id
    assert decision.reason_code == "unique_supported_candidate"


@pytest.mark.parametrize(
    "options",
    [
        *(
            {"current": "Example Title", "titles": titles, "subtitle": subtitle}
            for titles in (
                ("Example Title", "Example Title", "Other Work"),
                ("Other Work", "Example Title", "Example Title"),
                ("Example Title", "Other Work", "Example Title"),
            )
            for subtitle in (None, "A Subtitle")
        ),
        {"author": "Different Author"},
        {"second_work": True},
        {"titles": ("Example Title", "Other Work")},
        {"titles": ("Example Title", "Example Title", "Other Work")},
        {"content_title": "A Different Work"},
        {"isbns": (("9780306406157", "ambiguous"),), "rear_isbn": True},
        {"titles": ("Example Title", "Example Title: An Abridgement")},
        {"titles": ("Example Title", "Example Title: A Translation")},
        {"titles": ("Example Title", "Example Title: An Adaptation")},
        pytest.param(
            {
                "current": "Example Work: Prologue",
                "content_title": "Example Work: Prologue",
                "titles": ("Example Work",),
            },
            id="package-prologue-versus-full-work",
        ),
        pytest.param(
            {"titles": ("Example Work", "Example Work: An Excerpt")},
            id="full-work-versus-excerpt",
        ),
        pytest.param(
            {"titles": ("Example Work", "Example Work: Omnibus")},
            id="full-work-versus-omnibus",
        ),
        pytest.param(
            {"titles": ("Example Work", "Example Work", "Example Work: Preview")},
            id="provider-majority-versus-preview",
        ),
    ],
)
def test_material_conflict_cannot_gain_title_authority(options):
    report = MetadataReport.from_json(isbn_title_report(**options).to_json())
    proposal = evaluate_report(report)
    result = next(r for r in proposal.results if r.destination.value == "title")
    assert result.reasons != (EVIDENCE_LED_TITLE_REASON,)
    assert result.selected_alternative_id is None
    assert len(result.alternatives) >= 2
    if "titles" in options:
        assert result.outcome.value == "unresolved_choice"
        assert result.reasons == ("isbn_title_material_conflict",)
        assert set(options["titles"]) <= {a.value.value for a in result.alternatives}
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    packet = ShadowReviewPacket.from_json(packet.to_json(), report, proposal)
    assert all(
        not c["decision_summary"]["independent_title"]
        for p in packet.proposals
        for c in p["candidates"]
    )
    decision = next(
        d for d in decide_shadow_packet(packet).decisions if d.destination == "title"
    )
    assert decision.recommendation == "manual"
    assert decision.candidate_id is None
    if result.reasons == ("isbn_title_material_conflict",):
        assert decision.reason_code == "isbn_title_material_conflict"
    assert any(
        c["evidence_references"]
        for p in packet.proposals
        if p["destination"] == "title"
        for c in p["candidates"]
    )


def test_forged_title_reference_is_rejected_by_report_parser():
    value = json.loads(isbn_title_report().to_json())
    value["providers"][0]["candidates"][0]["claims"][0]["reference"] = (
        "provider_" + "0" * 64
    )
    with pytest.raises(ValueError):
        MetadataReport.from_json(json.dumps(value))


@pytest.mark.parametrize("role", ["translator", "adapter", "abridger"])
def test_same_title_with_competing_variant_creators_is_withheld(role):
    report = isbn_title_report(
        titles=("Example Title", "Example Title"), role=role, variant_conflict=True
    )
    result = next(
        r for r in evaluate_report(report).results if r.destination.value == "title"
    )
    assert result.reasons == ("isbn_title_material_conflict",)


@pytest.mark.parametrize("role", ["translator", "adapter", "abridger"])
@pytest.mark.parametrize(
    "local_variant", [None, "Bea Contributor", "Different Contributor"]
)
@pytest.mark.parametrize("additional_variant", [None, "Another Contributor"])
def test_local_variant_identity_is_retained_and_only_explicit_disagreement_withholds(
    role, local_variant, additional_variant
):
    report = MetadataReport.from_json(
        isbn_title_report(
            role=role,
            local_variant=local_variant,
            additional_variant=additional_variant,
        ).to_json()
    )
    proposal = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    assert ShadowReviewPacket.from_json(packet.to_json(), report, proposal) == packet
    decision = next(
        d for d in decide_shadow_packet(packet).decisions if d.destination == "title"
    )
    assert decision.recommendation == (
        "manual" if local_variant == "Different Contributor" else "recommend_candidate"
    )
    assert any(
        c.role == role and c.value == "Bea Contributor"
        for p in report.providers
        for v in p.candidates
        for c in v.claims
    )
    if local_variant is not None:
        assert any(
            c.role == role and c.canonical == local_variant for c in report.local_claims
        )


def test_independent_title_keeps_a_meaningful_subtitle_source_association():
    report = isbn_title_report(
        current="Example Title: A Subtitle", subtitle="A Subtitle"
    )
    proposal = evaluate_report(report)
    results = {r.destination.value: r for r in proposal.results}
    title_id = results["title"].selected_alternative_id
    subtitle_id = results["subtitle"].selected_alternative_id
    assert subtitle_id is not None
    association = next(
        c
        for c in proposal.bibliographic_combinations
        if any(s.alternative_id == subtitle_id for s in c.selections)
    )
    assert association.source_representations
    assert any(s.alternative_id == title_id for s in association.selections)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    field = next(p for p in packet.proposals if p["destination"] == "title")
    action = compile_shadow_review_actions(packet).action_for(
        field["proposal_id"], "recommend_candidate", title_id
    )
    assert action is not None and action.combination_ids == ()
    assert any(
        c["combination_id"] == association.combination_id
        for c in packet.bibliographic_combinations
    )


@pytest.mark.parametrize(
    "titles",
    [("Example Title", "example title"), ("The Author's Work", "The Author’s Work")],
)
def test_cosmetic_title_variation_preserves_one_original_independent_of_order(titles):
    selected = []
    for reverse in (False, True):
        report = isbn_title_report(titles=titles, reverse=reverse)
        proposal = evaluate_report(report)
        result = next(r for r in proposal.results if r.destination.value == "title")
        assert result.reasons == (EVIDENCE_LED_TITLE_REASON,)
        chosen = next(
            a
            for a in result.alternatives
            if a.alternative_id == result.selected_alternative_id
        )
        assert chosen.value.value in titles
        selected.append((chosen.alternative_id, chosen.value.value))
        packet = ShadowReviewPacket.create(
            ReviewCase.create(proposal, report), proposal, report
        )
        decision = next(
            d
            for d in decide_shadow_packet(packet).decisions
            if d.destination == "title"
        )
        assert decision.recommendation == "recommend_candidate"
        assert decision.candidate_id == chosen.alternative_id
        assert {
            c.value
            for p in report.providers
            for v in p.candidates
            for c in v.claims
            if c.kind.value == "title"
        } == set(titles)
    assert selected[0] == selected[1]


@pytest.mark.parametrize(
    "mutation", ["missing", "integer", "removed", "other", "reason"]
)
def test_independent_title_fact_cannot_be_missing_or_reassigned(mutation):
    report = isbn_title_report()
    proposal = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    value = json.loads(packet.to_json())
    title = next(p for p in value["proposals"] if p["destination"] == "title")
    selected = next(
        c for c in title["candidates"] if c["decision_summary"]["independent_title"]
    )
    if mutation == "missing":
        del selected["decision_summary"]["independent_title"]
    elif mutation == "integer":
        selected["decision_summary"]["independent_title"] = 1
    elif mutation == "removed":
        selected["decision_summary"]["independent_title"] = False
    elif mutation == "other":
        for candidate in title["candidates"]:
            candidate["decision_summary"]["independent_title"] = not candidate[
                "decision_summary"
            ]["independent_title"]
    else:
        title["reasons"] = ["single_supported_alternative"]
    with pytest.raises(ValueError):
        ShadowReviewPacket.from_json(json.dumps(value), report, proposal)
