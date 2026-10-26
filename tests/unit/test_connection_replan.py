"""A write staled by a lost connection is planned again, within a bound (#1402).

A plan that could not proceed only because Audiobookshelf was unreachable does
not consume its decision: once the connection is observed healthy the decision
is planned again through every guard, at most three times. A write that landed
before its confirmation failed is recorded `verified` from the next pre-read,
and nothing here ever sends again a write whose outcome is unknown.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import GuardedApplicationError
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    FieldEffectError,
    FieldEffectOperation,
)
from mechanical_librarian.domain.human_decision import HumanDecisionError
from tests.support.automatic_decisions import _automatic_decisions
from tests.support.automatic_no_change import _automatic_no_change_inputs
from tests.support.field_effects import (
    _EffectEnvironment,
    _plan,
    _replan,
)
from tests.support.field_effects import (
    effects as effects,
)
from tests.support.genre_reports import genre_report, publish_genre_case
from tests.support.owner_values import OwnerStore


def _sent(effects: _EffectEnvironment) -> list[str]:
    """Count every request the fake library receives, sent or refused."""
    sent: list[str] = []
    apply = effects.target.apply

    def counted(plan):
        sent.append(plan.plan_id)
        return apply(plan)

    effects.target.apply = counted  # type: ignore[method-assign]  # reason: the fake's write is the seam counted here, so a re-plan is proven to have issued no second request.
    return sent


def _kinds(effects: _EffectEnvironment, plan_id: str) -> list[tuple[str, object]]:
    return [
        (event["kind"], event["failure"])
        for event in map(
            json.loads, effects.repository.field_effect_event_documents(plan_id)
        )
    ]


def test_connection_stale_is_planned_again_with_its_own_identity(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="1")
    plan_id = operation.plan.plan_id
    sent = _sent(effects)
    effects.capabilities.category = "connection_unavailable"
    stale = effects.service.apply(plan_id)
    assert (stale.state, stale.failure) == ("stale", "connection")
    effects.capabilities.category = "allowed"
    revived = _replan(effects, suffix="1")
    assert revived is not None
    assert (revived.plan.plan_id, revived.state, revived.attempt) == (
        plan_id,
        "planned",
        0,
    )
    assert revived.plan.created_at > operation.plan.created_at
    verified = effects.service.apply(plan_id)
    assert (verified.state, verified.failure, verified.attempt) == ("verified", None, 1)
    assert sent == [plan_id]
    assert _kinds(effects, plan_id) == [
        ("planned", None),
        ("stale", "connection"),
        ("planned", None),
        ("claimed", None),
        ("applying", None),
        ("verified", None),
    ]
    # The revival is a planning fact like any other: the store reopens over it.
    effects.repository.close()
    from mechanical_librarian.adapters.persistence.metadata_reports import (
        open_repository,
    )

    effects.repository = open_repository(effects.database_path, create=False)
    assert effects.repository.get_field_effect(plan_id).to_data() == verified.to_data()


def test_a_lost_connection_on_the_pre_read_is_a_connection_stale(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="1")
    effects.target.unavailable_scope_next = "connection"
    stale = effects.service.apply(operation.plan.plan_id)
    assert (stale.state, stale.failure) == ("stale", "connection")
    revived = _replan(effects, suffix="1")
    assert revived is not None and revived.state == "planned"
    assert effects.service.apply(operation.plan.plan_id).state == "verified"


@pytest.mark.parametrize(
    "refusal",
    ("field_unavailable", "digest", "destination_read", "authority", "cover"),
)
def test_every_other_stale_stays_terminal_and_is_never_planned_again(
    effects: _EffectEnvironment, refusal: str
) -> None:
    if refusal == "cover":
        operation = _plan(
            effects,
            destination="cover",
            pre=None,
            desired="cover_" + "a" * 64,
            suffix="1",
        )
        effects.capabilities.category = "connection_unavailable"
    else:
        operation = _plan(effects, suffix="1")
    if refusal == "field_unavailable":
        effects.capabilities.category = "field_unavailable"
    elif refusal == "digest":
        effects.capabilities.digest = "e" * 64
    elif refusal == "destination_read":
        effects.target.unavailable_scope_next = "destination"
    elif refusal == "authority":
        effects.authority.revoked.add(operation.plan.authority.decision_id)
    stale = effects.service.apply(operation.plan.plan_id)
    expected = "authority" if refusal == "authority" else "capability"
    assert (stale.state, stale.failure) == ("stale", expected)
    events = _kinds(effects, operation.plan.plan_id)
    effects.capabilities.category = "allowed"
    effects.capabilities.digest = "c" * 64
    effects.authority.revoked.clear()
    if refusal == "cover":
        again = _replan(
            effects,
            destination="cover",
            pre=None,
            desired="cover_" + "a" * 64,
            suffix="1",
        )
    else:
        again = _replan(effects, suffix="1")
    assert again is not None
    assert (again.state, again.failure) == ("stale", expected)
    assert _kinds(effects, operation.plan.plan_id) == events
    # Beside it, a lost connection alone is the stale that releases.
    sibling = _plan(effects, destination="publisher", suffix="2")
    effects.capabilities.category = "connection_unavailable"
    assert effects.service.apply(sibling.plan.plan_id).failure == "connection"
    effects.capabilities.category = "allowed"
    revived = _replan(effects, destination="publisher", suffix="2")
    assert revived is not None and revived.state == "planned"


@pytest.mark.parametrize("guard", ("permission", "digest", "consent", "paused"))
def test_a_replanned_connection_stale_still_meets_every_guard(
    effects: _EffectEnvironment, guard: str
) -> None:
    operation = _plan(effects, suffix="1")
    plan_id = operation.plan.plan_id
    sent = _sent(effects)
    effects.capabilities.category = "connection_unavailable"
    assert effects.service.apply(plan_id).failure == "connection"
    effects.capabilities.category = "allowed"
    revived = _replan(effects, suffix="1")
    assert revived is not None and revived.state == "planned"
    # The connection is back, but the field is no longer writable as planned.
    if guard == "permission":
        effects.capabilities.category = "insufficient_permission"
    elif guard == "digest":
        effects.capabilities.digest = "e" * 64
    elif guard == "consent":
        effects.consent.revoked.add(operation.plan.consent.consent_id)
    else:
        effects.service.set_paused(True)
        with pytest.raises(GuardedApplicationError, match="paused"):
            effects.service.apply(plan_id)
        assert effects.service.status(plan_id).state == "planned"
        assert sent == []
        return
    refused = effects.service.apply(plan_id)
    expected = "consent" if guard == "consent" else "capability"
    assert (refused.state, refused.failure) == ("stale", expected)
    assert sent == []
    assert effects.target.read(operation.plan).value == "old"
    effects.capabilities.category = "allowed"
    effects.capabilities.digest = "c" * 64
    effects.consent.revoked.clear()
    assert _replan(effects, suffix="1").state == "stale"  # type: ignore[union-attr]


@pytest.mark.parametrize("path", ("direct", "after_outage"))
def test_a_landed_write_whose_read_back_failed_is_verified_without_a_second_write(
    effects: _EffectEnvironment, path: str
) -> None:
    operation = _plan(effects, suffix="1")
    plan_id = operation.plan.plan_id
    sent = _sent(effects)
    effects.target.fail_read_after_apply_for.add(plan_id)
    assert effects.service.apply(plan_id).state == "reconciling"
    unknown = effects.service.reconcile(plan_id)
    assert (unknown.state, unknown.failure) == ("retryable", "target_failure")
    effects.target.fail_reads_for.clear()
    if path == "after_outage":
        # An outage meets the unresolved plan first. Planning it again restores
        # the unresolved state, so the landed write is still recognised.
        effects.capabilities.category = "connection_unavailable"
        assert effects.service.apply(plan_id).failure == "connection"
        effects.capabilities.category = "allowed"
        revived = _replan(effects, suffix="1")
        assert revived is not None
        assert (revived.state, revived.failure, revived.attempt) == (
            "retryable",
            "target_failure",
            1,
        )
        assert revived.claim_token == unknown.claim_token
    verified = effects.service.apply(plan_id)
    assert (verified.state, verified.failure, verified.attempt) == ("verified", None, 1)
    assert sent == [plan_id]
    assert _kinds(effects, plan_id)[-2:] == [
        ("retryable", "target_failure"),
        ("verified", None),
    ]


@pytest.mark.parametrize("later", ("third_value", "read_lost"))
def test_an_unknown_outcome_is_never_sent_again(
    effects: _EffectEnvironment, later: str
) -> None:
    operation = _plan(effects, suffix="1")
    plan_id = operation.plan.plan_id
    sent = _sent(effects)
    effects.target.fail_read_after_apply_for.add(plan_id)
    effects.service.apply(plan_id)
    assert effects.service.reconcile(plan_id).state == "retryable"
    effects.target.fail_reads_for.clear()
    if later == "third_value":
        effects.target.drift(operation.plan, "third")
        result = effects.service.apply(plan_id)
        assert (result.state, result.failure) == ("stale", "target_changed")
    else:
        effects.target.unavailable_scope_next = "connection"
        result = effects.service.apply(plan_id)
        assert (result.state, result.failure) == ("stale", "connection")
        revived = _replan(effects, suffix="1")
        assert revived is not None and revived.state == "retryable"
        effects.target.drift(operation.plan, "third")
        assert effects.service.apply(plan_id).failure == "target_changed"
    assert sent == [plan_id]


def test_a_revived_unsent_retry_takes_the_unrevived_guarded_path(
    effects: _EffectEnvironment,
) -> None:
    def unsent_retry(suffix: str) -> FieldEffectOperation:
        operation = _plan(effects, destination="publisher", suffix=suffix)
        effects.target.fail_next = True
        retry = effects.service.apply(operation.plan.plan_id)
        assert (retry.state, retry.failure) == ("retryable", "target_failure")
        return retry

    plain = unsent_retry("1")
    assert effects.service.apply(plain.plan.plan_id).state == "verified"
    revived_retry = unsent_retry("2")
    effects.capabilities.category = "connection_unavailable"
    assert effects.service.apply(revived_retry.plan.plan_id).failure == "connection"
    effects.capabilities.category = "allowed"
    revived = _replan(effects, destination="publisher", suffix="2")
    assert revived is not None
    assert (revived.state, revived.failure, revived.attempt, revived.claim_token) == (
        "retryable",
        "target_failure",
        revived_retry.attempt,
        revived_retry.claim_token,
    )
    finished = effects.service.apply(revived_retry.plan.plan_id)
    assert (finished.state, finished.attempt) == ("verified", 2)
    plain_events = _kinds(effects, plain.plan.plan_id)
    revived_events = _kinds(effects, revived_retry.plan.plan_id)
    # The same claim, request and read-back follow the restored retry.
    assert revived_events == [
        *plain_events[:4],
        ("stale", "connection"),
        ("retryable", "target_failure"),
        *plain_events[4:],
    ]


def test_retryable_reaches_verified_only_from_an_unknown_outcome(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="1")
    unknown = FieldEffectOperation(
        operation.plan, "retryable", 1, 5, "target_failure", "1" * 64
    )
    assert unknown.transition("verified", at=6).state == "verified"
    for failure in ("not_sent", "not_applied", None):
        with pytest.raises(FieldEffectError, match="invalid_transition"):
            FieldEffectOperation(
                operation.plan, "retryable", 1, 5, failure, "1" * 64
            ).transition("verified", at=6)
    with pytest.raises(FieldEffectError, match="invalid_transition"):
        FieldEffectOperation(operation.plan, updated_at=5).transition("verified", at=6)


@pytest.fixture
def owner(tmp_path: Path) -> Iterator[OwnerStore]:
    store = OwnerStore(tmp_path, None)
    try:
        yield store
    finally:
        store.close()


def test_a_superseded_stranded_claim_stales_at_preclaim_in_production(
    owner: OwnerStore,
) -> None:
    case_id, _ = publish_genre_case(owner.repository, genre_report())
    owner.load(case_id)
    automatic = next(
        decision
        for raw in owner.repository.get_review_case_decisions(case_id)
        if (decision := AutomaticDecision.from_json(raw)).destination == "genres"
    )
    operation = owner.plan(automatic)
    plan_id = operation.plan.plan_id
    owner.repository.claim_field_effect(plan_id, "planned", "1" * 64, owner.clock())
    # While the claim stands the owner cannot replace the automatic choice;
    # superseding its authority leaves the claim it cannot interrupt in place.
    with pytest.raises(HumanDecisionError, match="already_decided"):
        owner.decide(
            "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
        )
    assert owner.service.supersede(automatic.decision_id) == 0
    assert owner.service.recover_startup() == (1, 0, 0)
    returned = owner.service.status(plan_id)
    assert (returned.state, returned.failure) == ("retryable", "not_sent")
    stale = owner.service.apply(plan_id)
    assert (stale.state, stale.failure) == ("stale", "authority")
    assert owner.patches == []


def test_an_outage_across_passes_then_recovery_verifies_each_write_once(
    effects: _EffectEnvironment,
) -> None:
    report, category, option, _ = _automatic_no_change_inputs("genres_fill_empty")
    decisions = [
        value
        for value in _automatic_decisions(
            effects,
            report,
            category,
            option,
            qualified=True,
            also=(("description_fill", "safe_plain_text"), ("tags", "fill_empty")),
        )
        if value.outcome == "candidate"
    ]
    assert sorted(value.destination for value in decisions) == ["description", "tags"]
    authority = PersistedFieldEffectAuthority(effects.repository)
    effects.repository.grant_field_effect_consents(
        "primary", ("ebooks",), ("description", "tags"), 5
    )
    for decision in decisions:
        snapshot = authority.resolve(decision)
        key = (
            snapshot.connection,
            snapshot.library,
            snapshot.work_id,
            snapshot.destination,
        )
        effects.target._values[key] = snapshot.pre_value
        effects.target._versions[key] = snapshot.target_version
    service = effects.persisted_service(effects.repository, effects.target, authority)
    sent = _sent(effects)
    lane = ContinuousGuardedApplication(
        effects.repository,
        {("primary", "ebooks"): service},
        {("primary", "ebooks"): effects.capabilities},
        authority,
        (("primary", "ebooks"),),
        effects.clock,
        lambda *_: None,
    )
    assert lane.schedule() == 2
    plan_ids = [
        row[0]
        for row in effects.repository._connection.execute(
            "SELECT plan_id FROM field_effects ORDER BY plan_id"
        )
    ]
    effects.capabilities.category = "connection_unavailable"
    for _ in range(4):
        lane._run_until_idle()
    assert [
        (
            effects.repository.get_field_effect(plan_id).state,
            effects.repository.get_field_effect(plan_id).failure,
        )
        for plan_id in plan_ids
    ] == [("stale", "connection")] * 2
    assert sent == []
    # Nothing but the poll's own pass observes the recovery.
    effects.capabilities.category = "allowed"
    lane._run_until_idle()
    assert [
        effects.repository.get_field_effect(plan_id).state for plan_id in plan_ids
    ] == ["verified"] * 2
    assert sorted(sent) == plan_ids
    for plan_id in plan_ids:
        assert [kind for kind, _ in _kinds(effects, plan_id)] == [
            "planned",
            "stale",
            "planned",
            "claimed",
            "applying",
            "verified",
        ]
    lane._run_until_idle()
    assert sorted(sent) == plan_ids
    assert effects.repository.pending_field_effect_decision_documents(100) == ()
