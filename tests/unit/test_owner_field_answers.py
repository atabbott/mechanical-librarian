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
    case_question,
    chosen_value_fingerprint,
    field_fingerprint,
    pinned_field_fingerprint,
)
from mechanical_librarian.domain.owner_field_answer import (
    OWNER_FIELD_EVENT_SCHEMA,
    OwnerAnswerRow,
    OwnerFieldAnswer,
    OwnerFieldEventRow,
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
from tests.unit.test_description_advice_labels import _readmit, prepared_report
from tests.unit.test_description_advice_labels import _store as _labels_store

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
    key: tuple[str, str, str] = _KEY,
    carried_from: str | None = None,
    halves: tuple[str, str] | None = None,
) -> str:
    """A ledger decision row as the store writes its columns; the document
    carries only what the record reads."""
    decision_id = _id(tag)
    document: dict[str, object] = {"cause": cause, "episode": episode}
    if carried_from is not None:
        document["carried_from"] = carried_from
        halves = halves or ("1" * 64, "2" * 64)
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
            json.dumps(document),
            *key,
            case,
            "null",
            None,
            carried_from,
            *(halves or (None, None)),
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
    answer: str | None = None,
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
        "answer_id": answer,
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
        answer_id=answer,
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
    title unpin the record leaves out."""
    connection = _bare()
    _decision(
        connection,
        "filler",
        destination="title",
        outcome="unpin",
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
    """Which record rules a ledger breaks; a record unlike the ledger's fold
    counts as -2, and the event chain as -1."""
    fired = [
        index
        for index, rule in enumerate(owner_field_answers._RECORD_RULES)
        if connection.execute(rule).fetchone() is not None
    ]
    if owner_field_answers.record_differences(connection):
        fired.append(-2)
    try:
        validate_owner_field_answers(connection)
    except MetadataReportError:
        if not fired:
            fired.append(-1)
    return fired


def _refused(connection: sqlite3.Connection, rule: int) -> None:
    """Exactly ``rule`` fires, whether or not the record also differs from
    the fold, as an edited record mostly does."""
    assert [fired for fired in _firing(connection) if fired != -2] == [rule]
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


def test_events_are_append_only_and_retired_names_its_answer(
    ledger: sqlite3.Connection,
) -> None:
    """`retired` names the answer whose plan it retired (C4')."""
    request = _event(ledger, "resume_requested")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        _event(ledger, "retired", destination="title")
    _event(ledger, "retired", destination="title", answer=_id("filler"))
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
                '"schema_version":"' + OWNER_FIELD_EVENT_SCHEMA + '"}'
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
        # The fold opens the same request over the answer it covers.
        assert _firing(ledger) == []
    elif fault == "below":
        # An answer above an unclosed request's bound breaks the ledger's
        # own rule too (10); the record naming it as open breaks this one,
        # and the fold, which covers only answers at or below it, leaves it out.
        assert _firing(ledger) == [6, 10, -2]
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


@pytest.mark.parametrize("kind", ("resume_completed", "resume_superseded"))
def test_a_request_closed_twice_the_same_way_is_refused(
    ledger: sqlite3.Connection, kind: str
) -> None:
    request = _event(ledger, "resume_requested", destination="title")
    _event(ledger, kind, request=request, destination="title")
    assert _firing(ledger) == []
    _event(ledger, kind, request=request, destination="title")
    _refused(ledger, 8)


@pytest.mark.parametrize("order", ("superseded-first", "completed-first"))
def test_a_superseded_request_that_completes_is_refused(
    ledger: sqlite3.Connection, order: str
) -> None:
    request = _event(ledger, "resume_requested", destination="title")
    kinds = ["resume_superseded", "resume_completed"]
    if order == "completed-first":
        kinds.reverse()
    _event(ledger, kinds[0], request=request, destination="title")
    assert _firing(ledger) == []
    _event(ledger, kinds[1], request=request, destination="title")
    _refused(ledger, 11)


def test_a_field_with_two_unclosed_requests_is_refused(
    ledger: sqlite3.Connection,
) -> None:
    first = _event(ledger, "resume_requested", destination="title")
    _event(ledger, "resume_failed", request=first, destination="title")
    assert _firing(ledger) == []
    _event(ledger, "resume_requested", destination="title")
    _refused(ledger, 9)


@pytest.mark.parametrize(
    "answer", ("human", "authored"), ids=("human-answer", "authored-answer")
)
def test_an_answer_above_an_unclosed_requests_bound_is_refused(
    ledger: sqlite3.Connection, answer: str
) -> None:
    """Every answer closes an open request first (S10); withdrawals and Book
    matching's rows may follow one, so only the fold judges those."""
    destination = "title" if answer == "human" else "description"
    _event(ledger, "resume_requested", destination=destination)
    _decision(ledger, "withdrawal", destination=destination, outcome="withdraw")
    assert _firing(ledger) == []
    if answer == "human":
        _decision(ledger, "late", destination="title", outcome="keep_current")
    else:
        _authored(ledger)
    _refused(ledger, 10)


def test_an_event_written_before_its_requests_bound_is_refused(
    ledger: sqlite3.Connection,
) -> None:
    request = _event(ledger, "resume_requested", destination="title")
    _event(ledger, "resume_failed", request=request, destination="title")
    assert _firing(ledger) == []
    _event(ledger, "resume_failed", request=request, destination="title", over=1)
    _refused(ledger, 12)


@pytest.mark.parametrize("fault", ("digest", "reordered"))
def test_an_event_whose_id_does_not_recompute_is_refused(
    ledger: sqlite3.Connection, fault: str
) -> None:
    first = _event(ledger, "resume_requested", destination="title")
    if fault == "digest":
        _event(
            ledger,
            "resume_failed",
            request=first,
            destination="title",
            event_id="owner_field_event_" + "0" * 64,
        )
    else:
        # Each id chains to the field's previous event, so a store cannot
        # drop or reorder one unseen.
        second = owner_field_event_id(
            connection_name="primary",
            library_name="books",
            item_id="work",
            destination="title",
            kind="resume_failed",
            request_id=first,
            over_ordinal=2,
            scan_id=None,
            answer_id=None,
            occurred_at=9,
            previous_event_id=None,
        )
        _event(
            ledger, "resume_failed", request=first, destination="title", event_id=second
        )
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
    halves: tuple[str, str] | None = None,
    carried: OwnerAnswerRow | None = None,
) -> OwnerAnswerRow:
    root = carried
    offer, seen = halves or (None, None)
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
        offer,
        seen,
        None if root is None else root.answer_id,
        case if root is None else root.review_case_id,
        episode if root is None else root.episode,
    )


def _record(*rows: OwnerAnswerRow) -> dict[str, object] | None:
    folded = fold_owner_field_answers(rows, ())
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
        (answer,) = fold_owner_field_answers((row,), ()).values()
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
    folded = fold_owner_field_answers((title, cover), ())
    assert {key[3]: answer.cover_selection_id for key, answer in folded.items()} == {
        "cover": _SELECTION,
        "title": None,
    }


# -- the fold with Resume's events -------------------------------------------


def _ev(
    tag: str,
    kind: str,
    over: int,
    *,
    request: OwnerFieldEventRow | None = None,
    answer: str | None = None,
) -> OwnerFieldEventRow:
    return OwnerFieldEventRow(
        "owner_field_event_" + hashlib.sha256(tag.encode()).hexdigest(),
        *_KEY,
        "title",
        kind,
        None if request is None else request.event_id,
        over,
        "scan-1" if kind == "resume_requested" else None,
        None if answer is None else _id(answer),
        9,
    )


def _resumed(
    rows: tuple[OwnerAnswerRow, ...], events: tuple[OwnerFieldEventRow, ...]
) -> tuple[str, str | None, bool] | None:
    folded = fold_owner_field_answers(rows, events)
    answer = next(iter(folded.values()), None)
    return (
        None
        if answer is None
        else (answer.answer_id, answer.open_resume_id, answer.carry_eligible)
    )


def test_a_request_stays_open_over_the_answer_it_covers() -> None:
    a = _row("a", 1)
    request = _ev("r", "resume_requested", 1)
    assert _resumed((a,), (request,)) == (_id("a"), request.event_id, True)
    # A failure changes nothing: the request is still open.
    failed = _ev("f", "resume_failed", 1, request=request)
    assert _resumed((a,), (request, failed)) == (_id("a"), request.event_id, True)


def test_an_answer_after_a_request_supersedes_it() -> None:
    a, b = _row("a", 1), _row("b", 2)
    request = _ev("r", "resume_requested", 1)
    superseded = _ev("s", "resume_superseded", 2, request=request, answer="b")
    assert _resumed((a, b), (request, superseded)) == (_id("b"), None, True)
    # Even unclosed, a request never names an answer above its bound.
    assert _resumed((a, b), (request,)) == (_id("b"), None, True)


def test_a_withdrawal_while_open_restores_and_keeps_it_or_empties_and_closes_it() -> (
    None
):
    earlier = _row("e", 1, case=_OTHER_CASE)
    b = _row("b", 2)
    request = _ev("r", "resume_requested", 2)
    withdraw = _row("w", 3, "withdraw")
    # Restored, the earlier answer is covered too: the request stays open.
    assert _resumed((earlier, b, withdraw), (request,)) == (
        _id("e"),
        request.event_id,
        False,
    )
    # Nothing to restore: the field is left without an answer and the
    # withdrawal closed the request.
    superseded = _ev("s", "resume_superseded", 3, request=request, answer="w")
    assert _resumed((b, withdraw), (request, superseded)) is None


def test_book_matching_while_open_keeps_the_request_and_stops_carrying() -> None:
    a = _row("a", 1)
    request = _ev("r", "resume_requested", 1)
    matching = _row("m", 2, "withdraw", cause="matching_changed")
    assert _resumed((a, matching), (request,)) == (_id("a"), request.event_id, False)


def test_completion_hands_back_what_its_request_covers_by_position() -> None:
    a = _row("a", 1)
    request = _ev("r", "resume_requested", 1)
    completed = _ev("c", "resume_completed", 1, request=request, answer="a")
    assert _resumed((a,), (request, completed)) is None
    # An answer given after the completion is above its position, so it
    # stands, whichever order the rows arrive in.
    later = _row("l", 2, case=_OTHER_CASE)
    assert _resumed((later, a), (request, completed)) == (_id("l"), None, True)
    # A row that changed nothing to hand back between the request and the
    # completion is gone with the answer it covered.
    matching = _row("m", 2, "withdraw", cause="matching_changed")
    late = _ev("c2", "resume_completed", 2, request=request, answer="a")
    assert _resumed((a, matching), (request, late)) is None


def test_a_failed_resume_closed_over_its_answer_hands_the_field_back() -> None:
    """Step 0: the request closes `resume_superseded` naming the answer it
    covers; the answer stays, no longer carried (ADR-0108)."""
    a = _row("a", 1)
    request = _ev("r", "resume_requested", 1)
    failed = _ev("f", "resume_failed", 1, request=request)
    closed = _ev("s", "resume_superseded", 1, request=request, answer="a")
    assert _resumed((a,), (request, failed, closed)) == (_id("a"), None, False)
    # Asked again, the request names no answer: the answer stays carried.
    again = _ev("s2", "resume_superseded", 1, request=request)
    assert _resumed((a,), (request, again)) == (_id("a"), None, True)


def test_a_retired_answer_stays_and_is_no_longer_carried() -> None:
    a = _row("a", 1)
    assert _resumed((a,), (_ev("t", "retired", 1, answer="a"),)) == (
        _id("a"),
        None,
        False,
    )
    # Retiring another answer leaves the record alone.
    b = _row("b", 2, case=_OTHER_CASE)
    assert _resumed((a, b), (_ev("t", "retired", 2, answer="a"),)) == (
        _id("b"),
        None,
        True,
    )


def test_the_record_copies_the_answers_question_halves() -> None:
    a = _row("a", 1, halves=("1" * 64, "2" * 64))
    folded = next(iter(fold_owner_field_answers((a,), ()).values()))
    assert (folded.offer_digest, folded.seen_target_digest) == ("1" * 64, "2" * 64)
    # A pin over the answer marks it and keeps its halves.
    pin = _row("p", 2, "pin")
    folded = next(iter(fold_owner_field_answers((a, pin), ()).values()))
    assert (folded.offer_digest, folded.seen_target_digest) == ("1" * 64, "2" * 64)
    # The next answer brings its own.
    b = _row("b", 3, "keep_current", case=_OTHER_CASE)
    folded = next(iter(fold_owner_field_answers((a, pin, b), ()).values()))
    assert (folded.offer_digest, folded.seen_target_digest) == (None, None)


def test_a_carried_answer_restores_its_root_and_counts_as_given_there() -> None:
    a = _row("a", 1, halves=("1" * 64, "2" * 64))
    carried = _row(
        "c", 2, case=_OTHER_CASE, episode=1, halves=("3" * 64, "4" * 64), carried=a
    )
    record = fold_owner_field_answers((a, carried), ())
    answer = next(iter(record.values()))
    # The record takes the new case, episode and halves, and restores the root.
    assert (answer.answer_id, answer.previous_answer_id, answer.carry_eligible) == (
        _id("c"),
        _id("a"),
        True,
    )
    assert (answer.review_case_id, answer.episode, answer.offer_digest) == (
        _OTHER_CASE,
        1,
        "3" * 64,
    )
    # An answer on the new case replaces an answer from the root's case: a
    # withdrawal of it restores the carried row.
    b = _row("b", 3, case=_OTHER_CASE, episode=1)
    assert _record(a, carried, b)["previous"] == _id("c")
    # Back on the root's case in a later episode, the same holds.
    back = _row("k", 3, episode=2)
    assert _record(a, carried, back)["previous"] == _id("c")


def test_resume_asked_again_supersedes_the_open_request() -> None:
    a = _row("a", 1)
    first = _ev("r1", "resume_requested", 1)
    again = _ev("s", "resume_superseded", 1, request=first)
    second = _ev("r2", "resume_requested", 1)
    assert _resumed((a,), (first, again, second)) == (_id("a"), second.event_id, True)
    # Completing the new request hands the answer back.
    done = _ev("c", "resume_completed", 1, request=second, answer="a")
    assert _resumed((a,), (first, again, second, done)) is None


def test_a_failed_request_still_completes_on_a_later_successful_run() -> None:
    a = _row("a", 1)
    request = _ev("r", "resume_requested", 1)
    failed = _ev("f", "resume_failed", 1, request=request)
    completed = _ev("c", "resume_completed", 1, request=request, answer="a")
    assert _resumed((a,), (request, failed, completed)) is None


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


def test_an_owner_answer_stores_the_question_it_was_given_against(
    tmp_path: Path,
) -> None:
    """The owner command site writes the offer part of the case's current
    offer and the target part of the case's field; the record copies them
    from the ledger row. A pin and a withdrawal store none (ADR-0108)."""
    store = OwnerStore(tmp_path, genre_report())
    try:
        connection = store.repository._connection
        accepted = store.decide("accept", json.loads(_GENRES), destination="genres")
        question = case_question(store.case, store.case.proposals, "genres")
        assert question is not None
        assert connection.execute(
            "SELECT carried_from,offer_digest,seen_target_digest FROM human_decisions "
            "WHERE decision_id=?",
            (accepted.decision_id,),
        ).fetchone() == (None, question.offer_part, question.target_part)
        record = _stored(store)["genres"]
        assert (record.offer_digest, record.seen_target_digest) == (
            question.offer_part,
            question.target_part,
        )
        assert_record_recomputes(connection)
        withdrawn = store.decide("withdraw", destination="genres")
        assert connection.execute(
            "SELECT offer_digest,seen_target_digest FROM human_decisions "
            "WHERE decision_id=?",
            (withdrawn.decision_id,),
        ).fetchone() == (None, None)
        assert_record_recomputes(connection)
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


def test_a_case_admitted_again_after_only_an_authored_description_starts_an_episode(
    tmp_path: Path,
) -> None:
    """The authored description takes an ordinal, so the case's next episode
    starts after it: a later answer there restores it (ADR-0108, O1).

    The owner cannot withdraw a description (UNREVERSIBLE_DESTINATIONS), so
    the withdrawal is the ledger row the store's withdrawal path writes."""
    prepared = _labels_store(tmp_path)
    prepared.choose()
    connection = prepared.repository._connection
    ((authored, ordinal),) = connection.execute(
        "SELECT decision_id,ordinal FROM owner_authored_descriptions"
    ).fetchall()
    assert _readmit(prepared, prepared_report(prepared)) == prepared.case
    assert connection.execute(
        "SELECT decision_floor FROM review_case_episodes WHERE review_case_id=?",
        (prepared.case,),
    ).fetchone() == (ordinal,)
    prepared.decide("keep_current")
    (record,) = stored_answers(connection)
    assert (record.outcome, record.episode, record.previous_answer_id) == (
        "keep_current",
        ordinal,
        authored,
    )
    assert_record_recomputes(connection)
    with connection:
        withdrawal = _decision(
            connection,
            "withdraw",
            destination="description",
            outcome="withdraw",
            selected="null",
            case=prepared.case,
            episode=ordinal,
            key=record.key[:3],
        )
        owner_field_answers.withdraw_answer(connection, withdrawal)
    (record,) = stored_answers(connection)
    assert (record.answer_id, record.source, record.carry_eligible) == (
        authored,
        "authored_description",
        False,
    )
    assert_record_recomputes(connection)
    prepared.repository.close()


@pytest.mark.parametrize(
    "edit",
    (
        "DELETE FROM owner_field_answers WHERE destination='genres'",
        "UPDATE owner_field_answers SET carry_eligible=1-carry_eligible "
        "WHERE destination='genres'",
        "UPDATE owner_field_answers SET pin_fingerprint='" + "a" * 64 + "' "
        "WHERE destination='genres'",
    ),
    ids=("missing", "carry_eligible", "pin_fingerprint"),
)
def test_a_record_unlike_the_ledgers_fold_is_refused(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, edit: str
) -> None:
    """Open recomputes the record from the ledger and compares every row, so
    a row missing, added or changed refuses the store (ADR-0108). The edited
    copy is kept apart from the teardown check, which it is meant to fail."""
    store = OwnerStore(tmp_path, genre_report())
    try:
        store.decide("accept", json.loads(_GENRES), destination="genres")
        path = copied_store(
            store.repository, tmp_path_factory.mktemp("edited") / "copy"
        )
    finally:
        store.close()
    assert opens(path)
    assert rewrite(path, edit) == 1
    assert not opens(path)


# -- Resume's events in the store (C2b) ---------------------------------------


def _kinds(connection: sqlite3.Connection) -> list[tuple[str, str | None]]:
    return connection.execute(
        "SELECT kind,answer_id FROM owner_field_events ORDER BY rowid"
    ).fetchall()


def test_resume_is_requested_failed_asked_again_superseded_and_completed(
    tmp_path: Path,
) -> None:
    """Each Resume operation writes its event and the record in the caller's
    transaction, and the stored record stays the fold of the ledger (S10)."""
    store = OwnerStore(tmp_path, genre_report())
    connection = store.repository._connection
    try:
        kept = store.decide("keep_current", destination="genres")
        item = _stored(store)["genres"].key[:3]
        with connection:
            first = owner_field_answers.request_resume(
                connection, item, "genres", "scan-a", 50
            )
        assert first is not None
        assert _stored(store)["genres"].open_resume_id == first
        assert_record_recomputes(connection)
        with connection:
            assert (
                owner_field_answers.fail_open_resumes_for_scan(connection, "scan-a", 51)
                == 1
            )
            # Already failed since its latest event: not written again.
            assert (
                owner_field_answers.fail_open_resumes_for_scan(connection, "scan-a", 52)
                == 0
            )
        assert owner_field_answers.has_open_resume_for_scan(connection, "scan-a")
        assert not owner_field_answers.has_open_resume_for_scan(connection, "scan-b")
        assert _stored(store)["genres"].open_resume_id == first
        with connection:
            second = owner_field_answers.request_resume(
                connection, item, "genres", "scan-b", 53
            )
        assert _stored(store)["genres"].open_resume_id == second != first
        assert not owner_field_answers.has_open_resume_for_scan(connection, "scan-a")
        assert_record_recomputes(connection)
        accepted = store.decide("accept", json.loads(_GENRES), destination="genres")
        assert _stored(store)["genres"].open_resume_id is None
        assert_record_recomputes(connection)
        with connection:
            owner_field_answers.request_resume(connection, item, "genres", "scan-c", 54)
            assert owner_field_answers.complete_resume(connection, item, "genres", 55)
            assert not owner_field_answers.complete_resume(
                connection, item, "genres", 56
            )
        assert "genres" not in _stored(store)
        assert _kinds(connection) == [
            ("resume_requested", kept.decision_id),
            ("resume_failed", None),
            ("resume_superseded", None),
            ("resume_requested", kept.decision_id),
            ("resume_superseded", accepted.decision_id),
            ("resume_requested", accepted.decision_id),
            ("resume_completed", accepted.decision_id),
        ]
        assert_record_recomputes(connection)
        validate_owner_field_answers(connection)
    finally:
        store.close()


def test_the_stores_events_chain_each_to_the_fields_previous_one(
    tmp_path: Path,
) -> None:
    """Every event the store writes is the digest of its columns and the
    field's previous event, at the counter's value, read and never taken."""
    store = OwnerStore(tmp_path, genre_report())
    connection = store.repository._connection
    try:
        store.decide("keep_current", destination="genres")
        item = _stored(store)["genres"].key[:3]
        with connection:
            owner_field_answers.request_resume(connection, item, "genres", "scan-a", 50)
            owner_field_answers.fail_open_resumes_for_scan(connection, "scan-a", 51)
            owner_field_answers.complete_resume(connection, item, "genres", 52)
        previous = None
        rows = list(owner_field_answers.ledger_events(connection))
        for event in rows:
            assert event.event_id == owner_field_event_id(
                connection_name=event.connection_name,
                library_name=event.library_name,
                item_id=event.item_id,
                destination=event.destination,
                kind=event.kind,
                request_id=event.request_id,
                over_ordinal=event.over_ordinal,
                scan_id=event.scan_id,
                answer_id=event.answer_id,
                occurred_at=event.occurred_at,
                previous_event_id=previous,
            )
            previous = event.event_id
        assert [(event.kind, event.over_ordinal, event.scan_id) for event in rows] == [
            ("resume_requested", 1, "scan-a"),
            ("resume_failed", 1, "scan-a"),
            ("resume_completed", 1, "scan-a"),
        ]
        assert [event.request_id for event in rows[1:]] == [rows[0].event_id] * 2
        assert connection.execute(
            "SELECT ordinal FROM owner_answer_ordinal"
        ).fetchone() == (1,)
    finally:
        store.close()


def test_resume_is_not_recorded_without_an_unpinned_answer(tmp_path: Path) -> None:
    store = OwnerStore(tmp_path, genre_report())
    connection = store.repository._connection
    try:
        store.decide("accept", json.loads(_GENRES), destination="genres")
        item = _stored(store)["genres"].key[:3]
        store.decide("pin", destination="genres")
        with connection:
            for destination in ("genres", "title"):
                assert (
                    owner_field_answers.request_resume(
                        connection, item, destination, "scan-a", 50
                    )
                    is None
                )
            assert not owner_field_answers.complete_resume(
                connection, item, "genres", 51
            )
        assert _kinds(connection) == []
    finally:
        store.close()


@pytest.mark.parametrize("earlier", (True, False), ids=("restores", "empties"))
def test_a_withdrawal_while_resume_is_open_keeps_or_closes_it(earlier: bool) -> None:
    """A withdrawal that restores an earlier answer keeps the request open
    over it; one that leaves no answer closes it, naming the withdrawal."""
    connection = _bare()
    try:
        if earlier:
            owner_field_answers.record_answer(
                connection, "human", _decision(connection, "earlier", case=_OTHER_CASE)
            )
        owner_field_answers.record_answer(
            connection, "human", _decision(connection, "answer")
        )
        request = owner_field_answers.request_resume(
            connection, _KEY, "genres", "scan-a", 50
        )
        withdrawal = _decision(connection, "withdrawal", outcome="withdraw")
        owner_field_answers.withdraw_answer(connection, withdrawal)
        stored = list(stored_answers(connection))
        if earlier:
            assert [
                (answer.answer_id, answer.open_resume_id, answer.carry_eligible)
                for answer in stored
            ] == [(_id("earlier"), request, False)]
            assert _kinds(connection) == [("resume_requested", _id("answer"))]
        else:
            assert stored == []
            assert _kinds(connection) == [
                ("resume_requested", _id("answer")),
                ("resume_superseded", withdrawal),
            ]
        assert _firing(connection) == []
    finally:
        connection.close()


def test_an_answers_supersession_rolls_back_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The S10 supersession is written in the answer's own transaction, so a
    failure after it leaves the request open and the record unchanged."""
    store = OwnerStore(tmp_path, genre_report())
    connection = store.repository._connection
    try:
        store.decide("keep_current", destination="genres")
        item = _stored(store)["genres"].key[:3]
        with connection:
            request = owner_field_answers.request_resume(
                connection, item, "genres", "scan-a", 50
            )
        before = _stored(store)["genres"]

        def then_fail(*args: object, **kwargs: object) -> None:
            owner_field_answers.record_answer(*args, **kwargs)  # type: ignore[arg-type]  # reason: forwards the product call unchanged.
            raise sqlite3.OperationalError("after the supersession")

        monkeypatch.setattr(decisions, "record_answer", then_fail)
        with pytest.raises(MetadataReportError):
            store.decide("accept", json.loads(_GENRES), destination="genres")
        assert _stored(store)["genres"] == before
        assert before.open_resume_id == request
        assert _kinds(connection) == [("resume_requested", before.answer_id)]
        assert_record_recomputes(connection)
    finally:
        store.close()
