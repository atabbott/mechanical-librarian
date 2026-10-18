"""Synthetic installed-surface lifecycle proof for review admission storage."""

import json
import sys
import tempfile
import threading
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.field_effect_fakes import (
    FakeAuthorityCurrentness,
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeConsentCurrentness,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.field_effects import (
    GuardedFieldApplication,
    authority_from_decision,
)
from mechanical_librarian.domain.field_effect import project_decision_value
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    ClaimKind,
    ClaimScope,
    FileFacts,
    LocalClaim,
    MetadataReport,
    TargetFacts,
    default_target_fields,
)
from mechanical_librarian.domain.proposal_set import ProposalSet, evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.audiobook_reports import admitted_audiobook_report


def _actionable(report):
    return evaluate_report(report)


def _report(
    title: str = "Title",
    content_digest: str = "a",
    file_identity: str = "b",
    *,
    actionable: bool = True,
) -> MetadataReport:
    claims: tuple[LocalClaim, ...] = ()
    reasons = ("no_opening_claims",)
    if actionable:
        claims = (
            LocalClaim.create(
                kind=ClaimKind.TITLE,
                observed="Exact evaluator candidate",
                canonical="Exact evaluator candidate",
                excerpt="Exact evaluator candidate",
                applicability="not_applicable",
                checksum="not_applicable",
                applicability_basis="not_applicable",
                scope=ClaimScope.WORK,
            ),
        )
        reasons = ("opening_matter_title",)
    return MetadataReport.create(
        target=TargetFacts(
            "primary",
            "ebooks",
            "item-1",
            default_target_fields(title),
        ),
        file=FileFacts(0, content_digest * 64, file_identity * 64),
        local_claims=claims,
        local_reasons=reasons,
        providers=(),
    )


def test_absent_restart_replay_successor_and_retirement():
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reviews.sqlite"
        repository = open_repository(database)
        first = _report()
        repository.publish(first, lambda: True)
        proposal = _actionable(first)
        repository.publish_proposal(proposal)
        admitted = json.loads(repository.admit_review_case(proposal))
        case_id = admitted["review_case_id"]
        assert "admitted" == admitted["status"]
        assert (
            "replayed" == json.loads(repository.admit_review_case(proposal))["status"]
        )
        repository.close()
        repository = open_repository(database, create=False)
        assert json.loads(repository.get_review_case(case_id))["current"]
        successor = _report("Changed", "c", "d")
        repository.publish(successor, lambda: True)
        later = _actionable(successor)
        repository.publish_proposal(later)
        later_case = json.loads(repository.admit_review_case(later))["review_case_id"]
        assert not (json.loads(repository.get_review_case(case_id))["current"])
        assert json.loads(repository.get_review_case(later_case))["current"]
        retired_report = _report("Retired", "e", "f", actionable=False)
        repository.publish(retired_report, lambda: True)
        retired = evaluate_report(retired_report)
        repository.publish_proposal(retired)
        assert "retired" == json.loads(repository.admit_review_case(retired))["status"]
        assert not (json.loads(repository.get_review_case(later_case))["current"])
        assert (2,) == repository._connection.execute(
            "SELECT COUNT(*) FROM review_cases"
        ).fetchone()
        repository.close()


def test_concurrent_identical_admission_converges():
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "reviews.sqlite"
        repository = open_repository(database)
        report = _report()
        repository.publish(report, lambda: True)
        proposal = _actionable(report)
        repository.publish_proposal(proposal)
        repository.close()
        barrier, outcomes = threading.Barrier(2), []

        def admit():
            if sys.platform.startswith("linux"):
                barrier.wait()
                local = open_repository(database, create=False)
            else:
                local = open_repository(database, create=False)
                barrier.wait()
            try:
                outcomes.append(json.loads(local.admit_review_case(proposal)))
            finally:
                local.close()

        workers = [threading.Thread(target=admit) for _ in range(2)]
        # The comprehensions are used for their effect; the collected `None`
        # results are discarded.
        [worker.start() for worker in workers]  # type: ignore[func-returns-value]  # reason: the record-then-yield idiom is deliberate; the discarded None never reaches an assertion
        [worker.join() for worker in workers]  # type: ignore[func-returns-value]  # reason: the record-then-yield idiom is deliberate; the discarded None never reaches an assertion
        assert 1 == len({value["review_case_id"] for value in outcomes})


def _audiobook_accept(case, destination: str, key: str):
    proposal = next(
        value for value in case.proposals if value["destination"] == destination
    )
    result = proposal["result"]
    assert type(result) is dict
    alternatives = result["alternatives"]
    assert type(alternatives) is list
    return HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination=destination,
        actor="owner",
        outcome="accept",
        selected_value=alternatives[0]["value"],
        idempotency_key=key,
        occurred_at=1_000,
    )


def test_an_audiobook_decision_applies_through_the_guarded_path():
    """F47's outcome, end to end on one admitted recording.

    Decision, plan, apply, read-back verification and audit for each audiobook
    destination, on the same guarded service an ebook uses. An ambiguous
    response is reconciled from a targeted read rather than blindly retried,
    and re-applying a settled plan issues no second request (ADR-0071, #968).
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repository = open_repository(root / "reviews.sqlite", cross_thread=True)
        report = admitted_audiobook_report()
        repository.publish(report, lambda: True)
        proposal = evaluate_report(report)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        assert "audiobook" == case.media_kind
        target = FakeTargetService()
        requests: list[str] = []
        service_apply = target.apply

        def counted(plan):
            requests.append(plan.plan_id)
            return service_apply(plan)

        target.apply = counted  # type: ignore[method-assign]  # reason: the fake's write is the seam this test counts, and wrapping it is what proves an ambiguous response and a settled plan each issue exactly one.
        authority = FakeAuthorityCurrentness()
        consent = FakeConsentCurrentness(
            repository.field_effect_consent_envelope_identity
        )
        ticks = [1_000]

        def clock() -> int:
            ticks[0] += 1
            return ticks[0]

        service = GuardedFieldApplication(
            repository,
            target,
            FakeBackupReadiness(),
            FakeCapabilityObserver("c" * 64),
            authority,
            FakeDependencyCurrentness(),
            consent,
            PrivateCoverRecoveryStore(root / "recovery", lambda _: b"old-cover"),
            FakeForwardReevaluation(),
            clock,
        )
        plans = {}
        for index, destination in enumerate(("narrators", "asin")):
            decision = _audiobook_accept(
                case, destination, f"audiobook-apply-{index:04d}"
            )
            assert "apply" == decision.application
            repository.record_human_decision(decision)
            assert authority_from_decision(decision) is not None
            authority.bind(
                decision,
                connection=report.target.connection,
                library=report.target.library,
                work_id=report.target.item,
                target_version="version-1",
            )
            consent.allow(
                connection=report.target.connection,
                library=report.target.library,
                destination=destination,
            )
            operation = service.plan(
                decision,
                connection=report.target.connection,
                library=report.target.library,
                work_id=report.target.item,
                destination=destination,
                pre_value=None,
                desired_value=decision.selected_value,
                target_fingerprint=case.target_fingerprint,
                target_version="version-1",
                dependency_digests=("d" * 64,),
                capability_digest="c" * 64,
            )
            assert operation is not None
            target.seed(operation.plan)
            plans[destination] = operation.plan
            issued = len(requests)
            if destination == "asin":
                # The library answers without saying what it did. One request
                # left, the value did not move, and the plan waits for the
                # targeted read rather than writing again.
                target.ambiguous_next = "pre"
                waiting = service.apply(operation.plan.plan_id)
                assert "retryable" == waiting.state
                assert 1 == len(requests) - issued
            verified = service.apply(operation.plan.plan_id)

            assert ("verified", None) == (verified.state, verified.failure)
            assert target.read(operation.plan).value == project_decision_value(
                destination, decision.selected_value
            )
            audit = [
                json.loads(document)
                for document in repository.field_effect_event_documents(
                    operation.plan.plan_id
                )
            ]
            assert "planned" == audit[0]["kind"]
            assert "verified" == audit[-1]["kind"]
            assert audit[-1]["state"] == verified.state

        # Idempotent: a settled plan is already applied, and re-running it
        # issues no further request to the library.
        settled = len(requests)
        for plan in plans.values():
            assert "verified" == service.apply(plan.plan_id).state
        assert settled == len(requests)
        repository.close()


def test_description_advice_uses_real_persisted_admission_and_guarded_installed_lifecycle():
    from mechanical_librarian.adapters.field_effect_authority import (
        PersistedFieldEffectAuthority,
    )
    from tests.support.description_advice import _guarded_application, _prepared

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repository, store, binding, candidate, _ = _prepared(root)
        decision = store.admit(
            candidate.candidate_id, binding, "automatic_policy", "e" * 64, 11
        )
        attempt = store.begin_attempt(
            candidate.request_identity,
            binding,
            candidate.engine,
            candidate.model,
            12,
            lambda: store.binding_is_current(binding),
            retry=True,
        )
        assert attempt is not None
        assert store.start(attempt[0], 13, lambda: store.binding_is_current(binding))
        assert (
            store.read(binding.review_case_id)["selected_advice"]["candidate"][
                "candidate_id"
            ]
            == candidate.candidate_id
        )
        authority = PersistedFieldEffectAuthority(repository)
        snapshot = authority.resolve(decision)
        service, target = _guarded_application(repository, root, authority)
        operation = service.plan(
            decision,
            connection=snapshot.connection,
            library=snapshot.library,
            work_id=snapshot.work_id,
            destination=snapshot.destination,
            pre_value=snapshot.pre_value,
            desired_value=snapshot.decision_value,
            target_fingerprint=snapshot.target_fingerprint,
            target_version=snapshot.target_version,
            dependency_digests=snapshot.dependency_digests,
            capability_digest="c" * 64,
        )
        assert operation is not None
        target.seed(operation.plan)
        result = service.apply(operation.plan.plan_id)
        assert result.state == "verified"
        assert target.read(operation.plan).value == candidate.output.description
        assert service.apply(operation.plan.plan_id) == result
        activity = repository.field_effect_activity_page(
            search=None, event_type="all", occurred_after=0, page=1, page_size=25
        )
        assert any(
            event["state"] == "verified" and event["record"].get("advised") is True
            for event in activity["events"]
        )
        assert (
            store.finish(
                attempt[0],
                None,
                14,
                lambda: store.binding_is_current(binding),
                failure="unavailable",
            )
            is False
        )
        assert store.read(binding.review_case_id)["application_outcome"] == "verified"
        repository.close()
        reopened = open_repository(root / "reports.sqlite", create=False)
        advice = reopened.description_advice_store().read(binding.review_case_id)
        assert advice["candidate"] is None
        assert (
            advice["selected_advice"]["candidate"]["candidate_id"]
            == candidate.candidate_id
        )
        assert advice["application_outcome"] == "verified"
        assert advice["failure"] == "unavailable"
        assert advice["selection"]["decision_id"] == decision.decision_id
        reopened.close()


@pytest.mark.parametrize(
    "effect", ("verified", "formatting_drift", "wrong_readback", "keep_current")
)
@pytest.mark.parametrize("media_kind", ("ebook", "audiobook"))
@pytest.mark.parametrize("newline", ("\n", "\r\n"), ids=("lf", "crlf"))
def test_multiline_incumbent_description_persists_and_applies(
    tmp_path, effect, media_kind, newline
):
    """Real intake, current SQL/review replay and guarded owner choice for both media."""
    import httpx

    from mechanical_librarian.adapters.library_services.audiobookshelf_item import (
        AudiobookshelfItemReader,
    )
    from mechanical_librarian.domain.audiobook_corroboration import (
        admitted_audiobook_identity,
    )
    from mechanical_librarian.domain.field_effect import field_value_version
    from mechanical_librarian.domain.metadata_report import TargetDestination
    from tests.support.audiobookshelf_targets import synthetic_audio_file
    from tests.support.metadata_report_doubles import single_track_audio_facts
    from tests.support.proposal_evidence import (
        _local_description,
        consensus_local_title,
    )

    root = tmp_path / f"{media_kind}-{len(newline)}"
    root.mkdir(mode=0o700)
    current = f" \tFirst paragraph.{newline}{newline}Second paragraph.\t "
    desired = "A different description from local metadata."
    metadata: dict[str, object] = {"title": "Current title", "description": current}
    media: dict[str, object] = {"metadata": metadata}
    if media_kind == "audiobook":
        metadata.update(title="Project Hail Mary", authors=[{"name": "Andy Weir"}])
        media["audioFiles"] = [synthetic_audio_file("book.m4b")]
    else:
        media["ebookFile"] = {
            "ebookFormat": "epub",
            "ino": "inode-1",
            "metadata": {
                "relPath": "book.epub",
                "path": "/library/book.epub",
                "size": 1,
            },
        }
    payload = {
        "id": "item-1",
        "libraryId": "library-id",
        "isInvalid": False,
        "isMissing": False,
        "mediaType": "book",
        "media": media,
    }
    reader = AudiobookshelfItemReader(
        client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ),
        endpoint="https://example.invalid",
        library_id="library-id",
        service_path="/library",
        maximum_bytes=65536,
    )
    snapshot = reader.read("item-1")
    assert snapshot.media_kind == media_kind
    audio = single_track_audio_facts() if media_kind == "audiobook" else None
    admitted = admitted_audiobook_report() if audio else None
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", snapshot.fields),
        file=FileFacts(
            0,
            "a" * 64,
            "b" * 64,
            "m4b" if audio else "epub",
        ),
        local_claims=(*admitted.local_claims, _local_description(desired))
        if admitted
        else (
            consensus_local_title("Candidate title"),
            _local_description(desired),
        ),
        local_reasons=("opening_matter_title",),
        providers=admitted.providers if admitted else (),
        audio=audio,
    )
    if audio:
        assert admitted_audiobook_identity(report)
    assert MetadataReport.from_json(report.to_json()) == report
    repository = open_repository(root / "reviews.sqlite")
    repository.publish(report, lambda: True)
    proposal = evaluate_report(report)
    result = next(
        item
        for item in proposal.to_data()["results"]
        if item["destination"] == "description"
    )
    assert result["outcome"] == "unresolved_choice"
    assert result["alternatives"][0]["assessment"] == "replaces_target"
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    repository.close()
    repository = open_repository(root / "reviews.sqlite", create=False)
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(case_id))["case"]
    )
    assert (
        next(
            field.value
            for field in TargetFacts.from_data(case.target).fields
            if field.destination is TargetDestination.DESCRIPTION
        )
        == current
    )
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase

    projected = InspectCurrentReviewCase(repository).run(case_id)
    assert (
        next(
            field
            for field in projected["fields"]
            if field["destination"] == "description"
        )["current"]["value"]
        == current
    )
    decision = _audiobook_accept(case, "description", "paragraph-replacement-0001")
    if effect == "keep_current":
        decision = HumanDecision.create(
            case,
            proposal_id=decision.proposal_id,
            target_fingerprint=case.target_fingerprint,
            destination="description",
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key="paragraph-keep-current-0001",
            occurred_at=1000,
        )
    repository.record_human_decision(decision)
    assert decision.application == "apply"
    target = FakeTargetService()
    writes = []
    target.before_apply = lambda plan: writes.append(plan.plan_id)
    authority = FakeAuthorityCurrentness()
    consent = FakeConsentCurrentness(repository.field_effect_consent_envelope_identity)
    version = field_value_version("description", current)
    if effect != "keep_current":
        authority.bind(
            decision,
            connection="primary",
            library="ebooks",
            work_id="item-1",
            target_version=version,
        )
    consent.allow(connection="primary", library="ebooks", destination="description")
    service = GuardedFieldApplication(
        repository,
        target,
        FakeBackupReadiness(),
        FakeCapabilityObserver("c" * 64),
        authority,
        FakeDependencyCurrentness(),
        consent,
        PrivateCoverRecoveryStore(root / "recovery", lambda _: b"old-cover"),
        FakeForwardReevaluation(),
        lambda: 1001,
    )
    operation = service.plan(
        decision,
        connection="primary",
        library="ebooks",
        work_id="item-1",
        destination="description",
        pre_value=current,
        desired_value=decision.selected_value,
        target_fingerprint=case.target_fingerprint,
        target_version=version,
        dependency_digests=("d" * 64,),
        capability_digest="c" * 64,
    )
    if effect == "keep_current":
        assert operation is None
        assert writes == []
        assert repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone() == (0,)
        assert (
            json.loads(repository.get_review_case(case_id))["case"]["target"]
            == case.target
        )
        assert decision.outcome == "keep_current"
        repository.close()
        return
    assert operation is not None
    assert operation.plan.pre_value == current
    target.seed(operation.plan)
    if effect == "formatting_drift":
        drifted = (
            current.replace("\r\n", "\n")
            if newline == "\r\n"
            else current.replace("\n", "\r\n")
        )
        target.drift(operation.plan, drifted)
    elif effect == "wrong_readback":
        target.after_apply = lambda plan: target.drift(
            plan, "Wrong read-back description."
        )
    verified = service.apply(operation.plan.plan_id)
    if effect != "verified":
        assert (verified.state, verified.failure) == (
            ("stale", "target_changed")
            if effect == "formatting_drift"
            else ("reconciling", "readback")
        )
        audit = [
            json.loads(value)
            for value in repository.field_effect_event_documents(operation.plan.plan_id)
        ]
        assert not any(event["kind"] == "verified" for event in audit)
        repository.close()
        return
    assert (verified.state, verified.failure) == ("verified", None)
    assert target.read(operation.plan).value == desired
    assert service.apply(operation.plan.plan_id) == verified
    audit = [
        json.loads(value)
        for value in repository.field_effect_event_documents(operation.plan.plan_id)
    ]
    assert (audit[0]["kind"], audit[-1]["kind"]) == ("planned", "verified")
    repository.close()


@pytest.mark.parametrize(
    "scenario, admitted",
    (
        ("supported", True),
        ("single_provider", False),
        ("two_provider_fill", True),
        ("same_authority", False),
        ("local_fill", True),
        ("local_replace", False),
        ("incidental", False),
        ("conflict", False),
        ("none", False),
    ),
)
def test_generated_authors_cross_persistence_owner_projection_and_guarded_fake_effect(
    tmp_path: Path, scenario: str, admitted: bool
) -> None:
    from types import SimpleNamespace

    from mechanical_librarian.adapters.field_effect_authority import (
        PersistedFieldEffectAuthority,
    )
    from mechanical_librarian.adapters.field_effect_runtime import (
        PersistedEffectConsent,
    )
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase
    from mechanical_librarian.domain.automatic_decision import AutomaticDecision
    from tests.support.author_evidence import author_report
    from tests.support.automatic_decisions import _automatic_decisions

    report = author_report(tmp_path / "media", scenario)
    database = tmp_path / "reviews.sqlite"
    repository = open_repository(database)
    try:
        decisions = _automatic_decisions(
            SimpleNamespace(repository=repository),
            report,
            "contributor_names",
            "canonical",
            qualified=True,
        )
        authors = [value for value in decisions if value.destination == "authors"]
        assert bool(authors) == admitted
        if not admitted:
            assert repository._connection.execute(
                "SELECT count(*) FROM automatic_decisions WHERE destination='authors'"
            ).fetchone() == (0,)
            return
        decision = authors[0]
        raw = decision.to_json()
        case_id = decision.review_case_id
        detail = InspectCurrentReviewCase(repository).run(case_id)
        projected = next(
            field for field in detail["fields"] if field["destination"] == "authors"
        )
        assert projected["automatic_decision"]["selected_value"] == {
            "kind": "list",
            "value": ["Ada Example"],
        }
        repository.persist_shadow_outcome(case_id, 5)
        assert repository._connection.execute(
            "SELECT count(*) FROM automatic_decisions WHERE destination='authors'"
        ).fetchone() == (1,)
        repository.close()
        repository = open_repository(database, create=False)
        assert raw in repository.get_review_case_decisions(case_id)
        decision = AutomaticDecision.from_json(raw)
        authority = repository.field_effect_authority_source(decision)
        target = FakeTargetService()
        service = GuardedFieldApplication(
            repository,
            target,
            FakeBackupReadiness(),
            FakeCapabilityObserver("c" * 64),
            PersistedFieldEffectAuthority(repository),
            FakeDependencyCurrentness(),
            PersistedEffectConsent(repository),
            SimpleNamespace(),
            FakeForwardReevaluation(),
            lambda: 10,
        )
        repository.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 6)
        operation = service.plan(
            decision,
            connection=cast(str, authority["connection"]),
            library=cast(str, authority["library"]),
            work_id=cast(str, authority["work_id"]),
            destination="authors",
            pre_value=authority["pre_value"],
            desired_value=authority["decision_value"],
            target_fingerprint=cast(str, authority["target_fingerprint"]),
            target_version=cast(str, authority["target_version"]),
            dependency_digests=cast(tuple[str, ...], authority["dependency_digests"]),
            capability_digest="c" * 64,
        )
        assert operation is not None
        target.seed(operation.plan)
        applied = service.apply(operation.plan.plan_id)
        assert applied.state == "verified"
        assert target.read(operation.plan).value == project_decision_value(
            "authors", authority["decision_value"]
        )
    finally:
        repository.close()


@pytest.mark.parametrize("rear", (False, True))
@pytest.mark.parametrize("recovered", (False, True))
@pytest.mark.parametrize("scenario", ("mixed", "copyright_notice"))
def test_mixed_author_indicia_cannot_fill_empty_target_automatically(
    tmp_path, rear, recovered, scenario
):
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase
    from tests.support.author_evidence import author_report, publish_author_case

    report = author_report(tmp_path / "media", scenario, rear=rear, recovered=recovered)
    repository = open_repository(tmp_path / "mixed.sqlite")
    try:
        case_id, proposal = publish_author_case(repository, report)
        authors = next(
            result.to_data()
            for result in proposal.results
            if result.destination.value == "authors"
        )
        assert authors["alternatives"] == []
        field = next(
            value
            for value in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if value["destination"] == "authors"
        )
        assert field["current"]["state"] == "missing"
        assert field["automatic_decision"] is None
        assert field["decision_actions"] == []
        assert repository._connection.execute(
            "SELECT count(*) FROM automatic_decisions WHERE destination='authors'"
        ).fetchone() == (0,)
        repository.persist_shadow_outcome(case_id, 5)
        assert repository.get_review_case_decisions(case_id) == ()
    finally:
        repository.close()


@pytest.mark.parametrize("scenario", ("single_provider", "same_authority"))
def test_one_author_provider_remains_owner_review_without_automatic_authority(
    tmp_path, scenario
):
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase
    from tests.support.author_evidence import author_report, publish_author_case

    repository = open_repository(tmp_path / "one-provider.sqlite")
    try:
        case_id, proposal = publish_author_case(
            repository, author_report(tmp_path / "media", scenario)
        )
        field = next(
            value
            for value in InspectCurrentReviewCase(repository).run(case_id)["fields"]
            if value["destination"] == "authors"
        )
        assert field["current"]["state"] == "missing"
        assert field["automatic_decision"] is None
        assert field["editable"] is True
        assert "accept" in field["decision_actions"]
        assert repository._connection.execute(
            "SELECT count(*) FROM automatic_decisions WHERE destination='authors'"
        ).fetchone() == (0,)
    finally:
        repository.close()


@pytest.mark.parametrize("mode", ["retained", "dismissed", "unrelated_terminal"])
@pytest.mark.parametrize("blocker", [None, "pause", "global"])
def test_completed_title_recovery_restarts_as_one_durable_no_change(
    tmp_path: Path, mode: str, blocker: str | None
) -> None:
    from types import SimpleNamespace

    from mechanical_librarian.application.continuous_field_effects import (
        ContinuousGuardedApplication,
    )
    from mechanical_librarian.application.metadata_policy import AutomaticSafetyControls
    from mechanical_librarian.domain.automatic_decision import AutomaticSafetyState
    from tests.support.evidence_led_titles import (
        complete_title_work,
        isbn_title_report,
        replace_title_presentation,
    )

    path = tmp_path / "recovery.sqlite"
    repository = open_repository(path)
    case_id, _ = complete_title_work(
        repository,
        isbn_title_report(current="Example Title"),
        safety=AutomaticSafetyState(
            blocker == "pause",
            "retained_global" if blocker == "global" else None,
            (("title_layout", "automatic_admission_failed"),),
        ),
    )
    replace_title_presentation(repository, mode)
    AutomaticSafetyControls(repository).recover(30, "title_layout")
    if blocker is not None:
        assert repository.claim_review_pipeline_action(30) is None
        repository.close()
        repository = open_repository(path, create=False)
        controls = AutomaticSafetyControls(repository)
        if blocker == "pause":
            controls.pause(False, 30)
        else:
            controls.recover(30)
    queued = repository.preparation_presentation(30)
    assert queued is not None and queued.state.value == "running"
    assert queued.total == (2 if mode == "unrelated_terminal" else 1)
    repository.close()
    repository = open_repository(path, create=False)
    action = repository.claim_review_pipeline_action(31)
    assert action is not None and action.stage == "projection"
    running = repository.preparation_presentation(31)
    assert running is not None and len(running.active_books) == 1
    repository.persist_shadow_outcome(case_id, 32, staged_action=action)
    repository.advance_review_pipeline_action(action, None, 33)
    assert repository.claim_review_pipeline_action(34) is None
    settled = repository.preparation_presentation(34)
    assert settled is not None and settled.passage.ready == 1
    assert settled.state.value == (
        "completed_with_failures" if mode == "unrelated_terminal" else "completed_clean"
    )
    scheduler = ContinuousGuardedApplication(
        repository,
        {("primary", "ebooks"): cast(GuardedFieldApplication, SimpleNamespace())},
        {("primary", "ebooks"): SimpleNamespace()},
        SimpleNamespace(),
        (("primary", "ebooks"),),
        lambda: 35,
        lambda *_: None,
    )
    assert scheduler.schedule() == scheduler.schedule() == 0
    assert repository._connection.execute(
        "SELECT count(*) FROM field_effect_no_changes"
    ).fetchone() == (1,)
    assert repository._connection.execute(
        "SELECT count(*) FROM field_effects"
    ).fetchone() == (0,)
    assert repository.pending_field_effect_decision_documents(100) == ()
    repository.close()
    repository = open_repository(path, create=False)
    assert repository.preparation_presentation(34) == settled
    assert repository._connection.execute(
        "SELECT count(*) FROM automatic_decisions WHERE destination='title'"
    ).fetchone() == (1,)
    assert repository._connection.execute(
        "SELECT count(*) FROM field_effect_no_changes"
    ).fetchone() == (1,)
    repository.close()


@pytest.mark.parametrize(
    "effect_state,successor_case",
    [
        (state, successor)
        for state in ("planned", "claimed", "applying", "reconciling", "verified")
        for successor in (False, True)
    ]
    + [("revision_race", False), ("claim_race", False)],
)
def test_owner_confirmed_unknown_cover_lifecycle(
    tmp_path, effect_state, successor_case
):
    """Real retained producer, authenticated confirmation, restart and guarded effect."""
    import os
    from dataclasses import replace

    from argon2 import PasswordHasher
    from fastapi.testclient import TestClient

    from mechanical_librarian.adapters.field_effect_authority import (
        PersistedFieldEffectAuthority,
    )
    from mechanical_librarian.adapters.field_effect_fakes import FakePreclaimHook
    from mechanical_librarian.adapters.field_effect_runtime import (
        PersistedEffectConsent,
        PersistedEffectDependencies,
    )
    from mechanical_librarian.adapters.persistence.cover_artifacts import (
        CoverArtifactStore,
    )
    from mechanical_librarian.application.configuration import ReviewWebConfiguration
    from mechanical_librarian.application.cover_consideration import (
        CoverConsiderationService,
    )
    from mechanical_librarian.application.cover_policy import CoverPolicy
    from mechanical_librarian.domain.cover_evidence import CoverArtifact
    from tests.support.cover_producers import _preview, _source
    from tests.support.field_effects import CAPABILITY, Clock
    from tests.unit.review_app_fixture import (
        TEST_REACT_BUILD_IDENTITY,
        create_transport_test_app,
    )

    race_order = effect_state if effect_state.endswith("_race") else None
    if race_order:
        effect_state = "planned" if race_order == "revision_race" else "claimed"
    os.chmod(tmp_path, 0o700)
    sources = tuple(
        replace(
            _source(i, _preview(i)),
            artifact=CoverArtifact.create(
                _preview(i + 10), _preview(i), "image/png", 1, 1
            ),
        )
        for i in (1, 2)
    )
    report = MetadataReport.create(
        target=TargetFacts(
            "synthetic", "books", "item", default_target_fields("Synthetic title")
        ),
        file=FileFacts(0, "a" * 64, "b" * 64),
        local_claims=(),
        local_reasons=("no_opening_claims",),
        providers=(),
        cover_sources=tuple(sorted(sources, key=lambda source: source.source_id)),
    )
    database = tmp_path / "application.sqlite"
    repository = open_repository(database, cross_thread=True)
    artifacts = CoverArtifactStore(tmp_path / "artifacts")
    for i, source in enumerate(sources, 1):
        assert source.artifact is not None
        artifacts.stage(source.artifact, _preview(i + 10), _preview(i))
    repository.publish(report, lambda: True)
    proposals = evaluate_report(report)
    repository.publish_proposal(proposals)
    case_id = json.loads(repository.admit_review_case(proposals))["review_case_id"]
    case = ReviewCase.from_json(
        json.dumps(
            json.loads(repository.get_review_case(case_id))["case"],
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    proposal = next(p for p in case.proposals if p["destination"] == "cover")
    policy = CoverPolicy()
    fetching = [False]

    def bind(repo):
        service = CoverConsiderationService(
            repo,
            read_preview=lambda artifact_id, _variant: artifacts.read_preview_digest(
                next(
                    source.artifact.preview_digest
                    for source in sources
                    if source.artifact is not None
                    and source.artifact.artifact_id == artifact_id
                )
            ),
            read_preview_digest=artifacts.read_preview_digest,
            require_artifact=artifacts.require_artifact,
            clock=lambda: 100,
            policy=policy,
            provider_fetching=lambda _: fetching[0],
        )
        repo.bind_current_cover_policy(policy.reference)
        repo.bind_current_cover_replay(service.replay_reference)
        repo.bind_owner_cover_validation(service.require_owner_cover)
        repo.bind_cover_consideration_currentness(lambda *_args: fetching[0])
        return service

    covers = bind(repository)
    consideration = covers.ensure(case_id, report.to_json(), proposal)
    candidates = consideration["candidates"]
    assert all(
        c["match_state"] == "match_not_confirmed"
        and c["manual_eligible"]
        and not c["selectable"]
        for c in candidates
    )
    original = repository.current_cover_consideration_document(case_id)
    from mechanical_librarian.application.review_web import (
        BrowseReviewLibrary,
        InspectCurrentReviewCase,
    )

    inspected = InspectCurrentReviewCase(repository, covers).run(case_id)
    assert (
        next(field for field in inspected["fields"] if field["destination"] == "cover")[
            "needs_attention"
        ]
        is True
    )
    attention = BrowseReviewLibrary(repository).attention(
        search=None, limit=10, cursor=None
    )
    assert (case_id, "cover") in [
        (item["review_case_id"], item["destination"]) for item in attention["items"]
    ]
    password = tmp_path / "owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"))
    password.chmod(0o600)
    config = ReviewWebConfiguration(
        tls_certificate_file="/synthetic/review.crt",
        tls_private_key_file="/synthetic/review.key",
        canonical_origin="https://review.test",
        owner_password_hash_file=str(password),
        session_lifetime_seconds=600,
    )
    app = create_transport_test_app(
        repository, config, clock=lambda: 101, cover_considerations=covers
    )
    client = TestClient(app, base_url="https://review.test")
    path = f"/api/v1/review-cases/{case_id}/decisions"
    payload = dict(
        proposal_id=proposal["proposal_id"],
        target_fingerprint=case.target_fingerprint,
        destination="cover",
        outcome="accept",
        selected_value={"kind": "cover", "value": candidates[0]["artifact_id"]},
        consideration_id=consideration["consideration_id"],
        cover_confirmation={
            "expected_selection_id": None,
            "confirm_unverified_match": True,
        },
    )
    headers = {
        "Origin": "https://review.test",
        "Idempotency-Key": "owner-cover-first-0001",
        "x-ml-build-identity": TEST_REACT_BUILD_IDENTITY,
    }
    assert client.post(path, json=payload, headers=headers).status_code == 401
    login = client.post(
        "/api/v1/session/login",
        json={"password": "correct horse"},
        headers={"Origin": "https://review.test"},
    )
    assert login.status_code == 200
    headers["X-CSRF-Token"] = login.json()["csrf_token"]
    denied = client.post(
        path, json=payload, headers=headers | {"X-CSRF-Token": "invalid"}
    )
    assert denied.status_code == 403
    for invalid in (
        None,
        {"expected_selection_id": None, "confirm_unverified_match": False},
    ):
        refused = client.post(
            path, json=payload | {"cover_confirmation": invalid}, headers=headers
        )
        assert refused.status_code == 422, refused.text
    assert repository._connection.execute(
        "SELECT count(*) FROM human_decisions"
    ).fetchone() == (0,)
    if effect_state == "planned" and not successor_case:
        for malformed in (
            {"expected_selection_id": None},
            {"expected_selection_id": None, "confirm_unverified_match": 1},
            {
                "expected_selection_id": None,
                "confirm_unverified_match": True,
                "extra": True,
            },
        ):
            assert (
                client.post(
                    path,
                    json=payload | {"cover_confirmation": malformed},
                    headers=headers,
                ).status_code
                == 422
            )
        fetching[0] = True
        assert client.post(path, json=payload, headers=headers).status_code == 409
        fetching[0] = False
        selected_artifact = next(
            source.artifact
            for source in sources
            if source.artifact is not None
            and source.artifact.artifact_id == candidates[0]["artifact_id"]
        )
        assert selected_artifact is not None
        for digest in (
            selected_artifact.original_digest,
            selected_artifact.preview_digest,
        ):
            blob = tmp_path / "artifacts" / digest
            content = blob.read_bytes()
            for damage in ("missing", "corrupt", "directory", "symlink"):
                blob.unlink()
                if damage == "corrupt":
                    blob.write_bytes(b"corrupt")
                elif damage == "directory":
                    blob.mkdir()
                elif damage == "symlink":
                    blob.symlink_to(password)
                refused = client.post(path, json=payload, headers=headers)
                assert refused.status_code == 503, refused.text
                assert repository._connection.execute(
                    "SELECT count(*) FROM human_decisions"
                ).fetchone() == (0,)
                assert repository._connection.execute(
                    "SELECT count(*) FROM cover_selection_authorities"
                ).fetchone() == (0,)
                if damage == "directory":
                    blob.rmdir()
                elif blob.is_symlink() or blob.exists():
                    blob.unlink()
                blob.write_bytes(content)
                blob.chmod(0o600)
    from concurrent.futures import ThreadPoolExecutor

    barrier = threading.Barrier(2)

    def submit(_index):
        barrier.wait()
        return client.post(path, json=payload, headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert all(response.status_code == 200 for response in responses), [
        response.text for response in responses
    ]
    response = responses[0]
    decision_id = response.json()["decision_id"]
    if effect_state == "planned" and not successor_case and race_order is None:
        repository.bind_current_cover_replay("cover_replay_" + "0" * 64)
        drifted = client.post(path, json=payload, headers=headers)
        assert drifted.status_code == 409 and drifted.json()["error"]["code"] == "stale"
        conflict = client.post(
            path,
            json=payload
            | {
                "cover_confirmation": {
                    "expected_selection_id": None,
                    "confirm_unverified_match": False,
                }
            },
            headers=headers,
        )
        assert (
            conflict.status_code == 409
            and conflict.json()["error"]["code"] == "idempotency_conflict"
        )
        repository.bind_current_cover_replay(covers.replay_reference)
    assert (
        client.post(path, json=payload, headers=headers).json()["decision_id"]
        == decision_id
    )
    assert repository.current_cover_consideration_document(case_id) == original
    assert repository._connection.execute(
        "SELECT count(*) FROM human_decisions"
    ).fetchone() == (1,)
    first_selection = repository._connection.execute(
        "SELECT selection_id FROM current_cover_selection_authorities"
    ).fetchone()[0]
    inspected = InspectCurrentReviewCase(repository, covers).run(case_id)
    chosen_field = next(
        field for field in inspected["fields"] if field["destination"] == "cover"
    )
    assert chosen_field["needs_attention"] is False
    assert chosen_field["current_cover_selection_id"] == first_selection
    attention = BrowseReviewLibrary(repository).attention(
        search=None, limit=10, cursor=None
    )
    assert (case_id, "cover") not in [
        (item["review_case_id"], item["destination"]) for item in attention["items"]
    ]
    client.close()
    repository.close()
    repository = open_repository(database, cross_thread=True)
    covers = bind(repository)
    retained = json.loads(
        repository._connection.execute(
            "SELECT document FROM cover_selection_authorities"
        ).fetchone()[0]
    )
    assert retained["schema_version"] == "cover-selection-authority-v2"
    assert retained["observed_identity"]["outcome"] == "unknown"
    if effect_state == "planned" and not successor_case and race_order is None:
        import sqlite3

        from mechanical_librarian.application.metadata_report import MetadataReportError

        for mutation in ("confirmation", "observed_identity", "preview_sha256"):
            incompatible = tmp_path / (mutation + ".sqlite")
            clone = sqlite3.connect(incompatible)
            repository._connection.backup(clone)
            altered = json.loads(json.dumps(retained))
            if mutation == "confirmation":
                altered["confirmation"]["confirm_unverified_match"] = False
            elif mutation == "observed_identity":
                altered["observed_identity"]["outcome"] = "verified_work_match"
            else:
                del altered[mutation]
            trigger = clone.execute(
                "SELECT sql FROM sqlite_master WHERE name='cover_selection_authorities_no_update'"
            ).fetchone()[0]
            clone.execute("DROP TRIGGER cover_selection_authorities_no_update")
            clone.execute(
                "UPDATE cover_selection_authorities SET document=?",
                (json.dumps(altered, sort_keys=True, separators=(",", ":")),),
            )
            clone.execute(trigger)
            clone.commit()
            clone.close()
            incompatible.chmod(0o600)
            before = incompatible.read_bytes()
            with pytest.raises(MetadataReportError):
                open_repository(incompatible)
            assert incompatible.read_bytes() == before
    decision = HumanDecision.from_json(retained["decision_document"])
    authority = PersistedFieldEffectAuthority(repository)
    snapshot = authority.resolve(decision)
    repository.grant_field_effect_consents(
        snapshot.connection, (snapshot.library,), (snapshot.destination,), 102
    )
    target = FakeTargetService()
    application = GuardedFieldApplication(
        repository,
        target,
        FakeBackupReadiness(),
        FakeCapabilityObserver(CAPABILITY),
        authority,
        PersistedEffectDependencies(repository),
        PersistedEffectConsent(repository),
        PrivateCoverRecoveryStore(tmp_path / "recovery", lambda _plan: b"incumbent"),
        FakeForwardReevaluation(),
        Clock(),
        FakePreclaimHook(),
        FakePreclaimHook(),
        FakePreclaimHook(),
    )
    operation = application.plan(
        decision,
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
    assert operation is not None
    if effect_state == "planned" and not successor_case and race_order is None:
        from mechanical_librarian.domain.field_effect import FieldEffectError

        fetching[0] = True
        with pytest.raises(FieldEffectError, match="authority"):
            repository.claim_field_effect(
                operation.plan.plan_id, "planned", "e" * 64, 103
            )
        fetching[0] = False
        blob = tmp_path / "artifacts" / selected_artifact.original_digest
        content = blob.read_bytes()
        blob.unlink()
        with pytest.raises(FieldEffectError, match="authority"):
            repository.claim_field_effect(
                operation.plan.plan_id, "planned", "e" * 64, 103
            )
        blob.write_bytes(content)
        blob.chmod(0o600)
        repository._connection.execute(
            "DELETE FROM current_cover_considerations WHERE review_case_id=?",
            (case_id,),
        )
        repository._connection.commit()
        with pytest.raises(FieldEffectError, match="authority"):
            repository.claim_field_effect(
                operation.plan.plan_id, "planned", "e" * 64, 103
            )
        repository._connection.execute(
            "INSERT INTO current_cover_considerations(review_case_id,consideration_id) VALUES(?,?)",
            (case_id, consideration["consideration_id"]),
        )
        repository._connection.commit()
        assert repository.get_field_effect(operation.plan.plan_id).state == "planned"
        assert repository._connection.execute(
            "SELECT selection_id FROM current_cover_selection_authorities"
        ).fetchone() == (first_selection,)
    target.seed(operation.plan)
    if effect_state == "verified":
        assert application.apply(operation.plan.plan_id).state == "verified"
        assert target.read(operation.plan).value == candidates[0]["artifact_id"]
    elif effect_state != "planned" and race_order is None:
        assert (
            repository.claim_field_effect(
                operation.plan.plan_id, "planned", "a" * 64, 103
            ).state
            == "claimed"
        )
        if effect_state in {"applying", "reconciling"}:
            repository.transition_field_effect(
                operation.plan.plan_id, "claimed", "applying", 104
            )
        if effect_state == "reconciling":
            repository.transition_field_effect(
                operation.plan.plan_id, "applying", "reconciling", 105
            )
    if successor_case:
        predecessor_report_id = report.report_id
        report = MetadataReport.create(
            target=report.target,
            file=replace(report.file, content_digest="c" * 64),
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers,
            cover_sources=report.cover_sources,
        )
        repository.publish(report, lambda: True, expected_current=predecessor_report_id)
        proposals = evaluate_report(report)
        repository.publish_proposal(proposals)
        case_id = json.loads(repository.admit_review_case(proposals))["review_case_id"]
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        proposal = next(p for p in case.proposals if p["destination"] == "cover")
        consideration = covers.ensure(case_id, report.to_json(), proposal)
    replacement = HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="cover",
        actor="owner",
        outcome="accept",
        selected_value={"kind": "cover", "value": candidates[1]["artifact_id"]},
        idempotency_key="owner-cover-second-0002",
        occurred_at=103,
    )
    from mechanical_librarian.domain.human_decision import HumanDecisionError

    with pytest.raises(HumanDecisionError, match="stale"):
        repository.record_human_decision(
            replacement,
            cover_consideration_id=consideration["consideration_id"],
            cover_confirmation={
                "expected_selection_id": None,
                "confirm_unverified_match": True,
            },
        )
    if race_order:
        other = open_repository(
            database, create=False, authority=repository.authority, cross_thread=True
        )
        bind(other)
        entered = threading.Event()
        release = threading.Event()
        original_validator = repository._cover_considerations.require_owner_cover

        def hold_transaction(document, artifact_id):
            assert repository._connection.in_transaction
            original_validator(document, artifact_id)
            entered.set()
            assert release.wait(5)

        repository._cover_considerations.require_owner_cover = hold_transaction

        def revise(repo):
            return repo.record_human_decision(
                replacement,
                cover_consideration_id=consideration["consideration_id"],
                cover_confirmation={
                    "expected_selection_id": first_selection,
                    "confirm_unverified_match": True,
                },
            )

        def claim(repo):
            return repo.claim_field_effect(
                operation.plan.plan_id, "planned", "f" * 64, 103
            )

        first, second = (
            (revise, claim) if race_order == "revision_race" else (claim, revise)
        )
        second_started = threading.Event()

        def competing():
            second_started.set()
            return second(other)

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                winner = pool.submit(first, repository)
                assert entered.wait(5)
                loser = pool.submit(competing)
                assert second_started.wait(5)
                release.set()
                winner.result(timeout=5)
                from mechanical_librarian.domain.field_effect import FieldEffectError

                with pytest.raises(
                    FieldEffectError
                    if race_order == "revision_race"
                    else HumanDecisionError,
                    match="claim_conflict"
                    if race_order == "revision_race"
                    else "already_decided",
                ):
                    loser.result(timeout=5)
        finally:
            release.set()
            repository._cover_considerations.require_owner_cover = original_validator
            other.close()
    if effect_state != "planned":
        with pytest.raises(HumanDecisionError, match="already_decided"):
            repository.record_human_decision(
                replacement,
                cover_consideration_id=consideration["consideration_id"],
                cover_confirmation={
                    "expected_selection_id": first_selection,
                    "confirm_unverified_match": True,
                },
            )
        assert repository._connection.execute(
            "SELECT count(*) FROM human_decisions"
        ).fetchone() == (1,)
    else:
        repository.record_human_decision(
            replacement,
            cover_consideration_id=consideration["consideration_id"],
            cover_confirmation={
                "expected_selection_id": first_selection,
                "confirm_unverified_match": True,
            },
        )
        assert repository._connection.execute(
            "SELECT count(*) FROM human_decisions"
        ).fetchone() == (2,)
        assert repository.get_field_effect(operation.plan.plan_id).state == "superseded"
        with pytest.raises(HumanDecisionError, match="stale"):
            repository.record_human_decision(
                decision,
                cover_consideration_id=payload["consideration_id"],
                cover_confirmation=payload["cover_confirmation"],
            )
    repository.close()


@pytest.mark.parametrize(
    "model",
    (
        "openai/gpt-6-luna",
        "xiaomi/mimo-v2.6-flash",
        "mistralai/ministral-3b-2512",
        "openai/gpt-5.6-luna",
        "mistralai/mistral-small-2603",
    ),
)
def test_exact_cover_reviewer_configuration_http_persistence_and_selection_drift(
    tmp_path, model
):
    from unittest.mock import Mock

    import httpx

    from mechanical_librarian.adapters.persistence.cover_artifacts import (
        CoverArtifactStore,
    )
    from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterReviewer
    from mechanical_librarian.application.cover_consideration import (
        CoverConsiderationError,
        CoverConsiderationService,
    )
    from mechanical_librarian.application.cover_observation import (
        OUTPUT_SCHEMA_VERSION,
        TAXONOMY,
    )
    from mechanical_librarian.application.reviewer_access import (
        ReviewerAccessOperations,
    )
    from mechanical_librarian.application.reviewer_contracts import (
        CodexDeviceAuthState,
        ReviewerProvisioningStatus,
    )
    from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
    from tests.support.cover_producers import _qualified_cover_report

    report, proposals = _qualified_cover_report(tmp_path)
    assert isinstance(proposals, ProposalSet)
    artifacts = CoverArtifactStore(tmp_path / "artifacts")
    database = tmp_path / "reviews.sqlite"
    repository = open_repository(database)
    repository.publish(report, lambda: True)
    repository.publish_proposal(proposals)
    case_id = json.loads(repository.admit_review_case(proposals))["review_case_id"]
    case = ReviewCase.from_json(
        json.dumps(
            json.loads(repository.get_review_case(case_id))["case"],
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    proposal = next(p for p in case.proposals if p["destination"] == "cover")
    chats = []

    def http(request):
        if request.url.path == "/api/v1/key":
            return httpx.Response(200, json={"data": {"is_free_tier": False}})
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": model,
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["text"],
                            },
                            "supported_parameters": ["structured_outputs"],
                        }
                    ]
                },
            )
        body = json.loads(request.content)
        chats.append(body)
        assert body["model"] == model
        assert body["provider"] == {
            "require_parameters": True,
            "data_collection": "deny",
        }
        packet = json.loads(
            body["messages"][0]["content"][0]["text"].split("\nSealed packet:\n")[1]
        )
        result = {
            "schema_version": OUTPUT_SCHEMA_VERSION,
            "observations": [
                {
                    "candidate_reference": row["reference"],
                    **{name: values[0][0] for name, values in TAXONOMY},
                }
                for row in packet["candidates"]
            ],
        }
        return httpx.Response(
            200,
            json={
                "model": model,
                "choices": [{"message": {"content": json.dumps(result)}}],
            },
        )

    def bind(repo):
        access = ReviewerAccessOperations(
            repo.reviewer_access_store(),
            codex_auth=Mock(
                passive_status=Mock(return_value=CodexDeviceAuthState("ready"))
            ),
            codex_provisioning=lambda _: ReviewerProvisioningStatus(
                False, "profile_not_provisioned"
            ),
            codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
            codex_binding=Mock(),
            openrouter_factory=lambda key, selected: OpenRouterReviewer(
                key, selected, transport=httpx.MockTransport(http)
            ),
        )
        service = CoverConsiderationService(
            repo,
            read_preview=lambda artifact, _: artifacts.read_preview_digest(
                next(
                    s.artifact.preview_digest
                    for s in report.cover_sources
                    if s.artifact and s.artifact.artifact_id == artifact
                )
            ),
            read_preview_digest=artifacts.read_preview_digest,
            clock=lambda: 10,
            reviewer_dispatch=access.dispatch_cover_observation,
            reviewer_identity=access.current_cover_observation_identity,
            reviewer_revision_current=access.revision_is_current,
        )
        return access, service

    access, service = bind(repository)
    selected = access.configure_openrouter(
        expected_revision=1, api_key="synthetic-key", model=model
    )
    assert selected.mapping.model == model
    first = service.ensure(case_id, report.to_json(), proposal)
    assert first["reviewer_status"] == "completed"
    assert first["reviewer_model"] == model
    assert len(chats) == 1
    access.close()
    repository.close()
    repository = open_repository(database, create=False)
    access, service = bind(repository)
    assert access.read().mapping.model == model
    replay = service.ensure(case_id, report.to_json(), proposal)
    assert replay["consideration_id"] == first["consideration_id"]
    assert len(chats) == 1
    request = service.prepare_observation(case_id, report.to_json())
    access.select_none(expected_revision=selected.revision)
    evidence = service.observe(request)
    with pytest.raises(CoverConsiderationError, match="stale|changed"):
        service.ensure(case_id, report.to_json(), proposal, reviewer_evidence=evidence)
    access.close()
    repository.close()
