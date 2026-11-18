"""Change, withdraw and reselection against a real store (#1285, ADR-0085).

Each refusal is asserted with nothing appended, and each write with the fake
library's request count, so a command that is refused is shown to have changed
nothing anywhere.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import FakeCapabilityObserver
from mechanical_librarian.adapters.persistence.activity_facts import ActivityFactStore
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_SOURCE_SQL,
)
from mechanical_librarian.adapters.persistence.schema import _HUMAN_DECISIONS_SQL
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.review_web import (
    InspectCurrentReviewCase,
    ReviewWebError,
)
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    FieldEffectError,
    FieldEffectOperation,
)
from mechanical_librarian.domain.human_decision import HumanDecision, HumanDecisionError
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyProfile,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import ClaimScope, MetadataReport
from tests.support.genre_reports import genre_report, publish_genre_case
from tests.support.owner_values import CAPABILITY, OwnerStore, rewritten
from tests.support.proposal_evidence import _local_subtitle

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

    # The ISBN asks for its edition to be verified; the authors are one
    # source's suggestion, which is choosable but does not ask (#1310).
    before = store.repository.count_current_attention_fields(None)
    assert asking() == {"isbn"}
    store.decide("keep_current", destination="isbn")
    assert "isbn" not in asking()
    assert store.repository.count_current_attention_fields(None) == before - 1
    store.decide("withdraw", destination="isbn")
    assert "isbn" in asking()
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
    # On the writing case the written value is current at once, so the value
    # it replaced is suggested with no scan (#1523, S5).
    assert store.previous() == {"title": "Real Tigers"}
    assert _suggestions(store)[-1] == {"rule": "previous", "value": "Real Tigers"}
    assert _field(store, "title")["owner_control"]["current"] == ORCHARD["value"]
    _successor(store, title="The Silent Orchard")
    assert _suggestions(store)[-1] == {"rule": "previous", "value": "Real Tigers"}


# #1523 S5: going back on the case that was just written is an ordinary
# owner-entered decision through the existing correction path.
def test_going_back_on_the_written_case_writes_the_previous_value(
    store: OwnerStore,
) -> None:
    written = store.write(store.decide("owner_entered", ORCHARD))
    deciding = store.case.review_case_id
    # The written value is the one refused as unchanged.
    _refused(store, "owner_value_unchanged", "owner_entered", ORCHARD)
    back = store.decide("owner_entered", {"kind": "text", "value": "Real Tigers"})
    assert store.case.review_case_id == deciding
    operation = store.write(back)
    assert (operation.plan.pre_value, operation.plan.desired_value) == (
        written.plan.desired_value,
        "Real Tigers",
    )
    assert store.target.read(operation.plan).value == "Real Tigers"
    # Now the value written last is current, and the one before it is offered.
    assert _field(store, "title")["owner_control"]["current"] == "Real Tigers"
    assert store.previous() == {"title": "The Silent Orchard"}
    assert _suggestions(store)[-1] == {
        "rule": "previous",
        "value": "The Silent Orchard",
    }


def test_going_back_while_the_write_is_in_flight_refuses_as_today(
    store: OwnerStore,
) -> None:
    # Until the write is verified the case reads its target, so the earlier
    # value is still the current one; another value meets `already_decided`.
    operation = store.plan(store.decide("owner_entered", ORCHARD))
    refusals: list[str] = []

    def back(_plan) -> None:
        before = _rows(store)
        try:
            store.decide("owner_entered", {"kind": "text", "value": "Real Tigers"})
        except HumanDecisionError as error:
            refusals.append(error.code)
        assert _rows(store) == before

    store.target.before_apply = back
    assert store.service.apply(operation.plan.plan_id).state == "verified"
    assert refusals == ["owner_value_unchanged"]
    assert store.patches == [operation.plan.plan_id]


def test_going_back_after_an_external_change_stales_unwritten(
    store: OwnerStore,
) -> None:
    store.write(store.decide("owner_entered", ORCHARD))
    operation = store.plan(
        store.decide("owner_entered", {"kind": "text", "value": "Real Tigers"})
    )
    assert operation.plan.pre_value == "The Silent Orchard"
    store.target.drift(operation.plan, "Edited in the library")
    staled = store.service.apply(operation.plan.plan_id)
    assert (staled.state, staled.failure) == ("stale", "target_changed")


# #1523 S6: on a successor the earlier value is offered only while the field
# still reads what the latest write left.
def test_a_successor_changed_elsewhere_offers_no_previous_value(
    store: OwnerStore,
) -> None:
    store.write(store.decide("owner_entered", ORCHARD))
    _successor(store, title="Edited in the library")
    assert store.previous() == {"title": "Real Tigers"}
    assert {"rule": "previous", "value": "Real Tigers"} not in _suggestions(store)
    assert _field(store, "title")["owner_control"]["current"] == (
        "Edited in the library"
    )


# #1523, amendment 1: after a write that filled an empty subtitle, emptying
# the input is going back. No empty suggestion is sent; the clear is recorded
# on the writing case and written through the guarded path.
def test_a_clear_after_a_fill_goes_back_to_the_empty_field(tmp_path: Path) -> None:
    base = genre_report()
    report = MetadataReport.create(
        target=base.target,
        file=base.file,
        local_claims=(
            *base.local_claims,
            _local_subtitle("A Quiet Subtitle", ClaimScope.WORK),
        ),
        local_reasons=base.local_reasons,
        providers=base.providers,
    )
    store = OwnerStore(tmp_path, report)
    try:
        filled = store.write(
            store.decide(
                "accept",
                {"kind": "text", "value": "A Quiet Subtitle"},
                destination="subtitle",
            )
        )
        assert filled.plan.pre_value is None
        assert store.previous() == {}
        control = _field(store, "subtitle")["owner_control"]
        assert (control["clear"], control["current"]) == (True, "A Quiet Subtitle")
        assert all(
            item["rule"] != "previous" for item in control["entry"]["suggestions"]
        )
        clear = store.decide(
            "owner_entered", {"kind": "empty", "value": None}, destination="subtitle"
        )
        written = store.write(clear)
        assert (written.plan.pre_value, written.plan.desired_value) == (
            "A Quiet Subtitle",
            None,
        )
        assert store.target.read(written.plan).value is None
        assert (
            store.repository.field_effect_manual_hold_token(
                store.case.review_case_id, "subtitle"
            )
            == clear.decision_id
        )
        # History and Activity read the clear as a removal, never as a value.
        history = InspectCurrentReviewCase(store.repository).history(
            store.case.review_case_id
        )
        assert [
            (item["outcome"], item["previous_value"], item["selected_value"])
            for item in history["items"]
            if item.get("destination") == "subtitle"
            and item.get("outcome") == "owner_entered"
        ] == [
            (
                "owner_entered",
                {"kind": "text", "value": "A Quiet Subtitle"},
                {"kind": "empty", "value": None},
            )
        ]
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
        cleared = [
            (event["state"], event["change"], event["evidence"])
            for event in events
            if event["field_label"] == "Subtitle"
            and event["change"] == "Cleared subtitle"
        ]
        assert [state for state, *_ in cleared] == ["verified", "owner_entered"]
        assert all(
            {"label": "New value", "value": "Not set"} in evidence
            for *_, evidence in cleared
        )
        # Cleared again, the field is empty, so another clear is unchanged.
        _refused(
            store,
            "owner_value_unchanged",
            "owner_entered",
            {"kind": "empty", "value": None},
            destination="subtitle",
        )
    finally:
        store.close()


def _pending(store: OwnerStore) -> list[str]:
    return [
        json.loads(document)["decision_id"]
        for document in store.repository.pending_field_effect_decision_documents(50)
    ]


# #1523: on the case that wrote a field the case read empty, Keep current
# goes back to empty. A clearable field writes the empty value through the
# guarded path, as a clear does; any other field refuses the keep.
def test_keeping_an_empty_genres_after_a_write_goes_back_to_empty(
    store: OwnerStore,
) -> None:
    written = store.write(
        store.decide(
            "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
        )
    )
    assert (written.plan.pre_value, written.plan.desired_value) == (None, ["Fantasy"])
    keep = store.decide("keep_current", destination="genres")
    assert keep.decision_id in _pending(store)
    back = store.write(keep)
    assert (back.plan.pre_value, back.plan.desired_value) == (["Fantasy"], None)
    assert store.target.read(back.plan).value is None
    assert (
        store.repository.field_effect_manual_hold_token(
            store.case.review_case_id, "genres"
        )
        == keep.decision_id
    )
    assert keep.decision_id not in _pending(store)
    # Activity says the keep went back on the write, never that it kept the
    # current value (#1523).
    (row,) = (
        event
        for event in _activity(store)
        if event["field_label"] == "Genres"
        and event["state"] in {"keep_current", "went_back"}
    )
    assert (row["state"], row["change"], row["change_detail"]) == (
        "went_back",
        "Fantasy → Not set",
        "Set Genres back from Fantasy to Not set",
    )
    assert row["evidence"][:2] == [
        {"label": "Written value", "value": "Fantasy"},
        {"label": "Value before your change", "value": "Not set"},
    ]
    assert "Kept" not in row["change"] and "Current value" not in str(row["evidence"])


def _activity(store: OwnerStore) -> list[dict[str, Any]]:
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
    return cast("list[dict[str, Any]]", events)


def _genres_keep(rows: list[dict[str, Any]]) -> dict[str, Any]:
    (row,) = (
        event
        for event in rows
        if event["field_label"] == "Genres"
        and event["category"] == "owner"
        and event["change"] == "Fantasy → Not set"
    )
    return row


def test_a_keep_reads_went_back_only_once_its_write_back_verifies(
    store: OwnerStore,
) -> None:
    """Not planned, it is recorded; planned, it is planned; verified, it went
    back. Every step reads as the change it makes, never Kept current, with
    the written value its plan or the write it follows names (#1523)."""
    store.write(
        store.decide(
            "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
        )
    )
    keep = store.decide("keep_current", destination="genres")
    for step in ("recorded", "planned", "went_back"):
        if step == "planned":
            store.plan(keep)
        elif step == "went_back":
            store.service.apply(
                store.repository._connection.execute(
                    "SELECT plan_id FROM field_effects WHERE decision_id=?",
                    (keep.decision_id,),
                ).fetchone()[0]
            )
        row = _genres_keep(_activity(store))
        assert row["state"] == step
        assert row["evidence"][:2] == [
            {"label": "Written value", "value": "Fantasy"},
            {"label": "Value before your change", "value": "Not set"},
        ]
        assert "Kept" not in str(row) and "Current value" not in str(row)


def _served(store: OwnerStore, borrower: Any) -> list[dict[str, Any]]:
    target = store.case.target
    return cast(
        "list[dict[str, Any]]",
        ContinuousGuardedApplication(
            borrower,
            {},
            {},
            PersistedFieldEffectAuthority(borrower),
            ((str(target["connection"]), str(target["library"])),),
            lambda: 2_000,
            lambda *_: None,
            activity_only=True,
        ).activity()["events"],
    )


def _genres_owner(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [
        (str(event["state"]), str(event["change"]))
        for event in rows
        if event["field_label"] == "Genres" and event["category"] == "owner"
    ]


@pytest.fixture
def served(store: OwnerStore) -> Iterator[Callable[[], list[dict[str, Any]]]]:
    """Activity as a borrowed fact store serves it (ADR-0082), checked against
    the canonical read at every call."""
    borrower = open_repository(
        store.root / "owner.sqlite",
        create=False,
        authority=store.repository.authority,
    )
    borrower.configure_activity_page_reader(ActivityFactStore())

    def read() -> list[dict[str, Any]]:
        rows = _served(store, borrower)
        assert rows == _activity(store)
        return rows

    try:
        yield read
    finally:
        borrower.close()


def test_the_served_activity_follows_a_keep_write_back(
    store: OwnerStore, served: Callable[[], list[dict[str, Any]]]
) -> None:
    """The served Activity re-reads an owner decision's row when its own write
    moves, as the canonical read does (ADR-0082, #1286, #1523)."""
    store.write(
        store.decide(
            "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
        )
    )
    keep = store.decide("keep_current", destination="genres")
    assert _genres_keep(served())["state"] == "recorded"
    store.write(keep)
    assert _genres_keep(served())["state"] == "went_back"


def test_an_unplanned_keep_reads_its_own_episode_after_the_case_returns(
    store: OwnerStore, served: Callable[[], list[dict[str, Any]]]
) -> None:
    """A keep recorded after a write and never planned keeps reading that
    write once the case returns as a new episode: the library held Fantasy
    when it was recorded (#1358, #1523)."""
    first = store.case.review_case_id
    store.write(
        store.decide(
            "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
        )
    )
    store.decide("keep_current", destination="genres")
    assert _genres_keep(served())["state"] == "recorded"
    _successor(store, genres=("Fantasy",))
    assert store.publish(genre_report()).review_case_id == first
    assert _genres_keep(served())["state"] == "recorded"


def test_a_plain_keep_still_reads_kept_in_activity(store: OwnerStore) -> None:
    keep = store.decide("keep_current", destination="genres")
    assert keep.decision_id not in _pending(store)
    (row,) = (
        event
        for event in _activity(store)
        if event["field_label"] == "Genres" and event["category"] == "owner"
    )
    assert row["state"] == "keep_current"
    assert row["change"] == "Left genres unset"


def test_keeping_an_empty_isbn_after_a_write_is_refused(store: OwnerStore) -> None:
    written = store.write(
        store.decide(
            "accept", {"kind": "text", "value": "9780306406157"}, destination="isbn"
        )
    )
    assert written.plan.pre_value is None
    effects = store.repository._connection.execute(
        "SELECT count(*) FROM field_effects"
    ).fetchone()
    pending = _pending(store)
    _refused(store, "owner_value_required", "keep_current", destination="isbn")
    assert _pending(store) == pending
    assert (
        store.repository._connection.execute(
            "SELECT count(*) FROM field_effects"
        ).fetchone()
        == effects
    )
    assert store.target.read(written.plan).value == "9780306406157"


def test_keeping_an_empty_isbn_before_any_write_is_recorded(
    store: OwnerStore,
) -> None:
    keep = store.decide("keep_current", destination="isbn")
    assert keep.decision_id not in _pending(store)


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
    # The recurring case is a new episode: its earlier keep-current is
    # history, so the field reads as handled automatically and unheld.
    genres = _field(store, "genres")
    assert (genres["manual_hold"], genres["human_decision"]) == (False, None)
    # The rewritten row passes the store's own validation on reopening.
    store.repository.close()
    store.repository = open_repository(store.root / "owner.sqlite")
    assert _hold(store) is None


# #1358: Return to automatic on the case that holds the field releases it
# there, so the case no longer shows the hold its own decision set.
def test_returning_the_holding_case_to_automatic_clears_its_held_flag(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    assert _field(store, "genres")["manual_hold"] is True
    assert store.repository.clear_field_effect_manual_hold(
        store.case.review_case_id, "genres", store.clock(), token
    )
    assert _hold(store) is None
    assert _field(store, "genres")["manual_hold"] is False


def _request_return(store: OwnerStore, token: str) -> tuple[str, int]:
    scan = store.repository.request_review_scan(
        "primary", "ebooks", "library-ebooks", "a" * 64, store.clock()
    )
    at = store.clock()
    assert store.repository.request_field_effect_manual_return(
        store.case.review_case_id, "genres", token, scan.scan_id, at, at
    )
    return scan.scan_id, at


def _held_and_requested(store: OwnerStore) -> tuple[object, object]:
    field = _field(store, "genres")
    return field["manual_hold"], field["manual_return_requested"]


# #1755: the case shows a requested return while the hold waits for its
# checks, so a reload can tell whether "Resume automatic handling" landed.
def test_the_holding_case_shows_a_requested_return_until_it_is_released(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    assert _held_and_requested(store) == (True, False)
    assert _field(store, "genres")["manual_return_failed"] is False
    _request_return(store, token)
    assert _held_and_requested(store) == (True, True)
    assert store.repository.clear_field_effect_manual_hold(
        store.case.review_case_id, "genres", store.clock(), token
    )
    assert _held_and_requested(store) == (False, False)


def test_a_requested_return_shows_on_a_successor_and_on_a_case_that_recurs(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    first = store.case.review_case_id
    store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    assert store.case.review_case_id != first
    assert _held_and_requested(store) == (True, False)
    _request_return(store, token)
    assert _held_and_requested(store) == (True, True)
    store.publish(_HELD)
    assert store.case.review_case_id == first
    assert _held_and_requested(store) == (True, True)
    store.repository.close()
    store.repository = open_repository(store.root / "owner.sqlite")
    assert _held_and_requested(store) == (True, True)


# The copy for an unconfirmed request says requesting again is safe: a second
# request on the same hold is accepted, keeps one active row and re-points it.
def test_requesting_a_return_again_on_the_same_hold_is_safe(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    first = _request_return(store, token)
    second = _request_return(store, token)
    assert second[1] > first[1]
    rows = store.repository._connection.execute(
        "SELECT document FROM field_effect_manual_holds "
        "WHERE review_case_id=? AND destination='genres' AND active=1",
        (store.case.review_case_id,),
    ).fetchall()
    assert len(rows) == 1
    document = json.loads(rows[0][0])
    assert document["pending_return"] == {
        "requested_at": second[1],
        "scan_id": second[0],
    }
    assert document["decision_id"] == token
    assert _hold(store) == token
    assert _held_and_requested(store) == (True, True)


# A requested scan that finishes with failures keeps the hold, and nothing
# retries it. The case must stop saying "requested", so the owner can ask
# again; asking again requeues that scan and reads as requested (#1755).
def test_a_requested_return_whose_checks_fail_can_be_requested_again(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    scan_id, _ = _request_return(store, token)
    field = _field(store, "genres")
    assert (field["manual_return_requested"], field["manual_return_failed"]) == (
        True,
        False,
    )
    store.repository._connection.execute(
        "UPDATE review_preparation_scans SET state='completed_with_failures' "
        "WHERE scan_id=?",
        (scan_id,),
    )
    store.repository._connection.commit()
    field = _field(store, "genres")
    assert field["manual_hold"] is True
    assert (field["manual_return_requested"], field["manual_return_failed"]) == (
        False,
        True,
    )
    again, _ = _request_return(store, token)
    assert again == scan_id
    assert store.repository._connection.execute(
        "SELECT state FROM review_preparation_scans WHERE scan_id=?", (scan_id,)
    ).fetchone() == ("queued",)
    field = _field(store, "genres")
    assert (field["manual_return_requested"], field["manual_return_failed"]) == (
        True,
        False,
    )


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


_FANTASY = {"kind": "list", "value": ["Fantasy"]}


def _genres_policy(store: OwnerStore, destination: str, generation: int) -> None:
    """Turn genres to `destination` for later reads of the work."""
    active = MetadataPolicyProfile.from_data(
        json.loads(store.repository.active_metadata_policy())
    )
    profile = MetadataPolicyProfile.create(
        f"Genres {destination}",
        dict(active.options),
        dict(active.destinations) | {"genres": destination},
    )
    resolved = resolve_policy(profile, _HELD.target.library)
    store.resolved = resolved
    store.repository.create_metadata_policy_draft(profile.canonical_json)
    store.repository.activate_metadata_policy(
        profile.policy_id,
        active.policy_id,
        ((_HELD.target.library, resolved.resolved_id, resolved.canonical_json),),
        store.clock(),
        expected_active_generation=generation,
    )


def _recur_after_release(
    store: OwnerStore, token: str, before_release: str | None = None
) -> None:
    """Genres turns to Ask, so no later case has an automatic genres decision
    and each carries the hold; the work reads A, then B, where the hold is
    released, then A again. `before_release` is recorded on A first."""
    _genres_policy(store, "ask", 2)
    earlier = store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    if before_release is not None:
        # Every owner answer holds the field (ADR-0085, Amendment 1), so
        # this answer's hold is the one B carries and the one released.
        token = store.decide(before_release, _FANTASY, destination="genres").decision_id
    store.publish(rewritten(_HELD, publisher="Changed Again"))
    assert _hold(store) == token
    assert store.repository.clear_field_effect_manual_hold(
        store.case.review_case_id, "genres", store.clock(), token
    )
    assert store.publish(rewritten(_HELD, publisher="Changed Elsewhere")) == earlier
    assert _hold(store) is None
    assert _automatic_genres(store) == []


def test_a_decision_on_a_recurring_case_without_automatic_handling_is_planned(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    _recur_after_release(store, token)
    # The owner's accept on the recurring case still becomes a write.
    decision = store.decide("accept", _FANTASY, destination="genres")
    pending = map(
        json.loads, store.repository.pending_field_effect_decision_documents(32)
    )
    assert decision.decision_id in {document["decision_id"] for document in pending}
    assert store.write(decision).plan.desired_value == ["Fantasy"]
    # With nothing automatic on the case, the answer still holds the field,
    # replacing the released row (ADR-0085, Amendment 1).
    assert _hold(store) == decision.decision_id


def _automatic_genres_again(store: OwnerStore, later: MetadataReport) -> None:
    """Genres turns back to Automatic and the work is read as `later`."""
    # The Ask transition's work item went stale as the work changed under
    # it; the owner acknowledges that before the next policy is activated.
    transition = store.repository.metadata_policy_transition()
    assert transition is not None
    store.repository.acknowledge_metadata_policy_transition(
        transition.transition_id, store.clock()
    )
    _genres_policy(store, "automatic", 3)
    store.publish(later)


# Amendment 1: that answer holds the field on the work's later cases, so a
# successor read under automatic handling keeps the owner's genres.
def test_a_decision_on_a_recurring_case_holds_its_field_on_later_cases(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    _recur_after_release(store, token)
    decision = store.decide("accept", _FANTASY, destination="genres")
    store.write(decision)
    _automatic_genres_again(
        store, rewritten(_HELD, publisher="Changed Elsewhere", genres=("Fantasy",))
    )
    assert _hold(store) == decision.decision_id
    assert _automatic_genres(store) == []


# R-A: a withdrawn answer takes its hold with it, and the released hold is
# not restored, so a successor read under automatic handling is handled
# automatically.
def test_a_withdrawn_decision_on_a_recurring_case_carries_no_hold_forward(
    held: tuple[OwnerStore, str],
) -> None:
    store, token = held
    # The accept recorded before the release is the earlier episode's
    # (#1358), so the owner accepts and withdraws within this one.
    _recur_after_release(store, token, before_release="accept")
    store.decide("accept", _FANTASY, destination="genres")
    store.decide("withdraw", destination="genres")
    assert _hold(store) is None
    _automatic_genres_again(store, rewritten(_HELD, publisher="Changed Later"))
    assert _hold(store) is None
    assert _automatic_genres(store) == [store.case.review_case_id]


# R11 F-A: Resume releases a hold only where the case shows it, so the API
# cannot release a hold that holds nothing and let an older copy return.
def _resume_service(store: OwnerStore, tmp_path: Path, completed: list[int]):
    import os
    from types import SimpleNamespace

    from argon2 import PasswordHasher

    from mechanical_librarian.adapters.field_effect_fakes import FakeCapabilityObserver
    from mechanical_librarian.application.configuration import ReviewWebConfiguration
    from tests.support.owner_values import CAPABILITY
    from tests.unit.review_app_fixture import create_transport_test_app

    context = ("primary", "ebooks")
    scans = []

    def request_scan(connection: str, library: str):
        scans.append((connection, library))
        return store.repository.request_review_scan(
            connection, library, "library-ebooks", "a" * 64, store.clock()
        )

    scheduler = ContinuousGuardedApplication(
        store.repository,
        {context: store.service},
        {context: FakeCapabilityObserver(CAPABILITY)},
        PersistedFieldEffectAuthority(store.repository),
        (context,),
        store.clock,
        request_scan,
        scan_progress=lambda *_: SimpleNamespace(
            scan_id=store.repository._connection.execute(
                "SELECT scan_id FROM review_preparation_scans"
            ).fetchone()[0],
            state="completed",
            rescan_requested=False,
            updated_at=completed[0],
        ),
    )
    password = tmp_path / "owner.hash"
    password.write_text(PasswordHasher().hash("correct horse"), encoding="utf-8")
    os.chmod(password, 0o600)
    app = create_transport_test_app(
        store.repository,
        ReviewWebConfiguration(
            tls_certificate_file="/synthetic/review.crt",
            tls_private_key_file="/synthetic/review.key",
            canonical_origin="https://review.test",
            owner_password_hash_file=str(password),
            session_lifetime_seconds=600,
        ),
        clock=store.clock,
        field_application=scheduler,
    )
    return app.state.review_application, scheduler, scans


def _hold_document(store: OwnerStore) -> tuple[int, str] | None:
    return store.repository._connection.execute(
        "SELECT active,document FROM field_effect_manual_holds "
        "WHERE review_case_id=? AND destination='genres'",
        (store.case.review_case_id,),
    ).fetchone()


def test_resume_is_refused_where_the_hold_holds_no_decision(
    held: tuple[OwnerStore, str], tmp_path: Path
) -> None:
    from mechanical_librarian.application.field_effects import GuardedApplicationError

    store, token = held
    # Genres turns to Ask: the successor carries the hold with no automatic
    # decision for it to hold, so no Resume control is shown.
    _genres_policy(store, "ask", 2)
    store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    assert _hold(store) == token
    assert _field(store, "genres")["manual_hold"] is False
    application, _, scans = _resume_service(store, tmp_path, [0])
    before = _hold_document(store)
    with pytest.raises(GuardedApplicationError) as error:
        application.return_to_automatic(store.case.review_case_id, "genres")
    assert error.value.code == "manual_hold"
    # Nothing was requested or released.
    assert scans == []
    assert _hold_document(store) == before


def test_resume_shown_on_a_successor_requests_a_return_that_completes(
    held: tuple[OwnerStore, str], tmp_path: Path
) -> None:
    store, token = held
    store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    assert _field(store, "genres")["manual_hold"] is True
    completed = [0]
    application, scheduler, scans = _resume_service(store, tmp_path, completed)
    application.return_to_automatic(store.case.review_case_id, "genres")
    assert scans == [("primary", "ebooks")]
    row = _hold_document(store)
    assert row is not None and row[0] == 1
    assert "pending_return" in json.loads(row[1])
    # The fresh checks complete, and the pending return releases the hold.
    completed[0] = 10**9
    scheduler.schedule()
    assert _hold(store) is None
    assert _automatic_genres(store) == [store.case.review_case_id]


# R11 F-B: a contextual row (a current value, nothing retained to compare)
# offers no controls, so the case projects no owner entry for it and the store
# refuses one; a selectable field with no eligible alternative keeps its entry.
def _record_entry(store: OwnerStore, destination: str, value: str, key: str):
    from mechanical_librarian.application.review_web import RecordHumanDecision

    field = _field(store, destination)
    return RecordHumanDecision(store.repository, clock=store.clock).run(
        store.case.review_case_id,
        proposal_id=str(field["proposal_id"]),
        target_fingerprint=str(field["target_fingerprint"]),
        destination=destination,
        actor="owner",
        outcome="owner_entered",
        selected_value={"kind": "text", "value": value},
        idempotency_key=key,
    )


@pytest.mark.parametrize("destination", ["subtitle", "publisher"])
def test_a_contextual_field_offers_and_takes_no_owner_value(
    tmp_path: Path, destination: str
) -> None:
    store = OwnerStore(tmp_path, rewritten(genre_report(), **{destination: "As Read"}))
    try:
        field = _field(store, destination)
        assert (field["inventory_role"], field["owner_control"]) == ("contextual", None)
        assert "enter your own" not in str(field["classification_reason"])
        before = _rows(store)
        with pytest.raises(ReviewWebError) as error:
            _record_entry(
                store, destination, "Typed Value", f"contextual-{destination}"
            )
        assert error.value.code == "unsupported"
        assert _rows(store) == before
        # A1: the title, selectable with no eligible alternative, is unchanged.
        title = _field(store, "title")
        assert title["inventory_role"] == "primary"
        assert cast("dict[str, object]", title["owner_control"])["entry"] is not None
        assert str(title["classification_reason"]).endswith("You can enter your own.")
        _record_entry(
            store, "title", "The Silent Orchard", f"owner-entry-a1-{destination}"
        )
        assert _rows(store) > before
    finally:
        store.close()


# R11 F-C: two writes verified within one second are ordered as they were
# verified, so Previous value is the first write's value, never the older
# pre-image a content-hashed plan id could pick.
@pytest.mark.parametrize(
    "second", ["Second Orchard", "The Second Orchard", "Orchard Again", "Last Orchard"]
)
def test_previous_value_follows_verification_order_within_one_second(
    tmp_path: Path, second: str
) -> None:
    from unittest.mock import patch

    with patch.object(OwnerStore, "clock", lambda self: 5_000):
        store = OwnerStore(tmp_path, genre_report())
    try:
        first = store.write(store.decide("owner_entered", ORCHARD))
        store.ticks += 1
        _successor(store, title=ORCHARD["value"])
        later = store.write(
            store.decide("owner_entered", {"kind": "text", "value": second})
        )
        assert first.updated_at == later.updated_at == 5_000
        assert store.previous()["title"] == ORCHARD["value"]
    finally:
        store.close()


# #1358: a work read A, then B, then A again re-admits case A as a new
# episode. Its wording, recorded outcome and held flag describe only what the
# owner did since it became current again; earlier decisions stay as history.
_BACK = {"kind": "text", "value": "Real Tigers"}


def _decision_ids(store: OwnerStore) -> set[str]:
    return {
        str(json.loads(document)["decision_id"])
        for document in store.repository.get_review_case_decisions(
            store.case.review_case_id
        )
        if json.loads(document)["schema_version"].startswith("human")
    }


def _recur_by_reselection(store: OwnerStore) -> tuple[str, str]:
    """Write ORCHARD on A, choose the previous value again on B, and read A
    back; return both case ids."""
    first = store.case.review_case_id
    store.write(store.decide("owner_entered", ORCHARD))
    _successor(store, title="The Silent Orchard")
    second = store.case.review_case_id
    store.write(store.decide("owner_entered", _BACK))
    assert store.publish(genre_report()).review_case_id == first
    return first, second


def _asking(store: OwnerStore) -> set[object]:
    connection = store.repository._connection
    rows = sorted(connection.execute(_FACTS_SQL).fetchall(), key=repr)
    assert rows == sorted(
        connection.execute(_ATTENTION_FACT_SOURCE_SQL).fetchall(), key=repr
    )
    return {row[4] for row in rows}


def test_choosing_the_previous_value_again_reads_the_earlier_case_as_new(
    store: OwnerStore,
) -> None:
    before = _asking(store)
    _recur_by_reselection(store)
    title = _field(store, "title")
    # Not "You entered The Silent Orchard", written, while the library holds
    # Real Tigers again.
    assert title["human_decision"] is None
    assert title["owner_control"]["withdraw"] is None
    assert _decision_ids(store) == set()
    assert _asking(store) == before
    _refused(store, "nothing_to_withdraw", "withdraw")
    # The earlier episode stays as history, and the store reopens.
    assert store.repository._connection.execute(
        "SELECT count(*) FROM human_decisions WHERE review_case_id=?",
        (store.case.review_case_id,),
    ).fetchone() == (1,)
    store.repository.close()
    store.repository = open_repository(store.root / "owner.sqlite")
    assert _field(store, "title")["human_decision"] is None


def test_a_decision_on_the_readmitted_case_is_its_own(store: OwnerStore) -> None:
    _recur_by_reselection(store)
    another = {"kind": "text", "value": "Another Orchard"}
    decision = store.decide("owner_entered", another)
    assert _decision_ids(store) == {decision.decision_id}
    title = _field(store, "title")
    assert title["human_decision"]["decision_id"] == decision.decision_id
    assert title["owner_control"]["withdraw"] == "available"
    pending = {
        json.loads(document)["decision_id"]
        for document in store.repository.pending_field_effect_decision_documents(32)
    }
    assert decision.decision_id in pending
    # The earlier episode's write is not what the library holds now.
    plan = store.write(decision).plan
    assert (plan.pre_value, plan.desired_value) == ("Real Tigers", "Another Orchard")


def test_a_later_successor_that_recurs_is_a_new_episode_too(
    store: OwnerStore,
) -> None:
    first, second = _recur_by_reselection(store)
    # A recurring case's own decision is withdrawn within its episode.
    store.decide("owner_entered", {"kind": "text", "value": "Another Orchard"})
    store.decide("withdraw")
    assert _field(store, "title")["human_decision"] is None
    # B recurs: the previous-value write recorded on it is history too.
    store.write(store.decide("owner_entered", ORCHARD))
    _successor(store, title="The Silent Orchard")
    assert store.case.review_case_id == second
    assert _decision_ids(store) == set()
    assert _field(store, "title")["human_decision"] is None
    assert first != second


def test_a_plan_from_the_earlier_episode_never_writes(store: OwnerStore) -> None:
    """A plan made on A before the work left it and came back authorizes
    nothing once A is a new episode, whether or not the owner decides again."""
    first = store.case.review_case_id
    earlier = store.plan(store.decide("owner_entered", ORCHARD))
    _successor(store, publisher="Changed Elsewhere")
    assert store.publish(genre_report()).review_case_id == first
    assert store.service.apply(earlier.plan.plan_id).state != "verified"
    assert earlier.plan.plan_id not in store.patches
    # The owner's choice in this episode is the one that writes.
    another = {"kind": "text", "value": "Another Orchard"}
    written = store.write(store.decide("owner_entered", another))
    assert written.plan.desired_value == "Another Orchard"
    assert store.patches == [written.plan.plan_id]


def test_deciding_again_closes_the_earlier_episodes_plan(store: OwnerStore) -> None:
    first = store.case.review_case_id
    earlier = store.plan(store.decide("owner_entered", ORCHARD))
    _successor(store, publisher="Changed Elsewhere")
    assert store.publish(genre_report()).review_case_id == first
    store.decide("owner_entered", {"kind": "text", "value": "Another Orchard"})
    closed = store.repository.get_field_effect(earlier.plan.plan_id)
    assert (closed.state, closed.failure) == ("superseded", "authority_superseded")


# #1346, K-D19: a pin's history row carries no value, as an unpin's; the
# value a pin keeps is its answer's or its case's.
def test_pin_and_unpin_history_events(store: OwnerStore) -> None:
    pin = store.decide("pin")
    unpin = store.decide("unpin")
    events = {
        event["decision_id"]: event
        for event in store.repository.get_review_case_history(
            store.case.review_case_id, 50
        )
        if event.get("kind") == "human_decision_recorded"
    }
    assert (
        events[pin.decision_id]["outcome"],
        events[pin.decision_id]["selected_value"],
    ) == ("pin", None)
    assert (
        events[unpin.decision_id]["outcome"],
        events[unpin.decision_id]["selected_value"],
    ) == ("unpin", None)


# #1523 review B1: a keep on a case that returned as a new episode (#1358)
# has nothing in its own episode to go back from. It is not pending work, and
# it never stops the scheduler from applying another write.
_KEPT_THEN_RETURNED: dict[
    str,
    tuple[dict[str, object], dict[str, tuple[str, ...]], str, dict[str, object]],
] = {
    # A clearable field the case read empty: filled on it, cleared on the
    # kept case, and read empty again.
    "genres": (
        {"kind": "list", "value": ["Fantasy"]},
        {"genres": ("Fantasy",)},
        "owner_entered",
        {"kind": "empty", "value": None},
    ),
    # A field the case read a value: changed on it, entered back on the kept
    # case, and read with its first value again.
    "authors": (
        {"kind": "list", "value": ["Example, Ada"]},
        {"authors": ("Example, Ada",)},
        "owner_entered",
        {"kind": "list", "value": ["Ada Example"]},
    ),
}


@pytest.mark.parametrize("destination", sorted(_KEPT_THEN_RETURNED))
def test_a_keep_on_a_returned_episode_plans_nothing_and_stops_nothing(
    store: OwnerStore, destination: str
) -> None:
    chosen, read_back, outcome, back = _KEPT_THEN_RETURNED[destination]
    first = store.case.review_case_id
    store.write(store.decide("accept", chosen, destination=destination))
    # The case that wrote a field says so, and only for that field (#1523).
    assert _field(store, destination)["written_by_case"] is True
    assert _field(store, "isbn")["written_by_case"] is False
    _successor(store, **read_back)
    assert store.case.review_case_id != first
    assert _field(store, destination)["written_by_case"] is False
    store.write(store.decide(outcome, back, destination=destination))
    assert _field(store, destination)["written_by_case"] is True
    assert store.publish(genre_report()).review_case_id == first
    # The returned episode wrote nothing yet: its earlier write no longer counts.
    assert _field(store, destination)["written_by_case"] is False
    keep = store.decide("keep_current", destination=destination)
    pending = _pending(store)
    assert keep.decision_id not in pending
    # Resolved anyway, the keep binds nothing rather than planning a value.
    with pytest.raises(FieldEffectError, match="authority_binding"):
        PersistedFieldEffectAuthority(store.repository).resolve(keep)
    # An unrelated write on the same case still applies in the next cycle.
    isbn = store.decide(
        "accept", {"kind": "text", "value": "9780306406157"}, destination="isbn"
    )
    target = store.case.target
    context = (str(target["connection"]), str(target["library"]))
    store.consent.allow(connection=context[0], library=context[1], destination="isbn")
    store.repository.grant_field_effect_consents(
        context[0], (context[1],), ("isbn",), store.clock()
    )
    assert isbn.decision_id in _pending(store)
    scheduler = ContinuousGuardedApplication(
        store.repository,
        {context: store.service},
        {context: FakeCapabilityObserver(CAPABILITY)},
        PersistedFieldEffectAuthority(store.repository),
        (context,),
        store.clock,
        lambda *_: None,
    )
    # Planning walks the pending queue past the keep to the ISBN; before
    # #1523's fix the keep's plan raised here on every cycle.
    scheduler.schedule(10)
    (planned,) = store.repository._connection.execute(
        "SELECT document FROM field_effects WHERE decision_id=?", (isbn.decision_id,)
    ).fetchone()
    store.target.seed(FieldEffectOperation.from_json(planned).plan)
    for _ in range(4):
        scheduler.run_cycle()
    assert store.repository._connection.execute(
        "SELECT state FROM field_effects WHERE decision_id=?", (isbn.decision_id,)
    ).fetchall() == [("verified",)]
    assert not store.repository._connection.execute(
        "SELECT 1 FROM field_effects WHERE decision_id=?", (keep.decision_id,)
    ).fetchall()


@pytest.mark.parametrize("destination", sorted(_KEPT_THEN_RETURNED))
def test_a_keep_on_a_returned_episode_reads_from_its_own_episode(
    store: OwnerStore, destination: str
) -> None:
    """History's From for a keep follows only the writes of the case's current
    episode, so a keep after the work returns never reads as going back over
    a write of an earlier episode (#1523)."""
    chosen, read_back, outcome, back = _KEPT_THEN_RETURNED[destination]
    first = store.case.review_case_id
    store.write(store.decide("accept", chosen, destination=destination))
    _successor(store, **read_back)
    store.write(store.decide(outcome, back, destination=destination))
    assert store.publish(genre_report()).review_case_id == first
    keep = store.decide("keep_current", destination=destination)
    (entry,) = (
        event
        for event in store.repository.get_review_case_history(first, 50)
        if event.get("decision_id") == keep.decision_id
    )
    # The episode wrote nothing, so the keep keeps what the case read: From and
    # To are the same value, never C's first-episode write as From.
    assert entry["previous_value"] == entry["selected_value"], entry
    assert entry["previous_value"] != chosen


def test_the_served_activity_follows_a_write_that_verifies_after_a_keep(
    held: tuple[OwnerStore, str], tmp_path: Path
) -> None:
    """A keep recorded before any write of its field, then the field returned
    to automatic and written: the served Activity reads the keep's row as the
    canonical read does once that write verifies (ADR-0082, #1523)."""
    store, _ = held
    borrower = open_repository(
        store.root / "owner.sqlite",
        create=False,
        authority=store.repository.authority,
    )
    try:
        borrower.configure_activity_page_reader(ActivityFactStore())
        assert ("keep_current", "Kept Drama") in _genres_owner(_activity(store))
        assert _served(store, borrower) == _activity(store)
        store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
        completed = [0]
        application, scheduler, _ = _resume_service(store, tmp_path, completed)
        application.return_to_automatic(store.case.review_case_id, "genres")
        completed[0] = 10**9
        scheduler.schedule()
        automatic = next(
            AutomaticDecision.from_json(raw)
            for raw in store.repository.get_review_case_decisions(
                store.case.review_case_id
            )
            if json.loads(raw)["schema_version"] == "automatic-decision-v1"
            and json.loads(raw)["destination"] == "genres"
        )
        assert _served(store, borrower) == _activity(store)
        store.write(automatic)
        assert _served(store, borrower) == _activity(store)
    finally:
        borrower.close()


def _automatic_after_going_back(
    store: OwnerStore, *, planned_first: bool
) -> tuple[HumanDecision, AutomaticDecision]:
    """Read Drama; accept Fantasy and keep Drama, return the field to
    automatic, and its automatic decision writes Fantasy again: a write that
    lands after the owner's writes but was recorded before them.

    ``planned_first`` plans the automatic write before the accept supersedes
    it. Going back restores that plan's pre-value, so the return revives the
    same deterministic plan in its old row rather than inserting a new one."""
    store.automatic("genres", "replace", _HELD.target.library)
    store.publish(_HELD)
    automatic = next(
        AutomaticDecision.from_json(raw)
        for raw in store.repository.get_review_case_decisions(store.case.review_case_id)
        if json.loads(raw)["schema_version"] == "automatic-decision-v1"
        and json.loads(raw)["destination"] == "genres"
    )
    early = store.plan(automatic) if planned_first else None
    accept = store.decide(
        "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
    )
    store.write(accept)
    store.write(store.decide("keep_current", destination="genres"))
    token = _hold(store)
    assert token is not None
    assert store.repository.clear_field_effect_manual_hold(
        store.case.review_case_id, "genres", store.clock(), token
    )
    written = store.write(automatic)
    assert written.plan.desired_value == ["Fantasy"]
    if early is not None:
        # Revived in place: the plan row predates the owner's writes.
        assert written.plan.plan_id == early.plan.plan_id
        assert store.repository._connection.execute(
            "SELECT decision_id FROM field_effects WHERE state='verified' "
            "ORDER BY rowid LIMIT 1"
        ).fetchone() == (automatic.decision_id,)
    return accept, automatic


@pytest.mark.parametrize("planned_first", [False, True], ids=["fresh", "revived"])
def test_an_automatic_write_after_going_back_is_the_cases_latest(
    tmp_path: Path, planned_first: bool
) -> None:
    """The case's latest write is the one that landed last, not the latest
    decision or the latest plan row: an automatic decision returned to after
    the owner's writes writes after them (#1523)."""
    store = OwnerStore(tmp_path, None)
    try:
        _, automatic = _automatic_after_going_back(store, planned_first=planned_first)
        assert store.repository.written_field_values(store.case.review_case_id) == (
            ("genres", ["Fantasy"]),
        )
        assert cast("dict[str, Any]", _field(store, "genres")["owner_control"])[
            "current"
        ] == ["Fantasy"]
        later = store.decide("keep_current", destination="genres")
        assert later.follows == automatic.decision_id
        assert later.decision_id in _pending(store)
        back = store.write(later)
        assert (back.plan.pre_value, back.plan.desired_value) == (
            ["Fantasy"],
            ["Drama"],
        )
    finally:
        store.close()


@pytest.mark.parametrize("planned_first", [False, True], ids=["fresh", "revived"])
def test_history_reads_what_a_decision_replaced_not_a_later_automatic_write(
    tmp_path: Path, planned_first: bool
) -> None:
    """History's From is the write that landed last before the decision: an
    automatic write planned after it is not what it replaced (#1523)."""
    store = OwnerStore(tmp_path, None)
    try:
        accept, _ = _automatic_after_going_back(store, planned_first=planned_first)
        assert _keep_history(store, accept)["previous_value"] == {
            "kind": "list",
            "value": ["Drama"],
        }
        # Accepting Fantasy again would change nothing, so it is refused; a
        # keep is the decision that follows the automatic write (#1523).
        again = store.decide("keep_current", destination="genres")
        assert _keep_history(store, again)["previous_value"] == {
            "kind": "list",
            "value": ["Fantasy"],
        }
    finally:
        store.close()


def test_accepting_the_value_the_case_wrote_is_unchanged(store: OwnerStore) -> None:
    """An accept of the value the case's latest write already holds would
    plan nothing and wait for ever, so it is refused as unchanged and records
    nothing; the first accept still replays, and a keep still records."""
    first = store.decide("accept", _FANTASY, destination="genres")
    store.write(first)
    hold = _hold_document(store)
    _refused(store, "owner_value_unchanged", "accept", _FANTASY, destination="genres")
    assert _pending(store) == []
    assert _hold_document(store) == hold
    replay = HumanDecision.create(
        store.case,
        proposal_id=first.proposal_id,
        target_fingerprint=first.target_fingerprint,
        destination="genres",
        actor="owner",
        outcome="accept",
        selected_value=first.selected_value,
        idempotency_key=f"owner-command-{first.occurred_at - 1:08d}",
        occurred_at=first.occurred_at,
    )
    assert (
        HumanDecision.from_json(store.repository.record_human_decision(replay)) == first
    )
    keep = store.decide("keep_current", destination="genres")
    assert _pending(store) == [keep.decision_id]


_DRAMA = {"kind": "list", "value": ["Drama"]}


def _genres_automatic(store: OwnerStore) -> AutomaticDecision:
    (decision,) = (
        AutomaticDecision.from_json(raw)
        for raw in store.repository.get_review_case_decisions(store.case.review_case_id)
        if json.loads(raw)["schema_version"] == "automatic-decision-v1"
        and json.loads(raw)["destination"] == "genres"
    )
    return decision


def _leave_and_return(store: OwnerStore) -> None:
    """The work leaves the case's content and returns to it: the same case
    is admitted again and starts a new episode (#1358)."""
    first = store.case.review_case_id
    store.publish(rewritten(_HELD, publisher="Changed Elsewhere"))
    assert store.case.review_case_id != first
    store.publish(_HELD)
    assert store.case.review_case_id == first


@pytest.mark.parametrize("planned", ["reused", "revived"])
def test_a_write_landing_in_a_later_episode_is_the_cases_own(
    tmp_path: Path, planned: str
) -> None:
    """An automatic plan made in the first episode that lands after the case
    was admitted again is the case's write: an episode holds the writes that
    landed in it, whichever episode planned them (#1973)."""
    store = OwnerStore(tmp_path, None)
    try:
        store.automatic("genres", "replace", _HELD.target.library)
        store.publish(_HELD)
        automatic = _genres_automatic(store)
        early = store.plan(automatic)
        if planned == "revived":
            store.decide("accept", _FANTASY, destination="genres")
        _leave_and_return(store)
        if planned == "revived":
            assert store.repository.clear_field_effect_manual_hold(
                store.case.review_case_id, "genres", store.clock(), _hold(store)
            )
        assert store.write(automatic).plan.plan_id == early.plan.plan_id
        assert store.repository.written_field_values(store.case.review_case_id) == (
            ("genres", ["Fantasy"]),
        )
        keep = store.decide("keep_current", destination="genres")
        assert keep.follows == automatic.decision_id
    finally:
        store.close()


@pytest.mark.parametrize("first_episode", ["planned", "verified"])
def test_a_plan_from_an_earlier_episode_still_waits(
    tmp_path: Path, first_episode: str
) -> None:
    """A write planned before the case was admitted again and not yet landed
    still waits, so a pin is refused as it is in the first episode
    (ADR-0085, Amendment 2); one that landed before is history (#1973)."""
    store = OwnerStore(tmp_path, None)
    try:
        store.automatic("genres", "replace", _HELD.target.library)
        store.publish(_HELD)
        automatic = _genres_automatic(store)
        if first_episode == "planned":
            store.plan(automatic)
        else:
            store.write(automatic)
        _leave_and_return(store)
        if first_episode == "planned":
            _refused(store, "pin_change_waiting", "pin", destination="genres")
        else:
            assert store.decide("pin", destination="genres").outcome == "pin"
    finally:
        store.close()


class _SameSecond(OwnerStore):
    """A store whose clock stops while ``frozen``: everything then happens
    in one second."""

    frozen = False

    def clock(self) -> int:
        if not self.frozen:
            self.ticks += 1
        return self.ticks


@pytest.mark.parametrize("same_second", [False, True], ids=["later", "same_second"])
def test_history_reads_the_write_before_a_decision_in_its_second(
    tmp_path: Path, same_second: bool
) -> None:
    """History's From is the write a decision followed when it was recorded,
    never one placed before it by a clock in whole seconds: an automatic
    write planned in the decision's second after a return to automatic is
    not what it replaced (#1973)."""
    store = _SameSecond(tmp_path, None)
    try:
        store.automatic("genres", "replace", _HELD.target.library)
        store.publish(_HELD)
        automatic = _genres_automatic(store)
        store.frozen = same_second
        accept = store.decide("accept", _FANTASY, destination="genres")
        assert store.repository.clear_field_effect_manual_hold(
            store.case.review_case_id, "genres", store.clock(), _hold(store)
        )
        written = store.write(automatic)
        store.frozen = False
        assert (written.plan.created_at == accept.occurred_at) is same_second
        assert _keep_history(store, accept)["previous_value"] == _DRAMA
    finally:
        store.close()


def test_history_of_an_earlier_episode_decision_reads_its_own_episode(
    tmp_path: Path,
) -> None:
    """A decision's From stays the write of its own episode after the case
    returns again: it is frozen when the decision is recorded, never read
    again from floors a later return moves (#1973, #1358)."""
    store = OwnerStore(tmp_path, _HELD)
    try:
        store.write(store.decide("accept", _FANTASY, destination="genres"))
        _leave_and_return(store)
        again = store.decide("accept", _FANTASY, destination="genres")
        assert _keep_history(store, again)["previous_value"] == _DRAMA
        _leave_and_return(store)
        assert _keep_history(store, again)["previous_value"] == _DRAMA
    finally:
        store.close()


def _keep_history(store: OwnerStore, keep: HumanDecision) -> dict[str, object]:
    (entry,) = (
        event
        for event in store.repository.get_review_case_history(
            store.case.review_case_id, 50
        )
        if event.get("decision_id") == keep.decision_id
    )
    return entry


def test_a_keep_in_a_middle_episode_reads_alike_after_the_next_return(
    store: OwnerStore, served: Callable[[], list[dict[str, Any]]]
) -> None:
    """A keep recorded in an episode that wrote nothing is a plain keep, and
    stays one in Activity and History after the case returns again: what it
    goes back on is frozen when it is recorded, never read from episode
    floors a later return moves (#1358, #1523)."""
    first = store.case.review_case_id
    store.write(
        store.decide(
            "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
        )
    )
    _successor(store, genres=("Fantasy",))
    assert store.publish(genre_report()).review_case_id == first
    keep = store.decide("keep_current", destination="genres")
    assert keep.follows is None
    rows, entry = _genres_owner(served()), _keep_history(store, keep)
    assert ("keep_current", "Left genres unset") in rows
    assert entry["previous_value"] == entry["selected_value"]
    _successor(store, genres=("Other",))
    assert store.publish(genre_report()).review_case_id == first
    assert _genres_owner(served()) == rows
    assert _keep_history(store, keep) == entry


def test_a_keep_freezes_the_write_planning_resolves_for_it(
    store: OwnerStore,
) -> None:
    """In one episode, the write a keep's document names is the one planning
    resolves for it when it is recorded, and the one its plan goes back on
    (#1523)."""
    accept = store.decide(
        "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
    )
    store.write(accept)
    keep = store.decide("keep_current", destination="genres")
    resolved = store.repository._field_effect_activity._human_predecessor(keep)
    assert resolved is not None
    assert (
        keep.follows
        == FieldEffectOperation.from_json(resolved[0]).plan.authority.decision_id
        == accept.decision_id
    )
    assert store.write(keep).plan.pre_value == ["Fantasy"]
    assert _keep_history(store, keep)["previous_value"] == {
        "kind": "list",
        "value": ["Fantasy"],
    }


@pytest.mark.parametrize(
    ("report", "back"),
    [
        (genre_report(), "keep"),
        (genre_report(), "clear"),
        (_HELD, "keep"),
    ],
    ids=["after_keep", "after_clear", "held_value"],
)
def test_a_keep_where_the_field_holds_what_the_case_read_is_a_plain_keep(
    tmp_path: Path, report: MetadataReport, back: str
) -> None:
    """A keep whose followed write already holds the value the case read goes
    back on nothing: it is a plain keep, never a pending write that plans no
    operation (#1523)."""
    store = OwnerStore(tmp_path, report)
    borrower = open_repository(
        store.root / "owner.sqlite",
        create=False,
        authority=store.repository.authority,
    )
    try:
        borrower.configure_activity_page_reader(ActivityFactStore())
        store.write(
            store.decide(
                "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
            )
        )
        store.write(
            store.decide("keep_current", destination="genres")
            if back == "keep"
            else store.decide(
                "owner_entered", {"kind": "empty", "value": None}, destination="genres"
            )
        )
        keep = store.decide("keep_current", destination="genres")
        assert keep.follows is None
        assert keep.decision_id not in _pending(store)
        assert store.repository.pending_field_effect_decision_counts() == ()
        assert (
            store.repository._connection.execute(
                "SELECT count(*) FROM field_effects WHERE decision_id=?",
                (keep.decision_id,),
            ).fetchone()[0]
            == 0
        )
        rows = _served(store, borrower)
        assert rows == _activity(store)
        assert (
            "keep_current",
            "Kept Drama" if report is _HELD else "Left genres unset",
        ) in _genres_owner(rows)
        entry = _keep_history(store, keep)
        assert entry["previous_value"] == entry["selected_value"]
        assert _hold(store) == keep.decision_id
    finally:
        borrower.close()
        store.close()


def test_the_store_admits_a_named_write_on_any_decision(store: OwnerStore) -> None:
    """The `human_decisions` table holds `follows` as null or as text, on any
    outcome (#1523, #1973)."""
    accept = store.decide(
        "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
    )
    store.write(accept)
    store.decide("keep_current", destination="genres")
    connection = store.repository._connection
    connection.row_factory = sqlite3.Row
    try:
        rows = {
            row["outcome"]: dict(row)
            for row in connection.execute(
                "SELECT * FROM human_decisions WHERE destination='genres'"
            )
        }
    finally:
        connection.row_factory = None
    assert json.loads(rows["keep_current"]["document"])["follows"] == (
        accept.decision_id
    )

    def admitted(outcome: str, follows: object) -> bool:
        row = dict(rows[outcome])
        row["document"] = json.dumps(
            {**json.loads(row["document"]), "follows": follows},
            separators=(",", ":"),
            sort_keys=True,
        )
        memory = sqlite3.connect(":memory:")
        try:
            memory.execute(_HUMAN_DECISIONS_SQL)
            memory.execute(
                f"INSERT INTO human_decisions ({','.join(row)}) "
                f"VALUES ({','.join('?' for _ in row)})",
                tuple(row.values()),
            )
        except sqlite3.IntegrityError:
            return False
        finally:
            memory.close()
        return True

    assert admitted("keep_current", accept.decision_id)
    assert admitted("keep_current", None)
    assert admitted("accept", None)
    assert not admitted("keep_current", 1)
    assert admitted("accept", accept.decision_id)
    assert not admitted("accept", 1)
