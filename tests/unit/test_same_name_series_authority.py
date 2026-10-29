"""Same-name membership through real producers and durable decision authority."""

import json
from contextlib import closing

import pytest

from mechanical_librarian.adapters.persistence.field_effect_validation import (
    _automatic_decision_value,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.metadata_report import admitted_audiobook_identity
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.metadata_policy import human_review_destinations
from mechanical_librarian.domain.metadata_report import TargetFieldState, TargetSeries
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from tests.support.audiobook_reports import publish_audiobook_preview
from tests.support.same_name_series import same_name_series_report


@pytest.mark.parametrize("media", ("ebook", "audiobook"))
@pytest.mark.parametrize(
    "state", (TargetFieldState.MISSING, TargetFieldState.NULL, TargetFieldState.EMPTY)
)
def test_same_name_series_keeps_title_and_persists_exact_series_authority(
    tmp_path, media, state
):
    report = same_name_series_report(media, state=state)
    database = tmp_path / "series.sqlite"
    with closing(open_repository(database)) as repository:
        case_id, policy = publish_audiobook_preview(
            repository,
            report,
            ("series", "title_layout"),
            selected_options={
                "series": "primary_narrative",
                "title_layout": "preserve",
            },
        )
        raw = repository.persist_shadow_outcome(case_id, 4)
        outcomes = ShadowOutcomeSet.from_json(raw)
        selected = {value.destination: value for value in outcomes.outcomes}
        assert selected["title"].disposition == "keep_current"
        assert selected["series"].disposition == "candidate"
        assert selected["title"].reason_code == "unique_supported_preservation"
        assert selected["series"].reason_code == "unique_supported_candidate"
        assert set(selected["series"].citation_ids) == {
            claim.reference
            for provider in report.providers
            for candidate in provider.candidates
            for claim in candidate.claims
            if claim.kind.value in {"series", "series_sequence"}
        }
        assert repository.automatic_safety_state().category_suspensions == ()
        documents = repository.get_review_case_decisions(case_id)
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)
    with closing(open_repository(database, create=False)) as repository:
        assert repository.persist_shadow_outcome(case_id, 5) == raw
        assert repository.get_review_case_decisions(case_id) == documents
        proposal = evaluate_report(
            report, policy.resolved_id, human_review_destinations(policy)
        )
        case = ReviewCase.create(proposal, report)
        decisions = {
            json.loads(value)["destination"]: AutomaticDecision.from_json(value)
            for value in documents
        }
        suffix = ":audiobook_title_preserve" if media == "audiobook" else ""
        assert decisions["title"].policy_option == "preserve" + suffix
        assert (
            _automatic_decision_value(
                decisions["title"], case, report, proposal, outcomes
            )
            is None
        )
        suffix = ":audiobook_series_fill" if media == "audiobook" else ""
        assert decisions["series"].policy_option == "primary_narrative" + suffix
        assert _automatic_decision_value(
            decisions["series"], case, report, proposal, outcomes
        ) == {
            "kind": "series",
            "value": [{"name": report.target.current_title, "sequence": "1"}],
        }
        detail = InspectCurrentReviewCase(repository).run(case_id)
        fields = {value["destination"]: value for value in detail["fields"]}
        assert not fields["title"]["needs_attention"]
        assert not fields["series"]["needs_attention"]
        assert fields["series"]["automatic_decision"] is not None


@pytest.mark.parametrize("media", ("ebook", "audiobook"))
@pytest.mark.parametrize(
    "variant",
    ("normalized", "different", "competing", "wrong_author", "no_local_title"),
)
def test_real_series_association_and_title_witness_remain_authoritative(
    tmp_path, media, variant
):
    title = "Synthetic Journey" if media == "audiobook" else "First Journey"
    name = (
        title.upper().replace(" ", "—")
        if variant == "normalized"
        else "Different Order"
        if variant == "different"
        else title
    )
    report = same_name_series_report(
        media,
        name=name,
        positions=("1", "2") if variant == "competing" else ("1",),
        source_author="Unrelated Author" if variant == "wrong_author" else None,
        independent_title=variant != "no_local_title",
    )
    with closing(open_repository(tmp_path / "series.sqlite")) as repository:
        case_id, policy = publish_audiobook_preview(
            repository,
            report,
            ("series", "title_layout"),
            selected_options={
                "series": "primary_narrative",
                "title_layout": "preserve",
            },
        )
        # A fed answer under another author keeps only its ASIN (#1257); one
        # under the target's author witnesses the book, so its series is an
        # owner choice on the work route (#1253).
        if media == "audiobook" and variant in {"no_local_title", "wrong_author"}:
            assert not admitted_audiobook_identity(report)
            results = {
                value.destination.value: value
                for value in evaluate_report(report).results
            }
            assert not results["subtitle"].alternatives
            if variant == "wrong_author":
                assert case_id is None
                assert not results["title"].alternatives
                assert not results["series"].alternatives
            else:
                assert case_id is not None
                assert results["title"].outcome.value == "ineligible"
                assert results["series"].outcome.value == "propose_change"
                assert {
                    alternative.value.value
                    for alternative in results["series"].alternatives
                } == {(TargetSeries("Synthetic Journey", "1"),)}
                outcomes = ShadowOutcomeSet.from_json(
                    repository.persist_shadow_outcome(case_id, 4)
                )
                assert {
                    value.destination: value.disposition
                    for value in outcomes.outcomes
                    if value.destination in {"title", "series"}
                } == {"title": "manual", "series": "manual"}
            assert repository._connection.execute(
                "SELECT count(*) FROM automatic_decisions"
            ).fetchone() == (0,)
            assert repository.pending_field_effect_decision_documents(10) == ()
            return
        if media == "audiobook":
            assert admitted_audiobook_identity(report)
        outcomes = ShadowOutcomeSet.from_json(
            repository.persist_shadow_outcome(case_id, 4)
        )
        selected = {value.destination: value for value in outcomes.outcomes}
        if variant in {"competing", "wrong_author"}:
            assert selected["series"].disposition == "abstention"
        elif variant == "no_local_title":
            assert selected["title"].disposition != "keep_current"
        else:
            assert selected["title"].disposition == "keep_current"
            assert selected["series"].disposition == "candidate"
            proposal = evaluate_report(
                report, policy.resolved_id, human_review_destinations(policy)
            )
            decision = next(
                AutomaticDecision.from_json(raw)
                for raw in repository.get_review_case_decisions(case_id)
                if json.loads(raw)["destination"] == "series"
            )
            assert _automatic_decision_value(
                decision,
                ReviewCase.create(proposal, report),
                report,
                proposal,
                outcomes,
            ) == {"kind": "series", "value": [{"name": name, "sequence": "1"}]}


@pytest.mark.parametrize("media", ("ebook", "audiobook"))
@pytest.mark.parametrize("kind", ("same", "different"))
def test_actual_base_proposal_and_store_refuse_without_mutation(media, kind):
    import hashlib
    from pathlib import Path

    from mechanical_librarian.domain.metadata_report import canonical_json
    from mechanical_librarian.domain.proposal_set import ProposalSet, ProposalValueError
    from tests.support.metadata_report_schemas import retained_report_as_current

    root = Path(__file__).parents[1] / "fixtures/same-name-series-predecessor"
    provenance = json.loads((root / "provenance.json").read_text())
    assert provenance["source_sha"] == "6dd9a74e9814588878ccdd4f4ed0fb3998c65536"
    # Sidecars retain archived capture entries recoverable from Git history.
    names = [
        f"{medium}-{scenario}-{document}.json"
        for medium in ("ebook", "audiobook")
        for scenario in ("same", "different")
        for document in ("report", "proposal", "outcome")
    ] + [f"{medium}-different-decisions.json" for medium in ("ebook", "audiobook")]
    names.append("ebook-same-store.sql")
    for name in names:
        assert (
            hashlib.sha256((root / name).read_bytes()).hexdigest()
            == provenance["sha256"][name]
        )
    report = retained_report_as_current(
        canonical_json(json.loads((root / f"{media}-{kind}-report.json").read_text()))
    )
    assert evaluate_report(report).policy_version == "fixed-conservative-v39"
    with pytest.raises(ProposalValueError):
        ProposalSet.from_json(
            canonical_json(
                json.loads((root / f"{media}-{kind}-proposal.json").read_text())
            )
        )


def test_genuine_predecessor_store_refuses_before_row_validation(tmp_path):
    import sqlite3
    from pathlib import Path
    from unittest.mock import patch

    from mechanical_librarian.adapters.persistence import schema
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        MetadataReportError,
    )

    source = (
        Path(__file__).parents[1]
        / "fixtures/same-name-series-predecessor/ebook-same-store.sql"
    )
    database = tmp_path / "old.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(source.read_text())
        assert connection.execute(
            "SELECT count(*) FROM shadow_decision_outcomes"
        ).fetchone() == (1,)
    database.chmod(0o600)
    before = database.read_bytes()
    with patch.object(
        schema, "_validate_schema_contents", wraps=schema._validate_schema_contents
    ) as validate_rows:
        with pytest.raises(MetadataReportError):
            open_repository(database, create=False)
        validate_rows.assert_not_called()
        assert database.read_bytes() == before
        current = tmp_path / "current.sqlite"
        with closing(open_repository(current)):
            pass
        validate_rows.reset_mock()
        with closing(open_repository(current, create=False)):
            pass
        validate_rows.assert_called()


@pytest.mark.parametrize("media", ("ebook", "audiobook"))
@pytest.mark.parametrize(
    "guard", ("success", "no_consent", "no_backup", "stale_target", "readback_mismatch")
)
def test_same_name_series_effect_retains_guards_and_real_activity(
    tmp_path, media, guard
):
    from types import SimpleNamespace

    from mechanical_librarian.adapters.field_effect_authority import (
        PersistedFieldEffectAuthority,
    )
    from mechanical_librarian.adapters.field_effect_fakes import (
        FakeBackupReadiness,
        FakeCapabilityObserver,
        FakeDependencyCurrentness,
        FakeForwardReevaluation,
        FakeTargetService,
    )
    from mechanical_librarian.adapters.field_effect_runtime import (
        PersistedEffectConsent,
    )
    from mechanical_librarian.application.field_effects import GuardedFieldApplication

    report = same_name_series_report(media)
    with closing(open_repository(tmp_path / "effects.sqlite")) as repository:
        case_id, _ = publish_audiobook_preview(
            repository,
            report,
            ("series", "title_layout"),
            selected_options={
                "series": "primary_narrative",
                "title_layout": "preserve",
            },
        )
        repository.persist_shadow_outcome(case_id, 4)
        decision = next(
            AutomaticDecision.from_json(raw)
            for raw in repository.get_review_case_decisions(case_id)
            if json.loads(raw)["destination"] == "series"
        )
        authority = PersistedFieldEffectAuthority(repository).resolve(decision)
        if guard != "no_consent":
            repository.grant_field_effect_consents(
                authority.connection, (authority.library,), ("series",), 5
            )
        target = FakeTargetService()
        backup = FakeBackupReadiness()
        service = GuardedFieldApplication(
            repository,
            target,
            backup,
            FakeCapabilityObserver("c" * 64),
            PersistedFieldEffectAuthority(repository),
            FakeDependencyCurrentness(),
            PersistedEffectConsent(repository),
            SimpleNamespace(),
            FakeForwardReevaluation(),
            lambda: 6,
        )
        if guard == "no_backup":
            backup.ready_value = False
        from contextlib import nullcontext

        from mechanical_librarian.application.field_effects import (
            GuardedApplicationError,
        )

        with (
            pytest.raises(GuardedApplicationError, match="consent")
            if guard == "no_consent"
            else nullcontext()
        ):
            plan = service.plan(
                decision,
                connection=authority.connection,
                library=authority.library,
                work_id=authority.work_id,
                destination="series",
                pre_value=authority.pre_value,
                desired_value=authority.decision_value,
                target_fingerprint=authority.target_fingerprint,
                target_version=authority.target_version,
                dependency_digests=authority.dependency_digests,
                capability_digest="c" * 64,
            )
        if guard == "no_consent":
            return
        assert plan is not None
        target.seed(plan.plan)
        if guard == "stale_target":
            target.drift(plan.plan, [{"name": "Changed Order", "sequence": "2"}])
        elif guard == "readback_mismatch":
            target.after_apply = lambda current: target.drift(
                current, [{"name": "Changed Order", "sequence": "2"}]
            )
        result = service.apply(plan.plan.plan_id)
        assert result.state == (
            "verified"
            if guard == "success"
            else "reconciling"
            if guard == "readback_mismatch"
            else "stale"
        )
        if guard == "success":
            assert target.read(plan.plan).value == [
                {"name": report.target.current_title, "sequence": "1"}
            ]
            activity = repository.field_effect_activity_page(
                search=None,
                event_type="metadata",
                occurred_after=0,
                page=1,
                page_size=25,
            )
            assert any(event.get("state") == "verified" for event in activity["events"])


@pytest.mark.parametrize("media", ("ebook", "audiobook"))
@pytest.mark.parametrize("mode", ("automatic", "ask", "ignore"))
@pytest.mark.parametrize("choice", ("accept", "keep_current"))
def test_series_owner_reachability_ignore_and_finality(tmp_path, media, mode, choice):
    from dataclasses import replace

    from mechanical_librarian.application.field_effects import authority_from_decision
    from mechanical_librarian.application.review_web import (
        BrowseReviewLibrary,
        RecordHumanDecision,
        ReviewWebError,
    )

    path = tmp_path / "owner.sqlite"
    report = same_name_series_report(media)
    expected = {
        "kind": "series",
        "value": [{"name": report.target.current_title, "sequence": "1"}],
    }
    with closing(open_repository(path)) as repository:
        case_id, _ = publish_audiobook_preview(
            repository,
            report,
            ("title_layout",),
            selected_options={
                "series": "primary_narrative",
                "title_layout": "preserve",
            },
            selected_destinations={"series": mode},
        )
        repository.persist_shadow_outcome(case_id, 4)
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if f["destination"] == "series"
        )
        attention = BrowseReviewLibrary(repository).attention(
            search=None, limit=50, cursor=None
        )
        assert any(item["destination"] == "series" for item in attention["items"]) is (
            mode == "ask"
        )
        if mode == "ignore":
            assert not field["editable"]
            assert field["automatic_decision"] is None
            assert not any(
                json.loads(raw)["destination"] == "series"
                for raw in repository.pending_field_effect_decision_documents(100)
            )
            return
        assert field["editable"]
        assert field["decision_actions"]
        args = dict(
            proposal_id=field["proposal_id"],
            target_fingerprint=field["target_fingerprint"],
            destination="series",
            actor="owner",
            outcome="accept",
            selected_value=expected,
            idempotency_key="series-owner-choice",
        )
        command = RecordHumanDecision(repository, clock=lambda: 10)
        for changes in (
            {"target_fingerprint": "f" * 64},
            {
                "selected_value": {
                    "kind": "series",
                    "value": [{"name": "Forged", "sequence": "9"}],
                }
            },
        ):
            with pytest.raises(ReviewWebError):
                command.run(case_id, **(args | changes))
        result = command.run(
            case_id,
            **(
                args
                | {
                    "outcome": choice,
                    "selected_value": expected if choice == "accept" else None,
                }
            ),
        )
        assert (
            command.run(
                case_id,
                **(
                    args
                    | {
                        "outcome": choice,
                        "selected_value": expected if choice == "accept" else None,
                    }
                ),
            )
            == result
        )
        repository.persist_shadow_outcome(case_id, 11)
    with closing(open_repository(path, create=False)) as repository:
        repository.persist_shadow_outcome(case_id, 12)
        field = next(
            f
            for f in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if f["destination"] == "series"
        )
        assert field["classification"] == "human_recorded"
        assert field["human_decision"]["outcome"] == choice
        assert not field["needs_attention"]
        if mode == "automatic":
            old = next(
                AutomaticDecision.from_json(raw)
                for raw in repository.get_review_case_decisions(case_id)
                if json.loads(raw).get("schema_version") == "automatic-decision-v1"
                and json.loads(raw)["destination"] == "series"
            )
            authority = authority_from_decision(old)
            assert authority is not None
            assert not repository.field_effect_authority_is_current(
                replace(authority, source="persisted_review_case")
            )


@pytest.mark.parametrize("media", ("ebook", "audiobook"))
def test_actual_different_name_base_decisions_are_rejected(media):
    from pathlib import Path

    from mechanical_librarian.domain.automatic_decision import AutomaticDecisionError

    path = (
        Path(__file__).parents[1]
        / "fixtures/same-name-series-predecessor"
        / f"{media}-different-decisions.json"
    )
    documents = json.loads(path.read_text())
    assert {json.loads(raw)["destination"] for raw in documents} == {"title", "series"}
    for raw in documents:
        with pytest.raises(AutomaticDecisionError):
            AutomaticDecision.from_json(raw)


@pytest.mark.parametrize("route", ("ebook", "admitted", "work"))
@pytest.mark.parametrize("name", ("same", "different"))
def test_an_ignored_title_keeps_the_current_title_beside_its_series(route, name):
    from mechanical_librarian.domain.metadata_policy import OPTION_CATALOGUE

    report = same_name_series_report(
        "ebook" if route == "ebook" else "audiobook",
        name="Different Order" if name == "different" else None,
        independent_title=route != "work",
    )
    assert route == "ebook" or admitted_audiobook_identity(report) is (
        route == "admitted"
    )

    def series(title: str) -> object:
        destinations = dict.fromkeys(OPTION_CATALOGUE, "ask") | {"title_layout": title}
        proposal = evaluate_report(report, None, destinations)
        results = {value.destination.value: value for value in proposal.results}
        if title == "ignore":
            # The work route masks a title with no cleanup as it did before.
            assert results["title"].reasons == (
                ("no_supported_evidence",) if route == "work" else ("policy_ignored",)
            )
        return results["series"], len(proposal.bibliographic_combinations)

    # An ignored title leaves the current one in place, as a kept title does,
    # so the series stands exactly as it does beside the kept title.
    ignored, asked = series("ignore"), series("ask")
    assert ignored == asked
    assert ignored[0].outcome.value == "propose_change"
    assert ignored[1] == 1
