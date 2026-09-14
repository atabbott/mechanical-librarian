"""Clean-state SQLite repository for immutable reports and ProposalSets."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import sys
from _thread import RLock
from collections.abc import Callable
from pathlib import Path
from typing import cast
from urllib.parse import quote

from mechanical_librarian.adapters.persistence.binding import (
    _connect_bound,
    _LinuxDatabaseBinding,
    _LinuxDirectoryChain,
    _PortableDatabaseAuthority,
    _reject_database_sidecars,
    _unlink_created,
    _validate_connection_binding,
    _validate_delete_mode_header,
)
from mechanical_librarian.adapters.persistence.cover_considerations import (
    SQLiteCoverConsiderationRepository,
)
from mechanical_librarian.adapters.persistence.decisions import (
    SQLiteDecisionRepository,
)
from mechanical_librarian.adapters.persistence.description_advice import (
    SQLiteDescriptionAdviceRepository,
)
from mechanical_librarian.adapters.persistence.field_effect_activity import (
    SQLiteFieldEffectActivityRepository,
)
from mechanical_librarian.adapters.persistence.field_effects import (
    SQLiteFieldEffectRepository,
)
from mechanical_librarian.adapters.persistence.metadata_policy import (
    SQLiteMetadataPolicyRepository,
)
from mechanical_librarian.adapters.persistence.owner_settings import OwnerSettingsStore
from mechanical_librarian.adapters.persistence.preparation_presentation import (
    dismiss_preparation_presentation,
    preparation_presentation_snapshot,
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
from mechanical_librarian.adapters.persistence.review_projections import (
    SQLiteReviewProjectionRepository,
)
from mechanical_librarian.adapters.persistence.review_scans import (
    SQLiteReviewScanRepository,
)
from mechanical_librarian.adapters.persistence.reviewer_access import (
    ReviewerAccessStore,
    ReviewerAccessStoreError,
)
from mechanical_librarian.adapters.persistence.schema import (
    _create_schema,
    _provider_recovery_outbox_id,
    _validate_existing_read_only,
    _validate_report_row,
    _validate_schema,
)
from mechanical_librarian.adapters.persistence.setup_state import SetupStateStore
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    TransactionScope,
)
from mechanical_librarian.application.catalogue import (
    CatalogueEntry,
)
from mechanical_librarian.application.metadata_policy import PolicyTransition
from mechanical_librarian.application.metadata_report import (
    MetadataReportError,
    ReportRepository,
)
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    PipelineProgress,
    TargetConnectionCondition,
    is_first_boot_scan_id,
)
from mechanical_librarian.application.review_web import (
    CurrentReviewCaseSnapshot,
    ReviewAttentionSnapshot,
    ReviewLibrarySnapshot,
)
from mechanical_librarian.domain import audiobook_decision_qualification
from mechanical_librarian.domain.automatic_decision import (
    AutomaticDecision,
    AutomaticDecisionError,
    AutomaticSafetyState,
)
from mechanical_librarian.domain.cover_automatic_decision import CoverAutomaticDecision
from mechanical_librarian.domain.description_advice import (
    AuthoredDescriptionDecision,
    DescriptionAdviceBinding,
)
from mechanical_librarian.domain.field_effect import (
    EffectAuthority,
    EffectConsent,
    FieldEffectError,
    FieldEffectOperation,
)
from mechanical_librarian.domain.human_decision import (
    HumanDecision,
)
from mechanical_librarian.domain.metadata_policy import (
    MetadataPolicyError,
    MetadataPolicyProfile,
    ResolvedMetadataPolicy,
    resolve_policy,
)
from mechanical_librarian.domain.metadata_report import (
    MetadataReport,
)
from mechanical_librarian.domain.preparation_presentation import (
    PreparationPresentation,
    PreparationTrigger,
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
        authority: _PortableDatabaseAuthority | None = None,
        *,
        owns_authority: bool = False,
    ) -> None:
        super().__init__(connection)
        self._setup_serialization_lock = RLock()
        self._browse_failure_lock = RLock()
        self._browse_unavailable_exception_classes: tuple[str, ...] = ()
        self._binding = binding
        self._authority = authority
        self._owns_authority = owns_authority
        self.identity = identity
        self._recovery_outbox = SQLiteRecoveryOutboxRepository(connection)
        self._proposal_sets = SQLiteProposalSetRepository(
            connection,
            binding,
            current_resolved_metadata_policy=(
                lambda library: self.current_resolved_metadata_policy(library)
            ),
        )
        self._cover_considerations = SQLiteCoverConsiderationRepository(
            connection,
            revoke_automatic=lambda decision_id, changed_at: (
                self._revoke_field_effect_authority_in_transaction(
                    decision_id, changed_at, required=False
                )
            ),
        )
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
            restart_metadata_policy_work=(
                lambda work_id, work_stage, report_id, updated_at: (
                    self._restart_metadata_policy_work(
                        work_id, work_stage, report_id, updated_at
                    )
                )
            ),
            activate_metadata_policy=(
                lambda policy_id, expected, resolutions, activated_at, *, reconcile_current=False: (
                    self.activate_metadata_policy(
                        policy_id,
                        expected,
                        resolutions,
                        activated_at,
                        reconcile_current=reconcile_current,
                    )
                )
            ),
        )
        self._owner_settings = OwnerSettingsStore(
            connection, self._setup_serialization_lock
        )
        self._reviewer_access = ReviewerAccessStore(
            connection, self._setup_serialization_lock
        )
        self._description_advice = SQLiteDescriptionAdviceRepository(
            connection,
            lambda decision_id, at: self._revoke_field_effect_authority_in_transaction(
                decision_id, at, required=False
            ),
            lambda case_id, destination, at, decision_id: (
                self._set_field_effect_manual_hold(
                    case_id, destination, at, decision_id
                )
            ),
        )
        self._review_scans = SQLiteReviewScanRepository(connection)
        self._current_cover_authority: tuple[str | None, str | None] = (None, None)
        self._field_effects = SQLiteFieldEffectRepository(
            connection,
            binding,
            record_field_effect_event=(
                lambda operation, kind, occurred_at: self._record_field_effect_event(
                    operation, kind, occurred_at
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
            current_cover_authority=(lambda: self._current_cover_authority),
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
                lambda report, proposal: self._metadata_policy._policy_destinations_for(
                    report, proposal
                )
            ),
            current_cover_authority=(lambda: self._current_cover_authority),
            record_browse_unavailable_exception=(
                lambda error: self._record_browse_unavailable_exception(error)
            ),
        )
        # The two cover references are rebound by `bind_current_cover_policy`
        # and `bind_current_cover_replay` long after this point, so the
        # projection side reads them through a callable rather than receiving
        # the `None` they hold here.
        self._field_effect_activity = SQLiteFieldEffectActivityRepository(
            connection,
            binding,
            validate_shadow_outcome_row=self._validate_shadow_outcome_row,
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
                lambda operation, kind, occurred_at: self._record_field_effect_event(
                    operation, kind, occurred_at
                )
            ),
            field_effect_event_documents=(
                lambda plan_id: self.field_effect_event_documents(plan_id)
            ),
            current_cover_policy_reference=(lambda: self._current_cover_authority[0]),
            current_cover_replay_reference=(lambda: self._current_cover_authority[1]),
            description_authority_value=self._description_advice.authority_value,
        )
        self._decisions = SQLiteDecisionRepository(
            connection,
            binding,
            get_review_case=(
                lambda review_case_id: self.get_review_case(review_case_id)
            ),
            review_case_accepts_authority=(
                lambda review_case_id: self._review_case_accepts_authority(
                    review_case_id
                )
            ),
            current_resolved_policy_id=(
                lambda library: self.current_resolved_policy_id(library)
            ),
            record_cover_decision_outcome=(
                lambda decision, consideration_id: (
                    self._cover_considerations._record_cover_decision_outcome(
                        decision, consideration_id
                    )
                )
            ),
            revoke_field_effect_authority_in_transaction=(
                lambda decision_id, changed_at, *, required: (
                    self._revoke_field_effect_authority_in_transaction(
                        decision_id, changed_at, required=required
                    )
                )
            ),
            set_field_effect_manual_hold=(
                lambda review_case_id, destination, changed_at, decision_id: (
                    self._set_field_effect_manual_hold(
                        review_case_id, destination, changed_at, decision_id
                    )
                )
            ),
            validate_shadow_outcome_row=(
                lambda row: self._validate_shadow_outcome_row(row)
            ),
        )
        self._review_projections = SQLiteReviewProjectionRepository(
            connection,
            binding,
            current_resolved_metadata_policy=(
                lambda library: self.current_resolved_metadata_policy(library)
            ),
            require_staged_shadow_projection_current=(
                lambda review_case_id, action: (
                    self._require_staged_shadow_projection_current(
                        review_case_id, action
                    )
                )
            ),
            get_review_case=(
                lambda review_case_id: self.get_review_case(review_case_id)
            ),
            get_review_case_report=(
                lambda review_case_id: self.get_review_case_report(review_case_id)
            ),
            get_review_case_decisions=(
                lambda review_case_id: self.get_review_case_decisions(review_case_id)
            ),
            get_review_case_history=(
                lambda review_case_id, limit: self.get_review_case_history(
                    review_case_id, limit
                )
            ),
            description_advice=self._description_advice.read,
            current_cover_consideration_document=(
                lambda review_case_id: self.current_cover_consideration_document(
                    review_case_id
                )
            ),
            cover_consideration_history=(
                lambda review_case_id, limit: self.cover_consideration_history(
                    review_case_id, limit
                )
            ),
            list_current_review_case_documents=(
                lambda search, limit, offset, attention: (
                    self.list_current_review_case_documents(
                        search, limit, offset, attention
                    )
                )
            ),
            count_current_review_cases=(
                lambda search: self.count_current_review_cases(search)
            ),
            list_current_attention_documents=(
                lambda search, limit, offset: self.list_current_attention_documents(
                    search, limit, offset
                )
            ),
            count_current_attention_fields=(
                lambda search: self.count_current_attention_fields(search)
            ),
            current_cover_authority=(lambda: self._current_cover_authority),
        )

    def _record_browse_unavailable_exception(self, error: sqlite3.Error) -> None:
        """Retain bounded storage-failure classes for the support export only."""
        name = type(error).__name__
        if not name.isidentifier() or len(name) > 64:
            return
        with self._browse_failure_lock:
            self._browse_unavailable_exception_classes = tuple(
                sorted({*self._browse_unavailable_exception_classes, name})[-8:]
            )

    def browse_unavailable_exception_classes(self) -> tuple[str, ...]:
        with self._browse_failure_lock:
            return self._browse_unavailable_exception_classes

    def bind_current_cover_policy(self, reference: str) -> None:
        """Bind guarded cover effects to this process's validated policy."""
        if re.fullmatch(r"cover_policy_[0-9a-f]{64}", reference) is None:
            raise ValueError("cover policy reference is invalid")
        self._current_cover_authority = (reference, self._current_cover_authority[1])

    def bind_current_cover_replay(self, reference: str) -> None:
        """Bind guarded cover effects to the current observer/contract profile."""
        if re.fullmatch(r"cover_replay_[0-9a-f]{64}", reference) is None:
            raise ValueError("cover replay reference is invalid")
        self._current_cover_authority = (self._current_cover_authority[0], reference)

    def bind_current_cover_authority(
        self, policy_reference: str, replay_reference: str
    ) -> None:
        """Validate then replace both live cover authority references together."""
        if re.fullmatch(r"cover_policy_[0-9a-f]{64}", policy_reference) is None:
            raise ValueError("cover policy reference is invalid")
        if re.fullmatch(r"cover_replay_[0-9a-f]{64}", replay_reference) is None:
            raise ValueError("cover replay reference is invalid")
        self._current_cover_authority = (policy_reference, replay_reference)

    def bind_cover_consideration_currentness(
        self, predicate: Callable[[str, str], str]
    ) -> None:
        """Bind the repository-local live-cover predicate used by review SQL."""

        def current(document: object, replay_reference: object) -> int:
            if type(document) is not str or type(replay_reference) is not str:
                return 0
            try:
                return {
                    "inactive": 0,
                    "current": 1,
                    "refresh_required": 2,
                }.get(predicate(document, replay_reference), 0)
            except Exception:
                return 0

        self._connection.create_function("ml_cover_consideration_current", 2, current)

    def field_effect_authority_source(
        self,
        decision: HumanDecision
        | AutomaticDecision
        | CoverAutomaticDecision
        | AuthoredDescriptionDecision,
    ) -> dict[str, object]:
        return self._field_effect_activity.field_effect_authority_source(decision)

    def record_current_field_effect_no_change(
        self,
        decision: AutomaticDecision | AuthoredDescriptionDecision,
        occurred_at: int,
    ) -> bool:
        return self._field_effects.record_current_field_effect_no_change(
            decision,
            occurred_at,
        )

    def field_effect_authority_is_current(self, authority: EffectAuthority) -> bool:
        return self._field_effect_activity.field_effect_authority_is_current(authority)

    def grant_field_effect_consents(
        self,
        connection: str,
        libraries: tuple[str, ...],
        destinations: tuple[str, ...],
        granted_at: int,
    ) -> tuple[EffectConsent, ...]:
        return self._field_effects.grant_field_effect_consents(
            connection,
            libraries,
            destinations,
            granted_at,
        )

    def grant_field_effect_consent_matrix(
        self,
        connection: str,
        grants: tuple[tuple[str, tuple[str, ...]], ...],
        granted_at: int,
    ) -> tuple[EffectConsent, ...]:
        return self._field_effects.grant_field_effect_consent_matrix(
            connection,
            grants,
            granted_at,
        )

    def current_field_effect_consent(
        self,
        connection: str,
        library: str,
        destination: str,
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
        self,
        connection: str,
        library: str,
        changed_at: int,
    ) -> int:
        return self._field_effects.revoke_library_field_effect_consents(
            connection,
            library,
            changed_at,
        )

    def pending_field_effect_decision_documents(
        self,
        limit: int,
        after: tuple[int, str] | None = None,
    ) -> tuple[str, ...]:
        return self._field_effect_activity.pending_field_effect_decision_documents(
            limit, after
        )

    def pending_field_effect_activity_example_document(self) -> str | None:
        return (
            self._field_effect_activity.pending_field_effect_activity_example_document()
        )

    def field_effect_activity_snapshot(
        self, call: Callable[[], dict[str, object]]
    ) -> dict[str, object]:
        return self._field_effect_activity.field_effect_activity_snapshot(call)

    def pending_field_effect_decision_counts(self) -> tuple[dict[str, object], ...]:
        return self._field_effect_activity.pending_field_effect_decision_counts()

    def field_effect_manual_override(
        self,
        review_case_id: str,
        destination: str,
        changed_at: int,
    ) -> int:
        """Supersede only unclaimed automatic authority for a human override."""
        if (
            type(review_case_id) is not str
            or type(destination) is not str
            or type(changed_at) is not int
            or changed_at < 0
        ):
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
        self,
        review_case_id: str,
        destination: str,
        changed_at: int,
        decision_id: str,
    ) -> None:
        self._field_effects._set_field_effect_manual_hold(
            review_case_id,
            destination,
            changed_at,
            decision_id,
        )

    def field_effect_manual_hold_context(
        self,
        review_case_id: str,
        destination: str,
    ) -> tuple[str, str] | None:
        return self._field_effects.field_effect_manual_hold_context(
            review_case_id,
            destination,
        )

    def field_effect_manual_hold_token(
        self,
        review_case_id: str,
        destination: str,
    ) -> str | None:
        return self._field_effects.field_effect_manual_hold_token(
            review_case_id,
            destination,
        )

    def request_field_effect_manual_return(
        self,
        review_case_id: str,
        destination: str,
        hold_token: str,
        scan_id: str,
        requested_at: int,
        changed_at: int,
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
        self,
        limit: int,
        after: tuple[int, str, str] | None = None,
    ) -> tuple[tuple[str, str, str, str, str, str, int, int], ...]:
        return self._field_effect_activity.pending_field_effect_manual_returns(
            limit, after
        )

    def _has_pending_field_effect_manual_return(self, scan_id: str) -> bool:
        return self._field_effects._has_pending_field_effect_manual_return(scan_id)

    def clear_field_effect_manual_hold(
        self,
        review_case_id: str,
        destination: str,
        changed_at: int,
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

    def field_effect_backup_ready(self, *, fresh: bool = False) -> bool:
        return self._field_effect_activity.field_effect_backup_ready(fresh=fresh)

    def next_field_effect_plan_id(
        self,
        last_library: str | None,
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

    def configure_activity_page_reader(self) -> None:
        """Bound the independently opened Activity projection reader only."""

        self._field_effect_activity.configure_activity_page_reader()

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

    def field_effect_activity_transitions(
        self, event_id: str, after_sequence: int | None = None, limit: int = 20
    ) -> tuple[dict[str, object], ...]:
        return self._field_effect_activity.field_effect_activity_transitions(
            event_id, after_sequence, limit
        )

    def record_field_effect(
        self, operation: FieldEffectOperation
    ) -> FieldEffectOperation:
        return self._field_effects.record_field_effect(operation)

    def get_field_effect(self, plan_id: str) -> FieldEffectOperation:
        return self._field_effects.get_field_effect(plan_id)

    def field_effect_event_documents(self, plan_id: str) -> tuple[str, ...]:
        return self._field_effects.field_effect_event_documents(plan_id)

    def suspend_field_effect_capability(
        self,
        *,
        scope: str,
        connection: str,
        destination: str,
        reason: str,
        changed_at: int,
    ) -> tuple[str, ...]:
        return self._field_effects.suspend_field_effect_capability(
            scope=scope,
            connection=connection,
            destination=destination,
            reason=reason,
            changed_at=changed_at,
        )

    def field_effect_capability_suspension(
        self, connection: str, destination: str
    ) -> str | None:
        return self._field_effects.field_effect_capability_suspension(
            connection,
            destination,
        )

    def clear_field_effect_capability_suspension(
        self,
        *,
        scope: str,
        connection: str,
        destination: str,
        reason: str,
        observed_at: int,
    ) -> None:
        self._field_effects.clear_field_effect_capability_suspension(
            scope=scope,
            connection=connection,
            destination=destination,
            reason=reason,
            observed_at=observed_at,
        )

    def claim_field_effect(
        self, plan_id: str, expected_state: str, claim_token: str, claimed_at: int
    ) -> FieldEffectOperation:
        return self._field_effect_activity.claim_field_effect(
            plan_id, expected_state, claim_token, claimed_at
        )

    def transition_field_effect(
        self,
        plan_id: str,
        expected_state: str,
        state: str,
        changed_at: int,
        failure: str | None = None,
    ) -> FieldEffectOperation:
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
        self,
        decision_id: str,
        changed_at: int,
        *,
        required: bool,
    ) -> int:
        return self._field_effects._revoke_field_effect_authority_in_transaction(
            decision_id,
            changed_at,
            required=required,
        )

    def revoke_field_effect_consent(self, consent_id: str, changed_at: int) -> int:
        return self._field_effects.revoke_field_effect_consent(consent_id, changed_at)

    def schedule_field_effect_reconciliation(
        self,
        plan_id: str,
        expected_state: str,
        changed_at: int,
        failure: str = "ambiguous",
    ) -> FieldEffectOperation:
        return self._field_effects.schedule_field_effect_reconciliation(
            plan_id,
            expected_state,
            changed_at,
            failure,
        )

    def complete_field_effect_reconciliation(
        self,
        plan_id: str,
        state: str,
        failure: str | None,
        changed_at: int,
        reevaluate: bool = False,
    ) -> FieldEffectOperation:
        return self._field_effects.complete_field_effect_reconciliation(
            plan_id,
            state,
            failure,
            changed_at,
            reevaluate,
        )

    def retire_stale_cover_decision(self, decision_id: str, changed_at: int) -> bool:
        return self._cover_considerations.retire_stale_cover_decision(
            decision_id, changed_at
        )

    def claim_next_field_effect_action(
        self, changed_at: int, plan_id: str | None = None
    ) -> dict[str, object] | None:
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

    def _update_field_effect(
        self, operation: FieldEffectOperation, expected_state: str
    ) -> None:
        self._field_effects._update_field_effect(operation, expected_state)

    def _record_field_effect_event(
        self, operation: FieldEffectOperation, kind: str, occurred_at: int
    ) -> None:
        self._field_effects._append_field_effect_event(operation, kind, occurred_at)

    def require_current(self) -> None:
        if self._binding is not None:
            self._binding.require_current()

    @property
    def authority(self) -> object | None:
        # A dependent presents the same authority its root does, so a further
        # dependent of either borrows one live root admission.
        return self._binding if self._binding is not None else self._authority

    def request_review_scan(
        self,
        connection: str,
        library: str,
        library_id: str,
        producer_fingerprint: str,
        requested_at: int,
        *,
        first_boot: bool = False,
        trigger: PreparationTrigger | None = None,
    ) -> PipelineProgress:
        return self._review_scans.request_review_scan(
            connection,
            library,
            library_id,
            producer_fingerprint,
            requested_at,
            first_boot=first_boot,
            trigger=trigger,
        )

    def request_library_addition(
        self,
        connection: str,
        library: str,
        library_id: str,
        producer_fingerprint: str,
        requested_at: int,
    ) -> PipelineProgress | None:
        return self._review_scans.request_library_addition(
            connection,
            library,
            library_id,
            producer_fingerprint,
            requested_at,
        )

    def deferred_library_additions(self) -> tuple[tuple[str, str], ...]:
        return self._review_scans.deferred_library_additions()

    def preparation_presentation(self, now: int) -> PreparationPresentation | None:
        return preparation_presentation_snapshot(self._connection, now)

    def dismiss_preparation_presentation(self, run_id: str, dismissed_at: int) -> None:
        transaction = self._transaction("dismiss_preparation_presentation")
        try:
            transaction.begin()
            dismiss_preparation_presentation(self._connection, run_id, dismissed_at)
            transaction.commit()
            return
        except BaseException as error:
            transaction.rollback()
            failure = error
        raise failure from None

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

    def resume_review_pipeline(
        self,
        libraries: tuple[tuple[str, str, str, str], ...],
        updated_at: int,
    ) -> None:
        self._review_scans.resume_review_pipeline(libraries, updated_at)

    def drain_library_additions(
        self,
        libraries: tuple[tuple[str, str, str, str], ...],
        updated_at: int,
    ) -> None:
        self._review_scans.drain_library_additions(libraries, updated_at)

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

    def qualify_review_discovery(
        self, action: PipelineAction, qualified_at: int
    ) -> bool:
        return self._review_scans.qualify_review_discovery(action, qualified_at)

    def defer_for_target_connection(
        self, action: PipelineAction, code: str, updated_at: int
    ) -> None:
        self._review_scans.defer_for_target_connection(action, code, updated_at)

    def hold_target_connection(
        self, connection: str, code: str, updated_at: int
    ) -> None:
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
        return self._review_preparation.claim_review_pipeline_work(work_id, claimed_at)

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

    def completed_provider_retry_successor(self, source_report_id: str) -> str | None:
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

    def publish(
        self,
        report: MetadataReport,
        currentness: Callable[[], bool],
        expected_current: str | None = None,
        after_publication: Callable[[ReportRepository, MetadataReport], None]
        | None = None,
        *,
        expected_package: tuple[str | None, str, str | None, int | None, str]
        | None = None,
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

    def get_proposal_input(self, report_id: str) -> str:
        return self._reports.get_proposal_input(report_id)

    def publish_proposal(self, proposal_set: ProposalSet) -> str:
        return self._proposal_sets.publish_proposal(proposal_set)

    def get_proposal(self, proposal_set_id: str) -> str:
        return self._proposal_sets.get_proposal(proposal_set_id)

    def admit_review_case(self, proposal_set: ProposalSet) -> str:
        return self._review_cases.admit_review_case(proposal_set)

    def review_case_exists(self, review_case_id: str) -> bool:
        return self._review_cases.review_case_exists(review_case_id)

    def current_review_case_successor(self, review_case_id: str) -> str | None:
        return self._review_cases.current_review_case_successor(review_case_id)

    def get_review_case(self, review_case_id: str) -> str:
        return self._review_cases.get_review_case(review_case_id)

    def description_advice_store(self) -> SQLiteDescriptionAdviceRepository:
        return self._description_advice

    def read_staged_description_preparation(
        self, action: PipelineAction
    ) -> tuple[str, str]:
        return self._review_projections.read_staged_description_preparation(action)

    def read_staged_cover_preparation(
        self, action: PipelineAction
    ) -> tuple[str, str, bool] | None:
        return self._review_projections.read_staged_cover_preparation(action)

    def get_review_case_report(self, review_case_id: str) -> str:
        return self._review_cases.get_review_case_report(review_case_id)

    def review_case_ready_at(self, review_case_id: str) -> int | None:
        return self._review_preparation.review_case_ready_at(review_case_id)

    def read_current_review_case(
        self, review_case_id: str, history_limit: int | None
    ) -> CurrentReviewCaseSnapshot:
        return self._review_projections.read_current_review_case(
            review_case_id, history_limit
        )

    def read_review_library(
        self,
        search: str | None,
        limit: int,
        offset: int,
        attention: bool | None,
    ) -> ReviewLibrarySnapshot:
        return self._review_projections.read_review_library(
            search, limit, offset, attention
        )

    def read_review_attention(
        self, search: str | None, limit: int, offset: int
    ) -> ReviewAttentionSnapshot:
        return self._review_projections.read_review_attention(search, limit, offset)

    def read_current_report_document(
        self, connection: str, library: str, item: str
    ) -> str | None:
        return self._review_projections.read_current_report_document(
            connection, library, item
        )

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

    def count_current_review_cases(self, search: str | None) -> tuple[int, int]:
        return self._review_cases.count_current_review_cases(search)

    def list_current_attention_documents(
        self, search: str | None, limit: int, offset: int
    ) -> tuple[
        tuple[str, str, str, int, str | None, ReviewFieldCause | None, bool], ...
    ]:
        return self._review_cases.list_current_attention_documents(
            search, limit, offset
        )

    def count_current_attention_fields(self, search: str | None) -> int:
        return self._review_cases.count_current_attention_fields(search)

    def shadow_posture(self) -> str:
        return self._metadata_policy.shadow_posture()

    def enter_shadow_preview(self, updated_at: int) -> str:
        return self._metadata_policy.enter_shadow_preview(updated_at)

    def activate_qualified_automatic(self, updated_at: int) -> str:
        return self._metadata_policy.activate_qualified_automatic(updated_at)

    def _schedule_current_shadow_work(self, case: ReviewCase, updated_at: int) -> None:
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
            media_kind=case.media_kind,
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
            if case_for_recovery.media_kind == "audiobook" and not (
                audiobook_decision_qualification.F46_PASSING_AUTOMATIC_OPTIONS
            ):
                # While no audiobook tranche is qualified the projection
                # abstains here rather than deciding and holding the decision
                # back: no shadow outcome is published and no automatic
                # decision can be minted from one. The work still completes,
                # because abstaining is an answer (#966). Once a record
                # qualifies one identity the ordinary projection runs and
                # withdraws Automatic from every destination that record does
                # not name (#967).
                transaction.commit()
                return "inactive"
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
                case,
                decide_shadow_packet(packet),
                policy,
                state[0],
                report,
                proposal,
                media_kind=case.media_kind,
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
                            if message.startswith("category:")
                            else None
                        )
                        if category:
                            categories = dict(current.category_suspensions)
                            categories[category] = "automatic_admission_failed"
                            safety = AutomaticSafetyState(
                                current.paused,
                                current.global_suspension,
                                tuple(sorted(categories.items())),
                            )
                            self._set_automatic_safety_state(safety, evaluated_at)
                            if category not in attempted_categories:
                                attempted_categories.add(category)
                                continue
                        else:
                            safety = AutomaticSafetyState(
                                current.paused,
                                "automatic_authority_failed",
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
                    self._schedule_current_shadow_work(case_for_recovery, evaluated_at)
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
        self._decisions._record_automatic_outcomes(outcome, occurred_at)

    def list_shadow_outcomes(self, limit: int) -> tuple[tuple[str, int], ...]:
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

    def retry_metadata_policy_transition(
        self, transition_id: str, retried_at: int
    ) -> PolicyTransition:
        return self._metadata_policy.retry_metadata_policy_transition(
            transition_id, retried_at
        )

    def acknowledge_metadata_policy_transition(
        self, transition_id: str, acknowledged_at: int
    ) -> PolicyTransition:
        return self._metadata_policy.acknowledge_metadata_policy_transition(
            transition_id, acknowledged_at
        )

    def current_unapplied_count(self, library: str) -> int:
        return self._metadata_policy.current_unapplied_count(library)

    def current_policy_impact_pairs(
        self, library: str, limit: int
    ) -> tuple[int, tuple[tuple[MetadataReport, ProposalSet], ...]]:
        return self._metadata_policy.current_policy_impact_pairs(library, limit)

    def get_review_case_decisions(self, review_case_id: str) -> tuple[str, ...]:
        return self._decisions.get_review_case_decisions(review_case_id)

    def current_cover_consideration(
        self, review_case_id: str, replay_digest: str
    ) -> str | None:
        return self._cover_considerations.current_cover_consideration(
            review_case_id, replay_digest
        )

    def current_cover_consideration_document(self, review_case_id: str) -> str | None:
        return self._cover_considerations.current_cover_consideration_document(
            review_case_id
        )

    def latest_cover_consideration_document(self, review_case_id: str) -> str | None:
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
        reviewer_access_revision: int | None = None,
    ) -> str:
        return self._cover_considerations.record_cover_consideration(
            document,
            review_case_id=review_case_id,
            report_id=report_id,
            replay_digest=replay_digest,
            supersedes_id=supersedes_id,
            occurred_at=occurred_at,
            preparation_kind=preparation_kind,
            reviewer_access_revision=reviewer_access_revision,
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

    def request_description_retry(
        self, review_case_id: str, now: int
    ) -> tuple[str, str]:
        return self._description_advice.request_retry(review_case_id, now)

    def record_authored_description_decision(
        self,
        candidate_id: str,
        binding: DescriptionAdviceBinding,
        idempotency_digest: str,
        now: int,
    ) -> str:
        return self._description_advice.admit(
            candidate_id, binding, "human", idempotency_digest, now
        ).to_json()

    def find_human_decision(self, idempotency_digest: str) -> str | None:
        return self._decisions.find_human_decision(idempotency_digest)

    def record_human_decision(
        self,
        decision: HumanDecision,
        *,
        cover_consideration_id: str | None = None,
    ) -> str:
        return self._decisions.record_human_decision(
            decision, cover_consideration_id=cover_consideration_id
        )

    def _record_automatic_decision(self, decision: AutomaticDecision) -> str:
        return self._decisions._record_automatic_decision(decision)

    def automatic_safety_state(self) -> AutomaticSafetyState:
        return self._decisions.automatic_safety_state()

    def set_automatic_safety_state(
        self, state: AutomaticSafetyState, updated_at: int
    ) -> None:
        self._decisions.set_automatic_safety_state(state, updated_at)

    def _set_automatic_safety_state(
        self, state: AutomaticSafetyState, updated_at: int
    ) -> None:
        self._decisions._set_automatic_safety_state(state, updated_at)

    def get_review_case_history(
        self, review_case_id: str, limit: int
    ) -> tuple[dict[str, object], ...]:
        return self._decisions.get_review_case_history(review_case_id, limit)

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
        if (
            action.lineage_checkpoint is None
            or action.policy_generation is None
            or self._connection.execute(
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
                    action.action_id,
                    action.attempt,
                    action.connection,
                    action.library,
                    action.item_id,
                    action.report_id,
                    action.proposal_set_id,
                    review_case_id,
                    action.lineage_checkpoint,
                    action.policy_generation,
                ),
            ).fetchone()
            != (1,)
        ):
            raise ReviewAdmissionStaleError

    def _review_case_accepts_authority(self, review_case_id: str) -> bool:
        return self._review_cases._review_case_accepts_authority(review_case_id)

    def current(
        self, connection: str, library: str, item: str, file_identity: str
    ) -> str:
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
        self,
        *,
        limit: int = 100,
        after: tuple[int, str] | None = None,
    ) -> tuple[tuple[object, ...], ...]:
        """Return bounded exact package cutovers awaiting recovery enqueue."""
        return self._recovery_outbox.pending_provider_recovery_outbox(
            limit=limit, after=after
        )

    def complete_provider_recovery_outbox(
        self,
        outbox_id: str,
        *,
        delivered_at: int,
    ) -> None:
        """Mark one exact cutover delivered only after idempotent enqueue."""
        self._recovery_outbox.complete_provider_recovery_outbox(
            outbox_id, delivered_at=delivered_at
        )

    def current_optional(
        self, connection: str, library: str, item: str, file_identity: str
    ) -> str | None:
        return self._reports.current_optional(connection, library, item, file_identity)

    def preview_digest(
        self, report_id: str, artifact_id: str, preview_variant: str
    ) -> str:
        return self._reports.preview_digest(report_id, artifact_id, preview_variant)

    def preview_digest_for_artifact(
        self, artifact_id: str, preview_variant: str
    ) -> str:
        return self._reports.preview_digest_for_artifact(artifact_id, preview_variant)

    def artifact_is_referenced(self, digest: str) -> bool:
        return self._reports.artifact_is_referenced(digest)

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            if self._binding is not None:
                self._binding.close()
            # Only the root retires the authority; a dependent that closes
            # first must leave its siblings a live one.
            if self._authority is not None and self._owns_authority:
                self._authority.close()

    def owner_settings_store(self) -> OwnerSettingsStore:
        return self._owner_settings

    def reviewer_access_store(self) -> ReviewerAccessStore:
        return self._reviewer_access

    def setup_state_store(self) -> SetupStateStore:
        """The setup-only composition is the sole caller of this incomplete-state port."""
        return SetupStateStore(self._connection, self._setup_serialization_lock)

    def has_first_boot_preparation(self) -> bool:
        """Read explicit setup-origin scan identities without inferring store history."""
        acknowledged = self._connection.execute(
            "SELECT first_boot_acknowledged FROM review_preparation_control WHERE singleton=1"
        ).fetchone()
        if acknowledged != (0,):
            return False
        rows = self._connection.execute(
            "SELECT scan_id,connection_name,library_name FROM review_preparation_scans"
        ).fetchall()
        return any(
            is_first_boot_scan_id(scan_id, connection, library)
            for scan_id, connection, library in rows
        )

    def acknowledge_first_boot_preparation(self) -> None:
        """Durably retire only the setup-origin handover presentation."""
        transaction = self._transaction("acknowledge_first_boot_preparation")
        try:
            transaction.begin()
            cursor = self._connection.execute(
                "UPDATE review_preparation_control SET first_boot_acknowledged=1 "
                "WHERE singleton=1"
            )
            if cursor.rowcount != 1:
                raise MetadataReportError("review preparation state is invalid")
            transaction.commit()
            return
        except BaseException as error:
            transaction.rollback()
            failure = error
        raise failure from None


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


def open_repository(
    path: Path,
    *,
    create: bool = True,
    expected_identity: tuple[int, int] | None = None,
    cross_thread: bool = False,
    authority: object | None = None,
) -> SQLiteMetadataReportRepository:
    created = False
    descriptor = -1
    created_identity: tuple[int, int] | None = None
    connection: sqlite3.Connection | None = None
    binding: _LinuxDatabaseBinding | None = None
    portable_authority: _PortableDatabaseAuthority | None = None
    owns_authority = False
    schema_validated = False
    try:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            raise MetadataReportError("database no-follow access is unavailable")
        flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | nofollow
        if os.path.lexists(path):
            descriptor = os.open(path, flags)
            information = os.fstat(descriptor)
            if (
                expected_identity is not None
                and (information.st_dev, information.st_ino) != expected_identity
            ):
                raise MetadataReportError("database authority changed")
            if not stat.S_ISREG(information.st_mode):
                raise MetadataReportError("database path is invalid")
            _validate_delete_mode_header(descriptor)
            if sys.platform.startswith("linux"):
                binding = _LinuxDatabaseBinding.create(path, descriptor, authority)
                descriptor = -1
                schema_validated = binding.borrows_schema_validation()
                if not schema_validated:
                    _validate_existing_read_only(binding.alias_path, binding.descriptor)
                    schema_validated = True
                binding.require_current()
            elif authority is None:
                _reject_database_sidecars(path)
                _validate_existing_read_only(path, descriptor)
                _reject_database_sidecars(path)
                schema_validated = True
            else:
                # A dependent of a live root: it borrows that root's inode
                # identity and schema admission, and the rollback journal it
                # may see is that root's open transaction rather than
                # recovery state. `-wal` and `-shm` stay refused.
                if type(authority) is not _PortableDatabaseAuthority:
                    raise MetadataReportError("database authority changed")
                authority.require_current()
                if path.absolute() != authority.configured_path:
                    raise MetadataReportError("database authority changed")
                if authority.expected_identity != (
                    information.st_dev,
                    information.st_ino,
                ):
                    raise MetadataReportError("database authority changed")
                portable_authority = authority
                _reject_database_sidecars(path, allow_rollback_journal=True)
                schema_validated = authority.borrows_schema_validation()
                if not schema_validated:
                    _validate_existing_read_only(path, descriptor)
                    schema_validated = True
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
        database_descriptor = binding.descriptor if binding is not None else descriptor
        connection = _connect_bound(
            database_path,
            database_descriptor,
            binding.require_current if binding is not None else None,
            cross_thread=cross_thread,
            allow_rollback_journal=portable_authority is not None,
        )
        connection.create_function(
            "ml_casefold",
            1,
            _review_casefold,
            deterministic=True,
        )
        connection.create_function(
            "ml_cover_consideration_current",
            2,
            lambda _document, _replay_reference: 0,
        )
        if binding is not None:
            binding.require_current()
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=5000")
        if created:
            _create_schema(connection)
        if not schema_validated:
            _validate_schema(connection)
        if binding is not None:
            binding.mark_schema_validated()
        if binding is None and portable_authority is None:
            portable_authority = _PortableDatabaseAuthority(
                path, (information.st_dev, information.st_ino)
            )
            owns_authority = True
            portable_authority.mark_schema_validated()
        if binding is None:
            os.close(descriptor)
            descriptor = -1
        repository = SQLiteMetadataReportRepository(
            connection,
            binding,
            (information.st_dev, information.st_ino),
            portable_authority,
            owns_authority=owns_authority,
        )
        try:
            repository.reviewer_access_store().snapshot()
        except ReviewerAccessStoreError as error:
            raise MetadataReportError("reviewer access state is unavailable") from error
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
