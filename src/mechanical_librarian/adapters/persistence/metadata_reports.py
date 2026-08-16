"""Clean-state SQLite repository for immutable reports and ProposalSets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, cast
from urllib.parse import quote

from mechanical_librarian.adapters.persistence.binding import (
    _connect_bound,
    _LinuxDatabaseBinding,
    _LinuxDirectoryChain,
    _reject_database_sidecars,
    _unlink_created,
    _validate_connection_binding,
    _validate_delete_mode_header,
)
from mechanical_librarian.adapters.persistence.cover_considerations import (
    SQLiteCoverConsiderationRepository,
)
from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.field_effect_activity import (
    SQLiteFieldEffectActivityRepository,
)
from mechanical_librarian.adapters.persistence.field_effects import (
    SQLiteFieldEffectRepository,
)
from mechanical_librarian.adapters.persistence.metadata_policy import (
    SQLiteMetadataPolicyRepository,
)
from mechanical_librarian.adapters.persistence.proposal_sets import (
    SQLiteProposalSetRepository,
    _validate_proposal_row,
)
from mechanical_librarian.adapters.persistence.recovery_outbox import (
    SQLiteRecoveryOutboxRepository,
)
from mechanical_librarian.adapters.persistence.reports import (
    SQLiteReportRepository,
)
from mechanical_librarian.adapters.persistence.review_cases import (
    SQLiteReviewCaseRepository,
)
from mechanical_librarian.adapters.persistence.review_preparation import (
    SQLiteReviewPreparationRepository,
)
from mechanical_librarian.adapters.persistence.review_scans import (
    SQLiteReviewScanRepository,
)
from mechanical_librarian.adapters.persistence.review_sessions import (
    SQLiteReviewSessionRepository,
)
from mechanical_librarian.adapters.persistence.review_sql import (
    _CURRENT_REVIEW_FROM_SQL,
)
from mechanical_librarian.adapters.persistence.schema import (
    _create_schema,
    _provider_recovery_outbox_id,
    _validate_existing_read_only,
    _validate_report_row,
    _validate_schema,
)
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    TransactionScope,
)
from mechanical_librarian.application.catalogue import (
    CatalogueEntry,
    TargetConnectionError,
)
from mechanical_librarian.application.metadata_policy import PolicyTransition
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    PipelineProgress,
    TargetConnectionCondition,
)
from mechanical_librarian.application.review_web import (
    CurrentReviewCaseSnapshot,
    ReviewAttentionSnapshot,
    ReviewLibrarySnapshot,
)
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    AutomaticDecisionError,
    AutomaticSafetyState,
    qualification_manifest_sha256,
)
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectError,
    FieldEffectOperation,
)
from mechanical_librarian.domain.field_effect import (
    canonical_json as canonical_effect_json,
)
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
    HumanDecisionError,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyError,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
    ReportValueError,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
)
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionStaleError,
    ReviewCase,
    ReviewCaseValueError,
)
from mechanical_librarian.domain.review_field_classification import ReviewFieldCause
from mechanical_librarian.domain.shadow_decision import decide_shadow_packet
from mechanical_librarian.domain.shadow_outcome import (
    ShadowOutcomeError,
    ShadowOutcomeSet,
)
from mechanical_librarian.domain.shadow_review import (
    ShadowReviewPacket,
    ShadowReviewValueError,
)

if sys.platform.startswith("linux"):
    import fcntl
else:
    fcntl = None


class SQLiteMetadataReportRepository(ConnectionScoped):
    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        identity: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(connection)
        self._binding = binding
        self.identity = identity
        self._recovery_outbox = SQLiteRecoveryOutboxRepository(connection)
        self._proposal_sets = SQLiteProposalSetRepository(
            connection,
            binding,
            current_resolved_metadata_policy=(
                lambda library: self.current_resolved_metadata_policy(library)
            ),
        )
        self._cover_considerations = SQLiteCoverConsiderationRepository(connection)
        self._reports = SQLiteReportRepository(
            connection,
            binding,
            current_review_package=(
                lambda name, library, item: self.current_review_package(
                    name, library, item
                )
            ),
        )
        self._metadata_policy = SQLiteMetadataPolicyRepository(
            connection,
            binding,
            restart_metadata_policy_work=self._restart_metadata_policy_work,
        )
        self._review_sessions = SQLiteReviewSessionRepository(connection)
        self._review_scans = SQLiteReviewScanRepository(connection)
        self._field_effects = SQLiteFieldEffectRepository(
            connection,
            binding,
            record_field_effect_event=(
                lambda operation, kind, occurred_at: (
                    self._record_field_effect_event(operation, kind, occurred_at)
                )
            ),
            field_effect_consent_envelope_identity=(
                lambda library: self.field_effect_consent_envelope_identity(library)
            ),
            field_effect_authority_source=(
                lambda decision: self.field_effect_authority_source(decision)
            ),
            retire_stale_cover_consideration=(
                lambda operation, state, changed_at: (
                    self._cover_considerations._retire_stale_cover_consideration(
                        operation, state, changed_at
                    )
                )
            ),
        )
        # Every injected callable resolves its target through this repository at
        # call time rather than capturing a bound method here.  Three review
        # pipeline tests substitute `_has_pending_field_effect_manual_return` on
        # the repository and drive it through a claimed action; a captured bound
        # method would leave them passing while testing nothing.
        self._review_preparation = SQLiteReviewPreparationRepository(
            connection,
            binding,
            has_pending_field_effect_manual_return=(
                lambda scan_id: self._has_pending_field_effect_manual_return(scan_id)
            ),
            require_staged_shadow_projection_current=(
                lambda review_case_id, action: (
                    self._require_staged_shadow_projection_current(
                        review_case_id, action
                    )
                )
            ),
            refresh_metadata_policy_transition=(
                lambda transition_id, updated_at: (
                    self._metadata_policy._refresh_metadata_policy_transition(
                        transition_id, updated_at
                    )
                )
            ),
            enqueue_provider_recovery_outbox=(
                lambda name, library, item, pointer, created_at: (
                    self._recovery_outbox.enqueue(
                        name, library, item, pointer, created_at
                    )
                )
            ),
        )
        self._review_cases = SQLiteReviewCaseRepository(
            connection,
            binding,
            current_resolved_metadata_policy=(
                lambda library: self.current_resolved_metadata_policy(library)
            ),
            policy_destinations_for=(
                lambda report, proposal: (
                    self._metadata_policy._policy_destinations_for(report, proposal)
                )
            ),
        )
        self._current_cover_policy_reference: str | None = None
        self._current_cover_replay_reference: str | None = None
        # The two cover references are rebound by `bind_current_cover_policy`
        # and `bind_current_cover_replay` long after this point, so the
        # projection side reads them through a callable rather than receiving
        # the `None` they hold here.
        self._field_effect_activity = SQLiteFieldEffectActivityRepository(
            connection,
            binding,
            review_case_accepts_authority=(
                lambda review_case_id: self._review_case_accepts_authority(
                    review_case_id
                )
            ),
            get_review_case=(
                lambda review_case_id: self.get_review_case(review_case_id)
            ),
            current_resolved_policy_id=(
                lambda library: self.current_resolved_policy_id(library)
            ),
            field_effect_paused=(lambda: self.field_effect_paused()),
            field_effect_capability_suspension=(
                lambda connection_name, destination: (
                    self.field_effect_capability_suspension(
                        connection_name, destination
                    )
                )
            ),
            consent_envelope_identity=(
                lambda library: self.field_effect_consent_envelope_identity(library)
            ),
            update_field_effect=(
                lambda operation, expected_state: self._update_field_effect(
                    operation, expected_state
                )
            ),
            record_field_effect_event=(
                lambda operation, kind, occurred_at: (
                    self._record_field_effect_event(operation, kind, occurred_at)
                )
            ),
            current_cover_policy_reference=(
                lambda: self._current_cover_policy_reference
            ),
            current_cover_replay_reference=(
                lambda: self._current_cover_replay_reference
            ),
        )

    def bind_current_cover_policy(self, reference: str) -> None:
        """Bind guarded cover effects to this process's validated policy."""
        if re.fullmatch(r"cover_policy_[0-9a-f]{64}", reference) is None:
            raise ValueError("cover policy reference is invalid")
        self._current_cover_policy_reference = reference

    def bind_current_cover_replay(self, reference: str) -> None:
        """Bind guarded cover effects to the current observer/contract profile."""
        if re.fullmatch(r"cover_replay_[0-9a-f]{64}", reference) is None:
            raise ValueError("cover replay reference is invalid")
        self._current_cover_replay_reference = reference

    def field_effect_authority_source(
        self, decision: HumanDecision | AutomaticDecision
    ) -> dict[str, object]:
        return self._field_effect_activity.field_effect_authority_source(decision)

    def record_current_field_effect_no_change(
        self, decision: AutomaticDecision, occurred_at: int,
    ) -> bool:
        return self._field_effects.record_current_field_effect_no_change(
            decision,
            occurred_at,
        )

    def field_effect_authority_is_current(self, authority: EffectAuthority) -> bool:
        return self._field_effect_activity.field_effect_authority_is_current(
            authority
        )

    def grant_field_effect_consents(
        self, connection: str, libraries: tuple[str, ...],
        destinations: tuple[str, ...], granted_at: int,
    ) -> tuple[EffectConsent, ...]:
        return self._field_effects.grant_field_effect_consents(
            connection,
            libraries,
            destinations,
            granted_at,
        )

    def grant_field_effect_consent_matrix(
        self, connection: str,
        grants: tuple[tuple[str, tuple[str, ...]], ...], granted_at: int,
    ) -> tuple[EffectConsent, ...]:
        return self._field_effects.grant_field_effect_consent_matrix(
            connection,
            grants,
            granted_at,
        )

    def current_field_effect_consent(
        self, connection: str, library: str, destination: str,
    ) -> EffectConsent:
        return self._field_effects.current_field_effect_consent(
            connection,
            library,
            destination,
        )

    def field_effect_consent_is_current(self, consent: EffectConsent) -> bool:
        return self._field_effects.field_effect_consent_is_current(consent)

    def field_effect_consent_envelope_identity(self, library: str) -> str:
        return self._field_effect_activity.field_effect_consent_envelope_identity(
            library
        )

    def revoke_library_field_effect_consents(
        self, connection: str, library: str, changed_at: int,
    ) -> int:
        return self._field_effects.revoke_library_field_effect_consents(
            connection,
            library,
            changed_at,
        )

    def pending_field_effect_decision_documents(
        self, limit: int, after: tuple[int, str] | None = None,
    ) -> tuple[str, ...]:
        return self._field_effect_activity.pending_field_effect_decision_documents(
            limit, after
        )

    def pending_field_effect_decision_counts(self) -> tuple[dict[str, object], ...]:
        return self._field_effect_activity.pending_field_effect_decision_counts()

    def field_effect_manual_override(
        self, review_case_id: str, destination: str, changed_at: int,
    ) -> int:
        """Supersede only unclaimed automatic authority for a human override."""
        if (type(review_case_id) is not str or type(destination) is not str
                or type(changed_at) is not int or changed_at < 0):
            raise FieldEffectError("authority_binding")
        transaction = self._transaction("field_effect_manual_override")
        try:
            transaction.begin()
            rows = self._connection.execute(
                "SELECT automatic.decision_id,human.decision_id FROM automatic_decisions automatic "
                "JOIN human_decisions human ON human.review_case_id=automatic.review_case_id "
                "AND human.destination=automatic.destination "
                "AND human.ordinal=(SELECT MAX(current.ordinal) FROM human_decisions current "
                "WHERE current.review_case_id=human.review_case_id AND "
                "current.destination=human.destination) "
                "WHERE automatic.review_case_id=? AND automatic.destination=?",
                (review_case_id, destination),
            ).fetchall()
            if rows:
                self._set_field_effect_manual_hold(
                    review_case_id, destination, changed_at, rows[0][1]
                )
            superseded = sum(
                self._revoke_field_effect_authority_in_transaction(
                    decision_id, changed_at, required=False
                )
                for decision_id, _ in rows
            )
            transaction.commit()
            return superseded
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def _set_field_effect_manual_hold(
        self, review_case_id: str, destination: str, changed_at: int,
        decision_id: str,
    ) -> None:
        self._field_effects._set_field_effect_manual_hold(
            review_case_id,
            destination,
            changed_at,
            decision_id,
        )

    def field_effect_manual_hold_context(
        self, review_case_id: str, destination: str,
    ) -> tuple[str, str] | None:
        return self._field_effects.field_effect_manual_hold_context(
            review_case_id,
            destination,
        )

    def field_effect_manual_hold_token(
        self, review_case_id: str, destination: str,
    ) -> str | None:
        return self._field_effects.field_effect_manual_hold_token(
            review_case_id,
            destination,
        )

    def request_field_effect_manual_return(
        self, review_case_id: str, destination: str, hold_token: str,
        scan_id: str, requested_at: int, changed_at: int,
    ) -> bool:
        return self._field_effects.request_field_effect_manual_return(
            review_case_id,
            destination,
            hold_token,
            scan_id,
            requested_at,
            changed_at,
        )

    def pending_field_effect_manual_returns(
        self, limit: int, after: tuple[int, str, str] | None = None,
    ) -> tuple[tuple[str, str, str, str, str, str, int, int], ...]:
        return self._field_effect_activity.pending_field_effect_manual_returns(
            limit, after
        )

    def _has_pending_field_effect_manual_return(self, scan_id: str) -> bool:
        return self._field_effects._has_pending_field_effect_manual_return(scan_id)

    def clear_field_effect_manual_hold(
        self, review_case_id: str, destination: str, changed_at: int,
        expected_hold_token: str | None = None,
    ) -> tuple[str, str] | None:
        return self._field_effects.clear_field_effect_manual_hold(
            review_case_id,
            destination,
            changed_at,
            expected_hold_token,
        )

    def field_effect_dependencies_current(self, digests: tuple[str, ...]) -> bool:
        return self._field_effect_activity.field_effect_dependencies_current(digests)

    def field_effect_backup_ready(self) -> bool:
        return self._field_effect_activity.field_effect_backup_ready()

    def next_field_effect_plan_id(
        self, last_library: str | None,
    ) -> tuple[str, str] | None:
        return self._field_effects.next_field_effect_plan_id(last_library)

    def pending_field_effect_action_plan_ids(self, limit: int) -> tuple[str, ...]:
        return self._field_effects.pending_field_effect_action_plan_ids(limit)

    def field_effect_activity_summary(self) -> dict[str, object]:
        return self._field_effect_activity.field_effect_activity_summary()

    def field_effect_activity_data(
        self, limit: int = 50, *, include_events: bool = True
    ) -> dict[str, object]:
        return self._field_effect_activity.field_effect_activity_data(
            limit, include_events=include_events
        )

    def field_effect_activity_page(
        self,
        *,
        search: str | None,
        event_type: str,
        occurred_after: int,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        return self._field_effect_activity.field_effect_activity_page(
            search=search,
            event_type=event_type,
            occurred_after=occurred_after,
            page=page,
            page_size=page_size,
        )

    def record_field_effect(self, operation: FieldEffectOperation) -> FieldEffectOperation:
        return self._field_effects.record_field_effect(operation)

    def get_field_effect(self, plan_id: str) -> FieldEffectOperation:
        return self._field_effects.get_field_effect(plan_id)

    def field_effect_event_documents(self, plan_id: str) -> tuple[str, ...]:
        return self._field_effects.field_effect_event_documents(plan_id)

    def suspend_field_effect_capability(self, *, scope: str, connection: str,
                                        destination: str, reason: str,
                                        changed_at: int) -> None:
        self._field_effects.suspend_field_effect_capability(
            scope=scope,
            connection=connection,
            destination=destination,
            reason=reason,
            changed_at=changed_at,
        )

    def field_effect_capability_suspension(self, connection: str,
                                           destination: str) -> str | None:
        return self._field_effects.field_effect_capability_suspension(
            connection,
            destination,
        )

    def clear_field_effect_capability_suspension(
        self, *, scope: str, connection: str, destination: str, reason: str,
        observed_at: int,
    ) -> None:
        self._field_effects.clear_field_effect_capability_suspension(
            scope=scope,
            connection=connection,
            destination=destination,
            reason=reason,
            observed_at=observed_at,
        )

    def claim_field_effect(self, plan_id: str, expected_state: str,
                           claim_token: str, claimed_at: int) -> FieldEffectOperation:
        return self._field_effect_activity.claim_field_effect(
            plan_id, expected_state, claim_token, claimed_at
        )

    def transition_field_effect(self, plan_id: str, expected_state: str,
                                state: str, changed_at: int,
                                failure: str | None = None) -> FieldEffectOperation:
        return self._field_effects.transition_field_effect(
            plan_id,
            expected_state,
            state,
            changed_at,
            failure,
        )

    def supersede_field_effect(self, decision_id: str, superseded_at: int) -> int:
        return self.revoke_field_effect_authority(decision_id, superseded_at)

    def revoke_field_effect_authority(self, decision_id: str, changed_at: int) -> int:
        transaction = self._transaction("revoke_field_effect_authority")
        try:
            transaction.begin()
            count = self._revoke_field_effect_authority_in_transaction(
                decision_id, changed_at, required=True
            )
            transaction.commit()
            return count
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def _revoke_field_effect_authority_in_transaction(
        self, decision_id: str, changed_at: int, *, required: bool,
    ) -> int:
        return self._field_effects._revoke_field_effect_authority_in_transaction(
            decision_id,
            changed_at,
            required=required,
        )

    def revoke_field_effect_consent(self, consent_id: str, changed_at: int) -> int:
        return self._field_effects.revoke_field_effect_consent(consent_id, changed_at)

    def schedule_field_effect_reconciliation(self, plan_id: str,
                                             expected_state: str,
                                             changed_at: int,
                                             failure: str = "ambiguous") -> FieldEffectOperation:
        return self._field_effects.schedule_field_effect_reconciliation(
            plan_id,
            expected_state,
            changed_at,
            failure,
        )

    def complete_field_effect_reconciliation(self, plan_id: str, state: str,
                                             failure: str | None, changed_at: int,
                                             reevaluate: bool = False) -> FieldEffectOperation:
        return self._field_effects.complete_field_effect_reconciliation(
            plan_id,
            state,
            failure,
            changed_at,
            reevaluate,
        )

    def retire_stale_cover_decision(
        self, decision_id: str, changed_at: int
    ) -> bool:
        return self._cover_considerations.retire_stale_cover_decision(
            decision_id, changed_at
        )

    def claim_next_field_effect_action(self, changed_at: int,
                                       plan_id: str | None = None) -> dict[str, object] | None:
        return self._field_effects.claim_next_field_effect_action(changed_at, plan_id)

    def complete_forward_reevaluation(self, action_id: str, changed_at: int) -> None:
        self._field_effects.complete_forward_reevaluation(action_id, changed_at)

    def field_effect_cleanup_pending(self, plan_id: str) -> bool:
        return self._field_effects.field_effect_cleanup_pending(plan_id)

    def pending_field_effect_cleanups(self, limit: int) -> tuple[str, ...]:
        return self._field_effects.pending_field_effect_cleanups(limit)

    def pending_field_effect_plan_ids(self, limit: int) -> tuple[str, ...]:
        return self._field_effects.pending_field_effect_plan_ids(limit)

    def complete_field_effect_cleanup(self, plan_id: str, changed_at: int) -> None:
        self._field_effects.complete_field_effect_cleanup(plan_id, changed_at)

    def fail_field_effect_cleanup(self, plan_id: str, changed_at: int) -> None:
        self._field_effects.fail_field_effect_cleanup(plan_id, changed_at)

    def set_field_effect_paused(self, paused: bool, changed_at: int) -> None:
        self._field_effects.set_field_effect_paused(paused, changed_at)

    def field_effect_paused(self) -> bool:
        return self._field_effects.field_effect_paused()

    def operational_snapshot(self) -> dict[str, object]:
        return self._field_effect_activity.operational_snapshot()

    def _update_field_effect(self, operation: FieldEffectOperation,
                             expected_state: str) -> None:
        self._field_effects._update_field_effect(operation, expected_state)

    def _record_field_effect_event(self, operation: FieldEffectOperation,
                                   kind: str, occurred_at: int) -> None:
        sequence = self._connection.execute(
            "SELECT count(*)+1 FROM field_effect_events WHERE plan_id=?",
            (operation.plan.plan_id,),
        ).fetchone()[0]
        content = {
            "schema_version": "field-effect-event-v1", "plan_id": operation.plan.plan_id,
            "decision_id": operation.plan.authority.decision_id,
            "origin": operation.plan.authority.origin, "destination": operation.plan.destination,
            "pre_value": operation.plan.pre_value, "desired_value": operation.plan.desired_value,
            "state": operation.state, "attempt": operation.attempt, "failure": operation.failure,
            "occurred_at": occurred_at, "kind": kind, "sequence": sequence,
        }
        event_id = "effect_event_" + hashlib.sha256(
            canonical_effect_json(content).encode()
        ).hexdigest()
        document = canonical_effect_json({**content, "event_id": event_id})
        self._connection.execute(
            "INSERT INTO field_effect_events(event_id,plan_id,sequence,kind,occurred_at,document) VALUES(?,?,?,?,?,?)",
            (event_id, operation.plan.plan_id, sequence, kind, occurred_at, document),
        )

    @property
    def authority(self) -> object | None:
        return self._binding

    def request_review_scan(
        self,
        connection: str,
        library: str,
        library_id: str,
        producer_fingerprint: str,
        requested_at: int,
    ) -> PipelineProgress:
        return self._review_scans.request_review_scan(
            connection, library, library_id, producer_fingerprint, requested_at
        )

    def review_pipeline_progress(
        self, connection: str, library: str
    ) -> PipelineProgress | None:
        return self._review_scans.review_pipeline_progress(connection, library)

    def review_pipeline_status(
        self, libraries: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[PipelineProgress | None, ...], bool]:
        return self._review_scans.review_pipeline_status(libraries)

    def review_pipeline_paused(self) -> bool:
        return self._review_scans.review_pipeline_paused()

    def set_review_pipeline_paused(self, paused: bool, updated_at: int) -> None:
        self._review_scans.set_review_pipeline_paused(paused, updated_at)

    def recover_review_pipeline(
        self, producers: tuple[tuple[str, str, str, str], ...]
    ) -> None:
        self._review_scans.recover_review_pipeline(producers)

    def target_connection_conditions(self) -> tuple[TargetConnectionCondition, ...]:
        return self._review_scans.target_connection_conditions()

    def guard_target_connection(self, action: PipelineAction, checked_at: int) -> bool:
        return self._review_scans.guard_target_connection(action, checked_at)

    def discovery_requires_qualification(self, action: PipelineAction) -> bool:
        return self._review_scans.discovery_requires_qualification(action)

    def qualify_review_discovery(self, action: PipelineAction, qualified_at: int) -> bool:
        return self._review_scans.qualify_review_discovery(action, qualified_at)

    def defer_for_target_connection(
        self, action: PipelineAction, code: str, updated_at: int
    ) -> None:
        self._review_scans.defer_for_target_connection(action, code, updated_at)

    def hold_target_connection(self, connection: str, code: str, updated_at: int) -> None:
        self._review_scans.hold_target_connection(connection, code, updated_at)

    def begin_target_connection_check(
        self, connection: str, checked_at: int
    ) -> TargetConnectionCondition | None:
        return self._review_scans.begin_target_connection_check(connection, checked_at)

    def finish_target_connection_check(
        self, connection: str, epoch: int, code: str | None, checked_at: int
    ) -> bool:
        return self._review_scans.finish_target_connection_check(
            connection, epoch, code, checked_at
        )

    def claim_review_pipeline_action(self, claimed_at: int) -> PipelineAction | None:
        return self._review_preparation.claim_review_pipeline_action(claimed_at)

    def claim_review_pipeline_work(
        self, work_id: str, claimed_at: int
    ) -> PipelineAction | None:
        return self._review_preparation.claim_review_pipeline_work(
            work_id, claimed_at
        )

    def stage_provider_retry_successor(
        self, source_report_id: str, report: MetadataReport, staged_at: int
    ) -> str:
        return self._review_preparation.stage_provider_retry_successor(
            source_report_id, report, staged_at
        )

    def provider_retry_work_id(self, report_id: str) -> str:
        return self._review_preparation.provider_retry_work_id(report_id)

    def provider_retry_complete(self, report_id: str) -> bool:
        return self._review_preparation.provider_retry_complete(report_id)

    def completed_provider_retry_successor(
        self, source_report_id: str
    ) -> str | None:
        return self._review_preparation.completed_provider_retry_successor(
            source_report_id
        )

    def complete_review_discovery(
        self,
        action: PipelineAction,
        generation: int,
        fingerprint: str,
        entries: tuple[CatalogueEntry, ...],
        completed_at: int,
    ) -> None:
        self._review_preparation.complete_review_discovery(
            action, generation, fingerprint, entries, completed_at
        )

    def advance_review_pipeline_action(
        self,
        action: PipelineAction,
        output_id: str | None,
        completed_at: int,
        claim_next: bool = False,
        exact_next: bool = False,
    ) -> PipelineAction | None:
        return self._review_preparation.advance_review_pipeline_action(
            action, output_id, completed_at, claim_next, exact_next
        )

    def begin_review_pipeline_output(self) -> None:
        self._review_preparation.begin_review_pipeline_output()

    def take_review_pipeline_claim(self) -> PipelineAction | None:
        return self._review_preparation.take_review_pipeline_claim()

    def rollback_review_pipeline_output(self) -> None:
        self._review_preparation.rollback_review_pipeline_output()

    def complete_review_extraction_publication(
        self,
        action: PipelineAction,
        report_id: str,
        completed_at: int,
        recovery_delay_seconds: int = 0,
        provider_failure_code: str | None = None,
        claim_next: bool = False,
        provider_failure_root: str | None = None,
    ) -> None:
        self._review_preparation.complete_review_extraction_publication(
            action,
            report_id,
            completed_at,
            recovery_delay_seconds,
            provider_failure_code,
            claim_next,
            provider_failure_root,
        )

    def fail_review_pipeline_action(
        self,
        action: PipelineAction,
        code: str,
        retryable: bool,
        completed_at: int,
        failure_root: str | None = None,
    ) -> None:
        self._review_preparation.fail_review_pipeline_action(
            action, code, retryable, completed_at, failure_root
        )

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

    def publish(
        self,
        report: MetadataReport,
        currentness: Callable[[], bool],
        expected_current: str | None = None,
        after_publication: Callable[[object, MetadataReport], None] | None = None,
        *,
        expected_package: tuple[str | None, str, str | None, int | None, str] | None = None,
    ) -> str:
        return self._reports.publish(
            report,
            currentness,
            expected_current,
            after_publication,
            expected_package=expected_package,
            bound=self,
        )

    def get(self, report_id: str) -> str:
        return self._reports.get(report_id)

    def publish_proposal(self, proposal_set: ProposalSet) -> str:
        return self._proposal_sets.publish_proposal(proposal_set)

    def get_proposal(self, proposal_set_id: str) -> str:
        return self._proposal_sets.get_proposal(proposal_set_id)

    def admit_review_case(self, proposal_set: ProposalSet) -> str:
        return self._review_cases.admit_review_case(proposal_set)

    def review_case_exists(self, review_case_id: str) -> bool:
        return self._review_cases.review_case_exists(review_case_id)

    def get_review_case(self, review_case_id: str) -> str:
        return self._review_cases.get_review_case(review_case_id)

    def read_staged_cover_preparation(
        self, action: PipelineAction
    ) -> tuple[str, str, bool] | None:
        """Read only the claimed staged cover facts before F22 package cutover."""
        if action.review_case_id is None:
            raise MetadataReportError("review case is unavailable")
        try:
            self._require_staged_shadow_projection_current(action.review_case_id, action)
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
            if case.review_case_id != action.review_case_id or case.report_id != report.report_id:
                raise MetadataReportError("review case is unavailable")
            cover = next(
                (proposal for proposal in case.proposals
                 if proposal["destination"] == "cover"),
                None,
            )
            # Every ReviewCase proposal carries a "result" object; the
            # surrounding except clause turns any drift into MetadataReportError.
            if cover is None or cast("dict[str, object]", cover["result"])["outcome"] == "ineligible":
                return None
            return report.to_json(), str(cover["proposal_id"]), False
        except (sqlite3.Error, ReviewCaseValueError, ReportValueError, KeyError, TypeError) as error:
            raise MetadataReportError("review case is unavailable") from error

    def get_review_case_report(self, review_case_id: str) -> str:
        return self._review_cases.get_review_case_report(review_case_id)

    def review_case_ready_at(self, review_case_id: str) -> int | None:
        return self._review_preparation.review_case_ready_at(review_case_id)

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
                automatic_attention_destinations=self._automatic_attention_destinations(review_case_id),
                automatic_safety_suspended_destinations=self._automatic_attention_destinations(review_case_id),
                shadow_abstention_destinations=self._shadow_abstention_destinations(review_case_id),
                automatic_override_blocked_destinations=self._automatic_override_blocked_destinations(review_case_id),
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
                    else self.cover_consideration_history(
                        review_case_id, history_limit
                    )
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
                ]
            ] = []
            for case_document, report_document in rows:
                if case_document is None and '"schema_version":"review-terminal-work-v1"' in report_document:
                    projected_rows.append((case_document, report_document, (), (), (), (), None))
                    continue
                report = MetadataReport.from_json(report_document)
                terminal = self._connection.execute(
                    "SELECT work_id,stage,attempt,recovery_attempt,state,failure_code,failure_root "
                    "FROM review_preparation_work WHERE connection_name=? AND library_name=? AND item_id=? "
                    "ORDER BY updated_at DESC,work_id DESC LIMIT 1",
                    (report.target.connection, report.target.library, report.target.item),
                ).fetchone()
                terminal_document = None if (
                    terminal is None
                    or terminal[4] != "failed"
                    or terminal[5] is None
                ) else _canonical_json({
                    "attempt": terminal[2], "connection": report.target.connection,
                    "failure_root": terminal[6], "item": report.target.item,
                    "lane": terminal[1], "library": report.target.library,
                    "recovery_attempt": terminal[3], "schema_version": "review-terminal-work-v1",
                    "status": terminal[5], "work_id": terminal[0],
                })
                case_id = None if case_document is None else ReviewCase.from_json(case_document).review_case_id
                projected_rows.append((
                    case_document, report_document,
                    () if case_id is None else self.get_review_case_decisions(case_id),
                    () if case_id is None else self._automatic_attention_destinations(case_id),
                    () if case_id is None else self._field_effect_capability_suspended_destinations(case_id),
                    () if case_id is None else self._shadow_abstention_destinations(case_id),
                    terminal_document,
                ))
            projected = tuple(projected_rows)
            total, attention_total = self.count_current_review_cases(search)
            snapshot = ReviewLibrarySnapshot(
                rows=projected,
                total=total,
                attention_total=attention_total,
            )
        return snapshot

    def _automatic_attention_destinations(self, review_case_id: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT DISTINCT json_extract(outcome.value,'$.destination') FROM shadow_decision_outcomes shadow, "
            "json_each(shadow.document,'$.outcomes') outcome, automatic_safety_state safety "
            "WHERE shadow.review_case_id=? AND json_extract(outcome.value,'$.policy_destination')='automatic' AND ("
            "safety.paused=1 OR safety.global_suspension IS NOT NULL OR EXISTS(SELECT 1 FROM json_each(safety.category_suspensions_document) category WHERE json_extract(category.value,'$[0]')=json_extract(outcome.value,'$.policy_category'))) ORDER BY 1",
            (review_case_id,),
        ).fetchall()
        if any(type(row[0]) is not str for row in rows):
            raise MetadataReportError("review attention is unavailable")
        return tuple(row[0] for row in rows)

    def _shadow_abstention_destinations(self, review_case_id: str) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT DISTINCT json_extract(outcome.value,'$.destination') FROM shadow_decision_outcomes shadow, "
            "json_each(shadow.document,'$.outcomes') outcome WHERE shadow.review_case_id=? "
            "AND json_extract(outcome.value,'$.disposition')='abstention' ORDER BY 1",
            (review_case_id,),
        ).fetchall()
        if any(type(row[0]) is not str for row in rows):
            raise MetadataReportError("review attention is unavailable")
        return tuple(row[0] for row in rows)

    def _automatic_override_blocked_destinations(
        self, review_case_id: str,
    ) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT DISTINCT automatic.destination FROM automatic_decisions automatic "
            "JOIN field_effects effect ON effect.decision_id=automatic.decision_id "
            "WHERE automatic.review_case_id=? AND effect.state IN "
            "('claimed','applying','reconciling','verified') ORDER BY automatic.destination",
            (review_case_id,),
        ).fetchall()
        if any(type(row[0]) is not str for row in rows):
            raise MetadataReportError("review attention is unavailable")
        return tuple(row[0] for row in rows)

    def _field_effect_capability_suspended_destinations(
        self, review_case_id: str,
    ) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT json_extract(result.value,'$.destination') "
            "FROM current_review_case_lineages c JOIN review_cases rc "
            "ON rc.review_case_id=c.review_case_id,"
            "json_each(rc.document,'$.results') result "
            "WHERE rc.review_case_id=? AND EXISTS(SELECT 1 "
            "FROM field_effect_suspensions suspension "
            "WHERE suspension.connection_name=c.connection_name AND ("
            "(suspension.scope='connection' AND suspension.destination='') OR "
            "(suspension.scope='destination' AND suspension.destination="
            "json_extract(result.value,'$.destination')))) ORDER BY 1",
            (review_case_id,),
        ).fetchall()
        if any(type(row[0]) is not str for row in rows):
            raise MetadataReportError("review attention is unavailable")
        return tuple(row[0] for row in rows)

    def _field_effect_capability_suspension_causes(
        self, review_case_id: str,
    ) -> tuple[tuple[str, ReviewFieldCause], ...]:
        rows = self._connection.execute(
            "SELECT json_extract(result.value,'$.destination'),(SELECT "
            "suspension.scope FROM field_effect_suspensions suspension "
            "WHERE suspension.connection_name=c.connection_name AND (("
            "suspension.scope='connection' AND suspension.destination='') OR "
            "(suspension.scope='destination' AND suspension.destination="
            "json_extract(result.value,'$.destination'))) ORDER BY CASE "
            "suspension.scope WHEN 'connection' THEN 0 ELSE 1 END LIMIT 1) "
            "FROM current_review_case_lineages c JOIN review_cases rc "
            "ON rc.review_case_id=c.review_case_id,json_each(rc.document,'$.results') result "
            "WHERE rc.review_case_id=? AND EXISTS(SELECT 1 FROM "
            "field_effect_suspensions suspension WHERE "
            "suspension.connection_name=c.connection_name AND (("
            "suspension.scope='connection' AND suspension.destination='') OR "
            "(suspension.scope='destination' AND suspension.destination="
            "json_extract(result.value,'$.destination')))) ORDER BY 1",
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
                # ReviewAttentionSnapshot.rows is declared as a 4-tuple, but
                # this repository has always produced (and review_web's
                # attention() reader has always consumed, via its len(row)
                # guards) the two extra automatic-outcome and cause columns.
                # The declaration in application/review_web.py is the stale
                # half of that pair; this cast records the real shape.
                rows=cast(
                    "tuple[tuple[str, str, str, int], ...]",
                    self.list_current_attention_documents(
                        search, limit, offset
                    ),
                ),
                total=self.count_current_attention_fields(search),
            )
        return snapshot

    def list_current_review_case_documents(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> tuple[tuple[str | None, str], ...]:
        return self._review_cases.list_current_review_case_documents(
            search, limit, offset, attention
        )

    def count_current_review_cases(
        self, search: str | None
    ) -> tuple[int, int]:
        return self._review_cases.count_current_review_cases(search)

    def list_current_attention_documents(
        self, search: str | None, limit: int, offset: int
    ) -> tuple[tuple[str, str, str, int, str | None, str | None], ...]:
        return self._review_cases.list_current_attention_documents(
            search, limit, offset
        )

    def count_current_attention_fields(self, search: str | None) -> int:
        return self._review_cases.count_current_attention_fields(search)

    def begin_login(
        self, now: int, window_seconds: int, maximum_failures: int
    ) -> bool:
        return self._review_sessions.begin_login(
            now, window_seconds, maximum_failures
        )

    def shadow_posture(self) -> str:
        return self._metadata_policy.shadow_posture()

    def enter_shadow_preview(self, updated_at: int) -> str:
        """Enter Preview and reconcile the exact-current cohort when leaving automatic."""
        if type(updated_at) is not int or updated_at < 0:
            raise MetadataReportError("shadow posture request is invalid")
        transaction = self._transaction("enter_shadow_preview")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT active_policy_id,generation,posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            if row is None or row[2] not in {"review", "preview", "automatic"}:
                raise MetadataReportError("shadow posture is unavailable")
            # Automatic decisions must never survive a posture change without
            # re-projecting the exact-current cohort through the established
            # F09 transition/work protocol.  A fresh generation makes both the
            # bounded progress signal and the later confirmation check durable.
            if row[2] == "automatic":
                active_document = self._connection.execute(
                    "SELECT document FROM metadata_policy_drafts WHERE policy_id=?",
                    (row[0],),
                ).fetchone()
                if active_document is None or type(active_document[0]) is not str:
                    raise MetadataReportError("shadow posture reconciliation is unavailable")
                active_profile = MetadataPolicyProfile.from_data(json.loads(active_document[0]))
                # A policy with no automatic destination has no affected
                # automatic cohort.  Preserve the ordinary Preview workflow.
                affected = (
                    self._connection.execute("SELECT 1 FROM automatic_decisions LIMIT 1").fetchone()
                    if any(value == "automatic" for _, value in active_profile.destinations)
                    else None
                )
                # There is no prior automatic authority to reconcile until a
                # current cohort has actually received an automatic decision.
                # Initial Preview remains a harmless report-only entry.
                if affected is not None:
                    resolutions = tuple(self._connection.execute(
                        "SELECT library_name,resolved_policy_id,document FROM metadata_policy_resolutions WHERE generation=? ORDER BY library_name",
                        (row[1],),
                    ).fetchall())
                    if not resolutions:
                        profile = active_profile
                        libraries = self._connection.execute(
                            "SELECT DISTINCT c.library_name " + _CURRENT_REVIEW_FROM_SQL + " ORDER BY c.library_name"
                        ).fetchall()
                        resolutions = tuple(
                            (library[0], resolved.resolved_id, resolved.canonical_json)
                            for library in libraries
                            for resolved in (resolve_policy(profile, library[0]),)
                        )
                    self.activate_metadata_policy(
                        row[0], row[0], resolutions, updated_at, reconcile_current=True
                    )
            self._connection.execute(
                "UPDATE metadata_policy_state SET posture='preview' WHERE singleton=1"
            )
            transaction.commit()
            return "preview"
        except (sqlite3.Error, MetadataReportError) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("shadow posture persistence failed") from error

    def activate_qualified_automatic(self, updated_at: int) -> str:
        return self._metadata_policy.activate_qualified_automatic(updated_at)

    def _schedule_current_shadow_work(
        self, case: ReviewCase, updated_at: int
    ) -> None:
        self._review_preparation._schedule_current_shadow_work(case, updated_at)

    def _persisted_shadow_sources(
        self,
        review_case_id: str,
        *,
        include_policy_ignored: bool = False,
    ) -> tuple[ReviewCase, ShadowReviewPacket, MetadataReport, ProposalSet]:
        case_row = self._connection.execute(
            "SELECT document FROM review_cases WHERE review_case_id=?",
            (review_case_id,),
        ).fetchone()
        if case_row is None or type(case_row[0]) is not str:
            raise MetadataReportError("shadow outcome source is unavailable")
        case = ReviewCase.from_json(case_row[0])
        proposal_row = self._connection.execute(
            "SELECT proposal_set_id,report_id,policy_version,document FROM proposal_sets "
            "WHERE proposal_set_id=?",
            (case.proposal_set_id,),
        ).fetchone()
        report_row = self._connection.execute(
            "SELECT report_id,connection_name,library_name,item_id,file_identity,"
            "content_digest,schema_version,document_digest,document "
            "FROM metadata_reports WHERE report_id=?",
            (case.report_id,),
        ).fetchone()
        if proposal_row is None or report_row is None:
            raise MetadataReportError("shadow outcome source is unavailable")
        proposal = ProposalSet.from_json(_validate_proposal_row(proposal_row))
        report = MetadataReport.from_json(_validate_report_row(report_row))
        return (
            case,
            ShadowReviewPacket.create(
                case,
                proposal,
                report,
                include_policy_ignored=include_policy_ignored,
            ),
            report,
            proposal,
        )

    def _persisted_shadow_packet_source(
        self,
        review_case_id: str,
        *,
        include_policy_ignored: bool = False,
    ) -> tuple[ReviewCase, ShadowReviewPacket]:
        case, packet, _, _ = self._persisted_shadow_sources(
            review_case_id, include_policy_ignored=include_policy_ignored
        )
        return case, packet

    def _validate_shadow_outcome_row(
        self, row: tuple[object, ...] | sqlite3.Row
    ) -> tuple[str, int]:
        if len(row) != 8:
            raise MetadataReportError("shadow outcome persistence is invalid")
        # The eight-column shadow outcome projection stores its document last.
        outcome = ShadowOutcomeSet.from_json(cast(str, row[7]))
        if (
            type(row[6]) is not int
            or row[6] < 0
            or (
                outcome.outcome_set_id,
                outcome.review_case_id,
                outcome.report_id,
                outcome.proposal_set_id,
                outcome.resolved_policy_id,
                outcome.source_decision_set_id,
            )
            != row[:6]
        ):
            raise MetadataReportError("shadow outcome persistence is invalid")
        case, packet, report, proposal = self._persisted_shadow_sources(
            outcome.review_case_id, include_policy_ignored=True
        )
        library = case.target["library"]
        if type(library) is not str:
            raise MetadataReportError("shadow outcome persistence is invalid")
        resolution = self._connection.execute(
            "SELECT document FROM metadata_policy_resolutions "
            "WHERE generation=? AND library_name=?",
            (outcome.policy_generation, library),
        ).fetchone()
        if resolution is None and outcome.policy_generation == 1:
            draft = self._connection.execute(
                "SELECT document FROM metadata_policy_drafts WHERE policy_id=?",
                (outcome.policy_profile_id,),
            ).fetchone()
            if draft is None or type(draft[0]) is not str:
                raise MetadataReportError("shadow outcome persistence is invalid")
            policy = resolve_policy(
                MetadataPolicyProfile.from_data(json.loads(draft[0])), library
            )
        else:
            if resolution is None or type(resolution[0]) is not str:
                raise MetadataReportError("shadow outcome persistence is invalid")
            policy = ResolvedMetadataPolicy.from_data(json.loads(resolution[0]))
            if policy.canonical_json != resolution[0]:
                raise MetadataReportError("shadow outcome persistence is invalid")
        if (
            policy.profile_id != outcome.policy_profile_id
            or policy.resolved_id != outcome.resolved_policy_id
        ):
            raise MetadataReportError("shadow outcome persistence is invalid")
        expected = ShadowOutcomeSet.create(
            case,
            decide_shadow_packet(packet),
            policy,
            outcome.policy_generation,
            report,
            proposal,
        )
        if expected.to_json() != row[7]:
            raise MetadataReportError("shadow outcome persistence is invalid")
        return expected.to_json(), row[6]

    def persist_shadow_outcome(
        self,
        review_case_id: str,
        evaluated_at: int,
        *,
        staged_action: PipelineAction | None = None,
    ) -> str:
        """Atomically publish or replay one exact Preview decision set."""
        if (
            type(review_case_id) is not str
            or re.fullmatch(r"review_case_[0-9a-f]{64}", review_case_id) is None
            or type(evaluated_at) is not int
            or evaluated_at < 0
            or (staged_action is not None and type(staged_action) is not PipelineAction)
        ):
            raise MetadataReportError("shadow outcome request is invalid")
        case_for_recovery: ReviewCase | None = None
        transaction = self._transaction("persist_shadow_outcome")
        try:
            transaction.begin()
            raw_case = self._connection.execute(
                "SELECT document FROM review_cases WHERE review_case_id=?",
                (review_case_id,),
            ).fetchone()
            if raw_case is None:
                raise MetadataReportError("shadow outcome source is unavailable")
            case_for_recovery = ReviewCase.from_json(raw_case[0])
            if staged_action is not None:
                self._require_staged_shadow_projection_current(
                    review_case_id, staged_action
                )
            else:
                self._require_shadow_case_current(review_case_id)
            state = self._connection.execute(
                "SELECT s.generation,s.posture,d.document FROM metadata_policy_state s "
                "JOIN metadata_policy_drafts d ON d.policy_id=s.active_policy_id "
                "WHERE s.singleton=1"
            ).fetchone()
            if state is None or state[1] not in {"review", "preview", "automatic"}:
                raise MetadataReportError("shadow posture is unavailable")
            if state[1] == "review":
                transaction.commit()
                return "inactive"
            case, packet, report, proposal = self._persisted_shadow_sources(
                review_case_id, include_policy_ignored=True
            )
            library = case.target["library"]
            if type(library) is not str:
                raise MetadataReportError("shadow outcome source is invalid")
            policy = self.current_resolved_metadata_policy(library)
            if policy is None and state[0] == 1:
                policy = resolve_policy(
                    MetadataPolicyProfile.from_data(json.loads(state[2])), library
                )
            if policy is None:
                raise MetadataReportError("shadow policy is unavailable")
            if staged_action is not None and (
                staged_action.report_id != report.report_id
                or staged_action.proposal_set_id != proposal.proposal_set_id
                or staged_action.review_case_id != case.review_case_id
                or proposal.resolved_policy_id != policy.resolved_id
            ):
                raise ReviewAdmissionStaleError
            outcome = ShadowOutcomeSet.create(
                case, decide_shadow_packet(packet), policy, state[0], report, proposal
            )
            serialized = outcome.to_json()
            existing = self._connection.execute(
                "SELECT outcome_set_id,review_case_id,report_id,proposal_set_id,"
                "resolved_policy_id,source_decision_set_id,evaluated_at,document "
                "FROM shadow_decision_outcomes WHERE outcome_set_id=?",
                (outcome.outcome_set_id,),
            ).fetchone()
            if existing is not None:
                stored, _ = self._validate_shadow_outcome_row(existing)
                if stored != serialized:
                    raise MetadataReportError("shadow outcome identity conflicts")
            if existing is None:
                self._connection.execute(
                    "INSERT INTO shadow_decision_outcomes(outcome_set_id,review_case_id,"
                    "report_id,proposal_set_id,resolved_policy_id,source_decision_set_id,"
                    "evaluated_at,document) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        outcome.outcome_set_id,
                        outcome.review_case_id,
                        outcome.report_id,
                        outcome.proposal_set_id,
                        outcome.resolved_policy_id,
                        outcome.source_decision_set_id,
                        evaluated_at,
                        serialized,
                    ),
                )
            # Preview remains an immutable observation even once it has become
            # the input to decision-only automatic handling.  Decisions are
            # derived inside this same exact-current transaction, never by a
            # later queue or apply worker.
            if state[1] == "automatic":
                attempted_categories: set[str] = set()
                while True:
                    admission = self._transaction("automatic_admission")
                    admission.begin()
                    try:
                        self._record_automatic_outcomes(outcome, evaluated_at)
                    except AutomaticDecisionError as error:
                        # Keep the outer report/case/outcome projection intact.
                        # A category defect is suspended, then unaffected
                        # categories are retried from the same exact outcome.
                        admission.rollback()
                        current = self.automatic_safety_state()
                        message = str(error)
                        category = (
                            message.removeprefix("category:")
                            if message.startswith("category:") else None
                        )
                        if category:
                            categories = dict(current.category_suspensions)
                            categories[category] = "automatic_admission_failed"
                            safety = AutomaticSafetyState(
                                current.paused, current.global_suspension,
                                tuple(sorted(categories.items())),
                            )
                            self._set_automatic_safety_state(safety, evaluated_at)
                            if category not in attempted_categories:
                                attempted_categories.add(category)
                                continue
                        else:
                            safety = AutomaticSafetyState(
                                current.paused, "automatic_authority_failed",
                                current.category_suspensions,
                            )
                            self._set_automatic_safety_state(safety, evaluated_at)
                        break
                    else:
                        admission.commit()
                        break
            if self._binding is not None:
                self._binding.require_current()
            transaction.commit()
            return serialized
        except ReviewAdmissionStaleError:
            try:
                if staged_action is None and case_for_recovery is not None:
                    self._schedule_current_shadow_work(
                        case_for_recovery, evaluated_at
                    )
                transaction.commit()
                return "stale"
            except (sqlite3.Error, MetadataReportError) as error:
                transaction.rollback()
                if isinstance(error, MetadataReportError):
                    raise
                raise MetadataReportError(
                    "shadow outcome persistence failed"
                ) from error
        except (
            sqlite3.Error,
            MetadataReportError,
            MetadataPolicyError,
            ReviewCaseValueError,
            ShadowOutcomeError,
        ) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("shadow outcome persistence failed") from error

    def _record_automatic_outcomes(
        self, outcome: ShadowOutcomeSet, occurred_at: int
    ) -> None:
        """Append eligible dispositions, leaving paused/suspended work human."""
        safety = self.automatic_safety_state()
        automatic_dispositions = {"candidate", "keep_current", "ignore"}
        required_bundles = {
            # Subtitle remains a closed witness for the selected title
            # combination. Independently qualified series decisions use their
            # own category and preserve the proposal's combination evidence.
            "title_layout": ("title",),
            "date_precision": ("publishedDate", "publishedYear"),
        }
        bundle_ids: dict[str, str] = {}
        for category, destinations in required_bundles.items():
            if not safety.permits(category):
                continue
            members = tuple(
                field for field in outcome.outcomes
                if field.policy_category == category and field.destination in destinations
            )
            all_automatic = bool(members) and all(
                field.disposition in automatic_dispositions for field in members
            )
            evidence_ids = tuple(
                field.combination_ids
                if category == "title_layout"
                else field.citation_ids
                for field in members
            )
            coherent = (
                len(members) == len(destinations)
                and {field.destination for field in members} == set(destinations)
                and all_automatic
                and len(set(evidence_ids)) == 1
                and bool(evidence_ids[0])
            )
            if not coherent:
                # A fully automatic but incoherent protected tuple is an
                # authority defect, unlike an ordinary abstention/conflict.
                if len(members) == len(destinations) and all_automatic:
                    raise AutomaticDecisionError("category:" + category)
                continue
            bundle_ids[category] = "automatic_bundle_" + hashlib.sha256(
                _canonical_json({
                    "category": category,
                    "evidence_ids": list(evidence_ids[0]),
                }).encode("utf-8")
            ).hexdigest()
        decisions: list[AutomaticDecision] = []
        for field in outcome.outcomes:
            if (
                field.disposition not in automatic_dispositions
                or field.policy_category is None
                or field.policy_option is None
                or field.policy_destination != "automatic"
                or not safety.permits(field.policy_category)
                or (field.policy_category in required_bundles and field.policy_category not in bundle_ids)
            ):
                continue
            try:
                decision = AutomaticDecision.create(
                    review_case_id=outcome.review_case_id,
                    report_id=outcome.report_id,
                    proposal_set_id=outcome.proposal_set_id,
                    proposal_id=field.proposal_id,
                    target_fingerprint=outcome.target_fingerprint,
                    destination=field.destination,
                    outcome=field.disposition,
                    candidate_id=field.candidate_id,
                    resolved_policy_id=outcome.resolved_policy_id,
                    policy_generation=outcome.policy_generation,
                    rule_id=field.rule_id,
                    policy_category=field.policy_category,
                    policy_option=field.policy_option,
                    qualification_manifest_sha256=qualification_manifest_sha256(
                        field.policy_category, field.policy_option
                    ),
                    source_outcome_id=field.outcome_id,
                    bundle_id=bundle_ids.get(field.policy_category),
                    occurred_at=occurred_at,
                )
            except AutomaticDecisionError as error:
                # Construction and identity/source/manifest binding defects
                # invalidate automatic authority as a whole.  Category
                # suspension is reserved for the explicit protected-bundle
                # coherence check above.
                raise AutomaticDecisionError("authority") from error
            decisions.append(decision)
        # A normal human winner or replay is not an authority failure.  Remove
        # its whole bundle before any insert, so a bundle is all-or-none.
        blocked_bundles: set[str] = set()
        for decision in decisions:
            conflict = self._connection.execute(
                "SELECT 1 FROM human_decisions WHERE review_case_id=? AND destination=? "
                "UNION ALL SELECT 1 FROM automatic_decisions WHERE review_case_id=? AND destination=?",
                (decision.review_case_id, decision.destination, decision.review_case_id, decision.destination),
            ).fetchone()
            if conflict is not None and decision.bundle_id is not None:
                blocked_bundles.add(decision.bundle_id)
        decisions = [decision for decision in decisions if decision.bundle_id not in blocked_bundles]
        # Complete validation precedes every insert: an authority defect cannot
        # leave a subset of a multi-field bundle durable.
        for decision in decisions:
            existing = self._connection.execute(
                "SELECT document FROM automatic_decisions WHERE idempotency_digest=?",
                (decision.idempotency_digest,),
            ).fetchone()
            if existing is not None:
                # The event timestamp is deliberately not part of the replay
                # key; a later projection retry must reproduce the immutable
                # authority fact rather than manufacture a second decision.
                continue
            conflict = self._connection.execute(
                "SELECT 1 FROM human_decisions WHERE review_case_id=? AND destination=? "
                "UNION ALL SELECT 1 FROM automatic_decisions WHERE review_case_id=? AND destination=?",
                (decision.review_case_id, decision.destination,
                 decision.review_case_id, decision.destination),
            ).fetchone()
            if conflict is not None:
                # Human wins and independent replay are ordinary fallback, not
                # a global safety event.  Bundles were removed as a unit above.
                continue
            self._connection.execute(
                "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,idempotency_digest,occurred_at,document) VALUES(?,?,?,?,?,?)",
                (decision.decision_id, decision.review_case_id, decision.destination,
                 decision.idempotency_digest, decision.occurred_at, decision.to_json()),
            )

    def list_shadow_outcomes(
        self, limit: int
    ) -> tuple[tuple[str, int], ...]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise MetadataReportError("shadow outcome sample is unavailable")
        try:
            rows = self._connection.execute(
                "SELECT outcome_set_id,review_case_id,report_id,proposal_set_id,"
                "resolved_policy_id,source_decision_set_id,evaluated_at,document "
                "FROM shadow_decision_outcomes "
                "ORDER BY evaluated_at DESC,outcome_set_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return tuple(self._validate_shadow_outcome_row(row) for row in rows)
        except (
            sqlite3.Error,
            MetadataPolicyError,
            ReviewCaseValueError,
            ShadowOutcomeError,
            TypeError,
            ValueError,
        ) as error:
            raise MetadataReportError("shadow outcome sample is unavailable") from error

    def create_metadata_policy_draft(self, document: str) -> str:
        return self._metadata_policy.create_metadata_policy_draft(document)

    def get_metadata_policy_draft(self, policy_id: str) -> str:
        return self._metadata_policy.get_metadata_policy_draft(policy_id)

    def active_metadata_policy(self) -> str:
        return self._metadata_policy.active_metadata_policy()

    def current_resolved_policy_id(self, library: str) -> str:
        return self._metadata_policy.current_resolved_policy_id(library)

    def current_resolved_metadata_policy(
        self, library: str
    ) -> ResolvedMetadataPolicy | None:
        return self._metadata_policy.current_resolved_metadata_policy(library)

    def metadata_policy_resolution_for_preview(
        self, library: str
    ) -> ResolvedMetadataPolicy | None:
        return self._metadata_policy.metadata_policy_resolution_for_preview(library)

    def metadata_policy_preview_baseline(
        self, libraries: tuple[str, ...]
    ) -> tuple[str, tuple[tuple[str, ResolvedMetadataPolicy | None], ...]]:
        return self._metadata_policy.metadata_policy_preview_baseline(libraries)

    def bind_initial_metadata_policy_resolution(self, document: str) -> str:
        return self._metadata_policy.bind_initial_metadata_policy_resolution(document)

    def _restart_metadata_policy_work(
        self,
        work_id: str,
        work_stage: str,
        report_id: str,
        updated_at: int,
    ) -> None:
        self._review_preparation._restart_metadata_policy_work(
            work_id, work_stage, report_id, updated_at
        )

    def activate_metadata_policy(
        self,
        policy_id: str,
        expected_active_policy_id: str,
        resolutions: tuple[tuple[str, str, str], ...],
        activated_at: int,
        *,
        reconcile_current: bool = False,
    ) -> PolicyTransition | None:
        return self._metadata_policy.activate_metadata_policy(
            policy_id,
            expected_active_policy_id,
            resolutions,
            activated_at,
            reconcile_current=reconcile_current,
        )

    def metadata_policy_transition(self) -> PolicyTransition | None:
        return self._metadata_policy.metadata_policy_transition()

    def retry_metadata_policy_transition(self, transition_id: str, retried_at: int) -> PolicyTransition:
        return self._metadata_policy.retry_metadata_policy_transition(
            transition_id, retried_at
        )

    def acknowledge_metadata_policy_transition(self, transition_id: str, acknowledged_at: int) -> PolicyTransition:
        return self._metadata_policy.acknowledge_metadata_policy_transition(
            transition_id, acknowledged_at
        )

    def current_unapplied_count(self, library: str) -> int:
        return self._metadata_policy.current_unapplied_count(library)

    def current_policy_impact_pairs(
        self, library: str, limit: int
    ) -> tuple[int, tuple[tuple[MetadataReport, ProposalSet], ...]]:
        return self._metadata_policy.current_policy_impact_pairs(library, limit)

    def create_review_session(
        self,
        session_digest: str,
        csrf_digest: str,
        created_at: int,
        expires_at: int,
    ) -> None:
        self._review_sessions.create_review_session(
            session_digest, csrf_digest, created_at, expires_at
        )

    def authenticate_review_session(
        self, session_digest: str, now: int
    ) -> tuple[str, str, int]:
        return self._review_sessions.authenticate_review_session(session_digest, now)

    def rotate_review_session_csrf(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        return self._review_sessions.rotate_review_session_csrf(
            session_digest, csrf_digest, now
        )

    def revoke_review_session(
        self, session_digest: str, csrf_digest: str, now: int
    ) -> str:
        # Belongs to the review-session aggregate, and is deliberately still
        # here: `test_sqlite_logout_classification_is_atomic_across_connections`
        # substitutes `_connection` on this repository to interleave a competing
        # logout at the exact statement below. An aggregate holds its own
        # reference to the shared connection, so moving this method would leave
        # the substitution addressing an object the statement never reaches.
        transaction = self._transaction("revoke_review_session")
        try:
            transaction.begin()
            cursor = self._connection.execute(
                "UPDATE review_sessions SET revoked_at=? WHERE session_digest=? AND csrf_digest=? AND revoked_at IS NULL AND expires_at>?",
                (now, session_digest, csrf_digest, now),
            )
            if cursor.rowcount != 1:
                active = self._connection.execute(
                    "SELECT 1 FROM review_sessions WHERE session_digest=? AND revoked_at IS NULL AND expires_at>?",
                    (session_digest, now),
                ).fetchone()
                transaction.rollback()
                return "csrf" if active is not None else "unauthorized"
            transaction.commit()
            return "revoked"
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError("review session is unavailable") from error

    def get_review_case_decisions(
        self, review_case_id: str
    ) -> tuple[str, ...]:
        try:
            envelope = json.loads(self.get_review_case(review_case_id))
            if type(envelope) is not dict or type(envelope.get("case")) is not dict:
                raise MetadataReportError("human decision is unavailable")
            case = ReviewCase.create_from_data(envelope["case"])
            rows = self._connection.execute(
                "SELECT decision_id,proposal_id,target_fingerprint,destination,"
                "actor,outcome,selected_value_document,idempotency_digest,"
                "request_digest,occurred_at,document "
                "FROM human_decisions WHERE review_case_id=? "
                "ORDER BY ordinal",
                (review_case_id,),
            ).fetchall()
            documents = []
            for row in rows:
                decision = HumanDecision.from_json(row[10])
                decision.validate_for(case)
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
                    raise MetadataReportError("human decision is invalid")
                documents.append(decision.to_json())
            automatic_rows = self._connection.execute(
                "SELECT document FROM automatic_decisions WHERE review_case_id=? "
                "ORDER BY occurred_at,decision_id",
                (review_case_id,),
            ).fetchall()
            for (document,) in automatic_rows:
                decision = json.loads(document)
                if (
                    type(decision) is not dict
                    or decision.get("schema_version") != "automatic-decision-v1"
                    or decision.get("origin") != "automatic_policy"
                    or decision.get("review_case_id") != review_case_id
                ):
                    raise MetadataReportError("automatic decision is invalid")
                documents.append(document)
            return tuple(documents)
        except (sqlite3.Error, HumanDecisionError, TypeError) as error:
            raise MetadataReportError("human decision is unavailable") from error

    def current_cover_consideration(
        self, review_case_id: str, replay_digest: str
    ) -> str | None:
        return self._cover_considerations.current_cover_consideration(
            review_case_id, replay_digest
        )

    def current_cover_consideration_document(
        self, review_case_id: str
    ) -> str | None:
        return self._cover_considerations.current_cover_consideration_document(
            review_case_id
        )

    def latest_cover_consideration_document(
        self, review_case_id: str
    ) -> str | None:
        return self._cover_considerations.latest_cover_consideration_document(
            review_case_id
        )

    def atomic_cover_preparation(self, call: Callable[[], object]) -> object:
        return self._cover_considerations.atomic_cover_preparation(call)

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
        return self._cover_considerations.record_cover_consideration(
            document,
            review_case_id=review_case_id,
            report_id=report_id,
            replay_digest=replay_digest,
            supersedes_id=supersedes_id,
            occurred_at=occurred_at,
            preparation_kind=preparation_kind,
        )

    def record_cover_consideration_outcome(
        self,
        consideration_id: str,
        kind: str,
        occurred_at: int,
        decision_id: str | None = None,
    ) -> None:
        self._cover_considerations.record_cover_consideration_outcome(
            consideration_id, kind, occurred_at, decision_id
        )

    def cover_consideration_history(
        self, review_case_id: str, limit: int = 50
    ) -> tuple[dict[str, object], ...]:
        return self._cover_considerations.cover_consideration_history(
            review_case_id, limit
        )

    def find_human_decision(self, idempotency_digest: str) -> str | None:
        try:
            row = self._connection.execute(
                "SELECT decision_id,review_case_id,proposal_id,"
                "target_fingerprint,destination,actor,outcome,"
                "selected_value_document,idempotency_digest,"
                "request_digest,occurred_at,document FROM human_decisions "
                "WHERE idempotency_digest=?",
                (idempotency_digest,),
            ).fetchone()
            if row is None:
                return None
            decision = HumanDecision.from_json(row[11])
            stored_columns = (
                decision.decision_id,
                decision.review_case_id,
                decision.proposal_id,
                decision.target_fingerprint,
                decision.destination,
                decision.actor,
                decision.outcome,
                _canonical_json(decision.selected_value),
                decision.idempotency_digest,
                decision.request_digest,
                decision.occurred_at,
            )
            if (
                stored_columns != row[:11]
                or decision.idempotency_digest != idempotency_digest
            ):
                raise MetadataReportError("human decision is unavailable")
            return decision.to_json()
        except (sqlite3.Error, HumanDecisionError, TypeError) as error:
            raise MetadataReportError("human decision is unavailable") from error

    def record_human_decision(
        self,
        decision: HumanDecision,
        *,
        cover_consideration_id: str | None = None,
    ) -> str:
        transaction = self._transaction("record_human_decision")
        try:
            transaction.begin()
            replay = self._connection.execute(
                "SELECT request_digest,document FROM human_decisions "
                "WHERE idempotency_digest=?",
                (decision.idempotency_digest,),
            ).fetchone()
            if replay is not None:
                if replay[0] != decision.request_digest:
                    raise HumanDecisionError("idempotency_conflict")
                try:
                    stored = HumanDecision.from_json(replay[1])
                except HumanDecisionError as error:
                    raise MetadataReportError(
                        "human decision persistence failed"
                    ) from error
                if (
                    stored.idempotency_digest != decision.idempotency_digest
                    or stored.request_digest != replay[0]
                ):
                    raise MetadataReportError("human decision persistence failed")
                self._cover_considerations._record_cover_decision_outcome(
                    stored, cover_consideration_id
                )
                transaction.commit()
                return stored.to_json()
            case_envelope = json.loads(self.get_review_case(decision.review_case_id))
            if (
                type(case_envelope) is not dict
                or case_envelope.get("current") is not True
                or type(case_envelope.get("case")) is not dict
                or not self._review_case_accepts_authority(
                    decision.review_case_id
                )
            ):
                raise HumanDecisionError("stale")
            case = ReviewCase.create_from_data(case_envelope["case"])
            decision.validate_for(case)
            previous = self._connection.execute(
                "SELECT decision_id,'human' FROM human_decisions "
                "WHERE review_case_id=? AND destination=? ORDER BY ordinal DESC LIMIT 1",
                (decision.review_case_id, decision.destination),
            ).fetchone()
            if previous is None:
                previous = self._connection.execute(
                    "SELECT decision_id,'automatic' FROM automatic_decisions "
                    "WHERE review_case_id=? AND destination=?",
                    (decision.review_case_id, decision.destination),
                ).fetchone()
            automatic = self._connection.execute(
                "SELECT decision_id FROM automatic_decisions "
                "WHERE review_case_id=? AND destination=?",
                (decision.review_case_id, decision.destination),
            ).fetchone()
            if previous is not None:
                effect = self._connection.execute(
                    "SELECT 1 FROM field_effects WHERE decision_id=? AND state NOT IN "
                    "('planned','retryable','stale','superseded','abandoned') LIMIT 1",
                    (previous[0],),
                ).fetchone()
                if effect is not None:
                    raise HumanDecisionError("already_decided")
            selected = _canonical_json(decision.selected_value)
            self._connection.execute(
                "INSERT INTO human_decisions("
                "decision_id,review_case_id,proposal_id,target_fingerprint,"
                "destination,actor,outcome,selected_value_document,"
                "idempotency_digest,request_digest,occurred_at,document) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    decision.decision_id,
                    decision.review_case_id,
                    decision.proposal_id,
                    decision.target_fingerprint,
                    decision.destination,
                    decision.actor,
                    decision.outcome,
                    selected,
                    decision.idempotency_digest,
                    decision.request_digest,
                    decision.occurred_at,
                    decision.to_json(),
                ),
            )
            self._cover_considerations._record_cover_decision_outcome(
                decision, cover_consideration_id
            )
            event_id = "human_decision_event_" + hashlib.sha256(
                ("human-decision-event-v1:" + decision.decision_id).encode("utf-8")
            ).hexdigest()
            event_document = _canonical_json(
                {
                    "actor": decision.actor,
                    "decision_id": decision.decision_id,
                    "destination": decision.destination,
                    "event_id": event_id,
                    "kind": "human_decision_recorded",
                    "occurred_at": decision.occurred_at,
                    "outcome": decision.outcome,
                    "review_case_id": decision.review_case_id,
                    "schema_version": "human-decision-event-v1",
                }
            )
            self._connection.execute(
                "INSERT INTO human_decision_events("
                "event_id,decision_id,review_case_id,destination,actor,kind,"
                "occurred_at,document) VALUES(?,?,?,?,?,'human_decision_recorded',?,?)",
                (
                    event_id,
                    decision.decision_id,
                    decision.review_case_id,
                    decision.destination,
                    decision.actor,
                    decision.occurred_at,
                    event_document,
                ),
            )
            if previous is not None:
                self._revoke_field_effect_authority_in_transaction(
                    previous[0], decision.occurred_at, required=False
                )
            if automatic is not None:
                if previous is None or automatic[0] != previous[0]:
                    self._revoke_field_effect_authority_in_transaction(
                        automatic[0], decision.occurred_at, required=False
                    )
                self._set_field_effect_manual_hold(
                    decision.review_case_id,
                    decision.destination,
                    decision.occurred_at,
                    decision.decision_id,
                )
            if self._binding is not None:
                self._binding.require_current()
            transaction.commit()
            return decision.to_json()
        except HumanDecisionError:
            transaction.rollback()
            raise
        except MetadataReportError:
            transaction.rollback()
            raise
        except (
            sqlite3.Error,
            ReviewCaseValueError,
            TypeError,
            ValueError,
        ) as error:
            transaction.rollback()
            raise MetadataReportError("human decision persistence failed") from error

    def _record_automatic_decision(self, decision: AutomaticDecision) -> str:
        """Legacy internal single-row helper; projection admission owns policy classification.

        It is intentionally not a repository command or web/API seam.  The
        only supported admission path is ``persist_shadow_outcome``, which
        validates a complete outcome and applies bundle/race classification
        before any durable insert.
        """
        if type(decision) is not AutomaticDecision:
            raise MetadataReportError("automatic decision persistence failed")
        transaction = self._transaction("_record_automatic_decision")
        try:
            transaction.begin()
            replay = self._connection.execute(
                "SELECT document FROM automatic_decisions WHERE idempotency_digest=?",
                (decision.idempotency_digest,),
            ).fetchone()
            if replay is not None:
                if replay != (decision.to_json(),):
                    raise AutomaticDecisionError("idempotency_conflict")
                transaction.commit()
                return decision.to_json()
            posture = self._connection.execute(
                "SELECT posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            # The persistence seam is intentionally inert until the complete
            # policy-control/suspension composition admits active posture.
            if posture != ("automatic",):
                raise AutomaticDecisionError("unavailable")
            if not self.automatic_safety_state().permits(decision.policy_category):
                raise AutomaticDecisionError("suspended")
            case = json.loads(self.get_review_case(decision.review_case_id))
            if type(case) is not dict or case.get("current") is not True:
                raise AutomaticDecisionError("stale")
            existing = self._connection.execute(
                "SELECT 1 FROM human_decisions WHERE review_case_id=? AND destination=? "
                "UNION ALL SELECT 1 FROM automatic_decisions WHERE review_case_id=? AND destination=?",
                (decision.review_case_id, decision.destination, decision.review_case_id, decision.destination),
            ).fetchone()
            if existing is not None:
                raise AutomaticDecisionError("already_decided")
            outcome = self._connection.execute(
                "SELECT document FROM shadow_decision_outcomes WHERE review_case_id=? AND report_id=? "
                "AND proposal_set_id=? AND resolved_policy_id=?",
                (decision.review_case_id, decision.report_id, decision.proposal_set_id, decision.resolved_policy_id),
            ).fetchone()
            if outcome is None:
                raise AutomaticDecisionError("stale")
            source = ShadowOutcomeSet.from_json(outcome[0])
            field = next((value for value in source.outcomes if value.outcome_id == decision.source_outcome_id), None)
            if field is None or (field.disposition != decision.outcome) or any((field.proposal_id != decision.proposal_id, field.destination != decision.destination, field.rule_id != decision.rule_id, field.policy_category != decision.policy_category, field.policy_option != decision.policy_option)):
                raise AutomaticDecisionError("stale")
            if decision.policy_category in {"title_layout", "date_precision"}:
                required = ("title",) if decision.policy_category == "title_layout" else ("publishedDate", "publishedYear")
                members = tuple(
                    value for value in source.outcomes
                    if value.policy_category == decision.policy_category
                    and value.destination in required
                )
                expected_bundle = "automatic_bundle_" + hashlib.sha256(
                    _canonical_json({"category": decision.policy_category, "combination_ids": list(field.combination_ids)}).encode("utf-8")
                ).hexdigest()
                if (
                    len(members) != len(required)
                    or {value.destination for value in members} != set(required)
                    or len({value.combination_ids for value in members}) != 1
                    or not field.combination_ids
                    or decision.bundle_id != expected_bundle
                ):
                    raise AutomaticDecisionError("bundle_identity")
            if self.current_resolved_policy_id(json.loads(case["case"])["target"]["library"]) != decision.resolved_policy_id:
                raise AutomaticDecisionError("stale")
            self._connection.execute(
                "INSERT INTO automatic_decisions(decision_id,review_case_id,destination,idempotency_digest,occurred_at,document) VALUES(?,?,?,?,?,?)",
                (decision.decision_id, decision.review_case_id, decision.destination, decision.idempotency_digest, decision.occurred_at, decision.to_json()),
            )
            if self._binding is not None:
                self._binding.require_current()
            transaction.commit()
            return decision.to_json()
        except AutomaticDecisionError:
            transaction.rollback()
            raise
        except (sqlite3.Error, MetadataReportError, ShadowOutcomeError, TypeError, ValueError, json.JSONDecodeError) as error:
            transaction.rollback()
            raise MetadataReportError("automatic decision persistence failed") from error

    def automatic_safety_state(self) -> AutomaticSafetyState:
        try:
            row = self._connection.execute("SELECT paused,global_suspension,category_suspensions_document FROM automatic_safety_state WHERE singleton=1").fetchone()
            if row is None:
                raise MetadataReportError("automatic safety state is unavailable")
            categories = json.loads(row[2])
            return AutomaticSafetyState(bool(row[0]), row[1], tuple(tuple(value) for value in categories))
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError, AutomaticDecisionError) as error:
            raise MetadataReportError("automatic safety state is unavailable") from error

    def set_automatic_safety_state(self, state: AutomaticSafetyState, updated_at: int) -> None:
        if type(state) is not AutomaticSafetyState or type(updated_at) is not int or updated_at < 0:
            raise MetadataReportError("automatic safety state is invalid")
        transaction = self._transaction("set_automatic_safety_state")
        try:
            transaction.begin()
            self._set_automatic_safety_state(state, updated_at)
            transaction.commit()
        except (sqlite3.Error, MetadataReportError) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("automatic safety state is unavailable") from error

    def _set_automatic_safety_state(self, state: AutomaticSafetyState, updated_at: int) -> None:
        """Update safety state inside a caller-owned transaction when present."""
        try:
            changed = self._connection.execute(
                "UPDATE automatic_safety_state SET paused=?,global_suspension=?,category_suspensions_document=?,updated_at=? WHERE singleton=1",
                (int(state.paused), state.global_suspension, _canonical_json([list(value) for value in state.category_suspensions]), updated_at),
            ).rowcount
            if changed != 1:
                raise MetadataReportError("automatic safety state is unavailable")
        except (sqlite3.Error, MetadataReportError) as error:
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError("automatic safety state is unavailable") from error

    def get_review_case_history(
        self, review_case_id: str, limit: int
    ) -> tuple[dict[str, object], ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise MetadataReportError("review history is unavailable")
        try:
            transition_rows = self._connection.execute(
                "SELECT transition_id,checkpoint,kind,reason,from_review_case_id,to_review_case_id "
                "FROM review_case_transitions WHERE from_review_case_id=? OR to_review_case_id=? "
                "ORDER BY checkpoint DESC,transition_id DESC LIMIT ?",
                (review_case_id, review_case_id, limit),
            ).fetchall()
            event_rows = self._connection.execute(
                "SELECT event_id,decision_id,review_case_id,destination,actor,"
                "kind,occurred_at,document FROM human_decision_events "
                "WHERE review_case_id=? ORDER BY occurred_at DESC,event_id DESC LIMIT ?",
                (review_case_id, limit),
            ).fetchall()
            shadow_rows = self._connection.execute(
                "SELECT outcome_set_id,review_case_id,report_id,proposal_set_id,"
                "resolved_policy_id,source_decision_set_id,evaluated_at,document "
                "FROM shadow_decision_outcomes "
                "WHERE review_case_id=? ORDER BY evaluated_at DESC,outcome_set_id DESC LIMIT ?",
                (review_case_id, limit),
            ).fetchall()
            automatic_rows = self._connection.execute(
                "SELECT decision_id,destination,occurred_at,document FROM automatic_decisions "
                "WHERE review_case_id=? ORDER BY occurred_at DESC,decision_id DESC LIMIT ?",
                (review_case_id, limit),
            ).fetchall()
            transitions = [
                {
                    "checkpoint": row[1],
                    "kind": row[2],
                    "reason": row[3],
                    "transition_id": row[0],
                }
                for row in transition_rows
            ]
            events: list[tuple[int, str, dict[str, object]]] = []
            for row in event_rows:
                value = json.loads(row[7])
                if type(value) is not dict or set(value) != {
                    "actor",
                    "decision_id",
                    "destination",
                    "event_id",
                    "kind",
                    "occurred_at",
                    "outcome",
                    "review_case_id",
                    "schema_version",
                } or value["schema_version"] != "human-decision-event-v1" or (
                    value["event_id"],
                    value["decision_id"],
                    value["review_case_id"],
                    value["destination"],
                    value["actor"],
                    value["kind"],
                    value["occurred_at"],
                ) != row[:7]:
                    raise MetadataReportError("review history is invalid")
                events.append((row[6], row[0], value))
            for row in shadow_rows:
                document, evaluated_at = self._validate_shadow_outcome_row(row)
                outcome = ShadowOutcomeSet.from_json(document)
                if (
                    outcome.outcome_set_id != row[0]
                    or outcome.review_case_id != review_case_id
                ):
                    raise MetadataReportError("review history is invalid")
                dispositions: dict[str, int] = {}
                for field in outcome.outcomes:
                    dispositions[field.disposition] = (
                        dispositions.get(field.disposition, 0) + 1
                    )
                events.append(
                    (
                        evaluated_at,
                        row[0],
                        {
                            "authority": "none",
                            "dispositions": dispositions,
                            "kind": "shadow_preview_recorded",
                            "occurred_at": evaluated_at,
                            "origin": "policy_preview",
                            "outcome_set_id": row[0],
                            "review_case_id": review_case_id,
                            "schema_version": "shadow-preview-history-event-v1",
                        },
                    )
                )
            for decision_id, destination, occurred_at, document in automatic_rows:
                value = json.loads(document)
                if (
                    type(value) is not dict
                    or value.get("schema_version") != "automatic-decision-v1"
                    or value.get("origin") != "automatic_policy"
                    or value.get("decision_id") != decision_id
                    or value.get("review_case_id") != review_case_id
                    or value.get("destination") != destination
                    or value.get("occurred_at") != occurred_at
                ):
                    raise MetadataReportError("review history is invalid")
                events.append(
                    (occurred_at, decision_id, {
                        "decision_id": decision_id,
                        "destination": destination,
                        "kind": "automatic_decision_recorded",
                        "occurred_at": occurred_at,
                        "origin": "automatic_policy",
                        "outcome": value["outcome"],
                        "review_case_id": review_case_id,
                        "schema_version": "automatic-decision-history-event-v1",
                    })
                )
            ordered_events = [
                value for _, _, value in sorted(events, reverse=True)
            ]
            return tuple((ordered_events + transitions)[:limit])
        except (
            sqlite3.Error,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ShadowOutcomeError,
        ) as error:
            raise MetadataReportError("review history is unavailable") from error

    def preview_shadow_packet(self, review_case_id: str) -> ShadowReviewPacket:
        """Project the exact provider-independent packet without durable state."""
        try:
            return self._shadow_packet(review_case_id)
        except MetadataReportError:
            raise
        except (
            sqlite3.Error,
            ReviewCaseValueError,
            ShadowReviewValueError,
        ) as error:
            raise MetadataReportError("shadow review preview failed") from error

    def _shadow_packet(self, review_case_id: str) -> ShadowReviewPacket:
        return self._shadow_packet_source(review_case_id)[1]

    def _shadow_packet_source(
        self,
        review_case_id: str,
        *,
        include_policy_ignored: bool = False,
    ) -> tuple[ReviewCase, ShadowReviewPacket]:
        self._require_shadow_case_current(review_case_id)
        return self._persisted_shadow_packet_source(
            review_case_id,
            include_policy_ignored=include_policy_ignored,
        )

    def _require_shadow_case_current(self, review_case_id: str) -> None:
        reopened = json.loads(self.get_review_case(review_case_id))
        if (
            type(reopened) is not dict
            or reopened.get("current") is not True
            or type(reopened.get("case")) is not dict
            or not self._review_case_accepts_authority(review_case_id)
        ):
            raise ReviewAdmissionStaleError

    def _require_staged_shadow_projection_current(
        self, review_case_id: str, action: PipelineAction
    ) -> None:
        """Admit only the exact claimed staged case to package-internal projection."""
        if action.lineage_checkpoint is None or action.policy_generation is None or self._connection.execute(
            "SELECT 1 FROM review_preparation_work work "
            "JOIN current_review_case_lineages staged ON "
            "staged.connection_name=work.connection_name AND "
            "staged.library_name=work.library_name AND "
            "staged.item_id=work.item_id AND "
            "staged.proposal_set_id=work.proposal_set_id AND "
            "staged.report_id=work.report_id AND "
            "staged.review_case_id=work.review_case_id "
            "JOIN current_metadata_reports report ON "
            "report.connection_name=staged.connection_name AND "
            "report.library_name=staged.library_name AND "
            "report.item_id=staged.item_id AND "
            "report.report_id=staged.report_id "
            "JOIN metadata_policy_state policy ON policy.singleton=1 "
            "WHERE work.work_id=? AND work.stage='projection' "
            "AND work.state='running' AND work.attempt=? "
            "AND work.connection_name=? AND work.library_name=? "
            "AND work.item_id=? AND work.report_id=? "
            "AND work.proposal_set_id=? AND work.review_case_id=? "
            "AND staged.checkpoint=? AND policy.generation=?",
            (
                action.action_id, action.attempt, action.connection,
                action.library, action.item_id, action.report_id,
                action.proposal_set_id, review_case_id, action.lineage_checkpoint,
                action.policy_generation,
            ),
        ).fetchone() != (1,):
            raise ReviewAdmissionStaleError

    def _review_case_accepts_authority(self, review_case_id: str) -> bool:
        return self._review_cases._review_case_accepts_authority(review_case_id)

    def current(self, connection: str, library: str, item: str, file_identity: str) -> str:
        document = self.current_optional(connection, library, item, file_identity)
        if document is None:
            raise MetadataReportError("persisted current report is unavailable")
        return document

    def current_review_package(
        self, connection: str, library: str, item: str
    ) -> tuple[str | None, str, str | None, int | None, str] | None:
        return self._review_cases.current_review_package(connection, library, item)

    def current_review_package_report(
        self, connection: str, library: str, item: str
    ) -> str | None:
        return self._review_cases.current_review_package_report(
            connection, library, item
        )

    def pending_provider_recovery_outbox(
        self, *, limit: int = 100,
    ) -> tuple[tuple[object, ...], ...]:
        """Return bounded exact package cutovers awaiting recovery enqueue."""
        return self._recovery_outbox.pending_provider_recovery_outbox(limit=limit)

    def complete_provider_recovery_outbox(
        self, outbox_id: str, *, delivered_at: int,
    ) -> None:
        """Mark one exact cutover delivered only after idempotent enqueue."""
        self._recovery_outbox.complete_provider_recovery_outbox(
            outbox_id, delivered_at=delivered_at
        )

    def current_optional(self, connection: str, library: str, item: str, file_identity: str) -> str | None:
        return self._reports.current_optional(connection, library, item, file_identity)

    def preview_digest(self, report_id: str, artifact_id: str, preview_variant: str) -> str:
        return self._reports.preview_digest(report_id, artifact_id, preview_variant)

    def preview_digest_for_artifact(self, artifact_id: str, preview_variant: str) -> str:
        return self._reports.preview_digest_for_artifact(artifact_id, preview_variant)

    def artifact_is_referenced(self, digest: str) -> bool:
        return self._reports.artifact_is_referenced(digest)

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            if self._binding is not None:
                self._binding.close()


class SQLiteShadowPreviewRepository:
    """Narrow packet reader backed by a no-create, immutable SQLite connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        descriptor: int,
        path: Path,
        identity: tuple[int, int, int, int, int],
    ) -> None:
        self._connection = connection
        self._descriptor = descriptor
        self._path = path
        self._identity = identity
        self._reader = SQLiteMetadataReportRepository(connection)
        self._closed = False

    def _require_current(self) -> None:
        try:
            named = self._path.lstat()
            opened = os.fstat(self._descriptor)
        except OSError as error:
            raise MetadataReportError("database authority changed") from error
        named_identity = (
            named.st_dev,
            named.st_ino,
            named.st_size,
            named.st_mtime_ns,
            named.st_ctime_ns,
        )
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        if (
            not stat.S_ISREG(named.st_mode)
            or named_identity != self._identity
            or opened_identity != self._identity
        ):
            raise MetadataReportError("database authority changed")
        _reject_database_sidecars(self._path)

    def current_unapplied_count(self, library: str) -> int:
        self._require_current()
        value = self._reader.current_unapplied_count(library)
        self._require_current()
        return value

    def active_metadata_policy(self) -> str:
        self._require_current()
        value = self._reader.active_metadata_policy()
        self._require_current()
        return value

    def metadata_policy_resolution_for_preview(
        self, library: str
    ) -> ResolvedMetadataPolicy | None:
        self._require_current()
        value = self._reader.metadata_policy_resolution_for_preview(library)
        self._require_current()
        return value

    def metadata_policy_preview_baseline(
        self, libraries: tuple[str, ...]
    ) -> tuple[str, tuple[tuple[str, ResolvedMetadataPolicy | None], ...]]:
        self._require_current()
        value = self._reader.metadata_policy_preview_baseline(libraries)
        self._require_current()
        return value

    def current_policy_impact_pairs(
        self, library: str, limit: int
    ) -> tuple[int, tuple[tuple[MetadataReport, ProposalSet], ...]]:
        self._require_current()
        value = self._reader.current_policy_impact_pairs(library, limit)
        self._require_current()
        return value

    def preview_shadow_packet(self, review_case_id: str) -> ShadowReviewPacket:
        if self._closed:
            raise MetadataReportError("database is unavailable")
        self._require_current()
        transaction = TransactionScope(
            self._connection, "preview_shadow_packet", deferred=True
        )
        try:
            transaction.begin()
            packet = self._reader._shadow_packet(review_case_id)
            transaction.commit()
            self._require_current()
            return packet
        except MetadataReportError:
            transaction.rollback()
            raise
        except (
            sqlite3.Error,
            ReviewCaseValueError,
            ShadowReviewValueError,
        ) as error:
            transaction.rollback()
            raise MetadataReportError("shadow decision preview failed") from error

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._connection.close()
        finally:
            os.close(self._descriptor)


def open_shadow_preview_repository(path: Path) -> SQLiteShadowPreviewRepository:
    """Open an exact-current SQLite database without create or write authority."""
    descriptor = -1
    connection: sqlite3.Connection | None = None
    try:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise MetadataReportError("database no-follow access is unavailable")
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow,
        )
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode):
            raise MetadataReportError("database path is invalid")
        identity = (
            information.st_dev,
            information.st_ino,
            information.st_size,
            information.st_mtime_ns,
            information.st_ctime_ns,
        )
        _validate_delete_mode_header(descriptor)
        _reject_database_sidecars(path)
        _validate_existing_read_only(path, descriptor)
        _reject_database_sidecars(path)
        absolute = str(path.absolute())
        uri = f"file:{quote(absolute, safe='/')}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, isolation_level=None)
        _validate_connection_binding(connection, path, descriptor)
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone() != (1,):
            raise MetadataReportError("database query-only mode is unavailable")
        if connection.execute("PRAGMA database_list").fetchall() == []:
            raise MetadataReportError("database connection identity is unavailable")
        _validate_schema(connection)
        _reject_database_sidecars(path)
        current = path.lstat()
        if (
            not stat.S_ISREG(current.st_mode)
            or (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
                current.st_ctime_ns,
            )
            != identity
        ):
            raise MetadataReportError("database authority changed")
        repository = SQLiteShadowPreviewRepository(
            connection, descriptor, path, identity
        )
        connection = None
        descriptor = -1
        return repository
    except MetadataReportError:
        raise
    except (OSError, sqlite3.Error, ValueError) as error:
        raise MetadataReportError("database is unavailable or incompatible") from error
    finally:
        if connection is not None:
            connection.close()
        if descriptor >= 0:
            os.close(descriptor)


def open_repository(path: Path, *, create: bool = True, expected_identity: tuple[int, int] | None = None, cross_thread: bool = False, authority: object | None = None) -> SQLiteMetadataReportRepository:
    created = False
    descriptor = -1
    created_identity: tuple[int, int] | None = None
    connection: sqlite3.Connection | None = None
    binding: _LinuxDatabaseBinding | None = None
    try:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise MetadataReportError("database no-follow access is unavailable")
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | nofollow
        if os.path.lexists(path):
            descriptor = os.open(path, flags)
            information = os.fstat(descriptor)
            if expected_identity is not None and (information.st_dev, information.st_ino) != expected_identity:
                raise MetadataReportError("database authority changed")
            if not stat.S_ISREG(information.st_mode):
                raise MetadataReportError("database path is invalid")
            _validate_delete_mode_header(descriptor)
            if sys.platform.startswith("linux"):
                binding = _LinuxDatabaseBinding.create(path, descriptor, authority)
                descriptor = -1
                _validate_existing_read_only(
                    binding.alias_path, binding.descriptor
                )
                binding.require_current()
            else:
                _reject_database_sidecars(path)
                _validate_existing_read_only(path, descriptor)
                _reject_database_sidecars(path)
        elif expected_identity is not None:
            raise MetadataReportError("database authority changed")
        elif create:
            descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            information = os.fstat(descriptor)
            if not stat.S_ISREG(information.st_mode):
                raise MetadataReportError("database path is invalid")
            created = True
            created_identity = (information.st_dev, information.st_ino)
            if sys.platform.startswith("linux"):
                binding = _LinuxDatabaseBinding.create(path, descriptor, authority)
                descriptor = -1
        else:
            raise MetadataReportError("database is unavailable")
        database_path = binding.alias_path if binding is not None else path
        database_descriptor = (
            binding.descriptor if binding is not None else descriptor
        )
        connection = _connect_bound(
            database_path,
            database_descriptor,
            binding.require_current if binding is not None else None,
            cross_thread=cross_thread,
        )
        connection.create_function(
            "ml_casefold",
            1,
            _review_casefold,
            deterministic=True,
        )
        if binding is not None:
            binding.require_current()
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        if created:
            _create_schema(connection)
        _validate_schema(connection)
        if binding is None:
            os.close(descriptor)
            descriptor = -1
        repository = SQLiteMetadataReportRepository(connection, binding, (information.st_dev, information.st_ino))
        connection = None
        binding = None
        return repository
    except MetadataReportError:
        if connection is not None:
            connection.close()
        if binding is not None:
            try:
                binding.close()
            except MetadataReportError:
                pass
        if created and created_identity is not None:
            _unlink_created(path, created_identity)
        raise
    except (OSError, sqlite3.Error) as error:
        if connection is not None:
            connection.close()
        if binding is not None:
            try:
                binding.close()
            except MetadataReportError:
                pass
        if created and created_identity is not None:
            _unlink_created(path, created_identity)
        raise MetadataReportError("database is unavailable or incompatible") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _review_casefold(value: object) -> str:
    if type(value) is not str:
        raise ValueError("review search value is invalid")
    return value.casefold()


# The schema spine now lives in `schema.py`, but 33 test modules and the
# composition root import these names from here. Re-export rather than rewrite
# the call sites.
__all__ = [
    "SQLiteMetadataReportRepository",
    "SQLiteShadowPreviewRepository",
    "_LinuxDirectoryChain",
    "_provider_recovery_outbox_id",
    "open_repository",
    "open_shadow_preview_repository",
]
