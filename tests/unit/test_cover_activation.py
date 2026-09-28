from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import (
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeConsentCurrentness,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakePreclaimHook,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.field_effect_runtime import (
    PersistedEffectConsent,
    PersistedEffectDependencies,
)
from mechanical_librarian.adapters.persistence.cover_artifacts import CoverArtifactStore
from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.domain.cover_automatic_decision import CoverAutomaticDecision
from mechanical_librarian.domain.cover_evidence import (
    CoverDesignation,
    CoverSourceState,
)
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    human_review_destinations,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.support.field_effects import CAPABILITY, Clock
from tests.unit.test_cover_packets import _preview, _report, _source
from tests.unit.test_shadow_outcome_persistence import (
    _pipeline_configuration,
    _qualified_cover_report,
    _run_qualified_cover_pipeline,
    _test_reviewer_access,
)


@pytest.mark.parametrize("option", ("fixed_source_order", "replace_if_strictly_better"))
@pytest.mark.parametrize("scenario", ("known_empty", "upgrade", "downgrade"))
def test_real_automatic_cover_admission_controls_guarded_fake_service_effect(
    scenario: str,
    option: str,
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        database = root / "reports.sqlite"
        repository = open_repository(database)
        if scenario == "known_empty":
            preview = _preview(3)
            candidate = _source(3, preview, embedded=True)
            timeout = replace(
                _source(103, preview),
                state=CoverSourceState.TIMEOUT,
                artifact=None,
                timeout_scope="attempted",
            )
            base = _report((candidate, timeout))
            report = MetadataReport.create(
                target=TargetFacts("primary", "ebooks", "item-1", base.target.fields),
                file=base.file,
                local_claims=base.local_claims,
                local_reasons=base.local_reasons,
                providers=base.providers,
                cover_sources=base.cover_sources,
            )
            artifacts = CoverArtifactStore(root / "artifacts")
            assert candidate.artifact is not None
            artifacts.stage(candidate.artifact, b"original-3", preview)
        else:
            report, _ = _qualified_cover_report(
                root,
                incumbent_size=(3000, 4500) if scenario == "downgrade" else (300, 450),
                candidate_size=(300, 450) if scenario == "downgrade" else (3000, 4500),
                candidate_designation=(
                    CoverDesignation.WORK
                    if scenario == "downgrade"
                    else CoverDesignation.EXACT_EDITION
                ),
                typed_timeout=True,
            )

        options = dict(DEFAULT_OPTIONS)
        options["cover_order"] = option
        destinations = {
            category: "unsupported" if category == "explicit" else "ask"
            for category in OPTION_CATALOGUE
        }
        destinations["cover_order"] = "automatic"
        profile = MetadataPolicyProfile.create(
            "Deterministic confirmed covers", options, destinations
        )
        resolved = resolve_policy(profile, report.target.library)
        proposal = evaluate_report(
            report, resolved.resolved_id, human_review_destinations(resolved)
        )
        repository.enter_shadow_preview(1)
        repository.create_metadata_policy_draft(profile.canonical_json)
        repository.activate_metadata_policy(
            profile.policy_id,
            MetadataPolicyProfile.builtin().policy_id,
            ((report.target.library, resolved.resolved_id, resolved.canonical_json),),
            2,
        )
        repository.activate_qualified_automatic(3)
        recovery = composition.SQLiteProviderRecoveryStore.open(
            root / "provider-recovery.sqlite"
        )
        supervisor = composition.create_review_pipeline(
            _pipeline_configuration(),
            database,
            root / "catalogue.sqlite",
            root / "artifacts",
            _test_reviewer_access(repository),
            database_authority=repository.authority,
            provider_recovery=recovery,
        )
        with patch.object(composition, "run_cover_packet_reviewer") as dispatch:
            pipeline = _run_qualified_cover_pipeline(
                repository, root, report, proposal, supervisor
            )
            for _ in range(64):
                if not pipeline.run_next(1):
                    break
            else:
                pytest.fail("cover pipeline did not reach a terminal state")
        dispatch.assert_not_called()

        terminal = repository._connection.execute(
            "SELECT stage,state,report_id,proposal_set_id,review_case_id,failure_code "
            "FROM review_preparation_work WHERE item_id='item-1'"
        ).fetchone()
        assert terminal is not None
        assert terminal[:4] == (
            "complete",
            "complete",
            report.report_id,
            proposal.proposal_set_id,
        )
        assert terminal[4] is not None
        assert terminal[5] is None

        selection_rows = repository._connection.execute(
            "SELECT selection.document FROM current_cover_selection_authorities current "
            "JOIN cover_selection_authorities selection "
            "ON selection.selection_id=current.selection_id"
        ).fetchall()
        if scenario == "downgrade" or (
            option == "replace_if_strictly_better" and scenario == "upgrade"
        ):
            assert selection_rows == []
            assert repository.pending_field_effect_decision_documents(1) == ()
            assert repository._connection.execute(
                "SELECT count(*) FROM field_effects"
            ).fetchone() == (0,)
            supervisor.stop()
            repository.close()
            return

        assert len(selection_rows) == 1
        automatic = CoverAutomaticDecision.from_json(
            json.loads(selection_rows[0][0])["decision_document"]
        )
        assert automatic.policy_option == option
        assert automatic.qualification_identity is None
        assert (
            json.loads(automatic.to_json())["schema_version"]
            == "cover-automatic-decision-v3"
        )
        assert CoverAutomaticDecision.from_json(automatic.to_json()) == automatic
        other = CoverAutomaticDecision.create(
            **(
                automatic._core()
                | {
                    "rank_key": automatic.rank_key,
                    "citation_ids": automatic.citation_ids,
                    "policy_option": "replace_if_strictly_better"
                    if option == "fixed_source_order"
                    else "fixed_source_order",
                }
            ),
            occurred_at=automatic.occurred_at,
        )
        assert (other.decision_id, other.idempotency_digest, other.rule_id) != (
            automatic.decision_id,
            automatic.idempotency_digest,
            automatic.rule_id,
        )
        from mechanical_librarian.adapters.persistence.field_effect_validation import (
            _cover_automatic_decision_value,
        )

        consideration = repository._connection.execute(
            "SELECT document FROM cover_considerations WHERE consideration_id=?",
            (automatic.consideration_id,),
        ).fetchone()[0]
        case_document = repository._connection.execute(
            "SELECT document FROM review_cases WHERE review_case_id=?",
            (automatic.review_case_id,),
        ).fetchone()[0]
        with pytest.raises(FieldEffectError, match="authority_binding"):
            _cover_automatic_decision_value(
                repository._connection,
                other,
                ReviewCase.from_json(case_document),
                report,
                proposal,
                consideration,
            )
        authority = PersistedFieldEffectAuthority(repository)
        snapshot = authority.resolve(automatic)
        repository.grant_field_effect_consents(
            snapshot.connection,
            (snapshot.library,),
            (snapshot.destination,),
            10,
        )
        target = FakeTargetService()
        application = GuardedFieldApplication(
            repository,
            target,
            FakeBackupReadiness(),
            FakeCapabilityObserver(CAPABILITY),
            authority,
            FakeDependencyCurrentness(),
            PersistedEffectConsent(repository),
            PrivateCoverRecoveryStore(
                root / "recovery", lambda _plan: b"incumbent-original"
            ),
            FakeForwardReevaluation(),
            Clock(),
            FakePreclaimHook(),
            FakePreclaimHook(),
            FakePreclaimHook(),
        )
        operation = application.plan(
            automatic,
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
        verified = application.apply(operation.plan.plan_id)
        assert verified.state == "verified"
        assert target.read(operation.plan).value == automatic.artifact_id
        assert repository.field_effect_activity_page(
            search=None,
            event_type="metadata",
            occurred_after=0,
            page=1,
            page_size=25,
        )["events"]
        supervisor.stop()
        repository.close()
        reopened = open_repository(database)
        try:
            retained = reopened._connection.execute(
                "SELECT document FROM cover_selection_authorities WHERE decision_id=?",
                (automatic.decision_id,),
            ).fetchone()[0]
            assert (
                CoverAutomaticDecision.from_json(
                    json.loads(retained)["decision_document"]
                ).policy_option
                == option
            )
            assert reopened.get_field_effect(operation.plan.plan_id).state == "verified"
            events = reopened.field_effect_activity_page(
                search=None, event_type="all", occurred_after=0, page=1, page_size=25
            )["events"]
            assert any(
                event.get("record", {}).get("policy_option") == option
                for event in events
            )
        finally:
            reopened.close()


def test_selected_original_reaches_verified_guarded_fake_service_application() -> None:
    incumbent_preview = _preview(1)
    alternative_preview = _preview(2)
    sources = (
        _source(1, incumbent_preview, incumbent=True),
        _source(2, alternative_preview, embedded=True),
    )
    base = _report(sources)
    incumbent = sources[0].artifact
    assert incumbent is not None
    fields = tuple(
        TargetField(
            field.destination,
            TargetFieldState.VALUE,
            incumbent.artifact_id,
        )
        if field.destination is TargetDestination.COVER
        else field
        for field in base.target.fields
    )
    report = MetadataReport.create(
        target=TargetFacts(
            base.target.connection,
            base.target.library,
            base.target.item,
            fields,
        ),
        file=base.file,
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=base.providers,
        cover_sources=base.cover_sources,
    )

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        repository = open_repository(root / "application.sqlite3")
        artifacts = CoverArtifactStore(root / "artifacts")
        for index, (source, preview) in enumerate(
            zip(
                sources,
                (incumbent_preview, alternative_preview),
                strict=True,
            ),
            1,
        ):
            assert source.artifact is not None
            artifacts.stage(source.artifact, f"original-{index}".encode(), preview)

        repository.publish(report, lambda: True)
        proposal_set = evaluate_report(report)
        repository.publish_proposal(proposal_set)
        review_case_id = json.loads(repository.admit_review_case(proposal_set))[
            "review_case_id"
        ]
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(review_case_id))["case"]
        )
        proposal = next(
            value for value in case.proposals if value["destination"] == "cover"
        )
        policy = CoverPolicy()
        repository.bind_current_cover_policy(policy.reference)
        consideration_now = [100]
        consideration_service = CoverConsiderationService(
            repository,
            read_preview=lambda artifact_id, _variant: artifacts.read_preview_digest(
                next(
                    source.artifact.preview_digest
                    for source in sources
                    if source.artifact is not None
                    and source.artifact.artifact_id == artifact_id
                )
            ),
            read_preview_digest=artifacts.read_preview_digest,
            clock=lambda: consideration_now[0],
            policy=policy,
        )
        repository.bind_current_cover_replay(consideration_service.replay_reference)
        consideration = consideration_service.ensure(
            review_case_id, report.to_json(), proposal
        )
        selected = next(
            candidate
            for candidate in consideration["candidates"]
            if not candidate["incumbent"] and candidate["selectable"]
        )
        selected_value = {
            "kind": "cover",
            "value": selected["artifact_id"],
        }
        decision = HumanDecision.create(
            case,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination="cover",
            actor="owner",
            outcome="accept",
            selected_value=selected_value,
            idempotency_key="cover-activation-0001",
            occurred_at=101,
        )
        repository._connection.execute(
            "CREATE TEMP TRIGGER synthetic_cover_outcome_failure "
            "BEFORE INSERT ON cover_consideration_events "
            "WHEN NEW.kind='selected' "
            "BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
        with pytest.raises(MetadataReportError):
            repository.record_human_decision(
                decision,
                cover_consideration_id=str(consideration["consideration_id"]),
            )
        assert repository._connection.execute(
            "SELECT count(*) FROM human_decisions"
        ).fetchone() == (0,)
        repository._connection.execute("DROP TRIGGER synthetic_cover_outcome_failure")
        repository.record_human_decision(
            decision,
            cover_consideration_id=str(consideration["consideration_id"]),
        )

        authority = PersistedFieldEffectAuthority(repository)
        changed_profile = CoverConsiderationService(
            repository,
            read_preview=lambda artifact_id, _variant: artifacts.read_preview_digest(
                next(
                    source.artifact.preview_digest
                    for source in sources
                    if source.artifact is not None
                    and source.artifact.artifact_id == artifact_id
                )
            ),
            read_preview_digest=artifacts.read_preview_digest,
            clock=lambda: consideration_now[0],
            policy=policy,
            reviewer_profile=b"changed-profile",
        )
        repository.bind_current_cover_replay(changed_profile.replay_reference)
        with pytest.raises(FieldEffectError, match="authority_binding"):
            authority.resolve(decision)
        repository.bind_current_cover_replay(consideration_service.replay_reference)
        snapshot = authority.resolve(decision)
        dependencies = PersistedEffectDependencies(repository)
        consent = FakeConsentCurrentness(
            repository.field_effect_consent_envelope_identity
        )
        consent.allow(
            connection=snapshot.connection,
            library=snapshot.library,
            destination=snapshot.destination,
        )
        target = FakeTargetService()
        application = GuardedFieldApplication(
            repository,
            target,
            FakeBackupReadiness(),
            FakeCapabilityObserver(CAPABILITY),
            authority,
            dependencies,
            consent,
            PrivateCoverRecoveryStore(
                root / "recovery", lambda _plan: b"incumbent-original"
            ),
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
        stale = repository.transition_field_effect(
            operation.plan.plan_id,
            "planned",
            "stale",
            102,
            "authority",
        )
        assert "stale" == stale.state
        assert repository.current_cover_consideration_document(review_case_id) is None
        stale_detail = InspectCurrentReviewCase(repository, consideration_service).run(
            review_case_id
        )
        stale_cover = next(
            field for field in stale_detail["fields"] if field["destination"] == "cover"
        )
        assert stale_cover["human_decision"] is None
        assert stale_cover["cover_consideration"] is None
        assert stale_cover["needs_attention"]

        consideration_now[0] = 103
        successor = consideration_service.ensure(
            review_case_id, report.to_json(), proposal
        )
        assert consideration["consideration_id"] == successor["supersedes_id"]
        replacement = HumanDecision.create(
            case,
            proposal_id=proposal["proposal_id"],
            target_fingerprint=case.target_fingerprint,
            destination="cover",
            actor="owner",
            outcome="accept",
            selected_value=selected_value,
            idempotency_key="cover-activation-0002",
            occurred_at=104,
        )
        repository.record_human_decision(
            replacement,
            cover_consideration_id=str(successor["consideration_id"]),
        )
        snapshot = authority.resolve(replacement)
        operation = application.plan(
            replacement,
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

        verified = application.apply(operation.plan.plan_id)

        assert "verified" == verified.state
        assert selected["artifact_id"] == target.read(operation.plan).value
        events = consideration_service.history(review_case_id)
        assert Counter(
            {"selected": 2, "automatic_prepared": 2, "superseded": 1, "stale": 1}
        ) == Counter(event["kind"] for event in events)
        occurred_at = [cast(int, event["occurred_at"]) for event in events]
        assert sorted(occurred_at, reverse=True) == occurred_at
        history = InspectCurrentReviewCase(repository, consideration_service).history(
            review_case_id
        )
        selected_history = next(
            item
            for item in history["items"]
            if item["kind"] == "selected" and not item["consideration"]["current"]
        )
        assert not selected_history["consideration"]["current"]
        assert selected_value == selected_history["decision"]["selected_value"]
        assert {
            "actor",
            "application",
            "application_reason",
            "decision_id",
            "destination",
            "occurred_at",
            "outcome",
            "proposal_id",
            "review_case_id",
            "selected_value",
            "target_fingerprint",
        } == set(selected_history["decision"])
        assert (
            successor["candidates"] == selected_history["consideration"]["candidates"]
        )
        assert successor["policy"] == selected_history["consideration"]["policy"]
        assert repository.field_effect_dependencies_current(snapshot.dependency_digests)
        repository.close()


def test_artifact_recovery_borrower_shares_admission_without_holding_it_for_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from mechanical_librarian.application.operation_admission import OperationAdmission
    from mechanical_librarian.application.review_pipeline_stages import (
        ReviewPipelineStages,
    )

    admission = OperationAdmission()
    repository = open_repository(
        tmp_path / "reports.sqlite", cross_thread=True, admission=admission
    )
    _qualified_cover_report(tmp_path)
    recovery = composition.SQLiteProviderRecoveryStore.open(
        tmp_path / "recovery.sqlite"
    )
    stages: list[ReviewPipelineStages] = []
    make_pipeline = ReviewPipelineStages.pipeline

    def capture(self, *args, **kwargs):
        stages.append(self)
        return make_pipeline(self, *args, **kwargs)

    monkeypatch.setattr(ReviewPipelineStages, "pipeline", capture)
    supervisor = composition.create_review_pipeline(
        _pipeline_configuration(),
        tmp_path / "reports.sqlite",
        tmp_path / "catalogue.sqlite",
        tmp_path / "artifacts",
        _test_reviewer_access(repository),
        database_authority=repository.authority,
        provider_recovery=recovery,
        admission=admission,
    )
    entered_files = threading.Event()
    release_files = threading.Event()
    failures: list[BaseException] = []
    borrowers = []
    open_real = composition.open_repository
    recover_real = CoverArtifactStore.recover_unreferenced

    def open_borrower(*args, **kwargs):
        borrower = open_real(*args, **kwargs)
        borrowers.append(borrower)
        return borrower

    def recover_files(self, referenced):
        assert not admission.held
        entered_files.set()
        assert release_files.wait(5)
        return recover_real(self, referenced)

    def run() -> None:
        try:
            with admission.classify("background"):
                stages[0]._recover_artifacts()
        except BaseException as error:
            failures.append(error)

    monkeypatch.setattr(composition, "open_repository", open_borrower)
    monkeypatch.setattr(CoverArtifactStore, "recover_unreferenced", recover_files)
    thread = threading.Thread(target=run)
    try:
        admission.acquire()
        try:
            thread.start()
            with admission._condition:
                assert admission._condition.wait_for(
                    lambda: bool(admission._waiting), 5
                )
                assert admission._waiting[0].work_class == "background"
            assert not entered_files.is_set()
        finally:
            admission.release()
        assert entered_files.wait(5)
        assert borrowers and all(
            item.operation_admission is admission for item in borrowers
        )
        admission.acquire()
        admission.release()
    finally:
        release_files.set()
        thread.join(5)
        assert not thread.is_alive()
        supervisor.stop()
        repository.close()
    assert failures == []


def test_cover_decision_v3_binds_option_and_rejects_v2_and_tampering():
    from mechanical_librarian.domain.automatic_decision import AutomaticDecisionError
    from mechanical_librarian.domain.cover_automatic_decision import (
        parse_automatic_authority,
    )

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/cover-authority-v2.json").read_text()
    )
    prior = fixture["decisions"][0]
    with pytest.raises(AutomaticDecisionError):
        parse_automatic_authority(prior)
    raw = json.loads(prior)
    arguments = {
        name: raw[name]
        for name in CoverAutomaticDecision.__dataclass_fields__
        if name not in {"decision_id", "idempotency_digest"}
    }
    arguments.update(
        rank_key=tuple(raw["rank_key"]),
        citation_ids=tuple(raw["citation_ids"]),
        policy_option="replace_if_strictly_better",
    )
    current = CoverAutomaticDecision.create(**arguments)
    assert parse_automatic_authority(current.to_json()) == current
    for bad in (None, "unknown", "replace_if_strictly_better:audiobook_cover_fill"):
        with pytest.raises(AutomaticDecisionError):
            CoverAutomaticDecision.create(**(arguments | {"policy_option": bad}))
    for bad in (None, "unknown", "fixed_source_order"):
        altered = json.loads(current.to_json())
        altered["policy_option"] = bad
        with pytest.raises(AutomaticDecisionError):
            parse_automatic_authority(
                json.dumps(altered, sort_keys=True, separators=(",", ":"))
            )
    altered.pop("policy_option")
    with pytest.raises(AutomaticDecisionError):
        parse_automatic_authority(
            json.dumps(altered, sort_keys=True, separators=(",", ":"))
        )
    from mechanical_librarian.domain.audiobook_decision_qualification import (
        F46_IDENTITIES_BY_DESTINATION,
    )

    identity = next(iter(F46_IDENTITIES_BY_DESTINATION["cover"]))
    with pytest.raises(AutomaticDecisionError):
        CoverAutomaticDecision.create(
            **(arguments | {"qualification_identity": identity})
        )


@pytest.mark.parametrize(
    "nested",
    (
        None,
        "{}",
        '{"schema_version":null}',
        '{"schema_version":"cover-automatic-decision-v2"}',
        "broken",
    ),
)
def test_cover_selection_sql_requires_explicit_current_automatic_document(nested):
    import sqlite3

    from mechanical_librarian.adapters.persistence.schema import (
        _COVER_SELECTION_AUTHORITIES_SQL,
    )

    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(_COVER_SELECTION_AUTHORITIES_SQL)
        document = {} if nested is None else {"decision_document": nested}
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO cover_selection_authorities VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "cover_selection_" + "a" * 64,
                    "primary",
                    "ebooks",
                    "item",
                    "automatic",
                    "automatic_decision_" + "b" * 64,
                    "consideration",
                    None,
                    1,
                    json.dumps(document),
                ),
            )
    finally:
        connection.close()
