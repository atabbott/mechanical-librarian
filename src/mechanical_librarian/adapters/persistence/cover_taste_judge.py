"""Durable taste judgements, their append-only verdicts and the judge's count.

Which cover fields are due is one statement over the currentness facts (J1);
the application confirms each against the full policy report (J2). Every
state change is one short transaction, and none is open while a request is
in flight (#1372 PR 4, J5-J7).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from typing import Literal, cast

from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.application.cover_taste_judge import (
    JudgeDue,
    TasteJudgeVersion,
)
from mechanical_librarian.domain.cover_taste import (
    TASTE_MIN_ARTS,
    TasteJudgeFailure,
    TasteProfile,
)

_TASK = "cover_taste_judge"
_DAY_SECONDS = 86_400
# The faults whose cure the judge may choose among (J1 b, Q9).
_FAULTS = (
    "wrong_shape",
    "below_floor",
    "edition_language",
    "sharper_copy",
    "unreadable",
)
# The newest verdict stored for a consideration, under any version.
_LATEST_VERDICT_SQL = (
    "(SELECT json_extract(verdict.document,'$.decision') FROM cover_taste_verdicts "
    "verdict WHERE verdict.consideration_id=consideration.consideration_id "
    "ORDER BY verdict.created_at DESC,verdict.rowid DESC LIMIT 1)"
)
_LATEST_VERDICT_AT_SQL = (
    "(SELECT verdict.created_at FROM cover_taste_verdicts verdict "
    "WHERE verdict.consideration_id=consideration.consideration_id "
    "ORDER BY verdict.created_at DESC,verdict.rowid DESC LIMIT 1)"
)
# J1: a current, ready, choosable comparison of the work's current case that
# no owner has answered, decided or held, under a fault-free keep of a
# readable cover beside another art (a) or a fault the stage would cure (b).
# J6: never attempted under this version, and not one whose newest verdict
# kept the current cover or abstained. Pending suggestions come first, oldest
# verdict first, then the rest by when each comparison was prepared.
_DUE_SQL = (
    "SELECT consideration.consideration_id,pointer.review_case_id,"
    "consideration.document,facts.format,"
    "json_extract(consideration.currentness_document,'$.cover_stage.outcome'),"
    "json_extract(consideration.currentness_document,'$.cover_stage.reason'),"
    f"{_LATEST_VERDICT_SQL} AS latest "
    "FROM current_review_packages ml "
    "JOIN current_cover_considerations pointer "
    "ON pointer.review_case_id=ml.review_case_id "
    "JOIN cover_considerations consideration "
    "ON consideration.consideration_id=pointer.consideration_id "
    "JOIN metadata_report_browse_facts facts "
    "ON facts.report_id=consideration.report_id "
    "WHERE ml.review_case_id IS NOT NULL "
    "AND json_extract(consideration.document,'$.policy_reference')=? "
    "AND ml_cover_consideration_current(consideration.currentness_document,?)=1 "
    "AND json_extract(consideration.currentness_document,'$.manual_ready')=1 "
    "AND json_extract(consideration.currentness_document,'$.provider_fetching')=0 "
    "AND typeof(facts.format)='text' "
    "AND COALESCE((SELECT result.has_owner_decision_actions "
    "FROM review_case_field_facts result "
    "WHERE result.review_case_id=ml.review_case_id "
    "AND result.destination='cover'),0)=1 "
    "AND ((json_extract(consideration.currentness_document,'$.cover_stage.outcome')"
    "='keep' AND json_extract(consideration.currentness_document,"
    "'$.cover_stage.reason') IS NULL "
    "AND json_extract(consideration.currentness_document,'$.cover_stage.arts')>=? "
    "AND EXISTS (SELECT 1 FROM json_each(consideration.document,'$.candidates') "
    "current WHERE json_extract(current.value,'$.incumbent')=1 "
    "AND json_type(current.value,'$.preview_sha256')='text' "
    "AND json_type(current.value,'$.transport_sha256')='text')) "
    "OR (json_extract(consideration.currentness_document,'$.cover_stage.outcome') "
    "IN ('replace','suggest') "
    "AND json_extract(consideration.currentness_document,'$.cover_stage.reason') "
    f"IN ({','.join(repr(fault) for fault in _FAULTS)}))) "
    "AND NOT EXISTS (SELECT 1 FROM current_cover_selection_authorities current "
    "JOIN cover_selection_authorities answered "
    "ON answered.selection_id=current.selection_id "
    "WHERE current.connection_name=ml.connection_name "
    "AND current.library_name=ml.library_name AND current.item_id=ml.item_id "
    "AND answered.kind='owner') "
    "AND NOT EXISTS (SELECT 1 FROM human_decisions decided "
    "WHERE decided.review_case_id=ml.review_case_id AND decided.destination='cover') "
    "AND NOT EXISTS (SELECT 1 FROM field_effect_manual_holds held "
    "WHERE held.review_case_id=ml.review_case_id AND held.destination='cover' "
    "AND held.active=1) "
    "AND NOT EXISTS (SELECT 1 FROM cover_taste_judgements attempted "
    "WHERE attempted.consideration_id=consideration.consideration_id "
    "AND attempted.version=?) "
    f"AND COALESCE({_LATEST_VERDICT_SQL},'choose')='choose' "
    "ORDER BY latest IS NULL,"
    f"CASE WHEN latest IS NULL THEN consideration.occurred_at "
    f"ELSE {_LATEST_VERDICT_AT_SQL} END,pointer.review_case_id LIMIT 1"
)


def _day(now: int) -> int:
    return now // _DAY_SECONDS


class SQLiteCoverTasteJudgeRepository(ConnectionScoped):
    """`authority` reads the live cover policy and replay references, so the
    due statement reads currentness exactly as Covers does."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        authority: Callable[[], tuple[str | None, str | None]],
    ) -> None:
        super().__init__(connection)
        self._authority = authority

    @local_operation
    def active(self) -> TasteJudgeVersion | None:
        row = self._connection.execute(
            "SELECT version,created_at,document FROM cover_taste_profile_versions "
            "ORDER BY version DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return TasteJudgeVersion(
            row[0], row[1], TasteProfile.from_rules(json.loads(row[2])["rules"])
        )

    @local_operation
    def building(self) -> bool:
        """Whether a taste profile build is waiting or in flight."""
        return (
            self._connection.execute(
                "SELECT 1 FROM cover_taste_builds "
                "WHERE state IN ('requested','running') LIMIT 1"
            ).fetchone()
            is not None
        )

    @local_operation
    def refusal(self, now: int, daily_limit: int) -> bool:
        """Whether today's judge requests are at the ceiling or stopped."""
        return self._refused(now, daily_limit)

    def _refused(self, now: int, daily_limit: int) -> bool:
        row = self._connection.execute(
            "SELECT requests,stopped FROM reviewer_task_requests WHERE task=? AND day=?",
            (_TASK, _day(now)),
        ).fetchone()
        return row is not None and (row[1] is not None or row[0] >= daily_limit)

    @local_operation
    def next_due(self, version: int) -> JudgeDue | None:
        """The one field to judge next under `version`, or None (J1, J6)."""
        policy, replay = self._authority()
        if policy is None or replay is None:
            return None
        row = self._connection.execute(
            _DUE_SQL, (policy, replay, TASTE_MIN_ARTS, version)
        ).fetchone()
        if row is None:
            return None
        consideration_id, case_id, document, file_format, outcome, reason, _latest = row
        return JudgeDue(
            consideration_id,
            case_id,
            cast("dict[str, object]", json.loads(document)),
            cast(str, file_format),
            cast(str, outcome),
            cast("str | None", reason),
        )

    def _insert(
        self,
        consideration_id: str,
        version: int,
        state: Literal["running", "failed", "skipped"],
        failure: TasteJudgeFailure | None,
        model: str | None,
        now: int,
    ) -> str:
        judgement_id = "taste_judgement_" + uuid.uuid4().hex
        self._connection.execute(
            "INSERT INTO cover_taste_judgements VALUES(?,?,?,'cadence',NULL,?,?,?,?,?,?)",
            (
                judgement_id,
                consideration_id,
                version,
                state,
                failure,
                model,
                _day(now),
                now,
                now,
            ),
        )
        return judgement_id

    @local_operation
    def record(
        self,
        consideration_id: str,
        version: int,
        now: int,
        failure: Literal["unavailable"] | None = None,
    ) -> None:
        """Record a field sent nothing for: `skipped` when the full report
        makes it ineligible, else `failed` with `failure`; never counted."""
        transaction = self._transaction("taste_judge_record")
        try:
            transaction.begin()
            self._insert(
                consideration_id,
                version,
                "skipped" if failure is None else "failed",
                failure,
                None,
                now,
            )
            transaction.commit()
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def claim(
        self,
        consideration_id: str,
        version: int,
        now: int,
        model: str,
        daily_limit: int,
    ) -> str | None:
        """Insert the attempt `running` and count it before dispatch (J7), or
        None at the ceiling, on a stopped day or for a pair already tried."""
        transaction = self._transaction("taste_judge_claim")
        try:
            transaction.begin()
            if self._refused(now, daily_limit):
                transaction.commit()
                return None
            try:
                judgement_id = self._insert(
                    consideration_id, version, "running", None, model, now
                )
            except sqlite3.IntegrityError:
                transaction.commit()
                return None
            self._connection.execute(
                "INSERT INTO reviewer_task_requests VALUES(?,?,1,NULL) "
                "ON CONFLICT(task,day) DO UPDATE SET requests=requests+1",
                (_TASK, _day(now)),
            )
            transaction.commit()
            return judgement_id
        except BaseException:
            transaction.rollback()
            raise

    def _stop_day(self, now: int) -> None:
        self._connection.execute(
            "INSERT INTO reviewer_task_requests VALUES(?,?,0,'unknown_charge') "
            "ON CONFLICT(task,day) DO UPDATE SET stopped='unknown_charge'",
            (_TASK, _day(now)),
        )

    @local_operation
    def fail(
        self,
        judgement_id: str,
        now: int,
        failure: TasteJudgeFailure,
        *,
        stop_day: bool = False,
    ) -> None:
        transaction = self._transaction("taste_judge_fail")
        try:
            transaction.begin()
            self._connection.execute(
                "UPDATE cover_taste_judgements SET state='failed',failure=?,"
                "updated_at=max(?,updated_at) "
                "WHERE judgement_id=? AND state IN ('requested','running')",
                (failure, now, judgement_id),
            )
            if stop_day:
                self._stop_day(now)
            transaction.commit()
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def succeed(
        self,
        judgement_id: str,
        now: int,
        document: str,
        *,
        stop_day: bool = False,
    ) -> bool:
        """Store the admitted verdict for a running judgement; False when it
        was no longer running, so nothing is stored."""
        transaction = self._transaction("taste_judge_succeed")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT consideration_id,version FROM cover_taste_judgements "
                "WHERE judgement_id=? AND state='running'",
                (judgement_id,),
            ).fetchone()
            if row is not None:
                self._connection.execute(
                    "INSERT INTO cover_taste_verdicts VALUES(?,?,?,?,?,?)",
                    (
                        "taste_verdict_" + uuid.uuid4().hex,
                        judgement_id,
                        row[0],
                        row[1],
                        document,
                        now,
                    ),
                )
                self._connection.execute(
                    "UPDATE cover_taste_judgements SET state='succeeded',"
                    "updated_at=max(?,updated_at) WHERE judgement_id=?",
                    (now, judgement_id),
                )
            if stop_day:
                self._stop_day(now)
            transaction.commit()
            return row is not None
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def interrupt_recovered(self, now: int) -> None:
        """A judgement in flight when the process stopped ends `interrupted`."""
        transaction = self._transaction("taste_judge_interrupt")
        try:
            transaction.begin()
            self._connection.execute(
                "UPDATE cover_taste_judgements SET state='failed',"
                "failure='interrupted',updated_at=max(updated_at,?) "
                "WHERE state IN ('requested','running')",
                (now,),
            )
            transaction.commit()
        except BaseException:
            transaction.rollback()
            raise


__all__ = ["SQLiteCoverTasteJudgeRepository"]
