"""A policy activation never leaves a write waiting for its connection (#1599).

A write staled only by a lost connection waits for its library and is planned
again into the same plan (#1402, #1415). Activating a policy retires every
current review case. When the policy changed, the case never returns: its
successor is another case, and no decision transfers to it. Leaving Automatic
reactivates the unchanged policy, and preparation admits the same case again,
but an automatic field decision of the earlier generation never authorizes
again (#1596). Either way the waiting write would wait for good, so the
activation closes it in its own transaction, with a cover's cleanup. A waiting
write whose authority returns with its case keeps waiting and is still written
once. A later activation closes, whatever its origin, a write still waiting on a
case an earlier activation retired and preparation never readmitted, since that
case can return only as a new episode (#1657).

The field cases drive the real preparation harness of
`test_leaving_automatic_posture.py`; the cover cases drive the real owner cover
decision of `test_waiting_plan_identity.py`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import (
    FakeBackupReadiness,
    FakeCapabilityObserver,
    FakeDependencyCurrentness,
    FakeForwardReevaluation,
    FakeTargetService,
    PrivateCoverRecoveryStore,
)
from mechanical_librarian.adapters.field_effect_runtime import PersistedEffectConsent
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
    open_repository,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import (
    GuardedFieldApplication,
    TargetEffectResult,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import ReviewPipelineError
from mechanical_librarian.domain.field_effect import (
    FieldEffectOperation,
    FieldEffectPlan,
)
from mechanical_librarian.domain.metadata_policy import (
    DEFAULT_OPTIONS,
    OPTION_CATALOGUE,
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.proposal_set import ProposalSet
from tests.integration.test_leaving_automatic_posture import _accept, _Library
from tests.integration.test_leaving_automatic_posture import library as library
from tests.support.field_effects import Clock
from tests.unit.test_waiting_plan_identity import (
    _cleanup,
    _CoverLane,
    _decision_plans,
    _signals,
    _waiting_cover,
)
from tests.unit.test_waiting_plan_identity import cover as cover

_CLOSED = ("superseded", "authority_superseded")


class _FieldLane:
    """The library fixture's applier, with its connection observable."""

    def __init__(self, library: _Library, tmp_path: Path) -> None:
        repository = library.root
        self.target = FakeTargetService()
        self.capability = FakeCapabilityObserver("c" * 64)
        authority = PersistedFieldEffectAuthority(repository)
        service = GuardedFieldApplication(
            repository,
            self.target,
            FakeBackupReadiness(),
            self.capability,
            authority,
            FakeDependencyCurrentness(),
            PersistedEffectConsent(repository),
            PrivateCoverRecoveryStore(tmp_path / "recovery", lambda _: b"old"),
            FakeForwardReevaluation(),
            lambda: library.now[0],
        )
        self.scheduler = ContinuousGuardedApplication(
            repository,
            {("primary", "ebooks"): service},
            {("primary", "ebooks"): self.capability},
            authority,
            (("primary", "ebooks"),),
            lambda: library.now[0],
            lambda *_: None,
        )
        self.sent: list[str] = []
        apply = self.target.apply

        def counted(plan: FieldEffectPlan) -> TargetEffectResult:
            self.sent.append(plan.plan_id)
            return apply(plan)

        self.target.apply = counted  # type: ignore[method-assign]  # reason: the fake's write is the seam counted here, so a closed plan is proven to send nothing.

    def wait(self, library: _Library, decision_id: str) -> str:
        """Plan the decision, then lose the connection before it is written."""
        self.scheduler.schedule()
        operation = next(
            value
            for value in (
                FieldEffectOperation.from_json(document)
                for (document,) in library.root._connection.execute(
                    "SELECT document FROM field_effects WHERE decision_id=?",
                    (decision_id,),
                ).fetchall()
            )
        )
        self.target.seed(operation.plan)
        self.capability.category = "connection_unavailable"
        self.scheduler._run_until_idle()
        assert _decision_plans(library.root, decision_id) == [
            (operation.plan.plan_id, "stale", "connection")
        ]
        return operation.plan.plan_id

    def reconnect(self) -> None:
        self.capability.category = "allowed"
        for _ in range(4):
            self.scheduler.wake()
            self.scheduler._run_until_idle()

    def waiting_rows(self) -> list[tuple[object, object]]:
        """Activity's change rows, as the reviewer reads them."""
        activity = self.scheduler.activity(
            search=None, event_type="all", range_days=30, page=1, page_size=25
        )
        return [
            (event.get("change_id"), event.get("state"))
            for event in cast(list[dict[str, object]], activity["events"])
        ]


def _changed_policy(library: _Library, at: int) -> None:
    """Activate the fixture's policy with one owner-reviewed option changed."""
    root = library.root
    options = dict(DEFAULT_OPTIONS)
    destinations = {
        name: "unsupported" if name == "explicit" else "ask"
        for name in OPTION_CATALOGUE
    }
    options["contributor_names"] = "canonical"
    destinations["contributor_names"] = "automatic"
    options["date_precision"] = "full_date"
    profile = MetadataPolicyProfile.create("Changed policy", options, destinations)
    resolved = resolve_policy(profile, "ebooks")
    active, generation = root._connection.execute(
        "SELECT active_policy_id,generation FROM metadata_policy_state"
    ).fetchone()
    assert active != profile.policy_id
    root.create_metadata_policy_draft(profile.canonical_json)
    root.activate_metadata_policy(
        profile.policy_id,
        active,
        (("ebooks", resolved.resolved_id, resolved.canonical_json),),
        at,
        expected_active_generation=generation,
    )


def _reopen_fields(library: _Library, tmp_path: Path) -> None:
    """Reopen the store so startup validation reads every new fact."""
    library.root.close()
    open_repository(tmp_path / "application.sqlite3", create=False).close()


def _pending(repository: SQLiteMetadataReportRepository, decision_id: str) -> bool:
    return any(
        json.loads(document)["decision_id"] == decision_id
        for document in repository.pending_field_effect_decision_documents(100)
    )


def _owner_decision_id(library: _Library) -> str:
    case = library.case()
    destination = next(
        str(value)
        for _, value in library.attention()
        if value in {proposal["destination"] for proposal in case.proposals}
    )
    decision = _accept(case, destination, "waiting-owner-write", 11)
    library.root.record_human_decision(decision)
    library.root.grant_field_effect_consents("primary", ("ebooks",), (destination,), 12)
    return decision.decision_id


@pytest.mark.parametrize("origin", ["automatic", "owner"])
def test_a_policy_change_closes_a_waiting_field_write(
    library: _Library, tmp_path: Path, origin: str
) -> None:
    if origin == "automatic":
        decision_id = library.automatic().decision_id
        library.root.grant_field_effect_consents(
            "primary", ("ebooks",), ("authors",), 12
        )
    else:
        decision_id = _owner_decision_id(library)
    lane = _FieldLane(library, tmp_path)
    library.now[0] = 13
    waiting_id = lane.wait(library, decision_id)
    case_before = library.case_id()
    # A lost connection releases its decision, so it reads pending (#1402).
    assert _pending(library.root, decision_id)
    assert ("automatic_recovery", "in_progress") in _signals(library.root)

    _changed_policy(library, 20)

    # Retiring the case leaves nothing of it pending, before this change as
    # after it: neither a waiting nor a closed plan holds the decision.
    assert library.root.pending_field_effect_decision_counts() == ()
    assert _decision_plans(library.root, decision_id) == [(waiting_id, *_CLOSED)]
    assert all(kind != "automatic_recovery" for kind, _ in _signals(library.root))
    assert (waiting_id, "superseded") in lane.waiting_rows()
    assert (waiting_id, "stale") not in lane.waiting_rows()

    # The successor is another case, and nothing transfers to it.
    library.now[0] = 30
    library.drain()
    assert library.case_id() != case_before
    lane.reconnect()
    assert _decision_plans(library.root, decision_id) == [(waiting_id, *_CLOSED)]
    assert waiting_id not in lane.sent
    if origin == "owner":
        assert lane.sent == []
    _reopen_fields(library, tmp_path)


def test_leaving_automatic_closes_a_waiting_automatic_field_write(
    library: _Library, tmp_path: Path
) -> None:
    automatic = library.automatic()
    library.root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    lane = _FieldLane(library, tmp_path)
    library.now[0] = 13
    waiting_id = lane.wait(library, automatic.decision_id)
    assert _pending(library.root, automatic.decision_id)

    assert library.root.enter_shadow_preview(20, ("ebooks",)) == "preview"

    assert library.root.pending_field_effect_decision_counts() == ()
    assert _decision_plans(library.root, automatic.decision_id) == [
        (waiting_id, *_CLOSED)
    ]
    assert all(kind != "automatic_recovery" for kind, _ in _signals(library.root))
    # Preparation admits the same case again, and Confirm Automatic makes the
    # earlier generation's decision pending again; it still writes nothing.
    library.now[0] = 30
    library.drain()
    assert library.case_id() == automatic.review_case_id
    lane.reconnect()
    library.root.activate_qualified_automatic(40)
    library.now[0] = 41
    lane.reconnect()
    assert _decision_plans(library.root, automatic.decision_id) == [
        (waiting_id, *_CLOSED)
    ]
    assert lane.sent == []
    assert all(kind != "automatic_recovery" for kind, _ in _signals(library.root))
    _reopen_fields(library, tmp_path)


def test_leaving_automatic_keeps_a_waiting_owner_field_write(
    library: _Library, tmp_path: Path
) -> None:
    """The case returns as itself, so the owner's write still lands once."""
    decision_id = _owner_decision_id(library)
    lane = _FieldLane(library, tmp_path)
    library.now[0] = 13
    waiting_id = lane.wait(library, decision_id)

    assert library.root.enter_shadow_preview(20, ("ebooks",)) == "preview"

    assert _decision_plans(library.root, decision_id) == [
        (waiting_id, "stale", "connection")
    ]
    assert (waiting_id, "stale") in lane.waiting_rows()
    library.now[0] = 30
    library.drain()
    lane.reconnect()
    assert _decision_plans(library.root, decision_id) == [
        (waiting_id, "verified", None)
    ]
    assert lane.sent == [waiting_id]
    _reopen_fields(library, tmp_path)


def test_a_policy_change_leaves_a_ready_field_write_to_its_claim(
    library: _Library, tmp_path: Path
) -> None:
    """A ready plan is not rewritten; it goes stale at its claim (ADR-0039)."""
    decision_id = _owner_decision_id(library)
    lane = _FieldLane(library, tmp_path)
    library.now[0] = 13
    lane.scheduler.schedule()
    ((plan_id, state, failure),) = _decision_plans(library.root, decision_id)
    assert (state, failure) == ("planned", None)

    _changed_policy(library, 20)

    assert _decision_plans(library.root, decision_id) == [(plan_id, "planned", None)]
    library.now[0] = 30
    library.drain()
    lane.reconnect()
    assert _decision_plans(library.root, decision_id) == [
        (plan_id, "stale", "authority")
    ]
    assert lane.sent == []
    _reopen_fields(library, tmp_path)


def _retired_by_activation(repository: SQLiteMetadataReportRepository) -> list[object]:
    return repository._connection.execute(
        "SELECT lineage.review_case_id,lineage.outcome,retirement.actor "
        "FROM current_review_case_lineages lineage JOIN review_case_transitions "
        "retirement ON retirement.transition_id=lineage.transition_id"
    ).fetchall()


def test_a_policy_change_closes_a_field_write_whose_case_did_not_return(
    library: _Library, tmp_path: Path
) -> None:
    """Leaving Automatic keeps the owner's write, but preparation fails to
    bring its case back; the next activation closes it (#1657)."""
    decision_id = _owner_decision_id(library)
    lane = _FieldLane(library, tmp_path)
    library.now[0] = 13
    waiting_id = lane.wait(library, decision_id)
    propose = library.pipeline._propose

    def fail(*_: object) -> str:
        raise ReviewPipelineError("internal_failure")

    library.pipeline._propose = fail
    assert library.root.enter_shadow_preview(20, ("ebooks",)) == "preview"
    library.now[0] = 21
    library.drain()
    library.pipeline._propose = propose
    transition = library.root.metadata_policy_transition()
    assert transition is not None
    assert (transition.state, transition.failed) == ("completed_with_failures", 1)
    assert _retired_by_activation(library.root) == [
        (None, "retired", "policy-activation-v1")
    ]
    assert _decision_plans(library.root, decision_id) == [
        (waiting_id, "stale", "connection")
    ]

    _changed_policy(library, 25)

    assert _decision_plans(library.root, decision_id) == [(waiting_id, *_CLOSED)]
    assert library.root.pending_field_effect_decision_counts() == ()
    assert all(kind != "automatic_recovery" for kind, _ in _signals(library.root))
    assert (waiting_id, "superseded") in lane.waiting_rows()
    assert (waiting_id, "stale") not in lane.waiting_rows()
    library.now[0] = 30
    library.drain()
    lane.reconnect()
    assert _decision_plans(library.root, decision_id) == [(waiting_id, *_CLOSED)]
    assert lane.sent == []
    _reopen_fields(library, tmp_path)


def _cover_lane(cover: _CoverLane) -> ContinuousGuardedApplication:
    key = (cover.connection, cover.library)
    return ContinuousGuardedApplication(
        cover.repository,
        {key: cover.application},
        {key: cover.capabilities},
        PersistedFieldEffectAuthority(cover.repository),
        (key,),
        Clock(),
        lambda *_: None,
    )


def _activate_cover_policy(
    cover: _CoverLane, profile: MetadataPolicyProfile, *, reconcile: bool
) -> None:
    repository = cover.repository
    resolved = resolve_policy(profile, cover.library)
    active, generation = repository._connection.execute(
        "SELECT active_policy_id,generation FROM metadata_policy_state"
    ).fetchone()
    if not reconcile:
        repository.create_metadata_policy_draft(profile.canonical_json)
    repository.activate_metadata_policy(
        profile.policy_id,
        active,
        ((cover.library, resolved.resolved_id, resolved.canonical_json),),
        199 + generation,
        expected_active_generation=generation,
        reconcile_current=reconcile,
    )


def _all_ask() -> MetadataPolicyProfile:
    return MetadataPolicyProfile.create(
        "All ask",
        dict(DEFAULT_OPTIONS),
        {
            name: "unsupported" if name == "explicit" else "ask"
            for name in OPTION_CATALOGUE
        },
    )


def _cover_case(repository: SQLiteMetadataReportRepository) -> object:
    return repository._connection.execute(
        "SELECT review_case_id FROM current_review_case_lineages"
    ).fetchone()[0]


def test_a_policy_change_closes_a_waiting_cover_and_its_restore_point(
    cover: _CoverLane,
) -> None:
    waiting_id = _waiting_cover(cover)
    assert cover.repository.pending_field_effect_decision_counts()

    _activate_cover_policy(cover, _all_ask(), reconcile=False)

    assert cover.repository.pending_field_effect_decision_counts() == ()
    left = cover.repository.get_field_effect(waiting_id)
    assert (left.state, left.failure) == _CLOSED
    assert _cleanup(cover.repository, waiting_id) == [("pending",)]
    assert all(kind != "automatic_recovery" for kind, _ in _signals(cover.repository))
    lane = _cover_lane(cover)
    cover.capabilities.category = "allowed"
    for _ in range(4):
        lane.wake()
        lane._run_until_idle()
    assert _cleanup(cover.repository, waiting_id) == [("completed",)]
    assert not cover.recovery.exists(waiting_id)
    left = cover.repository.get_field_effect(waiting_id)
    assert (left.state, left.failure) == _CLOSED
    cover.reopen()


def _readmit(repository: SQLiteMetadataReportRepository) -> None:
    """Admit the retired case's proposal set again, as preparation does."""
    (proposal_set_id,) = repository._connection.execute(
        "SELECT proposal_set_id FROM review_case_transitions "
        "WHERE kind='admitted' ORDER BY checkpoint LIMIT 1"
    ).fetchone()
    proposal = ProposalSet.from_json(repository.get_proposal(proposal_set_id))
    assert json.loads(repository.admit_review_case(proposal))["status"] == "admitted"


@pytest.mark.parametrize("readmitted", [True, False])
def test_leaving_automatic_keeps_a_waiting_owner_cover(
    cover: _CoverLane, readmitted: bool
) -> None:
    """The same-policy reactivation retires the case; it is due back, so the
    owner's cover keeps waiting and its restore point stays."""
    waiting_id = _waiting_cover(cover)
    case_id = _cover_case(cover.repository)

    _activate_cover_policy(cover, MetadataPolicyProfile.builtin(), reconcile=True)

    left = cover.repository.get_field_effect(waiting_id)
    assert (left.state, left.failure) == ("stale", "connection")
    assert _cleanup(cover.repository, waiting_id) == []
    assert cover.recovery.exists(waiting_id)
    if readmitted:
        _readmit(cover.repository)
        assert _cover_case(cover.repository) == case_id
        lane = _cover_lane(cover)
        cover.capabilities.category = "allowed"
        for _ in range(4):
            lane.wake()
            lane._run_until_idle()
        written = cover.repository.get_field_effect(waiting_id)
        assert (written.state, written.failure) == ("verified", None)
        assert _cleanup(cover.repository, waiting_id) == [("completed",)]
        assert not cover.recovery.exists(waiting_id)
    cover.reopen()


@pytest.mark.parametrize("same_policy", [False, True], ids=["changed", "same_policy"])
def test_a_later_activation_closes_a_cover_whose_case_did_not_return(
    cover: _CoverLane, same_policy: bool
) -> None:
    """The case an earlier activation retired is never readmitted, so it can
    return only as a new episode; the next activation closes its cover (#1657)."""
    waiting_id = _waiting_cover(cover)
    _activate_cover_policy(cover, MetadataPolicyProfile.builtin(), reconcile=True)
    left = cover.repository.get_field_effect(waiting_id)
    assert (left.state, left.failure) == ("stale", "connection")

    if same_policy:
        _activate_cover_policy(cover, MetadataPolicyProfile.builtin(), reconcile=True)
    else:
        _activate_cover_policy(cover, _all_ask(), reconcile=False)

    left = cover.repository.get_field_effect(waiting_id)
    assert (left.state, left.failure) == _CLOSED
    assert _cleanup(cover.repository, waiting_id) == [("pending",)]
    lane = _cover_lane(cover)
    cover.capabilities.category = "allowed"
    for _ in range(4):
        lane.wake()
        lane._run_until_idle()
    assert _cleanup(cover.repository, waiting_id) == [("completed",)]
    assert not cover.recovery.exists(waiting_id)
    assert all(kind != "automatic_recovery" for kind, _ in _signals(cover.repository))
    cover.reopen()


def test_a_failed_activation_keeps_the_waiting_write(
    cover: _CoverLane, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The supersession is part of the activation: a fault undoes both."""
    waiting_id = _waiting_cover(cover)
    case_id = _cover_case(cover.repository)
    consents = cover.repository._connection.execute(
        "SELECT consent_id,current,document FROM field_effect_consents"
    ).fetchall()

    def fail(*_: object) -> None:
        raise sqlite3.OperationalError("synthetic")

    monkeypatch.setattr(
        cover.repository._field_effects, "_record_field_effect_event", fail
    )
    with pytest.raises(MetadataReportError, match="activation is unavailable"):
        _activate_cover_policy(cover, _all_ask(), reconcile=False)
    monkeypatch.undo()
    assert _cover_case(cover.repository) == case_id
    assert (
        cover.repository._connection.execute(
            "SELECT consent_id,current,document FROM field_effect_consents"
        ).fetchall()
        == consents
    )
    left = cover.repository.get_field_effect(waiting_id)
    assert (left.state, left.failure) == ("stale", "connection")
    assert _cleanup(cover.repository, waiting_id) == []
    assert cover.recovery.exists(waiting_id)
    cover.reopen()
