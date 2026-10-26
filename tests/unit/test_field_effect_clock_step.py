"""A wall clock stepped back mid-write does not strand the plan (#1386).

An NTP correction or a VM resume can move `time.time()` backwards between two
steps of one guarded write. Each transition keeps the later of its own time
and the plan's, so the write still completes, its history stays in sequence
order, and the store reopens. Replay is refused by state, never by time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.domain.field_effect import (
    FieldEffectError,
    FieldEffectOperation,
    FieldEffectPlan,
)
from tests.support.field_effects import _EffectEnvironment, _plan
from tests.support.field_effects import effects as effects


def _events(effects: _EffectEnvironment, plan_id: str) -> list[tuple[str, int]]:
    return [
        (event["kind"], event["occurred_at"])
        for event in map(
            json.loads, effects.repository.field_effect_event_documents(plan_id)
        )
    ]


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
