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
from typing import cast

from mechanical_librarian.adapters.persistence.cover_consideration_documents import (
    compact_cover_consideration,
    expand_cover_consideration,
)
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationError,
    _validated_cover_consideration,
    cover_consideration_currentness_document,
)
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.audiobook_cover_replacement import (
    material_audiobook_cover_reference,
)
from mechanical_librarian.domain.audiobook_decision_qualification import (
    AudiobookQualificationError,
    identity_for_effect,
    qualification_manifest_sha256,
)
from mechanical_librarian.domain.cover_automatic_decision import CoverAutomaticDecision
from mechanical_librarian.domain.deterministic_cover_election import (
    strictly_better_cover_rank,
)
from mechanical_librarian.domain.field_effect import FieldEffectOperation
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
    manual_cover_eligible,
    validate_cover_confirmation,
)
from mechanical_librarian.domain.metadata_policy import ResolvedMetadataPolicy
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    TargetDestination,
)
from mechanical_librarian.domain.proposal_set import ProposalSet
from mechanical_librarian.domain.review_case import ReviewCase


class SQLiteCoverConsiderationRepository(ConnectionScoped):
    """Persist, read and retire the cover considerations of one review case."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        revoke_automatic: Callable[[str, int], object] | None = None,
        current_cover_authority: Callable[[], tuple[str | None, str | None]],
    ) -> None:
        super().__init__(connection)
        self._current_cover_authority = current_cover_authority
        self.require_owner_cover: Callable[[str, str | None], None] = (
            self._unavailable_owner_cover
        )
        self._revoke_automatic = revoke_automatic or (lambda _decision, _at: None)

    @staticmethod
    def _unavailable_owner_cover(_document: str, _artifact_id: str | None) -> None:
        raise HumanDecisionError("unavailable")

    def _admit_automatic_cover(self, document: str, occurred_at: int) -> None:
        value = json.loads(document)
        if value.get("provider_fetching") is not False:
            return
        policy_report_document = value.get("policy_report_document")
        if type(policy_report_document) is not str or not policy_report_document:
            return
        try:
            policy_report = json.loads(policy_report_document)
        except json.JSONDecodeError:
            return
        if (
            policy_report.get("schema_version") != "cover-policy-report-v2"
            or policy_report.get("owner_ready") is not True
            or policy_report.get("actual_target_state")
            not in {"known_empty", "confirmed"}
        ):
            return
        case_row = self._connection.execute(
            "SELECT document FROM review_cases WHERE review_case_id=?",
            (value["review_case_id"],),
        ).fetchone()
        if case_row is None:
            return
        case = ReviewCase.from_json(case_row[0])
        try:
            report = MetadataReport.from_json(value["report_document"])
        except (KeyError, TypeError, ValueError):
            return
        if (
            report.report_id != case.report_id
            or report.report_id != value.get("report_id")
            or report.target.fingerprint != case.target_fingerprint
        ):
            return
        qualification_identity = None
        target = case.target
        connection_name, library_name, item_id = (
            target.get("connection"),
            target.get("library"),
            target.get("item"),
        )
        if not all(
            type(item) is str and item
            for item in (connection_name, library_name, item_id)
        ):
            return
        lineage = (connection_name, library_name, item_id)
        if self._connection.execute(
            "SELECT proposal_set_id,report_id,review_case_id FROM current_review_case_lineages "
            "WHERE connection_name=? AND library_name=? AND item_id=?",
            lineage,
        ).fetchone() != (case.proposal_set_id, case.report_id, case.review_case_id):
            return
        if self._connection.execute(
            "SELECT report_id FROM current_metadata_reports WHERE connection_name=? "
            "AND library_name=? AND item_id=? AND file_identity=?",
            (*lineage, report.file.identity),
        ).fetchone() != (case.report_id,):
            return
        if self._connection.execute(
            "SELECT consideration_id FROM current_cover_considerations WHERE review_case_id=?",
            (case.review_case_id,),
        ).fetchone() != (value.get("consideration_id"),):
            return
        state = self._connection.execute(
            "SELECT s.generation,s.posture,r.document FROM metadata_policy_state s "
            "JOIN metadata_policy_resolutions r ON r.generation=s.generation "
            "AND r.library_name=? WHERE s.singleton=1",
            (library_name,),
        ).fetchone()
        if state is None or state[1] != "automatic":
            return
        resolved = ResolvedMetadataPolicy.from_data(json.loads(state[2]))
        if dict(resolved.destinations).get("cover_order") != "automatic" or dict(
            resolved.options
        ).get("cover_order") not in {
            "fixed_source_order",
            "replace_if_strictly_better",
        }:
            return
        if (
            report.audio is None
            and dict(resolved.options)["cover_order"] == "replace_if_strictly_better"
            and policy_report.get("actual_target_state") != "known_empty"
        ):
            return
        material_replacement = (
            report.audio is not None
            and dict(resolved.options)["cover_order"] == "replace_if_strictly_better"
        )
        current_cover = next(
            f.value
            for f in report.target.fields
            if f.destination is TargetDestination.COVER
        )
        reference = (
            material_audiobook_cover_reference(value.get("candidates"), current_cover)
            if material_replacement
            and policy_report.get("actual_target_state") == "confirmed"
            else None
            if material_replacement
            else policy_report.get("recommended_candidate_reference")
        )
        candidate = next(
            (
                row
                for row in value.get("candidates", ())
                if type(row) is dict and row.get("reference") == reference
            ),
            None,
        )
        if (
            candidate is None
            or candidate.get("incumbent") is not False
            or candidate.get("selectable") is not True
            or (
                not material_replacement
                and (
                    candidate.get("deterministic_rank") != 1
                    or policy_report.get("status") != "recommended"
                )
            )
            or not candidate.get("deterministic_citations")
            or type(candidate.get("deterministic_rank_key")) is not list
        ):
            return
        if report.audio is not None:
            option = dict(resolved.options)["cover_order"]
            if (
                option == "fixed_source_order"
                and policy_report.get("actual_target_state") != "known_empty"
            ):
                return
            current = next(
                f.value
                for f in report.target.fields
                if f.destination is TargetDestination.COVER
            )
            qualification_identity = identity_for_effect(
                "cover", "cover_order", option, current, candidate.get("artifact_id")
            )
            if qualification_identity is None:
                return
            try:
                qualification_manifest_sha256(qualification_identity)
            except AudiobookQualificationError:
                return
        safety = self._connection.execute(
            "SELECT paused,global_suspension,category_suspensions_document "
            "FROM automatic_safety_state WHERE singleton=1"
        ).fetchone()
        if (
            safety is None
            or safety[0]
            or safety[1] is not None
            or "cover_order" in dict(json.loads(safety[2]))
        ):
            return
        proposal_row = self._connection.execute(
            "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
            (case.proposal_set_id,),
        ).fetchone()
        if proposal_row is None:
            return
        proposal = ProposalSet.from_json(proposal_row[0])
        cover = next(
            (
                result
                for result in proposal.results
                if result.destination is TargetDestination.COVER
            ),
            None,
        )
        if cover is None:
            return
        alternative = next(
            (
                item
                for item in cover.alternatives
                if item.value.value == candidate["artifact_id"]
            ),
            None,
        )
        proposal_id = next(
            (
                item.get("proposal_id")
                for item in case.proposals
                if item.get("destination") == "cover"
            ),
            None,
        )
        if alternative is None or proposal_id != value["proposal_id"]:
            return
        current = self._connection.execute(
            "SELECT selection.document FROM current_cover_selection_authorities current "
            "JOIN cover_selection_authorities selection ON selection.selection_id=current.selection_id "
            "WHERE current.connection_name=? AND current.library_name=? AND current.item_id=?",
            (connection_name, library_name, item_id),
        ).fetchone()
        prior = None if current is None else json.loads(current[0])
        if prior is not None and (
            prior["kind"] == "owner"
            or (
                not material_replacement
                and not strictly_better_cover_rank(
                    candidate["deterministic_rank_key"], prior.get("rank_key")
                )
            )
        ):
            return
        supersedes = None if prior is None else prior["decision_id"]
        decision = CoverAutomaticDecision.create(
            policy_option=(
                dict(resolved.options)["cover_order"]
                if qualification_identity is None
                else qualification_identity.split(":", 1)[1]
            ),
            qualification_identity=qualification_identity,
            review_case_id=case.review_case_id,
            report_id=case.report_id,
            proposal_set_id=case.proposal_set_id,
            proposal_id=str(proposal_id),
            target_fingerprint=case.target_fingerprint,
            candidate_id=alternative.alternative_id,
            resolved_policy_id=resolved.resolved_id,
            policy_generation=state[0],
            consideration_id=value["consideration_id"],
            consideration_digest=hashlib.sha256(document.encode()).hexdigest(),
            replay_digest=value["replay_digest"],
            policy_reference=value["policy_reference"],
            candidate_reference=candidate["reference"],
            artifact_id=candidate["artifact_id"],
            rank_key=tuple(candidate["deterministic_rank_key"]),
            citation_ids=tuple(sorted(set(candidate["deterministic_citations"]))),
            supersedes_decision_id=supersedes,
            occurred_at=occurred_at,
        )
        existing = self._connection.execute(
            "SELECT document FROM cover_selection_authorities WHERE decision_id=?",
            (decision.decision_id,),
        ).fetchone()
        if existing is not None:
            try:
                existing_document = json.loads(existing[0])
            except json.JSONDecodeError as error:
                raise MetadataReportError(
                    "cover automatic authority disagrees"
                ) from error
            if existing_document.get("decision_document") != decision.to_json():
                raise MetadataReportError("cover automatic authority disagrees")
            return
        if supersedes is not None:
            self._revoke_automatic(supersedes, occurred_at)
        selection_id = (
            "cover_selection_" + hashlib.sha256(decision.to_json().encode()).hexdigest()
        )
        selection = _canonical_json(
            {
                "candidate_artifact_id": decision.artifact_id,
                "consideration_id": decision.consideration_id,
                "decision_document": decision.to_json(),
                "decision_id": decision.decision_id,
                "kind": "automatic",
                "rank_key": list(decision.rank_key),
                "schema_version": "cover-selection-authority-v1",
                "selection_id": selection_id,
                "supersedes_selection_id": None
                if prior is None
                else prior["selection_id"],
            }
        )
        self._connection.execute(
            "INSERT INTO cover_selection_authorities(selection_id,connection_name,library_name,item_id,kind,decision_id,consideration_id,supersedes_selection_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                selection_id,
                connection_name,
                library_name,
                item_id,
                "automatic",
                decision.decision_id,
                decision.consideration_id,
                None if prior is None else prior["selection_id"],
                occurred_at,
                selection,
            ),
        )
        self._connection.execute(
            "INSERT INTO current_cover_selection_authorities(connection_name,library_name,item_id,selection_id) VALUES(?,?,?,?) "
            "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET selection_id=excluded.selection_id",
            (connection_name, library_name, item_id, selection_id),
        )
        self._record_cover_consideration_event(
            decision.consideration_id,
            decision.review_case_id,
            "selected",
            occurred_at,
            decision.decision_id,
        )

    def _retire_stale_cover_consideration(
        self,
        operation: FieldEffectOperation,
        state: str,
        changed_at: int,
    ) -> None:
        # A cover waiting for its connection keeps its consideration (#1415).
        if (
            state != "stale"
            or operation.plan.destination != "cover"
            or operation.failure == "connection"
        ):
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

    @local_operation
    def retire_stale_cover_decision(self, decision_id: str, changed_at: int) -> bool:
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

    @local_operation
    def current_cover_consideration(
        self, review_case_id: str, replay_digest: str
    ) -> str | None:
        row = self._connection.execute(
            "SELECT consideration.document,report.document,report.document_digest,consideration.document_digest,consideration.report_id FROM current_cover_considerations current "
            "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
            "LEFT JOIN metadata_reports report ON report.report_id=consideration.report_id "
            "JOIN current_review_case_lineages lineage ON lineage.review_case_id=current.review_case_id "
            "WHERE current.review_case_id=? AND consideration.replay_digest=? "
            "AND NOT EXISTS (SELECT 1 FROM cover_consideration_events event "
            "WHERE event.consideration_id=consideration.consideration_id "
            "AND event.kind='stale')",
            (review_case_id, replay_digest),
        ).fetchone()
        return None if row is None else expand_cover_consideration(*row)

    @local_operation
    def current_cover_consideration_document(self, review_case_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT consideration.document,report.document,report.document_digest,consideration.document_digest,consideration.report_id FROM current_cover_considerations current "
            "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
            "LEFT JOIN metadata_reports report ON report.report_id=consideration.report_id "
            "JOIN current_review_case_lineages lineage ON lineage.review_case_id=current.review_case_id "
            "WHERE current.review_case_id=? "
            "AND NOT EXISTS (SELECT 1 FROM cover_consideration_events event "
            "WHERE event.consideration_id=consideration.consideration_id "
            "AND event.kind='stale')",
            (review_case_id,),
        ).fetchone()
        return None if row is None else expand_cover_consideration(*row)

    @local_operation
    def latest_cover_consideration_document(self, review_case_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT consideration.document,report.document,report.document_digest,consideration.document_digest,consideration.report_id FROM current_cover_considerations current "
            "JOIN cover_considerations consideration "
            "ON consideration.consideration_id=current.consideration_id "
            "LEFT JOIN metadata_reports report ON report.report_id=consideration.report_id "
            "JOIN current_review_case_lineages lineage "
            "ON lineage.review_case_id=current.review_case_id "
            "WHERE current.review_case_id=?",
            (review_case_id,),
        ).fetchone()
        return None if row is None else expand_cover_consideration(*row)

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
        event_id = (
            "cover_consideration_event_"
            + hashlib.sha256(document.encode("utf-8")).hexdigest()
        )
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

    @local_operation
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
        reviewer_access_revision: int | None = None,
    ) -> str:
        try:
            value = json.loads(document)
            if (
                type(value) is not dict
                or value.get("schema_version") != "cover-consideration-v4"
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
        currentness_document = cover_consideration_currentness_document(document)
        transaction = self._transaction("record_cover_consideration")
        try:
            transaction.begin()
            if reviewer_access_revision is not None:
                row = self._connection.execute(
                    "SELECT revision FROM reviewer_access WHERE singleton=1"
                ).fetchone()
                current_revision = 1 if row is None else row[0]
                if current_revision != reviewer_access_revision:
                    raise MetadataReportError("cover consideration changed")
            existing = self._connection.execute(
                "SELECT consideration.document,report.document,report.document_digest,consideration.document_digest,consideration.report_id FROM cover_considerations consideration LEFT JOIN metadata_reports report ON report.report_id=consideration.report_id WHERE consideration.consideration_id=?",
                (consideration_id,),
            ).fetchone()
            if existing is not None:
                if expand_cover_consideration(*existing) != document:
                    raise MetadataReportError("cover consideration disagrees")
                self._admit_automatic_cover(document, occurred_at)
                transaction.commit()
                return document
            current = self._connection.execute(
                "SELECT consideration_id FROM current_cover_considerations WHERE review_case_id=?",
                (review_case_id,),
            ).fetchone()
            actual_supersedes = None if current is None else current[0]
            if actual_supersedes != supersedes_id:
                raise MetadataReportError("cover consideration changed")
            report_row = self._connection.execute(
                "SELECT document,document_digest FROM metadata_reports WHERE report_id=?",
                (report_id,),
            ).fetchone()
            if (
                report_row is None
                or type(report_row[0]) is not str
                or hashlib.sha256(report_row[0].encode("utf-8")).hexdigest()
                != report_row[1]
            ):
                raise MetadataReportError("cover consideration is invalid")
            stored_document = compact_cover_consideration(document, report_row[0])
            self._connection.execute(
                "INSERT INTO cover_considerations(consideration_id,review_case_id,report_id,replay_digest,document_digest,supersedes_id,occurred_at,document,currentness_document) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    consideration_id,
                    review_case_id,
                    report_id,
                    replay_digest,
                    hashlib.sha256(document.encode("utf-8")).hexdigest(),
                    supersedes_id,
                    occurred_at,
                    stored_document,
                    currentness_document,
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
            self._admit_automatic_cover(document, occurred_at)
            transaction.commit()
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError("cover consideration is unavailable") from error
        except MetadataReportError:
            transaction.rollback()
            raise
        return document

    @local_operation
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

    @local_operation
    def cover_consideration_history(
        self, review_case_id: str, limit: int = 50
    ) -> tuple[dict[str, object], ...]:
        rows = self._connection.execute(
            "SELECT event.document,consideration.document,report.document,report.document_digest,consideration.document_digest,consideration.report_id FROM "
            "cover_consideration_events event JOIN cover_considerations consideration "
            "ON consideration.consideration_id=event.consideration_id "
            "LEFT JOIN metadata_reports report ON report.report_id=consideration.report_id "
            "WHERE event.review_case_id=? ORDER BY event.occurred_at DESC,event.event_id DESC LIMIT ?",
            (review_case_id, min(max(limit, 1), 50)),
        ).fetchall()
        items = []
        for row in rows:
            event = json.loads(row[0])
            event["consideration_document"] = expand_cover_consideration(*row[1:])
            items.append(event)
        return tuple(items)

    def _record_cover_decision_outcome(
        self,
        decision: HumanDecision,
        consideration_id: str | None,
        confirmation: dict[str, object] | None,
        *,
        validate_only: bool = False,
    ) -> None:
        if decision.destination != "cover":
            if consideration_id is not None or confirmation is not None:
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
        recorded = self._connection.execute(
            "SELECT document FROM cover_selection_authorities WHERE decision_id=?",
            (decision.decision_id,),
        ).fetchone()
        if (
            recorded is not None
            and json.loads(recorded[0]).get("confirmation") != confirmation
        ):
            raise HumanDecisionError("idempotency_conflict")
        row = self._connection.execute(
            "SELECT consideration.document,review.document,report.document,report.document_digest,consideration.document_digest,consideration.report_id FROM current_cover_considerations current "
            "JOIN cover_considerations consideration "
            "ON consideration.consideration_id=current.consideration_id "
            "LEFT JOIN metadata_reports report ON report.report_id=consideration.report_id "
            "JOIN review_cases review ON review.review_case_id=current.review_case_id "
            "JOIN current_review_case_lineages lineage ON lineage.review_case_id=review.review_case_id "
            "JOIN current_review_packages package ON package.review_case_id=lineage.review_case_id AND package.report_id=lineage.report_id AND package.proposal_set_id=lineage.proposal_set_id "
            "WHERE current.review_case_id=? AND current.consideration_id=?",
            (decision.review_case_id, consideration_id),
        ).fetchone()
        if row is None:
            raise HumanDecisionError("stale")
        try:
            expanded_document = expand_cover_consideration(row[0], *row[2:])
            consideration, report = _validated_cover_consideration(expanded_document)
            case = ReviewCase.from_json(row[1])
            if (
                consideration["policy_reference"],
                consideration["replay_reference"],
            ) != self._current_cover_authority():
                raise HumanDecisionError("stale")
            if (
                consideration["proposal_id"] != decision.proposal_id
                or consideration["target_fingerprint"] != decision.target_fingerprint
            ):
                raise HumanDecisionError("stale")
            selected = None
            valid_choice = True
            if decision.outcome == "accept":
                selected = next(
                    (
                        candidate
                        for candidate in cast(
                            "list[dict[str, object]]", consideration["candidates"]
                        )
                        if decision.selected_value
                        == {"kind": "cover", "value": candidate["artifact_id"]}
                    ),
                    None,
                )
                if (
                    selected is None
                    or selected["incumbent"] is True
                    or not manual_cover_eligible(selected)
                ):
                    valid_choice = False
            elif decision.outcome == "keep_current":
                selected = next(
                    (
                        candidate
                        for candidate in cast(
                            "list[dict[str, object]]", consideration["candidates"]
                        )
                        if candidate["incumbent"] is True
                    ),
                    None,
                )
                if selected is None or decision.selected_value is not None:
                    valid_choice = False
            elif decision.selected_value is not None:
                valid_choice = False
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise MetadataReportError("cover consideration is unavailable") from error
        target = case.target
        connection_name, library_name, item_id = (
            target.get("connection"),
            target.get("library"),
            target.get("item"),
        )
        if not all(
            type(item) is str and item
            for item in (connection_name, library_name, item_id)
        ):
            raise MetadataReportError("cover consideration is unavailable")
        current = self._connection.execute(
            "SELECT selection.document FROM current_cover_selection_authorities current "
            "JOIN cover_selection_authorities selection ON selection.selection_id=current.selection_id "
            "WHERE current.connection_name=? AND current.library_name=? AND current.item_id=?",
            (connection_name, library_name, item_id),
        ).fetchone()
        prior = None if current is None else json.loads(current[0])
        confirmation = validate_cover_confirmation(confirmation)
        if existing is not None:
            if prior is None or prior["decision_id"] != decision.decision_id:
                raise HumanDecisionError("stale")
            return
        if confirmation["expected_selection_id"] != (
            None if prior is None else prior["selection_id"]
        ):
            raise HumanDecisionError("stale")
        # The decision repository calls this before checking the prior effect,
        # then again just before publication. Files and acquisition are external
        # to SQLite: their bounded recheck does not promise cross-store atomicity.
        if validate_only:
            return
        if not valid_choice or (
            decision.outcome != "accept"
            and confirmation["confirm_unverified_match"] is True
        ):
            raise HumanDecisionError("invalid_choice")
        if (
            selected is not None
            and decision.outcome == "accept"
            and (
                selected["match_state"] == "match_not_confirmed"
                and confirmation["confirm_unverified_match"] is not True
            )
        ):
            raise HumanDecisionError("invalid_choice")
        try:
            self.require_owner_cover(
                expanded_document,
                None if selected is None else cast(str, selected["artifact_id"]),
            )
        except CoverConsiderationError as error:
            raise HumanDecisionError(error.code) from error
        selection_id = (
            "cover_selection_"
            + hashlib.sha256(decision.to_json().encode("utf-8")).hexdigest()
        )
        selection_document = _canonical_json(
            {
                "candidate_artifact_id": (
                    None if selected is None else selected["artifact_id"]
                ),
                "consideration_id": consideration_id,
                "decision_document": decision.to_json(),
                "decision_id": decision.decision_id,
                "kind": "owner",
                "rank_key": (
                    None if selected is None else selected["deterministic_rank_key"]
                ),
                "schema_version": "cover-selection-authority-v2",
                "confirmation": confirmation,
                "observed_identity": None if selected is None else selected["identity"],
                "candidate_reference": None
                if selected is None
                else selected["reference"],
                "original_sha256": None
                if selected is None
                else selected["original_sha256"],
                "preview_sha256": None
                if selected is None
                else selected["preview_sha256"],
                "consideration_digest": hashlib.sha256(
                    expanded_document.encode()
                ).hexdigest(),
                "target": dict(case.target),
                "selection_id": selection_id,
                "supersedes_selection_id": (
                    None if prior is None else prior["selection_id"]
                ),
            }
        )
        self._connection.execute(
            "INSERT INTO cover_selection_authorities(selection_id,connection_name,library_name,item_id,kind,decision_id,consideration_id,supersedes_selection_id,occurred_at,document) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                selection_id,
                connection_name,
                library_name,
                item_id,
                "owner",
                decision.decision_id,
                consideration_id,
                None if prior is None else prior["selection_id"],
                decision.occurred_at,
                selection_document,
            ),
        )
        self._connection.execute(
            "INSERT INTO current_cover_selection_authorities(connection_name,library_name,item_id,selection_id) VALUES(?,?,?,?) "
            "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET selection_id=excluded.selection_id",
            (connection_name, library_name, item_id, selection_id),
        )
        self._record_cover_consideration_event(
            consideration_id,
            decision.review_case_id,
            kind,
            decision.occurred_at,
            decision.decision_id,
        )


__all__ = ["SQLiteCoverConsiderationRepository"]
