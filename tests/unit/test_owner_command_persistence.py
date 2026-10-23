"""Change, withdraw and revert against a real store (#1285, ADR-0085).

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
from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_SOURCE_SQL,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.review_web import InspectCurrentReviewCase
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.human_decision import HumanDecisionError
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


def _revert_command(store: OwnerStore, destination: str = "title") -> dict:
    candidate = store.candidates()[destination]
    return {
        "selected_value": {
            "kind": "list" if destination == "genres" else "text",
            "value": candidate["pre_value"],
        },
        "destination": destination,
        "reverts_plan_id": candidate["plan_id"],
    }


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
    # A written decision can only be reverted.
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
def test_withdraw_is_unsupported_where_revert_is(
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


# U9: what `revert_candidates` offers for the work's latest write.
def test_only_the_latest_verified_human_write_is_offered(store: OwnerStore) -> None:
    first = store.write(store.decide("owner_entered", ORCHARD))
    assert store.candidates()["title"]["eligibility"] == "eligible"
    store.write(store.decide("owner_entered", {"kind": "text", "value": "Orchard"}))
    latest = store.candidates()["title"]
    assert latest["plan_id"] != first.plan.plan_id
    assert (latest["pre_value"], latest["desired_value"]) == (
        "The Silent Orchard",
        "Orchard",
    )
    # An unwritten later plan is pending, not a write to revert.
    pending = store.plan(store.decide("owner_entered", {"kind": "text", "value": "O"}))
    assert store.candidates()["title"]["eligibility"] == "pending"
    # Superseded, it wrote nothing, so the verified write is latest again.
    store.decide("withdraw")
    assert store.repository.get_field_effect(pending.plan.plan_id).state == "superseded"
    assert store.candidates()["title"]["plan_id"] == latest["plan_id"]


def test_a_stale_plan_wrote_nothing_so_the_earlier_write_stays_latest(
    store: OwnerStore,
) -> None:
    written = store.write(store.decide("owner_entered", ORCHARD))
    _successor(store, title="The Silent Orchard")
    revert = store.decide("revert", **_revert_command(store))
    operation = store.plan(revert)
    store.target.drift(operation.plan, "Changed elsewhere")
    staled = store.service.apply(operation.plan.plan_id)
    assert (staled.state, staled.failure) == ("stale", "target_changed")
    assert store.candidates()["title"]["plan_id"] == written.plan.plan_id


def test_an_empty_pre_image_is_no_previous_value(store: OwnerStore) -> None:
    store.write(
        store.decide(
            "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
        )
    )
    assert store.candidates()["genres"]["eligibility"] == "no_previous_value"
    assert store.candidates()["genres"]["pre_value"] is None


def test_a_revert_write_is_never_itself_revertible(store: OwnerStore) -> None:
    store.write(store.decide("owner_entered", ORCHARD))
    _successor(store, title="The Silent Orchard")
    store.write(store.decide("revert", **_revert_command(store)))
    assert store.candidates()["title"]["eligibility"] == "reverted"


def test_cover_description_and_automatic_writes_are_never_offered(
    tmp_path: Path,
) -> None:
    store = OwnerStore(tmp_path, None)
    try:
        case_id, _ = publish_genre_case(store.repository, genre_report())
        store.load(case_id)
        automatic = next(
            AutomaticDecision.from_json(raw)
            for raw in store.repository.get_review_case_decisions(case_id)
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
        assert store.candidates() == {}
    finally:
        store.close()


def test_a_verified_event_that_disagrees_with_its_plan_refuses(
    store: OwnerStore,
) -> None:
    written = store.write(store.decide("owner_entered", ORCHARD))
    _successor(store, title="The Silent Orchard")
    command = _revert_command(store)
    connection = store.repository._connection
    connection.executescript(
        "DROP TRIGGER field_effect_events_no_update;"
        "UPDATE field_effect_events SET document=json_set(document,'$.pre_value','Other') "
        f"WHERE plan_id='{written.plan.plan_id}' AND kind='verified';"
    )
    assert store.candidates()["title"]["eligibility"] == "unavailable"
    _refused(store, "revert_unavailable", "revert", **command)


# U10: revert refusal precedence (P6), each case holding several conditions.
def test_revert_refuses_in_its_fixed_order(store: OwnerStore) -> None:
    written = store.write(store.decide("owner_entered", ORCHARD))
    command = _revert_command(store)
    # On the writing case the field still reads as before the write: the
    # version check fails, and so does nothing earlier.
    _refused(store, "revert_field_changed", "revert", **command)
    assert _field(store, "title")["owner_control"]["revert"] is None
    earlier = store.case
    _successor(store, title="The Silent Orchard")
    # Stale outranks everything.
    _refused(store, "stale", "revert", case=earlier, **command)
    # A plan that is not the latest write, with a field that has changed too.
    _refused(
        store,
        "revert_unavailable",
        "revert",
        **{**command, "reverts_plan_id": "effect_plan_" + "0" * 64},
    )
    # A value other than the recorded pre-image.
    _refused(
        store,
        "revert_unavailable",
        "revert",
        **{**command, "selected_value": {"kind": "text", "value": "Something else"}},
    )
    assert _field(store, "title")["owner_control"]["revert"]["state"] == "available"
    # The field changed since the write; the plan is still the latest.
    _successor(store, title="Edited elsewhere")
    _refused(store, "revert_field_changed", "revert", **command)
    assert _field(store, "title")["owner_control"]["revert"]["state"] == "field_changed"
    assert store.candidates()["title"]["plan_id"] == written.plan.plan_id


def test_no_previous_value_outranks_a_changed_field(store: OwnerStore) -> None:
    store.write(
        store.decide(
            "accept", {"kind": "list", "value": ["Fantasy"]}, destination="genres"
        )
    )
    command = {
        **_revert_command(store, "genres"),
        "selected_value": {"kind": "list", "value": ["Fantasy"]},
    }
    _successor(store, genres=("Drama",))
    _refused(store, "revert_no_previous_value", "revert", **command)
    assert (
        _field(store, "genres")["owner_control"]["revert"]["state"]
        == "no_previous_value"
    )


def test_a_later_write_in_flight_leaves_nothing_to_revert(store: OwnerStore) -> None:
    store.write(store.decide("owner_entered", ORCHARD))
    _successor(store, title="The Silent Orchard")
    command = _revert_command(store)
    pending = store.plan(
        store.decide("owner_entered", {"kind": "text", "value": "Next"})
    )
    codes: list[str] = []

    def revert(_plan) -> None:
        # The in-flight write is now the latest, so the earlier one is gone.
        try:
            store.decide("revert", **command)
        except HumanDecisionError as error:
            codes.append(error.code)

    store.target.before_apply = revert
    assert store.service.apply(pending.plan.plan_id).state == "verified"
    assert codes == ["revert_unavailable"]


# Activity names each new command and a restore that stopped unwritten.
def test_activity_states_withdraw_and_an_unwritten_restore(store: OwnerStore) -> None:
    written = store.write(store.decide("owner_entered", ORCHARD))
    _successor(store, title="The Silent Orchard")
    revert = store.decide("revert", **_revert_command(store))
    operation = store.plan(revert)
    store.target.drift(operation.plan, "Edited in the library")
    assert store.service.apply(operation.plan.plan_id).state == "stale"
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
        ("stale", "Not restored: the field changed before the write."),
        ("revert", "The Silent Orchard → Real Tigers"),
        ("verified", "Real Tigers → The Silent Orchard"),
        ("owner_entered", "Real Tigers → The Silent Orchard"),
    ]
    assert written.plan.plan_id in store.patches
