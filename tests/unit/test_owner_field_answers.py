"""The record of the owner's answers: its tables, counter, events, validator and fold (#2038).

ADR-0108 keeps one record row per field of a book. These tests pin its
storage rules on a bare schema, each open-validator rule on a ledger that
breaks exactly that rule, the event id's exact input, the counter, and the
pure fold's rules for every kind of owner answer.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from mechanical_librarian.adapters.persistence import (
    decisions,
    owner_field_answers,
    schema,
)
from mechanical_librarian.adapters.persistence.level_reconciliation import (
    HUMAN_DECISION_INSERT_SQL,
)
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.owner_field_answers import (
    next_ordinal,
    stored_answers,
    validate_owner_field_answers,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.human_decision import (
    chosen_value_fingerprint,
    field_fingerprint,
    pinned_field_fingerprint,
)
from mechanical_librarian.domain.owner_field_answer import (
    OwnerAnswerRow,
    OwnerFieldAnswer,
    fold_owner_field_answers,
    owner_field_event_id,
    restored_answer,
)
from tests.support.cover_taste import record_owner_cover_decisions
from tests.support.description_advice import _prepared
from tests.support.genre_reports import genre_report
from tests.support.ledger_partition import copied_store, opens, regenerate, rewrite
from tests.support.owner_field_record import (
    assert_record_recomputes,
    record_recomputes,  # noqa: F401
)
from tests.support.owner_values import OwnerStore

_CASE = "review_case_" + "a" * 64
_OTHER_CASE = "review_case_" + "b" * 64
_KEY = ("primary", "books", "work")
_GENRES = '{"kind":"list","value":["Fantasy"]}'
_SELECTION = "cover_selection_" + "c" * 64


def _id(tag: str) -> str:
    return "human_decision_" + hashlib.sha256(tag.encode()).hexdigest()


def _bare() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    schema._create_schema(connection)
    return connection


def _decision(
    connection: sqlite3.Connection,
    tag: str,
    *,
    destination: str = "genres",
    outcome: str = "accept",
    selected: str = _GENRES,
    case: str = _CASE,
    episode: int = 0,
    cause: str | None = None,
) -> str:
    """A ledger decision row as the store writes its columns; the document
    carries only what the record reads."""
    decision_id = _id(tag)
    connection.execute(
        HUMAN_DECISION_INSERT_SQL,
        (
            next_ordinal(connection),
            decision_id,
            case,
            "review_proposal_" + "d" * 64,
            "e" * 64,
            destination,
            "owner",
            outcome,
            selected,
            hashlib.sha256(("idempotency" + tag).encode()).hexdigest(),
            "f" * 64,
            7,
            json.dumps({"cause": cause, "episode": episode}),
            *_KEY,
            case,
            "null",
            None,
        ),
    )
    return decision_id


def _event(
    connection: sqlite3.Connection,
    kind: str,
    *,
    request: str | None = None,
    over: int = 2,
    destination: str = "genres",
    key: tuple[str, str, str] = _KEY,
    event_id: str | None = None,
) -> str:
    previous = connection.execute(
        "SELECT event_id FROM owner_field_events WHERE connection_name=? AND "
        "library_name=? AND item_id=? AND destination=? ORDER BY rowid DESC LIMIT 1",
        (*key, destination),
    ).fetchone()
    values = {
        "connection_name": key[0],
        "library_name": key[1],
        "item_id": key[2],
        "destination": destination,
        "kind": kind,
        "request_id": request,
        "over_ordinal": over,
        "scan_id": "scan-1" if kind == "resume_requested" else None,
        "answer_id": None,
        "occurred_at": 9,
    }
    event_id = event_id or owner_field_event_id(
        connection_name=key[0],
        library_name=key[1],
        item_id=key[2],
        destination=destination,
        kind=kind,
        request_id=request,
        over_ordinal=over,
        scan_id=cast("str | None", values["scan_id"]),
        answer_id=None,
        occurred_at=9,
        previous_event_id=None if previous is None else previous[0],
    )
    connection.execute(
        "INSERT INTO owner_field_events(event_id,connection_name,library_name,"
        "item_id,destination,kind,request_id,over_ordinal,scan_id,answer_id,"
        "occurred_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (event_id, *values.values()),
    )
    return event_id


@pytest.fixture
def ledger() -> Iterator[sqlite3.Connection]:
    """One genres answer in the record, as the store keeps it, given after a
    title decision the record leaves out."""
    connection = _bare()
    _decision(
        connection,
        "filler",
        destination="title",
        outcome="keep_current",
        selected="null",
    )
    decision = _decision(connection, "answer")
    owner_field_answers.record_answer(connection, "human", decision)
    validate_owner_field_answers(connection)
    try:
        yield connection
    finally:
        connection.close()


def _firing(connection: sqlite3.Connection) -> list[int]:
    """Which record rules a ledger breaks; the event chain counts as -1."""
    fired = [
        index
        for index, rule in enumerate(owner_field_answers._RECORD_RULES)
        if connection.execute(rule).fetchone() is not None
    ]
    try:
        validate_owner_field_answers(connection)
    except MetadataReportError:
        if not fired:
            fired.append(-1)
    return fired


def _refused(connection: sqlite3.Connection, rule: int) -> None:
    assert _firing(connection) == [rule]
    with pytest.raises(MetadataReportError, match="unavailable or incompatible"):
        validate_owner_field_answers(connection)


# -- storage rules ----------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("source", "automatic"),
        ("outcome", "withdraw"),
        ("outcome", "unpin"),
        ("answer_id", "automatic_decision_" + "a" * 64),
        ("ordinal", 0),
        ("episode", 99),
        ("previous_answer_id", "same"),
        ("carry_eligible", 2),
        ("pin_fingerprint", "x" * 64),
        ("offer_digest", "a" * 64),
        ("seen_target_digest", "a" * 64),
        ("cover_selection_id", _SELECTION),
        ("value_document", "{}"),
        ("review_case_id", "review_case_x"),
        ("destination", ""),
    ),
)
def test_the_record_refuses_a_row_its_checks_rule_out(
    ledger: sqlite3.Connection, column: str, value: object
) -> None:
    """Every CHECK holds on its own: the question halves both or neither, a
    selection only on a cover, an authored row only as a description, the
    episode before the ordinal, no answer its own previous one."""
    if value == "same":
        value = ledger.execute("SELECT answer_id FROM owner_field_answers").fetchone()[
            0
        ]
    with pytest.raises(sqlite3.IntegrityError):
        ledger.execute(f"UPDATE owner_field_answers SET {column}=?", (value,))


@pytest.mark.parametrize(
    "changes",
    (
        {
            "destination": "cover",
            "cover_selection_id": _SELECTION,
            "pin_fingerprint": "a" * 64,
        },
        {"source": "authored_description", "outcome": "authored"},
        {
            "source": "authored_description",
            "outcome": "authored",
            "destination": "title",
        },
        {
            "pin_fingerprint": "a" * 64,
            "open_resume_id": "owner_field_event_" + "a" * 64,
        },
    ),
    ids=("pinned-cover", "authored-genres", "authored-title", "pinned-and-resumed"),
)
def test_the_record_refuses_inconsistent_columns(
    ledger: sqlite3.Connection, changes: dict[str, object]
) -> None:
    assignments = ",".join(f"{column}=?" for column in changes)
    with pytest.raises(sqlite3.IntegrityError):
        ledger.execute(
            f"UPDATE owner_field_answers SET {assignments}", tuple(changes.values())
        )


def test_the_counter_is_one_row_and_never_below_zero(
    ledger: sqlite3.Connection,
) -> None:
    for statement in (
        "INSERT INTO owner_answer_ordinal VALUES(2,0)",
        "INSERT INTO owner_answer_ordinal VALUES(1,0)",
        "UPDATE owner_answer_ordinal SET ordinal=-1",
        "UPDATE owner_answer_ordinal SET ordinal=1.5",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            ledger.execute(statement)


@pytest.mark.parametrize(
    ("kind", "answers", "scan"),
    (
        ("resumed", None, "scan"),
        ("resume_requested", None, None),
        ("resume_requested", "owner_field_event_" + "a" * 64, "scan"),
        ("resume_completed", None, None),
        ("retired", "owner_field_event_" + "a" * 64, None),
    ),
)
def test_an_event_names_its_request_exactly_when_it_answers_one(
    ledger: sqlite3.Connection, kind: str, answers: str | None, scan: str | None
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        ledger.execute(
            "INSERT INTO owner_field_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                "owner_field_event_" + "b" * 64,
                *_KEY,
                "genres",
                kind,
                answers,
                1,
                scan,
                None,
                9,
            ),
        )


def test_events_are_append_only_and_retired_is_admitted(
    ledger: sqlite3.Connection,
) -> None:
    """`retired` is a kind the record admits; nothing writes it until C4'."""
    request = _event(ledger, "resume_requested")
    _event(ledger, "retired", destination="title")
    for statement in (
        "UPDATE owner_field_events SET occurred_at=10",
        "DELETE FROM owner_field_events",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            ledger.execute(statement)
    assert ledger.execute(
        "SELECT kind FROM owner_field_events ORDER BY rowid"
    ).fetchall() == [("resume_requested",), ("retired",)]
    assert request.startswith("owner_field_event_")


# -- the event id ------------------------------------------------------------


def test_an_event_id_is_the_digest_of_its_columns_and_the_keys_previous_event() -> None:
    values: dict[str, object] = {
        "connection_name": "primary",
        "library_name": "books",
        "item_id": "work",
        "destination": "title",
        "kind": "resume_completed",
        "request_id": "owner_field_event_" + "1" * 64,
        "over_ordinal": 4,
        "scan_id": None,
        "answer_id": _id("answer"),
        "occurred_at": 11,
        "previous_event_id": "owner_field_event_" + "2" * 64,
    }
    expected = (
        "owner_field_event_"
        + hashlib.sha256(
            (
                '{"answer_id":"' + _id("answer") + '","connection_name":"primary",'
                '"destination":"title","item_id":"work","kind":"resume_completed",'
                '"library_name":"books","occurred_at":11,"over_ordinal":4,'
                '"previous_event_id":"owner_field_event_' + "2" * 64 + '",'
                '"request_id":"owner_field_event_' + "1" * 64 + '","scan_id":null,'
                '"schema_version":"owner-field-event-v1"}'
            ).encode()
        ).hexdigest()
    )
    assert owner_field_event_id(**values) == expected  # type: ignore[arg-type]  # reason: the keyword values are exactly the function's typed parameters.
    for name in values:
        changed = dict(
            values, **{name: 5 if name in {"over_ordinal", "occurred_at"} else "other"}
        )
        assert owner_field_event_id(**changed) != expected  # type: ignore[arg-type]  # reason: as above, one value changed.


# -- the open validator, one rule at a time --------------------------------


def test_the_validator_runs_when_a_store_opens(tmp_path: Path) -> None:
    store = OwnerStore(tmp_path, genre_report())
    store.decide("keep_current", destination="genres")
    connection = store.repository._connection
    with connection:
        connection.execute("UPDATE owner_answer_ordinal SET ordinal=0")
    store.close()
    with pytest.raises(MetadataReportError, match="unavailable or incompatible"):
        open_repository(tmp_path / "owner.sqlite", create=False)


def test_a_missing_counter_row_is_refused(ledger: sqlite3.Connection) -> None:
    ledger.execute("DELETE FROM owner_answer_ordinal")
    with pytest.raises(MetadataReportError):
        validate_owner_field_answers(ledger)


def test_a_counter_behind_an_ordinal_is_refused(ledger: sqlite3.Connection) -> None:
    ledger.execute("UPDATE owner_answer_ordinal SET ordinal=0")
    _refused(ledger, 0)


def test_an_ordinal_given_twice_is_refused(ledger: sqlite3.Connection) -> None:
    ledger.execute(
        "INSERT INTO owner_authored_descriptions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            _id("authored"),
            _CASE,
            "description_candidate_" + "c" * 64,
            "c" * 64,
            "{}",
            *_KEY,
            "{}",
            2,
            0,
            None,
        ),
    )
    _refused(ledger, 1)


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("answer_id", _id("nothing")),
        ("ordinal", 11),
        ("outcome", "keep_current"),
        ("destination", "title"),
        ("item_id", "another"),
        ("review_case_id", _OTHER_CASE),
        ("value_document", '{"kind":"list","value":["Horror"]}'),
        ("episode", 1),
        ("recorded_at", 8),
        ("display_title", "Another"),
    ),
)
def test_a_human_answer_unlike_its_decision_is_refused(
    ledger: sqlite3.Connection, column: str, value: object
) -> None:
    if column == "ordinal":
        ledger.execute("UPDATE owner_answer_ordinal SET ordinal=20")
    ledger.execute(f"UPDATE owner_field_answers SET {column}=?", (value,))
    _refused(ledger, 2)


def _authored(connection: sqlite3.Connection, *, title: str | None = None) -> str:
    decision_id = _id("authored")
    connection.execute(
        "INSERT INTO owner_authored_descriptions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            decision_id,
            _CASE,
            "description_candidate_" + "c" * 64,
            "c" * 64,
            json.dumps({"occurred_at": 12}),
            *_KEY,
            "{}",
            next_ordinal(connection),
            0,
            title,
        ),
    )
    return decision_id


def test_an_authored_answer_unlike_its_description_is_refused(
    ledger: sqlite3.Connection,
) -> None:
    authored = _authored(ledger, title="Title")
    owner_field_answers.record_answer(ledger, "authored_description", authored)
    assert _firing(ledger) == []
    row = ledger.execute(
        "SELECT source,outcome,value_document,recorded_at,display_title "
        "FROM owner_field_answers WHERE answer_id=?",
        (authored,),
    ).fetchone()
    assert row == ("authored_description", "authored", "null", 12, "Title")
    ledger.execute(
        "UPDATE owner_field_answers SET display_title='Other' WHERE answer_id=?",
        (authored,),
    )
    _refused(ledger, 3)


def _selection(
    connection: sqlite3.Connection, decision: str, selection: str = _SELECTION
) -> None:
    connection.execute(
        "INSERT INTO owner_cover_selections(selection_id,connection_name,library_name,"
        "item_id,decision_id,consideration_id,supersedes_selection_id,occurred_at,"
        "document,cover_took_over) VALUES(?,?,?,?,?,?,NULL,7,?,0)",
        (
            selection,
            *_KEY,
            decision,
            "cover_consideration_" + "c" * 64,
            json.dumps({"schema_version": schema.COVER_SELECTION_AUTHORITY_SCHEMA}),
        ),
    )


def _cover_answer(connection: sqlite3.Connection) -> str:
    decision = _decision(
        connection,
        "cover",
        destination="cover",
        outcome="keep_current",
        selected="null",
    )
    _selection(connection, decision)
    return decision


def test_a_cover_answer_names_its_own_selection(ledger: sqlite3.Connection) -> None:
    cover = _cover_answer(ledger)
    owner_field_answers.record_answer(ledger, "human", cover)
    assert ledger.execute(
        "SELECT cover_selection_id FROM owner_field_answers WHERE answer_id=?", (cover,)
    ).fetchone() == (_SELECTION,)
    assert _firing(ledger) == []


def test_a_cover_answer_naming_another_decisions_selection_is_refused() -> None:
    connection = _bare()
    other = _decision(connection, "other")
    cover = _cover_answer(connection)
    owner_field_answers.record_answer(connection, "human", cover)
    other_selection = "cover_selection_" + "d" * 64
    _selection(connection, other, other_selection)
    connection.execute(
        "UPDATE owner_field_answers SET cover_selection_id=?", (other_selection,)
    )
    _refused(connection, 4)


@pytest.mark.parametrize("previous", ("later", "unknown", "other-field"))
def test_a_previous_answer_that_is_not_an_earlier_answer_of_the_field_is_refused(
    ledger: sqlite3.Connection, previous: str
) -> None:
    named = {
        "later": lambda: _decision(ledger, "later", case=_OTHER_CASE),
        "unknown": lambda: _id("unknown"),
        "other-field": lambda: _decision(ledger, "title", destination="title"),
    }[previous]()
    ledger.execute(
        "UPDATE owner_field_answers SET previous_answer_id=? WHERE destination='genres'",
        (named,),
    )
    _refused(ledger, 5)


@pytest.mark.parametrize("fault", ("none", "closed", "below", "other-field", "missing"))
def test_an_open_resume_names_an_unclosed_request_over_its_answer(
    ledger: sqlite3.Connection, fault: str
) -> None:
    request = _event(
        ledger,
        "resume_requested",
        over=1 if fault == "below" else 2,
        destination="title" if fault == "other-field" else "genres",
    )
    if fault == "closed":
        _event(ledger, "resume_completed", request=request)
    ledger.execute(
        "UPDATE owner_field_answers SET open_resume_id=?",
        (request,),
    )
    if fault == "missing":
        ledger.execute("PRAGMA foreign_keys=OFF")
        ledger.execute(
            "UPDATE owner_field_answers SET open_resume_id=?",
            ("owner_field_event_" + "9" * 64,),
        )
    if fault == "none":
        assert _firing(ledger) == []
    else:
        _refused(ledger, 6)


def test_an_event_answering_another_fields_request_is_refused(
    ledger: sqlite3.Connection,
) -> None:
    request = _event(ledger, "resume_requested", destination="title")
    _event(ledger, "resume_failed", request=request, destination="title")
    assert _firing(ledger) == []
    _event(ledger, "resume_failed", request=request, destination="genres")
    _refused(ledger, 7)


def test_a_request_closed_twice_is_refused(ledger: sqlite3.Connection) -> None:
    request = _event(ledger, "resume_requested")
    _event(ledger, "resume_superseded", request=request)
    assert _firing(ledger) == []
    _event(ledger, "resume_completed", request=request)
    _refused(ledger, 8)


@pytest.mark.parametrize("fault", ("digest", "reordered"))
def test_an_event_whose_id_does_not_recompute_is_refused(
    ledger: sqlite3.Connection, fault: str
) -> None:
    first = _event(ledger, "resume_requested")
    if fault == "digest":
        _event(
            ledger,
            "resume_failed",
            request=first,
            event_id="owner_field_event_" + "0" * 64,
        )
    else:
        # Each id chains to the field's previous event, so a store cannot
        # drop or reorder one unseen.
        second = owner_field_event_id(
            connection_name="primary",
            library_name="books",
            item_id="work",
            destination="genres",
            kind="resume_failed",
            request_id=first,
            over_ordinal=2,
            scan_id=None,
            answer_id=None,
            occurred_at=9,
            previous_event_id=None,
        )
        _event(ledger, "resume_failed", request=first, event_id=second)
    _refused(ledger, -1)


# -- the counter -------------------------------------------------------------


def test_every_owner_decision_takes_the_next_ordinal_and_its_episode(
    tmp_path: Path,
) -> None:
    """Ordinals come from the counter in each answer's transaction, so they
    rise with it; each decision freezes its case's episode (ADR-0108, S14)."""
    store = OwnerStore(tmp_path, genre_report())
    connection = store.repository._connection
    first = store.decide("keep_current", destination="genres")
    second = store.decide("accept", json.loads(_GENRES), destination="genres")
    with connection:
        floor = next_ordinal(connection)
        connection.execute(
            "INSERT INTO review_case_episodes VALUES(?,?,0)",
            (store.case.review_case_id, floor),
        )
    third = store.decide("keep_current", destination="genres")
    rows = connection.execute(
        "SELECT decision_id,ordinal,json_extract(document,'$.episode') "
        "FROM human_decisions ORDER BY ordinal"
    ).fetchall()
    assert rows == [
        (first.decision_id, 1, 0),
        (second.decision_id, 2, 0),
        (third.decision_id, 4, 3),
    ]
    assert (first.episode, third.episode) == (0, 3)
    assert connection.execute(
        "SELECT ordinal FROM owner_answer_ordinal"
    ).fetchone() == (4,)
    store.close()


# -- the fold ----------------------------------------------------------------


def _row(
    tag: str,
    ordinal: int,
    outcome: str = "accept",
    *,
    case: str = _CASE,
    episode: int = 0,
    destination: str = "title",
    source: str = "human",
    cause: str | None = None,
    value: str = '{"kind":"text","value":"Chosen"}',
    shown: str = '{"destination":"title","state":"value","value":"Shown"}',
    selection: str | None = None,
) -> OwnerAnswerRow:
    return OwnerAnswerRow(
        source,  # type: ignore[arg-type]  # reason: a literal of the row's source vocabulary.
        _id(tag),
        ordinal,
        *_KEY,
        destination,
        case,
        episode,
        outcome,
        cause,
        value if outcome in {"accept", "owner_entered"} else "null",
        f"title {tag}",
        ordinal * 10,
        shown,
        selection,
    )


def _record(*rows: OwnerAnswerRow) -> dict[str, object] | None:
    folded = fold_owner_field_answers(rows)
    assert len(folded) <= 1
    answer = next(iter(folded.values()), None)
    return (
        None
        if answer is None
        else {
            "answer": answer.answer_id,
            "previous": answer.previous_answer_id,
            "carry": answer.carry_eligible,
            "pin": answer.pin_fingerprint,
            "outcome": answer.outcome,
        }
    )


def test_each_answer_outcome_is_the_fields_answer_with_its_copies() -> None:
    for outcome in ("accept", "keep_current", "reject", "owner_entered"):
        row = _row("a", 3, outcome)
        (answer,) = fold_owner_field_answers((row,)).values()
        assert (
            answer.source,
            answer.answer_id,
            answer.ordinal,
            answer.outcome,
            answer.value_document,
            answer.display_title,
            answer.recorded_at,
            answer.review_case_id,
            answer.episode,
            answer.previous_answer_id,
            answer.carry_eligible,
            answer.open_resume_id,
            answer.pin_fingerprint,
            answer.cover_selection_id,
            answer.offer_digest,
            answer.seen_target_digest,
        ) == (
            "human",
            _id("a"),
            3,
            outcome,
            row.value_document,
            "title a",
            30,
            _CASE,
            0,
            None,
            True,
            None,
            None,
            None,
            None,
            None,
        )


def test_an_answer_restores_only_an_answer_from_another_case_or_episode() -> None:
    """A then B on one case gives B nothing to restore; C on another case
    restores B; D in a new episode of the first case restores C."""
    a, b = _row("a", 1), _row("b", 2, "keep_current")
    c = _row("c", 3, case=_OTHER_CASE)
    d = _row("d", 5, case=_CASE, episode=4)
    assert _record(a, b)["previous"] is None
    assert _record(a, b, c)["previous"] == _id("b")
    assert _record(a, b, c, d)["previous"] == _id("c")
    assert _record(c, _row("e", 4, case=_OTHER_CASE))["previous"] is None


def test_withdrawing_the_answer_restores_its_previous_one_not_carried() -> None:
    a = _row("a", 1)
    b = _row("b", 2, case=_OTHER_CASE)
    withdraw = _row("w", 3, "withdraw", case=_OTHER_CASE)
    assert _record(a, b, withdraw) == {
        "answer": _id("a"),
        "previous": None,
        "carry": False,
        "pin": None,
        "outcome": "accept",
    }
    restored = restored_answer((a, b, withdraw), _id("a"))
    assert restored is not None and restored.carry_eligible is False
    assert restored_answer((a, b), None) is None


def test_withdrawing_with_nothing_earlier_leaves_the_field_without_an_answer() -> None:
    a, b = _row("a", 1), _row("b", 2)
    assert _record(a, b, _row("w", 3, "withdraw")) is None


def test_withdrawing_an_answer_that_is_not_the_record_leaves_it() -> None:
    """The withdrawal is of the case's standing answer; the record moved on to
    another case's answer, which stays."""
    a = _row("a", 1)
    b = _row("b", 2, case=_OTHER_CASE)
    withdraw = _row("w", 3, "withdraw")
    assert _record(a, b, withdraw)["answer"] == _id("b")
    # A row of the case's earlier episode is not standing in its new one.
    old_episode = _row("w2", 4, "withdraw", episode=3)
    assert _record(a, old_episode)["answer"] == _id("a")


def test_book_matching_keeps_the_answer_but_stops_carrying_it() -> None:
    a = _row("a", 1)
    asked = _row("m", 2, "withdraw", cause="matching_changed")
    assert _record(a, asked) == {
        "answer": _id("a"),
        "previous": None,
        "carry": False,
        "pin": None,
        "outcome": "accept",
    }
    other = _row("b", 1, case=_OTHER_CASE)
    assert _record(other, asked)["carry"] is True


def test_a_pin_marks_the_standing_answer_or_stands_in_its_place() -> None:
    accept, keep = _row("a", 1), _row("k", 1, "keep_current")
    pin = _row("p", 2, "pin")
    chosen = chosen_value_fingerprint("title", {"kind": "text", "value": "Chosen"})
    shown = field_fingerprint("title", json.loads(pin.target_field_document))
    assert chosen != shown
    assert _record(accept, pin) == {
        "answer": _id("a"),
        "previous": None,
        "carry": True,
        "pin": chosen,
        "outcome": "accept",
    }
    assert _record(keep, pin)["pin"] == shown
    # With nothing standing on the case, the pin is the field's answer.
    earlier = _row("e", 1, case=_OTHER_CASE)
    assert _record(earlier, pin) == {
        "answer": _id("p"),
        "previous": _id("e"),
        "carry": True,
        "pin": shown,
        "outcome": "pin",
    }
    unpin = _row("u", 3, "unpin")
    assert _record(accept, pin, unpin)["pin"] is None
    assert _record(earlier, pin, unpin)["answer"] == _id("p")


def test_a_restored_answer_keeps_the_pin_it_stood_with() -> None:
    a, pin = _row("a", 1), _row("p", 2, "pin")
    b = _row("b", 3, case=_OTHER_CASE)
    withdraw = _row("w", 4, "withdraw", case=_OTHER_CASE)
    assert _record(a, pin, b, withdraw)["pin"] == chosen_value_fingerprint(
        "title", {"kind": "text", "value": "Chosen"}
    )


def test_an_authored_description_and_a_human_answer_replace_each_other() -> None:
    human = _row("h", 1, destination="description")
    authored = replace(
        _row("d", 2, destination="description", source="authored_description"),
        outcome="authored",
        value_document="null",
    )
    later = _row("l", 4, "keep_current", destination="description", case=_OTHER_CASE)
    assert _record(human, authored)["answer"] == _id("d")
    assert _record(human, authored, later) == {
        "answer": _id("l"),
        "previous": _id("d"),
        "carry": True,
        "pin": None,
        "outcome": "keep_current",
    }
    # A human row is never standing behind an authored one for a pin.
    assert _record(authored)["outcome"] == "authored"


def test_a_cover_answer_names_its_selection_and_fields_fold_apart() -> None:
    cover = _row("c", 1, "keep_current", destination="cover", selection=_SELECTION)
    title = _row("t", 2)
    folded = fold_owner_field_answers((title, cover))
    assert {key[3]: answer.cover_selection_id for key, answer in folded.items()} == {
        "cover": _SELECTION,
        "title": None,
    }


# -- the store writes the record beside the hold (C2a) ------------------------


def _stored(store: OwnerStore) -> dict[str, OwnerFieldAnswer]:
    return {
        answer.destination: answer
        for answer in stored_answers(store.repository._connection)
    }


def test_each_owner_answer_writes_the_record_and_a_withdrawal_takes_it_back(
    tmp_path: Path,
) -> None:
    store = OwnerStore(tmp_path, genre_report())
    try:
        kept = store.decide("keep_current", destination="genres")
        assert _stored(store)["genres"] == replace(
            _stored(store)["genres"],
            source="human",
            answer_id=kept.decision_id,
            outcome="keep_current",
            value_document="null",
            review_case_id=store.case.review_case_id,
            episode=0,
            previous_answer_id=None,
            carry_eligible=True,
            pin_fingerprint=None,
            cover_selection_id=None,
        )
        accepted = store.decide("accept", json.loads(_GENRES), destination="genres")
        record = _stored(store)["genres"]
        # The kept answer was this case's episode's, so it is not restorable.
        assert (record.answer_id, record.value_document, record.previous_answer_id) == (
            accepted.decision_id,
            _GENRES,
            None,
        )
        assert_record_recomputes(store.repository._connection)
        store.decide("withdraw", destination="genres")
        assert "genres" not in _stored(store)
        assert_record_recomputes(store.repository._connection)
    finally:
        store.close()


def test_a_pin_marks_the_standing_answer_and_an_unpin_clears_it(
    tmp_path: Path,
) -> None:
    store = OwnerStore(tmp_path, genre_report())
    try:
        accepted = store.decide("accept", json.loads(_GENRES), destination="genres")
        pin = store.decide("pin", destination="genres")
        record = _stored(store)["genres"]
        assert (record.answer_id, record.pin_fingerprint) == (
            accepted.decision_id,
            chosen_value_fingerprint("genres", json.loads(_GENRES)),
        )
        assert pin.decision_id not in {
            answer.answer_id for answer in _stored(store).values()
        }
        store.decide("unpin", destination="genres")
        record = _stored(store)["genres"]
        assert (record.answer_id, record.pin_fingerprint) == (
            accepted.decision_id,
            None,
        )
        assert_record_recomputes(store.repository._connection)
    finally:
        store.close()


def test_a_pin_with_no_standing_answer_is_the_answer_keeping_the_shown_value(
    tmp_path: Path,
) -> None:
    store = OwnerStore(tmp_path, genre_report())
    try:
        pin = store.decide("pin", destination="genres")
        record = _stored(store)["genres"]
        assert (record.answer_id, record.outcome, record.pin_fingerprint) == (
            pin.decision_id,
            "pin",
            pinned_field_fingerprint(store.case, "genres"),
        )
        assert_record_recomputes(store.repository._connection)
    finally:
        store.close()


def test_the_record_is_written_in_the_answers_own_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after the record is written rolls it back with the answer."""
    store = OwnerStore(tmp_path, genre_report())

    def then_fail(*args: object, **kwargs: object) -> None:
        owner_field_answers.record_answer(*args, **kwargs)  # type: ignore[arg-type]  # reason: forwards the product call unchanged.
        raise sqlite3.OperationalError("after the record")

    monkeypatch.setattr(decisions, "record_answer", then_fail)
    try:
        with pytest.raises(MetadataReportError):
            store.decide("keep_current", destination="genres")
        connection = store.repository._connection
        assert connection.execute(
            "SELECT (SELECT count(*) FROM human_decisions),"
            "(SELECT count(*) FROM owner_field_answers),"
            "(SELECT ordinal FROM owner_answer_ordinal)"
        ).fetchone() == (0, 0, 0)
    finally:
        store.close()


def test_an_authored_description_is_the_fields_answer(tmp_path: Path) -> None:
    repository, store, binding, candidate, _ = _prepared(tmp_path)
    try:
        decision = store.admit(candidate.candidate_id, binding, "human", "b" * 64, 11)
        (record,) = stored_answers(repository._connection)
        assert (
            record.source,
            record.answer_id,
            record.outcome,
            record.value_document,
            record.destination,
        ) == (
            "authored_description",
            decision.decision_id,
            "authored",
            "null",
            "description",
        )
        assert_record_recomputes(repository._connection)
    finally:
        repository.close()


def test_an_owner_cover_answer_names_its_selection(tmp_path: Path) -> None:
    repository = open_repository(tmp_path / "reports.sqlite")
    try:
        record_owner_cover_decisions(tmp_path, repository, 1)
        (record,) = stored_answers(repository._connection)
        assert (record.destination, record.cover_selection_id) == (
            "cover",
            repository._connection.execute(
                "SELECT selection_id FROM owner_cover_selections WHERE decision_id=?",
                (record.answer_id,),
            ).fetchone()[0],
        )
        # No Automatic cover stood, so this choice took nothing over (S9).
        assert repository._connection.execute(
            "SELECT cover_took_over FROM owner_cover_selections"
        ).fetchall() == [(0,)]
        assert_record_recomputes(repository._connection)
    finally:
        repository.close()


@pytest.mark.parametrize(
    "edit",
    (
        "UPDATE human_decisions SET display_title='Another title'",
        "UPDATE human_decisions SET item_id='another-book'",
    ),
)
def test_a_ledger_edit_the_record_disagrees_with_is_refused_once_regenerated(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, edit: str
) -> None:
    """The record copies each answer's work and title, so a regeneration no
    longer leaves an edit of one of them unseen (ADR-0108). The edited copy
    is kept apart from the teardown check, which it is meant to fail."""
    store = OwnerStore(tmp_path, genre_report())
    try:
        store.decide("accept", json.loads(_GENRES), destination="genres")
        path = copied_store(
            store.repository, tmp_path_factory.mktemp("edited") / "copy"
        )
    finally:
        store.close()
    regenerate(path)
    assert opens(path)
    rewrite(path, edit)
    assert not opens(path)
