"""The question check's ledger rows and the record helpers it shares (ADR-0108).

`carry_outcomes`, the carried kind of the decision audit event, the
`retired` event's answer, the helper that closes a Resume over its answer,
the retirement of a record answer and the ledger-only written predicate.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from mechanical_librarian.adapters.persistence import owner_field_answers, schema
from mechanical_librarian.adapters.persistence.ledger_meta import derived_epoch
from mechanical_librarian.adapters.persistence.owner_field_answers import (
    answer_written_sql,
    close_resume_over_answer,
    record_differences,
    retire_record_answer,
    stored_answers,
)
from mechanical_librarian.adapters.persistence.question_check import (
    record_carry_outcome,
    validate_carry_outcomes,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from tests.unit.test_owner_field_answers import (
    _CASE,
    _KEY,
    _OTHER_CASE,
    _bare,
    _decision,
    _event,
    _id,
)

_FIELD = (*_KEY, "genres")


@pytest.fixture
def ledger() -> Iterator[sqlite3.Connection]:
    """A genres answer in the record and its carried copy on another case."""
    connection = _bare()
    answer = _decision(connection, "answer", halves=("1" * 64, "2" * 64))
    owner_field_answers.record_answer(connection, "human", answer)
    try:
        yield connection
    finally:
        connection.close()


def _carried(connection: sqlite3.Connection, root: str, tag: str = "carried") -> str:
    carried = _decision(connection, tag, case=_OTHER_CASE, episode=1, carried_from=root)
    owner_field_answers.record_answer(connection, "human", carried)
    return carried


def _record(
    connection: sqlite3.Connection,
    outcome: str = "deferred",
    *,
    carried: str | None = None,
    answer: str | None = None,
    case: str = _OTHER_CASE,
) -> bool:
    return record_carry_outcome(
        connection,
        _FIELD,
        answer_id=answer or _id("answer"),
        review_case_id=case,
        episode=1,
        outcome=outcome,  # type: ignore[arg-type]  # reason: a literal of the outcome vocabulary.
        carried_decision_id=carried,
        checked_at=5,
    )


def _outcomes(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return connection.execute(
        "SELECT answer_id,review_case_id,episode,outcome,carried_decision_id,"
        "derived_epoch FROM carry_outcomes"
    ).fetchall()


def test_carry_outcomes_is_ledger() -> None:
    assert "carry_outcomes" in schema.LEDGER_TABLES


def test_a_provisional_outcome_is_replaced_and_a_final_one_kept(
    ledger: sqlite3.Connection,
) -> None:
    assert _record(ledger, "resume_pending")
    assert _record(ledger, "deferred")
    assert _record(ledger, "asked_again")
    # Final: the retry's later outcome changes nothing.
    assert not _record(ledger, "question_changed")
    assert _outcomes(ledger) == [
        (_id("answer"), _OTHER_CASE, 1, "asked_again", None, 0)
    ]
    validate_carry_outcomes(ledger)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE carry_outcomes SET outcome='deferred'",
        "DELETE FROM carry_outcomes",
    ],
    ids=["update", "delete"],
)
def test_a_final_outcome_is_never_updated_nor_any_deleted(
    ledger: sqlite3.Connection, statement: str
) -> None:
    _record(ledger, "target_changed")
    with pytest.raises(sqlite3.IntegrityError, match="kept"):
        ledger.execute(statement)


def test_a_provisional_outcome_may_be_updated_but_not_deleted(
    ledger: sqlite3.Connection,
) -> None:
    _record(ledger, "deferred")
    ledger.execute("UPDATE carry_outcomes SET checked_at=6")
    with pytest.raises(sqlite3.IntegrityError, match="kept"):
        ledger.execute("DELETE FROM carry_outcomes")


@pytest.mark.parametrize(
    ("outcome", "carried", "destination"),
    [
        ("carried", None, "genres"),
        ("asked_again", "human_decision_" + "0" * 64, "genres"),
        ("asked_again", None, "cover"),
        ("lapsed", None, "genres"),
    ],
    ids=["carried_names_nothing", "asked_names_a_decision", "cover", "vocabulary"],
)
def test_a_row_its_checks_rule_out_is_refused(
    ledger: sqlite3.Connection, outcome: str, carried: str | None, destination: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        record_carry_outcome(
            ledger,
            (*_KEY, destination),
            answer_id=_id("answer"),
            review_case_id=_OTHER_CASE,
            episode=1,
            outcome=outcome,  # type: ignore[arg-type]  # reason: an outcome outside the vocabulary.
            carried_decision_id=carried,
            checked_at=5,
        )


def test_a_carried_outcome_names_a_row_carried_from_the_answers_root(
    ledger: sqlite3.Connection,
) -> None:
    carried = _carried(ledger, _id("answer"))
    _record(ledger, "carried", carried=carried)
    validate_carry_outcomes(ledger)
    # Carrying a carried row names the same root.
    again = _decision(
        ledger, "again", case=_CASE, episode=3, carried_from=_id("answer")
    )
    record_carry_outcome(
        ledger,
        _FIELD,
        answer_id=carried,
        review_case_id=_CASE,
        episode=3,
        outcome="carried",
        carried_decision_id=again,
        checked_at=6,
    )
    validate_carry_outcomes(ledger)


@pytest.mark.parametrize(
    "broken",
    ["unknown_answer", "other_field", "not_carried", "other_case"],
    ids=["unknown_answer", "other_field", "not_carried", "other_case"],
)
def test_the_open_validator_refuses_a_broken_outcome(
    ledger: sqlite3.Connection, broken: str
) -> None:
    carried = _carried(ledger, _id("answer"))
    if broken == "unknown_answer":
        _record(ledger, "asked_again", answer=_id("nothing"))
    elif broken == "other_field":
        record_carry_outcome(
            ledger,
            (*_KEY, "title"),
            answer_id=_id("answer"),
            review_case_id=_OTHER_CASE,
            episode=1,
            outcome="asked_again",
            carried_decision_id=None,
            checked_at=5,
        )
    elif broken == "not_carried":
        _record(ledger, "carried", carried=_id("answer"))
    else:
        _record(ledger, "carried", carried=carried, case=_CASE)
    with pytest.raises(MetadataReportError, match="unavailable or incompatible"):
        validate_carry_outcomes(ledger)


def test_an_outcome_names_the_ledgers_derived_epoch(
    ledger: sqlite3.Connection,
) -> None:
    assert derived_epoch(ledger) == 0
    ledger.execute("UPDATE ledger_meta SET derived_epoch=3")
    _record(ledger, "deferred")
    assert _outcomes(ledger)[0][5] == 3


def test_the_decision_event_admits_the_carried_kind(
    ledger: sqlite3.Connection,
) -> None:
    columns = (
        "INSERT INTO human_decision_events(event_id,decision_id,review_case_id,"
        "destination,actor,kind,occurred_at,document,connection_name,library_name,"
        "item_id) VALUES(?,?,?,'genres','owner',?,1,'{}',?,?,?)"
    )
    ledger.execute(
        columns,
        (
            "human_decision_event_" + "1" * 64,
            _id("answer"),
            _CASE,
            "human_decision_carried",
            *_KEY,
        ),
    )
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        ledger.execute(
            columns,
            (
                "human_decision_event_" + "2" * 64,
                _id("answer"),
                _CASE,
                "carried",
                *_KEY,
            ),
        )


def test_closing_a_resume_over_its_answer_hands_the_field_back(
    ledger: sqlite3.Connection,
) -> None:
    owner_field_answers.request_resume(ledger, _KEY, "genres", "scan-1", 3)
    owner_field_answers.fail_open_resumes_for_scan(ledger, "scan-1", 4)

    assert close_resume_over_answer(ledger, _FIELD, 5)

    (answer,) = stored_answers(ledger)
    assert (answer.answer_id, answer.open_resume_id, answer.carry_eligible) == (
        _id("answer"),
        None,
        False,
    )
    assert ledger.execute(
        "SELECT kind,answer_id FROM owner_field_events ORDER BY rowid DESC LIMIT 1"
    ).fetchone() == ("resume_superseded", _id("answer"))
    assert record_differences(ledger) == {}
    # Nothing open: nothing changes.
    assert not close_resume_over_answer(ledger, _FIELD, 6)


def test_a_retired_answer_is_no_longer_carried(ledger: sqlite3.Connection) -> None:
    retire_record_answer(ledger, _FIELD, _id("answer"), 5)

    (answer,) = stored_answers(ledger)
    assert answer.carry_eligible is False
    assert record_differences(ledger) == {}
    # Retiring an answer the record no longer holds leaves the record.
    later = _decision(ledger, "later", case=_OTHER_CASE)
    owner_field_answers.record_answer(ledger, "human", later)
    retire_record_answer(ledger, _FIELD, _id("answer"), 6)
    (answer,) = stored_answers(ledger)
    assert (answer.answer_id, answer.carry_eligible) == (later, True)
    assert record_differences(ledger) == {}
    assert _event(ledger, "retired", destination="genres", answer=later)


def _written(connection: sqlite3.Connection, answer: str) -> bool:
    return bool(
        connection.execute("SELECT " + answer_written_sql("?"), (answer,)).fetchone()[0]
    )


def test_an_answer_is_written_by_a_verified_plan_or_no_change_of_its_chain(
    ledger: sqlite3.Connection,
) -> None:
    carried = _carried(ledger, _id("answer"))
    assert not _written(ledger, _id("answer")) and not _written(ledger, carried)
    ledger.execute(
        "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,"
        "work_id,destination,state,attempt,updated_at,document) "
        "VALUES(?,?,?,?,?,'genres',?,1,1,'{}')",
        ("effect_plan_" + "1" * 64, carried, *_KEY, "planned"),
    )
    assert not _written(ledger, _id("answer"))
    ledger.execute("UPDATE field_effects SET state='verified'")
    # The carried copy's write writes its root, and the other way round.
    assert _written(ledger, _id("answer")) and _written(ledger, carried)
    other = _decision(ledger, "other", case=_CASE, episode=5)
    assert not _written(ledger, other)
    ledger.execute(
        "INSERT INTO field_effect_no_changes VALUES(?,?,?,?,?,?,'genres',"
        "'fingerprint','version','human',1,?,NULL)",
        (other, _CASE, "report_" + "1" * 64, *_KEY, "event-other"),
    )
    assert _written(ledger, other)
