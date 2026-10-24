"""Durable description attempts, immutable text and independently current bindings."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from typing import Literal

from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.domain.audiobook_corroboration import (
    admitted_audiobook_identity,
)
from mechanical_librarian.domain.description_advice import (
    DESCRIPTION_INSTRUCTION,
    DESCRIPTION_OUTPUT_SCHEMA,
    AuthoredDescriptionDecision,
    DescriptionAdviceBinding,
    DescriptionAdviceCandidate,
    DescriptionAdviceInput,
    DescriptionAdviceSettings,
    description_digest,
    description_document,
)
from mechanical_librarian.domain.metadata_report import MetadataReport


class SQLiteDescriptionAdviceRepository(ConnectionScoped):
    def __init__(
        self,
        connection: sqlite3.Connection,
        revoke: Callable[[str, int], object] | None = None,
        manual_hold: Callable[[str, str, int, str], None] | None = None,
    ) -> None:
        super().__init__(connection)
        self._revoke = revoke
        self._manual_hold = manual_hold
        self._settings = DescriptionAdviceSettings()
        self._identity: Callable[[str, str | None], tuple[str, str] | None] = (
            lambda _engine, _model: None
        )

    def bind_identity(
        self, identity: Callable[[str, str | None], tuple[str, str] | None]
    ) -> None:
        """Bind a passive model/profile check without transport or another store."""
        self._identity = identity

    def bind_settings(self, settings: DescriptionAdviceSettings) -> None:
        self._settings = settings

    @local_operation
    def current_settings(self) -> DescriptionAdviceSettings:
        row = self._connection.execute(
            "SELECT document FROM owner_settings WHERE singleton=1"
        ).fetchone()
        return (
            self._settings
            if row is None
            else DescriptionAdviceSettings.model_validate(
                json.loads(row[0])["description_advice"]
            )
        )

    @local_operation
    def binding_is_current(self, binding: DescriptionAdviceBinding) -> bool:
        from mechanical_librarian.domain.review_case import ReviewCase
        from mechanical_librarian.domain.reviewer_access import ReviewerAccessDocument

        row = self._connection.execute(
            "SELECT c.document,a.document,p.generation FROM current_review_case_lineages l "
            "JOIN review_cases c ON c.review_case_id=l.review_case_id "
            "JOIN reviewer_access a ON a.singleton=1 JOIN metadata_policy_state p ON p.singleton=1 "
            "JOIN current_metadata_report_lineages r ON r.connection_name=l.connection_name AND r.library_name=l.library_name AND r.item_id=l.item_id AND r.report_id=l.report_id "
            "WHERE l.review_case_id=? AND l.report_id=? AND l.proposal_set_id=?",
            (binding.review_case_id, binding.report_id, binding.proposal_set_id),
        ).fetchone()
        if row is None:
            return False
        case = ReviewCase.from_json(row[0])
        access = ReviewerAccessDocument.model_validate_json(row[1])
        return (
            case.target_fingerprint == binding.target_fingerprint
            and access.revision == binding.access_revision
            and access.description_rewrite is not None
            and row[2] == binding.policy_generation
            and description_digest(self.current_settings().model_dump())
            == binding.preferences_digest
            and self._target_unchanged(binding)
        )

    def _target_unchanged(self, binding: DescriptionAdviceBinding) -> bool:
        """A verified changing write retires its original description binding."""
        from mechanical_librarian.domain.field_effect import FieldEffectOperation
        from mechanical_librarian.domain.review_case import ReviewCase

        row = self._connection.execute(
            "SELECT document FROM review_cases WHERE review_case_id=?",
            (binding.review_case_id,),
        ).fetchone()
        if row is None:
            return False
        case = ReviewCase.from_json(row[0])
        target = case.target
        changed = self._connection.execute(
            "SELECT document FROM field_effects WHERE connection_name=? AND library_name=? "
            "AND work_id=? AND destination='description' AND state='verified' "
            "AND json_extract(document,'$.plan.authority.review_case_id')=? "
            "AND json_extract(document,'$.plan.pre_value') IS NOT json_extract(document,'$.plan.desired_value') LIMIT 1",
            (
                target["connection"],
                target["library"],
                target["item"],
                binding.review_case_id,
            ),
        ).fetchone()
        if changed is None:
            return True
        operation = FieldEffectOperation.from_json(changed[0])
        return not (
            operation.state == "verified"
            and operation.plan.authority.review_case_id == binding.review_case_id
            and operation.plan.pre_value != operation.plan.desired_value
        )

    def _current_request(
        self, binding: DescriptionAdviceBinding, engine: str, model: str
    ) -> tuple[str, str, str, str] | None:
        """Reconstruct local request identity without an access gate or transport."""
        from mechanical_librarian.application.contained_reviewer_contract import (
            ContainedReviewRequest,
        )
        from mechanical_librarian.domain.metadata_report import MetadataReport
        from mechanical_librarian.domain.reviewer_access import ReviewerAccessDocument

        report_row = self._connection.execute(
            "SELECT document FROM metadata_reports WHERE report_id=?",
            (binding.report_id,),
        ).fetchone()
        access_row = self._connection.execute(
            "SELECT document FROM reviewer_access WHERE singleton=1"
        ).fetchone()
        generation = self._connection.execute(
            "SELECT generation FROM metadata_policy_state WHERE singleton=1"
        ).fetchone()
        if (
            report_row is None
            or access_row is None
            or generation != (binding.policy_generation,)
        ):
            return None
        access = ReviewerAccessDocument.model_validate_json(access_row[0])
        selection = access.description_rewrite
        settings = self.current_settings()
        if (
            selection is None
            or selection.engine != engine
            or access.revision != binding.access_revision
            or description_digest(settings.model_dump()) != binding.preferences_digest
            or (selection.engine == "openrouter" and selection.model != model)
        ):
            return None
        try:
            identity = self._identity(engine, getattr(selection, "model", None))
            if identity is None or identity[0] != model:
                return None
            preferences = settings.cleanup()
            request = ContainedReviewRequest(
                DESCRIPTION_INSTRUCTION,
                description_document(
                    DescriptionAdviceInput.from_report(
                        MetadataReport.from_json(report_row[0]), preferences
                    ).model_dump()
                ),
                DESCRIPTION_OUTPUT_SCHEMA,
            )
        except (OSError, ValueError, RuntimeError):
            return None
        request_identity = description_digest(
            {
                "request": request.digest,
                "access_revision": binding.access_revision,
                "engine": engine,
                "model": model,
                "profile": identity[1],
            }
        )
        return (
            request.digest,
            description_digest(preferences.model_dump()),
            identity[1],
            request_identity,
        )

    def _attempt_is_current(
        self,
        request_identity: str,
        binding: DescriptionAdviceBinding,
        engine: str,
        model: str,
    ) -> bool:
        request = self._current_request(binding, engine, model)
        return (
            request is not None
            and request[3] == request_identity
            and self._target_unchanged(binding)
        )

    def _candidate_is_current(
        self, candidate: DescriptionAdviceCandidate, binding: DescriptionAdviceBinding
    ) -> bool:
        if not self.binding_is_current(binding):
            return False
        request = self._current_request(binding, candidate.engine, candidate.model)
        return (
            request is not None
            and request
            == (
                candidate.request_digest,
                candidate.preferences_digest,
                candidate.profile_digest,
                candidate.request_identity,
            )
            and candidate.access_revision == binding.access_revision
            and candidate.instruction_digest
            == hashlib.sha256(DESCRIPTION_INSTRUCTION.encode()).hexdigest()
            and candidate.schema_digest
            == hashlib.sha256(DESCRIPTION_OUTPUT_SCHEMA.encode()).hexdigest()
        )

    @local_operation
    def admit(
        self,
        candidate_id: str,
        expected_binding: DescriptionAdviceBinding,
        origin: str,
        idempotency_digest: str,
        now: int,
    ) -> AuthoredDescriptionDecision:
        from mechanical_librarian.adapters.persistence.decisions import (
            _REPLACEMENT_BLOCKING_EFFECT_SQL,
        )
        from mechanical_librarian.domain.proposal_set import ProposalSet

        transaction = self._transaction("admit_description_decision")
        try:
            transaction.begin()
            replay = self._connection.execute(
                "SELECT document FROM description_decisions WHERE idempotency_digest=?",
                (idempotency_digest,),
            ).fetchone()
            if replay is not None:
                decision = AuthoredDescriptionDecision.from_json(replay[0])
                if (
                    decision.candidate_id != candidate_id
                    or decision.binding != expected_binding
                    or decision.origin != origin
                ):
                    raise ValueError("description idempotency conflict")
                transaction.commit()
                return decision
            row = self._connection.execute(
                "SELECT c.document,p.binding_document FROM current_description_advice p JOIN description_advice_candidates c ON c.candidate_id=p.candidate_id WHERE p.review_case_id=? AND p.candidate_id=?",
                (expected_binding.review_case_id, candidate_id),
            ).fetchone()
            if (
                row is None
                or DescriptionAdviceBinding.model_validate_json(row[1])
                != expected_binding
                or not self.binding_is_current(expected_binding)
            ):
                raise ValueError("description advice is stale")
            candidate = DescriptionAdviceCandidate.from_json(row[0])
            if not self._candidate_is_current(candidate, expected_binding):
                raise ValueError("description candidate request mismatch")
            case_id = expected_binding.review_case_id
            human = self._connection.execute(
                "SELECT 1 FROM human_decisions WHERE review_case_id=? AND destination='description' LIMIT 1",
                (case_id,),
            ).fetchone()
            prior = self._connection.execute(
                "SELECT d.document FROM current_description_decisions p JOIN description_decisions d ON d.decision_id=p.decision_id WHERE p.review_case_id=?",
                (case_id,),
            ).fetchone()
            if origin == "automatic_policy":
                safety = self._connection.execute(
                    "SELECT paused,global_suspension,category_suspensions_document FROM automatic_safety_state WHERE singleton=1"
                ).fetchone()
                posture = self._connection.execute(
                    "SELECT posture FROM metadata_policy_state WHERE singleton=1"
                ).fetchone()
                if (
                    not self.current_settings().automatic_application
                    or candidate.output.disposition != "ready"
                    or human is not None
                    # An owner's hold does not refuse the decision: a successor
                    # carries it with nothing yet to hold, and authority_value
                    # holds the write (ADR-0085).
                    or posture != ("automatic",)
                    or safety is None
                    or safety[0]
                    or safety[1] is not None
                    or "description" in dict(json.loads(safety[2]))
                    or prior is not None
                    and AuthoredDescriptionDecision.from_json(prior[0]).origin
                    == "human"
                ):
                    raise ValueError("description automatic admission unavailable")
            elif origin != "human":
                raise ValueError("invalid description authority")
            competing = [
                row[0]
                for row in self._connection.execute(
                    "SELECT decision_id FROM automatic_decisions WHERE review_case_id=? AND destination='description'",
                    (case_id,),
                )
            ]
            if origin == "human":
                competing.extend(
                    row[0]
                    for row in self._connection.execute(
                        "SELECT decision_id FROM human_decisions WHERE review_case_id=? AND destination='description'",
                        (case_id,),
                    )
                )
            if prior is not None:
                competing.append(
                    AuthoredDescriptionDecision.from_json(prior[0]).decision_id
                )
            for decision_id in competing:
                if (
                    self._connection.execute(
                        "SELECT 1 FROM field_effects WHERE decision_id=? AND "
                        + _REPLACEMENT_BLOCKING_EFFECT_SQL
                        + " LIMIT 1",
                        (decision_id, False),
                    ).fetchone()
                    is not None
                ):
                    raise ValueError("description effect is in flight")
            proposal_row = self._connection.execute(
                "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
                (expected_binding.proposal_set_id,),
            ).fetchone()
            if proposal_row is None:
                raise ValueError("description proposal is missing")
            proposal = ProposalSet.from_json(proposal_row[0])
            decision = AuthoredDescriptionDecision.create(
                candidate_id=candidate_id,
                binding=expected_binding,
                origin=origin,
                resolved_policy_id=proposal.resolved_policy_id,
                qualification_manifest_sha256=description_digest(
                    {
                        "instruction": DESCRIPTION_INSTRUCTION,
                        "schema": DESCRIPTION_OUTPUT_SCHEMA,
                        "preferences": self.current_settings().cleanup().model_dump(),
                    }
                ),
                idempotency_digest=idempotency_digest,
                occurred_at=now,
            )
            for decision_id in competing:
                if self._revoke is None:
                    raise ValueError("description revocation unavailable")
                self._revoke(decision_id, now)
            self._connection.execute(
                "INSERT INTO description_decisions VALUES(?,?,?,?,?)",
                (
                    decision.decision_id,
                    case_id,
                    candidate_id,
                    idempotency_digest,
                    decision.to_json(),
                ),
            )
            self._connection.execute(
                "INSERT INTO current_description_decisions VALUES(?,?) ON CONFLICT(review_case_id) DO UPDATE SET decision_id=excluded.decision_id",
                (case_id, decision.decision_id),
            )
            if origin == "human":
                if self._manual_hold is None:
                    raise ValueError("description manual hold unavailable")
                self._manual_hold(case_id, "description", now, decision.decision_id)

            transaction.commit()
            return decision
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def authority_value(self, decision: AuthoredDescriptionDecision) -> object:
        from mechanical_librarian.domain.field_effect import FieldEffectError

        if type(
            decision
        ) is not AuthoredDescriptionDecision or not self.binding_is_current(
            decision.binding
        ):
            raise FieldEffectError("authority_binding")
        row = self._connection.execute(
            "SELECT d.document,c.document FROM current_description_decisions p JOIN description_decisions d ON d.decision_id=p.decision_id JOIN description_advice_candidates c ON c.candidate_id=d.candidate_id WHERE p.review_case_id=? AND p.decision_id=?",
            (decision.review_case_id, decision.decision_id),
        ).fetchone()
        if row is None or row[0] != decision.to_json():
            raise FieldEffectError("authority_binding")
        candidate = DescriptionAdviceCandidate.from_json(row[1])
        if not self._candidate_is_current(candidate, decision.binding):
            raise FieldEffectError("authority_binding")
        if decision.origin == "automatic_policy":
            safety = self._connection.execute(
                "SELECT paused,global_suspension,category_suspensions_document FROM automatic_safety_state WHERE singleton=1"
            ).fetchone()
            posture = self._connection.execute(
                "SELECT posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            owner_final = self._connection.execute(
                "SELECT 1 FROM human_decisions WHERE review_case_id=? AND destination='description' "
                "UNION ALL SELECT 1 FROM field_effect_manual_holds WHERE review_case_id=? AND destination='description' AND active=1 LIMIT 1",
                (decision.review_case_id, decision.review_case_id),
            ).fetchone()
            if (
                not self.current_settings().automatic_application
                or candidate.output.disposition != "ready"
                or owner_final is not None
                or posture != ("automatic",)
                or safety is None
                or safety[0]
                or safety[1] is not None
                or "description" in dict(json.loads(safety[2]))
                or decision.qualification_manifest_sha256
                != description_digest(
                    {
                        "instruction": DESCRIPTION_INSTRUCTION,
                        "schema": DESCRIPTION_OUTPUT_SCHEMA,
                        "preferences": self.current_settings().cleanup().model_dump(),
                    }
                )
            ):
                raise FieldEffectError("authority_binding")
        return {"kind": "text", "value": candidate.output.description}

    @local_operation
    def begin_attempt(
        self,
        request_identity: str,
        binding: DescriptionAdviceBinding,
        engine: str,
        model: str,
        now: int,
        case_current: Callable[[], bool],
        *,
        retry: bool = False,
    ) -> tuple[str, DescriptionAdviceCandidate | None] | None:
        """Persist before dispatch; an ambiguous predecessor never implies retry."""
        transaction = self._transaction("description_attempt")
        try:
            transaction.begin()
            if not case_current() or not self._attempt_is_current(
                request_identity, binding, engine, model
            ):
                transaction.commit()
                return None
            retry = retry or self.pending_retry(binding.review_case_id)
            self.consume_pending(binding.review_case_id)
            prior = self._connection.execute(
                "SELECT attempt_id,state,binding_document FROM description_advice_attempts WHERE request_identity=? ORDER BY rowid DESC LIMIT 1",
                (request_identity,),
            ).fetchone()
            if prior is not None and (
                (not retry and prior[1] != "succeeded")
                or prior[1] in {"requested", "running"}
            ):
                self._bind(binding, prior[0], None)
                if prior[1] in {"requested", "running"} and prior[
                    2
                ] != description_document(binding.model_dump()):
                    self._connection.execute(
                        "INSERT INTO description_preparation_pending VALUES(?,0,?)",
                        (binding.review_case_id, now),
                    )
                transaction.commit()
                return None
            candidate_row = self._connection.execute(
                "SELECT c.attempt_id,c.document FROM description_advice_candidates c "
                "JOIN description_advice_attempts a ON a.attempt_id=c.attempt_id "
                "WHERE c.request_identity=? AND a.state='succeeded' ORDER BY a.rowid DESC LIMIT 1",
                (request_identity,),
            ).fetchone()
            if candidate_row is not None and not retry:
                candidate = DescriptionAdviceCandidate.from_json(candidate_row[1])
                self._bind(binding, candidate_row[0], candidate.candidate_id)
                transaction.commit()
                return candidate_row[0], candidate
            attempt_id = "description_attempt_" + uuid.uuid4().hex
            self._connection.execute(
                "INSERT INTO description_advice_attempts VALUES(?,?,?,?,?,'requested',NULL,?,?)",
                (
                    attempt_id,
                    request_identity,
                    description_document(binding.model_dump()),
                    engine,
                    model,
                    now,
                    now,
                ),
            )
            self._bind(binding, attempt_id, None)
            transaction.commit()
            return attempt_id, None
        except BaseException:
            transaction.rollback()
            raise

    def _bind(
        self,
        binding: DescriptionAdviceBinding,
        attempt_id: str,
        candidate_id: str | None,
    ) -> None:
        self._connection.execute(
            "INSERT INTO current_description_advice VALUES(?,?,?,?) "
            "ON CONFLICT(review_case_id) DO UPDATE SET attempt_id=excluded.attempt_id,candidate_id=excluded.candidate_id,binding_document=excluded.binding_document",
            (
                binding.review_case_id,
                attempt_id,
                candidate_id,
                description_document(binding.model_dump()),
            ),
        )

    @local_operation
    def start(
        self, attempt_id: str, now: int, case_current: Callable[[], bool]
    ) -> bool:
        transaction = self._transaction("description_start")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT request_identity,binding_document,engine,model FROM description_advice_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                transaction.commit()
                return False
            binding = DescriptionAdviceBinding.model_validate_json(row[1])
            if not case_current() or not self._attempt_is_current(
                row[0], binding, row[2], row[3]
            ):
                self._connection.execute(
                    "UPDATE description_advice_attempts SET state='interrupted',failure='interrupted',updated_at=? WHERE attempt_id=? AND state='requested'",
                    (now, attempt_id),
                )
                transaction.commit()
                return False
            changed = self._connection.execute(
                "UPDATE description_advice_attempts SET state='running',updated_at=? WHERE attempt_id=? AND state='requested'",
                (now, attempt_id),
            ).rowcount
            transaction.commit()
            return changed == 1
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def finish(
        self,
        attempt_id: str,
        candidate: DescriptionAdviceCandidate | None,
        now: int,
        case_current: Callable[[], bool],
        *,
        failure: Literal["unavailable", "invalid_output"] | None = None,
    ) -> bool:
        if (candidate is None) != (failure is not None) or failure not in {
            None,
            "unavailable",
            "invalid_output",
        }:
            raise ValueError("description result failure mismatch")
        transaction = self._transaction("description_finish")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT request_identity,binding_document,engine,model FROM description_advice_attempts WHERE attempt_id=? AND state='running'",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise ValueError("description attempt is not running")
            binding = DescriptionAdviceBinding.model_validate_json(row[1])
            if candidate is not None:
                DescriptionAdviceCandidate.from_json(candidate.to_json())
                if (
                    candidate.attempt_id,
                    candidate.request_identity,
                    candidate.engine,
                    candidate.model,
                ) != (
                    attempt_id,
                    row[0],
                    row[2],
                    row[3],
                ):
                    raise ValueError("description result binding mismatch")
                self._connection.execute(
                    "INSERT INTO description_advice_candidates VALUES(?,?,?,?)",
                    (
                        candidate.candidate_id,
                        attempt_id,
                        candidate.request_identity,
                        candidate.to_json(),
                    ),
                )
            self._connection.execute(
                "UPDATE description_advice_attempts SET state=?,failure=?,updated_at=? WHERE attempt_id=?",
                (
                    "succeeded" if candidate is not None else "failed",
                    failure,
                    now,
                    attempt_id,
                ),
            )
            admitted = case_current() and self._attempt_is_current(
                row[0], binding, row[2], row[3]
            )
            if candidate is not None and admitted:
                self._connection.execute(
                    "UPDATE current_description_advice SET candidate_id=? WHERE review_case_id=? AND attempt_id=? AND binding_document=?",
                    (
                        candidate.candidate_id,
                        binding.review_case_id,
                        attempt_id,
                        row[1],
                    ),
                )
            if self._task_configured():
                for (case_id,) in self._connection.execute(
                    "SELECT review_case_id FROM current_description_advice WHERE attempt_id=? AND candidate_id IS NULL AND binding_document<>?",
                    (attempt_id, row[1]),
                ).fetchall():
                    request_description_preparation(
                        self._connection,
                        now,
                        review_case_id=case_id,
                        preserve_pending=True,
                    )
            transaction.commit()
            return admitted
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def request_retry(self, review_case_id: str, now: int) -> tuple[str, str]:
        transaction = self._transaction("retry_description_advice")
        try:
            transaction.begin()
            lineage = self._connection.execute(
                "SELECT connection_name,library_name FROM current_review_case_lineages WHERE review_case_id=?",
                (review_case_id,),
            ).fetchone()
            active = self._connection.execute(
                "SELECT 1 FROM description_advice_attempts WHERE json_extract(binding_document,'$.review_case_id')=? AND state IN ('requested','running') LIMIT 1",
                (review_case_id,),
            ).fetchone()
            if lineage is None or active is not None or not self._task_configured():
                raise ValueError("description retry unavailable")
            report_row = self._connection.execute(
                "SELECT m.document FROM review_cases c JOIN metadata_reports m ON m.report_id=c.report_id WHERE c.review_case_id=?",
                (review_case_id,),
            ).fetchone()
            if report_row is None:
                raise ValueError("description retry unavailable")
            report = MetadataReport.from_json(report_row[0])
            if report.audio is not None and not admitted_audiobook_identity(report):
                raise ValueError("description retry unavailable")
            request_description_preparation(
                self._connection, now, review_case_id=review_case_id, retry=True
            )
            transaction.commit()
            return str(lineage[0]), str(lineage[1])
        except BaseException:
            transaction.rollback()
            raise

    def _task_configured(self) -> bool:
        from mechanical_librarian.domain.reviewer_access import ReviewerAccessDocument

        row = self._connection.execute(
            "SELECT document FROM reviewer_access WHERE singleton=1"
        ).fetchone()
        return (
            row is not None
            and ReviewerAccessDocument.model_validate_json(row[0]).description_rewrite
            is not None
        )

    @local_operation
    def consume_pending_if_unconfigured(self, review_case_id: str) -> None:
        transaction = self._transaction("cancel_description_preparation")
        try:
            transaction.begin()
            if not self._task_configured():
                self.consume_pending(review_case_id)
            transaction.commit()
        except BaseException:
            transaction.rollback()
            raise

    @local_operation
    def pending_retry(self, review_case_id: str) -> bool:
        row = self._connection.execute(
            "SELECT retry FROM description_preparation_pending WHERE review_case_id=?",
            (review_case_id,),
        ).fetchone()
        return bool(row == (1,))

    @local_operation
    def consume_pending(self, review_case_id: str) -> None:
        self._connection.execute(
            "DELETE FROM description_preparation_pending WHERE review_case_id=?",
            (review_case_id,),
        )

    @local_operation
    def interrupt_recovered(self, now: int) -> None:
        """Interrupt retained attempts at the runtime artifact-recovery boundary."""
        self._connection.execute(
            "UPDATE description_advice_attempts SET state='interrupted',failure='interrupted',updated_at=max(updated_at,?) WHERE state IN ('requested','running')",
            (now,),
        )

    @local_operation
    def read(self, review_case_id: str) -> dict[str, object] | None:
        row = self._connection.execute(
            "SELECT a.state,a.failure,a.engine,a.model,a.attempt_id,c.document,p.binding_document,p.candidate_id "
            "FROM current_description_advice p JOIN description_advice_attempts a ON a.attempt_id=p.attempt_id "
            "LEFT JOIN description_advice_candidates c ON c.attempt_id=p.attempt_id WHERE p.review_case_id=?",
            (review_case_id,),
        ).fetchone()
        if row is None:
            return None
        binding = DescriptionAdviceBinding.model_validate_json(row[6])
        selected = self._connection.execute(
            "SELECT d.document,c.document FROM current_description_decisions p "
            "JOIN description_decisions d ON d.decision_id=p.decision_id "
            "JOIN description_advice_candidates c ON c.candidate_id=d.candidate_id "
            "WHERE p.review_case_id=?",
            (review_case_id,),
        ).fetchone()
        selection = (
            None
            if selected is None
            else AuthoredDescriptionDecision.from_json(selected[0])
        )
        candidate = (
            None if row[5] is None else DescriptionAdviceCandidate.from_json(row[5])
        )
        selected_advice = None
        if selection is not None:
            assert selected is not None
            selected_candidate = DescriptionAdviceCandidate.from_json(selected[1])
            selected_advice = {
                "candidate": selected_candidate.model_dump(),
                "binding": selection.binding.model_dump(),
                "current": self._candidate_is_current(
                    selected_candidate, selection.binding
                ),
            }
        application_outcome = None
        if selection is not None:
            applied = self._connection.execute(
                "SELECT 'verified' FROM field_effects WHERE decision_id=? AND state='verified' "
                "UNION ALL SELECT 'no_change_required' FROM field_effect_no_changes WHERE decision_id=? LIMIT 1",
                (selection.decision_id, selection.decision_id),
            ).fetchone()
            if applied is not None:
                application_outcome = applied[0]
        return {
            "current": self.binding_is_current(binding)
            if candidate is None
            else row[7] == candidate.candidate_id
            and self._candidate_is_current(candidate, binding),
            "selection": None if selection is None else selection.model_dump(),
            "selected_advice": selected_advice,
            "application_outcome": application_outcome,
            "state": row[0],
            "failure": row[1],
            "engine": row[2],
            "model": row[3],
            "attempt_id": row[4],
            "candidate": None if candidate is None else candidate.model_dump(),
            "binding": DescriptionAdviceBinding.model_validate_json(
                row[6]
            ).model_dump(),
        }


def request_description_preparation(
    connection: sqlite3.Connection,
    now: int,
    *,
    review_case_id: str | None = None,
    retry: bool = False,
    preserve_pending: bool = False,
) -> None:
    """Record intent and request only existing scans in the caller's transaction."""
    from mechanical_librarian.adapters.persistence.review_scans import (
        SQLiteReviewScanRepository,
    )

    rows = connection.execute(
        "SELECT c.review_case_id,c.connection_name,c.library_name,s.library_id,s.producer_fingerprint "
        "FROM current_review_case_lineages c LEFT JOIN review_preparation_scans s "
        "ON s.connection_name=c.connection_name AND s.library_name=c.library_name "
        "WHERE c.review_case_id IS NOT NULL AND (? IS NULL OR c.review_case_id=?) "
        "UNION SELECT w.review_case_id,w.connection_name,w.library_name,s.library_id,s.producer_fingerprint "
        "FROM review_preparation_work w JOIN review_preparation_scans s ON s.scan_id=w.scan_id "
        "JOIN current_description_advice p ON p.review_case_id=w.review_case_id "
        "AND json_extract(p.binding_document,'$.report_id')=w.report_id "
        "AND json_extract(p.binding_document,'$.proposal_set_id')=w.proposal_set_id "
        "WHERE w.stage='projection' AND w.state='running' AND w.review_case_id=?",
        (review_case_id, review_case_id, review_case_id),
    ).fetchall()
    scans = set()
    for case_id, target, library, library_id, producer in rows:
        connection.execute(
            "INSERT INTO description_preparation_pending VALUES(?,?,?) "
            + (
                "ON CONFLICT(review_case_id) DO NOTHING"
                if preserve_pending
                else "ON CONFLICT(review_case_id) DO UPDATE SET retry=max(retry,excluded.retry),requested_at=excluded.requested_at"
            ),
            (case_id, int(retry), now),
        )
        if library_id is not None and producer is not None:
            scans.add((target, library, library_id, producer))
    if connection.execute(
        "SELECT paused FROM review_preparation_control WHERE singleton=1"
    ).fetchone() == (1,):
        return
    repository = SQLiteReviewScanRepository(connection)
    for target, library, library_id, producer in sorted(scans):
        repository.request_review_scan(target, library, library_id, producer, now)


def validate_description_history(connection: sqlite3.Connection) -> None:
    """Startup validates document identities and cross-table immutable bindings."""
    from mechanical_librarian.domain.description_advice import (
        AuthoredDescriptionDecision,
    )

    def validate_binding(document: str) -> DescriptionAdviceBinding:
        binding = DescriptionAdviceBinding.model_validate_json(document)
        case = connection.execute(
            "SELECT report_id,proposal_set_id,document FROM review_cases WHERE review_case_id=?",
            (binding.review_case_id,),
        ).fetchone()
        if (
            description_document(binding.model_dump()) != document
            or case is None
            or case[:2] != (binding.report_id, binding.proposal_set_id)
            or json.loads(case[2])["target_fingerprint"] != binding.target_fingerprint
        ):
            raise ValueError("invalid description binding")
        return binding

    for (
        attempt_id,
        request_identity,
        binding_document,
        state,
        failure,
    ) in connection.execute(
        "SELECT attempt_id,request_identity,binding_document,state,failure FROM description_advice_attempts"
    ):
        validate_binding(binding_document)
        count = connection.execute(
            "SELECT count(*) FROM description_advice_candidates WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()[0]
        if (
            re.fullmatch(r"description_attempt_[0-9a-f]{32}", attempt_id) is None
            or re.fullmatch(r"[0-9a-f]{64}", request_identity) is None
            or count != (1 if state == "succeeded" else 0)
            or (state in {"requested", "running", "succeeded"} and failure is not None)
            or (state == "failed" and failure not in {"invalid_output", "unavailable"})
            or (state == "interrupted" and failure != "interrupted")
        ):
            raise ValueError("invalid description attempt")

    for candidate_id, attempt_id, request_identity, document in connection.execute(
        "SELECT candidate_id,attempt_id,request_identity,document FROM description_advice_candidates"
    ):
        candidate = DescriptionAdviceCandidate.from_json(document)
        if (
            candidate.candidate_id,
            candidate.attempt_id,
            candidate.request_identity,
        ) != (
            candidate_id,
            attempt_id,
            request_identity,
        ):
            raise ValueError("invalid description history")
        attempt = connection.execute(
            "SELECT request_identity,engine,model,state FROM description_advice_attempts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if attempt != (
            request_identity,
            candidate.engine,
            candidate.model,
            "succeeded",
        ):
            raise ValueError("invalid description history")
    for case_id, attempt_id, candidate_id, binding_document in connection.execute(
        "SELECT review_case_id,attempt_id,candidate_id,binding_document FROM current_description_advice"
    ):
        binding = validate_binding(binding_document)
        if candidate_id is not None and connection.execute(
            "SELECT attempt_id FROM description_advice_candidates WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone() != (attempt_id,):
            raise ValueError("invalid description binding")
        if (
            binding.review_case_id != case_id
            or description_document(binding.model_dump()) != binding_document
        ):
            raise ValueError("invalid description binding")
    for (
        decision_id,
        case_id,
        candidate_id,
        idempotency_digest,
        document,
    ) in connection.execute(
        "SELECT decision_id,review_case_id,candidate_id,idempotency_digest,document FROM description_decisions"
    ):
        decision = AuthoredDescriptionDecision.from_json(document)
        validate_binding(description_document(decision.binding.model_dump()))
        if (
            decision.decision_id,
            decision.review_case_id,
            decision.candidate_id,
            decision.idempotency_digest,
        ) != (decision_id, case_id, candidate_id, idempotency_digest):
            raise ValueError("invalid description decision")

    if (
        connection.execute(
            "SELECT 1 FROM current_description_decisions current JOIN description_decisions decision "
            "ON decision.decision_id=current.decision_id WHERE current.review_case_id<>decision.review_case_id LIMIT 1"
        ).fetchone()
        is not None
    ):
        raise ValueError("invalid current description decision")
