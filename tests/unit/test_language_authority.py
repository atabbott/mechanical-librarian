"""Real report-to-persisted-decision language authority regressions."""

import json
from contextlib import closing

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_decision import decide_shadow_packet
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from mechanical_librarian.domain.shadow_review import ShadowReviewPacket
from tests.support.audiobook_reports import publish_audiobook_preview
from tests.support.language_reports import language_report


@pytest.mark.parametrize("audio", (False, True))
@pytest.mark.parametrize("current", (None, "und", "UND", "en-US"))
@pytest.mark.parametrize(
    "values", (("English", "en", "eng", "UND"), ("und",), ("en", "fr"), ("qqq",))
)
def test_language_authority_survives_real_publication(tmp_path, audio, current, values):
    report = language_report(values, current, audio=audio)
    raw = report.to_json()
    proposal = evaluate_report(report)
    result = next(
        value for value in proposal.results if value.destination.value == "language"
    )
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    decision = next(
        (
            value
            for value in decide_shadow_packet(packet).decisions
            if value.destination == "language"
        ),
        None,
    )
    supported = values[0] == "English"
    assert len(result.alternatives) == (
        1 if supported or values == ("qqq",) else 0 if values == ("und",) else 2
    )
    assert all(value.value.value not in {"UND", "und"} for value in result.alternatives)
    assert ("manual" if decision is None else decision.recommendation) == (
        "recommend_keep_current"
        if supported and current == "en-US"
        else "recommend_candidate"
        if supported
        else "manual"
    )
    assert report.to_json() == raw
    path = tmp_path / "language.sqlite"
    with closing(open_repository(path)) as repository:
        case_id, _ = publish_audiobook_preview(
            repository, report, ("language_rendering",)
        )
        outcome = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 4)
        )
        language = next(
            (value for value in outcome.outcomes if value.destination == "language"),
            None,
        )
        if not supported:
            assert language is None or language.disposition not in {
                "candidate",
                "keep_current",
            }
    with closing(open_repository(path)) as repository:
        decisions = tuple(
            json.loads(value) for value in repository.get_review_case_decisions(case_id)
        )
        language_decisions = [
            value for value in decisions if value["destination"] == "language"
        ]
        assert bool(language_decisions) is supported


@pytest.mark.parametrize("audio", (False, True))
@pytest.mark.parametrize("option", ("preserve", "display_name"))
@pytest.mark.parametrize("values", (("English", "en"), ("English",), ("en",)))
@pytest.mark.parametrize("current", (None, "UND", "eng"))
def test_observed_representation_survives_restart(
    tmp_path, audio, option, values, current
):
    from mechanical_librarian.adapters.persistence.field_effect_validation import (
        _automatic_decision_value,
    )
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase
    from mechanical_librarian.domain.automatic_decision import AutomaticDecision
    from mechanical_librarian.domain.metadata_policy import human_review_destinations

    report = language_report(values, current, audio=audio)
    path = tmp_path / "language.sqlite"
    with closing(open_repository(path)) as repository:
        case_id, resolved = publish_audiobook_preview(
            repository,
            report,
            ("language_rendering",),
            selected_options={"language_rendering": option},
        )
        outcome = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 4)
        )
    with closing(open_repository(path)) as repository:
        decision = next(
            AutomaticDecision.from_json(value)
            for value in repository.get_review_case_decisions(case_id)
            if json.loads(value)["destination"] == "language"
        )
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        proposal = evaluate_report(
            report, resolved.resolved_id, human_review_destinations(resolved)
        )
        value = _automatic_decision_value(decision, case, report, proposal, outcome)
        selected = "English" if option == "display_name" else "en"
        if selected not in values:
            selected = values[0]
        expected = None if current == "eng" else {"kind": "text", "value": selected}
        assert value == expected
        detail = InspectCurrentReviewCase(repository).run(case_id)
        field = next(
            value for value in detail["fields"] if value["destination"] == "language"
        )
        assert field["automatic_decision"]["selected_value"] == expected
        event = next(
            value
            for value in InspectCurrentReviewCase(repository).history(case_id)["items"]
            if value.get("decision_id") == decision.decision_id
        )
        assert event["selected_value"] == (
            expected or {"kind": "text", "value": current}
        )
        if audio:
            assert decision.policy_option.endswith(
                "_preserve" if current == "eng" else "_fill"
            )


@pytest.mark.parametrize("provider_fact", (False, True))
@pytest.mark.parametrize("audio", (False, True))
def test_undetermined_evidence_is_authenticated_paged_and_nonselectable(
    tmp_path, audio, provider_fact
):
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    from mechanical_librarian.application.configuration import ReviewWebConfiguration
    from tests.unit.review_app_fixture import create_transport_test_app

    report = language_report(("UND", "und"), audio=audio)
    if provider_fact:
        from mechanical_librarian.domain.metadata_report import (
            ClaimKind,
            ClaimScope,
            MetadataReport,
        )
        from tests.support.proposal_evidence import _claim, _provider

        provider = _provider(
            "language-source",
            "edition",
            (
                _claim(
                    "language-source",
                    "edition",
                    ClaimKind.LANGUAGE,
                    "UND",
                    scope=ClaimScope.EDITION,
                ),
            ),
        )
        report = MetadataReport.create(
            target=report.target,
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers + (provider,),
            audio=report.audio,
        )
    with closing(
        open_repository(tmp_path / "evidence.sqlite", cross_thread=True)
    ) as repository:
        case_id, _ = publish_audiobook_preview(
            repository, report, ("language_rendering",)
        )
        repository.persist_shadow_outcome(case_id, 4)
        password = tmp_path / "owner.hash"
        password.write_text(PasswordHasher().hash("synthetic owner"))
        password.chmod(0o600)
        app = create_transport_test_app(
            repository,
            ReviewWebConfiguration(
                tls_certificate_file="/synthetic/review.crt",
                tls_private_key_file="/synthetic/review.key",
                canonical_origin="https://review.test",
                owner_password_hash_file=str(password),
                session_lifetime_seconds=600,
            ),
            clock=lambda: 10,
        )
        with TestClient(app, base_url="https://review.test") as client:
            assert (
                client.get(f"/api/v1/review-cases/{case_id}/evidence").status_code
                == 401
            )
            assert (
                client.post(
                    "/api/v1/session/login",
                    json={"password": "synthetic owner"},
                    headers={"Origin": "https://review.test"},
                ).status_code
                == 200
            )
            items = []
            cursor = None
            while True:
                response = client.get(
                    f"/api/v1/review-cases/{case_id}/evidence",
                    params={"limit": 1, **({"cursor": cursor} if cursor else {})},
                )
                assert response.status_code == 200
                page = response.json()
                assert page["missing_lineage_count"] == 0
                items.extend(page["items"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            language = [item for item in items if item["destinations"] == ["language"]]
            assert len(language) == 2 + provider_fact
            assert len({item["observation"] for item in language}) == 2 + provider_fact
            for claim in report.local_claims:
                if claim.kind.value == "language":
                    assert (
                        sum(claim.reference in item["observation"] for item in language)
                        == 1
                    )
            assert all(
                "Undetermined" in item["observation"]
                and "not an eligible choice" in item["observation"]
                for item in language
            )
            detail = client.get(f"/api/v1/review-cases/{case_id}").json()
            field = next(
                field
                for field in detail["fields"]
                if field["destination"] == "language"
            )
            assert field["proposed_values"] == []
            assert field["automatic_decision"] is None
            assert not any(
                json.loads(value)["destination"] == "language"
                for value in repository.get_review_case_decisions(case_id)
            )


@pytest.mark.parametrize(
    "reasons",
    (["field_policy_conflict"], ["field_policy_conflict", "competing_evidence"]),
)
def test_compiler_conflict_refuses_even_with_other_packet_reasons(reasons):
    from mechanical_librarian.domain.shadow_review_actions import (
        compile_shadow_review_actions,
    )

    report = language_report(("en", "fr"))
    proposal = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    # The compiler accepts constructed packets; the producer's singleton reasons
    # are not a validated packet invariant.
    language = next(
        item for item in packet.proposals if item["destination"] == "language"
    )
    language["reasons"] = reasons
    catalogue = compile_shadow_review_actions(packet)
    assert all(
        action.recommendation == "manual"
        for action in catalogue.actions
        if action.destination == "language"
    )
    assert (
        next(
            value
            for value in decide_shadow_packet(packet).decisions
            if value.destination == "language"
        ).recommendation
        == "manual"
    )


def test_actual_predecessor_documents_and_sql_refuse_without_mutation(tmp_path):
    import sqlite3
    from pathlib import Path

    from mechanical_librarian.adapters.persistence.metadata_reports import (
        MetadataReportError,
    )
    from mechanical_librarian.domain.metadata_report import (
        MetadataReport,
        ReportValueError,
    )
    from mechanical_librarian.domain.proposal_set import ProposalSet, ProposalValueError
    from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeError

    fixtures = Path(__file__).parents[1] / "fixtures/language-policy-predecessor"
    for name in ("report-v47.json", "audio-report-v47.json", "asin-report-v47.json"):
        raw = (fixtures / name).read_bytes()
        with pytest.raises(ReportValueError):
            MetadataReport.from_json(raw.decode().strip())
        assert (fixtures / name).read_bytes() == raw
    with pytest.raises(ProposalValueError):
        ProposalSet.from_json((fixtures / "proposal-v28.json").read_text().strip())
    with pytest.raises(ShadowOutcomeError):
        ShadowOutcomeSet.from_json((fixtures / "outcome-v7.json").read_text().strip())
    path = tmp_path / "predecessor.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript((fixtures / "reports-v47.sql").read_text())
    path.chmod(0o600)
    before = path.read_bytes()
    entries = sorted(item.name for item in tmp_path.iterdir())
    with pytest.raises(MetadataReportError):
        open_repository(path)
    assert path.read_bytes() == before
    assert sorted(item.name for item in tmp_path.iterdir()) == entries


@pytest.mark.parametrize("audio", (False, True))
def test_summary_counts_local_language_once_without_recording_authority(audio):
    from mechanical_librarian.domain.reviewer_decision_summary import (
        _manifestation_binding,
    )

    report = language_report(("English", "en", "eng"), audio=audio)
    proposal = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    candidate = next(
        value for value in packet.proposals if value["destination"] == "language"
    )["candidates"][0]
    assert candidate["decision_summary"]["support"] == {
        "local_evidence": 1,
        "independent_provider_correlations": 0,
        "target_only_preservation": 0,
    }
    assert candidate["decision_summary"]["recording_support"] is False
    assert (
        candidate["decision_summary"]["selected_manifestation_binding"] == "established"
    )
    assert (
        _manifestation_binding(
            report, dict(candidate) | {"evidence_references": ["local_" + "0" * 64]}
        )
        == "unresolved"
    )
    work_reference = next(
        claim.reference for claim in report.local_claims if claim.scope.value == "work"
    )
    assert (
        _manifestation_binding(
            report, dict(candidate) | {"evidence_references": [work_reference]}
        )
        == "unresolved"
    )


@pytest.mark.parametrize(
    "option, expected", (("preserve", "en"), ("display_name", "en"))
)
def test_representation_cannot_borrow_name_outside_candidate_references(
    option, expected
):
    from mechanical_librarian.domain.proposal_set import (
        ProposalAlternative,
        select_language_representation,
    )

    report = language_report(("en", "English"))
    result = next(
        value
        for value in evaluate_report(report).results
        if value.destination.value == "language"
    )
    candidate = result.alternatives[0]
    code_reference = next(
        claim.reference for claim in report.local_claims if claim.canonical == "en"
    )
    bounded = ProposalAlternative.create(
        candidate.value,
        candidate.assessment,
        (code_reference,),
        candidate.correlations,
        candidate.scope,
    )
    assert select_language_representation(report, bounded, option) == expected
    forged = ProposalAlternative.create(
        candidate.value,
        candidate.assessment,
        ("local_" + "0" * 64,),
        candidate.correlations,
        candidate.scope,
    )
    assert select_language_representation(report, forged, option) is None


def test_audio_creator_roles_and_target_only_binding_close_exact_citations():
    from mechanical_librarian.domain.metadata_report import ClaimKind, MetadataReport
    from mechanical_librarian.domain.reviewer_decision_summary import (
        _manifestation_binding,
    )
    from tests.support.audiobook_providers import audio_claim, target_asin_claim

    base = language_report(("en",), audio=True)
    creators = (
        audio_claim(ClaimKind.CREATOR, "Example Author", 10, role="author"),
        audio_claim(ClaimKind.CONTRIBUTOR, "Example Translator", 11, role="translator"),
        audio_claim(ClaimKind.CONTRIBUTOR, "Example Narrator", 12, role="narrator"),
    )
    target_claim = target_asin_claim("B000000001")
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=base.local_claims + creators + (target_claim,),
        local_reasons=base.local_reasons,
        providers=(),
        audio=base.audio,
    )
    proposal = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    candidate = next(
        item for item in packet.proposals if item["destination"] == "authors"
    )["candidates"][0]
    roles = candidate["decision_summary"]["creator_roles"]
    assert {item["reference"] for item in roles["included"]} == {creators[0].reference}
    assert {item["reference"] for item in roles["excluded"]} == {
        claim.reference for claim in creators[1:]
    }
    assert {claim.reference for claim in creators} <= {
        item["citation_id"] for item in packet.citations
    }
    assert candidate["decision_summary"]["support"]["local_evidence"] == 1
    assert candidate["decision_summary"]["recording_support"] is False
    assert (
        _manifestation_binding(
            report,
            dict(candidate)
            | {"scope": "edition", "evidence_references": [target_claim.reference]},
        )
        == "unresolved"
    )


@pytest.mark.parametrize("audio", (False, True))
@pytest.mark.parametrize("reverse", (False, True))
@pytest.mark.parametrize(
    "values, positive",
    (
        (("English", "english", "en", "eng", "en-US", "en-GB"), True),
        (("en", "fr", "en"), False),
        (("zh-Hans", "zh-Hant"), False),
        (("en", "en-Latn"), False),
        (("EN-US",), False),
        (("x-private",), False),
        (("not a language!",), False),
        (("mul", "zxx"), False),
    ),
)
def test_language_closed_identity_controls_survive_compiler(
    audio, reverse, values, positive
):
    values = tuple(reversed(values)) if reverse else values
    report = language_report(values, audio=audio)
    proposal = evaluate_report(report)
    result = next(
        value for value in proposal.results if value.destination.value == "language"
    )
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    decision = next(
        value
        for value in decide_shadow_packet(packet).decisions
        if value.destination == "language"
    )
    assert decision.recommendation == ("recommend_candidate" if positive else "manual")
    if positive:
        assert len(result.alternatives) == 1
        assert set(result.alternatives[0].evidence_references) == {
            claim.reference
            for claim in report.local_claims
            if claim.kind.value == "language"
        }
    assert (
        tuple(
            claim.observed
            for claim in report.local_claims
            if claim.kind.value == "language"
        )
        == values
    )


@pytest.mark.parametrize("audio", (False, True))
@pytest.mark.parametrize("destination", ("ask", "ignore"))
def test_language_ask_ignore_never_publishes_automatic_authority(
    tmp_path, audio, destination
):
    from tests.support.language_reports import publish_language_case

    with closing(open_repository(tmp_path / "policy.sqlite")) as repository:
        case_id, _ = publish_language_case(
            repository, language_report(("English", "en"), audio=audio), destination
        )
        assert not any(
            json.loads(raw)["destination"] == "language"
            for raw in repository.get_review_case_decisions(case_id)
        )
        outcome = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 10)
        )
        language = next(
            (value for value in outcome.outcomes if value.destination == "language"),
            None,
        )
        assert language is None or language.disposition not in {
            "candidate",
            "keep_current",
        }


@pytest.mark.parametrize("audio", (False, True))
def test_language_owner_choice_remains_final_after_automatic_replay(tmp_path, audio):
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase
    from mechanical_librarian.domain.human_decision import HumanDecision
    from tests.support.language_reports import publish_language_case

    path = tmp_path / "owner.sqlite"
    with closing(open_repository(path)) as repository:
        case_id, _ = publish_language_case(
            repository,
            language_report(("English", "en"), audio=audio),
            option="preserve",
        )
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        proposal = next(
            value for value in case.proposals if value["destination"] == "language"
        )
        assert type(proposal["proposal_id"]) is str
        selected = proposal["result"]["alternatives"][0]["value"]
        assert selected == {"kind": "text", "value": "English"}
        decision = HumanDecision.create(
            case,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination="language",
            actor="owner",
            outcome="accept",
            selected_value=selected,
            idempotency_key="language-owner-final",
            occurred_at=20,
        )
        repository.record_human_decision(decision)
        repository.persist_shadow_outcome(case_id, 21)
    with closing(open_repository(path)) as repository:
        field = next(
            value
            for value in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if value["destination"] == "language"
        )
        assert field["human_decision"]["selected_value"] == selected
        assert field["human_decision"]["decision_id"] == decision.decision_id


def test_audio_provider_aliases_remain_one_correlation_beside_local_source():
    from mechanical_librarian.domain.metadata_report import ClaimKind, MetadataReport
    from mechanical_librarian.domain.reviewer_decision_summary import (
        _known_correlations,
    )
    from tests.support.audiobook_providers import audio_claim
    from tests.support.audiobook_reports import qualified_audiobook

    base = language_report(("English", "en"), audio=True)
    local = base.local_claims + (
        audio_claim(ClaimKind.CREATOR, "Andy Weir", 10, role="author"),
    )
    providers = tuple(
        qualified_audiobook(local, provider=name)
        for name in ("catalogue-one", "catalogue-two")
    )
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=local,
        local_reasons=base.local_reasons,
        providers=providers,
        audio=base.audio,
    )
    _, correlations = _known_correlations(report)
    assert len(correlations) == 1
    proposal = evaluate_report(report)
    packet = ShadowReviewPacket.create(
        ReviewCase.create(proposal, report), proposal, report
    )
    authors = next(
        value for value in packet.proposals if value["destination"] == "authors"
    )
    assert authors["candidates"][0]["decision_summary"]["support"] == {
        "local_evidence": 1,
        "independent_provider_correlations": 1,
        "target_only_preservation": 0,
    }
