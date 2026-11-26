"""The record of the owner's answer per field, its counter and its events (ADR-0108).

Every owner answer takes its ordinal from `owner_answer_ordinal` in its own
transaction, and every answer site writes `owner_field_answers` through this
module in that same transaction. Resume's requests, completions, failures and
supersessions are written here too, as `owner_field_events`, keyed by the
work's field. What Automatic records, plans and writes, Resume itself, and
what the owner sees and may do read the record (#2038 C2b), which replaced
the manual holds.
`domain.owner_field_answer` folds the same rows from the ledger; a withdrawal
restores through that fold, and open refuses a store whose record is not that
fold.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import astuple, replace

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.owner_field_answer import (
    ItemKey,
    OwnerAnswerRow,
    OwnerFieldAnswer,
    OwnerFieldEventRow,
    answer_from,
    fold_owner_field_answers,
    owner_field_event_id,
    restored_answer,
)

_HEX64 = "length({0})=64 AND {0} NOT GLOB '*[^0-9a-f]*'"
_HUMAN_ID = "{0} GLOB 'human_decision_[0-9a-f]*' AND length({0})=79"
_EVENT_KINDS = "'resume_requested','resume_completed','resume_failed','resume_superseded','retired'"
# A request is open until it completes or is superseded; a failure leaves it.
_UNCLOSED = (
    "NOT EXISTS (SELECT 1 FROM owner_field_events closing WHERE "
    "closing.request_id={0}.event_id "
    "AND closing.kind IN ('resume_completed','resume_superseded'))"
)


# A work Resume's request requeued for projection (C2b-12), over the work row
# `w`: queued or running there, in the scan the request was made for, while
# the request has neither completed nor failed. Its scan keeps counting it as
# it stood, checked and ready, so the scan's "X of Y works checked" never goes
# backwards; projection ends it again. An answer that supersedes the request
# does not end it: the requeued work still waits. A superseded request never
# completes or fails, so the scan bounds it, and a later scan's projection of
# the work is ordinary. A failed request stays open, but a requeue during it
# is ordinary projection.
RESUME_REPROJECTION_SQL = (
    "(w.stage='projection' AND w.state IN ('queued','delayed','running') "
    "AND EXISTS (SELECT 1 FROM owner_field_events request "
    "WHERE request.connection_name=w.connection_name "
    "AND request.library_name=w.library_name AND request.item_id=w.item_id "
    "AND request.kind='resume_requested' AND request.scan_id=w.scan_id "
    "AND NOT EXISTS (SELECT 1 FROM owner_field_events later "
    "WHERE later.request_id=request.event_id "
    "AND later.kind IN ('resume_completed','resume_failed'))))"
)


def case_record_sql(case: str, destination: str) -> str:
    """A SELECT of the record's row for the field of the case's book, keyed
    by item through the case's current lineage; `case` and `destination` are
    SQL expressions. Its columns are `record.*`."""
    return (
        "SELECT 1 FROM current_review_case_lineages record_case "
        "JOIN owner_field_answers record "
        "ON record.connection_name=record_case.connection_name "
        "AND record.library_name=record_case.library_name "
        "AND record.item_id=record_case.item_id "
        f"WHERE record_case.review_case_id={case} AND record.destination={destination}"
    )


# The record's answer for either half of a title and subtitle bundle holds
# the whole bundle, at record time and at write time; bound by the case twice.
BUNDLE_ANSWERED_SQL = (
    case_record_sql("?", "'title'") + " UNION ALL " + case_record_sql("?", "'subtitle'")
)


# The record holds an owner's cover for the case's book that took the cover
# over from Automatic (S9): where a cover hold stood before C2b, Automatic's
# cover mint and its taste switch stop, and Resume may show (ADR-0085,
# Amendment 3; ADR-0108); bound by the case.
COVER_TAKEN_OVER_SQL = (
    case_record_sql("?", "'cover'")
    + " AND EXISTS (SELECT 1 FROM owner_cover_selections took "
    "WHERE took.selection_id=record.cover_selection_id AND took.cover_took_over=1)"
)


def resumed_ordinal_sql(owner: str, destination: str) -> str:
    """The ordinal up to which Resume has the field's owner answers back: the
    `over_ordinal` of its open or latest completed request, NULL with none.
    `owner` names a row with the field's item key columns."""
    return (
        "(SELECT MAX(resumed.over_ordinal) FROM owner_field_events resumed "
        f"WHERE resumed.connection_name={owner}.connection_name "
        f"AND resumed.library_name={owner}.library_name "
        f"AND resumed.item_id={owner}.item_id AND resumed.destination={destination} "
        "AND resumed.kind='resume_requested' AND NOT EXISTS (SELECT 1 FROM "
        "owner_field_events superseded WHERE superseded.request_id=resumed.event_id "
        "AND superseded.kind='resume_superseded'))"
    )


def owner_row_counts_sql(human: str) -> str:
    """Whether owner decision `human` still keeps Automatic off its field:
    Resume has not taken it back (it is above the open or latest completed
    request's `over_ordinal`), and it is no valueless cause row of a field
    Resume was asked of (ADR-0108, "During and after a Resume")."""
    resumed = resumed_ordinal_sql(human, f"{human}.destination")
    return (
        f"NOT ({human}.ordinal<=COALESCE({resumed},0) OR "
        f"(json_extract({human}.document,'$.cause') IS NOT NULL "
        f"AND {resumed} IS NOT NULL))"
    )


OWNER_ANSWER_ORDINAL_SQL = (
    "CREATE TABLE owner_answer_ordinal ("
    "singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1), "
    "ordinal INTEGER NOT NULL CHECK(typeof(ordinal)='integer' AND ordinal>=0))"
)
SETUP_OWNER_ANSWER_ORDINAL_ROW = (1, 0)
# Resume's history beside the record: a request names the counter's value when
# it was asked; completion, failure and supersession name their request.
OWNER_FIELD_EVENTS_SQL = (
    "CREATE TABLE owner_field_events ("
    "event_id TEXT PRIMARY KEY NOT NULL CHECK(event_id GLOB 'owner_field_event_[0-9a-f]*' AND length(event_id)=82), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "destination TEXT NOT NULL CHECK(length(destination) BETWEEN 1 AND 64), "
    f"kind TEXT NOT NULL CHECK(kind IN ({_EVENT_KINDS})), "
    "request_id TEXT REFERENCES owner_field_events(event_id), "
    "over_ordinal INTEGER NOT NULL CHECK(typeof(over_ordinal)='integer' AND over_ordinal>=0), "
    "scan_id TEXT CHECK(scan_id IS NULL OR typeof(scan_id)='text'), "
    f"answer_id TEXT CHECK(answer_id IS NULL OR ({_HUMAN_ID.format('answer_id')})), "
    "occurred_at INTEGER NOT NULL CHECK(typeof(occurred_at)='integer' AND occurred_at>=0), "
    "CHECK((kind IN ('resume_requested','retired'))=(request_id IS NULL)), "
    "CHECK(kind<>'resume_requested' OR scan_id IS NOT NULL))"
)
OWNER_FIELD_EVENTS_KEY_INDEX_SQL = (
    "CREATE INDEX owner_field_events_key "
    "ON owner_field_events(connection_name,library_name,item_id,destination)"
)
OWNER_FIELD_EVENTS_REQUEST_INDEX_SQL = (
    "CREATE INDEX owner_field_events_request ON owner_field_events(request_id)"
)
OWNER_FIELD_EVENT_TRIGGERS = {
    f"owner_field_events_no_{operation}": (
        f"CREATE TRIGGER owner_field_events_no_{operation} BEFORE "
        f"{operation.upper()} ON owner_field_events BEGIN SELECT "
        "RAISE(ABORT, 'owner field events are append-only'); END"
    )
    for operation in ("update", "delete")
}
# One row per field of a book: the owner's latest answer there. The question
# halves stay null until the question check (C4'), both or neither.
OWNER_FIELD_ANSWERS_SQL = (
    "CREATE TABLE owner_field_answers ("
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    "destination TEXT NOT NULL CHECK(length(destination) BETWEEN 1 AND 64), "
    "source TEXT NOT NULL CHECK(source IN ('human','authored_description')), "
    f"answer_id TEXT NOT NULL UNIQUE CHECK({_HUMAN_ID.format('answer_id')}), "
    "ordinal INTEGER NOT NULL UNIQUE CHECK(typeof(ordinal)='integer' AND ordinal>0), "
    "outcome TEXT NOT NULL CHECK(outcome IN ('accept','keep_current','reject','owner_entered','pin','authored')), "
    "value_document TEXT NOT NULL CHECK(typeof(value_document)='text' AND json_valid(value_document) "
    "AND length(CAST(value_document AS BLOB)) BETWEEN 4 AND 32768), "
    "display_title TEXT CHECK(display_title IS NULL OR typeof(display_title)='text'), "
    "recorded_at INTEGER NOT NULL CHECK(typeof(recorded_at)='integer' AND recorded_at>=0), "
    "review_case_id TEXT NOT NULL CHECK(review_case_id GLOB 'review_case_[0-9a-f]*' AND length(review_case_id)=76), "
    "episode INTEGER NOT NULL CHECK(typeof(episode)='integer' AND episode>=0 AND episode<ordinal), "
    "previous_answer_id TEXT CHECK(previous_answer_id IS NULL OR "
    f"({_HUMAN_ID.format('previous_answer_id')} AND previous_answer_id<>answer_id)), "
    "carry_eligible INTEGER NOT NULL CHECK(carry_eligible IN (0,1)), "
    "open_resume_id TEXT UNIQUE REFERENCES owner_field_events(event_id), "
    f"pin_fingerprint TEXT CHECK(pin_fingerprint IS NULL OR ({_HEX64.format('pin_fingerprint')})), "
    "cover_selection_id TEXT REFERENCES owner_cover_selections(selection_id), "
    f"offer_digest TEXT CHECK(offer_digest IS NULL OR ({_HEX64.format('offer_digest')})), "
    f"seen_target_digest TEXT CHECK(seen_target_digest IS NULL OR ({_HEX64.format('seen_target_digest')})), "
    "CHECK((offer_digest IS NULL)=(seen_target_digest IS NULL)), "
    "CHECK((destination='cover')=(cover_selection_id IS NOT NULL)), "
    "CHECK(destination<>'cover' OR pin_fingerprint IS NULL), "
    "CHECK((source='authored_description')=(outcome='authored')), "
    "CHECK(source<>'authored_description' OR destination='description'), "
    "CHECK(open_resume_id IS NULL OR pin_fingerprint IS NULL), "
    "PRIMARY KEY(connection_name,library_name,item_id,destination)) WITHOUT ROWID"
)

_RECORD_COLUMNS = (
    "connection_name,library_name,item_id,destination,source,answer_id,ordinal,"
    "outcome,value_document,display_title,recorded_at,review_case_id,episode,"
    "previous_answer_id,carry_eligible,open_resume_id,pin_fingerprint,"
    "cover_selection_id,offer_digest,seen_target_digest"
)
_KEY_SQL = "connection_name=? AND library_name=? AND item_id=? AND destination=?"
_UPSERT_SQL = (
    f"INSERT INTO owner_field_answers({_RECORD_COLUMNS}) VALUES("
    + ",".join("?" * len(_RECORD_COLUMNS.split(",")))
    + ") ON CONFLICT(connection_name,library_name,item_id,destination) DO UPDATE SET "
    + ",".join(
        f"{column}=excluded.{column}" for column in _RECORD_COLUMNS.split(",")[4:]
    )
)
# The ledger rows the record folds, in `OwnerAnswerRow`'s field order: every
# owner decision, each cover answer naming its owner selection, and every
# owner authored description. `{human}` and `{authored}` narrow each arm.
_LEDGER_ANSWERS_SQL = (
    "SELECT 'human',decision.decision_id,decision.ordinal,decision.connection_name,"
    "decision.library_name,decision.item_id,decision.destination,"
    "decision.review_case_id,json_extract(decision.document,'$.episode'),"
    "decision.outcome,json_extract(decision.document,'$.cause'),"
    "decision.selected_value_document,decision.display_title,decision.occurred_at,"
    "decision.target_field_document,(SELECT selection.selection_id FROM "
    "owner_cover_selections selection WHERE selection.decision_id=decision.decision_id) "
    "FROM human_decisions decision WHERE {human} UNION ALL "
    "SELECT 'authored_description',authored.decision_id,authored.ordinal,"
    "authored.connection_name,authored.library_name,authored.item_id,'description',"
    "authored.review_case_id,authored.episode,'authored',NULL,'null',"
    "authored.display_title,json_extract(authored.document,'$.occurred_at'),'null',NULL "
    "FROM owner_authored_descriptions authored WHERE {authored}"
)


def next_ordinal(connection: sqlite3.Connection) -> int:
    """Take the next owner-answer ordinal, inside the caller's transaction."""
    row = connection.execute(
        "UPDATE owner_answer_ordinal SET ordinal=ordinal+1 WHERE singleton=1 "
        "RETURNING ordinal"
    ).fetchone()
    if row is None:
        raise MetadataReportError("database is unavailable or incompatible")
    return int(row[0])


def case_episode(connection: sqlite3.Connection, review_case_id: str) -> int:
    """The case's episode an answer is given in: its decision floor, 0 before
    it was ever admitted again (#1358). The floor counts every owner answer
    on the case, an authored description's too (ADR-0108)."""
    return int(
        connection.execute(
            "SELECT COALESCE((SELECT decision_floor FROM review_case_episodes "
            "WHERE review_case_id=?),0)",
            (review_case_id,),
        ).fetchone()[0]
    )


def ledger_answers(
    connection: sqlite3.Connection, key: ItemKey | None = None
) -> Iterator[OwnerAnswerRow]:
    """The ledger's owner answers, every field's or one field's."""
    parameters: tuple[str, ...] = ()
    if key is None:
        sql = _LEDGER_ANSWERS_SQL.format(human="1", authored="1")
    else:
        sql = _LEDGER_ANSWERS_SQL.format(
            human="decision.connection_name=? AND decision.library_name=? "
            "AND decision.item_id=? AND decision.destination=?",
            authored="authored.connection_name=? AND authored.library_name=? "
            "AND authored.item_id=? AND ?='description'",
        )
        parameters = (*key, *key)
    for row in connection.execute(sql, parameters):
        yield OwnerAnswerRow(*row)


_EVENT_COLUMNS = (
    "event_id,connection_name,library_name,item_id,destination,kind,"
    "request_id,over_ordinal,scan_id,answer_id,occurred_at"
)


def ledger_events(
    connection: sqlite3.Connection, key: ItemKey | None = None
) -> Iterator[OwnerFieldEventRow]:
    """Resume's events, every field's or one field's, in the order written."""
    if key is None:
        rows = connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM owner_field_events ORDER BY rowid"
        )
    else:
        rows = connection.execute(
            f"SELECT {_EVENT_COLUMNS} FROM owner_field_events WHERE {_KEY_SQL} "
            "ORDER BY rowid",
            key,
        )
    for row in rows:
        yield OwnerFieldEventRow(*row)


def stored_answers(connection: sqlite3.Connection) -> Iterator[OwnerFieldAnswer]:
    for row in connection.execute(f"SELECT {_RECORD_COLUMNS} FROM owner_field_answers"):
        values = list(row)
        values[14] = bool(values[14])
        yield OwnerFieldAnswer(*values)


def record_differences(
    connection: sqlite3.Connection,
) -> dict[ItemKey, tuple[OwnerFieldAnswer | None, OwnerFieldAnswer | None]]:
    """Every field whose stored record is not the ledger's fold, as
    (folded, stored); None where one of them has no row.

    The fold takes the owner's answers and Resume's events, so every column
    compares, `open_resume_id` included: a completion the record missed, a
    request it does not name as open or one it names after the request
    closed all differ. `offer_digest` and `seen_target_digest` compare as the
    fold gives them, null, until the question check (C4') writes them.
    """
    stored = {answer.key: answer for answer in stored_answers(connection)}
    folded = fold_owner_field_answers(
        ledger_answers(connection), ledger_events(connection)
    )
    return {
        key: (folded.get(key), stored.get(key))
        for key in folded.keys() | stored.keys()
        if folded.get(key) != stored.get(key)
    }


def _current(connection: sqlite3.Connection, key: ItemKey) -> OwnerFieldAnswer | None:
    row = connection.execute(
        f"SELECT {_RECORD_COLUMNS} FROM owner_field_answers WHERE {_KEY_SQL}", key
    ).fetchone()
    if row is None:
        return None
    values = list(row)
    values[14] = bool(values[14])
    return OwnerFieldAnswer(*values)


def _write(connection: sqlite3.Connection, answer: OwnerFieldAnswer) -> None:
    values = list(astuple(answer))
    values[14] = int(values[14])
    connection.execute(_UPSERT_SQL, values)


def _ledger_row(
    connection: sqlite3.Connection, source: str, answer_id: str
) -> OwnerAnswerRow:
    row = connection.execute(
        _LEDGER_ANSWERS_SQL.format(
            human="decision.decision_id=? AND ?='human'",
            authored="authored.decision_id=? AND ?='authored_description'",
        ),
        (answer_id, source) * 2,
    ).fetchone()
    if row is None:
        raise MetadataReportError("database is unavailable or incompatible")
    return OwnerAnswerRow(*row)


def record_answer(
    connection: sqlite3.Connection,
    source: str,
    answer_id: str,
    *,
    pin_fingerprint: str | None = None,
) -> None:
    """Make the just-recorded owner answer the field's answer.

    A pin that stands in place of an answer passes what it keeps. An answer
    while a Resume is open closes it `resume_superseded`, naming the answer,
    so an open request never covers less than the record's answer (S10).
    """
    row = _ledger_row(connection, source, answer_id)
    request = _open_request(connection, row.key)
    if request is not None:
        _append_event(
            connection,
            row.key,
            "resume_superseded",
            request_id=request[0],
            scan_id=None,
            answer_id=answer_id,
            at=row.recorded_at,
        )
    _write(connection, answer_from(row, _current(connection, row.key), pin_fingerprint))


def _decision(connection: sqlite3.Connection, decision_id: str) -> tuple[ItemKey, int]:
    row = connection.execute(
        "SELECT connection_name,library_name,item_id,destination,occurred_at "
        "FROM human_decisions WHERE decision_id=?",
        (decision_id,),
    ).fetchone()
    if row is None:
        raise MetadataReportError("database is unavailable or incompatible")
    return (row[0], row[1], row[2], row[3]), int(row[4])


def _decision_key(connection: sqlite3.Connection, decision_id: str) -> ItemKey:
    return _decision(connection, decision_id)[0]


def mark_pin(
    connection: sqlite3.Connection, decision_id: str, pin_fingerprint: str | None
) -> None:
    """The just-recorded pin (or, with None, unpin) ``decision_id`` marks the
    field's answer; the answer itself stays (ADR-0085, Amendment 2)."""
    connection.execute(
        f"UPDATE owner_field_answers SET pin_fingerprint=? WHERE {_KEY_SQL}",
        (pin_fingerprint, *_decision_key(connection, decision_id)),
    )


# The answer a withdrawal takes back: the latest owner decision of its case's
# episode before it, pins and unpins aside, as `_require_withdrawable` read it.
_WITHDRAWN_SQL = (
    "SELECT standing.decision_id FROM human_decisions withdrawal "
    "JOIN human_decisions standing "
    "ON standing.review_case_id=withdrawal.review_case_id "
    "AND standing.destination=withdrawal.destination "
    "AND standing.outcome NOT IN ('pin','unpin') "
    "AND standing.ordinal<withdrawal.ordinal "
    "AND standing.ordinal>json_extract(withdrawal.document,'$.episode') "
    "WHERE withdrawal.decision_id=? ORDER BY standing.ordinal DESC LIMIT 1"
)


def withdraw_answer(connection: sqlite3.Connection, decision_id: str) -> None:
    """The just-recorded withdrawal ``decision_id`` takes back its case's
    standing answer.

    Where that is the field's answer, the field goes back to the answer from
    an earlier case it replaced, no longer carried, or is left without one.
    Another answer's withdrawal leaves the record alone. An open Resume stays
    open over the restored answer, which it covers; one left with no answer
    closes `resume_superseded`, naming the withdrawal (ADR-0108).
    """
    key, occurred_at = _decision(connection, decision_id)
    current = _current(connection, key)
    withdrawn = connection.execute(_WITHDRAWN_SQL, (decision_id,)).fetchone()
    if current is None or withdrawn is None or current.answer_id != withdrawn[0]:
        return
    restored = restored_answer(
        ledger_answers(connection, key), current.previous_answer_id
    )
    if restored is None:
        request = _open_request(connection, key)
        if request is not None:
            _append_event(
                connection,
                key,
                "resume_superseded",
                request_id=request[0],
                scan_id=None,
                answer_id=decision_id,
                at=occurred_at,
            )
        connection.execute(f"DELETE FROM owner_field_answers WHERE {_KEY_SQL}", key)
    else:
        _write(connection, replace(restored, open_resume_id=current.open_resume_id))


def matching_changed(
    connection: sqlite3.Connection, decision_id: str, superseded: str
) -> None:
    """Book matching's just-recorded row ``decision_id`` asked the field again
    over ``superseded``: where that is the field's answer it stays, no longer
    carried (ADR-0108)."""
    connection.execute(
        f"UPDATE owner_field_answers SET carry_eligible=0 WHERE {_KEY_SQL} "
        "AND answer_id=?",
        (*_decision_key(connection, decision_id), superseded),
    )


# The field's request with no completion or supersession; failure leaves it open.
_OPEN_REQUEST_SQL = (
    "SELECT request.event_id,request.over_ordinal,request.scan_id "
    "FROM owner_field_events request WHERE request.connection_name=? "
    "AND request.library_name=? AND request.item_id=? AND request.destination=? "
    "AND request.kind='resume_requested' AND "
    + _UNCLOSED.format("request")
    + " ORDER BY request.rowid DESC LIMIT 1"
)


def _open_request(
    connection: sqlite3.Connection, key: ItemKey
) -> tuple[str, int, str | None] | None:
    row = connection.execute(_OPEN_REQUEST_SQL, key).fetchone()
    return None if row is None else (row[0], int(row[1]), row[2])


def _append_event(
    connection: sqlite3.Connection,
    key: ItemKey,
    kind: str,
    *,
    request_id: str | None,
    scan_id: str | None,
    answer_id: str | None,
    at: int,
) -> str:
    """Append one event to the field's chain, at the counter's value now
    (read, never taken), inside the caller's transaction."""
    counter = connection.execute(
        "SELECT ordinal FROM owner_answer_ordinal WHERE singleton=1"
    ).fetchone()
    if counter is None:
        raise MetadataReportError("database is unavailable or incompatible")
    previous = connection.execute(
        f"SELECT event_id FROM owner_field_events WHERE {_KEY_SQL} "
        "ORDER BY rowid DESC LIMIT 1",
        key,
    ).fetchone()
    connection_name, library_name, item_id, destination = key
    event_id = owner_field_event_id(
        connection_name=connection_name,
        library_name=library_name,
        item_id=item_id,
        destination=destination,
        kind=kind,
        request_id=request_id,
        over_ordinal=int(counter[0]),
        scan_id=scan_id,
        answer_id=answer_id,
        occurred_at=at,
        previous_event_id=None if previous is None else previous[0],
    )
    connection.execute(
        f"INSERT INTO owner_field_events({_EVENT_COLUMNS}) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (event_id, *key, kind, request_id, int(counter[0]), scan_id, answer_id, at),
    )
    return event_id


def request_resume(
    connection: sqlite3.Connection,
    item: tuple[str, str, str],
    destination: str,
    scan_id: str,
    at: int,
) -> str | None:
    """Ask Resume of the field's answer, inside the caller's transaction.

    The request covers every answer up to the counter's value now. One asked
    again closes the open request `resume_superseded`, naming no answer,
    before the new one is written. Returns the request, or None where the
    record holds no answer for the field or a pin is on it, which Resume
    never hands back (ADR-0085, Amendment 2). Whether the field still offers
    Resume (C0 `resume_offer`) the caller checks first.
    """
    key = (*item, destination)
    current = _current(connection, key)
    if current is None or current.pin_fingerprint is not None:
        return None
    previous = _open_request(connection, key)
    if previous is not None:
        _append_event(
            connection,
            key,
            "resume_superseded",
            request_id=previous[0],
            scan_id=None,
            answer_id=None,
            at=at,
        )
    request = _append_event(
        connection,
        key,
        "resume_requested",
        request_id=None,
        scan_id=scan_id,
        answer_id=current.answer_id,
        at=at,
    )
    _write(connection, replace(current, open_resume_id=request))
    return request


def complete_resume(
    connection: sqlite3.Connection,
    item: tuple[str, str, str],
    destination: str,
    at: int,
) -> bool:
    """Complete the field's open Resume, inside the caller's transaction:
    the record's answer goes when the request covers it, and
    `resume_completed` names the answer handed back. False with no open
    request."""
    key = (*item, destination)
    request = _open_request(connection, key)
    if request is None:
        return False
    current = _current(connection, key)
    handed_back = None
    if current is not None and current.ordinal <= request[1]:
        handed_back = current.answer_id
        connection.execute(f"DELETE FROM owner_field_answers WHERE {_KEY_SQL}", key)
    _append_event(
        connection,
        key,
        "resume_completed",
        request_id=request[0],
        scan_id=request[2],
        answer_id=handed_back,
        at=at,
    )
    return True


# Each request on the scan with no completion or supersession, whose latest
# event is not already its failure.
_FAILING_REQUESTS_SQL = (
    "SELECT request.event_id,request.connection_name,request.library_name,"
    "request.item_id,request.destination FROM owner_field_events request "
    "WHERE request.kind='resume_requested' AND request.scan_id=? AND "
    + _UNCLOSED.format("request")
    + " AND COALESCE((SELECT latest.kind FROM owner_field_events latest "
    "WHERE latest.request_id=request.event_id ORDER BY latest.rowid DESC LIMIT 1),"
    "'')<>'resume_failed' ORDER BY request.rowid"
)


def fail_open_resumes_for_scan(
    connection: sqlite3.Connection, scan_id: str, at: int
) -> int:
    """The scan ended with failures: each open request on it, not already
    failed since, writes `resume_failed`, inside the caller's transaction.
    The record and the request stay; a later successful run still completes
    it. Returns how many were written."""
    failing = connection.execute(_FAILING_REQUESTS_SQL, (scan_id,)).fetchall()
    for request_id, *key in failing:
        _append_event(
            connection,
            (key[0], key[1], key[2], key[3]),
            "resume_failed",
            request_id=request_id,
            scan_id=scan_id,
            answer_id=None,
            at=at,
        )
    return len(failing)


def has_open_resume_for_scan(connection: sqlite3.Connection, scan_id: str) -> bool:
    """Whether any request on the scan has no completion or supersession."""
    return bool(
        connection.execute(
            "SELECT EXISTS(SELECT 1 FROM owner_field_events request "
            "WHERE request.kind='resume_requested' AND request.scan_id=? AND "
            + _UNCLOSED.format("request")
            + ")",
            (scan_id,),
        ).fetchone()[0]
    )


_REFUSED = "database is unavailable or incompatible"
# Each names a ledger row that breaks one of the record's rules; any row
# refuses the store (ADR-0108).
_RECORD_RULES = (
    # The counter is ahead of every ordinal it gave.
    "SELECT 1 FROM owner_answer_ordinal counter WHERE counter.ordinal<MAX("
    "COALESCE((SELECT MAX(ordinal) FROM human_decisions),0),"
    "COALESCE((SELECT MAX(ordinal) FROM owner_authored_descriptions),0),"
    "COALESCE((SELECT MAX(ordinal) FROM owner_field_answers),0),"
    "COALESCE((SELECT MAX(over_ordinal) FROM owner_field_events),0))",
    # No two answers share an ordinal.
    "SELECT 1 FROM owner_authored_descriptions authored JOIN human_decisions "
    "decision ON decision.ordinal=authored.ordinal",
    # A human answer is the decision it names, as recorded.
    "SELECT 1 FROM owner_field_answers answer WHERE answer.source='human' "
    "AND NOT EXISTS (SELECT 1 FROM human_decisions decision WHERE "
    "decision.decision_id=answer.answer_id AND decision.ordinal=answer.ordinal "
    "AND decision.outcome=answer.outcome AND decision.destination=answer.destination "
    "AND decision.connection_name=answer.connection_name "
    "AND decision.library_name=answer.library_name AND decision.item_id=answer.item_id "
    "AND decision.review_case_id=answer.review_case_id "
    "AND decision.selected_value_document=answer.value_document "
    "AND json_extract(decision.document,'$.episode') IS answer.episode "
    "AND decision.occurred_at=answer.recorded_at "
    "AND decision.display_title IS answer.display_title)",
    # An authored answer is the owner authored description it names.
    "SELECT 1 FROM owner_field_answers answer WHERE "
    "answer.source='authored_description' AND NOT EXISTS (SELECT 1 FROM "
    "owner_authored_descriptions authored WHERE authored.decision_id=answer.answer_id "
    "AND authored.ordinal=answer.ordinal AND authored.episode=answer.episode "
    "AND authored.review_case_id=answer.review_case_id "
    "AND authored.connection_name=answer.connection_name "
    "AND authored.library_name=answer.library_name AND authored.item_id=answer.item_id "
    "AND answer.value_document='null' "
    "AND authored.display_title IS answer.display_title "
    "AND json_extract(authored.document,'$.occurred_at') IS answer.recorded_at)",
    # A cover answer names the owner selection its decision recorded.
    "SELECT 1 FROM owner_field_answers answer WHERE answer.cover_selection_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM owner_cover_selections selection WHERE "
    "selection.selection_id=answer.cover_selection_id "
    "AND selection.decision_id=answer.answer_id)",
    # What a withdrawal restores is an earlier answer for the same field.
    "SELECT 1 FROM owner_field_answers answer WHERE answer.previous_answer_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM human_decisions decision WHERE "
    "decision.decision_id=answer.previous_answer_id AND decision.ordinal<answer.ordinal "
    "AND decision.destination=answer.destination "
    "AND decision.connection_name=answer.connection_name "
    "AND decision.library_name=answer.library_name AND decision.item_id=answer.item_id) "
    "AND NOT EXISTS (SELECT 1 FROM owner_authored_descriptions authored WHERE "
    "authored.decision_id=answer.previous_answer_id AND authored.ordinal<answer.ordinal "
    "AND answer.destination='description' "
    "AND authored.connection_name=answer.connection_name "
    "AND authored.library_name=answer.library_name AND authored.item_id=answer.item_id)",
    # An open Resume is an unclosed request of the same field, asked over
    # the answer it would hand back.
    "SELECT 1 FROM owner_field_answers answer WHERE answer.open_resume_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM owner_field_events request WHERE "
    "request.event_id=answer.open_resume_id AND request.kind='resume_requested' "
    "AND request.connection_name=answer.connection_name "
    "AND request.library_name=answer.library_name AND request.item_id=answer.item_id "
    "AND request.destination=answer.destination AND answer.ordinal<=request.over_ordinal "
    "AND NOT EXISTS (SELECT 1 FROM owner_field_events closing WHERE "
    "closing.request_id=request.event_id "
    "AND closing.kind IN ('resume_completed','resume_superseded')))",
    # An event that answers a request answers a request of its own field.
    "SELECT 1 FROM owner_field_events event WHERE event.request_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM owner_field_events request WHERE "
    "request.event_id=event.request_id AND request.kind='resume_requested' "
    "AND request.rowid<event.rowid "
    "AND request.connection_name=event.connection_name "
    "AND request.library_name=event.library_name AND request.item_id=event.item_id "
    "AND request.destination=event.destination)",
    # A request completes at most once and is superseded at most once.
    "SELECT 1 FROM owner_field_events WHERE kind IN "
    "('resume_completed','resume_superseded') GROUP BY request_id,kind "
    "HAVING COUNT(*)>1",
    # A field has at most one unclosed request.
    "SELECT 1 FROM owner_field_events request WHERE request.kind='resume_requested' "
    f"AND {_UNCLOSED.format('request')} GROUP BY request.connection_name,"
    "request.library_name,request.item_id,request.destination HAVING COUNT(*)>1",
    # No answer is given above an unclosed request's bound on its field: an
    # answer closes it first (S10). Withdrawals, Book matching's rows, pins
    # and unpins may follow an open request and are left to the fold.
    "SELECT 1 FROM owner_field_events request WHERE request.kind='resume_requested' "
    f"AND {_UNCLOSED.format('request')} AND (EXISTS (SELECT 1 FROM human_decisions "
    "decision WHERE decision.connection_name=request.connection_name "
    "AND decision.library_name=request.library_name "
    "AND decision.item_id=request.item_id AND decision.destination=request.destination "
    "AND decision.ordinal>request.over_ordinal "
    "AND decision.outcome NOT IN ('withdraw','pin','unpin')) OR EXISTS (SELECT 1 FROM "
    "owner_authored_descriptions authored WHERE request.destination='description' "
    "AND authored.connection_name=request.connection_name "
    "AND authored.library_name=request.library_name "
    "AND authored.item_id=request.item_id AND authored.ordinal>request.over_ordinal))",
    # A completed request was never superseded.
    "SELECT 1 FROM owner_field_events completed WHERE completed.kind='resume_completed' "
    "AND EXISTS (SELECT 1 FROM owner_field_events superseded WHERE "
    "superseded.request_id=completed.request_id AND superseded.kind='resume_superseded')",
    # An event closing or failing a request is written at or after it.
    "SELECT 1 FROM owner_field_events closing JOIN owner_field_events request "
    "ON request.event_id=closing.request_id "
    "WHERE closing.over_ordinal<request.over_ordinal",
)


def validate_owner_field_answers(connection: sqlite3.Connection) -> None:
    """Refuse a store whose record, counter or events break a ledger rule,
    or whose record is not the fold of its ledger.

    Reads ledger rows only, so it runs the same with or without derived rows.
    """
    if connection.execute("SELECT COUNT(*) FROM owner_answer_ordinal").fetchone() != (
        1,
    ):
        raise MetadataReportError(_REFUSED)
    for rule in _RECORD_RULES:
        if connection.execute(rule).fetchone() is not None:
            raise MetadataReportError(_REFUSED)
    previous: dict[ItemKey, str] = {}
    for row in connection.execute(
        "SELECT event_id,connection_name,library_name,item_id,destination,kind,"
        "request_id,over_ordinal,scan_id,answer_id,occurred_at "
        "FROM owner_field_events ORDER BY rowid"
    ):
        key = (row[1], row[2], row[3], row[4])
        if row[0] != owner_field_event_id(
            connection_name=row[1],
            library_name=row[2],
            item_id=row[3],
            destination=row[4],
            kind=row[5],
            request_id=row[6],
            over_ordinal=row[7],
            scan_id=row[8],
            answer_id=row[9],
            occurred_at=row[10],
            previous_event_id=previous.get(key),
        ):
            raise MetadataReportError(_REFUSED)
        previous[key] = row[0]
    if record_differences(connection):
        raise MetadataReportError(_REFUSED)


__all__ = [
    "OWNER_ANSWER_ORDINAL_SQL",
    "OWNER_FIELD_ANSWERS_SQL",
    "OWNER_FIELD_EVENTS_KEY_INDEX_SQL",
    "OWNER_FIELD_EVENTS_REQUEST_INDEX_SQL",
    "OWNER_FIELD_EVENTS_SQL",
    "OWNER_FIELD_EVENT_TRIGGERS",
    "SETUP_OWNER_ANSWER_ORDINAL_ROW",
    "case_episode",
    "complete_resume",
    "fail_open_resumes_for_scan",
    "has_open_resume_for_scan",
    "ledger_answers",
    "ledger_events",
    "mark_pin",
    "matching_changed",
    "next_ordinal",
    "record_answer",
    "record_differences",
    "request_resume",
    "stored_answers",
    "validate_owner_field_answers",
    "withdraw_answer",
]
