"""Durable description attempts, immutable text and independently current bindings."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Callable
from typing import Literal

from mechanical_librarian.adapters.persistence.owner_field_answers import (
    case_episode,
    case_record_sql,
    next_ordinal,
    owner_row_counts_sql,
    record_answer,
    resumed_ordinal_sql,
)
from mechanical_librarian.adapters.persistence.owner_links import owner_link_bound
from mechanical_librarian.adapters.persistence.review_sql import (
    _AUTOMATIC_SAFETY_ROW_SQL,
    _CASE_AUTHORS_SQL,
    _CASE_TITLE_SQL,
    _authored_description_sql,
)
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    local_operation,
)
from mechanical_librarian.adapters.providers.visible_text import read_description
from mechanical_librarian.domain.audiobook_corroboration import (
    admitted_audiobook_identity,
)
from mechanical_librarian.domain.automatic_decision import (
    AUTOMATIC_POSTURE,
    automatic_safety_row_permits,
)
from mechanical_librarian.domain.description_advice import (
    DESCRIPTION_INSTRUCTION,
    DESCRIPTION_OUTPUT_SCHEMA,
    AuthoredDescriptionDecision,
    DescriptionAdviceBinding,
    DescriptionAdviceCandidate,
    DescriptionAdviceInput,
    DescriptionAdviceSettings,
    automatic_description_floor_met,
    description_advice_qualified,
    description_digest,
    description_document,
    description_qualification_manifest,
)
from mechanical_librarian.domain.human_decision import HumanDecisionError
from mechanical_librarian.domain.metadata_report import MetadataReport

# Whether the case's description field offers the owner a choice: the same
# fact, read from the current offer, that makes the field editable and offers
# Keep current. Description advice never decides a field the owner could not
# have decided or reversed (configuration.md, Description advice); a field
# every source repeats offers both beside advice (`_description_choosable`).
_DESCRIPTION_CHOICE_SQL = (
    "SELECT 1 FROM review_case_field_facts WHERE review_case_id=? "
    "AND destination='description' AND has_owner_decision_actions=1"
)

# What keeps automatic advice from deciding the case's description (17b): an
# owner description decision, or the owner's chosen advice, that Resume has
# not taken back, or a pin on the record (ADR-0085, Amendment 2; ADR-0108).
_OWNER_KEEPS_ADVICE_OUT_SQL = (
    "SELECT 1 FROM human_decisions human WHERE human.review_case_id=:case "
    "AND human.destination='description' AND "
    + owner_row_counts_sql("human")
    + " UNION ALL SELECT 1 FROM current_description_decisions chosen "
    "JOIN owner_authored_descriptions authored "
    "ON authored.decision_id=chosen.decision_id WHERE chosen.review_case_id=:case "
    "AND NOT authored.ordinal<=COALESCE("
    + resumed_ordinal_sql("authored", "'description'")
    + ",0) UNION ALL "
    + case_record_sql(":case", "'description'")
    + " AND record.pin_fingerprint IS NOT NULL LIMIT 1"
)


class SQLiteDescriptionAdviceRepository(ConnectionScoped):
    def __init__(
        self,
        connection: sqlite3.Connection,
        revoke: Callable[[str, int], object] | None = None,
    ) -> None:
        super().__init__(connection)
        self._revoke = revoke
        self._settings = DescriptionAdviceSettings()
        # The bound setting stands until owner settings are saved; Resume's
        # offer reads it through this, as `current_settings` does (C2b-13).
        connection.create_function(
            "ml_description_automatic_application",
            0,
            lambda: int(self._settings.automatic_application),
        )
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
                _authored_description_sql(
                    "SELECT document FROM {table} WHERE idempotency_digest=?"
                ),
                (idempotency_digest, idempotency_digest),
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
            candidate_document = row[0]
            candidate = DescriptionAdviceCandidate.from_json(candidate_document)
            if not self._candidate_is_current(candidate, expected_binding):
                raise ValueError("description candidate request mismatch")
            if advised_description(candidate.output.description) is None:
                # Text the reader refuses is never written (#1995, D3).
                raise ValueError("description advice unreadable")
            case_id = expected_binding.review_case_id
            prior = self._connection.execute(
                _authored_description_sql(
                    "SELECT d.document FROM current_description_decisions p JOIN {table} d ON d.decision_id=p.decision_id WHERE p.review_case_id=?"
                ),
                (case_id, case_id),
            ).fetchone()
            choosable = self._description_choosable(case_id)
            if origin == "automatic_policy":
                if self._automatic_admission_refused(candidate, case_id, choosable):
                    raise ValueError("description automatic admission unavailable")
            elif origin != "human":
                raise ValueError("invalid description authority")
            elif not owner_link_bound(self._connection, case_id):
                # The book link changed since the case's report (#1477 L9).
                raise HumanDecisionError("link_changed")
            elif not choosable:
                # The review page offers no choice here, so the owner could
                # neither make this one nor reverse it with Keep current.
                raise ValueError("description choice unavailable")
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
                qualification_manifest_sha256=description_qualification_manifest(
                    self.current_settings().cleanup()
                ),
                idempotency_digest=idempotency_digest,
                occurred_at=now,
            )
            for decision_id in competing:
                if self._revoke is None:
                    raise ValueError("description revocation unavailable")
                self._revoke(decision_id, now)
            row_values = (
                decision.decision_id,
                case_id,
                candidate_id,
                idempotency_digest,
                decision.to_json(),
            )
            if origin == "human":
                # The owner's choice is ledger: it keeps its work and the
                # chosen candidate verbatim (ADR-0108).
                work = self._connection.execute(
                    "SELECT json_extract(document,'$.target.connection'),"
                    "json_extract(document,'$.target.library'),"
                    "json_extract(document,'$.target.item') "
                    "FROM review_cases WHERE review_case_id=?",
                    (case_id,),
                ).fetchone()
                if work is None:
                    raise ValueError("description advice is stale")
                # It is an owner answer: its ordinal is the counter's and
                # its episode, title and authors are its case's now
                # (ADR-0108).
                self._connection.execute(
                    "INSERT INTO owner_authored_descriptions(decision_id,review_case_id,"
                    "candidate_id,idempotency_digest,document,connection_name,"
                    "library_name,item_id,candidate_document,ordinal,episode,"
                    "display_title,display_authors) VALUES(?,?,?,?,?,?,?,?,?,?,?,"
                    + _CASE_TITLE_SQL
                    + ","
                    + _CASE_AUTHORS_SQL
                    + ")",
                    (
                        *row_values,
                        *work,
                        candidate_document,
                        next_ordinal(self._connection),
                        case_episode(self._connection, case_id),
                        case_id,
                        case_id,
                    ),
                )
            else:
                self._connection.execute(
                    "INSERT INTO automatic_description_decisions(decision_id,"
                    "review_case_id,candidate_id,idempotency_digest,document) "
                    "VALUES(?,?,?,?,?)",
                    row_values,
                )
            self._connection.execute(
                "INSERT INTO current_description_decisions VALUES(?,?) ON CONFLICT(review_case_id) DO UPDATE SET decision_id=excluded.decision_id",
                (case_id, decision.decision_id),
            )
            if origin == "human":
                # The owner's choice is the field's answer in the record
                # (ADR-0108).
                record_answer(
                    self._connection, "authored_description", decision.decision_id
                )

            transaction.commit()
            return decision
        except BaseException:
            transaction.rollback()
            raise

    def _automatic_admission_refused(
        self, candidate: DescriptionAdviceCandidate, case_id: str, choosable: bool
    ) -> bool:
        """Whether automatic advice may not become the case's description
        decision (17b).

        Automatic application, a shipped record naming the adviser (ADR-0099
        Decision 10), ready advice above the floor, a choosable field, the
        Automatic posture and the shared safety row must all hold, and no
        owner answer Resume has not taken back, nor a pin, may stand: an
        owner description decision of the case, the owner's chosen advice on
        it, or a pin on the record (ADR-0085, Amendment 2; ADR-0108). The
        record's answer alone does not refuse the decision: a later case
        records it under that answer, and authority_value holds the write.
        """
        safety = self._connection.execute(_AUTOMATIC_SAFETY_ROW_SQL).fetchone()
        posture = self._connection.execute(
            "SELECT posture FROM metadata_policy_state WHERE singleton=1"
        ).fetchone()
        return (
            not self.current_settings().automatic_application
            or not description_advice_qualified(candidate)
            or candidate.output.disposition != "ready"
            or not automatic_description_floor_met(candidate.output.description)
            or self._connection.execute(
                _OWNER_KEEPS_ADVICE_OUT_SQL, {"case": case_id}
            ).fetchone()
            is not None
            or not choosable
            or posture != (AUTOMATIC_POSTURE,)
            or safety is None
            or not automatic_safety_row_permits(safety, "description")
        )

    def _description_choosable(self, case_id: str) -> bool:
        """Whether the description offers the owner a choice with its advice.

        The field's facts say so, or every source repeats the current text,
        where the field offers the adviser's text and Keep current only beside
        advice (#1905). The live proposal stands for the current offer without
        reading level storage (ADR-0096 "Readers"): a level never substitutes
        a description whose alternatives all repeat it, and a substituted one
        has a differing alternative, so its facts already say choosable
        (amendment 24). Advice exists only for an admitted case, so the read
        needs no recording check.
        """
        from mechanical_librarian.domain.human_decision import preserved_description
        from mechanical_librarian.domain.review_case import ReviewCase, current_offer

        if self._connection.execute(_DESCRIPTION_CHOICE_SQL, (case_id,)).fetchone():
            return True
        row = self._connection.execute(
            "SELECT document FROM review_cases WHERE review_case_id=?", (case_id,)
        ).fetchone()
        return row is not None and any(
            preserved_description(proposal)
            for proposal in current_offer(ReviewCase.from_json(row[0]), None)
        )

    @local_operation
    def authority_value(
        self, decision: AuthoredDescriptionDecision
    ) -> tuple[object, str | None]:
        """The advised value a decision writes, and its formatted copy.

        The adviser's text is read as any description is: its visible text is
        the value, and markup it keeps is written as the copy (#1995).
        """
        from mechanical_librarian.domain.field_effect import FieldEffectError

        if type(
            decision
        ) is not AuthoredDescriptionDecision or not self.binding_is_current(
            decision.binding
        ):
            raise FieldEffectError("authority_binding")
        row = self._connection.execute(
            _authored_description_sql(
                "SELECT d.document,c.document FROM current_description_decisions p JOIN {table} d ON d.decision_id=p.decision_id JOIN description_advice_candidates c ON c.candidate_id=d.candidate_id WHERE p.review_case_id=? AND p.decision_id=?"
            ),
            (decision.review_case_id, decision.decision_id) * 2,
        ).fetchone()
        if row is None or row[0] != decision.to_json():
            raise FieldEffectError("authority_binding")
        candidate = DescriptionAdviceCandidate.from_json(row[1])
        if not self._candidate_is_current(candidate, decision.binding):
            raise FieldEffectError("authority_binding")
        if decision.origin == "automatic_policy":
            safety = self._connection.execute(_AUTOMATIC_SAFETY_ROW_SQL).fetchone()
            posture = self._connection.execute(
                "SELECT posture FROM metadata_policy_state WHERE singleton=1"
            ).fetchone()
            # The record's answer for the description holds the write until
            # Resume completes (ADR-0108).
            owner_final = self._connection.execute(
                case_record_sql("?", "'description'"), (decision.review_case_id,)
            ).fetchone()
            # An owner's choice keeps its authority when the offer changes;
            # an automatic one writes only while the field offers a choice.
            choosable = self._description_choosable(decision.review_case_id)
            if (
                not self.current_settings().automatic_application
                # A stored automatic decision writes only while a shipped
                # record names its adviser (ADR-0099 Decision 10).
                or not description_advice_qualified(candidate)
                or candidate.output.disposition != "ready"
                or not automatic_description_floor_met(candidate.output.description)
                or owner_final is not None
                or not choosable
                or posture != (AUTOMATIC_POSTURE,)
                or safety is None
                or not automatic_safety_row_permits(safety, "description")
                or decision.qualification_manifest_sha256
                != description_qualification_manifest(self.current_settings().cleanup())
            ):
                raise FieldEffectError("authority_binding")
        advised = advised_description(candidate.output.description)
        if advised is None:
            raise FieldEffectError("authority_binding")
        return {"kind": "text", "value": advised[0]}, advised[1]

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
        reuse_only: bool = False,
    ) -> tuple[str, DescriptionAdviceCandidate | None] | None:
        """Persist before dispatch; an ambiguous predecessor never implies retry.

        With `reuse_only` an earlier attempt or candidate is bound as usual,
        but no attempt is created, no retry is taken and a pending request is
        left in place (#1355).
        """
        transaction = self._transaction("description_attempt")
        try:
            transaction.begin()
            if not case_current() or not self._attempt_is_current(
                request_identity, binding, engine, model
            ):
                transaction.commit()
                return None
            if reuse_only:
                retry = False
            else:
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
                    # A reuse-only preparation left any pending request.
                    self._connection.execute(
                        "INSERT OR IGNORE INTO description_preparation_pending "
                        "VALUES(?,0,?)",
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
            if reuse_only:
                transaction.commit()
                return None
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
            _authored_description_sql(
                "SELECT d.document,c.document FROM current_description_decisions p "
                "JOIN {table} d ON d.decision_id=p.decision_id "
                "JOIN description_advice_candidates c ON c.candidate_id=d.candidate_id "
                "WHERE p.review_case_id=?"
            ),
            (review_case_id, review_case_id),
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
                **_advised_reading(selected_candidate),
            }
        advised = (
            None
            if candidate is None
            else advised_description(candidate.output.description)
        )
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
            # How the advice shows and is written (#1995): its formatted copy,
            # and whether the reader refused it, so it can't be chosen.
            "formatted": None if advised is None else advised[1],
            "visible_text": None if advised is None else advised[0],
            "readable": candidate is None or advised is not None,
            "binding": DescriptionAdviceBinding.model_validate_json(
                row[6]
            ).model_dump(),
        }


def advised_description(text: str) -> tuple[str, str | None] | None:
    """An adviser's text as it is written: its visible text and formatted
    copy, or `None` where the description reader refuses it (#1995, D3).
    """
    shown, _, formatted, _ = read_description(text)
    return (shown, formatted) if type(shown) is str else None


def _advised_reading(candidate: DescriptionAdviceCandidate) -> dict[str, str | None]:
    """A chosen advice's formatted copy and visible text, as it is written."""
    advised = advised_description(candidate.output.description)
    return {
        "formatted": None if advised is None else advised[1],
        "visible_text": None if advised is None else advised[0],
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
        "SELECT paused FROM review_preparation_owner_control WHERE singleton=1"
    ).fetchone() == (1,):
        return
    repository = SQLiteReviewScanRepository(connection)
    for target, library, library_id, producer in sorted(scans):
        # Even during the first preparation: the poll would not scan a library
        # whose first scan has ended until it finishes, or ever after failures.
        repository.request_review_scan(
            target, library, library_id, producer, now, waiting_work=True
        )


def pending_description_libraries(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str], ...]:
    """Name the libraries whose current cases have description work waiting."""
    return tuple(
        (target, library)
        for target, library in connection.execute(
            "SELECT DISTINCT c.connection_name,c.library_name "
            "FROM description_preparation_pending p JOIN current_review_case_lineages c "
            "ON c.review_case_id=p.review_case_id ORDER BY 1,2"
        ).fetchall()
    )


def validate_description_history(connection: sqlite3.Connection) -> None:
    """Startup validates document identities and cross-table immutable bindings."""
    from mechanical_librarian.domain.description_advice import (
        AuthoredDescriptionDecision,
    )

    def validate_binding(
        document: str, *, case_required: bool = True
    ) -> DescriptionAdviceBinding:
        """The binding is canonical and, where its case is there, names it.

        Only the owner's decision, which is ledger, may outlive its case
        (ADR-0108); every derived row's case must be there.
        """
        binding = DescriptionAdviceBinding.model_validate_json(document)
        case = connection.execute(
            "SELECT report_id,proposal_set_id,document FROM review_cases WHERE review_case_id=?",
            (binding.review_case_id,),
        ).fetchone()
        if (
            description_document(binding.model_dump()) != document
            or (case is None and case_required)
            or (
                case is not None
                and (
                    case[:2] != (binding.report_id, binding.proposal_set_id)
                    or json.loads(case[2])["target_fingerprint"]
                    != binding.target_fingerprint
                )
            )
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
    # The owner's half keeps its work and the chosen candidate; Automatic's
    # names the candidate row. Each row's origin is its half's (ADR-0108).
    for origin, (
        decision_id,
        case_id,
        candidate_id,
        idempotency_digest,
        document,
        work,
        candidate_document,
    ) in (
        *(
            ("human", row)
            for row in connection.execute(
                "SELECT decision_id,review_case_id,candidate_id,idempotency_digest,document,"
                "json_array(connection_name,library_name,item_id),candidate_document "
                "FROM owner_authored_descriptions"
            )
        ),
        *(
            ("automatic_policy", row)
            for row in connection.execute(
                "SELECT decision_id,review_case_id,candidate_id,idempotency_digest,document,"
                "NULL,NULL FROM automatic_description_decisions"
            )
        ),
    ):
        decision = AuthoredDescriptionDecision.from_json(document)
        validate_binding(
            description_document(decision.binding.model_dump()),
            case_required=origin != "human",
        )
        if (
            decision.decision_id,
            decision.review_case_id,
            decision.candidate_id,
            decision.idempotency_digest,
            decision.origin,
        ) != (decision_id, case_id, candidate_id, idempotency_digest, origin):
            raise ValueError("invalid description decision")
        if origin == "human":
            # The copies are its own: the candidate is the one it names and
            # the work its case's. The case and the candidate are derived:
            # while each is there, the copy must equal it (ADR-0108).
            copied = DescriptionAdviceCandidate.from_json(candidate_document)
            target = connection.execute(
                "SELECT json_array(json_extract(document,'$.target.connection'),"
                "json_extract(document,'$.target.library'),"
                "json_extract(document,'$.target.item')) "
                "FROM review_cases WHERE review_case_id=?",
                (case_id,),
            ).fetchone()
            named = connection.execute(
                "SELECT document FROM description_advice_candidates WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
            if (
                copied.candidate_id != candidate_id
                or target not in {None, (work,)}
                or named not in {None, (candidate_document,)}
            ):
                raise ValueError("invalid description decision")

    # The case's chosen advice names a decision of its own case in exactly
    # one half; no key binds it to either, so open checks it (ADR-0108).
    if (
        connection.execute(
            _authored_description_sql(
                "SELECT 1 FROM current_description_decisions current JOIN {table} decision "
                "ON decision.decision_id=current.decision_id WHERE current.review_case_id<>decision.review_case_id"
            )
            + " UNION ALL SELECT 1 FROM current_description_decisions current "
            "WHERE (SELECT count(*) FROM ("
            + _authored_description_sql(
                "SELECT 1 FROM {table} WHERE decision_id=current.decision_id"
            )
            + "))<>1 LIMIT 1"
        ).fetchone()
        is not None
    ):
        raise ValueError("invalid current description decision")
