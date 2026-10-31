"""A write staled by a lost connection is planned again, within a bound (#1402).

A plan that could not proceed only because Audiobookshelf was unreachable does
not consume its decision: once the connection is observed healthy the decision
is planned again through every guard, at most three times. A write that landed
before its confirmation failed is recorded `verified` from the next pre-read,
and nothing here ever sends again a write whose outcome is unknown.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import cast

import httpx
import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import FakeCapabilityObserver
from mechanical_librarian.adapters.library_services.audiobookshelf_field_effect import (
    AudiobookshelfFieldEffectCapabilities,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    MetadataReportError,
    open_repository,
)
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
    canonical_json,
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
    metadata_report,
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


def _plans(effects: _EffectEnvironment) -> list[tuple[str, str, str | None]]:
    return sorted(
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
    )


def _probes(capabilities: FakeCapabilityObserver) -> list[str]:
    """Count every capability observation, the connection probe's included."""
    observed: list[str] = []
    observe = capabilities.observe

    def counted(plan):
        observed.append(plan.destination)
        return observe(plan)

    capabilities.observe = counted  # type: ignore[method-assign]  # reason: the observer is the probe traffic counted here.
    return observed


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
        # A cover waits for its connection like any field (#1415); a field
        # refusal still ends it.
        effects.capabilities.category = "field_unavailable"
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
    effects: _EffectEnvironment, *, reachable: bool = True
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
    if not reachable:
        effects.capabilities.category = "connection_unavailable"
    assert lane.schedule() == (2 if reachable else 0)
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


def _answered_by(
    effects: _EffectEnvironment, handle: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Observe through the real adapter, which decides whether the connection
    was reached; the fake keeps the digest these plans were made against."""
    service_side = AudiobookshelfFieldEffectCapabilities(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handle)),
        endpoint="https://abs.example.invalid",
        library_id="library-1",
    )

    def observe(plan: FieldEffectPlan) -> CapabilityObservation:
        answered = service_side.result(plan)
        return CapabilityObservation(
            answered.category, effects.capabilities.digest, answered.connection_reached
        )

    effects.capabilities.observe = observe  # type: ignore[method-assign]  # reason: the observer is the failing service under test.


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

    _answered_by(effects, handle)
    for _ in range(8):
        lane._run_until_idle()
    assert _plans(effects) == [
        ("description", "stale", "connection"),
        ("tags", "stale", "connection"),
    ]
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
    observed = _probes(effects.capabilities)
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


def _recovered(
    effects: _EffectEnvironment,
    lane: ContinuousGuardedApplication,
    decisions: list[AutomaticDecision],
    sent: list[str],
) -> None:
    """One pass after the connection answers plans, writes and verifies each
    decision exactly once, with no wake and no re-plan attempt spent."""
    lane._run_until_idle()
    assert _plans(effects) == sorted(
        (decision.destination, "verified", None) for decision in decisions
    )
    assert len(set(sent)) == len(sent) == len(decisions)
    assert [
        effects.repository.field_effect_connection_stales(decision.decision_id)
        for decision in decisions
    ] == [0] * len(decisions)
    assert effects.repository.field_effect_capability_suspension("primary", "") is None


def test_a_decision_scheduled_while_unreachable_is_planned_once_it_answers(
    effects: _EffectEnvironment,
) -> None:
    """A decision that met a lost connection before it had a plan waits on its
    library and is probed through its own item, once a pass (#1423)."""
    decisions, lane = _description_and_tags_lane(effects, reachable=False)
    sent = _sent(effects)
    probes = _probes(effects.capabilities)
    for _ in range(4):
        lane._run_until_idle()
    assert _plans(effects) == []
    assert len(probes) == 4
    assert sent == []
    effects.capabilities.category = "allowed"
    _recovered(effects, lane, decisions, sent)
    for operation in map(effects.repository.get_field_effect, sent):
        assert [kind for kind, _ in _kinds(effects, operation.plan.plan_id)] == [
            "planned",
            "claimed",
            "applying",
            "verified",
        ]
    # The probe target is released with the wait.
    traffic = len(probes)
    for _ in range(3):
        lane._run_until_idle()
    assert len(probes) == traffic
    assert len(sent) == 2


@pytest.mark.parametrize(
    "answer",
    ("transport", "key_rejected", "inactive", 500, 503, 408, 429, "malformed"),
)
def test_a_decision_with_no_plan_waits_while_the_connection_is_not_reached(
    effects: _EffectEnvironment, answer: str | int
) -> None:
    """Only a reached connection ends the wait; nothing is planned or sent
    before it, and the probe stays one observation a pass (#1423)."""
    decisions, lane = _description_and_tags_lane(effects, reachable=False)
    sent = _sent(effects)
    fake = effects.capabilities.observe
    user = {
        "isActive": answer != "inactive",
        "permissions": {"update": True, "upload": True, "accessAllLibraries": True},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if answer == "transport":
            raise httpx.ConnectError("lost", request=request)
        if request.url.path == "/api/me":
            if answer == "key_rejected":
                return httpx.Response(401)
            return httpx.Response(
                200, headers={"content-type": "application/json"}, json=user
            )
        if answer == "malformed":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, content=b"{"
            )
        # An inactive account's definite refusal still answers for no book.
        return httpx.Response(404 if answer == "inactive" else cast(int, answer))

    _answered_by(effects, handle)
    probes = _probes(effects.capabilities)
    for _ in range(8):
        lane._run_until_idle()
    assert _plans(effects) == []
    assert sent == []
    assert len(probes) == 8
    assert (
        effects.repository.field_effect_capability_suspension("primary", "")
        == "connection_unavailable"
    )
    effects.capabilities.observe = fake  # type: ignore[method-assign]  # reason: the service answers again through the fake.
    effects.capabilities.category = "allowed"
    _recovered(effects, lane, decisions, sent)


@pytest.mark.parametrize("withdrawn", ("consent", "paused"))
def test_a_withdrawn_waiting_decision_drops_its_probe_target(
    effects: _EffectEnvironment, withdrawn: str
) -> None:
    """Revoking a waiting decision's consent drops its probe target, and
    pausing drops every target until the next schedule; nothing is planned
    for a decision without consent (#1423)."""
    decisions, lane = _description_and_tags_lane(effects, reachable=False)
    description, _ = sorted(decisions, key=lambda value: value.destination)
    sent = _sent(effects)
    probes = _probes(effects.capabilities)
    lane._run_until_idle()
    if withdrawn == "paused":
        lane.set_paused(True)
        for _ in range(3):
            lane._run_until_idle()
        assert len(probes) == 1
        # Resuming wakes the schedule, which finds both still waiting.
        lane.set_paused(False)
        lane._run_until_idle()
        effects.capabilities.category = "allowed"
        _recovered(effects, lane, decisions, sent)
        return
    consent = effects.repository.current_field_effect_consent(
        "primary", "ebooks", "tags"
    )
    effects.repository.revoke_field_effect_consent(consent.consent_id, effects.clock())
    # Revoking consent wakes the schedule, as `revoke` does.
    lane.wake()
    del probes[:]
    for _ in range(3):
        lane._run_until_idle()
    assert set(probes) == {"description"}
    effects.capabilities.category = "allowed"
    _recovered(effects, lane, [description], sent)


def test_a_withdrawn_owner_decision_drops_its_probe_target(
    owner: OwnerStore,
) -> None:
    """An owner decision recorded while Audiobookshelf is unreachable is
    written once it answers; one withdrawn while it waits is not (#1423)."""
    owner.publish(genre_report())
    context = ("primary", "ebooks")
    capability = FakeCapabilityObserver(CAPABILITY)
    capability.category = "connection_unavailable"
    owner.repository.grant_field_effect_consents(
        "primary", ("ebooks",), ("title", "genres"), owner.clock()
    )
    authority = PersistedFieldEffectAuthority(owner.repository)
    lane = ContinuousGuardedApplication(
        owner.repository,
        {context: owner.service},
        {context: capability},
        authority,
        (context,),
        owner.clock,
        lambda *_: None,
    )
    kept = owner.decide(
        "owner_entered", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
    )
    owner.decide("owner_entered", {"kind": "text", "value": "The Silent Orchard"})
    owner.consent.allow(connection="primary", library="ebooks", destination="genres")
    snapshot = authority.resolve(kept)
    key = (*context, snapshot.work_id, "genres")
    owner.target._values[key] = snapshot.pre_value
    owner.target._versions[key] = snapshot.target_version
    observed = _probes(capability)
    # Recording an owner decision wakes the schedule.
    lane.wake()
    lane._run_until_idle()
    assert sorted(observed) == ["genres", "title"]
    owner.decide("withdraw")
    lane.wake()
    del observed[:]
    for _ in range(3):
        lane._run_until_idle()
    assert set(observed) == {"genres"}
    assert owner.patches == []
    capability.category = "allowed"
    lane._run_until_idle()
    ((plan_id, destination, state),) = owner.repository._connection.execute(
        "SELECT plan_id,destination,state FROM field_effects"
    ).fetchall()
    assert (destination, state) == ("genres", "verified")
    assert owner.patches == [plan_id]


def test_a_decision_whose_item_was_refused_is_not_held_waiting(
    effects: _EffectEnvironment,
) -> None:
    """An item the reachable service refused, a 404 included, has answered, so
    its decision is not probed every pass for a connection that is not lost
    (#1423)."""
    effects.capabilities.connection_reached = True
    _, lane = _description_and_tags_lane(effects, reachable=False)
    probes = _probes(effects.capabilities)
    for _ in range(3):
        lane._run_until_idle()
    assert probes == []
    assert _plans(effects) == []


def test_a_failing_waiting_plan_does_not_hide_an_unplanned_recovery(
    effects: _EffectEnvironment,
) -> None:
    """The library's probe alternates between a waiting plan and a decision
    that never had one, so a plan whose item keeps failing cannot hide the
    connection the other decision reaches; each recovered write goes once."""
    decisions, lane = _description_and_tags_lane(effects, reachable=False)
    sent = _sent(effects)
    effects.capabilities.category = "allowed"
    lane.wake()
    assert lane.schedule(1) == 1
    ((failing, _, _),) = _plans(effects)
    (answering,) = (
        value.destination for value in decisions if value.destination != failing
    )
    effects.capabilities.category = "connection_unavailable"
    lane._run_until_idle()
    assert _plans(effects) == [(failing, "stale", "connection")]
    fake = effects.capabilities.observe

    def observe(plan: FieldEffectPlan) -> CapabilityObservation:
        if plan.destination == failing:
            return CapabilityObservation(
                "connection_unavailable", effects.capabilities.digest, False
            )
        return fake(plan)

    effects.capabilities.observe = observe  # type: ignore[method-assign]  # reason: the waiting plan's item keeps failing while the other answers.
    effects.capabilities.category = "allowed"
    for _ in range(4):
        lane._run_until_idle()
    assert _plans(effects) == sorted(
        [(failing, "stale", "connection"), (answering, "verified", None)]
    )
    ((plan_id, _, _),) = effects.repository._connection.execute(
        "SELECT plan_id,destination,state FROM field_effects WHERE destination=?",
        (answering,),
    ).fetchall()
    assert sent == [plan_id]
    # Once the other item answers, the connection counts as reached, so the
    # failing plan spends its bounded re-plans as #1402 defines; the decision
    # that never had a plan spends none.
    assert {
        value.destination: effects.repository.field_effect_connection_stales(
            value.decision_id
        )
        for value in decisions
    } == {failing: 3, answering: 0}


def test_a_withdrawn_waiting_plan_leaves_the_unplanned_decision_its_probe(
    owner: OwnerStore,
) -> None:
    """Once the library's only waiting plan is withdrawn, an owner decision
    that never had a plan still holds the wait and is written once the
    connection is reached (#1423)."""
    owner.publish(genre_report())
    context = ("primary", "ebooks")
    capability = FakeCapabilityObserver(CAPABILITY)
    owner.repository.grant_field_effect_consents(
        "primary", ("ebooks",), ("title", "genres"), owner.clock()
    )
    authority = PersistedFieldEffectAuthority(owner.repository)
    lane = ContinuousGuardedApplication(
        owner.repository,
        {context: owner.service},
        {context: capability},
        authority,
        (context,),
        owner.clock,
        lambda *_: None,
    )
    for destination in ("title", "genres"):
        owner.consent.allow(
            connection="primary", library="ebooks", destination=destination
        )
    owner.decide(
        "owner_entered", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
    )
    # The pre-read loses the connection: the genres plan waits on it.
    owner.target.unavailable_scope_next = "connection"
    lane.wake()
    lane._run_until_idle()
    ((genres_plan, state, failure),) = owner.repository._connection.execute(
        "SELECT plan_id,state,json_extract(document,'$.failure') FROM field_effects"
    ).fetchall()
    assert (state, failure) == ("stale", "connection")
    capability.category = "connection_unavailable"
    kept = owner.decide(
        "owner_entered", {"kind": "text", "value": "The Silent Orchard"}
    )
    snapshot = authority.resolve(kept)
    key = (*context, snapshot.work_id, "title")
    owner.target._values[key] = snapshot.pre_value
    owner.target._versions[key] = snapshot.target_version
    lane.wake()
    lane._run_until_idle()
    owner.decide("withdraw", destination="genres")
    lane.wake()
    for _ in range(3):
        lane._run_until_idle()
    assert owner.repository.get_field_effect(genres_plan).state == "superseded"
    capability.category = "allowed"
    lane._run_until_idle()
    rows = owner.repository._connection.execute(
        "SELECT plan_id,destination,state FROM field_effects ORDER BY destination"
    ).fetchall()
    ((title_plan, _, _),) = [row for row in rows if row[1] == "title"]
    assert [row[1:] for row in rows] == [
        ("genres", "superseded"),
        ("title", "verified"),
    ]
    assert owner.patches == [title_plan]


def test_a_wake_during_a_decision_observation_keeps_its_target_out(
    effects: _EffectEnvironment,
) -> None:
    """A withdrawal that wakes the schedule while a decision is being observed
    leaves no target from the abandoned schedule behind (#1423)."""
    _, lane = _description_and_tags_lane(effects, reachable=False)
    lane.wake()
    fake = effects.capabilities.observe
    revoked: list[str] = []

    def observe(plan: FieldEffectPlan) -> CapabilityObservation:
        if plan.destination == "tags" and not revoked:
            consent = effects.repository.current_field_effect_consent(
                "primary", "ebooks", "tags"
            )
            revoked.append(consent.consent_id)
            effects.repository.revoke_field_effect_consent(
                consent.consent_id, effects.clock()
            )
            lane.wake()
        return fake(plan)

    effects.capabilities.observe = observe  # type: ignore[method-assign]  # reason: the revocation lands while the observation is in flight.
    lane._run_until_idle()
    assert revoked
    probes = _probes(effects.capabilities)
    for _ in range(4):
        lane._run_until_idle()
    assert probes == ["description"] * 4


def test_a_restart_schedules_a_waiting_decision_from_the_start(
    effects: _EffectEnvironment,
) -> None:
    """The probe target is in memory: a restarted lane's first schedule reads
    every pending decision again, as it did before #1423."""
    decisions, lane = _description_and_tags_lane(effects, reachable=False)
    lane._run_until_idle()
    sent = _sent(effects)
    effects.capabilities.category = "allowed"
    restarted = ContinuousGuardedApplication(
        effects.repository,
        dict(lane._applications),
        dict(lane._capabilities),
        lane._authority,
        lane._libraries,
        effects.clock,
        lambda *_: None,
    )
    _recovered(effects, restarted, decisions, sent)


def _cover(effects: _EffectEnvironment, suffix: str = "1") -> FieldEffectOperation:
    return _plan(
        effects,
        destination="cover",
        pre=None,
        desired="cover_" + "a" * 64,
        suffix=suffix,
    )


def _cover_again(effects: _EffectEnvironment) -> FieldEffectOperation | None:
    return _replan(
        effects, destination="cover", pre=None, desired="cover_" + "a" * 64, suffix="1"
    )


def _cleanup(effects: _EffectEnvironment, plan_id: str) -> list[tuple[object, ...]]:
    return effects.repository._connection.execute(
        "SELECT state FROM field_effect_cleanups WHERE plan_id=?", (plan_id,)
    ).fetchall()


def _lose_connection_after_capture(effects: _EffectEnvironment) -> None:
    def lose(_: FieldEffectPlan) -> None:
        effects.capabilities.category = "connection_unavailable"

    effects.preclaim.callback = lose


def test_a_cover_a_lost_connection_staled_is_planned_again_and_written_once(
    effects: _EffectEnvironment,
) -> None:
    """A cover waits for its connection like any field (#1415)."""
    operation = _cover(effects)
    plan_id = operation.plan.plan_id
    sent = _sent(effects)
    _lose_connection_after_capture(effects)
    stale = effects.service.apply(plan_id)
    assert (stale.state, stale.failure) == ("stale", "connection")
    # The restore point stays, and nothing waits to delete it.
    assert effects.recovery.exists(plan_id)
    assert _cleanup(effects, plan_id) == []
    effects.capabilities.category = "allowed"
    revived = _cover_again(effects)
    assert revived is not None
    assert (
        revived.plan.plan_id,
        revived.state,
        revived.attempt,
        revived.claim_token,
    ) == (plan_id, "planned", stale.attempt, stale.claim_token)
    verified = effects.service.apply(plan_id)
    assert verified.state == "verified"
    assert sent == [plan_id]
    assert _cleanup(effects, plan_id) == [("completed",)]
    assert not effects.recovery.exists(plan_id)
    effects.repository.close()
    effects.repository = open_repository(effects.database_path, create=False)
    assert effects.repository.get_field_effect(plan_id).to_data() == verified.to_data()


def test_a_cover_waits_three_times_then_stops_and_is_cleaned_up(
    effects: _EffectEnvironment,
) -> None:
    report = metadata_report("Paper Moons", "a", "b", item_id="book-1")
    effects.repository.publish(report, lambda: True)
    operation = _cover(effects)
    plan_id = operation.plan.plan_id
    sent = _sent(effects)
    for prepared in range(3):
        _lose_connection_after_capture(effects)
        waiting = effects.service.apply(plan_id)
        assert (waiting.state, waiting.failure) == ("stale", "connection")
        assert effects.recovery.exists(plan_id)
        assert _cleanup(effects, plan_id) == []
        if prepared == 1:
            row = next(event for event in _ledger(effects) if event["state"] == "stale")
            assert row["change"] == "Prepared change waiting"
            assert _facts(row)["What happens next"].endswith(
                "Prepared again 1 of 3 times."
            )
        effects.capabilities.category = "allowed"
        revived = _cover_again(effects)
        assert revived is not None and revived.state == "planned"
    effects.recovery.fail_remove_next = True
    _lose_connection_after_capture(effects)
    stopped = effects.service.apply(plan_id)
    assert (stopped.state, stopped.failure) == ("stale", "connection_exhausted")
    assert effects.repository.field_effect_cleanup_pending(plan_id)
    row = next(event for event in _ledger(effects) if event["state"] == "stale")
    assert _facts(row)["Why it stopped"] == (
        "Audiobookshelf could not be reached for this book, and this change "
        "stopped after it was prepared again 3 times"
    )
    assert effects.service.recover_pending() == (0, 1)
    assert not effects.recovery.exists(plan_id)
    effects.capabilities.category = "allowed"
    again = _cover_again(effects)
    assert again is not None
    assert (again.state, again.failure) == ("stale", "connection_exhausted")
    assert sent == []


@pytest.mark.parametrize("withdrawal", ("supersede", "revoke_consent"))
def test_a_withdrawn_waiting_cover_is_cleaned_up(
    effects: _EffectEnvironment, withdrawal: str
) -> None:
    """A waiting cover that is withdrawn deletes its restore point."""
    operation = _cover(effects)
    plan_id = operation.plan.plan_id
    _lose_connection_after_capture(effects)
    assert effects.service.apply(plan_id).failure == "connection"
    assert effects.recovery.exists(plan_id)
    if withdrawal == "supersede":
        assert effects.service.supersede(operation.plan.authority.decision_id) == 1
        assert effects.service.status(plan_id).state == "superseded"
    else:
        assert effects.service.revoke_consent(operation.plan.consent.consent_id) == 1
    assert _cleanup(effects, plan_id) == [("completed",)]
    assert not effects.recovery.exists(plan_id)
    effects.repository.close()
    effects.repository = open_repository(effects.database_path, create=False)


def test_a_cover_whose_write_landed_waits_and_is_verified_without_a_second_write(
    effects: _EffectEnvironment,
) -> None:
    operation = _cover(effects)
    plan_id = operation.plan.plan_id
    sent = _sent(effects)
    effects.target.fail_read_after_apply_for.add(plan_id)
    assert effects.service.apply(plan_id).state == "reconciling"
    unknown = effects.service.reconcile(plan_id)
    assert (unknown.state, unknown.failure) == ("retryable", "target_failure")
    effects.target.fail_reads_for.clear()
    effects.capabilities.category = "connection_unavailable"
    assert effects.service.apply(plan_id).failure == "connection"
    assert effects.recovery.exists(plan_id)
    assert _cleanup(effects, plan_id) == []
    effects.capabilities.category = "allowed"
    revived = _cover_again(effects)
    assert revived is not None
    assert (revived.state, revived.failure, revived.attempt) == (
        "retryable",
        "target_failure",
        unknown.attempt,
    )
    assert effects.service.apply(plan_id).state == "verified"
    assert sent == [plan_id]
    assert _cleanup(effects, plan_id) == [("completed",)]
    assert not effects.recovery.exists(plan_id)
    effects.repository.close()
    effects.repository = open_repository(effects.database_path, create=False)


def test_only_a_waiting_cover_may_lack_its_cleanup(
    effects: _EffectEnvironment,
) -> None:
    waiting = _cover(effects)
    _lose_connection_after_capture(effects)
    assert effects.service.apply(waiting.plan.plan_id).failure == "connection"
    effects.capabilities.category = "allowed"
    stopped = _cover(effects, "2")
    effects.capabilities.category = "field_unavailable"
    assert effects.service.apply(stopped.plan.plan_id).failure == "capability"
    assert _cleanup(effects, stopped.plan.plan_id) == [("completed",)]
    effects.repository.close()
    open_repository(effects.database_path, create=False).close()
    pristine = effects.database_path.read_bytes()
    for statement, parameters in (
        (
            "INSERT INTO field_effect_cleanups(plan_id,state,attempt,updated_at,"
            "document) VALUES(?,?,?,?,?)",
            (
                waiting.plan.plan_id,
                "pending",
                0,
                1,
                canonical_json(
                    {
                        "attempt": 0,
                        "plan_id": waiting.plan.plan_id,
                        "schema_version": "field-effect-cleanup-v1",
                        "state": "pending",
                        "updated_at": 1,
                    }
                ),
            ),
        ),
        (
            "DELETE FROM field_effect_cleanups WHERE plan_id=?",
            (stopped.plan.plan_id,),
        ),
    ):
        effects.database_path.write_bytes(pristine)
        connection = sqlite3.connect(effects.database_path)
        assert connection.execute(statement, parameters).rowcount == 1
        connection.commit()
        connection.close()
        with pytest.raises(MetadataReportError):
            open_repository(effects.database_path, create=False)
    effects.database_path.write_bytes(pristine)
    effects.repository = open_repository(effects.database_path, create=False)
