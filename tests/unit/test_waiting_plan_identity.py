"""A write waiting for its connection is never orphaned by a new plan (#1543).

A write staled only by a lost connection waits and is planned again into the
same plan once the library answers (#1402, #1415). The plan identity binds the
persisted authority, the target pre-image and version, the dependency digests,
the capability digest and the consent, so a re-plan that resolves any of them
differently mints another plan. These tests take one such input through the
store: the owner stops writes for the library while a write waits, then grants
them again before the library comes back.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import (
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeForwardReevaluation,
    FakePreclaimHook,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.field_effect_runtime import (
    PersistedEffectConsent,
    PersistedEffectDependencies,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.domain.field_effect import (
    FieldEffectOperation,
    FieldEffectPlan,
)
from tests.support.field_effects import CAPABILITY, Clock, _EffectEnvironment
from tests.support.field_effects import effects as effects
from tests.unit.test_connection_replan import _automatic_tags_lane, _sent
from tests.unit.test_cover_activation import _selected_cover


def _decision_plans(
    effects: _EffectEnvironment, decision_id: str
) -> list[tuple[str, str, str | None]]:
    return [
        (operation.plan.plan_id, operation.state, operation.failure)
        for operation in (
            FieldEffectOperation.from_json(row[0])
            for row in effects.repository._connection.execute(
                "SELECT document FROM field_effects WHERE decision_id=? ORDER BY rowid",
                (decision_id,),
            )
        )
    ]


def test_a_library_regrant_never_leaves_a_waiting_field_plan_behind(
    effects: _EffectEnvironment,
) -> None:
    """Stopping and granting writes again while a field waits (#1543)."""
    decision, _, operation, lane = _automatic_tags_lane(effects)
    waiting_id = operation.plan.plan_id
    sent = _sent(effects)
    effects.capabilities.category = "connection_unavailable"
    lane._run_until_idle()
    assert _decision_plans(effects, decision.decision_id) == [
        (waiting_id, "stale", "connection")
    ]
    # The owner stops writes for the library, then grants them again, as the
    # Activity page's commands record them, while the library is away.
    lane.revoke("primary", "ebooks")
    effects.repository.grant_field_effect_consent_matrix(
        "primary", (("ebooks", ("tags",)),), effects.clock()
    )
    effects.capabilities.category = "allowed"
    lane.wake()
    lane._run_until_idle()
    plans = _decision_plans(effects, decision.decision_id)
    # Measured: the re-plan binds the new consent, so it is another plan.
    assert [plan_id for plan_id, *_ in plans][0] == waiting_id
    assert len(plans) == 2
    replanned_id = plans[1][0]
    assert replanned_id != waiting_id
    assert plans[1][1:] == ("verified", None)
    assert sent == [replanned_id]
    # Required: the waiting plan is not left waiting beside the new one.
    assert plans[0][1] == "superseded", plans


def test_a_library_regrant_never_leaves_a_waiting_cover_or_its_bytes_behind() -> None:
    """The same sequence for a cover: its restore point must not outlive it."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        setup = _selected_cover(root)
        repository = setup.repository
        repository.record_human_decision(
            setup.decision,
            cover_consideration_id=str(setup.consideration["consideration_id"]),
            cover_confirmation={
                "expected_selection_id": None,
                "confirm_unverified_match": False,
            },
        )
        authority = PersistedFieldEffectAuthority(repository)
        snapshot = authority.resolve(setup.decision)
        connection, library = snapshot.connection, snapshot.library
        repository.grant_field_effect_consents(connection, (library,), ("cover",), 101)
        capabilities = FakeCapabilityObserver(CAPABILITY)
        preclaim = FakePreclaimHook()
        recovery = PrivateCoverRecoveryStore(
            root / "recovery", lambda _plan: b"incumbent-original"
        )
        target = FakeTargetService()
        application = GuardedFieldApplication(
            repository,
            target,
            FakeBackupReadiness(),
            capabilities,
            authority,
            PersistedEffectDependencies(repository),
            PersistedEffectConsent(repository),
            recovery,
            FakeForwardReevaluation(),
            Clock(),
            preclaim,
            FakePreclaimHook(),
            FakePreclaimHook(),
        )

        def plan() -> FieldEffectOperation:
            resolved = authority.resolve(setup.decision)
            operation = application.plan(
                setup.decision,
                connection=resolved.connection,
                library=resolved.library,
                work_id=resolved.work_id,
                destination=resolved.destination,
                pre_value=resolved.pre_value,
                desired_value=resolved.decision_value,
                target_fingerprint=resolved.target_fingerprint,
                target_version=resolved.target_version,
                dependency_digests=resolved.dependency_digests,
                capability_digest=CAPABILITY,
            )
            assert operation is not None
            return operation

        waiting = plan()
        waiting_id = waiting.plan.plan_id
        target.seed(waiting.plan)

        def lose(_: FieldEffectPlan) -> None:
            capabilities.category = "connection_unavailable"

        preclaim.callback = lose
        stale = application.apply(waiting_id)
        assert (stale.state, stale.failure) == ("stale", "connection")
        assert recovery.exists(waiting_id)
        repository.revoke_library_field_effect_consents(connection, library, 200)
        repository.grant_field_effect_consents(connection, (library,), ("cover",), 201)
        capabilities.category = "allowed"
        replanned = plan()
        # Measured: the re-plan binds the new consent, so it is another plan.
        assert replanned.plan.plan_id != waiting_id
        assert replanned.state == "planned"
        left = repository.get_field_effect(waiting_id)
        # Required: the waiting cover is superseded and its restore point and
        # cleanup are discharged, rather than left waiting with its bytes.
        assert left.state == "superseded", (left.state, left.failure)
        assert not repository.field_effect_cleanup_pending(waiting_id)
        assert not recovery.exists(waiting_id)
        repository.close()
