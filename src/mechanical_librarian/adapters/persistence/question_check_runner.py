"""The question check run over the store (ADR-0108, "The question check").

`check_case_answers` runs steps 0 to 5 for a case inside the projection's
transaction, or the retry's, and writes each answer's `carry_outcomes` row;
the carried row itself is formed by the decision store's recorder, which the
caller passes. `retry_cases` lists the published cases a retry still owes.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Literal, cast

from mechanical_librarian.adapters.persistence.level_reconciliation import (
    case_level_unreconciled,
    current_level_offer,
)
from mechanical_librarian.adapters.persistence.owner_field_answers import (
    answer_written_sql,
    case_episode,
    close_resume_over_answer,
    resume_state,
)
from mechanical_librarian.adapters.persistence.owner_links import owner_link_bound
from mechanical_librarian.adapters.persistence.question_check import (
    record_carry_outcome,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.decision_question import DecisionQuestion
from mechanical_librarian.domain.field_effect import (
    FieldEffectError,
    project_decision_value,
)
from mechanical_librarian.domain.human_decision import (
    HumanDecisionError,
    case_question,
)
from mechanical_librarian.domain.owner_field_answer import ItemKey
from mechanical_librarian.domain.question_check import (
    PROVISIONAL_CARRY_OUTCOMES,
    CheckedAnswer,
    carry_refusal_outcome,
    check_question,
)
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    ReviewCaseValueError,
    current_offer,
)

# A carried row's recording, as `SQLiteDecisionRepository` forms it inside the
# check's savepoint: the case, its current offer, the answer, the question it
# was given against, whether it is written, the episode and the time. Returns
# the carried decision's id.
CarryRecorder = Callable[
    [ReviewCase, tuple[dict[str, object], ...], str, DecisionQuestion, bool, int, int],
    str,
]
# The record's answers of the case's book the check has not settled on the
# case and episode: every field but the cover, other than one answered there.
# `{settled}` says which existing outcome rows settle it.
_UNCHECKED_SQL = (
    "SELECT record.destination,record.source,record.answer_id,record.outcome,"
    "record.pin_fingerprint IS NOT NULL,record.carry_eligible,record.offer_digest,"
    "record.seen_target_digest,(SELECT json_extract(decision.document,'$.follows') "
    "IS NOT NULL FROM human_decisions decision "
    "WHERE decision.decision_id=record.answer_id),"
    "(SELECT outcome.outcome FROM carry_outcomes outcome "
    "WHERE outcome.answer_id=record.answer_id AND outcome.review_case_id=:case "
    "AND outcome.episode=:episode) "
    "FROM owner_field_answers record WHERE record.connection_name=:connection "
    "AND record.library_name=:library AND record.item_id=:item "
    "AND record.destination<>'cover' "
    "AND NOT (record.review_case_id=:case AND record.episode=:episode) "
    "AND NOT EXISTS (SELECT 1 FROM carry_outcomes outcome "
    "WHERE outcome.answer_id=record.answer_id AND outcome.review_case_id=:case "
    "AND outcome.episode=:episode AND {settled}) ORDER BY record.destination"
)
# The carry chain of answer `?`: its root and every row carried from it.
_CHAIN_SQL = (
    "SELECT chain.decision_id FROM human_decisions answer JOIN human_decisions "
    "chain ON chain.decision_id=COALESCE(answer.carried_from,answer.decision_id) "
    "OR chain.carried_from=COALESCE(answer.carried_from,answer.decision_id) "
    "WHERE answer.decision_id=?"
)
_IN_FLIGHT = frozenset({"planned", "claimed", "applying", "reconciling", "retryable"})
# A plan stopped on one of these waits for the owner (ADR-0108, step 3).
_STOPPED_FOR_OWNER = frozenset(
    {"third_value", "target_changed", "readback", "restore_unavailable"}
)


def _chain_plan(
    connection: sqlite3.Connection, answer_id: str, destination: str
) -> tuple[Literal["none", "in_flight", "stopped"], tuple[object] | None]:
    """The carry chain's plan state and the value it wrote: a verified
    plan's desired value, else the projected value of a chain decision with
    nothing to write, else None (ADR-0108, steps 2 and 3)."""
    plan: Literal["none", "in_flight", "stopped"] = "none"
    written: tuple[object] | None = None
    stopped = False
    for state, document in connection.execute(
        "SELECT state,document FROM field_effects WHERE decision_id IN ("
        + _CHAIN_SQL
        + ") ORDER BY rowid",
        (answer_id,),
    ):
        failure = json.loads(document).get("failure")
        if state in _IN_FLIGHT or (state == "stale" and failure == "connection"):
            plan = "in_flight"
        elif state == "verified":
            written = (json.loads(document)["plan"]["desired_value"],)
        elif failure in _STOPPED_FOR_OWNER:
            stopped = True
    # Whether the chain is written at all is the ledger's own predicate; the
    # value it wrote is read only then.
    is_written = bool(
        connection.execute(
            "SELECT " + answer_written_sql("?"), (answer_id,)
        ).fetchone()[0]
    )
    if is_written and written is None:
        unchanged = connection.execute(
            "SELECT decision.selected_value_document FROM field_effect_no_changes "
            "no_change JOIN human_decisions decision "
            "ON decision.decision_id=no_change.decision_id WHERE no_change.decision_id IN ("
            + _CHAIN_SQL
            + ") ORDER BY decision.ordinal DESC LIMIT 1",
            (answer_id,),
        ).fetchone()
        if unchanged is not None:
            try:
                written = (
                    project_decision_value(destination, json.loads(unchanged[0])),
                )
            except FieldEffectError:
                written = None
    if plan == "none" and stopped and not is_written:
        plan = "stopped"
    return plan, written


def check_case_answers(
    connection: sqlite3.Connection,
    case: ReviewCase,
    at: int,
    carry: CarryRecorder,
    *,
    retry: bool = False,
) -> None:
    """Run the question check on ``case`` for each record answer of its book
    given on another case or episode, inside the caller's transaction
    (ADR-0108, "The question check").

    The projection runs it once per answer, case and episode: any outcome
    row settles the field. The retry (``retry``) also replaces a provisional
    row, and leaves one whose outcome stands unchanged as it is. Each field
    has its own savepoint, so a store refusal undoes only that field and
    writes no row, and the retry takes it. A case whose Book matching check
    is still owed, or whose report no longer binds the book's link, writes
    nothing: its offer is not yet the one the owner would answer.
    """
    target = case.target
    item = (str(target["connection"]), str(target["library"]), str(target["item"]))
    if not owner_link_bound(connection, case.review_case_id) or (
        case_level_unreconciled(connection, case.review_case_id)
    ):
        return
    try:
        offer = current_offer(
            case, current_level_offer(connection, case.review_case_id)
        )
    except ReviewCaseValueError:
        return
    episode = case_episode(connection, case.review_case_id)
    rows = connection.execute(
        _UNCHECKED_SQL.format(
            settled=(
                "outcome.outcome NOT IN ("
                + ",".join(f"'{value}'" for value in sorted(PROVISIONAL_CARRY_OUTCOMES))
                + ")"
            )
            if retry
            else "1"
        ),
        {
            "case": case.review_case_id,
            "episode": episode,
            "connection": item[0],
            "library": item[1],
            "item": item[2],
        },
    ).fetchall()
    for row in rows:
        _check_answer(connection, case, offer, item, episode, at, carry, row)


def _check_answer(
    connection: sqlite3.Connection,
    case: ReviewCase,
    offer: tuple[dict[str, object], ...],
    item: tuple[str, str, str],
    episode: int,
    at: int,
    carry: CarryRecorder,
    row: tuple[object, ...],
) -> None:
    """One field's check in its own savepoint (ADR-0108, steps 0 to 5)."""
    destination, answer_id, existing = str(row[0]), str(row[2]), row[9]
    key: ItemKey = (*item, destination)
    connection.execute("SAVEPOINT question_check")
    try:
        plan, written = _chain_plan(connection, answer_id, destination)
        answer = CheckedAnswer(
            destination=destination,
            source=cast("Literal['human', 'authored_description']", row[1]),
            outcome=str(row[3]),
            pinned=bool(row[4]),
            carry_eligible=bool(row[5]),
            resume=resume_state(connection, key),
            plan=plan,
            written=written,
            corrective=bool(row[8]),
            offer_digest=cast("str | None", row[6]),
            seen_target_digest=cast("str | None", row[7]),
        )
        question = case_question(case, offer, destination)
        actionable = connection.execute(
            "SELECT has_owner_decision_actions FROM review_case_field_facts "
            "WHERE review_case_id=? AND destination=?",
            (case.review_case_id, destination),
        ).fetchone() == (1,)
        step = check_question(answer, question, actionable)
        outcome = step.outcome
        if outcome is None or outcome == existing:
            connection.execute("RELEASE question_check")
            return
        carried: str | None = None
        if step.close_resume:
            close_resume_over_answer(connection, key, at)
        if outcome == "carried":
            # A carried step has the question and the halves it compared.
            if question is None or answer.offer_digest is None:
                raise _Transient
            connection.execute("SAVEPOINT question_carry")
            try:
                carried = carry(
                    case,
                    offer,
                    answer_id,
                    DecisionQuestion(answer.offer_digest, question.target_part),
                    step.written,
                    episode,
                    at,
                )
            except HumanDecisionError as error:
                connection.execute("ROLLBACK TO question_carry")
                connection.execute("RELEASE question_carry")
                refused = carry_refusal_outcome(str(error))
                if refused is None:
                    raise _Transient from error
                outcome = refused
            else:
                connection.execute("RELEASE question_carry")
        record_carry_outcome(
            connection,
            key,
            answer_id=answer_id,
            review_case_id=case.review_case_id,
            episode=episode,
            outcome=outcome,
            carried_decision_id=carried,
            checked_at=at,
        )
        connection.execute("RELEASE question_check")
    except (
        _Transient,
        sqlite3.Error,
        MetadataReportError,
        ValueError,
        TypeError,
        KeyError,
    ):
        # A store refusal or a transient refusal of the carried row: no row,
        # nothing of this field kept; the retry checks it again (contract T6).
        connection.execute("ROLLBACK TO question_check")
        connection.execute("RELEASE question_check")


class _Transient(Exception):
    """The carried row was refused for a reason a retry may not meet again."""


# The book's published current case, where the record's answer of a field
# was given on another case or episode and the check has no final outcome
# there; `{scope}` narrows it.
_RETRY_CASES_SQL = (
    "SELECT DISTINCT lineage.review_case_id FROM owner_field_answers record "
    "JOIN current_review_case_lineages lineage "
    "ON lineage.connection_name=record.connection_name "
    "AND lineage.library_name=record.library_name AND lineage.item_id=record.item_id "
    "JOIN current_review_packages package ON package.connection_name=lineage.connection_name "
    "AND package.library_name=lineage.library_name AND package.item_id=lineage.item_id "
    "AND package.proposal_set_id=lineage.proposal_set_id "
    "AND package.report_id=lineage.report_id "
    "AND package.review_case_id=lineage.review_case_id "
    "AND package.checkpoint=lineage.checkpoint "
    "WHERE lineage.review_case_id IS NOT NULL AND record.destination<>'cover' "
    "AND record.pin_fingerprint IS NULL AND record.outcome<>'pin' "
    "AND NOT (record.review_case_id=lineage.review_case_id AND record.episode="
    "COALESCE((SELECT episode.decision_floor FROM review_case_episodes episode "
    "WHERE episode.review_case_id=lineage.review_case_id),0)) "
    "AND NOT EXISTS (SELECT 1 FROM carry_outcomes outcome "
    "WHERE outcome.answer_id=record.answer_id "
    "AND outcome.review_case_id=lineage.review_case_id "
    "AND outcome.episode=COALESCE((SELECT episode.decision_floor "
    "FROM review_case_episodes episode "
    "WHERE episode.review_case_id=lineage.review_case_id),0) "
    "AND outcome.outcome NOT IN ({provisional})) {scope} "
    "ORDER BY lineage.review_case_id"
)


def retry_cases(
    connection: sqlite3.Connection,
    library: tuple[str, str] | None,
    *,
    provisional_only: bool,
) -> tuple[str, ...]:
    """The published current cases the question check still owes an outcome
    on: a refused or never-run check, or, with ``provisional_only``, only a
    provisional outcome waiting for its Resume or plan (ADR-0108)."""
    provisional = ",".join(f"'{value}'" for value in sorted(PROVISIONAL_CARRY_OUTCOMES))
    scope = ""
    parameters: tuple[str, ...] = ()
    if library is not None:
        scope += "AND record.connection_name=? AND record.library_name=? "
        parameters = library
    if provisional_only:
        scope += (
            "AND EXISTS (SELECT 1 FROM carry_outcomes waiting "
            "WHERE waiting.answer_id=record.answer_id "
            "AND waiting.review_case_id=lineage.review_case_id "
            f"AND waiting.outcome IN ({provisional}))"
        )
    return tuple(
        str(row[0])
        for row in connection.execute(
            _RETRY_CASES_SQL.format(provisional=provisional, scope=scope), parameters
        )
    )


__all__ = ["CarryRecorder", "check_case_answers", "retry_cases"]
