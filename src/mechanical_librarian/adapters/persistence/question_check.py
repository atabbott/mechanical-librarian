"""The question check's ledger outcomes, `carry_outcomes` (ADR-0108).

Each record answer, case and episode the question check reaches gets one row:
`carried` names the carried decision, and every other outcome says why the
field is asked again or left for the retry. `resume_pending` and `deferred`
are provisional, so the retry replaces them; every other outcome is final.
Triggers refuse every delete and any update of a final row, and a write is an
upsert that changes only a provisional row. No row references a derived row:
the case is named by id only, so the table survives a regeneration with the
rest of the ledger.
"""

from __future__ import annotations

import sqlite3

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.owner_field_answer import ItemKey
from mechanical_librarian.domain.question_check import (
    CARRY_OUTCOMES,
    PROVISIONAL_CARRY_OUTCOMES,
    CarryOutcome,
)

_HUMAN_ID = "{0} GLOB 'human_decision_[0-9a-f]*' AND length({0})=79"
_PROVISIONAL = ",".join(
    f"'{outcome}'" for outcome in sorted(PROVISIONAL_CARRY_OUTCOMES)
)
CARRY_OUTCOMES_SQL = (
    "CREATE TABLE carry_outcomes ("
    f"answer_id TEXT NOT NULL CHECK({_HUMAN_ID.format('answer_id')}), "
    "review_case_id TEXT NOT NULL CHECK(review_case_id GLOB 'review_case_[0-9a-f]*' "
    "AND length(review_case_id)=76), "
    "episode INTEGER NOT NULL CHECK(typeof(episode)='integer' AND episode>=0), "
    "connection_name TEXT NOT NULL, library_name TEXT NOT NULL, item_id TEXT NOT NULL, "
    # Covers are never carried (ADR-0108).
    "destination TEXT NOT NULL CHECK(length(destination) BETWEEN 1 AND 64 "
    "AND destination<>'cover'), "
    "outcome TEXT NOT NULL CHECK(outcome IN ("
    + ",".join(f"'{outcome}'" for outcome in CARRY_OUTCOMES)
    + ")), "
    "carried_decision_id TEXT CHECK(carried_decision_id IS NULL OR ("
    + _HUMAN_ID.format("carried_decision_id")
    + ")), "
    "checked_at INTEGER NOT NULL CHECK(typeof(checked_at)='integer' AND checked_at>=0), "
    # The derived epoch the check ran in, for the upgrade notice (contract
    # S14, T3).
    "derived_epoch INTEGER NOT NULL CHECK(typeof(derived_epoch)='integer' "
    "AND derived_epoch>=0), "
    "CHECK((outcome='carried')=(carried_decision_id IS NOT NULL)), "
    "PRIMARY KEY(answer_id,review_case_id,episode)) WITHOUT ROWID"
)
CARRY_OUTCOME_TRIGGERS = {
    "carry_outcomes_no_delete": (
        "CREATE TRIGGER carry_outcomes_no_delete BEFORE DELETE ON carry_outcomes "
        "BEGIN SELECT RAISE(ABORT, 'carry outcomes are kept'); END"
    ),
    "carry_outcomes_final": (
        "CREATE TRIGGER carry_outcomes_final BEFORE UPDATE ON carry_outcomes "
        f"WHEN OLD.outcome NOT IN ({_PROVISIONAL}) "
        "BEGIN SELECT RAISE(ABORT, 'a final carry outcome is kept'); END"
    ),
}
_COLUMNS = (
    "answer_id,review_case_id,episode,connection_name,library_name,item_id,"
    "destination,outcome,carried_decision_id,checked_at,derived_epoch"
)
_UPSERT_SQL = (
    f"INSERT INTO carry_outcomes({_COLUMNS}) VALUES(?,?,?,?,?,?,?,?,?,?,?) "
    "ON CONFLICT(answer_id,review_case_id,episode) DO UPDATE SET "
    "outcome=excluded.outcome,carried_decision_id=excluded.carried_decision_id,"
    "checked_at=excluded.checked_at,derived_epoch=excluded.derived_epoch "
    f"WHERE carry_outcomes.outcome IN ({_PROVISIONAL})"
)


def _derived_epoch(connection: sqlite3.Connection) -> int:
    """The derived epoch the ledger is in now, read in the caller's
    transaction.

    TODO(#2040, half 1): `ledger_meta` lands with the cutover's first packet;
    until it does this store has none and every check runs at epoch 0. Once
    it exists, read it unguarded so a missing row refuses.
    """
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ledger_meta'"
        ).fetchone()
        is None
    ):
        return 0
    row = connection.execute("SELECT derived_epoch FROM ledger_meta").fetchone()
    if row is None or type(row[0]) is not int:
        raise MetadataReportError("database is unavailable or incompatible")
    return row[0]


def record_carry_outcome(
    connection: sqlite3.Connection,
    key: ItemKey,
    *,
    answer_id: str,
    review_case_id: str,
    episode: int,
    outcome: CarryOutcome,
    carried_decision_id: str | None,
    checked_at: int,
) -> bool:
    """Write the check's outcome for the answer on the case and episode,
    inside the caller's transaction. A first row is inserted; a provisional
    row is replaced; a final row is kept. Returns whether a row was written.
    """
    return (
        connection.execute(
            _UPSERT_SQL,
            (
                answer_id,
                review_case_id,
                episode,
                *key,
                outcome,
                carried_decision_id,
                checked_at,
                _derived_epoch(connection),
            ),
        ).rowcount
        == 1
    )


_REFUSED = "database is unavailable or incompatible"
# Each names a carry outcome that breaks one of its ledger rules; any row
# refuses the store (ADR-0108). Ledger rows only.
_CARRY_RULES = (
    # The answer is an owner answer of the same field.
    "SELECT 1 FROM carry_outcomes outcome WHERE NOT EXISTS (SELECT 1 FROM "
    "human_decisions answer WHERE answer.decision_id=outcome.answer_id "
    "AND answer.connection_name=outcome.connection_name "
    "AND answer.library_name=outcome.library_name "
    "AND answer.item_id=outcome.item_id AND answer.destination=outcome.destination) "
    "AND NOT EXISTS (SELECT 1 FROM owner_authored_descriptions authored WHERE "
    "authored.decision_id=outcome.answer_id AND outcome.destination='description' "
    "AND authored.connection_name=outcome.connection_name "
    "AND authored.library_name=outcome.library_name "
    "AND authored.item_id=outcome.item_id)",
    # A carried outcome names a decision carried from the answer's root, on
    # the outcome's case and episode.
    "SELECT 1 FROM carry_outcomes outcome WHERE outcome.outcome='carried' "
    "AND NOT EXISTS (SELECT 1 FROM human_decisions answer JOIN human_decisions "
    "carried ON carried.decision_id=outcome.carried_decision_id "
    "AND carried.carried_from=COALESCE(answer.carried_from,answer.decision_id) "
    "WHERE answer.decision_id=outcome.answer_id "
    "AND carried.review_case_id=outcome.review_case_id "
    "AND json_extract(carried.document,'$.episode') IS outcome.episode "
    "AND carried.destination=outcome.destination)",
)


def validate_carry_outcomes(connection: sqlite3.Connection) -> None:
    """Refuse a store whose carry outcomes break a ledger rule."""
    for rule in _CARRY_RULES:
        if connection.execute(rule).fetchone() is not None:
            raise MetadataReportError(_REFUSED)


__all__ = [
    "CARRY_OUTCOMES_SQL",
    "CARRY_OUTCOME_TRIGGERS",
    "record_carry_outcome",
    "validate_carry_outcomes",
]
