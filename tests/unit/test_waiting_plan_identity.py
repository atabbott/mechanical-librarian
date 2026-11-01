"""A write waiting for its connection is never orphaned by a new plan (#1543).

A write staled only by a lost connection waits and is planned again into the
same plan once the library answers (#1402, #1415). The plan identity binds the
persisted authority, the target pre-image and version, the dependency digests,
the capability digest and the consent, so a re-plan that resolves any of them
differently mints another plan. The consent is the one input an owner command
can change for the same decision: stopping writes for a library, or granting
them again for a changed envelope or set of fields, retires it. Each retirement
closes the plans waiting on it, with a cover's cleanup, and a plan whose consent
is already withdrawn stales through its own code instead of starting to wait.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

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
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import GuardedFieldApplication
from mechanical_librarian.domain.field_effect import (
    FieldEffectError,
    FieldEffectOperation,
    FieldEffectPlan,
)
from tests.support.field_effects import CAPABILITY, Clock, _EffectEnvironment
from tests.support.field_effects import effects as effects
from tests.unit.test_connection_replan import _automatic_tags_lane, _sent
from tests.unit.test_cover_activation import _selected_cover

_SITES = ("library_revoke", "matrix_drop", "matrix_envelope", "grant_envelope")


def _decision_plans(
    repository: SQLiteMetadataReportRepository, decision_id: str
) -> list[tuple[str, str, str | None]]:
    return [
        (operation.plan.plan_id, operation.state, operation.failure)
        for operation in (
            FieldEffectOperation.from_json(row[0])
            for row in repository._connection.execute(
                "SELECT document FROM field_effects WHERE decision_id=? ORDER BY rowid",
                (decision_id,),
            )
        )
    ]


def _cleanup(
    repository: SQLiteMetadataReportRepository, plan_id: str
) -> list[tuple[object, ...]]:
    return repository._connection.execute(
        "SELECT state FROM field_effect_cleanups WHERE plan_id=?", (plan_id,)
    ).fetchall()


def _signals(repository: SQLiteMetadataReportRepository) -> list[tuple[object, ...]]:
    return [
        (value["kind"], value["cause"])
        for value in cast(
            "list[dict[str, object]]", repository.operational_snapshot()["signals"]
        )
    ]


def _reopen(effects: _EffectEnvironment) -> None:
    """Reopen the store so startup validation reads every new fact."""
    effects.repository.close()
    effects.repository = open_repository(
        effects.database_path, create=False, cross_thread=True
    )


def _retire(
    effects: _EffectEnvironment,
    lane: ContinuousGuardedApplication,
    site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retire the tags consent through one owner command's store seam."""
    repository = effects.repository
    if site == "library_revoke":
        lane.revoke("primary", "ebooks")
    elif site == "matrix_drop":
        repository.grant_field_effect_consent_matrix(
            "primary", (("ebooks", ("description",)),), effects.clock()
        )
    else:
        # A changed envelope stands for a grant made under other semantics;
        # the patch lasts only for the grant itself.
        with monkeypatch.context() as patch:
            patch.setattr(
                repository._field_effects,
                "field_effect_consent_envelope_identity",
                lambda _library: "e" * 64,
            )
            if site == "matrix_envelope":
                repository.grant_field_effect_consent_matrix(
                    "primary", (("ebooks", ("tags",)),), effects.clock()
                )
            else:
                repository.grant_field_effect_consents(
                    "primary", ("ebooks",), ("tags",), effects.clock()
                )


def _waiting_tags(
    effects: _EffectEnvironment,
) -> tuple[str, str, ContinuousGuardedApplication]:
    decision, _, operation, lane = _automatic_tags_lane(effects)
    effects.capabilities.category = "connection_unavailable"
    lane._run_until_idle()
    waiting_id = operation.plan.plan_id
    assert _decision_plans(effects.repository, decision.decision_id) == [
        (waiting_id, "stale", "connection")
    ]
    return decision.decision_id, waiting_id, lane


def test_a_library_regrant_never_leaves_a_waiting_field_plan_behind(
    effects: _EffectEnvironment,
) -> None:
    """Stopping and granting writes again while a field waits (#1543)."""
    decision_id, waiting_id, lane = _waiting_tags(effects)
    sent = _sent(effects)
    # The owner stops writes for the library, then grants them again, as the
    # Activity page's commands record them, while the library is away.
    lane.revoke("primary", "ebooks")
    effects.repository.grant_field_effect_consent_matrix(
        "primary", (("ebooks", ("tags",)),), effects.clock()
    )
    effects.capabilities.category = "allowed"
    lane.wake()
    lane._run_until_idle()
    plans = _decision_plans(effects.repository, decision_id)
    # The re-plan binds the new consent, so it is another plan, written once.
    assert [plan_id for plan_id, *_ in plans][0] == waiting_id
    assert len(plans) == 2
    replanned_id = plans[1][0]
    assert replanned_id != waiting_id
    assert plans[1][1:] == ("verified", None)
    assert sent == [replanned_id]
    # The waiting plan closed with its consent instead of waiting beside it.
    assert plans[0][1:] == ("superseded", "consent_revoked")
    _reopen(effects)


@pytest.mark.parametrize("site", _SITES)
def test_retiring_a_consent_closes_the_field_plan_waiting_on_it(
    effects: _EffectEnvironment, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    decision_id, waiting_id, lane = _waiting_tags(effects)
    sent = _sent(effects)
    pending = effects.repository.pending_field_effect_decision_counts()
    assert ("automatic_recovery", "in_progress") in _signals(effects.repository)
    _retire(effects, lane, site, monkeypatch)
    assert _decision_plans(effects.repository, decision_id) == [
        (waiting_id, "superseded", "consent_revoked")
    ]
    # Neither state holds the decision, so the pending count is unchanged;
    # nothing is left reporting a recovery in progress.
    assert effects.repository.pending_field_effect_decision_counts() == pending
    assert all(kind != "automatic_recovery" for kind, _ in _signals(effects.repository))
    # With no current consent for tags, nothing plans it again.
    effects.capabilities.category = "allowed"
    lane.wake()
    lane._run_until_idle()
    assert _decision_plans(effects.repository, decision_id) == [
        (waiting_id, "superseded", "consent_revoked")
    ]
    assert sent == []
    _reopen(effects)


@pytest.mark.parametrize("connection", ("lost", "up"))
def test_a_ready_field_plan_under_a_withdrawn_consent_never_starts_waiting(
    effects: _EffectEnvironment, connection: str
) -> None:
    """A withdrawn consent outranks a lost connection (#1543); with the
    connection up the same sequence already staled through `consent`."""
    decision, _, operation, lane = _automatic_tags_lane(effects)
    plan_id = operation.plan.plan_id
    sent = _sent(effects)
    lane.revoke("primary", "ebooks")
    if connection == "lost":
        effects.capabilities.category = "connection_unavailable"
    lane._run_until_idle()
    assert _decision_plans(effects.repository, decision.decision_id) == [
        (plan_id, "stale", "consent")
    ]
    effects.repository.grant_field_effect_consent_matrix(
        "primary", (("ebooks", ("tags",)),), effects.clock()
    )
    effects.capabilities.category = "allowed"
    lane.wake()
    lane._run_until_idle()
    # The stale holds its decision as before: no second plan stands beside it.
    assert _decision_plans(effects.repository, decision.decision_id) == [
        (plan_id, "stale", "consent")
    ]
    assert sent == []
    _reopen(effects)


@pytest.mark.parametrize("command", ("grant", "matrix"))
def test_a_same_envelope_grant_keeps_the_waiting_plan_and_its_identity(
    effects: _EffectEnvironment, command: str
) -> None:
    decision_id, waiting_id, lane = _waiting_tags(effects)
    sent = _sent(effects)
    if command == "grant":
        effects.repository.grant_field_effect_consents(
            "primary", ("ebooks",), ("tags",), effects.clock()
        )
    else:
        effects.repository.grant_field_effect_consent_matrix(
            "primary", (("ebooks", ("tags",)),), effects.clock()
        )
    assert _decision_plans(effects.repository, decision_id) == [
        (waiting_id, "stale", "connection")
    ]
    effects.capabilities.category = "allowed"
    lane.wake()
    lane._run_until_idle()
    assert _decision_plans(effects.repository, decision_id) == [
        (waiting_id, "verified", None)
    ]
    assert sent == [waiting_id]
    _reopen(effects)


def test_revoking_another_connection_leaves_the_waiting_plan_waiting(
    effects: _EffectEnvironment,
) -> None:
    decision_id, waiting_id, _ = _waiting_tags(effects)
    effects.repository.grant_field_effect_consents(
        "secondary", ("ebooks",), ("tags",), effects.clock()
    )
    assert (
        effects.repository.revoke_library_field_effect_consents(
            "secondary", "ebooks", effects.clock()
        )
        == 1
    )
    assert _decision_plans(effects.repository, decision_id) == [
        (waiting_id, "stale", "connection")
    ]
    _reopen(effects)


def test_a_stopped_connection_stale_keeps_its_code_under_a_withdrawn_consent(
    effects: _EffectEnvironment,
) -> None:
    """Only a lost connection is rewritten; the bound's end keeps its code."""
    decision, _, operation, lane = _automatic_tags_lane(effects)
    lane.revoke("primary", "ebooks")
    stopped = effects.repository.transition_field_effect(
        operation.plan.plan_id,
        "planned",
        "stale",
        effects.clock(),
        "connection_exhausted",
    )
    assert (stopped.state, stopped.failure) == ("stale", "connection_exhausted")
    _reopen(effects)


@dataclass
class _CoverLane:
    """A real owner cover decision over the store, with persisted consent."""

    root: Path
    repository: SQLiteMetadataReportRepository
    application: GuardedFieldApplication
    capabilities: FakeCapabilityObserver
    preclaim: FakePreclaimHook
    recovery: PrivateCoverRecoveryStore
    connection: str
    library: str
    plan: Callable[[], FieldEffectOperation]

    def lose_connection_after_capture(self) -> None:
        def lose(_: FieldEffectPlan) -> None:
            self.capabilities.category = "connection_unavailable"

        self.preclaim.callback = lose

    def reopen(self) -> None:
        self.repository.close()
        self.repository = open_repository(
            self.root / "application.sqlite3", create=False
        )


@pytest.fixture
def cover() -> Iterator[_CoverLane]:
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
        repository.grant_field_effect_consents(
            snapshot.connection, (snapshot.library,), ("cover",), 101
        )
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
            target.seed(operation.plan)
            return operation

        lane = _CoverLane(
            root,
            repository,
            application,
            capabilities,
            preclaim,
            recovery,
            snapshot.connection,
            snapshot.library,
            plan,
        )
        try:
            yield lane
        finally:
            lane.repository.close()


def _waiting_cover(cover: _CoverLane) -> str:
    waiting_id = cover.plan().plan.plan_id
    cover.lose_connection_after_capture()
    stale = cover.application.apply(waiting_id)
    assert (stale.state, stale.failure) == ("stale", "connection")
    assert cover.recovery.exists(waiting_id)
    assert _cleanup(cover.repository, waiting_id) == []
    return waiting_id


def test_a_library_regrant_never_leaves_a_waiting_cover_or_its_bytes_behind(
    cover: _CoverLane,
) -> None:
    """The same sequence for a cover: its restore point must not outlive it."""
    waiting_id = _waiting_cover(cover)
    cover.repository.revoke_library_field_effect_consents(
        cover.connection, cover.library, 200
    )
    left = cover.repository.get_field_effect(waiting_id)
    assert (left.state, left.failure) == ("superseded", "consent_revoked")
    assert _cleanup(cover.repository, waiting_id) == [("pending",)]
    # The production revoke wakes the lane, whose pass runs pending cleanups.
    assert cover.application.run_pending_cleanups() == 1
    assert _cleanup(cover.repository, waiting_id) == [("completed",)]
    assert not cover.recovery.exists(waiting_id)
    cover.repository.grant_field_effect_consents(
        cover.connection, (cover.library,), ("cover",), 201
    )
    cover.capabilities.category = "allowed"
    replanned = cover.plan()
    # The re-plan binds the new consent, so it is another plan.
    assert replanned.plan.plan_id != waiting_id
    assert replanned.state == "planned"
    assert cover.repository.get_field_effect(waiting_id).state == "superseded"
    cover.reopen()


def test_a_ready_cover_under_a_withdrawn_consent_never_starts_waiting(
    cover: _CoverLane,
) -> None:
    plan_id = cover.plan().plan.plan_id
    cover.repository.revoke_library_field_effect_consents(
        cover.connection, cover.library, 200
    )
    cover.capabilities.category = "connection_unavailable"
    stale = cover.application.apply(plan_id)
    assert (stale.state, stale.failure) == ("stale", "consent")
    assert _cleanup(cover.repository, plan_id) == [("completed",)]
    assert not cover.recovery.exists(plan_id)
    cover.reopen()


def test_a_failed_library_revoke_keeps_its_consent_and_the_waiting_cover(
    cover: _CoverLane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The supersession is part of the revoke: a fault undoes both."""
    waiting_id = _waiting_cover(cover)

    def fail(*_: object) -> None:
        raise sqlite3.OperationalError("synthetic")

    monkeypatch.setattr(
        cover.repository._field_effects, "_record_field_effect_event", fail
    )
    with pytest.raises(FieldEffectError, match="^persistence$"):
        cover.repository.revoke_library_field_effect_consents(
            cover.connection, cover.library, 200
        )
    monkeypatch.undo()
    assert cover.repository._connection.execute(
        "SELECT current FROM field_effect_consents"
    ).fetchall() == [(1,)]
    left = cover.repository.get_field_effect(waiting_id)
    assert (left.state, left.failure) == ("stale", "connection")
    assert _cleanup(cover.repository, waiting_id) == []
    assert cover.recovery.exists(waiting_id)
    cover.reopen()
