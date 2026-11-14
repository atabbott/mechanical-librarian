"""Book matching's level gate and reconciliation for fields (#1355, S4, S10).

Contract tests 17a, 17b, 17d, 17e and 17f, fields half. Changing the level
gates every case reconciled at another level: owner choices are refused and
owner writes wait until the projection reconciles the case from stored data.
Reconciliation asks again only the fields whose offer changed, with one
valueless ``matching_changed`` row each, closes their pending effects as
``matching_changed`` and leaves automatic decisions, consents and holds as
they are. The fixtures are synthetic books by "R. F. Example".
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.persistence.level_reconciliation import (
    case_level_unreconciled,
)
from mechanical_librarian.adapters.persistence.metadata_reports import (
    open_repository,
)
from mechanical_librarian.adapters.persistence.review_preparation import (
    matching_check_counts,
    matching_check_given_up,
)
from mechanical_librarian.adapters.persistence.schema import (
    _offered_alternatives_sql,
    _review_case_field_fact_values_sql,
    refresh_offered_field_facts,
    refresh_policy_settled_field_facts,
)
from mechanical_librarian.application.field_effects import GuardedApplicationError
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.domain.field_effect import FieldEffectOperation
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    decision_actions,
    offer_digest,
)
from mechanical_librarian.domain.metadata_report import MetadataReport, TargetFacts
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from tests.integration.test_book_matching_recheck import (
    _PAYLOAD,
    _cover_stage,
    _Level,
    _save,
    _work,
)
from tests.integration.test_leaving_automatic_posture import (
    _accept,
    _applier,
    _Library,
    _planned,
    _prepared,
)
from tests.support.owner_values import OwnerStore
from tests.unit import test_identity_refused_retention as retained
from tests.unit.test_cover_packets import _source
from tests.unit.test_level_offer_binding import (
    LIVE_SYNOPSIS,
    SYNOPSIS,
    choose,
    counts,
    field_of,
    level_description_report,
    level_store,
    offer_of,
    owner_client,
)

_IDENTIFIER = WorkIdentityLevel.IDENTIFIER
_EXACT = WorkIdentityLevel.TITLE_AND_CREATOR_EXACT
_WIDE = WorkIdentityLevel.TITLE_AND_CREATOR
_LEVEL_TABLES = (
    "review_cases",
    "proposal_sets",
    "human_decisions",
    "human_decision_events",
    "field_effects",
    "field_effect_events",
    "field_effect_authorities",
    "field_effect_manual_holds",
    "automatic_decisions",
)


# --- Fixtures -----------------------------------------------------------------


def _report(item: str = "item-1", *, refused: bool = True) -> MetadataReport:
    """The level fixture's book as ``item``: a live description answer, and,
    with ``refused``, a refused WORK answer the wider levels admit."""
    base = level_description_report()
    providers = (
        base.providers
        if refused
        else tuple(
            value for value in base.providers if value.provider != retained.PROVIDER
        )
    )
    report = MetadataReport.create(
        target=TargetFacts("primary", "ebooks", item, retained._fields()),
        file=replace(base.file),
        local_claims=base.local_claims,
        local_reasons=base.local_reasons,
        providers=providers,
    )
    return MetadataReport.from_json(report.to_json())


def _store(
    root: Path, level: WorkIdentityLevel, *reports: MetadataReport
) -> OwnerStore:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    store = OwnerStore(root, None)
    _change(store, level, 0)
    for report in reports or (_report(),):
        store.publish(report)
    return store


def _change(store: OwnerStore, level: WorkIdentityLevel, at: int) -> None:
    """Configure ``level`` as a save or a restart does."""
    store.repository.bind_current_cover_authority(
        "cover_policy_" + "1" * 64, "cover_replay_" + "2" * 64, level.value
    )
    store.repository.align_book_matching(level.value, at)


def _check(store: OwnerStore, at: int, case_id: str | None = None) -> str:
    """The projection's first step, as `ReviewPipelineStages.project` runs it."""
    return store.repository.reconcile_case_level(
        case_id or store.case.review_case_id, at
    )


def _marker(store: OwnerStore, case_id: str | None = None) -> str | None:
    row = store.repository._connection.execute(
        "SELECT reconciled_level FROM review_case_level_reconciliation "
        "WHERE review_case_id=?",
        (case_id or store.case.review_case_id,),
    ).fetchone()
    return None if row is None else str(row[0])


def _record(
    store: OwnerStore, text: str, key: str, *, live: bool = False
) -> HumanDecision:
    decision = choose(
        store,
        text,
        offer=store.case.proposals if live else None,
        key=key,
    )
    store.repository.record_human_decision(decision)
    return decision


def _effect(store: OwnerStore, plan_id: str) -> tuple[str, str | None]:
    operation = FieldEffectOperation.from_json(
        store.repository._connection.execute(
            "SELECT document FROM field_effects WHERE plan_id=?", (plan_id,)
        ).fetchone()[0]
    )
    return operation.state, operation.failure


def _rows(store: OwnerStore) -> dict[str, list[tuple[object, ...]]]:
    connection = store.repository._connection
    return {
        table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
        for table in _LEVEL_TABLES
    }


def _decisions(store: OwnerStore, case_id: str | None = None) -> list[HumanDecision]:
    return [
        HumanDecision.from_json(document)
        for (document,) in store.repository._connection.execute(
            "SELECT document FROM human_decisions WHERE review_case_id=? "
            "ORDER BY ordinal",
            (case_id or store.case.review_case_id,),
        ).fetchall()
    ]


def _event_id(decision: HumanDecision) -> str:
    return (
        "human_decision_event_"
        + hashlib.sha256(
            ("human-decision-event-v1:" + decision.decision_id).encode()
        ).hexdigest()
    )


def _activity(client: Any) -> list[dict[str, Any]]:
    response = client.get("/api/v1/activity")
    assert response.status_code == 200, response.text
    return cast("list[dict[str, Any]]", response.json()["events"])


# --- 17a: only a changed offer is asked again ---------------------------------


@pytest.mark.parametrize("case", ["no_substitution", "substituted_case"])
def test_level_change_leaves_cases_and_unrelated_writes_byte_identical(
    tmp_path: Path, case: str
) -> None:
    """A level change rewrites no case, proposal set, decision, effect, hold
    or automatic row: the cases are reconciled by their markers alone, and an
    owner write whose offer the level leaves alone still plans and writes."""
    unrelated = _report("item-2", refused=False)
    reports = (
        (unrelated,) if case == "no_substitution" else (_report("item-1"), unrelated)
    )
    store = _store(tmp_path, _IDENTIFIER, *reports)
    try:
        decision = _record(store, LIVE_SYNOPSIS, "unrelated-choice-01")
        operation = store.plan(decision)
        case_ids = [
            str(row[0])
            for row in store.repository._connection.execute(
                "SELECT review_case_id FROM current_review_case_lineages "
                "ORDER BY item_id"
            ).fetchall()
        ]
        before = _rows(store)

        _change(store, _WIDE, 50)
        assert all(
            case_level_unreconciled(store.repository._connection, case_id)
            for case_id in case_ids
        )
        for case_id in case_ids:
            assert _check(store, 51, case_id) == "reconciled"

        assert _rows(store) == before
        assert {case_id: _marker(store, case_id) for case_id in case_ids} == {
            case_id: _WIDE.value for case_id in case_ids
        }
        offers = store.repository._connection.execute(
            "SELECT review_case_id FROM review_case_level_offers"
        ).fetchall()
        assert len(offers) == (0 if case == "no_substitution" else 1)
        assert _effect(store, operation.plan.plan_id) == ("planned", None)
        assert store.service.apply(operation.plan.plan_id).state == "verified"
    finally:
        store.close()


def _reask_store(
    tmp_path: Path, case: str
) -> tuple[OwnerStore, HumanDecision, WorkIdentityLevel]:
    """The store, the standing choice, and the level the case then changes to."""
    if case in {"substituted_offer_changed", "level_alternative_chosen"}:
        store = _store(tmp_path, _WIDE)
        text = SYNOPSIS if case == "level_alternative_chosen" else LIVE_SYNOPSIS
        return store, _record(store, text, "standing-choice-01"), _EXACT
    if case == "live_to_level":
        store = _store(tmp_path, _IDENTIFIER)
        return store, _record(store, LIVE_SYNOPSIS, "standing-choice-01"), _WIDE
    if case == "level_to_live":
        store = _store(tmp_path, _WIDE)
        return store, _record(store, LIVE_SYNOPSIS, "standing-choice-01"), _IDENTIFIER
    store = _store(tmp_path, _IDENTIFIER)
    proposal = next(
        value for value in store.case.proposals if value["destination"] == "title"
    )
    decision = HumanDecision.create(
        store.case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=store.case.target_fingerprint,
        destination="title",
        actor="owner",
        outcome=(
            "owner_entered" if case == "no_alternatives_to_alternatives" else "accept"
        ),
        selected_value=(
            {"kind": "text", "value": "Example Book"}
            if case == "no_alternatives_to_alternatives"
            else next(
                alternative["value"]
                for alternative in proposal["result"]["alternatives"]
                if alternative["assessment"] != "preserves_target"
            )
        ),
        idempotency_key="standing-choice-01",
        occurred_at=store.clock(),
        previous=store.previous().get("title"),
    )
    store.repository.record_human_decision(decision)
    return store, decision, _WIDE


@pytest.mark.parametrize(
    "case",
    [
        "substituted_offer_changed",
        "level_alternative_chosen",
        "live_to_level",
        "level_to_live",
        "no_alternatives_to_alternatives",
        "group_member_changed",
    ],
)
def test_level_change_reasks_only_changed_offer(tmp_path: Path, case: str) -> None:
    """The field whose offer changed stops counting: one valueless
    ``matching_changed`` row binds its new offer, its pending effect closes as
    ``matching_changed``, its hold stays, Activity notes the choice as asked
    again, and no other field gains a row."""
    store, standing, changed_to = _reask_store(tmp_path, case)
    try:
        destination = standing.destination
        operation = store.plan(standing)
        # A hold the field carries stays: the field stays the owner's.
        store.repository._set_field_effect_manual_hold(
            store.case.review_case_id, destination, store.clock(), standing.decision_id
        )
        store.repository._connection.commit()
        holds = _rows(store)["field_effect_manual_holds"]
        events = counts(store)[1]
        before = offer_digest(offer_of(store), destination)

        _change(store, changed_to, 60)
        with pytest.raises(GuardedApplicationError) as deferred:
            store.service.apply(operation.plan.plan_id)
        assert deferred.value.code == "deferred"
        assert _check(store, 61) == "reconciled"

        after = offer_of(store)
        assert offer_digest(after, destination) != before
        *_, earlier, cause = _decisions(store)
        assert earlier == standing
        assert cause == HumanDecision.matching_changed(
            store.case, standing, changed_to.value, after, 61
        )
        assert [value.destination for value in _decisions(store)] == [
            destination,
            destination,
        ]
        assert counts(store)[1] == events  # no event, so no new Activity row
        assert _effect(store, operation.plan.plan_id) == ("stale", "matching_changed")
        assert _rows(store)["field_effect_manual_holds"] == holds
        assert _marker(store) == changed_to.value
        with owner_client(store, tmp_path) as (client, _csrf):
            detail = client.get(
                f"/api/v1/review-cases/{store.case.review_case_id}"
            ).json()
            notes = {event["event_id"]: event["note"] for event in _activity(client)}
        assert detail["checking"] is False
        assert field_of(detail, destination)["human_decision"] is None
        assert notes[_event_id(standing)] == "asked_again_matching"
        # A replayed check asks nothing twice.
        assert _check(store, 62) == "reconciled"
        assert len(_decisions(store)) == 2
    finally:
        store.close()


# --- 17b guard: automatic effects on unsubstituted fields ----------------------


@pytest.fixture
def automatic_library(tmp_path: Path) -> Any:
    with _prepared(tmp_path, automatic=True) as value:
        yield value


@pytest.mark.parametrize("state", ["planned", "awaiting_consent"])
def test_level_change_keeps_automatic_effect_on_unsubstituted_field(
    automatic_library: _Library, tmp_path: Path, state: str
) -> None:
    """An automatic decision's write, planned or still waiting for consent,
    is never deferred, closed or re-decided by a level change and its check."""
    harness = automatic_library
    automatic = harness.automatic()
    scheduler, service, target = _applier(harness, tmp_path)
    if state == "planned":
        harness.root.grant_field_effect_consents(
            "primary", ("ebooks",), ("authors",), 12
        )
        harness.now[0] = 13
        scheduler.schedule()
        operation = _planned(harness)[automatic.decision_id]
        assert operation.state == "planned"
    connection = harness.root._connection
    before = connection.execute(
        "SELECT document FROM automatic_decisions ORDER BY rowid"
    ).fetchall()

    _save(harness, _WIDE.value, at=40)
    if state == "planned":
        assert not harness.root.field_effect_deferred(operation.plan.plan_id)
    harness.now[0] = 41
    harness.drain()

    assert connection.execute(
        "SELECT reconciled_level FROM review_case_level_reconciliation"
    ).fetchall() == [(_WIDE.value,)]
    assert (
        connection.execute(
            "SELECT document FROM automatic_decisions ORDER BY rowid"
        ).fetchall()
        == before
    )
    assert connection.execute("SELECT count(*) FROM human_decisions").fetchone() == (0,)
    if state == "planned":
        assert harness.root.field_effect_authority_is_current(operation.plan.authority)
        target.seed(operation.plan)
        assert service.apply(operation.plan.plan_id).state == "verified"
    else:
        assert automatic.decision_id in harness.pending()
        assert not _planned(harness)


# --- 17d: the valueless row, the waiting write, and its guard ------------------


def test_offer_change_appends_valueless_matching_changed_decision(
    tmp_path: Path,
) -> None:
    """The row the check appends is valueless, owner-actored, caused by
    Book matching, bound to the new offer, idempotent by its superseded choice
    and level, and has no event; it reads as no human decision."""
    store = _store(tmp_path, _WIDE)
    try:
        standing = _record(store, SYNOPSIS, "level-choice-0002")
        _change(store, _IDENTIFIER, 70)
        _check(store, 71)
        *_, cause = _decisions(store)
        assert (cause.actor, cause.outcome, cause.selected_value, cause.cause) == (
            "owner",
            "withdraw",
            None,
            "matching_changed",
        )
        assert cause.proposal_id == standing.proposal_id
        assert cause.offered_alternatives_digest == offer_digest(
            store.case.proposals, "description"
        )
        assert (
            cause.idempotency_digest
            == hashlib.sha256(
                f"matching-changed-v1:{standing.decision_id}:identifier".encode()
            ).hexdigest()
        )
        assert store.repository._connection.execute(
            "SELECT count(*) FROM human_decision_events WHERE decision_id=?",
            (cause.decision_id,),
        ).fetchone() == (0,)
        # Readers validate it and project it as no human decision.
        assert (
            json.loads(
                store.repository.get_review_case_decisions(store.case.review_case_id)[
                    -1
                ]
            )["cause"]
            == "matching_changed"
        )
    finally:
        store.close()


def test_old_level_effect_waits_for_reconciliation(tmp_path: Path) -> None:
    """An owner write on a case still reconciled at the old level waits,
    unclaimed and still "Planned", noted as waiting for the check; once the
    check reconciles the case and leaves the offer alone, it writes."""
    store = _store(tmp_path, _IDENTIFIER, _report(refused=False))
    try:
        decision = _record(store, LIVE_SYNOPSIS, "waiting-choice-01")
        operation = store.plan(decision)
        plan_id = operation.plan.plan_id
        case_id = store.case.review_case_id
        _change(store, _WIDE, 80)
        assert store.repository.field_effect_deferred(plan_id)
        with pytest.raises(GuardedApplicationError) as deferred:
            store.service.apply(plan_id)
        assert deferred.value.code == "deferred"
        assert _effect(store, plan_id) == ("planned", None)
        with owner_client(store, tmp_path) as (client, _csrf):
            assert client.get(f"/api/v1/review-cases/{case_id}").json()["checking"]
            waiting = [
                event
                for event in _activity(client)
                if event.get("change_id") == plan_id
            ]
            assert waiting
            assert {event["state"] for event in waiting} == {"planned"}
            assert {event["note"] for event in waiting} == {"waiting_for_check"}

            assert _check(store, 81) == "reconciled"

            assert not client.get(f"/api/v1/review-cases/{case_id}").json()["checking"]
            assert {
                event["note"]
                for event in _activity(client)
                if event.get("change_id") == plan_id
            } == {None}
        assert not store.repository.field_effect_deferred(plan_id)
        assert store.service.apply(plan_id).state == "verified"
    finally:
        store.close()


def test_the_claim_defers_a_write_whose_case_became_unreconciled(
    tmp_path: Path,
) -> None:
    """The atomic claim re-checks the gate, so a check that began after the
    pre-claim guards ran still holds the write back (plan fact 5)."""
    store = _store(tmp_path, _IDENTIFIER, _report(refused=False))
    try:
        operation = store.plan(_record(store, LIVE_SYNOPSIS, "claim-choice-0001"))
        _change(store, _WIDE, 85)
        with pytest.raises(Exception) as refused:
            store.repository.claim_field_effect(
                operation.plan.plan_id, "planned", "c" * 64, 86
            )
        assert getattr(refused.value, "code", None) == "deferred"
        assert _effect(store, operation.plan.plan_id) == ("planned", None)
    finally:
        store.close()


def test_automatic_and_consent_effects_never_wait_on_reconciled_level(
    automatic_library: _Library, tmp_path: Path
) -> None:
    """While the case waits for its check, its automatic write is not
    deferred and writes; only owner writes wait (guard)."""
    harness = automatic_library
    automatic = harness.automatic()
    scheduler, service, target = _applier(harness, tmp_path)
    harness.root.grant_field_effect_consents("primary", ("ebooks",), ("authors",), 12)
    harness.now[0] = 13
    scheduler.schedule()
    operation = _planned(harness)[automatic.decision_id]
    _save(harness, _WIDE.value, at=40)
    connection = harness.root._connection
    assert case_level_unreconciled(connection, harness.case_id())
    assert not harness.root.field_effect_deferred(operation.plan.plan_id)
    target.seed(operation.plan)
    assert service.apply(operation.plan.plan_id).state == "verified"


# --- 17e: reconciliation is complete, retried and from stored data ------------


def test_decided_work_with_pending_owner_effect_is_reconciled(tmp_path: Path) -> None:
    """A decided work whose owner write is still pending is reconciled like
    any other: its choice is asked again and the pending write closes."""
    store = _store(tmp_path, _IDENTIFIER)
    try:
        standing = _record(store, LIVE_SYNOPSIS, "pending-choice-01")
        operation = store.plan(standing)
        _change(store, _WIDE, 90)
        assert _check(store, 91) == "reconciled"
        assert _marker(store) == _WIDE.value
        assert _decisions(store)[-1].cause == "matching_changed"
        assert _effect(store, operation.plan.plan_id) == ("stale", "matching_changed")
        assert store.patches == []
    finally:
        store.close()


def test_failed_reconciliation_keeps_effects_deferred_not_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check that fails writes nothing: the case stays gated, its owner
    write stays planned and deferred, and the next check reconciles it."""
    from mechanical_librarian.adapters.persistence import level_reconciliation

    store = _store(tmp_path, _IDENTIFIER)
    try:
        standing = _record(store, LIVE_SYNOPSIS, "failing-choice-01")
        operation = store.plan(standing)
        _change(store, _WIDE, 100)
        before = _rows(store)
        original = level_reconciliation.derive_level

        def failing(*_args: object, **_kwargs: object) -> object:
            raise ValueError("synthetic derivation failure")

        monkeypatch.setattr(level_reconciliation, "derive_level", failing)
        with pytest.raises(MetadataReportError):
            _check(store, 101)
        assert _rows(store) == before
        assert _marker(store) is None
        assert store.repository.field_effect_deferred(operation.plan.plan_id)
        assert _effect(store, operation.plan.plan_id) == ("planned", None)

        monkeypatch.setattr(level_reconciliation, "derive_level", original)
        assert _check(store, 102) == "reconciled"
        assert _effect(store, operation.plan.plan_id) == ("stale", "matching_changed")
    finally:
        store.close()


def test_back_to_back_level_changes_reconcile_to_last(tmp_path: Path) -> None:
    """Two saves before any check: the check reconciles to the last level
    only, with one row naming it."""
    store = _store(tmp_path, _IDENTIFIER)
    try:
        standing = _record(store, LIVE_SYNOPSIS, "back-to-back-0001")
        _change(store, _EXACT, 110)
        _change(store, _WIDE, 111)
        assert _check(store, 112) == "reconciled"
        assert _marker(store) == _WIDE.value
        *_, cause = _decisions(store)
        assert cause == HumanDecision.matching_changed(
            store.case, standing, _WIDE.value, offer_of(store), 112
        )
        assert len(_decisions(store)) == 2
        assert store.repository._connection.execute(
            "SELECT work_identity_level FROM review_case_level_offers"
        ).fetchall() == [(_WIDE.value,)]
    finally:
        store.close()


def test_full_synthetic_library_reconciled_after_level_change(tmp_path: Path) -> None:
    """Every current case of a mixed library, decided and undecided, with and
    without a level offer, is reconciled by one check each; only the decided
    fields whose offer changed are asked again."""
    reports = [
        _report(f"item-{index}", refused=index % 2 == 0) for index in range(1, 13)
    ]
    store = _store(tmp_path, _IDENTIFIER, *reports)
    try:
        connection = store.repository._connection
        decided: dict[str, bool] = {}
        for report in reports[:6]:
            case_id = connection.execute(
                "SELECT review_case_id FROM current_review_case_lineages "
                "WHERE item_id=?",
                (report.target.item,),
            ).fetchone()[0]
            store.load(case_id)
            _record(store, LIVE_SYNOPSIS, f"library-choice-{report.target.item}x")
            decided[case_id] = any(
                value.provider == retained.PROVIDER for value in report.providers
            )
        case_ids = [
            str(row[0])
            for row in connection.execute(
                "SELECT review_case_id FROM current_review_case_lineages"
            ).fetchall()
        ]
        assert len(case_ids) == 12
        _change(store, _WIDE, 120)
        assert all(case_level_unreconciled(connection, case) for case in case_ids)
        for case_id in case_ids:
            assert _check(store, 121, case_id) == "reconciled"
        assert not any(case_level_unreconciled(connection, case) for case in case_ids)
        causes = {
            str(row[0])
            for row in connection.execute(
                "SELECT review_case_id FROM human_decisions "
                "WHERE json_extract(document,'$.cause')='matching_changed'"
            ).fetchall()
        }
        assert causes == {case for case, substituted in decided.items() if substituted}
    finally:
        store.close()


@pytest.fixture
def recheck_level() -> _Level:
    return _Level()


@pytest.fixture
def recheck_library(tmp_path: Path, recheck_level: _Level) -> Iterator[_Library]:
    """The Book matching re-check harness: one work prepared in Preview whose
    projection prepares its cover comparison at the configured level."""
    with _prepared(
        tmp_path,
        automatic=False,
        covers=(_source(2, _PAYLOAD, embedded=True),),
        before_projection=_cover_stage(recheck_level),
    ) as value:
        recheck_level.now = value.now
        yield value


def test_the_check_and_self_refresh_announce_the_case(
    recheck_library: _Library,
    recheck_level: _Level,
) -> None:
    """The case reads ``checking`` from the save until its projection
    reconciles it, and that projection announces the case, so an open page
    refreshes itself out of ``checking``."""
    library = recheck_library
    announced: list[tuple[str, str | None]] = []
    library.pipeline.attach_updates(
        lambda topic, case_id: announced.append((topic, case_id))
    )
    case_id = library.case_id()
    assert InspectCurrentReviewCase(library.root).run(case_id)["checking"] is False
    recheck_level.value = _WIDE
    _save(library, _WIDE.value, at=40)
    waiting = InspectCurrentReviewCase(library.root).run(case_id)
    assert (waiting["checking"], waiting["check_given_up"]) == (True, False)
    assert matching_check_counts(library.root._connection, _WIDE.value) == (1, 1, 0)
    library.now[0] = 41
    library.drain()
    assert ("case", case_id) in announced
    checked = InspectCurrentReviewCase(library.root).run(case_id)
    assert (checked["checking"], checked["check_given_up"]) == (False, False)
    assert matching_check_counts(library.root._connection, _WIDE.value) == (1, 0, 0)


def test_a_refused_choice_requests_its_case_check_again(
    recheck_library: _Library,
) -> None:
    """A choice refused as ``unreconciled_case`` queues that case's check
    when it gave up, without touching any other work (amendment 4)."""
    library = recheck_library
    connection = library.root._connection
    _save(library, _WIDE.value, at=40)
    connection.execute(
        "UPDATE review_preparation_work SET state='failed',failure_code='projection' "
        "WHERE item_id='item-1'"
    )
    connection.commit()
    assert matching_check_counts(connection, _WIDE.value) == (1, 0, 1)
    # The case says its check gave up, by the rule Policy counts it with.
    case_id = library.case_id()
    given_up = InspectCurrentReviewCase(library.root).run(case_id)
    assert (given_up["checking"], given_up["check_given_up"]) == (True, True)
    assert matching_check_given_up(connection, _WIDE.value, case_id)
    case = library.case()
    destination = next(
        str(value["destination"]) for value in case.proposals if decision_actions(value)
    )
    with pytest.raises(Exception) as refused:
        library.root.record_human_decision(
            _accept(case, destination, "refused-key-0001", 42)
        )
    assert getattr(refused.value, "reason", None) == "unreconciled_case"
    assert connection.execute("SELECT count(*) FROM human_decisions").fetchone() == (0,)
    assert _work(library) == ("projection", "queued")
    assert matching_check_counts(connection, _WIDE.value) == (1, 1, 0)
    # Queued again, it waits: the case no longer says it gave up.
    queued = InspectCurrentReviewCase(library.root).run(case_id)
    assert (queued["checking"], queued["check_given_up"]) == (True, False)
    assert not matching_check_given_up(connection, _WIDE.value, case_id)


# --- 17f: never gated at identifier; born reconciled; stuck checks ------------


def test_identifier_cases_without_marker_never_gated(tmp_path: Path) -> None:
    """At ``identifier``, a case with no level row is reconciled: choices
    record and owner writes write, with no check (guard)."""
    store = _store(tmp_path, _IDENTIFIER)
    try:
        assert _marker(store) is None
        assert not case_level_unreconciled(
            store.repository._connection, store.case.review_case_id
        )
        operation = store.plan(_record(store, LIVE_SYNOPSIS, "identifier-choice"))
        assert not store.repository.field_effect_deferred(operation.plan.plan_id)
        assert store.service.apply(operation.plan.plan_id).state == "verified"
        assert _check(store, 130) == "reconciled"
        assert _marker(store) is None
    finally:
        store.close()


def test_policy_shows_stuck_check_line(
    recheck_library: _Library,
    recheck_level: _Level,
) -> None:
    """The server counts a case whose fields wait for a check that gave up as
    stuck, and a save of the same value runs the check again.

    The case's cover comparison is already at the configured level, so only
    the widened selection, which reads the case's marker, finds it (#1355).
    """
    library = recheck_library
    connection = library.root._connection
    case_id = library.case_id()
    recheck_level.value = _WIDE
    _save(library, _WIDE.value, at=45)
    library.now[0] = 46
    library.drain()
    assert not case_level_unreconciled(connection, case_id)
    assert matching_check_counts(connection, _WIDE.value) == (1, 0, 0)
    # The case's fields fall back to identifier and their check gave up at the
    # projection; its cover comparison stays at the wider level.
    connection.execute(
        "UPDATE review_case_level_reconciliation SET reconciled_level='identifier' "
        "WHERE review_case_id=?",
        (case_id,),
    )
    connection.execute(
        "UPDATE review_preparation_work SET state='failed',failure_code='projection',"
        "outcome=NULL WHERE item_id='item-1'"
    )
    connection.commit()
    assert case_level_unreconciled(connection, case_id)
    assert matching_check_counts(connection, _WIDE.value) == (1, 0, 1)
    _save(library, _WIDE.value, at=50)
    assert matching_check_counts(connection, _WIDE.value) == (1, 1, 0)
    library.now[0] = 51
    library.drain()
    assert matching_check_counts(connection, _WIDE.value) == (1, 0, 0)
    assert not case_level_unreconciled(connection, case_id)


# --- Amendment 14: the works list and attention follow the offer ---------------


def test_works_list_and_attention_facts_follow_the_offer(tmp_path: Path) -> None:
    """Each field's stored facts are those of its current offer: the facts the
    reconciliation writes are the offer's, the open-time check derives the same,
    and a field whose offer gains an eligible alternative needs the owner."""
    store = _store(tmp_path, _IDENTIFIER)
    try:
        _change(store, _WIDE, 150)
        _check(store, 151)
        offer = {str(value["destination"]): value for value in offer_of(store)}
        facts = dict(
            store.repository._connection.execute(
                "SELECT destination,has_owner_decision_actions "
                "FROM review_case_field_facts WHERE review_case_id=?",
                (store.case.review_case_id,),
            ).fetchall()
        )
        assert facts == {
            destination: int(bool(decision_actions(proposal)))
            for destination, proposal in offer.items()
        }
    finally:
        store.close()
    # The open-time integrity check derives the same facts from the offer.
    open_repository(tmp_path / "owner.sqlite").close()

    # The SQL reads the offered alternatives: a live result with only the
    # target's own value gains a choice from its level offer.
    connection = __import__("sqlite3").connect(":memory:")
    connection.executescript(
        "CREATE TABLE review_case_level_offers(review_case_id,work_identity_level,"
        "reconciled_level,document);"
        "CREATE TABLE review_case_level_reconciliation(review_case_id,reconciled_level);"
        "CREATE TABLE review_preparation_control(singleton,work_identity_level);"
        "CREATE TABLE proposal_set_browse_facts(proposal_set_id,resolved_policy_id);"
        "CREATE TABLE metadata_policy_resolutions(resolved_policy_id,document);"
        "CREATE TABLE review_cases(review_case_id,proposal_set_id,document);"
        "CREATE TABLE review_case_field_facts(review_case_id,destination,"
        "unresolved_choice,manual_bibliographic_attention,has_owner_decision_actions);"
    )
    live = {
        "destination": "description",
        "outcome": "propose_change",
        "reasons": [],
        "alternatives": [{"assessment": "preserves_target", "correlations": []}],
    }
    connection.execute(
        "INSERT INTO review_cases VALUES('case-1','proposal-1',?)",
        (json.dumps({"results": [live]}),),
    )
    statement = (
        "SELECT "
        + _review_case_field_fact_values_sql("rc.document", offered=True)
        + " FROM review_cases rc,json_each(rc.document,'$.results') result"
    )
    assert connection.execute(statement).fetchall() == [("description", 0, 0, 0)]
    connection.execute(
        "INSERT INTO review_case_level_reconciliation VALUES('case-1',?)",
        (_WIDE.value,),
    )
    connection.execute(
        "INSERT INTO review_case_level_offers VALUES('case-1',?,?,?)",
        (
            _WIDE.value,
            _WIDE.value,
            json.dumps(
                {
                    "fields": [
                        {
                            "destination": "description",
                            "alternatives": [
                                {"assessment": "replaces_target", "correlations": []}
                            ],
                        }
                    ]
                }
            ),
        ),
    )
    assert connection.execute(statement).fetchall() == [("description", 0, 0, 1)]
    # The reconciliation's refresh writes those offered facts.
    refresh_offered_field_facts(connection, "case-1")
    assert connection.execute("SELECT * FROM review_case_field_facts").fetchall() == [
        ("case-1", "description", 0, 0, 1)
    ]
    connection.close()


def test_facts_that_differ_from_live_are_checked_and_kept_by_the_offer(
    tmp_path: Path,
) -> None:
    """A field whose offer reads differently from its live proposal keeps the
    offer's facts: the open-time integrity check accepts them and a policy
    refresh writes them again, so neither reads the live alternatives.

    The case's stored offer at the other wider level offers no description
    where live offers one; the marker moves to that level, as a check would.
    """
    exact = WorkIdentityLevel.TITLE_AND_CREATOR_EXACT.value
    store = level_store(tmp_path / "store")
    connection = store.repository._connection
    case_id = store.case.review_case_id
    try:
        level_set, document = connection.execute(
            "SELECT level_proposal_set_id,document FROM review_case_level_offers "
            "WHERE review_case_id=?",
            (case_id,),
        ).fetchone()
        offer = json.loads(document)
        offer["work_identity_level"] = exact
        for field in offer["fields"]:
            if field["destination"] == "description":
                field["alternatives"] = []
        connection.execute(
            "INSERT INTO review_case_level_offers VALUES(?,?,?,?,?)",
            (case_id, exact, level_set, exact, json.dumps(offer)),
        )
        connection.execute(
            "UPDATE review_case_level_reconciliation SET reconciled_level=? "
            "WHERE review_case_id=?",
            (exact, case_id),
        )
        connection.execute(
            "UPDATE review_preparation_control SET work_identity_level=?", (exact,)
        )
        refresh_offered_field_facts(connection, case_id)
        connection.commit()

        def description_fact() -> int:
            return int(
                connection.execute(
                    "SELECT has_owner_decision_actions FROM review_case_field_facts "
                    "WHERE review_case_id=? AND destination='description'",
                    (case_id,),
                ).fetchone()[0]
            )

        # Live offers a description to choose; the offer offers none.
        assert decision_actions(
            next(p for p in store.case.proposals if p["destination"] == "description")
        )
        assert description_fact() == 0
        (resolved_policy_id,) = connection.execute(
            "SELECT policy.resolved_policy_id FROM proposal_set_browse_facts policy "
            "JOIN review_cases rc ON rc.proposal_set_id=policy.proposal_set_id "
            "WHERE rc.review_case_id=?",
            (case_id,),
        ).fetchone()
        refresh_policy_settled_field_facts(connection, resolved_policy_id)
        connection.commit()
        assert description_fact() == 0
    finally:
        store.close()
    # The integrity check derives the stored facts from the offer, not live.
    open_repository(tmp_path / "store" / "owner.sqlite").close()


def test_sql_and_python_read_the_same_current_offer(tmp_path: Path) -> None:
    """The schema's SQL reads each field's current offer as ``compose_offer``
    does (review round 1, F3), on a case whose offer differs from live: the
    level offers a refused description and the title group's alternatives.
    A substituted field with an eligible alternative needs the owner in both
    (amendment 24): the facts say so, and ``decision_actions`` offers it."""
    store = level_store(tmp_path / "store", live_description=False)
    connection = store.repository._connection
    case_id = store.case.review_case_id
    try:
        live = {str(value["destination"]): value for value in store.case.proposals}
        python = {str(value["destination"]): value for value in offer_of(store)}
        sql = {
            str(destination): json.loads(alternatives)
            for destination, alternatives in connection.execute(
                "SELECT json_extract(result.value,'$.destination'),"
                + _offered_alternatives_sql("rc.review_case_id")
                + " FROM review_cases rc,json_each(rc.document,'$.results') result "
                "WHERE rc.review_case_id=?",
                (case_id,),
            ).fetchall()
        }
        assert sql == {
            destination: proposal["result"]["alternatives"]
            for destination, proposal in python.items()
        }
        differing = {
            destination
            for destination in python
            if python[destination]["result"]["alternatives"]
            != live[destination]["result"]["alternatives"]
        }
        assert {"description", "title", "authors"} <= differing
        facts = dict(
            connection.execute(
                "SELECT destination,has_owner_decision_actions "
                "FROM review_case_field_facts WHERE review_case_id=?",
                (case_id,),
            ).fetchall()
        )
        assert facts == {
            destination: int(bool(decision_actions(proposal)))
            for destination, proposal in python.items()
        }
        assert decision_actions(live["description"]) == ()
        assert facts["description"] == 1
    finally:
        store.repository.close()
    open_repository(tmp_path / "store" / "owner.sqlite").close()
