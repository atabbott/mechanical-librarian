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
from typing import cast

import httpx
import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.library_services.audiobookshelf_field_effect import (
    AudiobookshelfFieldEffectCapabilities,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import (
    CapabilityObservation,
    GuardedApplicationError,
    GuardedFieldApplication,
)
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    FieldEffectError,
    FieldEffectOperation,
    FieldEffectPlan,
)
from mechanical_librarian.domain.human_decision import HumanDecisionError
from tests.support.automatic_decisions import _automatic_decisions
from tests.support.automatic_no_change import _automatic_no_change_inputs
from tests.support.field_effects import (
    CAPABILITY,
    DEPENDENCY,
    _EffectEnvironment,
    _plan,
    _replan,
)
from tests.support.field_effects import (
    effects as effects,
)
from tests.support.genre_reports import genre_report, publish_genre_case
from tests.support.owner_values import OwnerStore
from tests.unit.test_activity_end_to_end import _facts, _ledger


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


@pytest.mark.parametrize("superseded", ("during_claim", "after_recovery"))
def test_a_superseded_stranded_claim_sends_nothing_in_production(
    owner: OwnerStore, superseded: str
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
    if superseded == "during_claim":
        assert owner.service.supersede(automatic.decision_id) == 0
    assert owner.service.recover_startup() == (1, 0, 0)
    returned = owner.service.status(plan_id)
    assert (returned.state, returned.failure) == ("retryable", "not_sent")
    if superseded == "during_claim":
        closed = owner.service.apply(plan_id)
        assert (closed.state, closed.failure) == ("stale", "authority")
    else:
        # Once the claim is returned the owner's decision supersedes it.
        owner.decide(
            "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
        )
        closed = owner.service.apply(plan_id)
        assert (closed.state, closed.failure) == ("superseded", "authority_superseded")
    assert owner.patches == []


def _description_and_tags_lane(
    effects: _EffectEnvironment,
) -> tuple[list[AutomaticDecision], ContinuousGuardedApplication]:
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
    return decisions, lane


def test_an_outage_across_passes_then_recovery_verifies_each_write_once(
    effects: _EffectEnvironment,
) -> None:
    _, lane = _description_and_tags_lane(effects)
    sent = _sent(effects)
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


@pytest.mark.parametrize("status", (500, 503))
def test_a_server_failing_every_item_keeps_each_waiting_decision_waiting(
    effects: _EffectEnvironment, status: int
) -> None:
    """A server that answers its identity check but fails every item request
    has answered for no book, so no waiting decision spends its bound (#1402)."""
    decisions, lane = _description_and_tags_lane(effects)
    sent = _sent(effects)
    effects.capabilities.category = "connection_unavailable"
    lane._run_until_idle()
    user = {
        "isActive": True,
        "permissions": {"update": True, "upload": True, "accessAllLibraries": True},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/me":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, json=user
            )
        return httpx.Response(status)

    service_side = AudiobookshelfFieldEffectCapabilities(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
    )

    def observe(plan: FieldEffectPlan) -> CapabilityObservation:
        # The real adapter decides whether the connection was reached; the
        # fake keeps the digest these plans were made against.
        answered = service_side.result(plan)
        return CapabilityObservation(
            answered.category, effects.capabilities.digest, answered.connection_reached
        )

    effects.capabilities.observe = observe  # type: ignore[method-assign]  # reason: the observer is the failing service under test.
    for _ in range(8):
        lane._run_until_idle()
    assert sorted(
        (operation.plan.destination, operation.state, operation.failure)
        for operation in map(
            effects.repository.get_field_effect,
            (
                row[0]
                for row in effects.repository._connection.execute(
                    "SELECT plan_id FROM field_effects"
                )
            ),
        )
    ) == [("description", "stale", "connection"), ("tags", "stale", "connection")]
    assert [
        effects.repository.field_effect_connection_stales(decision.decision_id)
        for decision in decisions
    ] == [1, 1]
    assert sent == []


def _automatic_tags_lane(
    effects: _EffectEnvironment,
) -> tuple[
    AutomaticDecision,
    GuardedFieldApplication,
    FieldEffectOperation,
    ContinuousGuardedApplication,
]:
    report, category, option, _ = _automatic_no_change_inputs("genres_fill_empty")
    (decision,) = (
        value
        for value in _automatic_decisions(
            effects,
            report,
            category,
            option,
            qualified=True,
            also=(("tags", "fill_empty"),),
        )
        if value.outcome == "candidate"
    )
    authority = PersistedFieldEffectAuthority(effects.repository)
    effects.repository.grant_field_effect_consents("primary", ("ebooks",), ("tags",), 5)
    snapshot = authority.resolve(decision)
    key = (snapshot.connection, snapshot.library, snapshot.work_id, "tags")
    effects.target._values[key] = snapshot.pre_value
    effects.target._versions[key] = snapshot.target_version
    service = effects.persisted_service(effects.repository, effects.target, authority)
    lane = ContinuousGuardedApplication(
        effects.repository,
        {("primary", "ebooks"): service},
        {("primary", "ebooks"): effects.capabilities},
        authority,
        (("primary", "ebooks"),),
        effects.clock,
        lambda *_: None,
    )
    assert lane.schedule() == 1
    ((document,),) = effects.repository._connection.execute(
        "SELECT document FROM field_effects"
    ).fetchall()
    return decision, service, FieldEffectOperation.from_json(document), lane


def _restore_authority(effects: _EffectEnvironment, decision_id: str) -> None:
    """Stand for the owner returning the field to automatic, which makes the
    automatic authority current again."""
    connection = effects.repository._connection
    (document,) = connection.execute(
        "SELECT document FROM field_effect_authorities WHERE decision_id=?",
        (decision_id,),
    ).fetchone()
    raw = json.loads(document)
    raw["current"] = True
    connection.execute(
        "UPDATE field_effect_authorities SET current=1,document=? WHERE decision_id=?",
        (json.dumps(raw, sort_keys=True, separators=(",", ":")), decision_id),
    )
    connection.commit()


@pytest.mark.parametrize("withdrawn", ("authority", "consent"))
@pytest.mark.parametrize("history", ("sent", "unsent"))
def test_superseding_a_retry_sends_nothing_and_never_revives_a_sent_request(
    effects: _EffectEnvironment, history: str, withdrawn: str
) -> None:
    decision, service, operation, _ = _automatic_tags_lane(effects)
    plan_id = operation.plan.plan_id
    sent = _sent(effects)
    if history == "sent":
        effects.target.fail_next = True
        retry = service.apply(plan_id)
        assert (retry.state, retry.failure) == ("retryable", "target_failure")
    else:
        effects.repository.claim_field_effect(
            plan_id, "planned", "1" * 64, effects.clock()
        )
        retry = service.apply(plan_id)
        assert (retry.state, retry.failure) == ("retryable", "not_sent")
    before = list(sent)
    if withdrawn == "authority":
        assert service.supersede(decision.decision_id) == 1
    else:
        assert service.revoke_consent(operation.plan.consent.consent_id) == 1
    closed = effects.repository.get_field_effect(plan_id)
    assert (closed.state, closed.failure) == (
        "superseded",
        "authority_superseded" if withdrawn == "authority" else "consent_revoked",
    )
    row = next(event for event in _ledger(effects) if event["state"] == "superseded")
    # Only a request that left can be unconfirmed; one that never did says so.
    assert _facts(row)["Confirmed in Audiobookshelf"] == (
        "The selected change was not confirmed"
        if history == "sent"
        else "Nothing was sent to Audiobookshelf"
    )
    if withdrawn == "authority":
        _restore_authority(effects, decision.decision_id)
    again = effects.repository.record_field_effect(
        FieldEffectOperation(operation.plan, updated_at=operation.plan.created_at)
    )
    # A request that may have landed is never planned afresh; one that was
    # never sent is planned again like any other superseded plan.
    assert again.state == ("superseded" if history == "sent" else "planned")
    if history == "sent":
        assert service.apply(plan_id).state == "superseded"
    assert sent == before


def test_superseding_a_retry_is_only_a_withdrawal(
    effects: _EffectEnvironment,
) -> None:
    operation = _plan(effects, suffix="1")
    retry = FieldEffectOperation(
        operation.plan, "retryable", 1, 5, "target_failure", "1" * 64
    )
    waiting = FieldEffectOperation(
        operation.plan, "stale", 1, 5, "connection", "1" * 64
    )
    for current, allowed in (
        (retry, ("authority_superseded", "consent_revoked")),
        (waiting, ("authority_superseded", "consent_revoked")),
    ):
        for failure in ("authority_superseded", "consent_revoked", "owner_abandoned"):
            if failure in allowed:
                assert current.transition(
                    "superseded", at=6, failure=failure
                ).state == ("superseded")
            else:
                with pytest.raises(FieldEffectError, match="invalid_transition"):
                    current.transition("superseded", at=6, failure=failure)
    for failure in ("connection_exhausted", "capability", "authority"):
        with pytest.raises(FieldEffectError, match="invalid_transition"):
            FieldEffectOperation(
                operation.plan, "stale", 1, 5, failure, "1" * 64
            ).transition("superseded", at=6, failure="authority_superseded")


@pytest.mark.parametrize("cleanup", ("completed", "pending"))
def test_a_revived_superseded_cover_reopens_cleanly(
    effects: _EffectEnvironment, cleanup: str
) -> None:
    consent = effects.repository.grant_field_effect_consents(
        "primary", ("books",), ("cover",), 9
    )[0]
    plan = FieldEffectPlan.create(
        connection="primary",
        library="books",
        work_id="book-1",
        destination="cover",
        pre_value="cover_" + "0" * 64,
        desired_value="cover_" + "1" * 64,
        target_fingerprint="c" * 64,
        target_version="version-1",
        dependency_digests=(DEPENDENCY,),
        authority=EffectAuthority(
            "automatic_decision_" + "a" * 64,
            "review_case_" + "b" * 64,
            "c" * 64,
            "persisted_review_case",
            "automatic_policy",
            "candidate",
            "policy-1",
            "d" * 64,
        ),
        consent=consent,
        capability_digest=CAPABILITY,
        created_at=9,
    )
    operation = FieldEffectOperation(plan, updated_at=9)
    effects.repository.record_field_effect(operation)
    effects.recovery.fail_remove_next = cleanup == "pending"
    # Revoking consent supersedes the plan and schedules its cleanup while
    # its automatic authority stays current.
    assert effects.service.revoke_consent(consent.consent_id) == 1
    assert effects.repository.field_effect_cleanup_pending(plan.plan_id) is (
        cleanup == "pending"
    )
    # Recovery bytes not yet removed keep the plan where it is; once they are
    # gone the re-plan retires the discharged cleanup with the terminal state.
    expected = "superseded" if cleanup == "pending" else "planned"
    assert effects.repository.record_field_effect(operation).state == expected
    if cleanup == "pending":
        assert effects.service.recover_pending() == (0, 1)
        assert effects.repository.record_field_effect(operation).state == "planned"
    assert effects.repository._connection.execute(
        "SELECT count(*) FROM field_effect_cleanups"
    ).fetchone() == (0,)
    effects.repository.close()
    effects.repository = open_repository(
        effects.database_path, create=False, cross_thread=True
    )
    assert effects.repository.get_field_effect(plan.plan_id).state == "planned"


@pytest.mark.parametrize(
    ("answer", "failure", "connection_stales"),
    (
        ("item_gone", "connection_exhausted", 3),
        ("insufficient_permission", "capability", 1),
        ("field_unavailable", "capability", 1),
        ("key_rejected", "connection", 1),
    ),
)
def test_a_lone_waiting_plan_ends_once_the_service_answers(
    effects: _EffectEnvironment, answer: str, failure: str, connection_stales: int
) -> None:
    """Only an observation that reached the connection ends the wait (#1402).

    A deleted item answers, so it uses its bounded preparations and stops; a
    withdrawn permission or field answers and stops through its own code. A
    rejected key, an unusable account or a lost transport does not reach the
    connection, so the plan waits with
    its bound untouched and the owner keeps the decision.
    """
    decision, _, operation, lane = _automatic_tags_lane(effects)
    plan_id = operation.plan.plan_id
    sent = _sent(effects)
    observed: list[str] = []
    observe = effects.capabilities.observe

    def counted(plan):
        observed.append(plan.destination)
        return observe(plan)

    effects.capabilities.observe = counted  # type: ignore[method-assign]  # reason: the observer is the probe traffic counted here.
    effects.capabilities.category = "connection_unavailable"
    lane._run_until_idle()
    assert effects.repository.get_field_effect(plan_id).failure == "connection"
    if answer in {"item_gone", "key_rejected"}:
        effects.capabilities.connection_reached = answer == "item_gone"
    else:
        effects.capabilities.category = answer  # type: ignore[assignment]  # reason: the parametrized answer is one of the observer's closed categories.
    for _ in range(8):
        lane._run_until_idle()
    final = effects.repository.get_field_effect(plan_id)
    assert (final.state, final.failure) == ("stale", failure)
    assert (
        effects.repository.field_effect_connection_stales(decision.decision_id)
        == connection_stales
    )
    traffic = len(observed)
    for _ in range(3):
        lane._run_until_idle()
    # A stopped plan is never observed again; a waiting one keeps its probe.
    assert len(observed) - traffic == (3 if answer == "key_rejected" else 0)
    assert sent == []
    if answer == "key_rejected":
        row = next(event for event in _ledger(effects) if event["state"] == "stale")
        assert _facts(row)["What happens next"].endswith(
            "Not prepared again yet; up to 3 times."
        )


@pytest.mark.parametrize(
    ("failure", "signal"),
    (
        ("connection", ("automatic_recovery", "in_progress")),
        (
            "connection_exhausted",
            ("service_incompatibility", "service_incompatibility"),
        ),
        ("capability", ("service_incompatibility", "service_incompatibility")),
    ),
)
def test_a_stopped_connection_stale_signals_as_a_capability_stale_did(
    effects: _EffectEnvironment, failure: str, signal: tuple[str, str]
) -> None:
    """The bound's terminal stale keeps the signal a lost connection had (#1402)."""
    operation = _plan(effects, suffix="1")
    effects.repository.transition_field_effect(
        operation.plan.plan_id, "planned", "stale", effects.clock(), failure
    )
    signals = cast(
        "list[dict[str, object]]", effects.repository.operational_snapshot()["signals"]
    )
    assert [(value["kind"], value["cause"]) for value in signals] == [signal]
