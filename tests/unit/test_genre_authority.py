"""Actual report-to-persisted-genre authority, including restart and history."""

import json
from contextlib import closing

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.metadata_report import MetadataReport
from tests.support.genre_reports import genre_report, publish_genre_case


@pytest.mark.parametrize(
    "author,target_author",
    (
        ("Ada Example", "Ada Example"),
        ("Example, Ada", "Ada Example"),
        ("Ada Example", "Example, Ada"),
    ),
)
def test_equivalent_creator_genre_reaches_persisted_authority(
    tmp_path, author, target_author
):
    report = MetadataReport.from_json(
        genre_report(author, target_author=target_author).to_json()
    )
    path = tmp_path / "genre.sqlite"
    with closing(open_repository(path)) as repository:
        case_id, proposal = publish_genre_case(repository, report)
        before = repository.persist_shadow_outcome(case_id, 4)
        assert len(repository.get_review_case_decisions(case_id)) == 1
        genre = next(
            v for v in json.loads(before)["outcomes"] if v["destination"] == "genres"
        )
        assert genre["disposition"] == "candidate"
        assert genre["citation_ids"] == [
            claim.reference
            for provider in report.providers
            for candidate in provider.candidates
            for claim in candidate.claims
            if claim.kind.value == "genre"
        ]
    with closing(open_repository(path)) as repository:
        assert repository.persist_shadow_outcome(case_id, 5) == before
        (raw,) = repository.get_review_case_decisions(case_id)
        decision = json.loads(raw)
        assert decision["destination"] == "genres"
        authority = repository.field_effect_authority_source(
            AutomaticDecision.from_json(raw)
        )
        assert authority["decision_value"] == {"kind": "list", "value": ["Fantasy"]}


@pytest.mark.parametrize("destination", ("automatic", "ask", "ignore"))
@pytest.mark.parametrize("outcome", ("accept", "keep_current"))
def test_genre_owner_destination_and_finality(tmp_path, destination, outcome):
    from mechanical_librarian.application.review_web import (
        BrowseReviewLibrary,
        InspectCurrentReviewCase,
        RecordHumanDecision,
        ReviewWebError,
    )

    path = tmp_path / "owner.sqlite"
    with closing(open_repository(path)) as repository:
        case_id, _ = publish_genre_case(repository, genre_report(), destination)
        inspector = InspectCurrentReviewCase(repository)
        field = next(
            f for f in inspector.run(case_id)["fields"] if f["destination"] == "genres"
        )
        attention = BrowseReviewLibrary(repository).attention(
            search=None, limit=50, cursor=None
        )
        assert any(i["destination"] == "genres" for i in attention["items"]) is (
            destination == "ask"
        )
        assert (field["automatic_decision"] is not None) is (destination == "automatic")
        if destination == "ignore":
            assert not field["editable"]
            assert repository.get_review_case_decisions(case_id) == ()
            assert repository.pending_field_effect_decision_documents(100) == ()
            assert any(
                claim.kind.value == "genre"
                for provider in MetadataReport.from_json(
                    repository.get_review_case_report(case_id)
                ).providers
                for candidate in provider.candidates
                for claim in candidate.claims
            )
            return
        if destination == "ask":
            assert field["editable"]
            assert {"kind": "list", "value": ["Fantasy"]} in field["proposed_values"]
        command = RecordHumanDecision(repository, clock=lambda: 10)
        for changes in (
            {"target_fingerprint": "f" * 64},
            {"selected_value": {"kind": "list", "value": ["Forged"]}},
        ):
            args = dict(
                proposal_id=field["proposal_id"],
                target_fingerprint=field["target_fingerprint"],
                destination="genres",
                actor="owner",
                outcome="accept",
                selected_value={"kind": "list", "value": ["Fantasy"]},
                idempotency_key="forged-genre",
            )
            with pytest.raises(ReviewWebError):
                command.run(case_id, **(args | changes))
        command.run(
            case_id,
            proposal_id=field["proposal_id"],
            target_fingerprint=field["target_fingerprint"],
            destination="genres",
            actor="owner",
            outcome=outcome,
            selected_value={"kind": "list", "value": ["Fantasy"]}
            if outcome == "accept"
            else None,
            idempotency_key="owner-genre-final",
        )
        repository.persist_shadow_outcome(case_id, 11)
    with closing(open_repository(path)) as repository:
        repository.persist_shadow_outcome(case_id, 12)
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if f["destination"] == "genres"
        )
        assert field["classification"] == "human_recorded"
        assert field["human_decision"]["outcome"] == outcome
        history = InspectCurrentReviewCase(repository).history(case_id)["items"]
        event = next(
            i
            for i in history
            if i.get("decision_id") == field["human_decision"]["decision_id"]
        )
        assert event["outcome"] == outcome
        assert (field["automatic_decision"] is not None) is (destination == "automatic")
        if destination == "automatic":
            from dataclasses import replace

            from mechanical_librarian.application.field_effects import (
                authority_from_decision,
            )

            old = next(
                AutomaticDecision.from_json(raw)
                for raw in repository.get_review_case_decisions(case_id)
                if json.loads(raw).get("schema_version") == "automatic-decision-v1"
            )
            authority = authority_from_decision(old)
            assert authority is not None
            assert not repository.field_effect_authority_is_current(
                replace(authority, source="persisted_review_case")
            )
            assert (
                old.to_json()
                not in repository.pending_field_effect_decision_documents(100)
            )
        assert not any(
            i["destination"] == "genres"
            for i in BrowseReviewLibrary(repository).attention(
                search=None, limit=50, cursor=None
            )["items"]
        )


@pytest.mark.parametrize(
    "shape",
    (
        "wrong",
        "competition",
        "populated",
        "unavailable",
        "error",
        "no_match",
        "missing",
        "empty",
        "null",
    ),
)
def test_genre_fill_and_unavailable_are_not_dissent(tmp_path, shape):
    from mechanical_librarian.domain.metadata_report import (
        ProviderObservation,
        ProviderStatus,
        TargetFieldState,
    )

    extra: tuple[ProviderObservation, ...] = ()
    if shape in {"unavailable", "error", "no_match"}:
        extra = (
            ProviderObservation(
                "unavailable-catalogue",
                {
                    "unavailable": ProviderStatus.UNAVAILABLE,
                    "error": ProviderStatus.MALFORMED,
                    "no_match": ProviderStatus.NO_MATCH,
                }[shape],
            ),
        )
    if shape == "competition":
        from mechanical_librarian.domain.metadata_report import ClaimKind, ClaimScope
        from tests.support.proposal_evidence import _claim, _provider

        extra = (
            _provider(
                "other-catalogue",
                "edition-2",
                (
                    _claim(
                        "other-catalogue",
                        "edition-2",
                        ClaimKind.GENRE,
                        "Mystery",
                        scope=ClaimScope.WORK,
                    ),
                ),
            ),
        )
    report = genre_report(
        "Other Author" if shape == "wrong" else "Example, Ada",
        current=("Existing",) if shape == "populated" else None,
        state={"empty": TargetFieldState.EMPTY, "null": TargetFieldState.NULL}.get(
            shape, TargetFieldState.MISSING
        ),
        genres=("Fantasy",),
        extra=extra,
    )
    with closing(open_repository(tmp_path / "genre.sqlite")) as repository:
        case_id, _ = publish_genre_case(
            repository, MetadataReport.from_json(report.to_json())
        )
        decisions = [
            AutomaticDecision.from_json(raw)
            for raw in repository.get_review_case_decisions(case_id)
        ]
        if shape in {"wrong", "competition"}:
            assert decisions == []
        else:
            assert len(decisions) == 1
            assert repository.field_effect_authority_source(decisions[0])[
                "decision_value"
            ] == (
                None if shape == "populated" else {"kind": "list", "value": ["Fantasy"]}
            )


def test_genre_actual_predecessors_refuse_without_mutation(tmp_path):
    import sqlite3
    from pathlib import Path

    from mechanical_librarian.adapters.persistence.metadata_reports import (
        MetadataReportError,
    )
    from mechanical_librarian.domain.metadata_report import ReportValueError
    from mechanical_librarian.domain.proposal_set import ProposalSet, ProposalValueError

    fixtures = Path(__file__).parents[1] / "fixtures/genre-applicability-predecessor"
    for path in (fixtures / "report-v48.json", *fixtures.glob("inventaire-*.json")):
        with pytest.raises(ReportValueError):
            MetadataReport.from_json(path.read_text().strip())
    with pytest.raises(ProposalValueError):
        ProposalSet.from_json((fixtures / "proposal-v29.json").read_text().strip())
    historical = AutomaticDecision.from_json(
        (fixtures / "automatic-v48.json").read_text().strip()
    )
    assert historical.destination == "genres"
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript((fixtures / "reports-v48.sql").read_text())
    path.chmod(0o600)
    before = path.read_bytes()
    entries = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(MetadataReportError):
        open_repository(path)
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == entries


def test_genre_real_authority_reaches_consent_guarded_verified_effect(tmp_path):
    from mechanical_librarian.adapters.field_effect_authority import (
        PersistedFieldEffectAuthority,
    )
    from mechanical_librarian.application.field_effects import GuardedApplicationError
    from tests.support.description_advice import _guarded_application
    from tests.unit.test_field_effects import CAPABILITY

    with closing(open_repository(tmp_path / "effect.sqlite")) as repository:
        case_id, _ = publish_genre_case(repository, genre_report())
        decision = AutomaticDecision.from_json(
            repository.get_review_case_decisions(case_id)[0]
        )
        authority = PersistedFieldEffectAuthority(repository)
        snapshot = authority.resolve(decision)
        service, target = _guarded_application(repository, tmp_path, authority)
        args = dict(
            connection=snapshot.connection,
            library=snapshot.library,
            work_id=snapshot.work_id,
            destination=snapshot.destination,
            pre_value=snapshot.pre_value,
            desired_value=snapshot.decision_value,
            target_fingerprint=snapshot.target_fingerprint,
            target_version=snapshot.target_version,
            dependency_digests=snapshot.dependency_digests,
            capability_digest=CAPABILITY,
        )
        with pytest.raises(GuardedApplicationError):
            service.plan(decision, **args)
        repository.grant_field_effect_consents("primary", ("ebooks",), ("genres",), 12)
        operation = service.plan(decision, **args)
        assert operation is not None
        target.seed(operation.plan)
        assert service.apply(operation.plan.plan_id).state == "verified"
        assert target.read(operation.plan).value == ["Fantasy"]
        event = next(
            e
            for e in repository.field_effect_activity_page(
                search=None,
                event_type="metadata",
                occurred_after=0,
                page=1,
                page_size=25,
            )["events"]
            if e["event_id"] == decision.decision_id
        )
        assert event["record"]["selected_value"] == {
            "kind": "list",
            "value": ["Fantasy"],
        }


def test_genre_ask_stays_case_bound_then_retires_with_report_replacement(tmp_path):
    from mechanical_librarian.application.review_web import (
        BrowseReviewLibrary,
        InspectCurrentReviewCase,
        RecordHumanDecision,
        ReviewWebError,
    )
    from mechanical_librarian.domain.metadata_policy import (
        MetadataPolicyProfile,
        human_review_destinations,
        resolve_policy,
    )
    from mechanical_librarian.domain.proposal_set import evaluate_report

    with closing(open_repository(tmp_path / "replacement.sqlite")) as repository:
        report = genre_report()
        case_id, _ = publish_genre_case(repository, report, "ask")
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if f["destination"] == "genres"
        )
        original = MetadataPolicyProfile.from_data(
            json.loads(repository.active_metadata_policy())
        )
        replacement = MetadataPolicyProfile.create(
            "Skip genres",
            dict(original.options),
            dict(original.destinations) | {"genres": "ignore"},
        )
        resolved = resolve_policy(replacement, report.target.library)
        repository.create_metadata_policy_draft(replacement.canonical_json)
        repository.activate_metadata_policy(
            replacement.policy_id,
            original.policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            5,
        )
        # The old case retains its original Ask resolution, not the new profile.
        assert next(
            f
            for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if f["destination"] == "genres"
        )["needs_attention"]
        successor = genre_report("Ada Example")
        repository.publish(successor, lambda: True, expected_current=report.report_id)
        proposal = evaluate_report(
            successor, resolved.resolved_id, human_review_destinations(resolved)
        )
        repository.publish_proposal(proposal)
        next_case = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        repository.persist_shadow_outcome(next_case, 6)
        assert not any(
            i["destination"] == "genres"
            for i in BrowseReviewLibrary(repository).attention(
                search=None, limit=50, cursor=None
            )["items"]
        )
        assert not next(
            f
            for f in InspectCurrentReviewCase(repository).run(next_case)["fields"]
            if f["destination"] == "genres"
        )["editable"]
        before = repository.get_review_case_decisions(case_id)
        with pytest.raises(ReviewWebError):
            RecordHumanDecision(repository, clock=lambda: 7).run(
                case_id,
                proposal_id=field["proposal_id"],
                target_fingerprint=field["target_fingerprint"],
                destination="genres",
                actor="owner",
                outcome="accept",
                selected_value=field["proposed_values"][0],
                idempotency_key="stale-genre-owner",
            )
        assert repository.get_review_case_decisions(case_id) == before


@pytest.mark.parametrize(
    "destination,current,genres,eligible",
    (
        ("ask", None, (), False),
        ("ask", ("Fantasy",), ("Fantasy",), False),
        ("ignore", None, ("Fantasy",), False),
        ("ask", None, ("Fantasy",), True),
    ),
)
def test_genre_ask_counts_require_actual_owner_actions(
    tmp_path, destination, current, genres, eligible
):
    from mechanical_librarian.application.review_web import (
        BrowseReviewLibrary,
        InspectCurrentReviewCase,
    )

    with closing(open_repository(tmp_path / "attention.sqlite")) as repository:
        case_id, _ = publish_genre_case(
            repository, genre_report(current=current, genres=genres), destination
        )
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if f["destination"] == "genres"
        )
        assert field["editable"] is eligible
        assert field["needs_attention"] is eligible
        assert bool(field["decision_actions"]) is eligible
        browser = BrowseReviewLibrary(repository)
        attention = browser.attention(search=None, limit=50, cursor=None)
        assert any(i["destination"] == "genres" for i in attention["items"]) is eligible
        library = browser.library(lens="all", search=None, limit=50, cursor=None)
        assert library["items"][0]["attention_count"] == attention["total"]
        assert type(attention["total"]) is int
        assert library["counts"]["needs_attention"] == int(attention["total"] > 0)


def test_genre_ask_authenticated_command_refuses_forged_values(tmp_path):
    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    from mechanical_librarian.application.configuration import ReviewWebConfiguration
    from tests.unit.review_app_fixture import (
        TEST_REACT_BUILD_IDENTITY,
        create_transport_test_app,
    )

    with closing(
        open_repository(tmp_path / "api.sqlite", cross_thread=True)
    ) as repository:
        case_id, _ = publish_genre_case(repository, genre_report(), "ask")
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
            url = f"/api/v1/review-cases/{case_id}/decisions"
            assert client.get(f"/api/v1/review-cases/{case_id}").status_code == 401
            headers = {
                "Origin": "https://review.test",
                "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
                "Idempotency-Key": "genre-owner-api-choice",
            }
            login = client.post(
                "/api/v1/session/login",
                json={"password": "synthetic owner"},
                headers=headers,
            )
            assert login.status_code == 200
            field = next(
                f
                for f in client.get(f"/api/v1/review-cases/{case_id}").json()["fields"]
                if f["destination"] == "genres"
            )
            assert field["editable"] and field["needs_attention"]
            payload = {
                "destination": "genres",
                "outcome": "accept",
                "proposal_id": field["proposal_id"],
                "target_fingerprint": field["target_fingerprint"],
                "selected_value": field["proposed_values"][0],
            }
            before = repository.get_review_case_decisions(case_id)
            assert client.post(url, json=payload, headers=headers).status_code == 422
            headers["X-CSRF-Token"] = login.json()["csrf_token"]
            for forged in (["Forged"], ["Fantasy", "Forged"]):
                response = client.post(
                    url,
                    json=payload
                    | {"selected_value": {"kind": "list", "value": forged}},
                    headers=headers,
                )
                assert response.status_code == 422, response.text
                assert repository.get_review_case_decisions(case_id) == before
            accepted = client.post(url, json=payload, headers=headers)
            assert accepted.status_code == 200, accepted.text
            assert accepted.json()["outcome"] == "accept"
            refreshed = client.get(f"/api/v1/review-cases/{case_id}").json()
            language = next(
                f for f in refreshed["fields"] if f["destination"] == "genres"
            )
            assert (
                language["human_decision"]["decision_id"]
                == accepted.json()["decision_id"]
            )
            assert not language["needs_attention"]
            assert language["automatic_decision"] is None
