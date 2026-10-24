"""Change, withdraw and reselection against a real store (#1285, ADR-0085).

Each refusal is asserted with nothing appended, and each write with the fake
library's request count, so a command that is refused is shown to have changed
nothing anywhere.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_SOURCE_SQL,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.human_decision import HumanDecisionError
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import MetadataReport
from tests.support.genre_reports import genre_report, publish_genre_case
from tests.support.owner_values import OwnerStore, rewritten

ORCHARD = {"kind": "text", "value": "The Silent Orchard"}
_FACTS_SQL = (
    "SELECT connection_name,library_name,item_id,report_id,destination,"
    "cover_consideration_id,cover_policy_reference,cover_refresh_admits "
    "FROM review_attention_facts"
)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[OwnerStore]:
    value = OwnerStore(tmp_path, genre_report())
    yield value
    value.close()


def _rows(store: OwnerStore) -> int:
    return store.repository._connection.execute(
        "SELECT (SELECT count(*) FROM human_decisions)"
        "+(SELECT count(*) FROM human_decision_events)"
    ).fetchone()[0]


def _refused(store: OwnerStore, code: str, *args, **kwargs) -> None:
    before = _rows(store)
    with pytest.raises(HumanDecisionError) as error:
        store.decide(*args, **kwargs)
    assert error.value.code == code
    assert _rows(store) == before


def _field(store: OwnerStore, destination: str) -> dict[str, object]:
    detail = InspectCurrentReviewCase(store.repository).run(store.case.review_case_id)
    return next(
        value for value in detail["fields"] if value["destination"] == destination
    )


def _successor(store: OwnerStore, **values: str | tuple[str, ...]) -> None:
    """Read the work back after a write: a new current case with these values."""
    store.publish(rewritten(genre_report(), **values))


# U6: a change while the earlier effect is in flight keeps today's refusal.
@pytest.mark.parametrize(
    "outcome,value",
    [
        ("owner_entered", {"kind": "text", "value": "Another Orchard"}),
        ("withdraw", None),
    ],
)
def test_a_change_while_the_write_is_in_flight_is_already_decided(
    store: OwnerStore, outcome: str, value: object
) -> None:
    operation = store.plan(store.decide("owner_entered", ORCHARD))
    refusals: list[str] = []

    def change(_plan) -> None:
        before = _rows(store)
        try:
            store.decide(outcome, cast("dict[str, object] | None", value))
        except HumanDecisionError as error:
            refusals.append(error.code)
        assert _rows(store) == before

    store.target.before_apply = change
    assert store.service.apply(operation.plan.plan_id).state == "verified"
    assert refusals == ["already_decided"]


# U7: withdraw supersedes a planned write and never hands the field back to
# automatic handling.
def test_withdraw_supersedes_a_planned_write_and_rearms_nothing(
    store: OwnerStore,
) -> None:
    decision = store.decide("owner_entered", ORCHARD)
    operation = store.plan(decision)
    automatic = store.repository._connection.execute(
        "SELECT count(*) FROM automatic_decisions"
    ).fetchone()
    assert _field(store, "title")["owner_control"]["withdraw"] == "available"

    store.decide("withdraw")

    superseded = store.repository.get_field_effect(operation.plan.plan_id)
    assert (superseded.state, superseded.failure) == (
        "superseded",
        "authority_superseded",
    )
    assert store.service.apply(operation.plan.plan_id).state == "superseded"
    assert store.patches == []
    assert (
        store.repository._connection.execute(
            "SELECT count(*) FROM automatic_decisions"
        ).fetchone()
        == automatic
    )
    assert not [
        document
        for document in store.repository.pending_field_effect_decision_documents(16)
        if json.loads(document)["destination"] == "title"
    ]
    title = _field(store, "title")
    assert title["human_decision"] is None
    assert title["owner_control"]["withdraw"] is None


def test_withdraw_refuses_in_its_fixed_order(store: OwnerStore) -> None:
    # Nothing recorded, and nothing left once withdrawn.
    _refused(store, "nothing_to_withdraw", "withdraw")
    store.decide("owner_entered", ORCHARD)
    store.decide("withdraw")
    _refused(store, "nothing_to_withdraw", "withdraw")
    # A written decision stays; going back is choosing the earlier value again.
    store.write(store.decide("owner_entered", ORCHARD))
    _refused(store, "decision_written", "withdraw")
    assert _field(store, "title")["owner_control"]["withdraw"] == "written"
    # Stale outranks every later code: the earlier case no longer holds.
    earlier = store.case
    _successor(store, title="The Silent Orchard")
    _refused(store, "stale", "withdraw", case=earlier)
    _refused(store, "nothing_to_withdraw", "withdraw")


def test_withdraw_in_flight_is_already_decided(store: OwnerStore) -> None:
    operation = store.plan(store.decide("owner_entered", ORCHARD))
    codes: list[str] = []

    def withdraw(_plan) -> None:
        try:
            store.decide("withdraw")
        except HumanDecisionError as error:
            codes.append(error.code)
        assert _field(store, "title")["owner_control"]["withdraw"] == "in_progress"

    store.target.before_apply = withdraw
    assert store.service.apply(operation.plan.plan_id).state == "verified"
    assert codes == ["already_decided"]


def test_an_automatic_hold_outranks_a_written_decision(tmp_path: Path) -> None:
    store = OwnerStore(tmp_path, None)
    try:
        case_id, _ = publish_genre_case(store.repository, genre_report())
        store.load(case_id)
        assert store.repository._connection.execute(
            "SELECT count(*) FROM automatic_decisions WHERE destination='genres'"
        ).fetchone() == (1,)
        store.write(
            store.decide(
                "owner_entered",
                {"kind": "list", "value": ["Fantasy"]},
                destination="genres",
            )
        )
        _refused(store, "automatic_hold", "withdraw", destination="genres")
        assert _field(store, "genres")["owner_control"]["withdraw"] == "automatic_hold"
    finally:
        store.close()


@pytest.mark.parametrize("destination", ["cover", "description"])
def test_withdraw_is_unsupported_for_cover_and_description(
    store: OwnerStore, destination: str
) -> None:
    _refused(store, "unsupported", "withdraw", destination=destination)


# U8: a withdrawn actionable field asks for the owner again, and the
# trigger-maintained facts equal their canonical recomputation at every step.
def test_withdraw_returns_the_field_to_attention(store: OwnerStore) -> None:
    connection = store.repository._connection

    def facts() -> list[tuple[object, ...]]:
        rows = sorted(connection.execute(_FACTS_SQL).fetchall(), key=repr)
        assert rows == sorted(
            connection.execute(_ATTENTION_FACT_SOURCE_SQL).fetchall(), key=repr
        )
        return rows

    def asking() -> set[object]:
        return {row[4] for row in facts()}

    before = store.repository.count_current_attention_fields(None)
    assert "authors" in asking()
    store.decide(
        "accept", {"kind": "list", "value": ["Example, Ada"]}, destination="authors"
    )
    assert "authors" not in asking()
    assert store.repository.count_current_attention_fields(None) == before - 1
    store.decide("withdraw", destination="authors")
    assert "authors" in asking()
    assert store.repository.count_current_attention_fields(None) == before


# U9: the value a field held before its latest verified write is offered as
# one more suggestion, and choosing it is an ordinary owner-entered write.
def _suggestions(store: OwnerStore) -> list[dict[str, object]]:
    entry = _field(store, "title")["owner_control"]["entry"]
    return cast("list[dict[str, object]]", entry["suggestions"])


def test_the_previous_value_is_offered_after_a_write(store: OwnerStore) -> None:
    assert store.previous() == {}
    assert {"rule": "previous", "value": "Real Tigers"} not in _suggestions(store)
    store.write(store.decide("owner_entered", ORCHARD))
    # On the writing case the field still reads as before the write, so the
    # earlier value is the current one and is not suggested.
    assert store.previous() == {"title": "Real Tigers"}
    assert {"rule": "previous", "value": "Real Tigers"} not in _suggestions(store)
    _successor(store, title="The Silent Orchard")
    assert _suggestions(store)[-1] == {"rule": "previous", "value": "Real Tigers"}


def test_reselecting_the_previous_value_writes_it_back_under_the_guards(
    store: OwnerStore,
) -> None:
    store.write(store.decide("owner_entered", ORCHARD))
    earlier = store.case
    _successor(store, title="The Silent Orchard")
    back = {"kind": "text", "value": "Real Tigers"}
    # A panel left open on the earlier case is stale, and nothing is appended.
    _refused(store, "stale", "owner_entered", ORCHARD | {"value": "X"}, case=earlier)
    operation = store.plan(store.decide("owner_entered", back))
    assert (operation.plan.pre_value, operation.plan.desired_value) == (
        "The Silent Orchard",
        "Real Tigers",
    )
    # The library changed before the write: the guard stops it unwritten.
    store.target.drift(operation.plan, "Edited in the library")
    staled = store.service.apply(operation.plan.plan_id)
    assert (staled.state, staled.failure) == ("stale", "target_changed")
    assert operation.plan.plan_id not in store.patches
    _successor(store, title="Edited in the library")
    written = store.write(store.decide("owner_entered", back))
    assert written.plan.desired_value == "Real Tigers"
    assert store.previous() == {"title": "Edited in the library"}


def test_a_stale_plan_wrote_nothing_so_the_earlier_write_stays_latest(
    store: OwnerStore,
) -> None:
    store.write(store.decide("owner_entered", ORCHARD))
    _successor(store, title="The Silent Orchard")
    operation = store.plan(
        store.decide("owner_entered", {"kind": "text", "value": "O"})
    )
    store.target.drift(operation.plan, "Changed elsewhere")
    assert store.service.apply(operation.plan.plan_id).state == "stale"
    assert store.previous() == {"title": "Real Tigers"}


def test_previous_genres_join_the_subset_options(tmp_path: Path) -> None:
    store = OwnerStore(tmp_path, genre_report(current=("Drama", "Mystery")))
    try:
        store.write(
            store.decide(
                "owner_entered",
                {"kind": "list", "value": ["Drama"]},
                destination="genres",
            )
        )
        store.publish(
            rewritten(genre_report(current=("Drama", "Mystery")), genres=("Drama",))
        )
        options = _field(store, "genres")["owner_control"]["subset"]["options"]
        assert options == [
            {"value": "Drama", "current": True, "proposed": False, "previous": False},
            {"value": "Fantasy", "current": False, "proposed": True, "previous": False},
            {"value": "Mystery", "current": False, "proposed": False, "previous": True},
        ]
        _refused(
            store,
            "owner_value_invalid",
            "owner_entered",
            {"kind": "list", "value": ["Drama", "Other"]},
            destination="genres",
        )
        written = store.write(
            store.decide(
                "owner_entered",
                {"kind": "list", "value": ["Drama", "Mystery"]},
                destination="genres",
            )
        )
        assert written.plan.desired_value == ["Drama", "Mystery"]
    finally:
        store.close()


def test_an_empty_pre_image_offers_nothing(store: OwnerStore) -> None:
    store.write(
        store.decide(
            "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
        )
    )
    assert store.previous() == {}
    _successor(store, genres=("Fantasy",))
    options = _field(store, "genres")["owner_control"]
    assert options is None or not any(
        option["previous"]
        for option in (options["subset"] or {"options": []})["options"]
    )


def test_an_automatic_write_offers_its_pre_image_and_a_description_never_does(
    tmp_path: Path,
) -> None:
    store = OwnerStore(tmp_path, None)
    try:
        store.automatic("genres", "replace", _HELD.target.library)
        store.publish(_HELD)
        automatic = next(
            AutomaticDecision.from_json(raw)
            for raw in store.repository.get_review_case_decisions(
                store.case.review_case_id
            )
            if json.loads(raw)["schema_version"] == "automatic-decision-v1"
            and json.loads(raw)["destination"] == "genres"
        )
        store.write(automatic)
        store.write(
            store.decide(
                "accept",
                {"kind": "text", "value": "A qualified description."},
                destination="description",
            )
        )
        assert store.previous() == {"genres": ["Drama"]}
    finally:
        store.close()


def test_a_verified_event_that_disagrees_with_its_plan_offers_nothing(
    store: OwnerStore,
) -> None:
    written = store.write(store.decide("owner_entered", ORCHARD))
    store.repository._connection.executescript(
        "DROP TRIGGER field_effect_events_no_update;"
        "UPDATE field_effect_events SET document=json_set(document,'$.pre_value','Other') "
        f"WHERE plan_id='{written.plan.plan_id}' AND kind='verified';"
    )
    assert store.previous() == {}


# Activity names each new command.
def test_activity_states_owner_entry_and_withdraw(store: OwnerStore) -> None:
    written = store.write(store.decide("owner_entered", ORCHARD))
    _successor(store, title="The Silent Orchard")
    store.decide("owner_entered", {"kind": "text", "value": "Another"})
    store.decide("withdraw")
    target = store.case.target
    events = ContinuousGuardedApplication(
        store.repository,
        {},
        {},
        PersistedFieldEffectAuthority(store.repository),
        ((str(target["connection"]), str(target["library"])),),
        lambda: 2_000,
        lambda *_: None,
        activity_only=True,
    ).activity()["events"]
    changes = [
        (event["state"], event["change"])
        for event in events
        if event["field_label"] == "Title"
    ]
    assert changes == [
        ("withdraw", "Withdrawn"),
        ("owner_entered", "The Silent Orchard → Another"),
        ("verified", "Real Tigers → The Silent Orchard"),
        ("owner_entered", "Real Tigers → The Silent Orchard"),
    ]
    assert written.plan.plan_id in store.patches


# The owner's hold follows the work across its cases (ADR-0085, amending
# ADR-0039): automatic genres handling stays off on every successor until the
# owner returns the field to automatic.
_HELD = genre_report(current=("Drama",), genres=("Fantasy",))


@pytest.fixture
def held(tmp_path: Path) -> Iterator[tuple[OwnerStore, str]]:
    value = OwnerStore(tmp_path, None)
    value.automatic("genres", "replace", _HELD.target.library)
    value.publish(_HELD)
    assert _automatic_genres(value) == [value.case.review_case_id]
    token = value.decide("keep_current", destination="genres").decision_id
    assert _hold(value) == token
    assert _automatic_genres(value) == []
    yield value, token
    value.close()


def _automatic_genres(store: OwnerStore) -> list[str]:
    return [
        document["review_case_id"]
        for document in map(
            json.loads, store.repository.pending_field_effect_decision_documents(32)
        )
        if document["schema_version"].startswith("automatic")
        and document["destination"] == "genres"
    ]


def _hold(store: OwnerStore) -> str | None:
    return store.repository.field_effect_manual_hold_token(
        store.case.review_case_id, "genres"
    )


def test_the_hold_follows_the_work_to_its_successor(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    first = store.case.review_case_id
    store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    assert store.case.review_case_id != first
    assert _hold(store) == token
    assert _automatic_genres(store) == []
    # The successor says so, and offers "Return to automatic" for it.
    field = _field(store, "genres")
    assert (field["manual_hold"], field["human_decision"]) == (True, None)
    assert field["classification"] == "human_recorded"
    store.publish(rewritten(_HELD, publisher="Changed Again"))
    assert _hold(store) == token
    assert _automatic_genres(store) == []
    # The carried rows pass the store's own validation on reopening.
    store.repository.close()
    store.repository = open_repository(store.root / "owner.sqlite")
    assert _hold(store) == token


def test_a_pending_return_is_not_carried_forward(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    scan = store.repository.request_review_scan(
        "primary", "ebooks", "library-ebooks", "a" * 64, store.clock()
    )
    at = store.clock()
    assert store.repository.request_field_effect_manual_return(
        store.case.review_case_id, "genres", token, scan.scan_id, at, at
    )
    store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    assert _hold(store) is None
    assert _automatic_genres(store) == [store.case.review_case_id]


def test_retiring_and_readmitting_the_work_keeps_the_hold(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    first = store.case.review_case_id
    # The providers fall silent: nothing is actionable and the case retires.
    silent = MetadataReport.create(
        target=_HELD.target,
        file=_HELD.file,
        local_claims=(),
        local_reasons=_HELD.local_reasons,
        providers=(),
    )
    assert store.admit(silent) == "retired"
    assert store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    assert store.case.review_case_id != first
    assert _hold(store) == token
    assert _automatic_genres(store) == []


def test_returning_the_successor_to_automatic_releases_the_hold(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    successor = store.case.review_case_id
    assert store.repository.clear_field_effect_manual_hold(
        successor, "genres", store.clock(), token
    )
    assert _hold(store) is None
    assert _automatic_genres(store) == [successor]
    assert _field(store, "genres")["manual_hold"] is False
    store.publish(rewritten(_HELD, publisher="Changed Again"))
    assert _hold(store) is None
    assert _automatic_genres(store) == [store.case.review_case_id]


# R4: a case id is a content hash, so a work read back to exactly an earlier
# content admits that earlier case again. Its own hold row takes the work's
# latest hold state, so a hold released or returning since never resurrects.
def test_a_released_hold_stays_released_when_an_earlier_case_recurs(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    first = store.case.review_case_id
    store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    successor = store.case.review_case_id
    assert store.repository.clear_field_effect_manual_hold(
        successor, "genres", store.clock(), token
    )
    assert _automatic_genres(store) == [successor]
    store.publish(_HELD)
    assert store.case.review_case_id == first
    assert _hold(store) is None
    assert _automatic_genres(store) == [first]
    # Its earlier decision still reads as before (ADR-0085 records the gap).
    # The rewritten row passes the store's own validation on reopening.
    store.repository.close()
    store.repository = open_repository(store.root / "owner.sqlite")
    assert _hold(store) is None


def test_a_pending_return_reaches_an_earlier_case_that_recurs(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    first = store.case.review_case_id
    store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    scan = store.repository.request_review_scan(
        "primary", "ebooks", "library-ebooks", "a" * 64, store.clock()
    )
    at = store.clock()
    assert store.repository.request_field_effect_manual_return(
        store.case.review_case_id, "genres", token, scan.scan_id, at, at
    )
    store.publish(_HELD)
    assert store.case.review_case_id == first
    row = store.repository._connection.execute(
        "SELECT active,document FROM field_effect_manual_holds "
        "WHERE review_case_id=? AND destination='genres'",
        (first,),
    ).fetchone()
    assert row[0] == 1
    assert json.loads(row[1])["pending_return"]["scan_id"] == scan.scan_id
    store.repository.close()
    store.repository = open_repository(store.root / "owner.sqlite")


def test_an_earlier_case_that_recurs_keeps_a_hold_still_in_force(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    first = store.case.review_case_id
    store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    store.publish(_HELD)
    assert store.case.review_case_id == first
    assert _hold(store) == token
    assert _automatic_genres(store) == []


def test_a_decision_on_a_recurring_case_without_automatic_handling_is_planned(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    # Genres turns to Ask, so neither later case has an automatic genres
    # decision; each carries the hold.
    automatic = MetadataPolicyProfile.from_data(
        json.loads(store.repository.active_metadata_policy())
    )
    ask = MetadataPolicyProfile.create(
        "Ask genres",
        dict(automatic.options),
        dict(automatic.destinations) | {"genres": "ask"},
    )
    store.resolved = resolve_policy(ask, _HELD.target.library)
    store.repository.create_metadata_policy_draft(ask.canonical_json)
    store.repository.activate_metadata_policy(
        ask.policy_id,
        automatic.policy_id,
        (
            (
                _HELD.target.library,
                store.resolved.resolved_id,
                store.resolved.canonical_json,
            ),
        ),
        store.clock(),
        expected_active_generation=2,
    )
    earlier = store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    store.publish(rewritten(_HELD, publisher="Changed Again"))
    assert store.repository.clear_field_effect_manual_hold(
        store.case.review_case_id, "genres", store.clock(), token
    )
    assert store.publish(rewritten(_HELD, publisher="Changed Elsewhere")) == earlier
    assert _hold(store) is None
    assert _automatic_genres(store) == []
    # The owner's accept on the recurring case still becomes a write.
    decision = store.decide(
        "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
    )
    pending = map(
        json.loads, store.repository.pending_field_effect_decision_documents(32)
    )
    assert decision.decision_id in {document["decision_id"] for document in pending}
    assert store.write(decision).plan.desired_value == ["Fantasy"]
    assert _hold(store) == decision.decision_id
