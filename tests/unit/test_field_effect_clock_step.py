"""A wall clock stepped back mid-write does not strand the plan (#1386).

An NTP correction or a VM resume can move `time.time()` backwards between two
steps of one guarded write. Each transition keeps the later of its own time
and the plan's, so the write still completes, its history stays in sequence
order, and the store reopens. Replay is refused by state, never by time.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    FieldEffectError,
    FieldEffectOperation,
    FieldEffectPlan,
)
from tests.support.field_effects import (
    CAPABILITY,
    DEPENDENCY,
    _EffectEnvironment,
    _plan,
    _replan,
)
from tests.support.field_effects import effects as effects


def _events(effects: _EffectEnvironment, plan_id: str) -> list[tuple[str, int]]:
    return [
        (event["kind"], event["occurred_at"])
        for event in map(
            json.loads, effects.repository.field_effect_event_documents(plan_id)
        )
    ]


def _recorded(effects: _EffectEnvironment, created_at: int) -> FieldEffectOperation:
    """Record one automatic title plan, which a withdrawal may re-plan."""
    (consent,) = effects.repository.grant_field_effect_consents(
        "primary", ("books",), ("title",), 9
    )
    plan = FieldEffectPlan.create(
        connection="primary",
        library="books",
        work_id="book-1",
        destination="title",
        pre_value="old",
        desired_value="new",
        target_fingerprint="c" * 64,
        target_version="version-1",
        dependency_digests=(DEPENDENCY,),
        authority=EffectAuthority(
            "automatic_decision_" + "a" * 64,
            "review_case_" + "b" * 64,
            "c" * 64,
            "fake_service",
            "automatic_policy",
            "candidate",
            "policy-1",
            "d" * 64,
        ),
        consent=consent,
        capability_digest=CAPABILITY,
        created_at=created_at,
    )
    return effects.repository.record_field_effect(
        FieldEffectOperation(plan, updated_at=created_at)
    )


def test_a_transition_earlier_than_the_plan_keeps_the_later_time(
    effects: _EffectEnvironment,
) -> None:
    plan = _plan(effects, suffix="1").plan
    operation = FieldEffectOperation(plan, updated_at=100)

    claimed = operation.transition("claimed", at=40, claim_token="a" * 64)
    applying = claimed.transition("applying", at=100)
    verified = applying.transition("verified", at=101)

    assert (claimed.state, claimed.attempt, claimed.updated_at) == ("claimed", 1, 100)
    assert (applying.state, applying.updated_at) == ("applying", 100)
    assert (verified.state, verified.updated_at) == ("verified", 101)
    for at in (-1, True, 99.0, "99", None):
        with pytest.raises(FieldEffectError, match="invalid_transition"):
            claimed.transition("applying", at=at)  # type: ignore[arg-type]


def test_a_replayed_transition_is_refused_at_an_earlier_time(
    effects: _EffectEnvironment,
) -> None:
    plan: FieldEffectPlan = _plan(effects, suffix="2").plan
    verified = FieldEffectOperation(
        plan, "applying", 1, 100, None, "a" * 64
    ).transition("verified", at=50)
    assert verified.updated_at == 100
    for state in ("applying", "verified", "claimed"):
        with pytest.raises(FieldEffectError, match="invalid_transition"):
            verified.transition(state, at=10)

    claimed = effects.repository.claim_field_effect(
        plan.plan_id, "planned", "b" * 64, 1_000
    )
    effects.repository.transition_field_effect(
        plan.plan_id, claimed.state, "applying", 900
    )
    before = _events(effects, plan.plan_id)
    with pytest.raises(FieldEffectError, match="transition_conflict"):
        effects.repository.transition_field_effect(
            plan.plan_id, "claimed", "applying", 800
        )
    with pytest.raises(FieldEffectError, match="claim_conflict"):
        effects.repository.claim_field_effect(plan.plan_id, "planned", "b" * 64, 800)
    assert effects.service.status(plan.plan_id).state == "applying"
    assert _events(effects, plan.plan_id) == before


def test_a_write_completes_across_backward_clock_steps_and_reopens(
    effects: _EffectEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    operation = _plan(effects, suffix="3")
    plan_id = operation.plan.plan_id
    claim = effects.repository.claim_field_effect

    def claim_then_step_back(*arguments: object) -> FieldEffectOperation:
        claimed = claim(*arguments)  # type: ignore[arg-type]
        effects.clock.value = 500
        return claimed

    def step_back_again(_: object) -> None:
        effects.clock.value = 200

    effects.claim_hook.callback = lambda _: setattr(effects.clock, "value", 1_000)
    monkeypatch.setattr(effects.repository, "claim_field_effect", claim_then_step_back)
    effects.target.before_apply = step_back_again

    finished = effects.service.apply(plan_id)

    assert (finished.state, finished.attempt, finished.updated_at) == (
        "verified",
        1,
        1_001,
    )
    events = _events(effects, plan_id)
    assert events == [
        ("planned", operation.updated_at),
        ("claimed", 1_001),
        ("applying", 1_001),
        ("verified", 1_001),
    ]
    effects.repository.close()
    effects.repository = open_repository(
        Path(effects.temporary.name) / "effects.sqlite", create=False, cross_thread=True
    )
    effects.service._repository = effects.repository
    assert effects.service.status(plan_id).updated_at == 1_001
    assert _events(effects, plan_id) == events


def test_a_write_losing_its_connection_after_a_later_suspension_lands_retryable(
    effects: _EffectEnvironment,
) -> None:
    plan_id = _plan(effects, suffix="4").plan.plan_id

    def lose_connection_late(plan: FieldEffectPlan) -> None:
        # Another write recorded the loss first, before the clock stepped back.
        effects.repository.suspend_field_effect_capability(
            scope="connection",
            connection=plan.connection,
            destination=plan.destination,
            reason="connection_unavailable",
            changed_at=1_000,
        )
        effects.clock.value = 200
        effects.target.unavailable_scope_next = "connection"
        effects.target.unavailable_reason_next = "connection_unavailable"

    effects.target.before_apply = lose_connection_late

    finished = effects.service.apply(plan_id)

    assert (finished.state, finished.failure) == ("retryable", "target_failure")
    assert effects.repository._connection.execute(
        "SELECT reason,updated_at FROM field_effect_suspensions"
    ).fetchall() == [("connection_unavailable", 1_000)]


@pytest.mark.parametrize("withdrawn", ("authority", "consent"))
def test_a_withdrawal_after_a_backward_step_keeps_the_plans_time(
    effects: _EffectEnvironment, withdrawn: str
) -> None:
    plan = _recorded(effects, 1_000).plan
    if withdrawn == "authority":
        effects.repository.revoke_field_effect_authority(
            plan.authority.decision_id, 300
        )
    else:
        effects.repository.revoke_field_effect_consent(plan.consent.consent_id, 300)
    assert _events(effects, plan.plan_id) == [
        ("planned", 1_000),
        ("superseded", 1_000),
    ]
    if withdrawn == "consent":
        # Planned again from a clock still behind, it is not dated before
        # its own history.
        replanned = effects.repository.record_field_effect(
            FieldEffectOperation(replace(plan, created_at=300), updated_at=300)
        )
        assert (replanned.state, replanned.updated_at) == ("planned", 1_000)
        assert _events(effects, plan.plan_id)[-1] == ("planned", 1_000)


@pytest.mark.parametrize(
    ("step", "expected"),
    (
        ("reconciling", [("reconciling", 1_002)]),
        ("completion", [("reconciling", 1_003), ("verified", 1_003)]),
        ("reclaim", [("retryable", 1_003), ("claimed", 1_003)]),
    ),
)
def test_a_step_after_a_backward_step_keeps_the_plans_time(
    effects: _EffectEnvironment, step: str, expected: list[tuple[str, int]]
) -> None:
    plan_id = _recorded(effects, 1_000).plan.plan_id
    repository = effects.repository
    repository.claim_field_effect(plan_id, "planned", "a" * 64, 1_001)
    repository.transition_field_effect(plan_id, "claimed", "applying", 1_002)
    if step == "reconciling":
        repository.schedule_field_effect_reconciliation(plan_id, "applying", 300)
    elif step == "completion":
        repository.schedule_field_effect_reconciliation(plan_id, "applying", 1_003)
        repository.claim_next_field_effect_action(1_003, plan_id)
        repository.complete_field_effect_reconciliation(plan_id, "verified", None, 300)
    else:
        repository.transition_field_effect(
            plan_id, "applying", "retryable", 1_003, "target_failure"
        )
        repository.claim_field_effect(plan_id, "retryable", "b" * 64, 300)
    assert _events(effects, plan_id)[3:] == expected


def test_a_connection_revival_after_a_backward_step_keeps_the_plans_time(
    effects: _EffectEnvironment,
) -> None:
    effects.clock.value = 1_000
    plan_id = _plan(effects, suffix="5").plan.plan_id
    effects.capabilities.category = "connection_unavailable"
    stale = effects.service.apply(plan_id)
    assert (stale.state, stale.failure) == ("stale", "connection")
    effects.capabilities.category = "allowed"
    effects.clock.value = 300
    revived = _replan(effects, suffix="5")
    assert revived is not None
    assert (revived.state, revived.updated_at) == ("planned", stale.updated_at)
    assert _events(effects, plan_id)[-1] == ("planned", stale.updated_at)
