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
from mechanical_librarian.application.reviewer_contracts import (
    ReviewerProvisioningStatus,
)
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
    assert store.read(binding.review_case_id)["application_outcome"] is None
    snapshot = authority.resolve(decision)
    assert snapshot.decision_value == {
        "kind": "text",
        "value": "Authored synthetic description.",
    }
    assert (
        store.admit(candidate.candidate_id, binding, "automatic_policy", "a" * 64, 12)
        == decision
    )
    from tests.support.field_effects import CAPABILITY

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
    advice = store.read(binding.review_case_id)
    assert advice["current"] is False
    assert advice["application_outcome"] == "verified"
    assert advice["selection"]["decision_id"] == decision.decision_id
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase

    description = next(
        field
        for field in InspectCurrentReviewCase(repository).run(binding.review_case_id)[
            "fields"
        ]
        if field["destination"] == "description"
    )
    assert description["needs_attention"] is False
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

    application_history = repository.get_review_case_history(
        decision.review_case_id, 50
    )
    assert any(
        event["kind"] == "description_application"
        and event["state"] == "verified"
        and event["candidate"]["candidate_id"] == candidate.candidate_id
        for event in application_history
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
    assert any(
        event["kind"] == "description_selected" and event["state"] == "superseded"
        for event in repository.get_review_case_history(decision.review_case_id, 50)
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
        candidate.request_identity,
        binding,
        candidate.engine,
        candidate.model,
        12,
        lambda: True,
        retry=True,
    )
    assert attempt is not None
    altered = DescriptionAdviceCandidate.create(
        **{
            **candidate.model_dump(exclude={"candidate_id"}),
            "attempt_id": attempt[0],
            field: "f" * 64,
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
    assert field["description_advice"]["application_outcome"] == "no_change_required"
    assert field["description_advice"]["current"] is True
    page = repository.field_effect_activity_page(
        search=None, event_type="all", occurred_after=0, page=1, page_size=25
    )
    assert any(
        event["state"] == "no_change_required"
        and event["record"].get("advised") is True
        for event in page["events"]
    )
    events = reader.history(binding.review_case_id)["items"]
    assert any(
        event["kind"] == "description_application"
        and event["state"] == "no_change_required"
        and event["candidate"]["advised"] is True
        for event in events
    )
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
    from tests.support.description_advice import _contained_profile
    from tests.unit.test_shadow_outcome_persistence import (
        _pipeline_configuration,
        _run_qualified_cover_pipeline,
    )

    profile = _contained_profile(tmp_path)

    def pipeline_probe(repository, report, deterministic, access, calls):
        configuration = _pipeline_configuration()
        assert configuration.review is not None
        configuration = replace(
            configuration,
            review=replace(
                configuration.review,
                description_automatic_application=True,
                cover_reviewer_profile_file=str(profile),
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
            repository,
            tmp_path,
            report,
            proposal,
            supervisor,
            repository_factory=supervisor._pipeline._repository_factory,
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
            for factory in (
                supervisor._pipeline._repository_factory,
                supervisor._pipeline._observation_repository_factory,
            ):
                bound = factory()
                original_profile = profile.read_bytes()
                try:
                    assert (
                        bound.description_advice_store().read(
                            deterministic.review_case_id
                        )["current"]
                        is True
                    )
                    changed_profile = json.loads(original_profile)
                    changed_profile["model"] = "changed-synthetic-model"
                    profile.write_text(canonical_json(changed_profile))
                    assert (
                        bound.description_advice_store().read(
                            deterministic.review_case_id
                        )["current"]
                        is False
                    )
                finally:
                    profile.write_bytes(original_profile)
                    bound.close()
            assert len(calls) == 1
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

    repository, *_ = _prepared(
        tmp_path,
        pipeline_probe=pipeline_probe,
        contained_identity=lambda: ("gpt-5.6-sol", profile.read_bytes()),
    )
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
    assert attempt == ("failed", "invalid_output", "codex", "gpt-5.6-sol")
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
            store.bind_identity(repository.description_advice_store()._identity)
            report = MetadataReport.from_json(
                second.get_review_case_report(binding.review_case_id)
            )

            def unexpected_dispatch(*_args) -> str:
                second_calls.append("unexpected dispatch")
                pytest.fail("unchanged in-flight request must not dispatch")

            other_access = ReviewerAccessOperations(
                second.reviewer_access_store(),
                codex_auth=_Auth("usable"),
                codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(
                    True, None, model="gpt-5.6-sol"
                ),
                codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
                codex_identity=lambda: (
                    "gpt-5.6-sol",
                    b"synthetic-contained-profile",
                ),
                codex_binding=lambda: (
                    "gpt-5.6-sol",
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
    settings = store.current_settings()
    repository.close()
    reopened = open_repository(tmp_path / "reports.sqlite", create=False)
    recovered = reopened.description_advice_store()
    recovered.bind_settings(settings)
    recovered.bind_identity(
        lambda engine, model: (candidate.model, candidate.profile_digest)
    )
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
    from tests.support.field_effects import CAPABILITY

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
    assert store.read(binding.review_case_id)["current"] is False
    assert store.read(binding.review_case_id)["application_outcome"] is None
    with pytest.raises(ValueError, match="description advice is stale"):
        store.admit(candidate.candidate_id, binding, "human", "b" * 64, 30)
    repository.close()


def test_fake_authority_requires_explicit_value_for_persisted_authored_decision(
    tmp_path: Path,
) -> None:
    from mechanical_librarian.adapters.field_effect_fakes import (
        FakeAuthorityCurrentness,
    )

    repository, store, binding, candidate, _ = _prepared(tmp_path)
    decision = store.admit(candidate.candidate_id, binding, "human", "a" * 64, 11)
    persisted = PersistedFieldEffectAuthority(repository).resolve(decision)
    fake = FakeAuthorityCurrentness()
    with pytest.raises(KeyError):
        fake.bind(
            decision,
            connection=persisted.connection,
            library=persisted.library,
            work_id=persisted.work_id,
            target_version=persisted.target_version,
        )
    fake.authored_values[decision.decision_id] = persisted.decision_value
    fake.bind(
        decision,
        connection=persisted.connection,
        library=persisted.library,
        work_id=persisted.work_id,
        target_version=persisted.target_version,
    )
    assert fake.resolve(decision).decision_value == persisted.decision_value
    assert fake.resolve(decision).source == "fake_service"
    repository.close()


@pytest.mark.parametrize("origin", ["human", "automatic_policy"])
@pytest.mark.parametrize("drift", ["model", "profile", "unavailable"])
def test_live_contained_identity_drift_refuses_admission_and_authority(
    tmp_path: Path,
    origin: str,
    drift: str,
) -> None:
    from mechanical_librarian.domain.field_effect import FieldEffectError

    identity = ("gpt-5.6-sol", b"synthetic-contained-profile")
    unavailable = False

    def current_identity() -> tuple[str, bytes]:
        if unavailable:
            raise RuntimeError("synthetic profile unavailable")
        return identity

    repository, store, binding, candidate, _ = _prepared(
        tmp_path, contained_identity=current_identity
    )
    decision = store.admit(candidate.candidate_id, binding, origin, "a" * 64, 11)
    authority = PersistedFieldEffectAuthority(repository)
    assert authority.resolve(decision).decision_value == {
        "kind": "text",
        "value": candidate.output.description,
    }
    if drift == "model":
        identity = ("changed-synthetic-model", identity[1])
    elif drift == "profile":
        identity = (identity[0], b"changed-synthetic-profile")
    else:
        unavailable = True
    assert store.read(binding.review_case_id)["current"] is False
    assert (
        store.read(binding.review_case_id)["candidate"]["candidate_id"]
        == candidate.candidate_id
    )
    with pytest.raises(ValueError, match="candidate request mismatch"):
        store.admit(candidate.candidate_id, binding, origin, "b" * 64, 12)
    with pytest.raises(FieldEffectError, match="authority_binding"):
        authority.resolve(decision)
    # Idempotent replay is historical acknowledgement, not renewed authority.
    assert (
        store.admit(candidate.candidate_id, binding, origin, "a" * 64, 12) == decision
    )
    assert any(
        event["kind"] == "description_selected"
        for event in repository.get_review_case_history(binding.review_case_id, 50)
    )
    repository.close()


@pytest.mark.parametrize("boundary", ["preclaim", "claim", "target"])
def test_profile_drift_between_guarded_boundaries_never_writes(
    tmp_path: Path,
    boundary: str,
) -> None:
    from mechanical_librarian.domain.field_effect import FieldEffectError
    from tests.support.field_effects import CAPABILITY

    identity = ("gpt-5.6-sol", b"synthetic-contained-profile")
    calls = []

    def capture(_repository, _report, _decision, _access, dispatched):
        calls.append(dispatched)

    repository, store, binding, candidate, _ = _prepared(
        tmp_path, contained_identity=lambda: identity, pipeline_probe=capture
    )
    decision = store.admit(candidate.candidate_id, binding, "human", "a" * 64, 11)
    authority = PersistedFieldEffectAuthority(repository)
    snapshot = authority.resolve(decision)
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
    target.seed(operation.plan)
    if boundary == "target":
        repository.claim_field_effect(
            operation.plan.plan_id, "planned", "b" * 64, application._clock()
        )
    identity = (identity[0], b"changed-synthetic-profile")
    if boundary == "claim":
        with pytest.raises(FieldEffectError, match="authority_binding"):
            repository.claim_field_effect(
                operation.plan.plan_id, "planned", "b" * 64, 14
            )
    else:
        assert application.apply(operation.plan.plan_id).state == "stale"
    assert target.read(operation.plan).value == snapshot.pre_value
    assert target._effects == {}
    assert len(calls[0]) == 1
    repository.close()


def test_primary_and_independent_activity_bind_passive_profile_after_reopen(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace

    from mechanical_librarian.bootstrap import composition
    from tests.support.description_advice import _contained_profile
    from tests.unit.test_shadow_outcome_persistence import _pipeline_configuration

    profile = _contained_profile(tmp_path)
    repository, store, binding, candidate, _ = _prepared(
        tmp_path, contained_identity=lambda: ("gpt-5.6-sol", profile.read_bytes())
    )
    configuration = _pipeline_configuration()
    assert configuration.review is not None
    configuration = replace(
        configuration,
        review=replace(
            configuration.review,
            cover_reviewer_profile_file=str(profile),
            description_automatic_application=True,
        ),
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail(
            "passive identity must not touch auth, access serialization, or transport"
        )

    monkeypatch.setattr(composition, "CodexDeviceAuthManager", forbidden)
    monkeypatch.setattr(composition, "OpenRouterReviewer", forbidden)
    monkeypatch.setattr(composition, "run_description_reviewer", forbidden)
    assert configuration.review is not None
    access = composition._compose_reviewer_access(repository, configuration.review)
    import threading

    primary_gate = threading.Lock()

    def serialized(_call):
        with primary_gate:
            return forbidden()

    access.attach_serialization(serialized)
    assert store.read(binding.review_case_id)["current"] is True

    def independent():
        reopened = open_repository(
            tmp_path / "reports.sqlite", authority=repository.authority
        )
        try:
            raw = reopened.description_advice_store().read(binding.review_case_id)
            assert raw["current"] is False
            assert raw["candidate"]["candidate_id"] == candidate.candidate_id
            composition.compose_field_effect_activity_projection(
                configuration, reopened
            )
            assert (
                reopened.description_advice_store().read(binding.review_case_id)[
                    "current"
                ]
                is True
            )
            profile.unlink()
            assert (
                reopened.description_advice_store().read(binding.review_case_id)[
                    "current"
                ]
                is False
            )
        finally:
            reopened.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with primary_gate:
            pool.submit(independent).result(timeout=5)
    assert store.read(binding.review_case_id)["current"] is False
    access.attach_serialization(lambda call: call())
    access.close()
    repository.close()


@pytest.mark.parametrize("failed", [False, True])
def test_parked_and_failed_preparation_are_authored_activity_without_authority(
    tmp_path: Path,
    failed: bool,
) -> None:
    from mechanical_librarian.domain.activity_records import readable_activity_event

    repository, store, binding, candidate, _ = _prepared(
        tmp_path,
        automatic=False,
        output_transform=(lambda _response: "invalid synthetic result")
        if failed
        else None,
    )
    page = repository.field_effect_activity_page(
        search=None, event_type="all", occurred_after=0, page=1, page_size=25
    )
    events = [
        event
        for event in page["events"]
        if event["record"]["source_kind"] == "description_preparation"
    ]
    assert len(events) == 1
    event = events[0]
    assert event["state"] == ("failed" if failed else "succeeded")
    assert event["record"]["advised"] is True
    readable_activity_event(event, None)
    assert any(
        fact["value"] == "Advised · Authored content" for fact in event["evidence"]
    )
    assert any(
        "grants no write authority" in fact["value"] for fact in event["evidence"]
    )
    assert (
        "could not be prepared" in event["change"]
        if failed
        else "prepared for review" in event["change"]
    )
    assert repository._connection.execute(
        "SELECT count(*) FROM description_decisions"
    ).fetchone() == (0,)
    repository.close()


@pytest.mark.parametrize("during_dispatch", [False, True])
def test_verified_deterministic_fill_retires_advice_before_successor_scan(
    tmp_path: Path,
    during_dispatch: bool,
) -> None:
    from mechanical_librarian.domain.metadata_report import (
        TargetField,
        TargetFieldState,
    )
    from tests.support.automatic_no_change import _automatic_no_change_inputs
    from tests.support.field_effects import CAPABILITY
    from tests.support.proposal_evidence import _report

    base, *_ = _automatic_no_change_inputs("description_preserved")
    report = _report(
        fields=tuple(
            TargetField(field.destination, TargetFieldState.EMPTY, None)
            if field.destination.value == "description"
            else field
            for field in base.target.fields
        ),
        local_claims=base.local_claims,
        providers=base.providers,
    )
    writes = []
    captured = []

    def capture(_repository, _report, _decision, access, calls):
        captured.append((access, calls))

    def write(repository, _access, binding, deterministic):
        authority = PersistedFieldEffectAuthority(repository)
        snapshot = authority.resolve(deterministic)
        application, target = _guarded_application(repository, tmp_path, authority)
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
        assert target.read(operation.plan).value == "A qualified description."
        writes.append(operation.plan)

    repository, store, binding, candidate, deterministic = _prepared(
        tmp_path,
        automatic=False,
        report_override=report,
        during_dispatch=write if during_dispatch else None,
        result_current=not during_dispatch,
        pipeline_probe=capture,
    )
    if not during_dispatch:
        write(repository, None, binding, deterministic)
    assert len(writes) == 1
    advice = store.read(binding.review_case_id)
    assert advice["current"] is False
    assert advice["candidate"]["candidate_id"] == candidate.candidate_id
    assert advice["application_outcome"] is None
    with pytest.raises(ValueError, match="description advice is stale"):
        store.admit(candidate.candidate_id, binding, "human", "e" * 64, 30)
    assert repository._connection.execute(
        "SELECT count(*) FROM description_advice_attempts"
    ).fetchone() == (1,)
    assert repository._connection.execute(
        "SELECT count(*) FROM description_decisions"
    ).fetchone() == (0,)
    reopened = open_repository(
        tmp_path / "reports.sqlite", create=False, authority=repository.authority
    )
    assert (
        reopened.description_advice_store().read(binding.review_case_id)["current"]
        is False
    )
    assert (
        reopened.description_advice_store().read(binding.review_case_id)["candidate"][
            "candidate_id"
        ]
        == candidate.candidate_id
    )
    reopened.close()

    from mechanical_librarian.domain.metadata_policy import human_review_destinations
    from mechanical_librarian.domain.proposal_set import evaluate_report

    successor = _report(
        fields=tuple(
            TargetField(
                field.destination, TargetFieldState.VALUE, "A qualified description."
            )
            if field.destination.value == "description"
            else field
            for field in report.target.fields
        ),
        local_claims=report.local_claims,
        providers=report.providers,
    )
    resolved = repository.current_resolved_metadata_policy(successor.target.library)
    assert resolved is not None
    proposal = evaluate_report(
        successor, resolved.resolved_id, human_review_destinations(resolved)
    )
    repository.publish(successor, lambda: True, expected_current=report.report_id)
    repository.publish_proposal(proposal)
    case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
    assert case_id != binding.review_case_id
    fresh = binding.model_copy(
        update={
            "review_case_id": case_id,
            "report_id": successor.report_id,
            "proposal_set_id": proposal.proposal_set_id,
            "target_fingerprint": successor.target.fingerprint,
        }
    )
    access, calls = captured[0]
    reused = DescriptionAdviceService(
        store, access, store.current_settings, lambda: 40
    ).prepare(successor, fresh, lambda: store.binding_is_current(fresh))
    assert reused == candidate
    assert len(calls) == 1
    assert store.read(case_id)["current"] is True
    assert store.read(case_id)["binding"] == fresh.model_dump()
    selected = store.admit(candidate.candidate_id, fresh, "human", "f" * 64, 41)
    assert selected.binding == fresh
    repository.close()


def test_running_request_successor_waits_for_durable_cached_continuation(tmp_path):
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
    joined = []

    def coalesce(repository, access, binding, _deterministic):
        resolved = repository.current_resolved_metadata_policy(successor.target.library)
        proposal = evaluate_report(
            successor, resolved.resolved_id, human_review_destinations(resolved)
        )
        repository.publish(successor, lambda: True, expected_current=original.report_id)
        repository.publish_proposal(proposal)
        case_id = json.loads(repository.admit_review_case(proposal))["review_case_id"]
        fresh = binding.model_copy(
            update={
                "review_case_id": case_id,
                "report_id": successor.report_id,
                "proposal_set_id": proposal.proposal_set_id,
                "target_fingerprint": successor.target.fingerprint,
            }
        )
        store = repository.description_advice_store()
        service = DescriptionAdviceService(
            store, access, store.current_settings, lambda: 11
        )
        assert (
            service.prepare(successor, fresh, lambda: store.binding_is_current(fresh))
            is None
        )
        joined.append((fresh, service))

    repository, store, _binding, candidate, _ = _prepared(
        tmp_path, automatic=False, during_dispatch=coalesce, result_current=False
    )
    fresh, service = joined[0]
    before = store.read(fresh.review_case_id)
    assert before["candidate"]["candidate_id"] == candidate.candidate_id
    assert before["current"] is False
    assert repository._connection.execute(
        "SELECT retry FROM description_preparation_pending WHERE review_case_id=?",
        (fresh.review_case_id,),
    ).fetchone() == (0,)
    with pytest.raises(ValueError, match="description advice is stale"):
        store.admit(candidate.candidate_id, fresh, "human", "e" * 64, 12)
    assert (
        service.prepare(successor, fresh, lambda: store.binding_is_current(fresh))
        == candidate
    )
    assert store.read(fresh.review_case_id)["current"] is True
    assert (
        repository._connection.execute(
            "SELECT 1 FROM description_preparation_pending WHERE review_case_id=?",
            (fresh.review_case_id,),
        ).fetchone()
        is None
    )
    assert (
        store.admit(candidate.candidate_id, fresh, "human", "f" * 64, 13).binding
        == fresh
    )
    assert repository._connection.execute(
        "SELECT count(*) FROM description_advice_attempts"
    ).fetchone() == (1,)
    repository.close()


@pytest.mark.parametrize("before_cutover", [False, True])
@pytest.mark.parametrize("automatic", [False, True])
def test_coalesced_successor_continues_through_real_projection(
    tmp_path, before_cutover, automatic
):
    from concurrent.futures import ThreadPoolExecutor
    from dataclasses import replace
    from threading import Event

    from mechanical_librarian.bootstrap import composition
    from mechanical_librarian.domain.metadata_policy import human_review_destinations
    from mechanical_librarian.domain.metadata_report import TargetField
    from mechanical_librarian.domain.proposal_set import evaluate_report
    from tests.support.automatic_no_change import _automatic_no_change_inputs
    from tests.support.description_advice import _contained_profile
    from tests.support.proposal_evidence import _report
    from tests.unit.test_shadow_outcome_persistence import (
        _pipeline_configuration,
        _run_qualified_cover_pipeline,
    )

    profile = _contained_profile(tmp_path)
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
    entered, release, finished, cleanup = Event(), Event(), Event(), Event()
    captured = []
    results = []

    def block(repository, access, binding, _decision):
        captured.append((repository.authority, access, binding))
        entered.set()
        assert release.wait(10)

    def prepare():
        result = None
        try:
            result = _prepared(
                tmp_path,
                automatic=automatic,
                contained_identity=lambda: ("gpt-5.6-sol", profile.read_bytes()),
                during_dispatch=block,
                result_current=False,
            )
            results.append(result[3])
            finished.set()
            assert cleanup.wait(10)
            return result[3]
        finally:
            finished.set()
            if result is not None:
                result[0].close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(prepare)
        assert entered.wait(10)
        authority, _worker_access, original_binding = captured[0]
        access_repository = open_repository(
            tmp_path / "reports.sqlite", authority=authority
        )
        access = ReviewerAccessOperations(
            access_repository.reviewer_access_store(),
            codex_auth=_Auth("usable"),
            codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(
                True, None, model="gpt-5.6-sol"
            ),
            codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
            codex_identity=lambda: ("gpt-5.6-sol", profile.read_bytes()),
            codex_binding=lambda: pytest.fail("unexpected cover dispatch"),
            codex_description_binding=lambda: pytest.fail(
                "continuation dispatched again"
            ),
            openrouter_factory=lambda *_: pytest.fail("unexpected external transport"),
        )
        configuration = _pipeline_configuration()
        assert configuration.review is not None
        configuration = replace(
            configuration,
            review=replace(
                configuration.review,
                description_automatic_application=automatic,
                cover_reviewer_profile_file=str(profile),
            ),
        )
        recovery = composition.SQLiteProviderRecoveryStore.open(
            tmp_path / "recovery.sqlite"
        )
        supervisor = composition.create_review_pipeline(
            configuration,
            tmp_path / "reports.sqlite",
            tmp_path / "catalogue.sqlite",
            tmp_path / "artifacts",
            access,
            database_authority=authority,
            provider_recovery=recovery,
        )
        repository = supervisor._pipeline._repository_factory()
        resolved = repository.current_resolved_metadata_policy(successor.target.library)
        proposal = evaluate_report(
            successor, resolved.resolved_id, human_review_destinations(resolved)
        )
        pipeline = _run_qualified_cover_pipeline(
            repository,
            tmp_path,
            successor,
            proposal,
            supervisor,
            repository_factory=supervisor._pipeline._repository_factory,
        )

        def extract_successor(bound, action, claim_next):
            bound.publish(
                successor,
                lambda: True,
                expected_current=original.report_id,
                after_publication=lambda current, published: (
                    current.complete_review_extraction_publication(
                        action, published.report_id, 10, 0, None, claim_next, None
                    )
                ),
            )
            return successor.report_id, True

        pipeline._extract = extract_successor
        supervisor._pipeline = pipeline
        original_prepare = pipeline._before_projection
        joined: list[str] = []

        def join(bound, action):
            original_prepare(bound, action)
            assert action.review_case_id is not None
            if not joined:
                joined.append(action.review_case_id)
                for name in ("_extract", "_providers", "_propose", "_admit"):
                    setattr(
                        pipeline,
                        name,
                        lambda *_: pytest.fail(
                            "cached continuation repeated upstream work"
                        ),
                    )
                assert bound._connection.execute(
                    "SELECT candidate_id FROM current_description_advice WHERE review_case_id=?",
                    (action.review_case_id,),
                ).fetchone() == (None,)
                if before_cutover:
                    release.set()
                    assert finished.wait(10)
                    assert bound._connection.execute(
                        "SELECT rescan_requested FROM review_preparation_scans WHERE scan_id=?",
                        (action.scan_id,),
                    ).fetchone() == (1,)

        pipeline._before_projection = join
        try:
            for _ in range(64):
                if not pipeline.run_next(1):
                    break
            else:
                pytest.fail("successor projection did not settle")
            assert len(joined) == 1
            case_id = joined[0]
            assert case_id != original_binding.review_case_id
            if before_cutover:
                assert repository._connection.execute(
                    "SELECT state FROM review_preparation_scans"
                ).fetchone() == ("queued",)
                for _ in range(64):
                    if not pipeline.run_next(1):
                        break
                else:
                    pytest.fail("coalesced rescan did not settle")
            else:
                supervisor.set_paused(True)
                release.set()
                assert finished.wait(10)
                assert repository._connection.execute(
                    "SELECT retry FROM description_preparation_pending WHERE review_case_id=?",
                    (case_id,),
                ).fetchone() == (0,)
                assert pipeline.run_next(1) is False
                repository.close()
                repository = supervisor._pipeline._repository_factory()
                assert (
                    repository.description_advice_store().read(case_id)["current"]
                    is False
                )
                for name in ("_extract", "_providers", "_propose", "_admit"):
                    setattr(
                        pipeline,
                        name,
                        lambda *_: pytest.fail(
                            "cached continuation repeated upstream work"
                        ),
                    )
                pipeline._clock = lambda: 30
                supervisor.set_paused(False)
                for _ in range(64):
                    if not pipeline.run_next(1):
                        break
                else:
                    pytest.fail("reopened paused continuation did not settle")
            assert finished.is_set()
            assert len(results) == 1
            candidate = results[0]
            advice = repository.description_advice_store().read(case_id)
            assert advice["current"] is True
            assert advice["candidate"]["candidate_id"] == candidate.candidate_id
            assert (advice["selection"] is not None) is automatic
            if not automatic:
                from mechanical_librarian.domain.description_advice import (
                    DescriptionAdviceBinding,
                )

                fresh = DescriptionAdviceBinding.model_validate(advice["binding"])
                assert (
                    repository.description_advice_store()
                    .admit(candidate.candidate_id, fresh, "human", "d" * 64, 31)
                    .binding
                    == fresh
                )
            assert repository._connection.execute(
                "SELECT count(*) FROM description_advice_attempts"
            ).fetchone() == (1,)
            assert (
                repository._connection.execute(
                    "SELECT 1 FROM description_preparation_pending WHERE review_case_id=?",
                    (case_id,),
                ).fetchone()
                is None
            )
        finally:
            release.set()
            supervisor.stop()
            recovery.close()
            repository.close()
            access_repository.close()
            cleanup.set()
            future.result(timeout=10)


def test_composed_contained_description_uses_image_free_port_and_exact_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import hashlib
    from dataclasses import replace

    from mechanical_librarian.application.contained_reviewer_contract import (
        ContainedReviewRequest,
        ContainedReviewResult,
    )
    from mechanical_librarian.bootstrap import composition, contained_reviewer
    from mechanical_librarian.domain.metadata_report import MetadataReport
    from tests.support.description_advice import _contained_profile
    from tests.unit.test_shadow_outcome_persistence import _pipeline_configuration

    profile = _contained_profile(tmp_path)
    repository, store, binding, previous, _ = _prepared(
        tmp_path,
        automatic=False,
        contained_identity=lambda: ("gpt-5.6-sol", profile.read_bytes()),
    )
    review = _pipeline_configuration().review
    assert review is not None
    review = replace(review, cover_reviewer_profile_file=str(profile))
    monkeypatch.setattr(
        composition, "CodexDeviceAuthManager", lambda *_args, **_kwargs: _Auth("usable")
    )
    monkeypatch.setattr(
        composition,
        "reviewer_host_status",
        lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
    )

    def unexpected(*_args, **_kwargs):
        pytest.fail("description task must not use OpenRouter or cover dispatch")

    monkeypatch.setattr(composition, "OpenRouterReviewer", unexpected)
    monkeypatch.setattr(composition, "run_cover_packet_reviewer", unexpected)
    dispatched = []

    def engine(_self, request, on_dispatch):
        assert type(request) is ContainedReviewRequest
        assert not hasattr(request, "images")
        dispatched.append(request)
        on_dispatch(1, 1, "synthetic-process")
        document = canonical_json(
            {"description": "Composed authored description.", "disposition": "ready"}
        )
        return ContainedReviewResult(
            document, hashlib.sha256(document.encode()).hexdigest()
        )

    monkeypatch.setattr(contained_reviewer.ContainedReviewerAdapter, "run", engine)
    access = composition._compose_reviewer_access(repository, review)
    report = MetadataReport.from_json(
        repository.get_review_case_report(binding.review_case_id)
    )
    candidate = DescriptionAdviceService(
        store, access, store.current_settings, lambda: 20
    ).prepare(report, binding, lambda: store.binding_is_current(binding), retry=True)
    assert candidate is not None and candidate.candidate_id != previous.candidate_id
    assert candidate.engine == "codex" and candidate.model == "gpt-5.6-sol"
    assert candidate.profile_digest == hashlib.sha256(profile.read_bytes()).hexdigest()
    assert candidate.output.description == "Composed authored description."
    assert len(dispatched) == 1
    assert candidate.request_digest == dispatched[0].digest
    access.close()
    repository.close()


@pytest.mark.parametrize("wrong_model", [False, True])
def test_composed_openrouter_description_uses_exact_text_model_without_fallback(
    tmp_path: Path,
    monkeypatch,
    wrong_model: bool,
) -> None:
    import httpx

    from mechanical_librarian.adapters.reviewers.openrouter import OpenRouterReviewer
    from mechanical_librarian.bootstrap import composition
    from mechanical_librarian.domain.metadata_report import MetadataReport
    from tests.unit.test_shadow_outcome_persistence import _pipeline_configuration

    repository, store, binding, _, _ = _prepared(tmp_path, automatic=False)
    posted = []

    def transport(request):
        assert request.url.host == "openrouter.ai"
        if request.url.path == "/api/v1/key":
            return httpx.Response(200, json={"data": {"is_free_tier": True}})
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "example/text",
                            "architecture": {
                                "input_modalities": ["text"],
                                "output_modalities": ["text"],
                            },
                            "supported_parameters": ["structured_outputs"],
                            "pricing": {
                                "prompt": "0.000001",
                                "completion": "0.000002",
                                "request": "0",
                            },
                        }
                    ]
                },
            )
        assert request.url.path == "/api/v1/chat/completions"
        posted.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "other/model" if wrong_model else "example/text",
                "choices": [
                    {
                        "message": {
                            "content": canonical_json(
                                {
                                    "description": "Composed OpenRouter description.",
                                    "disposition": "ready",
                                }
                            )
                        }
                    }
                ],
            },
        )

    monkeypatch.setattr(
        composition,
        "OpenRouterReviewer",
        lambda key, model: OpenRouterReviewer(
            key, model, transport=httpx.MockTransport(transport)
        ),
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("OpenRouter description must not use contained or cover dispatch")

    monkeypatch.setattr(composition, "run_description_reviewer", forbidden)
    monkeypatch.setattr(composition, "run_cover_packet_reviewer", forbidden)
    review = _pipeline_configuration().review
    assert review is not None
    access = composition._compose_reviewer_access(repository, review)
    selected = access.configure_openrouter(
        expected_revision=2,
        api_key="synthetic-secret",
        model="example/text",
        task="description_rewrite",
    )
    fresh = binding.model_copy(update={"access_revision": selected.revision})
    report = MetadataReport.from_json(
        repository.get_review_case_report(binding.review_case_id)
    )
    candidate = DescriptionAdviceService(
        store, access, store.current_settings, lambda: 20
    ).prepare(report, fresh, lambda: store.binding_is_current(fresh))
    assert len(posted) == 1
    assert posted[0]["model"] == "example/text"
    assert posted[0]["provider"] == {
        "require_parameters": True,
        "allow_fallbacks": False,
    }
    assert "image_url" not in json.dumps(posted)
    assert "synthetic-secret" not in json.dumps(posted)
    if wrong_model:
        assert candidate is None
        assert store.read(binding.review_case_id)["state"] == "failed"
    else:
        assert candidate is not None
        assert candidate.engine == "openrouter" and candidate.model == "example/text"
        assert candidate.access_revision == selected.revision
        assert (
            store.admit(
                candidate.candidate_id, fresh, "human", "e" * 64, 21
            ).candidate_id
            == candidate.candidate_id
        )
    access.close()
    repository.close()


@pytest.mark.parametrize("outcome", ["planned", "target_failure", "readback_failure"])
def test_unverified_effect_does_not_retire_description_binding(
    tmp_path: Path,
    outcome: str,
) -> None:
    from tests.support.field_effects import CAPABILITY

    repository, store, binding, candidate, _ = _prepared(tmp_path)
    decision = store.admit(candidate.candidate_id, binding, "human", "a" * 64, 11)
    authority = PersistedFieldEffectAuthority(repository)
    snapshot = authority.resolve(decision)
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
    target.seed(operation.plan)
    if outcome != "planned":
        if outcome == "target_failure":
            target.fail_next = True
        else:
            target.fail_read_after_apply_for.add(operation.plan.plan_id)
        result = application.apply(operation.plan.plan_id)
        assert result.state in {"retryable", "reconciling"}
        assert result.failure is not None
    assert store.binding_is_current(binding)
    assert store.read(binding.review_case_id)["application_outcome"] is None
    assert len(target._effects) == (1 if outcome == "readback_failure" else 0)
    repository.close()


def test_guarded_runtime_composition_binds_its_reopened_description_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from dataclasses import replace
    from unittest.mock import Mock

    from mechanical_librarian.adapters.field_effect_fakes import (
        FakeCapabilityObserver,
        FakeTargetService,
    )
    from mechanical_librarian.bootstrap import composition
    from tests.support.description_advice import _contained_profile
    from tests.support.field_effects import CAPABILITY
    from tests.unit.test_shadow_outcome_persistence import _pipeline_configuration

    profile = _contained_profile(tmp_path)
    repository, _, binding, _, _ = _prepared(
        tmp_path, contained_identity=lambda: ("gpt-5.6-sol", profile.read_bytes())
    )
    reopened = open_repository(
        tmp_path / "reports.sqlite", authority=repository.authority
    )
    assert (
        reopened.description_advice_store().read(binding.review_case_id)["current"]
        is False
    )
    configuration = _pipeline_configuration()
    assert configuration.review is not None
    configuration = replace(
        configuration,
        review=replace(
            configuration.review,
            cover_reviewer_profile_file=str(profile),
            description_automatic_application=True,
        ),
    )
    target = FakeTargetService()
    monkeypatch.setattr(
        composition,
        "compose_audiobookshelf_field_effect_target",
        lambda *_args, **_kwargs: target,
    )
    monkeypatch.setattr(
        composition,
        "compose_audiobookshelf_field_effect_capabilities",
        lambda *_args, **_kwargs: FakeCapabilityObserver(CAPABILITY),
    )
    recovery = composition.SQLiteProviderRecoveryStore.open(
        tmp_path / "guarded-provider-recovery.sqlite"
    )
    try:
        composition.compose_continuous_guarded_application(
            configuration,
            reopened,
            tmp_path / "reports.sqlite",
            tmp_path / "guarded-artifacts",
            Mock(),
            provider_recovery=recovery,
        )
        assert (
            reopened.description_advice_store().read(binding.review_case_id)["current"]
            is True
        )
        profile.unlink()
        assert (
            reopened.description_advice_store().read(binding.review_case_id)["current"]
            is False
        )
        assert target._effects == {}
    finally:
        recovery.close()
        reopened.close()
        repository.close()


@pytest.mark.parametrize("scope", ["other_field", "other_target"])
def test_other_verified_fake_service_write_preserves_description_binding(
    tmp_path: Path,
    scope: str,
) -> None:
    from dataclasses import replace

    from mechanical_librarian.adapters.field_effect_fakes import (
        FakeAuthorityCurrentness,
    )
    from mechanical_librarian.domain.field_effect import field_value_version
    from tests.support.field_effects import CAPABILITY, DEPENDENCY
    from tests.support.field_effects import decision as fake_decision

    repository, store, binding, _, _ = _prepared(tmp_path)
    destination = "title" if scope == "other_field" else "description"
    work_id = "item-1" if scope == "other_field" else "other-synthetic-work"
    decision = replace(
        fake_decision(destination, {"kind": "text", "value": "Control value"}),
        review_case_id=binding.review_case_id,
        target_fingerprint=binding.target_fingerprint,
    )
    authority = FakeAuthorityCurrentness()
    version = field_value_version(destination, "Before")
    authority.bind(
        decision,
        connection="primary",
        library="ebooks",
        work_id=work_id,
        target_version=version,
    )
    application, target = _guarded_application(repository, tmp_path, authority)
    repository.grant_field_effect_consents("primary", ("ebooks",), (destination,), 12)
    operation = application.plan(
        decision,
        connection="primary",
        library="ebooks",
        work_id=work_id,
        destination=destination,
        pre_value="Before",
        desired_value=decision.selected_value,
        target_fingerprint=binding.target_fingerprint,
        target_version=version,
        dependency_digests=(DEPENDENCY,),
        capability_digest=CAPABILITY,
    )
    assert operation is not None
    target.seed(operation.plan)
    assert application.apply(operation.plan.plan_id).state == "verified"
    assert target.read(operation.plan).value == "Control value"
    assert store.binding_is_current(binding)
    assert store.read(binding.review_case_id)["current"] is True
    repository.close()


@pytest.mark.parametrize("claimed", [False, True])
def test_authored_admission_replaces_pending_but_not_claimed_deterministic_effect(
    tmp_path: Path, claimed: bool
) -> None:
    from mechanical_librarian.domain.metadata_report import (
        TargetField,
        TargetFieldState,
    )
    from tests.support.automatic_no_change import _automatic_no_change_inputs
    from tests.support.field_effects import CAPABILITY
    from tests.support.proposal_evidence import _report

    base, *_ = _automatic_no_change_inputs("description_preserved")
    report = _report(
        fields=tuple(
            TargetField(field.destination, TargetFieldState.EMPTY, None)
            if field.destination.value == "description"
            else field
            for field in base.target.fields
        ),
        local_claims=base.local_claims,
        providers=base.providers,
    )
    repository, store, binding, candidate, deterministic = _prepared(
        tmp_path, report_override=report
    )
    authority = PersistedFieldEffectAuthority(repository)
    snapshot = authority.resolve(deterministic)
    application, target = _guarded_application(repository, tmp_path, authority)
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
    if claimed:
        repository.claim_field_effect(
            operation.plan.plan_id, "planned", "b" * 64, application._clock()
        )
        with pytest.raises(ValueError, match="description effect is in flight"):
            store.admit(
                candidate.candidate_id, binding, "automatic_policy", "e" * 64, 20
            )
        assert store.read(binding.review_case_id)["selection"] is None
        assert authority.current(operation.plan.authority)
    else:
        selected = store.admit(
            candidate.candidate_id, binding, "automatic_policy", "e" * 64, 20
        )
        assert selected.to_json() in repository.pending_field_effect_decision_documents(
            100
        )
        assert not authority.current(operation.plan.authority)
    assert target._effects == {}
    assert store.read(binding.review_case_id)["current"] is True
    repository.close()


def test_complete_preparation_never_enters_public_access_gate_inside_sqlite(
    tmp_path: Path,
) -> None:
    observed = []

    def observe(repository, _report, _decision, access, _calls):
        def serialized(call):
            observed.append(repository._connection.in_transaction)
            assert not repository._connection.in_transaction
            return call()

        access.attach_serialization(serialized)

    repository, store, binding, candidate, _ = _prepared(
        tmp_path, pipeline_probe=observe
    )
    assert observed and not any(observed)
    assert store.read(binding.review_case_id)["current"] is True
    assert (
        store.read(binding.review_case_id)["candidate"]["candidate_id"]
        == candidate.candidate_id
    )
    assert repository._connection.execute(
        "SELECT count(*) FROM description_advice_attempts"
    ).fetchone() == (1,)
    repository.close()


def test_worker_attempt_and_owner_gate_waiting_for_sqlite_complete_without_inversion(
    tmp_path: Path,
) -> None:
    import hashlib
    import threading
    from concurrent.futures import ThreadPoolExecutor

    captured = []

    def capture(_repository, report, _decision, access, calls):
        captured.append((report, access, calls))

    repository, store, binding, candidate, _ = _prepared(
        tmp_path, pipeline_probe=capture
    )
    report, original_access, calls = captured[0]
    gate = threading.RLock()
    worker_begin = threading.Event()
    owner_waiting = threading.Event()
    owner_ready = threading.Event()
    worker_gate_states = []
    settings = store.current_settings()
    profile = b"synthetic-contained-profile"

    def owner():
        own = open_repository(
            tmp_path / "reports.sqlite", authority=repository.authority
        )
        try:
            owner_ready.set()
            assert worker_begin.wait(3)
            with gate:
                own._connection.set_trace_callback(
                    lambda statement: (
                        owner_waiting.set() if statement == "BEGIN IMMEDIATE" else None
                    )
                )
                own._connection.execute("BEGIN IMMEDIATE")
                own._connection.execute(
                    "UPDATE metadata_policy_state SET generation=generation"
                )
                own._connection.commit()
        finally:
            own.close()

    def worker():
        own = open_repository(
            tmp_path / "reports.sqlite", authority=repository.authority
        )
        local = own.description_advice_store()
        local.bind_settings(settings)

        def passive(_engine, _model):
            if own._connection.in_transaction and not worker_begin.is_set():
                worker_begin.set()
                assert owner_waiting.wait(3)
            return candidate.model, hashlib.sha256(profile).hexdigest()

        local.bind_identity(passive)
        access = ReviewerAccessOperations(
            own.reviewer_access_store(),
            codex_auth=_Auth("usable"),
            codex_provisioning=lambda _refresh: ReviewerProvisioningStatus(
                True, None, model="gpt-5.6-sol"
            ),
            codex_host=lambda: ReviewerHostStatus(True, None, 2 * 1024**3),
            codex_identity=lambda: (candidate.model, profile),
            codex_binding=original_access._codex_binding,
            codex_description_binding=original_access._codex_description_binding,
            openrouter_factory=lambda *_: pytest.fail("unexpected engine"),
        )

        def serialized(call):
            worker_gate_states.append(own._connection.in_transaction)
            assert not own._connection.in_transaction
            assert gate.acquire(timeout=3)
            try:
                return call()
            finally:
                gate.release()

        access.attach_serialization(serialized)
        try:
            assert owner_ready.wait(3)
            prepared = DescriptionAdviceService(
                local, access, local.current_settings, lambda: 30
            ).prepare(
                report,
                binding,
                lambda: local.binding_is_current(binding),
                retry=True,
            )
            assert prepared is not None
            assert local.read(binding.review_case_id)["current"] is True
        finally:
            own.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        owner_future = pool.submit(owner)
        worker_future = pool.submit(worker)
        worker_future.result(timeout=6)
        owner_future.result(timeout=6)
    assert worker_begin.is_set() and owner_waiting.is_set()
    assert worker_gate_states and not any(worker_gate_states)
    assert len(calls) == 2
    repository.close()


@pytest.mark.parametrize(
    "boundary", ["begin_to_start", "start_to_dispatch", "dispatch_to_finish"]
)
@pytest.mark.parametrize(
    "drift", ["access", "task", "model", "profile", "preferences", "staged_case"]
)
def test_attempt_transition_drift_never_publishes_or_admits_stale_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, drift: str
) -> None:
    identity = ("gpt-5.6-sol", b"synthetic-contained-profile")
    captured = []

    def capture(_repository, report, _decision, access, calls):
        captured.append((report, access, calls))

    repository, store, binding, candidate, _ = _prepared(
        tmp_path, contained_identity=lambda: identity, pipeline_probe=capture
    )
    report, access, calls = captured[0]
    staged_current = True

    def change():
        nonlocal identity, staged_current
        if drift == "access":
            access.select_codex(expected_revision=2, task="description_rewrite")
        elif drift == "task":
            access.select_none(expected_revision=2, task="description_rewrite")
        elif drift == "model":
            identity = ("changed-synthetic-model", identity[1])
        elif drift == "profile":
            identity = (identity[0], b"changed-synthetic-profile")
        elif drift == "preferences":
            store.bind_settings(
                DescriptionAdviceSettings(
                    automatic_application=True, remove_marketing=False
                )
            )
        else:
            staged_current = False

    if boundary == "begin_to_start":
        original = store.begin_attempt

        def begin(*args, **kwargs):
            result = original(*args, **kwargs)
            assert result is not None
            change()
            return result

        monkeypatch.setattr(store, "begin_attempt", begin)
    elif boundary == "start_to_dispatch":
        original = store.start

        def start(*args, **kwargs):
            result = original(*args, **kwargs)
            assert result
            change()
            return result

        monkeypatch.setattr(store, "start", start)
    else:
        original = store.finish

        def finish(*args, **kwargs):
            change()
            return original(*args, **kwargs)

        monkeypatch.setattr(store, "finish", finish)
    result = DescriptionAdviceService(
        store, access, store.current_settings, lambda: 30
    ).prepare(
        report,
        binding,
        lambda: staged_current and store.binding_is_current(binding),
        retry=True,
    )
    assert result is None
    dispatched = boundary == "dispatch_to_finish" or (
        boundary == "start_to_dispatch" and drift in {"preferences", "staged_case"}
    )
    assert len(calls) == 1 + int(dispatched)
    row = repository._connection.execute(
        "SELECT p.candidate_id,a.state FROM current_description_advice p JOIN description_advice_attempts a ON a.attempt_id=p.attempt_id"
    ).fetchone()
    assert row[0] is None
    assert row[1] == (
        "interrupted"
        if boundary == "begin_to_start"
        else "succeeded"
        if dispatched
        else "failed"
    )
    assert repository._connection.execute(
        "SELECT count(*) FROM description_advice_candidates"
    ).fetchone() == (1 + int(dispatched),)
    assert repository._connection.execute(
        "SELECT count(*) FROM description_decisions"
    ).fetchone() == (0,)
    with pytest.raises(ValueError):
        store.admit(candidate.candidate_id, binding, "human", "f" * 64, 31)
    repository.close()


@pytest.mark.parametrize("automatic_outcome", ["keep_current", "candidate"])
def test_automatic_history_values_survive_authored_selection_reversal_and_reopen(
    tmp_path, automatic_outcome
):
    from mechanical_librarian.application.review_web import (
        InspectCurrentReviewCase,
        RecordHumanDecision,
    )
    from mechanical_librarian.domain.metadata_report import (
        TargetField,
        TargetFieldState,
    )
    from tests.support.automatic_no_change import _automatic_no_change_inputs
    from tests.support.proposal_evidence import _report

    original, *_ = _automatic_no_change_inputs("description_preserved")
    report = (
        original
        if automatic_outcome == "keep_current"
        else _report(
            fields=tuple(
                TargetField(field.destination, TargetFieldState.EMPTY, None)
                if field.destination.value == "description"
                else field
                for field in original.target.fields
            ),
            local_claims=original.local_claims,
            providers=original.providers,
        )
    )
    repository, store, binding, candidate, automatic = _prepared(
        tmp_path, automatic=False, report_override=report
    )
    assert automatic.outcome == automatic_outcome

    def event(bound):
        return next(
            item
            for item in InspectCurrentReviewCase(bound).history(binding.review_case_id)[
                "items"
            ]
            if item.get("decision_id") == automatic.decision_id
        )

    before = event(repository)
    assert before["previous_value"] == (
        {"kind": "text", "value": "Existing description."}
        if automatic_outcome == "keep_current"
        else None
    )
    assert before["selected_value"] == {
        "kind": "text",
        "value": "Existing description."
        if automatic_outcome == "keep_current"
        else "A qualified description.",
    }
    store.admit(candidate.candidate_id, binding, "human", "c" * 64, 20)
    assert event(repository) == before
    RecordHumanDecision(repository, clock=lambda: 21).run(
        binding.review_case_id,
        proposal_id=automatic.proposal_id,
        target_fingerprint=binding.target_fingerprint,
        destination="description",
        actor="owner",
        outcome="keep_current",
        selected_value=None,
        idempotency_key="retain-description-after-authored-selection",
    )
    assert event(repository) == before
    repository.close()
    reopened = open_repository(tmp_path / "reports.sqlite", create=False)
    assert event(reopened) == before
    reopened.close()


@pytest.mark.parametrize(
    "kind,code,expected",
    [
        *[
            (
                "contained",
                code,
                "invalid_output"
                if code in {"malformed_output", "response_format"}
                else "unavailable",
            )
            for code in (
                "auth",
                "cancel",
                "local_cli_abort",
                "malformed_output",
                "profile",
                "profile_contract_mismatch",
                "response_format",
                "runtime",
                "setup",
                "spawn",
                "timeout",
            )
        ],
        ("openrouter", "malformed_response", "invalid_output"),
        *[
            ("openrouter", code, "unavailable")
            for code in (
                "invalid_key",
                "quota_exhausted",
                "rate_limited",
                "model_unavailable",
                "model_text_only",
                "unreachable",
                "transport",
                "profile_mismatch",
                "malformed_request",
            )
        ],
        ("access", "host_unsupported", "unavailable"),
        ("unexpected", "private failure canary", "unavailable"),
    ],
)
def test_description_failure_phase_is_bounded_and_not_retried(
    tmp_path, kind, code, expected
):
    from mechanical_librarian.application.contained_reviewer_contract import (
        ContainedReviewerError,
    )
    from mechanical_librarian.application.reviewer_access import (
        ReviewerAccessApplicationError,
    )
    from mechanical_librarian.application.reviewer_contracts import (
        OpenRouterReviewerError,
    )

    def fail(_response):
        if kind == "contained":
            raise ContainedReviewerError(code)
        if kind == "openrouter":
            raise OpenRouterReviewerError(code)
        if kind == "access":
            raise ReviewerAccessApplicationError(code)
        raise RuntimeError(code)

    repository, store, binding, candidate, _ = _prepared(
        tmp_path, output_transform=fail
    )
    assert candidate is None
    advice = store.read(binding.review_case_id)
    assert (advice["state"], advice["failure"]) == ("failed", expected)
    assert "private failure canary" not in str(advice)
    assert repository._connection.execute(
        "SELECT count(*) FROM description_advice_attempts"
    ).fetchone() == (1,)
    history = repository.get_review_case_history(binding.review_case_id, 50)
    assert any(
        event["kind"] == "description_advice_recorded" and event["state"] == "failed"
        for event in history
    )
    repository.close()
    reopened = open_repository(tmp_path / "reports.sqlite", create=False)
    assert (
        reopened.description_advice_store().read(binding.review_case_id)["failure"]
        == expected
    )
    reopened.close()


def test_real_projection_consumes_disabled_description_intent_once(tmp_path):
    from dataclasses import replace

    from mechanical_librarian.bootstrap import composition
    from mechanical_librarian.domain.proposal_set import ProposalSet
    from tests.support.description_advice import _contained_profile
    from tests.unit.test_shadow_outcome_persistence import (
        _pipeline_configuration,
        _run_qualified_cover_pipeline,
    )

    profile = _contained_profile(tmp_path)
    captured = []
    repository, store, binding, _, deterministic = _prepared(
        tmp_path,
        contained_identity=lambda: ("gpt-5.6-sol", profile.read_bytes()),
        pipeline_probe=lambda _repo, report, _decision, access, calls: captured.append(
            (report, access, calls)
        ),
    )
    report, access, calls = captured[0]
    configuration = _pipeline_configuration()
    assert configuration.review is not None
    configuration = replace(
        configuration,
        review=replace(configuration.review, cover_reviewer_profile_file=str(profile)),
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
        repository,
        tmp_path,
        report,
        proposal,
        supervisor,
        repository_factory=supervisor._pipeline._repository_factory,
    )
    try:
        for _ in range(64):
            if not pipeline.run_next(1):
                break
        else:
            pytest.fail("initial pipeline did not settle")
        before_calls = len(calls)
        access.select_none(expected_revision=2, task="description_rewrite")
        assert repository._connection.execute(
            "SELECT count(*) FROM description_preparation_pending"
        ).fetchone() == (1,)
        for name in ("_extract", "_providers", "_propose", "_admit"):
            setattr(
                pipeline,
                name,
                lambda *_: pytest.fail("disabled advice repeated upstream work"),
            )
        requested_at = repository._connection.execute(
            "SELECT max(requested_at) FROM review_preparation_scans"
        ).fetchone()[0]
        pipeline._clock = lambda: requested_at
        for _ in range(64):
            if not pipeline.run_next(1):
                break
        else:
            pytest.fail("disabled advice projection did not settle")
        assert repository._connection.execute(
            "SELECT count(*) FROM description_preparation_pending"
        ).fetchone() == (0,)
        assert len(calls) == before_calls
        assert pipeline.run_next(1) is False
        assert repository._connection.execute(
            "SELECT stage,state,failure_code FROM review_preparation_work WHERE item_id='item-1'"
        ).fetchone() == ("complete", "complete", None)
    finally:
        supervisor.stop()
        recovery.close()
        repository.close()


@pytest.mark.parametrize("same_current", [False, True])
@pytest.mark.parametrize("origin", ["human", "automatic_policy"])
def test_selected_effect_outcome_survives_new_attempt_and_reopen(
    tmp_path, same_current, origin
):
    from mechanical_librarian.application.review_web import InspectCurrentReviewCase
    from mechanical_librarian.domain.description_advice import (
        DescriptionAdviceCandidate,
    )
    from tests.support.field_effects import CAPABILITY

    repository, store, binding, candidate, _ = _prepared(
        tmp_path, same_current=same_current
    )
    decision = store.admit(candidate.candidate_id, binding, origin, "a" * 64, 11)
    authority = PersistedFieldEffectAuthority(repository)
    snapshot = authority.resolve(decision)
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
    if same_current:
        assert operation is None
        assert repository.record_current_field_effect_no_change(decision, 14)
        outcome = "no_change_required"
    else:
        assert operation is not None
        target.seed(operation.plan)
        assert application.apply(operation.plan.plan_id).state == "verified"
        outcome = "verified"
    for stage in ("running", "succeeded"):
        if stage == "succeeded":
            latest = DescriptionAdviceCandidate.create(
                **{
                    **candidate.model_dump(exclude={"candidate_id"}),
                    "attempt_id": attempt[0],
                    "created_at": 14,
                }
            )
            assert (
                store.finish(
                    attempt[0], latest, 14, lambda: store.binding_is_current(binding)
                )
                is same_current
            )
        advice = store.read(binding.review_case_id)
        assert advice["state"] == stage
        assert advice["selection"]["decision_id"] == decision.decision_id
        assert (
            advice["selected_advice"]["candidate"]["candidate_id"]
            == candidate.candidate_id
        )
        assert advice["application_outcome"] == outcome
        assert advice["selected_advice"]["current"] is same_current
        field = next(
            field
            for field in InspectCurrentReviewCase(repository).run(
                binding.review_case_id
            )["fields"]
            if field["destination"] == "description"
        )
        assert field["needs_attention"] is False
        history = repository.get_review_case_history(binding.review_case_id, 50)
        assert any(
            event["kind"] == "description_application" and event["state"] == outcome
            for event in history
        )
        activity = repository.field_effect_activity_page(
            search=None, event_type="all", occurred_after=0, page=1, page_size=25
        )
        assert any(
            event["state"] == outcome and event["record"].get("advised") is True
            for event in activity["events"]
        )
    repository.close()
    reopened = open_repository(tmp_path / "reports.sqlite", create=False)
    advice = reopened.description_advice_store().read(binding.review_case_id)
    assert advice["application_outcome"] == outcome
    assert (
        advice["selected_advice"]["candidate"]["candidate_id"] == candidate.candidate_id
    )
    reopened.close()


def test_configured_activity_reprojects_authored_dependents_before_search(
    tmp_path, monkeypatch
):
    from mechanical_librarian.adapters.persistence.activity_facts import (
        ActivityFactStore,
    )
    from mechanical_librarian.application.continuous_field_effects import (
        ContinuousGuardedApplication,
    )
    from mechanical_librarian.domain.field_effect import FieldEffectError
    from tests.support.field_effects import CAPABILITY

    repository, advice, binding, candidate, _ = _prepared(tmp_path)
    decision = advice.admit(
        candidate.candidate_id, binding, "automatic_policy", "a" * 64, 11
    )
    authority = PersistedFieldEffectAuthority(repository)
    snapshot = authority.resolve(decision)
    service, target = _guarded_application(repository, tmp_path, authority)
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
        capability_digest=CAPABILITY,
    )
    assert operation is not None
    target.seed(operation.plan)
    assert service.apply(operation.plan.plan_id).state == "verified"
    facts = ActivityFactStore()
    reader = open_repository(
        tmp_path / "reports.sqlite", create=False, authority=repository.authority
    )

    def application(owner):
        return ContinuousGuardedApplication(
            owner,
            {},
            {},
            PersistedFieldEffectAuthority(owner),
            (("primary", "books"),),
            lambda: 2000,
            lambda *_: None,
            activity_only=True,
        )

    try:
        reader.configure_activity_page_reader(facts)
        assert application(reader).activity() == application(repository).activity()
        admitted = facts.snapshot()
        assert admitted is not None
        assert {key[0] for key in admitted.facts} >= {
            "description_preparation",
            "description_decision",
            "field_effect",
        }
        projected = []
        original = reader._field_effect_activity._project_activity_facts

        def project(candidates, maximum):
            projected.append(candidates)
            return original(candidates, maximum)

        monkeypatch.setattr(
            reader._field_effect_activity, "_project_activity_facts", project
        )
        connection = repository._connection
        columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(description_advice_candidates)"
            )
        ]
        values = list(
            connection.execute(
                "SELECT * FROM description_advice_candidates WHERE candidate_id=?",
                (candidate.candidate_id,),
            ).fetchone()
        )
        values[columns.index("document")] = "{invalid"
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "INSERT OR REPLACE INTO description_advice_candidates VALUES("
            + ",".join("?" for _ in values)
            + ")",
            values,
        )
        connection.commit()
        with pytest.raises(FieldEffectError, match="persistence"):
            reader.field_effect_activity_page(
                search="no readable match",
                event_type="all",
                occurred_after=0,
                page=1,
                page_size=25,
            )
        assert {key[0] for key in projected[0]} >= {
            "description_preparation",
            "description_decision",
            "field_effect",
        }
        with pytest.raises(FieldEffectError, match="persistence"):
            repository.field_effect_activity_page(
                search="no readable match",
                event_type="all",
                occurred_after=0,
                page=1,
                page_size=25,
            )
    finally:
        reader.close()
        repository.close()
