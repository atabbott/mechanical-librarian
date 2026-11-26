"""The upgrade notice's state, read from the ledger (ADR-0108; contract S14).

The latest `derived_regenerations` row names the regeneration, its epoch and
`decision_ordinal_hwm`: an owner answer at or below that ordinal was given
before it. Each such answer's first final `carry_outcomes` row of that epoch
is its book's first new case: carried is kept, the other final outcomes are
asked again where the field has decision actions there. A record answer from
before with no final row of that epoch is not yet checked while a new case
can still come for it: its library is configured, and has not completed a
scan in which its work did not appear. The scan tables are derived, so every
scan they hold ran after the latest regeneration; a completed scan whose
discovery succeeded leaves exactly the works it listed. The one dismissal per
regeneration is `regeneration_notice_dismissals`.
"""

from __future__ import annotations

import json
import sqlite3

from mechanical_librarian.adapters.persistence.transactions import ConnectionScoped
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.regeneration_notice import (
    RegenerationNotice,
    RegenerationNoticeDismissalInvalid,
)
from mechanical_librarian.domain.question_check import PROVISIONAL_CARRY_OUTCOMES

_PROVISIONAL = ",".join(
    f"'{outcome}'" for outcome in sorted(PROVISIONAL_CARRY_OUTCOMES)
)
_LATEST_SQL = (
    "SELECT regeneration.regeneration_id,regeneration.derived_epoch_after,"
    "regeneration.finished_at,regeneration.decision_ordinal_hwm,EXISTS("
    "SELECT 1 FROM regeneration_notice_dismissals dismissal "
    "WHERE dismissal.regeneration_id=regeneration.regeneration_id) "
    "FROM derived_regenerations regeneration "
    "ORDER BY regeneration.regeneration_id DESC LIMIT 1"
)
# Each answer given before the regeneration, at its first final outcome of
# the regeneration's epoch: kept, and asked again on a field with decision
# actions (the notice counts no field there is nothing to answer on).
_FIRST_CASES_SQL = (
    "WITH earlier(decision_id) AS (SELECT decision_id FROM human_decisions "
    "WHERE ordinal<=:hwm UNION ALL SELECT decision_id "
    "FROM owner_authored_descriptions WHERE ordinal<=:hwm), "
    "checked AS (SELECT outcome.review_case_id,outcome.destination,"
    "outcome.outcome,ROW_NUMBER() OVER (PARTITION BY outcome.answer_id "
    "ORDER BY outcome.checked_at,outcome.review_case_id,outcome.episode) place "
    "FROM carry_outcomes outcome WHERE outcome.derived_epoch=:epoch "
    f"AND outcome.outcome NOT IN ({_PROVISIONAL}) "
    "AND outcome.answer_id IN (SELECT decision_id FROM earlier)) "
    "SELECT COALESCE(SUM(checked.outcome='carried'),0),"
    "COALESCE(SUM(checked.outcome IN ('question_changed','target_changed',"
    "'asked_again','invalid') AND EXISTS (SELECT 1 FROM review_case_field_facts "
    "facts WHERE facts.review_case_id=checked.review_case_id "
    "AND facts.destination=checked.destination "
    "AND facts.has_owner_decision_actions=1)),0) "
    "FROM checked WHERE checked.place=1"
)
# The record's answers from before the regeneration that the check runs on
# and no new case has settled yet, while one can still come: the library is
# configured, and no completed scan of it left the work out.
_NOT_YET_CHECKED_SQL = (
    "SELECT COUNT(*) FROM owner_field_answers record WHERE record.ordinal<=:hwm "
    "AND record.destination<>'cover' AND record.pin_fingerprint IS NULL "
    "AND record.outcome<>'pin' AND NOT EXISTS (SELECT 1 FROM carry_outcomes "
    "outcome WHERE outcome.answer_id=record.answer_id "
    f"AND outcome.derived_epoch=:epoch AND outcome.outcome NOT IN ({_PROVISIONAL})) "
    "AND EXISTS (SELECT 1 FROM json_each(:configured) configured "
    "WHERE json_extract(configured.value,'$[0]')=record.connection_name "
    "AND json_extract(configured.value,'$[1]')=record.library_name) "
    "AND NOT (EXISTS (SELECT 1 FROM review_preparation_scans scan "
    "WHERE scan.connection_name=record.connection_name "
    "AND scan.library_name=record.library_name "
    "AND scan.state IN ('completed','completed_with_failures') "
    "AND scan.discovery_failure IS NULL) "
    "AND NOT EXISTS (SELECT 1 FROM review_preparation_work work "
    "WHERE work.connection_name=record.connection_name "
    "AND work.library_name=record.library_name AND work.item_id=record.item_id))"
)


class SQLiteRegenerationNoticeStore(ConnectionScoped):
    """`RegenerationNoticeStore` over the application database."""

    def read_regeneration_notice(
        self, configured: frozenset[tuple[str, str]]
    ) -> RegenerationNotice | None:
        transaction = self._transaction("read_regeneration_notice", deferred=True)
        try:
            transaction.begin()
            latest = self._connection.execute(_LATEST_SQL).fetchone()
            notice = None
            if latest is not None:
                regeneration_id, epoch, finished_at, hwm, dismissed = latest
                bounds = {"epoch": epoch, "hwm": hwm}
                kept, asked_again = self._connection.execute(
                    _FIRST_CASES_SQL, bounds
                ).fetchone()
                (not_yet_checked,) = self._connection.execute(
                    _NOT_YET_CHECKED_SQL,
                    {**bounds, "configured": json.dumps(sorted(configured))},
                ).fetchone()
                notice = RegenerationNotice(
                    regeneration_id=regeneration_id,
                    derived_epoch=epoch,
                    finished_at=finished_at,
                    kept=kept,
                    asked_again=asked_again,
                    not_yet_checked=not_yet_checked,
                    dismissed=bool(dismissed),
                )
            transaction.commit()
            return notice
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError("regeneration notice is unavailable") from error

    def dismiss_regeneration_notice(
        self, regeneration_id: int, dismissed_at: int
    ) -> bool:
        if (
            type(regeneration_id) is not int
            or regeneration_id < 1
            or type(dismissed_at) is not int
            or dismissed_at < 0
        ):
            raise RegenerationNoticeDismissalInvalid
        transaction = self._transaction("dismiss_regeneration_notice")
        try:
            transaction.begin()
            finished = self._connection.execute(
                "SELECT finished_at FROM derived_regenerations WHERE regeneration_id=?",
                (regeneration_id,),
            ).fetchone()
            # The ledger's own rule, checked here so the write never leaves a
            # store that would not open (`regeneration_notice_dismissals`).
            if finished is None or dismissed_at < finished[0]:
                raise RegenerationNoticeDismissalInvalid
            dismissed = (
                self._connection.execute(
                    "INSERT INTO regeneration_notice_dismissals(regeneration_id,"
                    "dismissed_at) VALUES(?,?) "
                    "ON CONFLICT(regeneration_id) DO NOTHING",
                    (regeneration_id, dismissed_at),
                ).rowcount
                == 1
            )
            transaction.commit()
            return dismissed
        except MetadataReportError:
            transaction.rollback()
            raise
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError("regeneration notice is unavailable") from error


__all__ = ["SQLiteRegenerationNoticeStore"]
