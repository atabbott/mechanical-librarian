"""An owner pin keeps one field's value and stops the question (#1346).

ADR-0085, Amendment 2: a `pin` decision puts a digest of the case's value on
the record of the owner's answer for the field (ADR-0108). Every later case of the work compares its value
with that digest, so the field is neither asked nor decided automatically
while the value stays, and asks again, showing the pinned value, once
Audiobookshelf holds something else. Each test here fails before the change,
where neither outcome existed. Synthetic fixtures only.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from mechanical_librarian.adapters.field_effect_authority import (
    PersistedFieldEffectAuthority,
)
from mechanical_librarian.adapters.field_effect_fakes import FakeCapabilityObserver
from mechanical_librarian.adapters.persistence.decisions import pin_in_force_sql
from mechanical_librarian.adapters.persistence.owner_field_answers import (
    case_record_sql,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_SOURCE_SQL,
)
from mechanical_librarian.application.continuous_field_effects import (
    ContinuousGuardedApplication,
)
from mechanical_librarian.application.field_effects import (
    GuardedApplicationError,
    authority_from_decision,
)
from mechanical_librarian.application.review_application import (
    compose_review_application,
)
from mechanical_librarian.application.review_web import BrowseReviewLibrary
from mechanical_librarian.domain.human_decision import (
    HumanDecisionError,
    chosen_value_fingerprint,
    pinned_field_fingerprint,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
    TargetFacts,
    TargetField,
    TargetFieldState,
    TargetSeries,
)
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet
from tests.support.audiobook_reports import synthetic_audiobook_bibliographic_report
from tests.support.evidence_led_titles import isbn_title_report
from tests.support.genre_reports import genre_report
from tests.support.owner_field_record import record_recomputes  # noqa: F401
from tests.support.owner_field_record import resume as resume_field
from tests.support.owner_values import CAPABILITY, OwnerStore, rewritten
from tests.unit.test_owner_answer_holds import (
    ORIGINAL,
    SOURCES,
    _automatic,
    _field,
    _hold,
    _queued,
    _store,
)

ORCHARD = {"kind": "text", "value": "The Silent Orchard"}
ANOTHER = "Another Example Title"
FANTASY = {"kind": "list", "value": ["Fantasy"]}


def _record(
    store: OwnerStore, destination: str = "title", case_id: str | None = None
) -> dict[str, object] | None:
    """The record's row for the field of the case's book: its answer, its pin
    and its open Resume, and whether the pin reads in force on the case."""
    row = store.repository._connection.execute(
        "SELECT record.answer_id,record.pin_fingerprint,record.open_resume_id,"
        + pin_in_force_sql(":case", ":destination")
        + case_record_sql(":case", ":destination").removeprefix("SELECT 1"),
        {"case": case_id or store.case.review_case_id, "destination": destination},
    ).fetchone()
    if row is None:
        return None
    return {
        "answer_id": row[0],
        "pin": None
        if row[1] is None
        else {"fingerprint": row[1], "in_force": bool(row[3])},
        "open_resume_id": row[2],
    }


def _pin(store: OwnerStore, destination: str = "title") -> object:
    record = _record(store, destination)
    return None if record is None else record["pin"]


def _asked(store: OwnerStore, destination: str = "title") -> bool:
    """Whether the work asks about the field, live and in the persisted facts,
    which must agree."""
    connection = store.repository._connection
    target = store.case.target
    key = (target["connection"], target["library"], target["item"], destination)
    live = connection.execute(
        "SELECT count(*) FROM (" + _ATTENTION_FACT_SOURCE_SQL + ") derived "
        "WHERE connection_name=? AND library_name=? AND item_id=? AND destination=?",
        key,
    ).fetchone()[0]
    persisted = connection.execute(
        "SELECT count(*) FROM review_attention_facts WHERE connection_name=? "
        "AND library_name=? AND item_id=? AND destination=?",
        key,
    ).fetchone()[0]
    assert live == persisted, (destination, live, persisted)
    return bool(live)


def _rows(store: OwnerStore) -> tuple[int, int, int]:
    return store.repository._connection.execute(
        "SELECT (SELECT count(*) FROM human_decisions),"
        "(SELECT count(*) FROM human_decision_events),"
        "(SELECT count(*) FROM owner_field_answers)"
    ).fetchone()


def _refused(store: OwnerStore, code: str, outcome: str, **kwargs) -> None:
    before = _rows(store), _record(store, kwargs.get("destination", "title"))
    with pytest.raises(HumanDecisionError) as error:
        store.decide(outcome, **kwargs)
    assert error.value.code == code
    assert (
        _rows(store),
        _record(store, kwargs.get("destination", "title")),
    ) == before


def _with_state(
    report: MetadataReport, destination: str, state: TargetFieldState, **values: str
) -> MetadataReport:
    """The work read again with one field in a value-free state."""
    changed = rewritten(report, **values)
    fields = tuple(
        TargetField(field.destination, state, None)
        if field.destination.value == destination
        else field
        for field in changed.target.fields
    )
    target = changed.target
    return MetadataReport.create(
        target=TargetFacts(target.connection, target.library, target.item, fields),
        file=changed.file,
        local_claims=changed.local_claims,
        local_reasons=changed.local_reasons,
        providers=changed.providers,
    )


def _library_pinned_count(store: OwnerStore) -> int:
    items = cast(
        "list[dict[str, Any]]",
        BrowseReviewLibrary(store.repository).library(
            lens="all", search=None, limit=25, cursor=None
        )["items"],
    )
    assert len(items) == 1
    return items[0]["pinned_count"]


@pytest.fixture
def genres(tmp_path: Path):
    store = OwnerStore(tmp_path, genre_report())
    yield store
    store.close()


# AC13 and K-D8: the same pin again is refused, in force or waiting.
@pytest.mark.parametrize("state", ["in_force", "waiting"])
def test_a_repeated_pin_is_refused(genres: OwnerStore, state: str) -> None:
    if state == "waiting":
        genres.decide("owner_entered", ORCHARD)
    genres.decide("pin")
    assert _pin(genres)["in_force"] is (state == "in_force")
    _refused(genres, "already_pinned", "pin")


# AC14: unpin needs a pin.
def test_unpin_is_refused_without_a_pin(genres: OwnerStore) -> None:
    _refused(genres, "not_pinned", "unpin", destination="genres")
    genres.decide("keep_current", destination="genres")
    _refused(genres, "not_pinned", "unpin", destination="genres")


def _pending(store: OwnerStore) -> set[str]:
    return {
        json.loads(document)["decision_id"]
        for document in store.repository.pending_field_effect_decision_documents(32)
    }


def _pending_counts(store: OwnerStore) -> int:
    return sum(
        cast("int", row["count"])
        for row in store.repository.pending_field_effect_decision_counts()
    )


# KC2, KC3 and K-D1: a pin on a value the owner chose is accepted before the
# write lands. It only marks the choice's own hold, so the write goes on.
@pytest.mark.parametrize("stage", ["unwritten", "planned", "in_flight", "verified"])
def test_a_pin_on_a_chosen_value_is_accepted_at_every_stage(
    genres: OwnerStore, stage: str
) -> None:
    choice = genres.decide("owner_entered", ORCHARD)
    operation = None if stage in {"unwritten", "verified"} else genres.plan(choice)
    if stage == "verified":
        genres.write(choice)
    if stage == "in_flight":
        assert operation is not None
        genres.target.before_apply = lambda _plan: genres.decide("pin")
        assert genres.service.apply(operation.plan.plan_id).state == "verified"
    else:
        genres.decide("pin")
    assert _hold(genres) == choice.decision_id
    assert _pin(genres) == {
        "fingerprint": chosen_value_fingerprint("title", ORCHARD),
        "in_force": False,
    }
    record = _record(genres)
    assert record is not None
    assert record["open_resume_id"] is None
    if stage == "unwritten":
        assert _pending(genres) == {choice.decision_id}
        assert _pending_counts(genres) == 1
        genres.write(choice)
    elif stage == "planned":
        assert operation is not None
        assert genres.service.apply(operation.plan.plan_id).state == "verified"
    effects = genres.repository._connection.execute(
        "SELECT state FROM field_effects WHERE decision_id=?", (choice.decision_id,)
    ).fetchall()
    assert effects == [("verified",)]


# KC7 and K-D2: once a later case shows the chosen value the pin is in force.
@pytest.mark.parametrize(
    "destination,chosen,held",
    [("title", ORCHARD, ORCHARD["value"]), ("genres", FANTASY, ("Fantasy",))],
    ids=["title", "genres"],
)
def test_a_pin_on_a_chosen_value_is_in_force_once_read_back(
    genres: OwnerStore, destination: str, chosen: dict[str, Any], held: Any
) -> None:
    choice = genres.decide("owner_entered", chosen, destination=destination)
    genres.decide("pin", destination=destination)
    assert _pin(genres, destination)["in_force"] is False
    genres.write(choice)
    deciding = genres.case.review_case_id
    genres.publish(rewritten(genre_report(), **{destination: held}))
    assert genres.case.review_case_id != deciding
    assert (_hold(genres, destination), _pin(genres, destination)["in_force"]) == (
        choice.decision_id,
        True,
    )
    field = _field(genres, destination)
    assert field["pin"] == {"state": "in_force", "value": field["current"]}
    assert field["classification_cause"] == "pinned"
    assert _asked(genres, destination) is False


# KC7 and K-D3: a pinned choice that was never read back is carried as
# changed. The field asks, showing the chosen value, and nothing is written
# from the old choice.
def test_a_pinned_choice_never_read_back_asks_with_the_chosen_value(
    genres: OwnerStore,
) -> None:
    choice = genres.decide("owner_entered", ORCHARD)
    genres.decide("pin")
    assert _pending(genres) == {choice.decision_id}
    genres.publish(rewritten(genre_report(), publisher="Changed Elsewhere"))
    assert (_hold(genres), _pin(genres)["in_force"]) == (choice.decision_id, False)
    field = _field(genres, "title")
    assert field["pin"] == {
        "state": "changed",
        "value": {"destination": "title", "state": "value", "value": ORCHARD["value"]},
    }
    assert (field["classification"], field["classification_cause"]) == (
        "needs_attention",
        "pin_changed",
    )
    assert field["classification_reason"] == (
        "Audiobookshelf shows a different value from the one you pinned."
    )
    assert field["human_decision"] is None
    assert _asked(genres) is True
    assert _pending(genres) == set()
    assert _pending_counts(genres) == 0


# KC5 and K-D9: with no answer of the owner's standing, a pin is still
# refused while Automatic's change waits to be written or read back.
@pytest.mark.parametrize("stage", ["planned", "verified"])
def test_a_pin_is_refused_while_automatics_change_waits(
    tmp_path: Path, stage: str
) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    queued = _queued(store)
    assert queued is not None
    if stage == "planned":
        store.plan(queued)
    else:
        store.write(queued)
    _refused(store, "pin_change_waiting", "pin")
    assert _field(store, "title")["pin_control"] == {
        "pin": "change_waiting",
        "unpin": False,
    }
    store.close()


# #1984: the pin line says only the next scan is left once the field's change
# is written. An earlier write says nothing of Automatic's decision in a later
# policy generation on the same case, which is still to be written.
def test_a_refused_pin_says_whether_the_change_is_written(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    queued = _queued(store)
    assert queued is not None
    store.plan(queued)
    assert _field(store, "title")["change_written"] is False
    store.write(queued)
    written = _field(store, "title")
    assert written["pin_control"]["pin"] == "change_waiting"
    assert (written["written_by_case"], written["change_written"]) == (True, True)

    (document,) = store.repository._connection.execute(
        "SELECT document FROM shadow_decision_outcomes WHERE review_case_id=?",
        (store.case.review_case_id,),
    ).fetchone()
    outcome = ShadowOutcomeSet.from_json(document)
    later = {name: getattr(outcome, name) for name in outcome.__slots__} | {
        "policy_generation": outcome.policy_generation + 1
    }
    store.repository._record_automatic_outcomes(
        cast(ShadowOutcomeSet, SimpleNamespace(**later)), store.clock()
    )
    unwritten = _field(store, "title")
    assert unwritten["pin_control"]["pin"] == "change_waiting"
    assert (unwritten["written_by_case"], unwritten["change_written"]) == (
        True,
        False,
    )
    store.close()


# KC5, RK1 and K-D10: a pin over an answer whose hold a Resume was asked of,
# or released, is refused, so it never makes that answer's write pending again.
# Resume is offered only where the field is Automatic (ADR-0108).
@pytest.mark.parametrize("resume", ["requested", "released"])
def test_a_pin_after_resume_is_refused_and_revives_nothing(
    tmp_path: Path, resume: str
) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    answer = store.decide("owner_entered", ORCHARD)
    assert _pending(store) == {answer.decision_id}
    at = store.clock()
    if resume == "requested":
        scan = store.repository.request_review_scan(
            report.target.connection,
            report.target.library,
            "library-ebooks",
            "a" * 64,
            at,
        )
        assert store.repository.request_owner_field_resume(
            store.case.review_case_id, "title", answer.decision_id, scan.scan_id, at, at
        )
    else:
        assert resume_field(store.repository, store.case.review_case_id, "title", at)
    before = _pending(store), _pending_counts(store)
    _refused(store, "pin_change_waiting", "pin")
    assert (_pending(store), _pending_counts(store)) == before
    if resume == "released":
        # Automatic's decision is armed again; the answer's write is not.
        assert answer.decision_id not in before[0]
    assert _field(store, "title")["pin_control"] == {
        "pin": "change_waiting",
        "unpin": False,
    }
    store.close()


# KC2 and K-D13: Keep current over a value Automatic already wrote restores
# the case's value, so a pin on it is accepted and waits for that restore.
def test_a_pin_on_a_restoring_keep_current_waits_for_the_restore(
    tmp_path: Path,
) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    queued = _queued(store)
    assert queued is not None
    store.write(queued)
    kept = store.decide("keep_current")
    assert _pending(store) == {kept.decision_id}
    store.decide("pin")
    assert _hold(store) == kept.decision_id
    assert _pin(store) == {
        "fingerprint": pinned_field_fingerprint(store.case, "title"),
        "in_force": False,
    }
    assert _field(store, "title")["pin"]["state"] == "waiting"
    # The restore is still the pending write, from Automatic's value back
    # to the case's.
    assert _pending(store) == {kept.decision_id}
    restore = PersistedFieldEffectAuthority(store.repository).resolve(kept)
    assert (restore.pre_value, restore.decision_value) == (
        SOURCES,
        {"kind": "text", "value": ORIGINAL},
    )
    store.close()


# Q1 and K-D4: a Keep current that changes nothing puts the pin in force at
# once, on the hold that answer set.
def test_a_pin_follows_keep_current_without_waiting(genres: OwnerStore) -> None:
    kept = genres.decide("keep_current", destination="genres")
    genres.decide("pin", destination="genres")
    assert _hold(genres, "genres") == kept.decision_id
    assert _pin(genres, "genres") == {
        "fingerprint": pinned_field_fingerprint(genres.case, "genres"),
        "in_force": True,
    }
    field = _field(genres, "genres")
    assert field["pin"] == {"state": "in_force", "value": field["current"]}
    assert field["classification_cause"] == "pinned"


# KC1 and K-D5: an unpin takes only the pin off. The answer under it stands,
# with its pending write, and the field reads decided.
@pytest.mark.parametrize("outcome,value", [("keep_current", None), ("accept", FANTASY)])
def test_unpin_over_an_answer_leaves_the_answer(
    genres: OwnerStore, outcome: str, value: dict[str, object] | None
) -> None:
    answer = genres.decide(outcome, value, destination="genres")
    decided = _field(genres, "genres")
    pending = _pending(genres)
    assert pending == ({answer.decision_id} if outcome == "accept" else set())
    genres.decide("pin", destination="genres")
    assert _field(genres, "genres")["owner_control"]["withdraw"] is None
    genres.decide("unpin", destination="genres")
    assert (_hold(genres, "genres"), _pin(genres, "genres")) == (
        answer.decision_id,
        None,
    )
    assert _pending(genres) == pending
    field = _field(genres, "genres")
    assert field["pin"] is None
    assert field["human_decision"] == decided["human_decision"] is not None
    assert (field["classification"], field["classification_cause"]) == (
        decided["classification"],
        decided["classification_cause"],
    )
    assert field["owner_control"]["withdraw"] == "available"
    assert _asked(genres, "genres") is False


# KC4, RK1 and K-D6: a second choice under a pin replaces the first, whose
# planned write closes, and the pin ends.
def test_a_second_choice_under_a_pin_closes_the_first_choices_write(
    genres: OwnerStore,
) -> None:
    first = genres.decide("owner_entered", ORCHARD)
    planned = genres.plan(first)
    genres.decide("pin")
    second = genres.decide("owner_entered", {"kind": "text", "value": ANOTHER})
    assert (_hold(genres), _pin(genres)) == (second.decision_id, None)
    closed = genres.repository.get_field_effect(planned.plan.plan_id)
    assert (closed.state, closed.failure) == ("superseded", "authority_superseded")
    assert _pending(genres) == {second.decision_id}
    assert _field(genres, "title")["pin"] is None


# K-D7 (QK3): an answer cannot be withdrawn while its field is pinned.
def test_withdraw_is_refused_while_pinned(genres: OwnerStore) -> None:
    answer = genres.decide("owner_entered", ORCHARD)
    genres.decide("pin")
    _refused(genres, "pinned", "withdraw")
    assert _pending(genres) == {answer.decision_id}
    genres.decide("unpin")
    genres.decide("withdraw")
    assert _record(genres) is None
    assert _pending(genres) == set()
    assert _field(genres, "title")["human_decision"] is None


# KC6, KC8 and K-D16: on the deciding case a waiting pin leaves the field as
# its answer reads, and shows the chosen value as the pinned one.
def test_a_waiting_pin_reads_waiting_with_the_chosen_value(
    genres: OwnerStore,
) -> None:
    choice = genres.decide("owner_entered", ORCHARD)
    decided = _field(genres, "title")
    assert decided["owner_control"]["withdraw"] == "available"
    genres.decide("pin")
    field = _field(genres, "title")
    assert field["pin"] == {
        "state": "waiting",
        "value": {"destination": "title", "state": "value", "value": ORCHARD["value"]},
    }
    assert field["human_decision"] == decided["human_decision"]
    assert field["human_decision"]["decision_id"] == choice.decision_id
    assert (
        field["classification"],
        field["classification_cause"],
        field["classification_reason"],
    ) == (
        decided["classification"],
        decided["classification_cause"],
        decided["classification_reason"],
    )
    assert field["classification_cause"] != "pinned"
    assert field["owner_control"]["withdraw"] is None
    assert field["pin_control"] == {"pin": "available", "unpin": True}
    assert field["manual_hold"] is False
    assert _asked(genres) is False
    assert _library_pinned_count(genres) == 1


def test_a_pin_is_accepted_once_the_written_value_is_read_back(
    genres: OwnerStore,
) -> None:
    genres.write(genres.decide("owner_entered", ORCHARD))
    genres.publish(rewritten(genre_report(), title=ORCHARD["value"]))
    pin = genres.decide("pin")
    assert _hold(genres) == pin.decision_id
    assert _pin(genres) == {
        "fingerprint": _pin(genres)["fingerprint"],
        "in_force": True,
    }


@pytest.mark.parametrize("outcome", ["pin", "unpin"])
def test_withdraw_is_refused_on_a_pin_or_unpin_row(
    genres: OwnerStore, outcome: str
) -> None:
    genres.decide("pin")
    if outcome == "unpin":
        genres.decide("unpin")
    _refused(genres, "nothing_to_withdraw", "withdraw")


# AC15 and D19: the restored pin is compared with this case's value again.
def test_withdrawing_an_answer_over_a_pin_restores_the_pin_compared_again(
    tmp_path: Path,
) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    pin = store.decide("pin")
    pinned = _pin(store)
    assert pinned is not None and pinned["in_force"] is True
    store.publish(rewritten(report, title=ANOTHER))
    assert _pin(store) == {**pinned, "in_force": False}  # type: ignore[dict-item]  # reason: as above.
    answer = store.decide("owner_entered", {"kind": "text", "value": "Typed"})
    assert (_hold(store), _pin(store)) == (answer.decision_id, None)
    store.decide("withdraw")
    # The work's earlier hold is the pinned case's own row, in force there;
    # restored here it is compared with this case's title and reads changed.
    assert _hold(store) == pin.decision_id
    assert _pin(store) == {**pinned, "in_force": False}  # type: ignore[dict-item]  # reason: as above.
    assert _field(store, "title")["classification_cause"] == "pin_changed"
    store.close()


# D20 (R4): an answer given on the pinned case itself overwrote the pinned
# row, so withdrawing it leaves no earlier pin to restore.
def test_withdrawing_an_answer_on_the_pinned_case_restores_no_pin(
    genres: OwnerStore,
) -> None:
    genres.decide("pin")
    genres.decide("owner_entered", ORCHARD)
    genres.decide("withdraw")
    assert _hold(genres) is None
    assert _field(genres, "title")["pin"] is None


# AC14 and R3: unpin keeps the record's answer and takes its pin off, so
# the work's next case reads no pin (ADR-0108).
def test_unpin_keeps_the_hold_and_its_token(genres: OwnerStore) -> None:
    pin = genres.decide("pin")
    genres.decide("unpin")
    assert _record(genres) == {
        "answer_id": pin.decision_id,
        "pin": None,
        "open_resume_id": None,
    }
    genres.publish(rewritten(genre_report(), publisher="Changed Elsewhere"))
    assert (_hold(genres), _pin(genres)) == (pin.decision_id, None)


# AC15 and D18: any other answer replaces the hold, without a pin.
@pytest.mark.parametrize(
    "outcome,value",
    [("accept", FANTASY), ("keep_current", None), ("owner_entered", FANTASY)],
)
def test_choosing_another_value_ends_the_pin(
    genres: OwnerStore, outcome: str, value: dict[str, object] | None
) -> None:
    genres.decide("pin", destination="genres")
    assert _pin(genres, "genres") is not None
    answer = genres.decide(outcome, value, destination="genres")
    assert (_hold(genres, "genres"), _pin(genres, "genres")) == (
        answer.decision_id,
        None,
    )


# #1523, S14: a clear is an ordinary answer. It ends a pin on its field like
# any other, and a chosen clear can itself be pinned as the empty state, in
# force once the cleared field is read back.
def test_a_clear_ends_the_pin_and_can_be_pinned_empty(tmp_path: Path) -> None:
    store = OwnerStore(tmp_path, genre_report(current=("Drama",)))
    try:
        store.decide("pin", destination="genres")
        assert _pin(store, "genres") is not None
        clear = store.decide("owner_entered", _EMPTY, destination="genres")
        assert (_hold(store, "genres"), _pin(store, "genres")) == (
            clear.decision_id,
            None,
        )
        store.decide("pin", destination="genres")
        assert _pin(store, "genres") == {
            "fingerprint": chosen_value_fingerprint("genres", _EMPTY),
            "in_force": False,
        }
        store.write(clear)
        store.publish(_without_genres(genre_report(current=("Drama",))))
        assert _pin(store, "genres")["in_force"] is True
        assert _asked(store, "genres") is False
    finally:
        store.close()


_EMPTY = {"kind": "empty", "value": None}


def _without_genres(report: MetadataReport) -> MetadataReport:
    """The work read back after its genres were cleared: the field is empty."""
    fields = tuple(
        TargetField(field.destination, TargetFieldState.EMPTY, None)
        if field.destination is TargetDestination.GENRES
        else field
        for field in report.target.fields
    )
    target = report.target
    return MetadataReport.create(
        target=TargetFacts(target.connection, target.library, target.item, fields),
        file=report.file,
        local_claims=report.local_claims,
        local_reasons=report.local_reasons,
        providers=report.providers,
    )


# R5: the authored description's second hold write keeps the pin. The
# authored decision is Automatic's; the owner's own advice choice refuses a
# pin (`test_a_pin_never_reopens_a_description_choice`).
def test_a_pinned_description_keeps_its_pin_over_an_authored_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mechanical_librarian.domain.human_decision import HumanDecision
    from mechanical_librarian.domain.review_case import ReviewCase
    from tests.support.description_advice import (
        _prepared,
        qualify_description_advice,
    )

    qualify_description_advice(monkeypatch)
    repository, store, binding, original, _ = _prepared(tmp_path)
    store.admit(original.candidate_id, binding, "automatic_policy", "e" * 64, 11)
    connection = repository._connection
    assert connection.execute(
        "SELECT count(*) FROM current_description_decisions WHERE review_case_id=?",
        (binding.review_case_id,),
    ).fetchone() == (1,)
    case = ReviewCase.create_from_data(
        json.loads(repository.get_review_case(binding.review_case_id))["case"]
    )
    proposal = next(
        value for value in case.proposals if value["destination"] == "description"
    )
    pin = HumanDecision.create(
        case,
        proposal_id=str(proposal["proposal_id"]),
        target_fingerprint=case.target_fingerprint,
        destination="description",
        actor="owner",
        outcome="pin",
        selected_value=None,
        idempotency_key="owner-pins-description",
        occurred_at=12,
    )
    repository.record_human_decision(pin)
    assert connection.execute(
        "SELECT count(*) FROM current_description_decisions WHERE review_case_id=?",
        (binding.review_case_id,),
    ).fetchone() == (0,)
    assert connection.execute(
        "SELECT record.answer_id,record.pin_fingerprint IS NOT NULL,"
        + pin_in_force_sql(":case", "'description'")
        + case_record_sql(":case", "'description'").removeprefix("SELECT 1"),
        {"case": binding.review_case_id},
    ).fetchall() == [(pin.decision_id, 1, 1)]
    repository.close()


# AC5 and D5: a value edited elsewhere asks again, with the pinned value, and
# Automatic still records nothing.
def test_a_changed_value_asks_again_with_the_pinned_value(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    pinned = store.case.review_case_id
    store.decide("pin")
    store.publish(rewritten(report, title=ANOTHER))
    assert store.case.review_case_id != pinned
    assert _pin(store)["in_force"] is False
    field = _field(store, "title")
    assert (field["classification"], field["classification_cause"]) == (
        "needs_attention",
        "pin_changed",
    )
    assert field["pin"] == {
        "state": "changed",
        "value": {"destination": "title", "state": "value", "value": ORIGINAL},
    }
    assert field["pin_control"] == {"pin": "available", "unpin": True}
    assert _asked(store) is True
    assert [
        decision
        for decision in _automatic(store, "title")
        if decision.review_case_id == store.case.review_case_id
    ] == []
    assert _queued(store) is None
    store.close()


# AC5 and D6.
def test_a_value_that_returns_puts_the_pin_back_in_force(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    store.decide("pin")
    store.publish(rewritten(report, title=ANOTHER))
    assert _pin(store)["in_force"] is False
    assert _asked(store) is True
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _pin(store)["in_force"] is True
    assert _asked(store) is False
    assert _field(store, "title")["classification_cause"] == "pinned"
    store.close()


# C.3 and D11: missing, null and empty are one empty value.
@pytest.mark.parametrize("state", [TargetFieldState.EMPTY, TargetFieldState.NULL])
def test_an_empty_field_pinned_empty_stays_pinned_however_it_is_stored(
    tmp_path: Path, state: TargetFieldState
) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    subtitle = next(
        field for field in report.target.fields if field.destination.value == "subtitle"
    )
    assert subtitle.state is TargetFieldState.MISSING
    store.decide("pin", destination="subtitle")
    store.publish(_with_state(report, "subtitle", state, publisher="Changed Elsewhere"))
    assert _pin(store, "subtitle")["in_force"] is True
    assert _asked(store, "subtitle") is False
    field = _field(store, "subtitle")
    assert field["classification_reason"] == (
        "You pinned this field empty. Mechanical Librarian will not ask about it "
        "or fill it unless it changes in Audiobookshelf."
    )
    store.close()


# C.3 and D12 (R6): nothing can be compared with an unreadable value.
def test_an_unreadable_later_value_reads_as_changed(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    store.decide("pin")
    store.publish(_with_state(report, "title", TargetFieldState.UNLOADED))
    assert _pin(store)["in_force"] is False
    field = _field(store, "title")
    assert field["pin"]["state"] == "changed"
    # Nothing readable to pin again, so only Unpin is offered.
    assert field["pin_control"] == {"pin": None, "unpin": True}
    store.close()


# AC6 and D1.
def test_a_pinned_title_is_not_asked_again_under_ask_me(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    assert _asked(store) is True
    store.decide("pin")
    assert _asked(store) is False
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _asked(store) is False
    field = _field(store, "title")
    assert (field["classification"], field["classification_cause"]) == (
        "human_recorded",
        "pinned",
    )
    store.close()


# AC6 and D2: Automatic's abstention asks today; a pin stops it.
def test_a_pinned_title_is_not_asked_again_when_automatic_abstains(
    tmp_path: Path,
) -> None:
    store, _, _ = _store(tmp_path)
    report = isbn_title_report(empty_target_title=True, titles=(None,))
    store.publish(report)
    assert _field(store, "title")["classification_cause"] == "automatic_abstained"
    assert _asked(store) is True
    store.decide("pin")
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _asked(store) is False
    assert _field(store, "title")["classification_cause"] == "pinned"
    store.close()


# AC11: a field with nothing to choose still asks once its pinned value changes.
def test_a_changed_pin_joins_attention_without_alternatives(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    publisher = _field(store, "publisher")
    assert publisher["classification"] == "unsupported"
    assert publisher["inventory_role"] == "contextual"
    assert _asked(store, "publisher") is False
    store.decide("pin", destination="publisher")
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _asked(store, "publisher") is True
    field = _field(store, "publisher")
    assert (field["classification"], field["classification_cause"]) == (
        "needs_attention",
        "pin_changed",
    )
    # Its value changed and it needs the owner, so it leaves the panel.
    assert field["inventory_role"] == "primary"
    assert field["pin_control"] == {"pin": "available", "unpin": True}
    store.close()


# L.17: a field with nothing to decide is pinned where it sits.
def test_a_field_with_nothing_to_decide_offers_a_pin(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    publisher = _field(store, "publisher")
    assert (publisher["inventory_role"], publisher["classification_cause"]) == (
        "contextual",
        "nothing_to_decide",
    )
    assert publisher["pin_control"] == {"pin": "available", "unpin": False}
    cover = _field(store, "cover")
    assert cover["inventory_role"] == "contextual"
    assert cover["pin_control"] is None
    # Nothing readable, nothing to pin.
    store.publish(_with_state(report, "publisher", TargetFieldState.UNLOADED))
    unreadable = _field(store, "publisher")
    assert unreadable["inventory_role"] == "contextual"
    assert unreadable["pin_control"] is None
    store.close()


def test_a_pinned_field_with_nothing_to_decide_stays_where_it_was(
    tmp_path: Path,
) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    assert _library_pinned_count(store) == 0
    store.decide("pin", destination="publisher")

    def pinned() -> None:
        field = _field(store, "publisher")
        assert field["inventory_role"] == "contextual"
        assert (field["classification"], field["classification_cause"]) == (
            "human_recorded",
            "pinned",
        )
        assert field["classification_reason"] == (
            "You pinned this field empty. Mechanical Librarian will not ask about "
            "it or fill it unless it changes in Audiobookshelf."
        )
        assert field["pin"]["state"] == "in_force"
        assert field["pin_control"] == {"pin": "available", "unpin": True}
        assert field["owner_control"] is None
        assert _asked(store, "publisher") is False
        assert _library_pinned_count(store) == 1

    pinned()
    # A later case of the work keeps it there.
    store.publish(rewritten(report, title=ANOTHER))
    pinned()
    # A pinned field that had a question keeps its own tab.
    store.decide("pin")
    assert _field(store, "title")["inventory_role"] == "primary"
    assert _library_pinned_count(store) == 2
    store.close()


# C2b-15: the unpin is the owner's answer on this case, so the field reads
# as decided, never as having nothing to decide.
def test_unpin_on_a_field_with_nothing_to_decide_reads_as_decided(
    tmp_path: Path,
) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    store.decide("pin", destination="publisher")
    store.decide("unpin", destination="publisher")
    field = _field(store, "publisher")
    assert (field["inventory_role"], field["classification_cause"]) == (
        "primary",
        "human_decided",
    )
    assert field["pin"] is None
    assert field["pin_control"] == {"pin": "available", "unpin": False}
    assert _library_pinned_count(store) == 0
    store.close()


# R-f: a suspended field keeps its row whether its pin stands or has changed.
def test_a_suspended_field_with_nothing_to_decide_keeps_its_row_pinned(
    tmp_path: Path,
) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    store.repository.suspend_field_effect_capability(
        scope="destination",
        connection=report.target.connection,
        destination="publisher",
        reason="field_unavailable",
        changed_at=store.clock(),
    )
    suspended = _field(store, "publisher")
    assert suspended["inventory_role"] == "contextual"
    assert suspended["classification_cause"] == "external_field_failure"
    assert suspended["pin_control"] == {"pin": "available", "unpin": False}
    store.decide("pin", destination="publisher")
    pinned = _field(store, "publisher")
    assert (pinned["inventory_role"], pinned["classification_cause"]) == (
        "contextual",
        "pinned",
    )
    assert pinned["pin_control"] == {"pin": "available", "unpin": True}
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    changed = _field(store, "publisher")
    assert changed["pin"]["state"] == "changed"
    assert changed["inventory_role"] == "contextual"
    assert changed["classification_cause"] == "external_field_failure"
    assert changed["pin_control"] == {"pin": "available", "unpin": True}
    assert _asked(store, "publisher") is False
    store.close()


# AC11 and D10: a suspended field waits, as an ordinary question does.
def test_a_changed_pin_on_a_suspended_field_waits(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    store.decide("pin")
    store.repository.suspend_field_effect_capability(
        scope="destination",
        connection=report.target.connection,
        destination="title",
        reason="field_unavailable",
        changed_at=store.clock(),
    )
    store.publish(rewritten(report, title=ANOTHER))
    assert _pin(store)["in_force"] is False
    assert _asked(store) is False
    assert _field(store, "title")["classification_cause"] != "pin_changed"
    store.repository.clear_field_effect_capability_suspension(
        scope="destination",
        connection=report.target.connection,
        destination="title",
        reason="field_unavailable",
        observed_at=store.clock(),
    )
    assert _asked(store) is True
    store.close()


# AC4: the record's pin is exactly a digest (ADR-0108).
@pytest.mark.parametrize(
    "pin",
    ["a" * 63, "a" * 65, "A" * 64, "g" * 64, 1],
    ids=["short", "long", "upper_case", "not_hex", "integer"],
)
def test_the_record_refuses_a_malformed_pin(genres: OwnerStore, pin: object) -> None:
    genres.decide("pin")
    connection = genres.repository._connection
    assert _pin(genres) is not None
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        connection.execute(
            "UPDATE owner_field_answers SET pin_fingerprint=? WHERE destination='title'",
            (pin,),
        )


# AC9: a pin stands only on an answer with no Resume asked of it.
def test_the_record_refuses_a_pin_beside_an_open_resume(genres: OwnerStore) -> None:
    genres.decide("pin")
    connection = genres.repository._connection
    request = "owner_field_event_" + "e" * 64
    connection.execute("BEGIN")
    connection.execute(
        "INSERT INTO owner_field_events(event_id,connection_name,library_name,"
        "item_id,destination,kind,request_id,over_ordinal,scan_id,answer_id,"
        "occurred_at) SELECT ?,connection_name,library_name,item_id,destination,"
        "'resume_requested',NULL,ordinal,'scan-1',answer_id,1 "
        "FROM owner_field_answers WHERE destination='title'",
        (request,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        connection.execute(
            "UPDATE owner_field_answers SET open_resume_id=? WHERE destination='title'",
            (request,),
        )
    connection.execute("ROLLBACK")


# AC9: a Resume that read the hold before a pin was recorded on it is refused
# in its own write, so the pin is never released with the hold.
def test_a_return_request_never_releases_a_pinned_hold(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    pin = store.decide("pin")
    before = _record(store)
    scan = store.repository.request_review_scan(
        report.target.connection,
        report.target.library,
        "library-ebooks",
        "a" * 64,
        store.clock(),
    )
    at = store.clock()
    assert (
        store.repository.request_owner_field_resume(
            store.case.review_case_id, "title", pin.decision_id, scan.scan_id, at, at
        )
        is False
    )
    assert _record(store) == before
    store.close()


# Codex P2 on #1911: clocks are whole seconds and a re-admitted case keeps its
# old hold row, so an owner write on the returned case must still outrank the
# copy carried to a sibling case in the same second.
@pytest.mark.parametrize("ending", ["unpin", "keep_current"])
def test_an_owner_write_in_the_same_second_outranks_a_carried_pin(
    tmp_path: Path, ending: str
) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    ticks = store.ticks

    def fixed_clock() -> int:
        return ticks

    def advance() -> None:
        # The idempotency key follows the tick counter; the clock stays put.
        store.ticks += 1

    store.clock = fixed_clock  # type: ignore[method-assign]  # reason: one clock value throughout reproduces the whole-second tie.
    first = store.publish(report).review_case_id
    advance()
    store.decide("pin")
    second = store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert second.review_case_id != first
    assert _pin(store) is not None
    assert store.publish(report).review_case_id == first
    advance()
    decision = store.decide(ending)
    third = store.publish(rewritten(report, publisher="Changed Once More"))
    assert third.review_case_id not in {first, second.review_case_id}
    assert _pin(store) is None
    if ending == "unpin":
        assert _asked(store) is True
    else:
        assert _hold(store) == decision.decision_id
    store.close()


# The two Resume writers take a fresh rowid too, so a Resume asked or
# completed on the returned case in the same second is not undone by the
# active copy carried to a sibling case.
@pytest.mark.parametrize("resume", ["requested", "released"])
def test_a_resume_in_the_same_second_outranks_a_carried_hold(
    tmp_path: Path, resume: str
) -> None:
    store, report, _ = _store(tmp_path)
    ticks = store.ticks

    def fixed_clock() -> int:
        return ticks

    store.clock = fixed_clock  # type: ignore[method-assign]  # reason: one clock value throughout reproduces the whole-second tie.
    first = store.publish(report).review_case_id
    store.ticks += 1
    held = store.decide("keep_current")
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _hold(store) == held.decision_id
    assert store.publish(report).review_case_id == first
    if resume == "requested":
        scan = store.repository.request_review_scan(
            report.target.connection,
            report.target.library,
            "library-ebooks",
            "a" * 64,
            ticks,
        )
        assert store.repository.request_owner_field_resume(
            first, "title", held.decision_id, scan.scan_id, ticks, ticks
        )
    else:
        assert resume_field(store.repository, first, "title", ticks)
    store.publish(rewritten(report, publisher="Changed Once More"))
    assert store.case.review_case_id != first
    # The record follows the work: a Resume asked stays open over its answer
    # on the next case, and one completed leaves no answer (ADR-0108).
    record = _record(store)
    if resume == "requested":
        assert record is not None and record["open_resume_id"] is not None
    else:
        assert record is None
    store.close()


# AC9 and AC16.
def test_a_pinned_field_reads_pinned_and_offers_unpin(genres: OwnerStore) -> None:
    assert _field(genres, "title")["pin_control"] == {
        "pin": "available",
        "unpin": False,
    }
    genres.decide("pin")
    field = _field(genres, "title")
    assert field["pin"] == {"state": "in_force", "value": field["current"]}
    # The page marks the current value's card and sends Unpin from it.
    assert field["pin_control"] == {"pin": "available", "unpin": True}
    assert field["manual_hold"] is False
    assert (field["classification"], field["classification_cause"]) == (
        "human_recorded",
        "pinned",
    )
    assert field["classification_reason"] == (
        "You pinned this value. Mechanical Librarian will not ask about it or "
        "change it unless it changes in Audiobookshelf."
    )
    assert _field(genres, "cover")["pin_control"] is None


# AC9 and D21: a pin is never released by Resume; Unpin is the control.
def test_resume_is_refused_while_pinned(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    assert _automatic(store, "title")[-1].review_case_id == store.case.review_case_id
    pin = store.decide("pin")
    # A held answer beside an automatic decision would show Resume here.
    assert _hold(store) == pin.decision_id
    assert _field(store, "title")["manual_hold"] is False
    # The SQLite store serves every port in the served composition; the cast
    # stands in for the composition's separate session store.
    repository = cast("Any", store.repository)
    library = (report.target.connection, report.target.library)
    application = compose_review_application(
        state_owner=repository,
        session_store=repository,
        case_store=repository,
        decision_store=repository,
        library_store=repository,
        policy_store=repository,
        verify_password=lambda value: False,
        session_digest=lambda token: token,
        client_digest=lambda value: value,
        clock=store.clock,
        session_lifetime_seconds=600,
        graceful_shutdown_seconds=30,
        field_application=ContinuousGuardedApplication(
            repository,
            {library: store.service},
            {library: FakeCapabilityObserver(CAPABILITY)},
            PersistedFieldEffectAuthority(repository),
            (library,),
            store.clock,
            lambda *_: None,
        ),
    )
    with pytest.raises(GuardedApplicationError) as refused:
        application.return_to_automatic(store.case.review_case_id, "title")
    assert refused.value.args[0] == "manual_hold"
    assert _hold(store) == pin.decision_id
    store.close()


# AC11 and D9.
def test_the_changed_field_suggests_the_pinned_value(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    store.decide("pin")
    store.publish(rewritten(report, title=ANOTHER))
    control = _field(store, "title")["owner_control"]
    assert {"rule": "pinned", "value": ORIGINAL} in control["entry"]["suggestions"]
    answer = store.decide("owner_entered", {"kind": "text", "value": ORIGINAL})
    assert (_hold(store), _pin(store)) == (answer.decision_id, None)
    store.close()


# KC8 and K-D16: the pinned value reads with the JSON type it was chosen or
# held with. A boolean stays a boolean, a list a list, a series its objects
# with a null sequence, whether the pin keeps a chosen value or the case's.
@pytest.mark.parametrize(
    "destination,chosen,held,expected",
    [
        ("explicit", {"kind": "boolean", "value": True}, None, True),
        ("explicit", None, False, False),
        (
            "title",
            {"kind": "text", "value": "Synthetic Journey"},
            None,
            "Synthetic Journey",
        ),
        (
            "series",
            None,
            (TargetSeries("Example Series", "3"), TargetSeries("Another Series")),
            [
                {"name": "Example Series", "sequence": "3"},
                {"name": "Another Series", "sequence": None},
            ],
        ),
        ("authors", None, None, ["Example Author"]),
    ],
    ids=["chosen_boolean", "held_boolean", "chosen_text", "held_series", "held_list"],
)
def test_a_pinned_value_reads_with_its_own_type(
    tmp_path: Path, destination: str, chosen: Any, held: Any, expected: Any
) -> None:
    report = synthetic_audiobook_bibliographic_report(adult_product=True)
    if held is not None:
        fields = tuple(
            TargetField(field.destination, TargetFieldState.VALUE, held)
            if field.destination.value == destination
            else field
            for field in report.target.fields
        )
        report = MetadataReport.create(
            target=replace(report.target, fields=fields),
            file=report.file,
            local_claims=report.local_claims,
            local_reasons=report.local_reasons,
            providers=report.providers,
            audio=report.audio,
        )
    store = OwnerStore(tmp_path, report)
    if chosen is not None:
        store.decide("accept", chosen, destination=destination)
    store.decide("pin", destination=destination)
    pin = _field(store, destination)["pin"]
    assert pin == {
        "state": "in_force" if chosen is None else "waiting",
        "value": {"destination": destination, "state": "value", "value": expected},
    }
    assert type(pin["value"]["value"]) is type(expected)
    store.close()


# AC12, J2 and K-D9: while Automatic's change of the field waits the page
# offers no pin and says why; the owner's own choice never holds a pin back.
def test_pin_control_says_a_change_waits(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    available = {"pin": "available", "unpin": False}
    assert _field(store, "title")["pin_control"] == available
    queued = _queued(store)
    assert queued is not None
    store.plan(queued)
    assert _field(store, "title")["pin_control"] == {
        "pin": "change_waiting",
        "unpin": False,
    }
    store.close()


def test_pin_control_offers_a_pin_over_the_owners_unwritten_choice(
    genres: OwnerStore,
) -> None:
    decision = genres.decide("owner_entered", ORCHARD)
    available = {"pin": "available", "unpin": False}
    assert _field(genres, "title")["pin_control"] == available
    genres.plan(decision)
    assert _field(genres, "title")["pin_control"] == available


# AC17: each work counts its pinned fields.
def test_the_library_counts_pinned_fields(genres: OwnerStore) -> None:
    assert _library_pinned_count(genres) == 0
    genres.decide("pin")
    assert _library_pinned_count(genres) == 1
    genres.decide("pin", destination="genres")
    assert _library_pinned_count(genres) == 2
    genres.decide("unpin")
    assert _library_pinned_count(genres) == 1


# AC7 and D24: the projection a title recovery requeues mints nothing over a
# pin carried to a later case.
def test_title_recovery_records_nothing_for_a_pinned_title(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.repository.pause_automatic_safety(True, store.clock())
    store.publish(report)
    pin = store.decide("pin")
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert (_hold(store), _pin(store)["in_force"]) == (pin.decision_id, True)
    store.repository.pause_automatic_safety(False, store.clock())
    store.repository.persist_shadow_outcome(store.case.review_case_id, store.clock())
    assert _automatic(store, "title") == []
    store.close()


# AC3, KC4 and K-D12: a pin or unpin plans nothing and changes nothing of
# what is planned: after accept, pin and unpin both pending readers return
# exactly the accept.
def test_pin_and_unpin_plan_nothing(genres: OwnerStore) -> None:
    pin = genres.decide("pin")
    unpin = genres.decide("unpin")
    repository = genres.repository
    assert _pending(genres) == set()
    assert repository.pending_field_effect_decision_counts() == ()
    assert authority_from_decision(pin) is None
    assert authority_from_decision(unpin, allow_corrective_keep_current=True) is None
    accept = genres.decide("accept", FANTASY, destination="genres")
    counts = repository.pending_field_effect_decision_counts()
    assert _pending(genres) == {accept.decision_id}
    assert _pending_counts(genres) == 1
    genres.decide("pin", destination="genres")
    assert _pending(genres) == {accept.decision_id}
    assert repository.pending_field_effect_decision_counts() == counts
    genres.decide("unpin", destination="genres")
    assert _pending(genres) == {accept.decision_id}
    assert repository.pending_field_effect_decision_counts() == counts
    assert repository._connection.execute(
        "SELECT count(*) FROM field_effects"
    ).fetchone() == (0,)


# AC7 and D3: a later case Automatic would decide records nothing for the
# pinned field, and nothing is queued.
def test_automatic_records_nothing_for_a_pinned_field(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.publish(report)
    assert _queued(store) is not None
    store.decide("pin")
    assert _queued(store) is None
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert [
        decision
        for decision in _automatic(store, "title")
        if decision.review_case_id == store.case.review_case_id
    ] == []
    assert _queued(store) is None
    assert _asked(store) is False
    store.close()


# D7: a changed pin is pinned again at the new value.
def test_pin_this_value_pins_the_new_value(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path, title="ask")
    store.publish(report)
    store.decide("pin")
    first = _pin(store)
    store.publish(rewritten(report, title=ANOTHER))
    again = store.decide("pin")
    second = _pin(store)
    assert _hold(store) == again.decision_id
    assert second["in_force"] is True
    assert second["fingerprint"] != first["fingerprint"]
    assert _field(store, "title")["pin"] == {
        "state": "in_force",
        "value": {"destination": "title", "state": "value", "value": ANOTHER},
    }
    assert _asked(store) is False
    store.close()


# D8 and D22: after Unpin the field asks as an ordinary field, and Resume is
# offered once Automatic records a decision for it.
def test_unpin_lets_the_field_be_asked_again(tmp_path: Path) -> None:
    store, report, _ = _store(tmp_path)
    store.repository.pause_automatic_safety(True, store.clock())
    store.publish(report)
    pin = store.decide("pin")
    store.publish(rewritten(report, title=ANOTHER))
    store.decide("unpin")
    assert _field(store, "title")["pin"] is None
    assert _asked(store) is True
    store.repository.pause_automatic_safety(False, store.clock())
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert (_hold(store), _pin(store)) == (pin.decision_id, None)
    assert _automatic(store, "title")[-1].review_case_id == store.case.review_case_id
    assert _field(store, "title")["manual_hold"] is True
    store.close()


# D33: a replay with the same key returns the original pin.
def test_a_pin_replays_by_idempotency_key(genres: OwnerStore) -> None:
    pin = genres.decide("pin")
    before = _rows(genres)
    assert genres.repository.record_human_decision(pin) is not None
    assert _rows(genres) == before
    assert _hold(genres) == pin.decision_id


# D34: a pin made in Preview holds once Automatic is turned on.
def test_a_pin_in_preview_is_kept_after_automatic_is_turned_on(
    tmp_path: Path,
) -> None:
    store, report, _ = _store(tmp_path, posture="preview")
    first = store.publish(report)
    assert _automatic(store, "title") == []
    pin = store.decide("pin")
    store.repository.activate_qualified_automatic(store.clock())
    store.repository.persist_shadow_outcome(first.review_case_id, store.clock())
    store.publish(rewritten(report, publisher="Changed Elsewhere"))
    assert _automatic(store, "title") == []
    assert (_hold(store), _pin(store)["in_force"]) == (pin.decision_id, True)
    assert _asked(store) is False
    store.close()
