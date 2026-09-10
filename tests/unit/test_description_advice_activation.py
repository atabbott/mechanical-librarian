"""Real request, persisted authored admission and guarded synthetic effect proofs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.contained_reviewer_contract import (
    canonical_json,
)
from mechanical_librarian.application.description_advice import DescriptionAdviceService
from mechanical_librarian.application.reviewer_access import ReviewerAccessOperations
from mechanical_librarian.application.reviewer_host import ReviewerHostStatus
from mechanical_librarian.domain.description_advice import (
    DescriptionAdviceSettings,
)
from tests.support.description_advice import _guarded_application, _prepared
from tests.unit.test_reviewer_access_application import _Auth


@pytest.mark.parametrize("version_drift", [False, True])
def test_real_service_candidate_admits_through_persisted_authority(
    tmp_path: Path,
    version_drift: bool,
) -> None:
    repository, store, binding, candidate, deterministic = _prepared(tmp_path)
    decision = store.admit(
        candidate.candidate_id, binding, "automatic_policy", "a" * 64, 11
    )
    authority = PersistedFieldEffectAuthority(repository)
    snapshot = authority.resolve(decision)
    assert snapshot.decision_value == {
        "kind": "text",
        "value": "Authored synthetic description.",
    }
    assert (
        store.admit(candidate.candidate_id, binding, "automatic_policy", "a" * 64, 12)
        == decision
    )
    from tests.unit.test_field_effects import CAPABILITY

    application, target = _guarded_application(repository, tmp_path, authority)
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
    from dataclasses import replace

    assert authority.current(operation.plan.authority)
    assert not authority.current(
        replace(operation.plan.authority, source="fake_service")
    )
    assert not authority.current(
        replace(operation.plan.authority, qualification_identity="f" * 64)
    )
    target.seed(operation.plan)
    verified = application.apply(operation.plan.plan_id)
    assert verified.state == "verified"
    assert target.read(operation.plan).value == "Authored synthetic description."
    page = repository.field_effect_activity_page(
        search=None, event_type="all", occurred_after=0, page=1, page_size=25
    )
    assert any(
        event["state"] == "verified" and event["record"].get("advised") is True
        for event in page["events"]
    )
    assert any(
        event["record"]["source_kind"] == "description_decision"
        for event in page["events"]
    )

    from mechanical_librarian.application.review_web import RecordHumanDecision
    from mechanical_librarian.domain.human_decision import HumanDecision

    acknowledgement = RecordHumanDecision(repository, clock=lambda: 40).run(
        decision.review_case_id,
        proposal_id=deterministic.proposal_id,
        target_fingerprint=decision.target_fingerprint,
        destination="description",
        actor="owner",
        outcome="keep_current",
        selected_value=None,
        idempotency_key="restore-description-after-authored-write",
    )
    reversal = HumanDecision.from_json(
        repository._connection.execute(
            "SELECT document FROM human_decisions WHERE decision_id=?",
            (acknowledgement["decision_id"],),
        ).fetchone()[0]
    )
    assert reversal.to_json() in repository.pending_field_effect_decision_documents(100)
    restored = authority.resolve(reversal)
    assert restored.pre_value == "Authored synthetic description."
    assert restored.decision_value == {"kind": "text", "value": snapshot.pre_value}
    reverse_operation = application.plan(
        reversal,
        connection=restored.connection,
        library=restored.library,
        work_id=restored.work_id,
        destination=restored.destination,
        pre_value=restored.pre_value,
        desired_value=restored.decision_value,
        target_fingerprint=restored.target_fingerprint,
        target_version=restored.target_version,
        dependency_digests=restored.dependency_digests,
        capability_digest=CAPABILITY,
        allow_corrective_keep_current=True,
    )
    assert reverse_operation is not None
    if version_drift:
        target._versions[target._key(reverse_operation.plan)] = "f" * 64
    reversal_result = application.apply(reverse_operation.plan.plan_id)
    assert reversal_result.state == ("stale" if version_drift else "verified")
    if version_drift:
        assert reversal_result.failure == "target_changed"
        assert (
            target.read(reverse_operation.plan).value
            == "Authored synthetic description."
        )
    else:
        assert target.read(reverse_operation.plan).value == snapshot.pre_value
        # The ordinary human predecessor remains authoritative after it settles;
        # older authored history must not displace it on a later owner decision.
        next_ack = RecordHumanDecision(repository, clock=lambda: 50).run(
            decision.review_case_id,
            proposal_id=deterministic.proposal_id,
            target_fingerprint=decision.target_fingerprint,
            destination="description",
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key="keep-restored-description-after-reversal",
        )
        next_decision = HumanDecision.from_json(
            repository._connection.execute(
                "SELECT document FROM human_decisions WHERE decision_id=?",
                (next_ack["decision_id"],),
            ).fetchone()[0]
        )
        assert authority.resolve(next_decision).pre_value == snapshot.pre_value
    assert repository._connection.execute(
        "SELECT count(*) FROM description_decisions"
    ).fetchone() == (1,)
    assert repository._connection.execute(
        "SELECT count(*) FROM current_description_decisions"
    ).fetchone() == (0,)

    repository.close()
    reopened = open_repository(tmp_path / "reports.sqlite", create=False)
    reopened.close()


@pytest.mark.parametrize(
    "automatic,disposition", [(False, "ready"), (True, "needs_review")]
)
def test_parked_advice_preserves_deterministic_authority(
    tmp_path: Path, automatic: bool, disposition: str
) -> None:
    repository, store, binding, candidate, deterministic = _prepared(
        tmp_path, automatic=automatic, disposition=disposition
    )
    with pytest.raises(ValueError, match="automatic admission unavailable"):
        store.admit(candidate.candidate_id, binding, "automatic_policy", "a" * 64, 11)
    assert PersistedFieldEffectAuthority(repository).resolve(deterministic)
    assert repository._connection.execute(
        "SELECT count(*) FROM description_decisions"
    ).fetchone() == (0,)
    repository.close()


@pytest.mark.parametrize("field", ["preferences_digest", "request_digest"])
def test_mismatched_candidate_contract_cannot_gain_authority(
    tmp_path: Path, field: str
) -> None:
    from mechanical_librarian.domain.description_advice import (
        DescriptionAdviceCandidate,
    )

    repository, store, binding, candidate, _ = _prepared(tmp_path)
    attempt = store.begin_attempt(
        "e" * 64, binding, candidate.engine, candidate.model, 12, lambda: True
    )
    assert attempt is not None
    altered = DescriptionAdviceCandidate.create(
        **{
            **candidate.model_dump(exclude={"candidate_id"}),
            "attempt_id": attempt[0],
            field: "f" * 64,
            "request_identity": "e" * 64,
        }
    )
    assert store.start(attempt[0], 13, lambda: True)
    store.finish(attempt[0], altered, 14, lambda: True)
    with pytest.raises(ValueError, match="candidate request mismatch"):
        store.admit(altered.candidate_id, binding, "human", "a" * 64, 15)
    assert repository._connection.execute(
        "SELECT count(*) FROM description_decisions"
    ).fetchone() == (0,)
    repository.close()


def test_settings_drift_refuses_previously_admitted_authority(tmp_path: Path) -> None:
    from mechanical_librarian.domain.field_effect import FieldEffectError

    repository, store, binding, candidate, _ = _prepared(tmp_path)
    decision = store.admit(candidate.candidate_id, binding, "human", "a" * 64, 11)
    store.bind_settings(DescriptionAdviceSettings(remove_praise=False))
    with pytest.raises(FieldEffectError, match="authority_binding"):
        PersistedFieldEffectAuthority(repository).resolve(decision)
    repository.close()


@pytest.mark.parametrize("origin", ["human", "automatic_policy"])
def test_authored_same_text_uses_no_change_and_has_permanent_history(
    tmp_path: Path, origin: str
) -> None:
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase

    repository, store, binding, candidate, _ = _prepared(tmp_path, same_current=True)
    decision = store.admit(candidate.candidate_id, binding, origin, "a" * 64, 11)
    assert repository.record_current_field_effect_no_change(decision, 12)
    assert repository.record_current_field_effect_no_change(decision, 13)
    assert repository._connection.execute(
        "SELECT count(*) FROM field_effects"
    ).fetchone() == (0,)
    assert repository._connection.execute(
        "SELECT origin FROM field_effect_no_changes"
    ).fetchall() == [(origin,)]
    reader = InspectCurrentReviewCase(repository)
    field = next(
        field
        for field in reader.run(binding.review_case_id)["fields"]
        if field["destination"] == "description"
    )
    assert field["needs_attention"] is False
    assert field["description_advice"]["candidate"]["provenance"] == "authored_content"
    page = repository.field_effect_activity_page(
        search=None, event_type="all", occurred_after=0, page=1, page_size=25
    )
    assert any(
        event["state"] == "no_change_required"
        and event["record"].get("advised") is True
        for event in page["events"]
    )
    events = reader.history(binding.review_case_id)["items"]
    selected = next(
        event for event in events if event["kind"] == "description_selected"
    )
    assert selected["candidate"]["advised"] is True
    assert selected["decision"]["decision_id"] == decision.decision_id
    repository.close()
    reopened = open_repository(tmp_path / "reports.sqlite", create=False)
    reopened.close()


def test_real_projection_hook_prepares_and_admits_description(tmp_path: Path) -> None:
    from dataclasses import replace

    from mechanical_librarian.bootstrap import composition
    from mechanical_librarian.domain.proposal_set import ProposalSet
    from tests.unit.test_shadow_outcome_persistence import (
        _pipeline_configuration,
        _run_qualified_cover_pipeline,
    )

    def pipeline_probe(repository, report, deterministic, access, calls):
        configuration = _pipeline_configuration()
        assert configuration.review is not None
        configuration = replace(
            configuration,
            review=replace(
                configuration.review, description_automatic_application=True
            ),
        )
        recovery = composition.SQLiteProviderRecoveryStore.open(
            tmp_path / "provider-recovery.sqlite"
        )
        supervisor = composition.create_review_pipeline(
            configuration,
            tmp_path / "reports.sqlite",
            tmp_path / "catalogue.sqlite",
            tmp_path / "artifacts",
            access,
            database_authority=repository.authority,
            provider_recovery=recovery,
        )
        proposal = ProposalSet.from_json(
            repository._connection.execute(
                "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
                (deterministic.proposal_set_id,),
            ).fetchone()[0]
        )
        pipeline = _run_qualified_cover_pipeline(
            repository, tmp_path, report, proposal, supervisor
        )
        try:
            for _ in range(64):
                if not pipeline.run_next(1):
                    break
            else:
                pytest.fail("description pipeline did not settle")
            assert len(calls) == 1
            assert repository._connection.execute(
                "SELECT stage,state,failure_code FROM review_preparation_work WHERE item_id='item-1'"
            ).fetchone() == ("complete", "complete", None)
            assert repository._connection.execute(
                "SELECT count(*) FROM current_description_decisions"
            ).fetchone() == (1,)
            from mechanical_librarian.adapters.persistence.description_advice import (
                request_description_preparation,
            )

            for name in ("_extract", "_providers", "_propose", "_admit"):
                setattr(
                    pipeline,
                    name,
                    lambda *_: pytest.fail(
                        "unchanged advice requeue repeated upstream work"
                    ),
                )
            retained = repository._connection.execute(
                "SELECT report_id,proposal_set_id,review_case_id FROM review_preparation_work WHERE item_id='item-1'"
            ).fetchone()
            pipeline._clock = lambda: 20
            supervisor._pipeline = pipeline
            supervisor.set_paused(True)
            request_description_preparation(
                repository._connection, 20, review_case_id=deterministic.review_case_id
            )
            supervisor._schedule_due()
            assert pipeline.run_next(1) is False
            assert len(calls) == 1
            assert repository._connection.execute(
                "SELECT count(*) FROM description_preparation_pending"
            ).fetchone() == (1,)
            supervisor.set_paused(False)
            for _ in range(64):
                if not pipeline.run_next(1):
                    break
            else:
                pytest.fail("description projection requeue did not settle")
            assert len(calls) == 1
            assert (
                repository._connection.execute(
                    "SELECT report_id,proposal_set_id,review_case_id FROM review_preparation_work WHERE item_id='item-1'"
                ).fetchone()
                == retained
            )
            pipeline._clock = lambda: 30
            repository.description_advice_store().request_retry(
                deterministic.review_case_id, 30
            )
            for _ in range(64):
                if not pipeline.run_next(1):
                    break
            else:
                pytest.fail("explicit description retry did not settle")
            assert len(calls) == 2
            assert repository._connection.execute(
                "SELECT count(*) FROM description_advice_attempts"
            ).fetchone() == (2,)
        finally:
            supervisor.stop()
            recovery.close()

    repository, *_ = _prepared(tmp_path, pipeline_probe=pipeline_probe)
    repository.close()


@pytest.mark.parametrize(
    "malformed", ["not-json", "oversized", "extra", "duplicate", "wrong-request"]
)
def test_invalid_adviser_output_is_bounded_failed_history_without_retry(
    tmp_path, malformed
):
    def transform(response):
        if malformed == "not-json":
            return "private raw failure canary"
        if malformed == "oversized":
            return "x" * 65537
        if malformed == "duplicate":
            return response.replace('"result":', '"result":{},"result":')
        document = json.loads(response)
        if malformed == "extra":
            document["result"]["tool_instruction"] = "private raw failure canary"
        else:
            document["request_sha256"] = "f" * 64
        return canonical_json(document)

    repository, store, binding, candidate, deterministic = _prepared(
        tmp_path, output_transform=transform
    )
    assert candidate is None
    assert PersistedFieldEffectAuthority(repository).resolve(deterministic)
    attempt = repository._connection.execute(
        "SELECT state,failure,engine,model FROM description_advice_attempts"
    ).fetchone()
    assert attempt == ("failed", "invalid_output", "codex", "synthetic-model")
    assert repository._connection.execute(
        "SELECT count(*) FROM description_advice_candidates"
    ).fetchone() == (0,)
    assert repository._connection.execute(
        "SELECT count(*) FROM description_decisions"
    ).fetchone() == (0,)
    assert "private raw failure canary" not in str(store.read(binding.review_case_id))
    repository.close()
    reopened = open_repository(tmp_path / "reports.sqlite", create=False)
    assert (
        reopened.description_advice_store().read(binding.review_case_id)["state"]
        == "failed"
    )
    reopened.close()


@pytest.mark.parametrize("corruption", ["failure", "identity", "binding"])
def test_startup_rejects_incoherent_description_attempt_history(tmp_path, corruption):
    from mechanical_librarian.application.metadata_report import MetadataReportError

    repository, *_ = _prepared(tmp_path, output_transform=lambda _: "invalid")
    if corruption == "failure":
        repository._connection.execute(
            "UPDATE description_advice_attempts SET failure=NULL"
        )
    elif corruption == "identity":
        repository._connection.execute(
            "UPDATE description_advice_attempts SET request_identity=?", ("z" * 64,)
        )
    else:
        document = json.loads(
            repository._connection.execute(
                "SELECT binding_document FROM description_advice_attempts"
            ).fetchone()[0]
        )
        document["target_fingerprint"] = "f" * 64
        repository._connection.execute(
            "UPDATE description_advice_attempts SET binding_document=?",
            (canonical_json(document),),
        )
    repository.close()
    with pytest.raises(MetadataReportError):
        open_repository(tmp_path / "reports.sqlite", create=False)


def test_owner_decision_during_transport_wins_without_holding_sqlite_write_lock(
    tmp_path,
):
    import sqlite3

    from mechanical_librarian.application.review_web import RecordHumanDecision

    def owner_before_response(repository, access, binding, deterministic):
        # A second actual connection can acquire the writer while the model call
        # is active; the attempt is already durably running at this boundary.
        with sqlite3.connect(tmp_path / "reports.sqlite", timeout=0) as observer:
            observer.execute("BEGIN IMMEDIATE")
            assert observer.execute(
                "SELECT state FROM description_advice_attempts"
            ).fetchone() == ("running",)
            observer.rollback()
        RecordHumanDecision(repository, clock=lambda: 10).run(
            binding.review_case_id,
            proposal_id=deterministic.proposal_id,
            target_fingerprint=binding.target_fingerprint,
            destination="description",
            actor="owner",
            outcome="keep_current",
            selected_value=None,
            idempotency_key="owner-keeps-description-before-adviser-response",
        )

    repository, store, binding, candidate, _ = _prepared(
        tmp_path, during_dispatch=owner_before_response
    )
    assert candidate is not None
    with pytest.raises(ValueError):
        store.admit(candidate.candidate_id, binding, "automatic_policy", "e" * 64, 11)
    assert repository._connection.execute(
        "SELECT count(*) FROM description_decisions"
    ).fetchone() == (0,)
    assert repository._connection.execute(
        "SELECT count(*) FROM human_decisions"
    ).fetchone() == (1,)
    assert store.read(binding.review_case_id)["candidate"]["advised"] is True
    repository.close()


def test_second_preparation_worker_observes_running_attempt_without_lock_or_dispatch(
    tmp_path,
):
    from concurrent.futures import ThreadPoolExecutor

    from mechanical_librarian.domain.metadata_report import MetadataReport

    second_calls = []

    def blocked_dispatch(repository, access, binding, deterministic):
        def second_worker():
            second = open_repository(
                tmp_path / "reports.sqlite", authority=repository.authority
            )
            store = second.description_advice_store()
            settings = DescriptionAdviceSettings(automatic_application=True)
            store.bind_settings(settings)
            report = MetadataReport.from_json(
                second.get_review_case_report(binding.review_case_id)
            )

            def unexpected_dispatch(*_args) -> str:
                second_calls.append("unexpected dispatch")
                pytest.fail("unchanged in-flight request must not dispatch")

            other_access = ReviewerAccessOperations(
                second.reviewer_access_store(),
                codex_auth=_Auth("usable"),
                codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
                codex_identity=lambda: (
                    "synthetic-model",
                    b"synthetic-contained-profile",
                ),
                codex_binding=lambda: (
                    "synthetic-model",
                    b"synthetic-contained-profile",
                    unexpected_dispatch,
                ),
                openrouter_factory=lambda *_: pytest.fail("unexpected engine"),
            )
            try:
                service = DescriptionAdviceService(
                    store, other_access, lambda: settings, lambda: 10
                )
                assert (
                    service.prepare(
                        report, binding, lambda: store.binding_is_current(binding)
                    )
                    is None
                )
                assert second._connection.execute(
                    "SELECT state FROM description_advice_attempts"
                ).fetchone() == ("running",)
                second._connection.execute("BEGIN IMMEDIATE")
                second._connection.rollback()
            finally:
                second.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(second_worker).result(timeout=2)

    repository, *_ = _prepared(tmp_path, during_dispatch=blocked_dispatch)
    assert second_calls == []
    assert repository._connection.execute(
        "SELECT count(*) FROM description_advice_attempts"
    ).fetchone() == (1,)
    repository.close()


def test_interrupted_explicit_attempt_does_not_fall_back_to_older_success_or_retry(
    tmp_path,
):
    repository, store, binding, candidate, _ = _prepared(tmp_path)
    interrupted = store.begin_attempt(
        candidate.request_identity,
        binding,
        candidate.engine,
        candidate.model,
        20,
        lambda: True,
        retry=True,
    )
    assert interrupted is not None
    assert store.start(interrupted[0], 20, lambda: True)
    store.interrupt_recovered(21)
    assert store.read(binding.review_case_id)["state"] == "interrupted"
    assert store.read(binding.review_case_id)["candidate"] is None
    assert (
        store.begin_attempt(
            candidate.request_identity,
            binding,
            candidate.engine,
            candidate.model,
            22,
            lambda: True,
        )
        is None
    )
    assert repository._connection.execute(
        "SELECT count(*) FROM description_advice_attempts"
    ).fetchone() == (2,)
    repository.close()
    reopened = open_repository(tmp_path / "reports.sqlite", create=False)
    recovered = reopened.description_advice_store()
    assert recovered.read(binding.review_case_id)["state"] == "interrupted"
    assert (
        recovered.begin_attempt(
            candidate.request_identity,
            binding,
            candidate.engine,
            candidate.model,
            23,
            lambda: True,
        )
        is None
    )
    retry = recovered.begin_attempt(
        candidate.request_identity,
        binding,
        candidate.engine,
        candidate.model,
        24,
        lambda: True,
        retry=True,
    )
    assert retry is not None and retry[0] != interrupted[0] and retry[1] is None
    assert reopened._connection.execute(
        "SELECT count(*) FROM description_advice_attempts"
    ).fetchone() == (3,)
    reopened.close()


def test_advice_automatic_off_retains_qualified_deterministic_description_fill(
    tmp_path,
):
    from tests.support.description_advice import _description_candidate
    from tests.support.proposal_evidence import _target_work_report

    source = "This complete provider description contains specific words about a synthetic journey through an old library and the people who preserve its stories."
    report = _target_work_report(
        (
            (
                "catalogue",
                "hardcover",
                (_description_candidate("catalogue", "edition-1", "work-1", source),),
                True,
            ),
        )
    )
    repository, store, binding, candidate, deterministic = _prepared(
        tmp_path, automatic=False, report_override=report
    )
    assert deterministic.outcome == "candidate"
    snapshot = PersistedFieldEffectAuthority(repository).resolve(deterministic)
    assert snapshot.pre_value is None
    assert snapshot.decision_value == {"kind": "text", "value": source}
    assert (
        deterministic.to_json()
        in repository.pending_field_effect_decision_documents(100)
    )
    with pytest.raises(ValueError):
        store.admit(candidate.candidate_id, binding, "automatic_policy", "a" * 64, 11)
    assert (
        deterministic.to_json()
        in repository.pending_field_effect_decision_documents(100)
    )
    from tests.unit.test_field_effects import CAPABILITY

    application, target = _guarded_application(
        repository, tmp_path, PersistedFieldEffectAuthority(repository)
    )
    operation = application.plan(
        deterministic,
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
    target.seed(operation.plan)
    assert application.apply(operation.plan.plan_id).state == "verified"
    assert target.read(operation.plan).value == source
    repository.close()
