"""The review web projections: one consistent snapshot over five aggregates.

Every method here owns no table. Each opens one deferred snapshot through
`_consistent_review_read` and assembles a browser-facing view from the review
case, decision, cover-consideration, field-effect suspension and preparation
aggregates, so the answer a reader gets is internally consistent even while a
writer is committing.

Splitting these out is what makes `CurrentReviewCaseStore` and
`ReviewLibraryStore` narrow enough for the composition root to satisfy without
handing the whole repository to the application layer.

Eleven seams cross the boundary and every one arrives as a callable resolved
through the composing repository at call time. That is load-bearing rather than
stylistic: five review-web tests substitute `get_review_case`,
`list_current_review_case_documents` and `list_current_attention_documents` on
the composing repository *instance* -- to interleave a competing writer at an
exact statement, or to force a snapshot rollback -- and then drive
`read_current_review_case`, `read_review_library` and `read_review_attention`.
A bound method captured at construction would bypass every one of those
substitutions and leave the tests passing while testing nothing.

`read_staged_cover_preparation` is here rather than in `review_preparation.py`
because it reads the review case and its report to answer a pipeline question;
the preparation aggregate owns the staged work rows, not this projection.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from typing import Iterator, cast

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.review_sql import (
    _CURRENT_REVIEW_FROM_SQL,
    _EPUB_INAPPLICABLE_ATTENTION_SQL,
    _LATEST_PREPARATION_WORK_SQL,
    _REVIEW_FIELD_ATTENTION_SQL,
)
from mechanical_librarian.adapters.persistence.transactions import ConnectionScoped
from mechanical_librarian.application.catalogue import TargetConnectionError
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import PipelineAction
from mechanical_librarian.application.review_web import (
    CurrentReviewCaseSnapshot,
    ReviewAttentionSnapshot,
    ReviewLibrarySnapshot,
)
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    AutomaticDecisionError,
)
from mechanical_librarian.domain.human_decision import HumanDecision, HumanDecisionError
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ReportValueError,
)
from mechanical_librarian.domain.review_browse import (
    browse_case_with_id,
    browse_report,
)
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    ReviewCaseValueError,
)
from mechanical_librarian.domain.review_field_classification import ReviewFieldCause


class SQLiteReviewProjectionRepository(ConnectionScoped):
    """Assemble the browser's review views over one consistent snapshot."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        *,
        require_staged_shadow_projection_current: Callable[[str, PipelineAction], None],
        get_review_case: Callable[[str], str],
        get_review_case_report: Callable[[str], str],
        get_review_case_decisions: Callable[[str], tuple[str, ...]],
        get_review_case_history: Callable[[str, int], tuple[dict[str, object], ...]],
        current_cover_consideration_document: Callable[[str], str | None],
        cover_consideration_history: Callable[
            [str, int], tuple[dict[str, object], ...]
        ],
        list_current_review_case_documents: Callable[
            [str | None, int, int, bool | None], tuple[tuple[str | None, str], ...]
        ],
        count_current_review_cases: Callable[[str | None], tuple[int, int]],
        list_current_attention_documents: Callable[
            [str | None, int, int],
            tuple[
                tuple[str, str, str, int, str | None, ReviewFieldCause | None],
                ...,
            ],
        ],
        count_current_attention_fields: Callable[[str | None], int],
    ) -> None:
        super().__init__(connection, binding)
        self._require_staged_shadow_projection_current = (
            require_staged_shadow_projection_current
        )
        self.get_review_case = get_review_case
        self.get_review_case_report = get_review_case_report
        self.get_review_case_decisions = get_review_case_decisions
        self.get_review_case_history = get_review_case_history
        self.current_cover_consideration_document = current_cover_consideration_document
        self.cover_consideration_history = cover_consideration_history
        self.list_current_review_case_documents = list_current_review_case_documents
        self.count_current_review_cases = count_current_review_cases
        self.list_current_attention_documents = list_current_attention_documents
        self.count_current_attention_fields = count_current_attention_fields

    @contextmanager
    def _consistent_review_read(self) -> Iterator[None]:
        """Keep one review projection on one authoritative SQLite snapshot."""
        transaction = self._transaction("consistent_review_read", deferred=True)
        try:
            if self._binding is not None:
                self._binding.require_current()
            transaction.begin()
            yield
            transaction.commit()
            if self._binding is not None:
                self._binding.require_current()
        except TargetConnectionError:
            transaction.rollback()
            raise
        except MetadataReportError:
            transaction.rollback()
            raise
        except Exception as error:
            transaction.rollback()
            raise MetadataReportError("review snapshot is unavailable") from error

    def read_staged_cover_preparation(
        self, action: PipelineAction
    ) -> tuple[str, str, bool] | None:
        """Read only the claimed staged cover facts before F22 package cutover."""
        if action.review_case_id is None:
            raise MetadataReportError("review case is unavailable")
        try:
            self._require_staged_shadow_projection_current(
                action.review_case_id, action
            )
            row = self._connection.execute(
                "SELECT review.document,report.document FROM review_cases review "
                "JOIN metadata_reports report ON report.report_id=review.report_id "
                "WHERE review.review_case_id=? AND review.report_id=?",
                (action.review_case_id, action.report_id),
            ).fetchone()
            if row is None or len(row) != 2:
                raise MetadataReportError("review case is unavailable")
            case = ReviewCase.from_json(row[0])
            report = MetadataReport.from_json(row[1])
            if (
                case.review_case_id != action.review_case_id
                or case.report_id != report.report_id
            ):
                raise MetadataReportError("review case is unavailable")
            cover = next(
                (
                    proposal
                    for proposal in case.proposals
                    if proposal["destination"] == "cover"
                ),
                None,
            )
            # Every ReviewCase proposal carries a "result" object; the
            # surrounding except clause turns any drift into MetadataReportError.
            if (
                cover is None
                or cast("dict[str, object]", cover["result"])["outcome"] == "ineligible"
            ):
                return None
            return report.to_json(), str(cover["proposal_id"]), False
        except (
            sqlite3.Error,
            ReviewCaseValueError,
            ReportValueError,
            KeyError,
            TypeError,
        ) as error:
            raise MetadataReportError("review case is unavailable") from error

    def read_current_review_case(
        self, review_case_id: str, history_limit: int | None
    ) -> CurrentReviewCaseSnapshot:
        if history_limit is not None and (
            type(history_limit) is not int or not 1 <= history_limit <= 100
        ):
            raise MetadataReportError("review case snapshot is unavailable")
        with self._consistent_review_read():
            snapshot = CurrentReviewCaseSnapshot(
                case_document=self.get_review_case(review_case_id),
                report_document=self.get_review_case_report(review_case_id),
                decision_documents=self.get_review_case_decisions(review_case_id),
                history=(
                    ()
                    if history_limit is None
                    else self.get_review_case_history(review_case_id, history_limit)
                ),
                automatic_attention_destinations=self._automatic_attention_destinations(
                    review_case_id
                ),
                automatic_safety_suspended_destinations=self._automatic_attention_destinations(
                    review_case_id
                ),
                shadow_abstention_destinations=self._shadow_abstention_destinations(
                    review_case_id
                ),
                override_blocked_destinations=self._override_blocked_destinations(
                    review_case_id
                ),
                suspended_destinations=self._field_effect_capability_suspended_destinations(
                    review_case_id
                ),
                suspension_causes=self._field_effect_capability_suspension_causes(
                    review_case_id
                ),
                cover_consideration_document=(
                    self.current_cover_consideration_document(review_case_id)
                ),
                cover_history=(
                    ()
                    if history_limit is None
                    else self.cover_consideration_history(review_case_id, history_limit)
                ),
            )
        return snapshot

    def read_review_library(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> ReviewLibrarySnapshot:
        with self._consistent_review_read():
            rows = self.list_current_review_case_documents(
                search, limit, offset, attention
            )
            projected_rows: list[
                tuple[
                    str | None,
                    str,
                    tuple[str, ...],
                    tuple[str, ...],
                    tuple[str, ...],
                    tuple[str, ...],
                    str | None,
                    int,
                ]
            ] = []
            for case_document, report_document in rows:
                if (
                    case_document is None
                    and '"schema_version":"review-terminal-work-v1"' in report_document
                ):
                    projected_rows.append(
                        (case_document, report_document, (), (), (), (), None, 0)
                    )
                    continue
                report = browse_report(report_document)
                target = report["target"]
                if type(target) is not dict:
                    raise MetadataReportError("review library is unavailable")
                terminal = self._connection.execute(
                    _LATEST_PREPARATION_WORK_SQL,
                    (
                        target["connection"],
                        target["library"],
                        target["item"],
                    ),
                ).fetchone()
                terminal_document = (
                    None
                    if (
                        terminal is None
                        or terminal[4] != "failed"
                        or terminal[5] is None
                    )
                    else _canonical_json(
                        {
                            "attempt": terminal[2],
                            "connection": target["connection"],
                            "failure_root": terminal[6],
                            "item": target["item"],
                            "lane": terminal[1],
                            "library": target["library"],
                            "recovery_attempt": terminal[3],
                            "schema_version": "review-terminal-work-v1",
                            "status": terminal[5],
                            "work_id": terminal[0],
                        }
                    )
                )
                case_with_id = (
                    None
                    if case_document is None
                    else browse_case_with_id(case_document)
                )
                case = None if case_with_id is None else case_with_id[0]
                case_id = None if case_with_id is None else case_with_id[1]
                projected_rows.append(
                    (
                        case_document,
                        report_document,
                        ()
                        if case is None
                        else self._browse_decision_documents(
                            cast("str", case_id), case
                        ),
                        ()
                        if case_id is None
                        else self._automatic_attention_destinations(case_id),
                        ()
                        if case_id is None
                        else self._field_effect_capability_suspended_destinations(
                            case_id
                        ),
                        ()
                        if case_id is None
                        else self._shadow_abstention_destinations(case_id),
                        terminal_document,
                        0
                        if case_id is None
                        else self._current_attention_count(case_id),
                    )
                )
            projected = tuple(projected_rows)
            total, attention_total = self.count_current_review_cases(search)
            snapshot = ReviewLibrarySnapshot(
                rows=projected,
                total=total,
                attention_total=attention_total,
            )
        return snapshot

    def _browse_decision_documents(
        self, review_case_id: str, case: dict[str, object]
    ) -> tuple[str, ...]:
        """Validate only the decision documents attached to one visible row."""
        human_rows = self._connection.execute(
            "SELECT decision_id,proposal_id,target_fingerprint,destination,"
            "actor,outcome,selected_value_document,idempotency_digest,"
            "request_digest,occurred_at,document FROM human_decisions "
            "WHERE review_case_id=? ORDER BY ordinal",
            (review_case_id,),
        ).fetchall()
        documents: list[str] = []
        try:
            for row in human_rows:
                if len(row) != 11 or type(row[10]) is not str:
                    raise MetadataReportError("review library is unavailable")
                decision = HumanDecision.from_json(row[10])
                decision.validate_for_browse_case(review_case_id, case)
                if (
                    decision.decision_id,
                    decision.proposal_id,
                    decision.target_fingerprint,
                    decision.destination,
                    decision.actor,
                    decision.outcome,
                    _canonical_json(decision.selected_value),
                    decision.idempotency_digest,
                    decision.request_digest,
                    decision.occurred_at,
                ) != row[:10]:
                    raise MetadataReportError("review library is unavailable")
                documents.append(decision.to_json())
            automatic_rows = self._connection.execute(
                "SELECT decision_id,destination,idempotency_digest,occurred_at,document "
                "FROM automatic_decisions WHERE review_case_id=? "
                "ORDER BY occurred_at,decision_id",
                (review_case_id,),
            ).fetchall()
            for row in automatic_rows:
                if len(row) != 5 or type(row[4]) is not str:
                    raise MetadataReportError("review library is unavailable")
                automatic_decision = AutomaticDecision.from_json(row[4])
                if (
                    automatic_decision.decision_id,
                    automatic_decision.destination,
                    automatic_decision.idempotency_digest,
                    automatic_decision.occurred_at,
                ) != row[:4] or automatic_decision.review_case_id != review_case_id:
                    raise MetadataReportError("review library is unavailable")
                documents.append(automatic_decision.to_json())
        except (HumanDecisionError, AutomaticDecisionError, TypeError) as error:
            raise MetadataReportError("review library is unavailable") from error
        return tuple(documents)

    def _current_attention_count(self, review_case_id: str) -> int:
        """Use the same persisted field-membership predicate as every attention view."""
        row = self._connection.execute(
            "SELECT count(*) "
            + _CURRENT_REVIEW_FROM_SQL
            + " JOIN review_case_field_facts result "
            "ON result.review_case_id=c.review_case_id "
            "WHERE result.review_case_id=? AND "
            + _REVIEW_FIELD_ATTENTION_SQL
            + _EPUB_INAPPLICABLE_ATTENTION_SQL,
            (review_case_id,),
        ).fetchone()
        if row is None or len(row) != 1 or type(row[0]) is not int or row[0] < 0:
            raise MetadataReportError("review library is unavailable")
        return row[0]

    def _automatic_attention_destinations(self, review_case_id: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT DISTINCT outcome.destination FROM shadow_outcome_field_facts outcome, "
            "automatic_safety_state safety WHERE outcome.review_case_id=? "
            "AND outcome.automatic_destination=1 AND (safety.paused=1 OR "
            "safety.global_suspension IS NOT NULL OR EXISTS(SELECT 1 FROM "
            "json_each(safety.category_suspensions_document) category WHERE "
            "json_extract(category.value,'$[0]')=outcome.policy_category)) ORDER BY 1",
            (review_case_id,),
        ).fetchall()
        if any(type(row[0]) is not str for row in rows):
            raise MetadataReportError("review attention is unavailable")
        return tuple(row[0] for row in rows)

    def _shadow_abstention_destinations(self, review_case_id: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT DISTINCT outcome.destination FROM shadow_outcome_field_facts outcome "
            "WHERE outcome.review_case_id=? AND outcome.disposition='abstention' ORDER BY 1",
            (review_case_id,),
        ).fetchall()
        if any(type(row[0]) is not str for row in rows):
            raise MetadataReportError("review attention is unavailable")
        return tuple(row[0] for row in rows)

    def _override_blocked_destinations(
        self,
        review_case_id: str,
    ) -> tuple[str, ...]:
        # An owner's own recorded decision is superseded by the next one. Only
        # an in-flight effect blocks scalar replacement; verified cover effects
        # remain closed because their incumbent artifact is not a new selectable
        # target. `record_human_decision` enforces this exact boundary on write.
        rows = self._connection.execute(
            "SELECT DISTINCT recorded.destination FROM ("
            "SELECT destination,decision_id FROM automatic_decisions WHERE review_case_id=? "
            "UNION ALL "
            "SELECT destination,decision_id FROM human_decisions WHERE review_case_id=?"
            ") recorded JOIN field_effects effect ON effect.decision_id=recorded.decision_id "
            "WHERE effect.state IN ('claimed','applying','reconciling') OR "
            "(effect.state='verified' AND recorded.destination='cover') "
            "ORDER BY recorded.destination",
            (review_case_id, review_case_id),
        ).fetchall()
        if any(type(row[0]) is not str for row in rows):
            raise MetadataReportError("review attention is unavailable")
        return tuple(row[0] for row in rows)

    def _field_effect_capability_suspended_destinations(
        self,
        review_case_id: str,
    ) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT result.destination FROM current_review_case_lineages c "
            "JOIN review_case_field_facts result "
            "ON result.review_case_id=c.review_case_id "
            "WHERE result.review_case_id=? AND EXISTS(SELECT 1 "
            "FROM field_effect_suspensions suspension "
            "WHERE suspension.connection_name=c.connection_name AND ("
            "(suspension.scope='connection' AND suspension.destination='') OR "
            "(suspension.scope='destination' AND suspension.destination="
            "result.destination))) ORDER BY 1",
            (review_case_id,),
        ).fetchall()
        if any(type(row[0]) is not str for row in rows):
            raise MetadataReportError("review attention is unavailable")
        return tuple(row[0] for row in rows)

    def _field_effect_capability_suspension_causes(
        self,
        review_case_id: str,
    ) -> tuple[tuple[str, ReviewFieldCause], ...]:
        rows = self._connection.execute(
            "SELECT result.destination,(SELECT "
            "suspension.scope FROM field_effect_suspensions suspension "
            "WHERE suspension.connection_name=c.connection_name AND (("
            "suspension.scope='connection' AND suspension.destination='') OR "
            "(suspension.scope='destination' AND suspension.destination="
            "result.destination)) ORDER BY CASE "
            "suspension.scope WHEN 'connection' THEN 0 ELSE 1 END LIMIT 1) "
            "FROM current_review_case_lineages c JOIN review_case_field_facts result "
            "ON result.review_case_id=c.review_case_id "
            "WHERE result.review_case_id=? AND EXISTS(SELECT 1 FROM "
            "field_effect_suspensions suspension WHERE "
            "suspension.connection_name=c.connection_name AND (("
            "suspension.scope='connection' AND suspension.destination='') OR "
            "(suspension.scope='destination' AND suspension.destination="
            "result.destination))) ORDER BY 1",
            (review_case_id,),
        ).fetchall()
        causes: dict[str, ReviewFieldCause] = {
            "connection": "external_connection_failure",
            "destination": "external_field_failure",
        }
        if any(
            type(destination) is not str or scope not in causes
            for destination, scope in rows
        ):
            raise MetadataReportError("review attention is unavailable")
        return tuple((destination, causes[scope]) for destination, scope in rows)

    def read_review_attention(
        self, search: str | None, limit: int, offset: int
    ) -> ReviewAttentionSnapshot:
        with self._consistent_review_read():
            snapshot = ReviewAttentionSnapshot(
                rows=self.list_current_attention_documents(search, limit, offset),
                total=self.count_current_attention_fields(search),
            )
        return snapshot
