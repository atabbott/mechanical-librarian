"""The review web projections: one consistent snapshot over six aggregates.

Every method here owns no table. Each opens one deferred snapshot through
`_consistent_review_read` and assembles a browser-facing view from the review
case, decision, cover-consideration, field-effect suspension, preparation and
metadata-policy aggregates, so the answer a reader gets is internally consistent even while a
writer is committing.

Splitting these out is what makes `CurrentReviewCaseStore` and
`ReviewLibraryStore` narrow enough for the composition root to satisfy without
handing the whole repository to the application layer.

Every seam crosses the boundary as a callable resolved
through the composing repository at call time. That is load-bearing rather than
stylistic: five review-web tests substitute `get_review_case`,
`read_current_library_page` and `read_current_attention_page` on
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

import json
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from typing import Iterator, cast

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.decisions import (
    _automatic_decision_documents,
)
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.field_effect_validation import (
    _automatic_decision_value,
)
from mechanical_librarian.adapters.persistence.review_cases import (
    ReviewAttentionPage,
    ReviewLibraryPage,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _ATTENTION_FACT_CURRENT_SQL,
    _LATEST_PREPARATION_WORK_SQL,
    _POLICY_ASK_ATTENTION_SQL,
)
from mechanical_librarian.adapters.persistence.schema import (
    _FIELD_EFFECT_COMPANION_DECISION_SQL,
)
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.application.catalogue import TargetConnectionError
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import PipelineAction
from mechanical_librarian.application.review_web import (
    BROWSE_SCOPE_REUSE,
    CurrentReviewCaseSnapshot,
    LibraryLabels,
    ReviewAttentionSnapshot,
    ReviewLibrarySnapshot,
    ValidatedDocumentCache,
)
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import FieldEffectError
from mechanical_librarian.domain.human_decision import (
    DecisionScope,
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.domain.metadata_policy import ResolvedMetadataPolicy
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ReportValueError,
)
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.review_case import (
    ReviewCase,
    ReviewCaseValueError,
)
from mechanical_librarian.domain.review_field_classification import ReviewFieldCause
from mechanical_librarian.domain.shadow_outcome import ShadowOutcomeSet


class SQLiteReviewProjectionRepository(ConnectionScoped):
    """Assemble the browser's review views over one consistent snapshot."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        *,
        require_staged_shadow_projection_current: Callable[[str, PipelineAction], None],
        get_review_case: Callable[[str], str],
        get_review_case_source_report: Callable[[str], MetadataReport],
        get_review_case_decisions: Callable[[str, str | None], tuple[str, ...]],
        get_review_case_history: Callable[[str, int], tuple[dict[str, object], ...]],
        description_advice: Callable[[str], dict[str, object] | None],
        current_cover_consideration_document: Callable[[str], str | None],
        cover_consideration_history: Callable[
            [str, int], tuple[dict[str, object], ...]
        ],
        read_current_library_page: Callable[
            [str | None, int, int, bool | None, LibraryLabels], ReviewLibraryPage
        ],
        read_current_attention_page: Callable[
            [str | None, int, int, LibraryLabels], ReviewAttentionPage
        ],
        current_cover_authority: Callable[[], tuple[str | None, str | None]],
        current_resolved_metadata_policy: Callable[
            [str], ResolvedMetadataPolicy | None
        ],
        previous_field_values: Callable[
            [str, str, str], tuple[tuple[str, object], ...]
        ],
    ) -> None:
        super().__init__(connection, binding)
        self._require_staged_shadow_projection_current = (
            require_staged_shadow_projection_current
        )
        self.description_advice = description_advice
        self.get_review_case = get_review_case
        self.get_review_case_source_report = get_review_case_source_report
        self.get_review_case_decisions = get_review_case_decisions
        self.get_review_case_history = get_review_case_history
        self.current_cover_consideration_document = current_cover_consideration_document
        self.cover_consideration_history = cover_consideration_history
        self.read_current_library_page = read_current_library_page
        self.read_current_attention_page = read_current_attention_page
        self._current_cover_authority = current_cover_authority
        self.current_resolved_metadata_policy = current_resolved_metadata_policy
        self._previous_field_values = previous_field_values

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

    @local_operation
    def read_staged_description_preparation(
        self, action: PipelineAction
    ) -> tuple[str, str]:
        if action.review_case_id is None:
            raise MetadataReportError("review case is unavailable")
        self._require_staged_shadow_projection_current(action.review_case_id, action)
        row = self._connection.execute(
            "SELECT c.document,r.document FROM review_cases c JOIN metadata_reports r ON r.report_id=c.report_id WHERE c.review_case_id=? AND c.report_id=?",
            (action.review_case_id, action.report_id),
        ).fetchone()
        if row is None:
            raise MetadataReportError("review case is unavailable")
        return str(row[0]), str(row[1])

    @local_operation
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
            case_document = self.get_review_case(review_case_id)
            report = self.get_review_case_source_report(review_case_id)
            policy = self.current_resolved_metadata_policy(report.target.library)
            # The same read of the same case: its decisions are proved
            # against the document validated above, not a second read (#1202).
            decision_documents = self.get_review_case_decisions(
                review_case_id, case_document
            )
            source_documents = self._automatic_source_outcome_documents(review_case_id)
            snapshot = CurrentReviewCaseSnapshot(
                description_advice=self.description_advice(review_case_id),
                case_document=case_document,
                report_document=report.to_json(),
                report=report,
                resolved_policy_document=None
                if policy is None
                else policy.canonical_json,
                decision_documents=decision_documents,
                automatic_source_outcome_documents=source_documents,
                final_outcome_document=self._latest_final_outcome_document(
                    case_document, policy
                ),
                automatic_selected_values=self._automatic_selected_values(
                    case_document, report, decision_documents, source_documents
                ),
                history=(
                    ()
                    if history_limit is None
                    else self.get_review_case_history(review_case_id, history_limit)
                ),
                policy_attention_destinations=self._policy_attention_destinations(
                    review_case_id
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
                current_cover_selection=next(
                    (
                        row
                        for row in self._connection.execute(
                            "SELECT current.selection_id,selection.kind,json_extract(selection.document,'$.candidate_artifact_id') FROM current_cover_selection_authorities current JOIN cover_selection_authorities selection ON selection.selection_id=current.selection_id JOIN current_review_case_lineages lineage ON lineage.connection_name=current.connection_name AND lineage.library_name=current.library_name AND lineage.item_id=current.item_id WHERE lineage.review_case_id=?",
                            (review_case_id,),
                        )
                    ),
                    None,
                ),
                current_cover_automatic_decision_id=(
                    self._current_cover_automatic_decision_id(review_case_id)
                ),
                decision_effect_states=self._decision_effect_states(review_case_id),
                held_destinations=tuple(
                    row[0]
                    for row in self._connection.execute(
                        "SELECT destination FROM field_effect_manual_holds "
                        "WHERE review_case_id=? AND active=1 ORDER BY destination",
                        (review_case_id,),
                    )
                ),
                previous_values=self._previous_field_values(
                    report.target.connection,
                    report.target.library,
                    report.target.item,
                ),
            )
        return snapshot

    def _decision_effect_states(
        self, review_case_id: str
    ) -> tuple[tuple[str, str], ...]:
        """Whether each latest owner decision's write is in flight or written."""
        rows = self._connection.execute(
            "SELECT human.destination,MAX(CASE WHEN effect.state IN "
            "('claimed','applying','reconciling') THEN 2 WHEN effect.state='verified' "
            "THEN 1 ELSE 0 END) FROM human_decisions human JOIN field_effects effect "
            "ON effect.rowid IN (SELECT rowid FROM field_effects WHERE "
            "decision_id=human.decision_id UNION ALL SELECT rowid FROM field_effects "
            "WHERE " + _FIELD_EFFECT_COMPANION_DECISION_SQL + "=human.decision_id) "
            "WHERE human.review_case_id=? AND human.ordinal=(SELECT MAX(latest.ordinal) "
            "FROM human_decisions latest WHERE latest.review_case_id=human.review_case_id "
            "AND latest.destination=human.destination) GROUP BY human.destination "
            "ORDER BY human.destination",
            (review_case_id,),
        ).fetchall()
        return tuple(
            (row[0], "in_progress" if row[1] == 2 else "written")
            for row in rows
            if row[1] in {1, 2}
        )

    def _latest_final_outcome_document(
        self, case_document: str, policy: ResolvedMetadataPolicy | None
    ) -> str | None:
        """Read retained explanation independently of effect-bearing decisions."""
        if policy is None:
            return None
        case = ReviewCase.create_from_data(json.loads(case_document)["case"])
        row = self._connection.execute(
            "SELECT o.document,o.outcome_set_id,o.source_decision_set_id,s.generation "
            "FROM shadow_decision_outcomes o CROSS JOIN metadata_policy_state s "
            "WHERE s.singleton=1 AND o.review_case_id=? AND o.report_id=? "
            "AND o.proposal_set_id=? AND o.resolved_policy_id=? "
            "AND json_extract(o.document,'$.policy_generation')=s.generation "
            "ORDER BY o.evaluated_at DESC,o.outcome_set_id DESC LIMIT 1",
            (
                case.review_case_id,
                case.report_id,
                case.proposal_set_id,
                policy.resolved_id,
            ),
        ).fetchone()
        if row is None:
            return None
        outcome = ShadowOutcomeSet.from_json(row[0])
        if (
            outcome.outcome_set_id != row[1]
            or outcome.source_decision_set_id != row[2]
            or outcome.policy_generation != row[3]
            or outcome.review_case_id != case.review_case_id
            or outcome.report_id != case.report_id
            or outcome.proposal_set_id != case.proposal_set_id
            or outcome.target_fingerprint != case.target_fingerprint
            or outcome.resolved_policy_id != policy.resolved_id
            or outcome.policy_profile_id != policy.profile_id
        ):
            raise MetadataReportError("review case snapshot is unavailable")
        return outcome.to_json()

    def _automatic_selected_values(
        self,
        case_document: str,
        report: MetadataReport,
        decision_documents: tuple[str, ...],
        source_documents: tuple[str, ...],
    ) -> tuple[tuple[str, dict[str, object]], ...]:
        """Read the same exact value as effect authority within this snapshot."""
        decisions = tuple(
            AutomaticDecision.from_json(document)
            for document in decision_documents
            if json.loads(document).get("schema_version") == "automatic-decision-v1"
            and json.loads(document).get("outcome") == "candidate"
        )
        if not decisions:
            return ()
        case = ReviewCase.create_from_data(json.loads(case_document)["case"])
        row = self._connection.execute(
            "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
            (case.proposal_set_id,),
        ).fetchone()
        if row is None:
            raise MetadataReportError("review case snapshot is unavailable")
        proposal = ProposalSet.from_json(row[0])
        sources = {
            field.outcome_id: source
            for document in source_documents
            for source in (ShadowOutcomeSet.from_json(document),)
            for field in source.outcomes
        }
        values: list[tuple[str, dict[str, object]]] = []
        try:
            for decision in decisions:
                value = _automatic_decision_value(
                    decision,
                    case,
                    report,
                    proposal,
                    sources[decision.source_outcome_id],
                )
                if type(value) is not dict:
                    raise FieldEffectError("authority_binding")
                values.append((decision.decision_id, value))
            return tuple(values)
        except (KeyError, FieldEffectError) as error:
            raise MetadataReportError("review case snapshot is unavailable") from error

    def _current_cover_automatic_decision_id(self, review_case_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT selection.decision_id FROM current_review_case_lineages lineage "
            "JOIN current_cover_considerations current_cover "
            "ON current_cover.review_case_id=lineage.review_case_id "
            "JOIN current_cover_selection_authorities current_selection ON "
            "current_selection.connection_name=lineage.connection_name AND "
            "current_selection.library_name=lineage.library_name AND "
            "current_selection.item_id=lineage.item_id "
            "JOIN cover_selection_authorities selection ON "
            "selection.selection_id=current_selection.selection_id AND "
            "selection.consideration_id=current_cover.consideration_id "
            "WHERE lineage.review_case_id=? AND selection.kind='automatic'",
            (review_case_id,),
        ).fetchone()
        if row is None:
            return None
        if len(row) != 1 or type(row[0]) is not str:
            raise MetadataReportError("review case snapshot is unavailable")
        return row[0]

    def _automatic_source_outcome_documents(
        self, review_case_id: str
    ) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT DISTINCT outcome_set.document, outcome_set.evaluated_at, "
            "outcome_set.outcome_set_id FROM automatic_decisions decision "
            "JOIN shadow_decision_outcomes outcome_set "
            "ON outcome_set.review_case_id=decision.review_case_id "
            "JOIN json_each(outcome_set.document,'$.outcomes') outcome "
            "ON json_extract(outcome.value,'$.outcome_id')="
            "json_extract(decision.document,'$.source_outcome_id') "
            "WHERE decision.review_case_id=? "
            "ORDER BY outcome_set.evaluated_at,outcome_set.outcome_set_id",
            (review_case_id,),
        ).fetchall()
        return tuple(cast(str, row[0]) for row in rows)

    def read_review_library(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
        library_labels: LibraryLabels = (),
        validated: ValidatedDocumentCache | None = None,
    ) -> ReviewLibrarySnapshot:
        with self._consistent_review_read():
            page = self.read_current_library_page(
                search, limit, offset, attention, library_labels
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
            scopes: list[DecisionScope | None] = []
            for (case_document, report_document), envelope in zip(
                page.rows, page.envelopes, strict=True
            ):
                if (
                    case_document is None
                    and '"schema_version":"review-terminal-work-v1"' in report_document
                ):
                    projected_rows.append(
                        (case_document, report_document, (), (), (), (), None, 0)
                    )
                    scopes.append(None)
                    continue
                # The page read validated these envelopes from these bytes.
                if envelope is None:
                    raise MetadataReportError("review library is unavailable")
                case, case_id, report = envelope
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
                decisions, scope = (
                    ((), None)
                    if case is None
                    else self._browse_decision_documents(
                        cast("str", case_id), case, case_document, validated
                    )
                )
                scopes.append(scope)
                projected_rows.append(
                    (
                        case_document,
                        report_document,
                        decisions,
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
            snapshot = ReviewLibrarySnapshot(
                rows=projected,
                total=page.total,
                attention_total=page.attention_total,
                decision_scopes=tuple(scopes),
            )
        return snapshot

    def _browse_decision_documents(
        self,
        review_case_id: str,
        case: dict[str, object],
        case_document: str | None = None,
        validated: ValidatedDocumentCache | None = None,
    ) -> tuple[tuple[str, ...], DecisionScope | None]:
        """Validate only the decision documents attached to one visible row.

        The row's case is projected once, when its first decision has parsed,
        so every refusal is still raised at the step and in the order it was
        when each decision projected the case again (#1215). The scope goes
        out with the documents so the row's projection need not project the
        same case a third time. The page read derived `review_case_id` and
        `case` from `case_document`, so a scope retained for those exact bytes
        is the one this projection would build (#1226).
        """
        human_rows = self._connection.execute(
            "SELECT decision_id,proposal_id,target_fingerprint,destination,"
            "actor,outcome,selected_value_document,idempotency_digest,"
            "request_digest,occurred_at,document FROM human_decisions "
            "WHERE review_case_id=? ORDER BY ordinal",
            (review_case_id,),
        ).fetchall()
        documents: list[str] = []
        scope: DecisionScope | None = None
        try:
            for row in human_rows:
                if len(row) != 11 or type(row[10]) is not str:
                    raise MetadataReportError("review library is unavailable")
                decision = HumanDecision.from_json(row[10])
                if scope is None and validated is not None:
                    scope = validated.reused(BROWSE_SCOPE_REUSE, case_document)
                if scope is None:
                    scope = DecisionScope.of_browse_case(review_case_id, case)
                    if validated is not None:
                        validated.retain(BROWSE_SCOPE_REUSE, scope, case_document)
                decision.validate_in(scope)
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
            documents.extend(
                _automatic_decision_documents(self._connection, review_case_id)
            )
        except (HumanDecisionError, TypeError) as error:
            raise MetadataReportError("review library is unavailable") from error
        return tuple(documents), scope

    def _current_attention_count(self, review_case_id: str) -> int:
        """Count the case's persisted attention facts that are current (#1232).

        A fact exists only for a current package and a stored report, so the
        package's self-join and the report join of the other attention reads
        add nothing here; leaving them out keeps planning this per-row read to
        a few dozen steps instead of about a thousand.
        """
        cover_authority = self._current_cover_authority()
        row = self._connection.execute(
            "SELECT count(*) FROM review_cases rc "
            "JOIN current_review_packages c ON c.review_case_id=rc.review_case_id "
            "JOIN review_attention_facts f ON f.connection_name=c.connection_name "
            "AND f.library_name=c.library_name AND f.item_id=c.item_id "
            "AND f.report_id=rc.report_id WHERE rc.review_case_id=? AND "
            + _ATTENTION_FACT_CURRENT_SQL,
            (review_case_id, *cover_authority),
        ).fetchone()
        if row is None or len(row) != 1 or type(row[0]) is not int or row[0] < 0:
            raise MetadataReportError("review library is unavailable")
        return row[0]

    def _policy_attention_destinations(self, review_case_id: str) -> tuple[str, ...]:
        return tuple(
            row[0]
            for row in self._connection.execute(
                "SELECT result.destination FROM review_cases rc "
                "JOIN metadata_reports mr ON mr.report_id=rc.report_id "
                "JOIN review_case_field_facts result ON result.review_case_id=rc.review_case_id "
                "WHERE rc.review_case_id=? AND "
                + _POLICY_ASK_ATTENTION_SQL
                + " ORDER BY result.destination",
                (review_case_id,),
            )
        )

    def _automatic_attention_destinations(self, review_case_id: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT DISTINCT outcome.destination FROM shadow_outcome_field_facts outcome, "
            "automatic_safety_state safety WHERE outcome.review_case_id=? "
            "AND outcome.automatic_destination=1 AND (safety.paused=1 OR "
            "safety.global_suspension IS NOT NULL OR EXISTS(SELECT 1 FROM "
            "json_each(safety.category_suspensions_document) category WHERE "
            "json_extract(category.value,'$[0]')=CASE WHEN "
            "outcome.policy_category='title' "
            "THEN 'title_layout' ELSE outcome.policy_category END)) ORDER BY 1",
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
            "SELECT 'cover',selection.decision_id FROM current_review_case_lineages lineage "
            "JOIN current_cover_selection_authorities current ON "
            "current.connection_name=lineage.connection_name AND "
            "current.library_name=lineage.library_name AND current.item_id=lineage.item_id "
            "JOIN cover_selection_authorities selection ON selection.selection_id=current.selection_id "
            "WHERE lineage.review_case_id=? "
            "UNION ALL "
            "SELECT destination,decision_id FROM human_decisions WHERE review_case_id=?"
            ") recorded JOIN field_effects effect ON (effect.decision_id=recorded.decision_id OR "
            + _FIELD_EFFECT_COMPANION_DECISION_SQL
            + "=recorded.decision_id) "
            "WHERE effect.state IN ('claimed','applying','reconciling') OR "
            "(effect.state='verified' AND recorded.destination='cover') "
            "ORDER BY recorded.destination",
            (review_case_id, review_case_id, review_case_id),
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

    @local_operation
    def read_current_report_document(
        self, connection: str, library: str, item: str
    ) -> str | None:
        """Return one item's current report document, or ``None`` for no row.

        The same package row that puts a work in the library is what makes its
        report addressable, so a document reachable here is reachable there and
        nowhere else.

        Absence is a return value and a store fault is a raise, deliberately:
        the two mean different things to an owner -- there is no such work
        against this database cannot answer -- and a caller that had to read
        them off one exception could not tell them apart.  The document is
        returned unvalidated, as the browse listing's are; the projection that
        reads it reconstructs the domain report.
        """
        with self._consistent_review_read():
            row = self._connection.execute(
                "SELECT mr.document FROM current_review_packages ml "
                "JOIN metadata_reports mr ON mr.report_id=ml.report_id "
                "WHERE ml.connection_name=? AND ml.library_name=? AND ml.item_id=?",
                (connection, library, item),
            ).fetchone()
            if row is None:
                return None
            if type(row[0]) is not str:
                raise MetadataReportError("review report is unavailable")
            return row[0]

    def read_review_attention(
        self,
        search: str | None,
        limit: int,
        offset: int,
        library_labels: LibraryLabels = (),
    ) -> ReviewAttentionSnapshot:
        with self._consistent_review_read():
            page = self.read_current_attention_page(
                search, limit, offset, library_labels
            )
            snapshot = ReviewAttentionSnapshot(rows=page.rows, total=page.total)
        return snapshot
