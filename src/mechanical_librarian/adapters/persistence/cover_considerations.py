"""The cover-consideration aggregate.

Owns the `cover_considerations` table, its `current_cover_considerations`
pointer, and the append-only `cover_consideration_events` history: the
candidate covers prepared for one review case, which one is current, and every
preparation, supersession, staleness and decision outcome recorded against it.

The lineage joins that keep a consideration bound to a still-current review
case travel with the methods that need them, over the shared connection.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.transactions import ConnectionScoped
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.field_effect import FieldEffectOperation
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
)


class SQLiteCoverConsiderationRepository(ConnectionScoped):
    """Persist, read and retire the cover considerations of one review case."""

    def _retire_stale_cover_consideration(
        self,
        operation: FieldEffectOperation,
        state: str,
        changed_at: int,
    ) -> None:
        if state != "stale" or operation.plan.destination != "cover":
            return
        row = self._connection.execute(
            "SELECT event.consideration_id,event.review_case_id "
            "FROM cover_consideration_events event "
            "JOIN current_cover_considerations current "
            "ON current.consideration_id=event.consideration_id "
            "WHERE event.decision_id=? AND event.kind='selected' "
            "AND current.review_case_id=event.review_case_id",
            (operation.plan.authority.decision_id,),
        ).fetchone()
        if row is None:
            return
        consideration_id, review_case_id = str(row[0]), str(row[1])
        self._record_cover_consideration_event(
            consideration_id,
            review_case_id,
            "stale",
            changed_at,
            operation.plan.authority.decision_id,
        )

    def retire_stale_cover_decision(
        self, decision_id: str, changed_at: int
    ) -> bool:
        """Retire a selected cover whose case ceased to be current before planning."""
        with self._connection:
            row = self._connection.execute(
                "SELECT event.consideration_id,event.review_case_id "
                "FROM cover_consideration_events event "
                "JOIN current_cover_considerations current "
                "ON current.consideration_id=event.consideration_id "
                "WHERE event.decision_id=? AND event.kind='selected' "
                "AND current.review_case_id=event.review_case_id "
                "AND NOT EXISTS (SELECT 1 FROM cover_consideration_events stale "
                "WHERE stale.consideration_id=event.consideration_id "
                "AND stale.kind='stale' AND stale.decision_id=event.decision_id)",
                (decision_id,),
            ).fetchone()
            if row is None:
                return False
            self._record_cover_consideration_event(
                str(row[0]), str(row[1]), "stale", changed_at, decision_id
            )
        return True

    def current_cover_consideration(
        self, review_case_id: str, replay_digest: str
    ) -> str | None:
        row = self._connection.execute(
            "SELECT consideration.document FROM current_cover_considerations current "
            "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
            "JOIN current_review_case_lineages lineage ON lineage.review_case_id=current.review_case_id "
            "WHERE current.review_case_id=? AND consideration.replay_digest=? "
            "AND NOT EXISTS (SELECT 1 FROM cover_consideration_events event "
            "WHERE event.consideration_id=consideration.consideration_id "
            "AND event.kind='stale')",
            (review_case_id, replay_digest),
        ).fetchone()
        return None if row is None else str(row[0])

    def current_cover_consideration_document(
        self, review_case_id: str
    ) -> str | None:
        row = self._connection.execute(
            "SELECT consideration.document FROM current_cover_considerations current "
            "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
            "JOIN current_review_case_lineages lineage ON lineage.review_case_id=current.review_case_id "
            "WHERE current.review_case_id=? "
            "AND NOT EXISTS (SELECT 1 FROM cover_consideration_events event "
            "WHERE event.consideration_id=consideration.consideration_id "
            "AND event.kind='stale')",
            (review_case_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def latest_cover_consideration_document(
        self, review_case_id: str
    ) -> str | None:
        row = self._connection.execute(
            "SELECT consideration.document FROM current_cover_considerations current "
            "JOIN cover_considerations consideration "
            "ON consideration.consideration_id=current.consideration_id "
            "JOIN current_review_case_lineages lineage "
            "ON lineage.review_case_id=current.review_case_id "
            "WHERE current.review_case_id=?",
            (review_case_id,),
        ).fetchone()
        return None if row is None else str(row[0])

    def _record_cover_consideration_event(
        self,
        consideration_id: str,
        review_case_id: str,
        kind: str,
        occurred_at: int,
        decision_id: str | None = None,
    ) -> None:
        if decision_id is not None and self._connection.execute(
            "SELECT 1 FROM cover_consideration_events WHERE consideration_id=? AND kind=? AND decision_id=?",
            (consideration_id, kind, decision_id),
        ).fetchone() == (1,):
            return
        document = _canonical_json(
            {
                "consideration_id": consideration_id,
                "decision_id": decision_id,
                "kind": kind,
                "occurred_at": occurred_at,
                "review_case_id": review_case_id,
                "schema_version": "cover-consideration-event-v1",
            }
        )
        event_id = "cover_consideration_event_" + hashlib.sha256(
            document.encode("utf-8")
        ).hexdigest()
        self._connection.execute(
            "INSERT OR IGNORE INTO cover_consideration_events(event_id,consideration_id,review_case_id,kind,decision_id,occurred_at,document) VALUES(?,?,?,?,?,?,?)",
            (
                event_id,
                consideration_id,
                review_case_id,
                kind,
                decision_id,
                occurred_at,
                document,
            ),
        )

    def atomic_cover_preparation(self, call: Callable[[], object]) -> object:
        """Serialize staged final-currentness and the durable consideration write."""
        if self._connection.in_transaction:
            return call()
        transaction = self._transaction("atomic_cover_preparation")
        try:
            transaction.begin()
            result = call()
            transaction.commit()
            return result
        except Exception:
            transaction.rollback()
            raise

    def record_cover_consideration(
        self,
        document: str,
        *,
        review_case_id: str,
        report_id: str,
        replay_digest: str,
        supersedes_id: str | None,
        occurred_at: int,
        preparation_kind: str = "automatic_prepared",
    ) -> str:
        try:
            value = json.loads(document)
            if (
                type(value) is not dict
                or value.get("schema_version") != "cover-consideration-v1"
                or value.get("review_case_id") != review_case_id
                or value.get("report_id") != report_id
                or value.get("replay_digest") != replay_digest
                or value.get("supersedes_id") != supersedes_id
                or _canonical_json(value) != document
            ):
                raise ValueError
            consideration_id = value["consideration_id"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise MetadataReportError("cover consideration is invalid") from error
        if preparation_kind not in {"automatic_prepared", "owner_retry_prepared"}:
            raise MetadataReportError("cover preparation kind is invalid")
        transaction = self._transaction("record_cover_consideration")
        try:
            transaction.begin()
            existing = self._connection.execute(
                "SELECT document FROM cover_considerations WHERE consideration_id=?",
                (consideration_id,),
            ).fetchone()
            if existing is not None:
                if existing != (document,):
                    raise MetadataReportError("cover consideration disagrees")
                transaction.commit()
                return document
            current = self._connection.execute(
                "SELECT consideration_id FROM current_cover_considerations WHERE review_case_id=?",
                (review_case_id,),
            ).fetchone()
            actual_supersedes = None if current is None else current[0]
            if actual_supersedes != supersedes_id:
                raise MetadataReportError("cover consideration changed")
            self._connection.execute(
                "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?)",
                (
                    consideration_id,
                    review_case_id,
                    report_id,
                    replay_digest,
                    hashlib.sha256(document.encode("utf-8")).hexdigest(),
                    supersedes_id,
                    occurred_at,
                    document,
                ),
            )
            if supersedes_id is not None:
                self._record_cover_consideration_event(
                    supersedes_id, review_case_id, "superseded", occurred_at
                )
            self._connection.execute(
                "INSERT INTO current_cover_considerations(review_case_id,consideration_id) VALUES(?,?) "
                "ON CONFLICT(review_case_id) DO UPDATE SET consideration_id=excluded.consideration_id",
                (review_case_id, consideration_id),
            )
            self._record_cover_consideration_event(
                consideration_id, review_case_id, preparation_kind, occurred_at
            )
            transaction.commit()
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError("cover consideration is unavailable") from error
        except MetadataReportError:
            transaction.rollback()
            raise
        return document

    def record_cover_consideration_outcome(
        self,
        consideration_id: str,
        kind: str,
        occurred_at: int,
        decision_id: str | None = None,
    ) -> None:
        row = self._connection.execute(
            "SELECT review_case_id FROM cover_considerations WHERE consideration_id=?",
            (consideration_id,),
        ).fetchone()
        if row is None:
            raise MetadataReportError("cover consideration is unavailable")
        with self._connection:
            self._record_cover_consideration_event(
                consideration_id, str(row[0]), kind, occurred_at, decision_id
            )

    def cover_consideration_history(
        self, review_case_id: str, limit: int = 50
    ) -> tuple[dict[str, object], ...]:
        rows = self._connection.execute(
            "SELECT event.document,consideration.document FROM "
            "cover_consideration_events event JOIN cover_considerations consideration "
            "ON consideration.consideration_id=event.consideration_id "
            "WHERE event.review_case_id=? ORDER BY event.occurred_at DESC,event.event_id DESC LIMIT ?",
            (review_case_id, min(max(limit, 1), 50)),
        ).fetchall()
        items = []
        for event_document, consideration_document in rows:
            event = json.loads(event_document)
            event["consideration_document"] = consideration_document
            items.append(event)
        return tuple(items)

    def _record_cover_decision_outcome(
        self,
        decision: HumanDecision,
        consideration_id: str | None,
    ) -> None:
        if decision.destination != "cover":
            if consideration_id is not None:
                raise HumanDecisionError("invalid_choice")
            return
        if consideration_id is None:
            raise HumanDecisionError("stale")
        kind = {
            "accept": "selected",
            "keep_current": "kept_incumbent",
            "reject": "no_action",
        }.get(decision.outcome)
        if kind is None:
            raise HumanDecisionError("invalid_choice")
        existing = self._connection.execute(
            "SELECT consideration_id,kind FROM cover_consideration_events "
            "WHERE decision_id=? AND kind IN "
            "('selected','kept_incumbent','no_action')",
            (decision.decision_id,),
        ).fetchone()
        if existing is not None:
            if existing != (consideration_id, kind):
                raise HumanDecisionError("idempotency_conflict")
            return
        row = self._connection.execute(
            "SELECT consideration.document FROM current_cover_considerations current "
            "JOIN cover_considerations consideration "
            "ON consideration.consideration_id=current.consideration_id "
            "WHERE current.review_case_id=? AND current.consideration_id=?",
            (decision.review_case_id, consideration_id),
        ).fetchone()
        if row is None:
            raise HumanDecisionError("stale")
        try:
            consideration = json.loads(row[0])
            if (
                consideration["proposal_id"] != decision.proposal_id
                or consideration["target_fingerprint"] != decision.target_fingerprint
            ):
                raise HumanDecisionError("stale")
            if decision.outcome == "accept":
                selected = next(
                    (
                        candidate
                        for candidate in consideration["candidates"]
                        if decision.selected_value
                        == {"kind": "cover", "value": candidate["artifact_id"]}
                    ),
                    None,
                )
                if (
                    selected is None
                    or selected["incumbent"] is True
                    or selected["selectable"] is not True
                ):
                    raise HumanDecisionError("invalid_choice")
            elif decision.selected_value is not None:
                raise HumanDecisionError("invalid_choice")
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise MetadataReportError("cover consideration is unavailable") from error
        self._record_cover_consideration_event(
            consideration_id,
            decision.review_case_id,
            kind,
            decision.occurred_at,
            decision.decision_id,
        )


__all__ = ["SQLiteCoverConsiderationRepository"]
