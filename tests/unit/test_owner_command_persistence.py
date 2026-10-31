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
from mechanical_librarian.application.review_web import (
    InspectCurrentReviewCase,
    ReviewWebError,
)
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
        store.decide(before_release, _FANTASY, destination="genres")
    store.publish(rewritten(_HELD, publisher="Changed Again"))
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
    # With nothing automatic to hold, the case holds nothing.
    assert _hold(store) is None


# R-A: that decision leaves the case as if it was never held, so a successor
# read under automatic handling is handled automatically.
@pytest.mark.parametrize("outcome", ["accept", "withdraw"])
def test_a_decision_on_a_recurring_case_carries_no_hold_forward(
    held: tuple[OwnerStore, str], outcome: str
) -> None:
    store, token = held
    if outcome == "accept":
        _recur_after_release(store, token)
        store.write(store.decide("accept", _FANTASY, destination="genres"))
        later = rewritten(_HELD, publisher="Changed Elsewhere", genres=("Fantasy",))
    else:
        # The accept recorded before the release is the earlier episode's
        # (#1358), so the owner accepts and withdraws within this one.
        _recur_after_release(store, token, before_release="accept")
        store.decide("accept", _FANTASY, destination="genres")
        store.decide("withdraw", destination="genres")
        later = rewritten(_HELD, publisher="Changed Later")
    # The Ask transition's work item went stale as the work changed under
    # it; the owner acknowledges that before the next policy is activated.
    transition = store.repository.metadata_policy_transition()
    assert transition is not None
    store.repository.acknowledge_metadata_policy_transition(
        transition.transition_id, store.clock()
    )
    _genres_policy(store, "automatic", 3)
    store.publish(later)
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
