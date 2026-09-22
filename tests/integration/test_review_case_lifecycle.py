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
from mechanical_librarian.domain.proposal_set import evaluate_report
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
    metadata = {"title": "Current title", "description": current}
    media: dict[str, object] = {"metadata": metadata}
    if media_kind == "audiobook":
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
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", "item-1", snapshot.fields),
        file=FileFacts(
            0,
            "a" * 64,
            "b" * 64,
            "m4b" if audio else "epub",
        ),
        local_claims=(
            consensus_local_title("Candidate title"),
            _local_description(desired),
        ),
        local_reasons=("opening_matter_title",),
        providers=(),
        audio=audio,
    )
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
