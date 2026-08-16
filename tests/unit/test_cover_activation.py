from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import (
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeConsentCurrentness,
    FakeForwardReevaluation,
    FakePreclaimHook,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.field_effect_runtime import (
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
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.human_decision import HumanDecision
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
)
from mechanical_librarian.domain.proposal_set import evaluate_report
from mechanical_librarian.domain.review_case import ReviewCase
from tests.unit.test_cover_packets import _preview, _report, _source
from tests.unit.test_field_effects import CAPABILITY, Clock


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
        repository.bind_current_cover_replay(
            consideration_service.replay_reference
        )
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
        assert (
            repository._connection.execute(
                "SELECT count(*) FROM human_decisions"
            ).fetchone()
            == (0,)
        )
        repository._connection.execute(
            "DROP TRIGGER synthetic_cover_outcome_failure"
        )
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
        repository.bind_current_cover_replay(
            consideration_service.replay_reference
        )
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
        stale_detail = InspectCurrentReviewCase(
            repository, consideration_service
        ).run(review_case_id)
        stale_cover = next(
            field
            for field in stale_detail["fields"]
            if field["destination"] == "cover"
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
        history = InspectCurrentReviewCase(
            repository, consideration_service
        ).history(review_case_id)
        selected_history = next(
            item
            for item in history["items"]
            if item["kind"] == "selected"
            and not item["consideration"]["current"]
        )
        assert not selected_history["consideration"]["current"]
        assert selected_value == selected_history["decision"]["selected_value"]
        assert {
            "actor",
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
            successor["candidates"]
            == selected_history["consideration"]["candidates"]
        )
        assert successor["policy"] == selected_history["consideration"]["policy"]
        assert repository.field_effect_dependencies_current(
            snapshot.dependency_digests
        )
        repository.close()
