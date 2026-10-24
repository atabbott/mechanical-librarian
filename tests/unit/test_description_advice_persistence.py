"""Real SQLite attempt state and immutable history proofs."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.description_advice import (
    DescriptionAdviceCandidate,
)


def test_attempt_persisted_before_dispatch_and_success_rebinds_without_call(
    tmp_path: Path,
) -> None:
    from tests.support.description_advice import _prepared

    repository, store, binding, original, _ = _prepared(tmp_path)
    path = tmp_path / "reports.sqlite"
    attempt = store.begin_attempt(
        original.request_identity,
        binding,
        original.engine,
        original.model,
        12,
        lambda: store.binding_is_current(binding),
        retry=True,
    )
    assert attempt is not None and attempt[1] is None
    with sqlite3.connect(path) as observer:
        assert observer.execute(
            "SELECT state FROM description_advice_attempts ORDER BY rowid DESC LIMIT 1"
        ).fetchone() == ("requested",)
    assert store.start(attempt[0], 13, lambda: store.binding_is_current(binding))
    candidate = DescriptionAdviceCandidate.create(
        **{
            **original.model_dump(exclude={"candidate_id"}),
            "attempt_id": attempt[0],
            "created_at": 14,
        }
    )
    assert store.finish(
        attempt[0], candidate, 14, lambda: store.binding_is_current(binding)
    )
    assert store.begin_attempt(
        original.request_identity,
        binding,
        original.engine,
        original.model,
        15,
        lambda: store.binding_is_current(binding),
    ) == (attempt[0], candidate)
    with sqlite3.connect(path) as observer:
        assert observer.execute(
            "SELECT count(*) FROM description_advice_attempts"
        ).fetchone() == (2,)
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            observer.execute("UPDATE description_advice_candidates SET document='{}'")
    repository.close()
    reopened = open_repository(path, create=False)
    assert (
        reopened.description_advice_store().read(binding.review_case_id)["state"]
        == "succeeded"
    )
    reopened.close()


@pytest.mark.parametrize(
    "fixture", ["base-schema-before-f39.json", "base-schema-v39-before-f39.json"]
)
def test_base_schema_is_rejected_without_mutation(tmp_path: Path, fixture: str) -> None:
    from mechanical_librarian.application.metadata_report import MetadataReportError

    path = tmp_path / "predecessor.sqlite"
    objects = json.loads(
        (
            Path(__file__).parents[1]
            / "support/automatic_decision_predecessors"
            / fixture
        ).read_text()
    )
    with sqlite3.connect(path) as connection:
        for kind in ("table", "index", "trigger"):
            for actual_kind, _name, statement in objects:
                if actual_kind == kind and statement is not None:
                    connection.execute(statement)
    before = path.read_bytes()
    with pytest.raises(MetadataReportError):
        open_repository(path, create=False)
    assert path.read_bytes() == before


def _publish_successor(repository, binding):
    from mechanical_librarian.domain.metadata_policy import human_review_destinations
    from mechanical_librarian.domain.metadata_report import TargetField
    from mechanical_librarian.domain.proposal_set import evaluate_report
    from tests.support.automatic_no_change import _automatic_no_change_inputs
    from tests.support.proposal_evidence import _report

    original, *_ = _automatic_no_change_inputs("description_preserved")
    successor = _report(
        fields=tuple(
            TargetField(field.destination, field.state, "Observed successor text.")
            if field.destination.value == "description"
            else field
            for field in original.target.fields
        ),
        local_claims=original.local_claims,
        providers=original.providers,
    )
    resolved = repository.current_resolved_metadata_policy(successor.target.library)
    proposal = evaluate_report(
        successor, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.publish(successor, lambda: True, expected_current=original.report_id)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    return successor, binding.model_copy(
        update={
            "review_case_id": case_id,
            "report_id": successor.report_id,
            "proposal_set_id": proposal.proposal_set_id,
            "target_fingerprint": successor.target.fingerprint,
        }
    )


@pytest.mark.parametrize(
    ("phase", "outcome"),
    [
        ("requested", "interrupted"),
        ("running", "success"),
        ("running", "failed"),
        ("running", "interrupted"),
    ],
)
def test_coalesced_retry_is_consumed_and_terminal_continuation_does_not_retry(
    tmp_path, phase, outcome
):
    from tests.support.description_advice import _prepared

    repository, store, binding, original, _ = _prepared(tmp_path)
    attempt = store.begin_attempt(
        original.request_identity,
        binding,
        original.engine,
        original.model,
        12,
        lambda: store.binding_is_current(binding),
        retry=True,
    )
    assert attempt is not None
    if phase == "running":
        assert store.start(attempt[0], 13, lambda: store.binding_is_current(binding))
    _report, fresh = _publish_successor(repository, binding)
    store.request_retry(fresh.review_case_id, 14)
    assert store.pending_retry(fresh.review_case_id)
    assert (
        store.begin_attempt(
            original.request_identity,
            fresh,
            original.engine,
            original.model,
            15,
            lambda: store.binding_is_current(fresh),
        )
        is None
    )
    assert repository._connection.execute(
        "SELECT retry FROM description_preparation_pending WHERE review_case_id=?",
        (fresh.review_case_id,),
    ).fetchone() == (0,)
    if outcome == "interrupted":
        if phase == "requested":
            assert (
                store.start(attempt[0], 16, lambda: store.binding_is_current(binding))
                is False
            )
        store.interrupt_recovered(16)
        candidate = None
    else:
        candidate = (
            DescriptionAdviceCandidate.create(
                **{
                    **original.model_dump(exclude={"candidate_id"}),
                    "attempt_id": attempt[0],
                    "created_at": 16,
                }
            )
            if outcome == "success"
            else None
        )
        assert (
            store.finish(
                attempt[0],
                candidate,
                16,
                lambda: False,
                failure="invalid_output" if candidate is None else None,
            )
            is False
        )
    before = store.read(fresh.review_case_id)
    assert before["current"] is (candidate is None)
    assert (before["candidate"] is not None) is (candidate is not None)
    reused = store.begin_attempt(
        original.request_identity,
        fresh,
        original.engine,
        original.model,
        17,
        lambda: store.binding_is_current(fresh),
    )
    assert reused == ((attempt[0], candidate) if candidate is not None else None)
    assert store.read(fresh.review_case_id)["current"] is True
    assert (store.read(fresh.review_case_id)["candidate"] is not None) is (
        candidate is not None
    )
    assert (
        repository._connection.execute(
            "SELECT 1 FROM description_preparation_pending WHERE review_case_id=?",
            (fresh.review_case_id,),
        ).fetchone()
        is None
    )
    assert repository._connection.execute(
        "SELECT count(*) FROM description_advice_attempts"
    ).fetchone() == (2,)
    repository.close()


@pytest.mark.parametrize("replace_pointer", [False, True])
def test_late_completion_preserves_replaced_pointer_and_newer_retry(
    tmp_path, replace_pointer
):
    from mechanical_librarian.application.description_advice import (
        DescriptionAdviceService,
    )
    from mechanical_librarian.domain.description_advice import (
        DescriptionAdviceSettings,
        description_digest,
    )
    from tests.support.description_advice import _prepared

    captured = []
    repository, store, binding, original, _ = _prepared(
        tmp_path,
        pipeline_probe=lambda _r, _report, _d, access, calls: captured.append(
            (access, calls)
        ),
    )
    access, calls = captured[0]
    attempt = store.begin_attempt(
        original.request_identity,
        binding,
        original.engine,
        original.model,
        12,
        lambda: store.binding_is_current(binding),
        retry=True,
    )
    assert attempt is not None
    assert store.start(attempt[0], 13, lambda: store.binding_is_current(binding))
    report, fresh = _publish_successor(repository, binding)
    assert (
        store.begin_attempt(
            original.request_identity,
            fresh,
            original.engine,
            original.model,
            14,
            lambda: store.binding_is_current(fresh),
        )
        is None
    )
    candidate = None
    if replace_pointer:
        settings = DescriptionAdviceSettings(remove_marketing=False)
        store.bind_settings(settings)
        replacement = fresh.model_copy(
            update={"preferences_digest": description_digest(settings.model_dump())}
        )
        candidate = DescriptionAdviceService(
            store, access, store.current_settings, lambda: 15
        ).prepare(report, replacement, lambda: store.binding_is_current(replacement))
        assert (
            candidate is not None
            and candidate.request_identity != original.request_identity
        )
        assert len(calls) == 2
    store.request_retry(fresh.review_case_id, 16)
    old_candidate = DescriptionAdviceCandidate.create(
        **{
            **original.model_dump(exclude={"candidate_id"}),
            "attempt_id": attempt[0],
            "created_at": 17,
        }
    )
    assert store.finish(attempt[0], old_candidate, 17, lambda: False) is False
    assert store.read(fresh.review_case_id)["candidate"]["candidate_id"] == (
        candidate.candidate_id if candidate is not None else old_candidate.candidate_id
    )
    assert store.read(fresh.review_case_id)["current"] is replace_pointer
    assert repository._connection.execute(
        "SELECT retry,requested_at FROM description_preparation_pending WHERE review_case_id=?",
        (fresh.review_case_id,),
    ).fetchone() == (1, 16)
    repository.close()


def test_retry_arriving_during_public_identity_read_is_consumed_as_retry(tmp_path):
    from mechanical_librarian.application.description_advice import (
        DescriptionAdviceService,
    )
    from tests.support.description_advice import _prepared

    captured = []
    repository, store, binding, original, _ = _prepared(
        tmp_path,
        pipeline_probe=lambda _repository, report, _decision, access, calls: (
            captured.append((report, access, calls))
        ),
    )
    report, access, calls = captured[0]
    identity = access.current_description_rewrite_identity
    captured_retry = store.pending_retry(binding.review_case_id)
    assert captured_retry is False

    def arriving_retry():
        store.request_retry(binding.review_case_id, 11)
        return identity()

    access.current_description_rewrite_identity = arriving_retry
    service = DescriptionAdviceService(
        store, access, store.current_settings, lambda: 12
    )
    candidate = service.prepare(
        report, binding, lambda: store.binding_is_current(binding), retry=captured_retry
    )
    access.current_description_rewrite_identity = identity
    assert candidate is not None and candidate.candidate_id != original.candidate_id
    assert len(calls) == 2
    assert repository._connection.execute(
        "SELECT count(*) FROM description_advice_attempts"
    ).fetchone() == (2,)
    assert not store.pending_retry(binding.review_case_id)
    assert (
        service.prepare(report, binding, lambda: store.binding_is_current(binding))
        == candidate
    )
    assert len(calls) == 2
    repository.close()


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "interrupted"])
@pytest.mark.parametrize("pending", [False, True])
def test_terminal_attempt_requires_persisted_or_explicit_retry(
    tmp_path, terminal, pending
):
    from mechanical_librarian.application.description_advice import (
        DescriptionAdviceService,
    )
    from tests.support.description_advice import _prepared

    captured = []
    repository, store, binding, original, _ = _prepared(
        tmp_path,
        pipeline_probe=lambda _repository, report, _decision, access, calls: (
            captured.append((report, access, calls))
        ),
    )
    report, access, calls = captured[0]
    prior_count = 1
    if terminal != "succeeded":
        attempt = store.begin_attempt(
            original.request_identity,
            binding,
            original.engine,
            original.model,
            11,
            lambda: store.binding_is_current(binding),
            retry=True,
        )
        assert attempt is not None
        assert store.start(attempt[0], 12, lambda: store.binding_is_current(binding))
        if terminal == "failed":
            assert store.finish(
                attempt[0],
                None,
                13,
                lambda: store.binding_is_current(binding),
                failure="invalid_output",
            )
        else:
            store.interrupt_recovered(13)
        prior_count = 2
    if pending:
        store.request_retry(binding.review_case_id, 14)
        assert (
            store.begin_attempt(
                original.request_identity,
                binding,
                original.engine,
                original.model,
                15,
                lambda: False,
            )
            is None
        )
        assert store.pending_retry(binding.review_case_id)
    service = DescriptionAdviceService(
        store, access, store.current_settings, lambda: 16
    )
    candidate = service.prepare(
        report, binding, lambda: store.binding_is_current(binding)
    )
    if pending:
        assert candidate is not None and candidate.candidate_id != original.candidate_id
    else:
        assert candidate == (original if terminal == "succeeded" else None)
    assert len(calls) == (2 if pending else 1)
    assert repository._connection.execute(
        "SELECT count(*) FROM description_advice_attempts"
    ).fetchone() == (prior_count + int(pending),)
    assert not store.pending_retry(binding.review_case_id)
    assert (
        service.prepare(report, binding, lambda: store.binding_is_current(binding))
        == candidate
    )
    assert len(calls) == (2 if pending else 1)
    repository.close()


@pytest.mark.parametrize("origin", ["human", "automatic_policy"])
@pytest.mark.parametrize(
    "state", ["requested", "running", "failed", "interrupted", "succeeded"]
)
def test_selected_advice_survives_retry(tmp_path, origin, state):
    from tests.support.description_advice import _prepared

    repository, store, binding, candidate, _ = _prepared(tmp_path)
    decision = store.admit(candidate.candidate_id, binding, origin, "a" * 64, 11)
    assert decision is not None
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
    latest = None
    if state != "requested":
        assert store.start(attempt[0], 13, lambda: store.binding_is_current(binding))
    if state == "failed":
        assert store.finish(
            attempt[0],
            None,
            14,
            lambda: store.binding_is_current(binding),
            failure="unavailable",
        )
    elif state == "interrupted":
        store.interrupt_recovered(14)
    elif state == "succeeded":
        latest = DescriptionAdviceCandidate.create(
            **{
                **candidate.model_dump(exclude={"candidate_id"}),
                "attempt_id": attempt[0],
                "created_at": 14,
            }
        )
        assert store.finish(
            attempt[0], latest, 14, lambda: store.binding_is_current(binding)
        )
    advice = store.read(binding.review_case_id)
    assert advice["state"] == state
    assert advice["candidate"] == (None if latest is None else latest.model_dump())
    assert advice["selection"]["decision_id"] == decision.decision_id
    assert (
        advice["selected_advice"]["candidate"]["candidate_id"] == candidate.candidate_id
    )
    assert advice["selected_advice"]["current"] is True
    assert store.authority_value(decision) is not None
    if latest is not None and origin == "human":
        with pytest.raises(ValueError, match="automatic admission unavailable"):
            store.admit(latest.candidate_id, binding, "automatic_policy", "b" * 64, 15)
        replacement = store.admit(latest.candidate_id, binding, "human", "c" * 64, 15)
        assert replacement.candidate_id == latest.candidate_id
    repository.close()
    reopened = open_repository(tmp_path / "reports.sqlite", create=False)
    retained = reopened.description_advice_store().read(binding.review_case_id)
    assert retained["selection"]["decision_id"] == (
        replacement.decision_id
        if latest is not None and origin == "human"
        else decision.decision_id
    )
    assert (
        retained["selected_advice"]["candidate"]["candidate_id"]
        == retained["selection"]["candidate_id"]
    )
    reopened.close()


@pytest.mark.parametrize("reselect", ["before", "after", "never"])
def test_disabled_task_cancellation_preserves_reselection_intent(tmp_path, reselect):
    from mechanical_librarian.application.description_advice import (
        DescriptionAdviceService,
    )
    from tests.support.description_advice import _prepared

    captured = []
    repository, store, binding, _, _ = _prepared(
        tmp_path,
        pipeline_probe=lambda _repo, report, _decision, access, calls: captured.append(
            (report, access, calls)
        ),
    )
    report, access, calls = captured[0]
    store.request_retry(binding.review_case_id, 20)
    access.select_none(expected_revision=2, task="description_rewrite")
    with pytest.raises(ValueError, match="retry unavailable"):
        store.request_retry(binding.review_case_id, 21)
    if reselect == "before":
        access.select_codex(expected_revision=3, task="description_rewrite")
    service = DescriptionAdviceService(
        store, access, store.current_settings, lambda: 22
    )
    assert (
        service.prepare(report, binding, lambda: store.binding_is_current(binding))
        is None
    )
    assert repository._connection.execute(
        "SELECT count(*) FROM description_preparation_pending"
    ).fetchone() == ((1,) if reselect == "before" else (0,))
    if reselect == "after":
        access.select_codex(expected_revision=3, task="description_rewrite")
        assert repository._connection.execute(
            "SELECT count(*) FROM description_preparation_pending"
        ).fetchone() == (1,)
    assert len(calls) == 1
    repository.close()


def test_configured_unavailable_identity_keeps_pending_intent(tmp_path, monkeypatch):
    from mechanical_librarian.application.description_advice import (
        DescriptionAdviceService,
    )
    from tests.support.description_advice import _prepared

    captured = []
    repository, store, binding, _, _ = _prepared(
        tmp_path,
        pipeline_probe=lambda _repo, report, _decision, access, calls: captured.append(
            (report, access, calls)
        ),
    )
    report, access, calls = captured[0]
    store.request_retry(binding.review_case_id, 20)
    monkeypatch.setattr(access, "current_description_rewrite_identity", lambda: None)
    assert (
        DescriptionAdviceService(
            store, access, store.current_settings, lambda: 21
        ).prepare(report, binding, lambda: True)
        is None
    )
    assert store.pending_retry(binding.review_case_id)
    assert len(calls) == 1
    repository.close()


def test_disabled_late_success_keeps_history_without_resurrecting_continuation(
    tmp_path,
):
    from tests.support.description_advice import _prepared

    captured = []
    repository, store, binding, original, _ = _prepared(
        tmp_path,
        pipeline_probe=lambda _repo, _report, _decision, access, _calls: (
            captured.append(access)
        ),
    )
    attempt = store.begin_attempt(
        original.request_identity,
        binding,
        original.engine,
        original.model,
        12,
        lambda: store.binding_is_current(binding),
        retry=True,
    )
    assert attempt is not None
    assert store.start(attempt[0], 13, lambda: store.binding_is_current(binding))
    _, fresh = _publish_successor(repository, binding)
    assert (
        store.begin_attempt(
            original.request_identity,
            fresh,
            original.engine,
            original.model,
            14,
            lambda: store.binding_is_current(fresh),
        )
        is None
    )
    captured[0].select_none(expected_revision=2, task="description_rewrite")
    store.consume_pending_if_unconfigured(fresh.review_case_id)
    candidate = DescriptionAdviceCandidate.create(
        **{
            **original.model_dump(exclude={"candidate_id"}),
            "attempt_id": attempt[0],
            "created_at": 15,
        }
    )
    assert store.finish(attempt[0], candidate, 15, lambda: False) is False
    assert repository._connection.execute(
        "SELECT count(*) FROM description_preparation_pending"
    ).fetchone() == (0,)
    assert (
        store.read(fresh.review_case_id)["candidate"]["candidate_id"]
        == candidate.candidate_id
    )
    assert store.read(fresh.review_case_id)["current"] is False
    repository.close()


@pytest.mark.parametrize(
    "candidate_present,failure",
    [
        (False, None),
        (True, "unavailable"),
        (True, "invalid_output"),
        (False, "unknown"),
    ],
)
def test_finish_rejects_inconsistent_success_failure_without_mutation(
    tmp_path, candidate_present, failure
):
    from tests.support.description_advice import _prepared

    repository, store, binding, candidate, _ = _prepared(tmp_path)
    before = repository._connection.total_changes
    with pytest.raises(ValueError, match="failure mismatch"):
        store.finish(
            candidate.attempt_id,
            candidate if candidate_present else None,
            12,
            lambda: True,
            failure=failure,
        )
    assert repository._connection.total_changes == before
    assert store.read(binding.review_case_id)["state"] == "succeeded"
    repository.close()


def test_a_successor_carrying_the_owners_hold_admits_its_description_held(
    tmp_path,
):
    """F59 R10: the owner's authored description holds the field, and the hold
    follows the work to its successor. Automatic admission still records the
    successor's description, so the hold has a decision to hold and says so;
    nothing is written until Resume releases it. While held it is not pending
    either (R-D), so no pass resolves it into a failure and no permission offer
    counts it."""
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
    from mechanical_librarian.application.continuous_field_effects import (
        ContinuousGuardedApplication,
    )
    from mechanical_librarian.application.description_advice import (
        DescriptionAdviceService,
    )
    from mechanical_librarian.application.field_effects import GuardedFieldApplication
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase
    from mechanical_librarian.domain.field_effect import FieldEffectError
    from tests.support.description_advice import _prepared

    access = []
    repository, store, binding, original, _ = _prepared(
        tmp_path, pipeline_probe=lambda *values: access.append(values[3])
    )
    owner = store.admit(original.candidate_id, binding, "human", "e" * 64, 11)
    assert (
        repository.field_effect_manual_hold_token(binding.review_case_id, "description")
        == owner.decision_id
    )
    report, fresh = _publish_successor(repository, binding)
    successor = fresh.review_case_id
    assert (
        repository.field_effect_manual_hold_token(successor, "description")
        == owner.decision_id
    )
    candidate = DescriptionAdviceService(
        store, access[0], store.current_settings, lambda: 12
    ).prepare(report, fresh, lambda: store.binding_is_current(fresh))
    assert candidate is not None
    decision = store.admit(
        candidate.candidate_id, fresh, "automatic_policy", "a" * 64, 15
    )
    assert decision.origin == "automatic_policy"
    field = next(
        value
        for value in InspectCurrentReviewCase(repository).run(successor)["fields"]
        if value["destination"] == "description"
    )
    assert (field["manual_hold"], field["human_decision"]) == (True, None)
    # The admitted automatic description is the field's evaluation.
    assert field["automatic_withheld_reason"] is None
    authority = PersistedFieldEffectAuthority(repository)
    with pytest.raises(FieldEffectError):
        authority.resolve(decision)

    def pending() -> tuple[list[object], list[object]]:
        documents = [
            document
            for document in repository.pending_field_effect_decision_documents(32)
            if json.loads(document)["decision_id"] == decision.decision_id
        ]
        counts = [
            row
            for row in repository.pending_field_effect_decision_counts()
            if row["destination"] == "description"
        ]
        return documents, counts

    assert pending() == ([], [])
    connection, library = repository._connection.execute(
        "SELECT connection_name,library_name FROM current_review_case_lineages "
        "WHERE review_case_id=?",
        (successor,),
    ).fetchone()
    repository.grant_field_effect_consents(connection, (library,), ("description",), 16)
    capabilities = FakeCapabilityObserver("c" * 64)
    application = GuardedFieldApplication(
        repository,
        FakeTargetService(),
        FakeBackupReadiness(),
        capabilities,
        authority,
        FakeDependencyCurrentness(),
        PersistedEffectConsent(repository),
        SimpleNamespace(),
        FakeForwardReevaluation(),
        lambda: 17,
    )
    scheduler = ContinuousGuardedApplication(
        repository,
        {(connection, library): application},
        {(connection, library): capabilities},
        authority,
        ((connection, library),),
        lambda: 17,
        lambda *_: None,
    )
    assert scheduler.schedule() == 0
    assert scheduler.activity()["last_failure"] is None
    assert repository._connection.execute(
        "SELECT count(*) FROM field_effects WHERE decision_id=?",
        (decision.decision_id,),
    ).fetchone() == (0,)
    assert repository.clear_field_effect_manual_hold(
        successor, "description", 18, owner.decision_id
    )
    documents, counts = pending()
    assert len(documents) == 1
    assert [(row["origin"], row["count"]) for row in counts] == [("automatic", 1)]
    assert authority.resolve(decision).decision_value == {
        "kind": "text",
        "value": "Authored synthetic description.",
    }
    repository.close()
