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
from mechanical_librarian.adapters.persistence.field_effect_validation import (
    _automatic_decision_value,
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
    FIELD_EFFECT_FAILURES,
    SUPPORTED_DESTINATIONS,
    EffectAuthority,
    EffectConsent,
    FieldEffectError,
    FieldEffectNoChange,
    FieldEffectOperation,
    field_value_version,
    project_decision_value,
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
    ProviderStatus,
    ReportValueError,
)
from mechanical_librarian.domain.proposal_set import (
    ProposalSet,
    ProposalValueError,
)
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionStaleError,
    ReviewCase,
    ReviewCaseValueError,
)
from mechanical_librarian.domain.review_field_classification import ReviewFieldCause
from mechanical_librarian.domain.rich_metadata_preview import (
    RichMetadataPreviewError,
)
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
        """Resolve one exact persisted decision against its current case lineage."""
        if not self._review_case_accepts_authority(decision.review_case_id):
            raise FieldEffectError("authority_binding")
        table = "human_decisions" if type(decision) is HumanDecision else "automatic_decisions"
        row = self._connection.execute(
            f"SELECT document FROM {table} WHERE decision_id=?", (decision.decision_id,)
        ).fetchone()
        if row is None or row[0] != decision.to_json():
            raise FieldEffectError("authority_binding")
        envelope = json.loads(self.get_review_case(decision.review_case_id))
        if (type(envelope) is not dict or envelope.get("current") is not True
                or type(envelope.get("case")) is not dict):
            raise FieldEffectError("authority_binding")
        case = ReviewCase.from_json(_canonical_json(envelope["case"]))
        # ``value`` carries either a human selection envelope or the projected
        # automatic envelope, so it is declared at the widest of the two.
        value: object
        if type(decision) is HumanDecision:
            decision.validate_for(case)
            value = decision.selected_value
        else:
            # The declared parameter union plus the exact-type test above leave
            # only AutomaticDecision on this branch.
            automatic = cast(AutomaticDecision, decision)
            report_row = self._connection.execute(
                "SELECT document FROM metadata_reports WHERE report_id=?",
                (automatic.report_id,),
            ).fetchone()
            proposal_row = self._connection.execute(
                "SELECT document FROM proposal_sets WHERE proposal_set_id=?",
                (automatic.proposal_set_id,),
            ).fetchone()
            if report_row is None or proposal_row is None:
                raise FieldEffectError("authority_binding")
            try:
                value = _automatic_decision_value(
                    automatic, case, MetadataReport.from_json(report_row[0]),
                    ProposalSet.from_json(proposal_row[0]),
                )
            except (
                ReportValueError, ProposalValueError, RichMetadataPreviewError,
            ) as error:
                raise FieldEffectError("authority_binding") from error
        lineage = self._connection.execute(
            "SELECT connection_name,library_name,item_id FROM current_review_case_lineages "
            "WHERE review_case_id=?", (decision.review_case_id,),
        ).fetchone()
        if lineage is None:
            raise FieldEffectError("authority_binding")
        target_fields = case.target.get("fields")
        target_field = next(
            (
                # ReviewCase.target is always TargetFacts.to_data(), whose
                # "fields" key is a list of field objects.
                item for item in cast("list[object] | None", target_fields) or ()
                if type(item) is dict
                and item.get("destination") == decision.destination
            ),
            None,
        )
        if (type(target_fields) is not list or type(target_field) is not dict
                or target_field.get("state") not in {
                    "value", "null", "empty", "missing",
                }):
            raise FieldEffectError("authority_binding")
        pre_value = (
            target_field.get("value")
            if target_field["state"] == "value"
            else None
        )
        dependency_digests: tuple[str, ...] = (decision.target_fingerprint,)
        if type(decision) is HumanDecision and decision.destination == "cover":
            consideration_row = self._connection.execute(
                "SELECT consideration.document FROM current_cover_considerations current "
                "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
                "JOIN cover_consideration_events event ON event.consideration_id=consideration.consideration_id "
                "WHERE current.review_case_id=? AND event.kind='selected' AND event.decision_id=?",
                (decision.review_case_id, decision.decision_id),
            ).fetchone()
            if consideration_row is None:
                raise FieldEffectError("authority_binding")
            consideration = json.loads(consideration_row[0])
            selected_artifact = (
                value.get("value") if type(value) is dict else None
            )
            selected_candidate = next(
                (
                    candidate
                    for candidate in consideration.get("candidates", ())
                    if type(candidate) is dict
                    and candidate.get("artifact_id") == selected_artifact
                    and candidate.get("selectable") is True
                    and candidate.get("incumbent") is False
                ),
                None,
            )
            incumbent_digest = consideration.get("incumbent_digest")
            if (
                selected_candidate is None
                or consideration.get("target_fingerprint") != decision.target_fingerprint
                or consideration.get("policy_reference")
                != self._current_cover_policy_reference
                or consideration.get("replay_reference")
                != self._current_cover_replay_reference
                or (
                    incumbent_digest is not None
                    and pre_value != "cover_" + str(incumbent_digest)
                )
            ):
                raise FieldEffectError("authority_binding")
            dependency_digests = (
                decision.target_fingerprint,
                hashlib.sha256(consideration_row[0].encode("utf-8")).hexdigest(),
            )
        return {"connection": lineage[0], "library": lineage[1], "work_id": lineage[2],
                "destination": decision.destination,
                "review_case_id": decision.review_case_id,
                "target_fingerprint": decision.target_fingerprint,
                "target_version": field_value_version(decision.destination, pre_value),
                "decision_value": value, "source": "persisted_review_case",
                "pre_value": pre_value,
                "dependency_digests": dependency_digests}

    def record_current_field_effect_no_change(
        self, decision: AutomaticDecision, occurred_at: int,
    ) -> bool:
        """Atomically persist an equal automatic candidate without write authority."""
        if type(decision) is not AutomaticDecision or type(occurred_at) is not int or occurred_at < 0:
            raise FieldEffectError("authority_binding")
        transaction = self._transaction("record_current_field_effect_no_change")
        try:
            transaction.begin()
            source = self.field_effect_authority_source(decision)
            desired = project_decision_value(decision.destination, source["decision_value"])
            if desired != source["pre_value"]:
                transaction.commit()
                return False
            record = FieldEffectNoChange.create(
                decision_id=decision.decision_id, review_case_id=decision.review_case_id,
                # field_effect_authority_source builds exactly the
                # _AuthorityFields shape: these four keys are always str.
                report_id=decision.report_id, connection=cast(str, source["connection"]),
                library=cast(str, source["library"]), work_id=cast(str, source["work_id"]),
                destination=decision.destination, target_fingerprint=decision.target_fingerprint,
                target_version=cast(str, source["target_version"]), occurred_at=occurred_at,
            )
            inserted = self._connection.execute(
                "INSERT OR IGNORE INTO field_effect_no_changes(decision_id,review_case_id,report_id,connection_name,library_name,work_id,destination,target_fingerprint,target_version,origin,occurred_at,event_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (record.decision_id, record.review_case_id, record.report_id,
                 record.connection, record.library, record.work_id, record.destination,
                 record.target_fingerprint, record.target_version, record.origin,
                 record.occurred_at, record.event_id),
            ).rowcount
            if not inserted:
                row = self._connection.execute(
                    "SELECT review_case_id,report_id,connection_name,library_name,work_id,destination,target_fingerprint,target_version,origin,occurred_at,event_id FROM field_effect_no_changes WHERE decision_id=?",
                    (record.decision_id,),
                ).fetchone()
                if row is None:
                    raise FieldEffectError("persistence")
                existing = FieldEffectNoChange.create(
                    decision_id=record.decision_id, review_case_id=row[0], report_id=row[1],
                    connection=row[2], library=row[3], work_id=row[4], destination=row[5],
                    target_fingerprint=row[6], target_version=row[7], occurred_at=row[9],
                )
                if row[8] != existing.origin or row[10] != existing.event_id or (
                    existing.decision_id, existing.review_case_id, existing.report_id,
                    existing.connection, existing.library, existing.work_id,
                    existing.destination, existing.target_fingerprint,
                    existing.target_version, existing.origin, existing.event_id,
                ) != (
                    record.decision_id, record.review_case_id, record.report_id,
                    record.connection, record.library, record.work_id,
                    record.destination, record.target_fingerprint,
                    record.target_version, record.origin, record.event_id,
                ):
                    raise FieldEffectError("persistence")
            transaction.commit()
            return True
        except (sqlite3.Error, FieldEffectError, TypeError, ValueError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("authority_binding") from error

    def field_effect_authority_is_current(self, authority: EffectAuthority) -> bool:
        current = (self._connection.execute(
            "SELECT current FROM field_effect_authorities WHERE decision_id=?",
            (authority.decision_id,),
        ).fetchone() == (1,) and self._connection.execute(
            "SELECT 1 FROM current_review_case_lineages WHERE review_case_id=?",
            (authority.review_case_id,),
        ).fetchone() == (1,) and self._review_case_accepts_authority(
            authority.review_case_id
        ))
        if not current or authority.origin != "automatic_policy":
            return current
        lineage = self._connection.execute(
            "SELECT library_name FROM current_review_case_lineages "
            "WHERE review_case_id=?", (authority.review_case_id,),
        ).fetchone()
        if lineage is None:
            return False
        if self._connection.execute(
            "SELECT 1 FROM automatic_decisions automatic "
            "JOIN field_effect_manual_holds hold "
            "ON hold.review_case_id=automatic.review_case_id "
            "AND hold.destination=automatic.destination AND hold.active=1 "
            "WHERE automatic.decision_id=? LIMIT 1", (authority.decision_id,),
        ).fetchone() == (1,):
            return False
        try:
            return self.current_resolved_policy_id(lineage[0]) == authority.policy_identity
        except MetadataReportError:
            return False

    def grant_field_effect_consents(
        self, connection: str, libraries: tuple[str, ...],
        destinations: tuple[str, ...], granted_at: int,
    ) -> tuple[EffectConsent, ...]:
        """Grant one explicit library-scoped envelope as field-local consents."""
        if (type(connection) is not str or not connection
                or type(libraries) is not tuple or not libraries
                or type(destinations) is not tuple or not destinations
                or any(type(value) is not str or not value for value in libraries)
                or len(set(libraries)) != len(libraries)
                or len(set(destinations)) != len(destinations)
                or type(granted_at) is not int or granted_at < 0):
            raise FieldEffectError("invalid_consent")
        granted: list[EffectConsent] = []
        transaction = self._transaction("grant_field_effect_consents")
        try:
            transaction.begin()
            for library in libraries:
                envelope_identity = self.field_effect_consent_envelope_identity(library)
                for destination in destinations:
                    row = self._connection.execute(
                        "SELECT document FROM field_effect_consents WHERE current=1 "
                        "AND json_extract(document,'$.consent.connection')=? "
                        "AND json_extract(document,'$.consent.library')=? "
                        "AND json_extract(document,'$.consent.destination')=? "
                        "ORDER BY updated_at DESC,consent_id LIMIT 1",
                        (connection, library, destination),
                    ).fetchone()
                    if row is not None:
                        raw = json.loads(row[0])
                        consent = EffectConsent.from_data(raw["consent"])
                        if raw.get("current") is not True:
                            raise FieldEffectError("invalid_consent")
                        if consent.envelope_identity == envelope_identity:
                            granted.append(consent)
                            continue
                        revoked = canonical_effect_json({
                            "consent": consent.to_data(), "current": False,
                            "schema_version": "field-effect-consent-state-v1",
                            "updated_at": granted_at,
                        })
                        self._connection.execute(
                            "UPDATE field_effect_consents SET current=0,updated_at=?,document=? "
                            "WHERE consent_id=? AND current=1",
                            (granted_at, revoked, consent.consent_id),
                        )
                    consent = EffectConsent.create(
                        connection=connection, library=library,
                        destination=destination,
                        envelope_identity=envelope_identity,
                        granted_at=granted_at,
                    )
                    document = canonical_effect_json({
                        "consent": consent.to_data(), "current": True,
                        "schema_version": "field-effect-consent-state-v1",
                        "updated_at": granted_at,
                    })
                    self._connection.execute(
                        "INSERT INTO field_effect_consents(consent_id,current,updated_at,document) "
                        "VALUES(?,1,?,?)",
                        (consent.consent_id, granted_at, document),
                    )
                    granted.append(consent)
            transaction.commit()
            return tuple(granted)
        except (json.JSONDecodeError, KeyError, sqlite3.Error, MetadataReportError,
                FieldEffectError, TypeError, ValueError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def grant_field_effect_consent_matrix(
        self, connection: str,
        grants: tuple[tuple[str, tuple[str, ...]], ...], granted_at: int,
    ) -> tuple[EffectConsent, ...]:
        """Atomically persist only the capability-proven library destinations."""
        if (type(grants) is not tuple or not grants
                or any(type(library) is not str or not library
                       or type(destinations) is not tuple or not destinations
                       or any(type(destination) is not str or not destination
                              for destination in destinations)
                       or len(set(destinations)) != len(destinations)
                       for library, destinations in grants)
                or len({library for library, _ in grants}) != len(grants)):
            raise FieldEffectError("invalid_consent")
        granted: list[EffectConsent] = []
        transaction = self._transaction("grant_field_effect_consent_matrix")
        try:
            transaction.begin()
            for library, destinations in grants:
                envelope_identity = self.field_effect_consent_envelope_identity(library)
                eligible_destinations = set(destinations)
                current_rows = self._connection.execute(
                    "SELECT document FROM field_effect_consents WHERE current=1 "
                    "AND json_extract(document,'$.consent.connection')=? "
                    "AND json_extract(document,'$.consent.library')=?",
                    (connection, library),
                ).fetchall()
                for (document,) in current_rows:
                    raw = json.loads(document)
                    consent = EffectConsent.from_data(raw["consent"])
                    if raw.get("current") is not True:
                        raise FieldEffectError("invalid_consent")
                    if consent.destination in eligible_destinations:
                        continue
                    self._connection.execute(
                        "UPDATE field_effect_consents SET current=0,updated_at=?,document=? "
                        "WHERE consent_id=? AND current=1",
                        (granted_at, canonical_effect_json({
                            "consent": consent.to_data(), "current": False,
                            "schema_version": "field-effect-consent-state-v1",
                            "updated_at": granted_at,
                        }), consent.consent_id),
                    )
                for destination in destinations:
                    row = self._connection.execute(
                        "SELECT document FROM field_effect_consents WHERE current=1 "
                        "AND json_extract(document,'$.consent.connection')=? "
                        "AND json_extract(document,'$.consent.library')=? "
                        "AND json_extract(document,'$.consent.destination')=? "
                        "ORDER BY updated_at DESC,consent_id LIMIT 1",
                        (connection, library, destination),
                    ).fetchone()
                    if row is not None:
                        raw = json.loads(row[0])
                        consent = EffectConsent.from_data(raw["consent"])
                        if raw.get("current") is not True:
                            raise FieldEffectError("invalid_consent")
                        if consent.envelope_identity == envelope_identity:
                            granted.append(consent)
                            continue
                        self._connection.execute(
                            "UPDATE field_effect_consents SET current=0,updated_at=?,document=? "
                            "WHERE consent_id=? AND current=1",
                            (granted_at, canonical_effect_json({
                                "consent": consent.to_data(), "current": False,
                                "schema_version": "field-effect-consent-state-v1",
                                "updated_at": granted_at,
                            }), consent.consent_id),
                        )
                    consent = EffectConsent.create(
                        connection=connection, library=library,
                        destination=destination, envelope_identity=envelope_identity,
                        granted_at=granted_at,
                    )
                    self._connection.execute(
                        "INSERT INTO field_effect_consents(consent_id,current,updated_at,document) "
                        "VALUES(?,1,?,?)",
                        (consent.consent_id, granted_at, canonical_effect_json({
                            "consent": consent.to_data(), "current": True,
                            "schema_version": "field-effect-consent-state-v1",
                            "updated_at": granted_at,
                        })),
                    )
                    granted.append(consent)
            transaction.commit()
            return tuple(granted)
        except (json.JSONDecodeError, KeyError, sqlite3.Error, MetadataReportError,
                FieldEffectError, TypeError, ValueError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def current_field_effect_consent(
        self, connection: str, library: str, destination: str,
    ) -> EffectConsent:
        try:
            row = self._connection.execute(
                "SELECT document FROM field_effect_consents WHERE current=1 "
                "AND json_extract(document,'$.consent.connection')=? "
                "AND json_extract(document,'$.consent.library')=? "
                "AND json_extract(document,'$.consent.destination')=? "
                "ORDER BY updated_at DESC,consent_id LIMIT 1",
                (connection, library, destination),
            ).fetchone()
            if row is None:
                raise FieldEffectError("consent")
            raw = json.loads(row[0])
            if raw.get("current") is not True:
                raise FieldEffectError("consent")
            consent = EffectConsent.from_data(raw["consent"])
            if consent.envelope_identity != self.field_effect_consent_envelope_identity(library):
                raise FieldEffectError("consent")
            return consent
        except FieldEffectError:
            raise
        except (json.JSONDecodeError, KeyError, sqlite3.Error,
                TypeError, ValueError) as error:
            raise FieldEffectError("persistence") from error

    def field_effect_consent_is_current(self, consent: EffectConsent) -> bool:
        persisted = self._connection.execute(
            "SELECT current FROM field_effect_consents WHERE consent_id=?",
            (consent.consent_id,),
        ).fetchone() == (1,)
        if not persisted:
            return False
        try:
            return consent.envelope_identity == self.field_effect_consent_envelope_identity(
                consent.library
            )
        except (FieldEffectError, MetadataReportError):
            return False

    def field_effect_consent_envelope_identity(self, library: str) -> str:
        """Bind consent to the exact current automatic policy semantics."""
        if type(library) is not str or not library:
            raise FieldEffectError("invalid_consent")
        return self._field_effect_consent_envelope_identity(
            library, self.current_resolved_policy_id(library)
        )

    @staticmethod
    def _field_effect_consent_envelope_identity(
        library: str, resolved_policy_id: str,
    ) -> str:
        document = canonical_effect_json({
            "action": "apply_field",
            "destinations": sorted(SUPPORTED_DESTINATIONS),
            "library": library,
            "resolved_policy_id": resolved_policy_id,
            "schema_version": "field-effect-consent-envelope-v1",
        })
        return hashlib.sha256(document.encode("utf-8")).hexdigest()

    def revoke_library_field_effect_consents(
        self, connection: str, library: str, changed_at: int,
    ) -> int:
        """Atomically stop every new claim for one configured library."""
        if (type(connection) is not str or not connection
                or type(library) is not str or not library
                or type(changed_at) is not int or changed_at < 0):
            raise FieldEffectError("invalid_consent")
        transaction = self._transaction("revoke_library_field_effect_consents")
        try:
            transaction.begin()
            rows = self._connection.execute(
                "SELECT consent_id,document FROM field_effect_consents WHERE current=1 "
                "AND json_extract(document,'$.consent.connection')=? "
                "AND json_extract(document,'$.consent.library')=?",
                (connection, library),
            ).fetchall()
            for consent_id, document in rows:
                raw = json.loads(document)
                consent = EffectConsent.from_data(raw["consent"])
                revoked = canonical_effect_json({
                    "consent": consent.to_data(), "current": False,
                    "schema_version": "field-effect-consent-state-v1",
                    "updated_at": changed_at,
                })
                self._connection.execute(
                    "UPDATE field_effect_consents SET current=0,updated_at=?,document=? "
                    "WHERE consent_id=? AND current=1",
                    (changed_at, revoked, consent_id),
                )
            transaction.commit()
            return len(rows)
        except (json.JSONDecodeError, KeyError, sqlite3.Error,
                FieldEffectError, TypeError, ValueError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def pending_field_effect_decision_documents(
        self, limit: int, after: tuple[int, str] | None = None,
    ) -> tuple[str, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FieldEffectError("invalid_limit")
        if (after is not None and (type(after) is not tuple or len(after) != 2
                or type(after[0]) is not int or after[0] < 0
                or type(after[1]) is not str or not after[1])):
            raise FieldEffectError("invalid_limit")
        try:
            cursor = ""
            parameters: tuple[object, ...] = ()
            if after is not None:
                cursor = ("AND (pending.occurred_at>? OR "
                          "(pending.occurred_at=? AND pending.decision_id>?)) ")
                parameters = (after[0], after[0], after[1])
            rows = self._connection.execute(
                "SELECT pending.document FROM ("
                "SELECT human.decision_id,human.occurred_at,human.document "
                "FROM human_decisions human WHERE human.outcome='accept' AND "
                "(EXISTS (SELECT 1 FROM current_review_case_lineages lineage "
                "WHERE lineage.review_case_id=human.review_case_id) OR ("
                "human.destination='cover' AND EXISTS (SELECT 1 FROM "
                "cover_consideration_events selected JOIN current_cover_considerations current "
                "ON current.consideration_id=selected.consideration_id "
                "WHERE selected.decision_id=human.decision_id AND selected.kind='selected' "
                "AND current.review_case_id=human.review_case_id) AND NOT EXISTS ("
                "SELECT 1 FROM cover_consideration_events stale WHERE "
                "stale.decision_id=human.decision_id AND stale.kind='stale'))) AND "
                "human.ordinal=(SELECT MAX(current.ordinal) FROM human_decisions current "
                "WHERE current.review_case_id=human.review_case_id AND "
                "current.destination=human.destination) AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds returned WHERE "
                "returned.review_case_id=human.review_case_id AND "
                "returned.destination=human.destination AND returned.active=0) UNION ALL "
                "SELECT automatic.decision_id,automatic.occurred_at,automatic.document "
                "FROM automatic_decisions automatic WHERE "
                "EXISTS (SELECT 1 FROM current_review_case_lineages lineage JOIN "
                "current_review_packages package ON package.connection_name=lineage.connection_name "
                "AND package.library_name=lineage.library_name AND package.item_id=lineage.item_id "
                "AND package.proposal_set_id=lineage.proposal_set_id AND package.report_id=lineage.report_id "
                "AND package.review_case_id=lineage.review_case_id AND package.checkpoint=lineage.checkpoint "
                "WHERE lineage.review_case_id=automatic.review_case_id) AND "
                "json_extract(automatic.document,'$.outcome')='candidate' AND NOT EXISTS ("
                "SELECT 1 FROM human_decisions human WHERE "
                "human.review_case_id=automatic.review_case_id AND "
                "human.destination=automatic.destination AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds returned WHERE "
                "returned.review_case_id=automatic.review_case_id AND "
                "returned.destination=automatic.destination AND returned.active=0)) AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds hold WHERE "
                "hold.review_case_id=automatic.review_case_id AND "
                "hold.destination=automatic.destination AND hold.active=1)"
                ") AS pending WHERE NOT EXISTS (SELECT 1 FROM field_effects effect "
                "WHERE effect.decision_id=pending.decision_id AND "
                "effect.state NOT IN ('superseded','abandoned')) AND NOT EXISTS (SELECT 1 FROM field_effect_no_changes no_change WHERE no_change.decision_id=pending.decision_id) " + cursor +
                "ORDER BY pending.occurred_at,pending.decision_id LIMIT ?",
                (*parameters, limit),
            ).fetchall()
            return tuple(row[0] for row in rows)
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def pending_field_effect_decision_counts(self) -> tuple[dict[str, object], ...]:
        """Return exact current prepared counts grouped by library and origin."""
        try:
            rows = self._connection.execute(
                "WITH pending AS ("
                "SELECT human.decision_id,human.review_case_id,'human' origin "
                "FROM human_decisions human WHERE human.outcome='accept' AND "
                "EXISTS (SELECT 1 FROM current_review_case_lineages lineage "
                "WHERE lineage.review_case_id=human.review_case_id) AND "
                "human.ordinal=(SELECT MAX(current.ordinal) FROM human_decisions current "
                "WHERE current.review_case_id=human.review_case_id AND "
                "current.destination=human.destination) AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds returned WHERE "
                "returned.review_case_id=human.review_case_id AND "
                "returned.destination=human.destination AND returned.active=0) UNION ALL "
                "SELECT automatic.decision_id,automatic.review_case_id,'automatic' origin "
                "FROM automatic_decisions automatic WHERE "
                "EXISTS (SELECT 1 FROM current_review_case_lineages lineage JOIN "
                "current_review_packages package ON package.connection_name=lineage.connection_name "
                "AND package.library_name=lineage.library_name AND package.item_id=lineage.item_id "
                "AND package.proposal_set_id=lineage.proposal_set_id AND package.report_id=lineage.report_id "
                "AND package.review_case_id=lineage.review_case_id AND package.checkpoint=lineage.checkpoint "
                "WHERE lineage.review_case_id=automatic.review_case_id) AND "
                "json_extract(automatic.document,'$.outcome')='candidate' AND NOT EXISTS ("
                "SELECT 1 FROM human_decisions human WHERE "
                "human.review_case_id=automatic.review_case_id AND "
                "human.destination=automatic.destination AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds returned WHERE "
                "returned.review_case_id=automatic.review_case_id AND "
                "returned.destination=automatic.destination AND returned.active=0)) AND NOT EXISTS ("
                "SELECT 1 FROM field_effect_manual_holds hold WHERE "
                "hold.review_case_id=automatic.review_case_id AND "
                "hold.destination=automatic.destination AND hold.active=1)) "
                "SELECT lineage.connection_name,lineage.library_name,pending.origin,count(*) "
                "FROM pending JOIN current_review_case_lineages lineage "
                "ON lineage.review_case_id=pending.review_case_id JOIN current_review_packages package "
                "ON package.connection_name=lineage.connection_name AND package.library_name=lineage.library_name "
                "AND package.item_id=lineage.item_id AND package.proposal_set_id=lineage.proposal_set_id "
                "AND package.report_id=lineage.report_id AND package.review_case_id=lineage.review_case_id "
                "AND package.checkpoint=lineage.checkpoint "
                "WHERE NOT EXISTS (SELECT 1 FROM field_effects effect "
                "WHERE effect.decision_id=pending.decision_id AND "
                "effect.state NOT IN ('superseded','abandoned')) AND NOT EXISTS (SELECT 1 FROM field_effect_no_changes no_change WHERE no_change.decision_id=pending.decision_id) "
                "GROUP BY lineage.connection_name,lineage.library_name,pending.origin "
                "ORDER BY lineage.connection_name,lineage.library_name,pending.origin"
            ).fetchall()
            return tuple({
                "connection": connection,
                "library": library,
                "origin": origin,
                "count": count,
            } for connection, library, origin, count in rows)
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

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
        document = canonical_effect_json({
            "active": True, "destination": destination,
            "decision_id": decision_id,
            "pending_return": None,
            "review_case_id": review_case_id,
            "schema_version": "field-effect-manual-hold-v2",
            "updated_at": changed_at,
        })
        self._connection.execute(
            "INSERT INTO field_effect_manual_holds(review_case_id,destination,active,updated_at,document) "
            "VALUES(?,?,1,?,?) ON CONFLICT(review_case_id,destination) DO UPDATE SET "
            "active=1,updated_at=excluded.updated_at,document=excluded.document",
            (review_case_id, destination, changed_at, document),
        )

    def field_effect_manual_hold_context(
        self, review_case_id: str, destination: str,
    ) -> tuple[str, str] | None:
        row = self._connection.execute(
            "SELECT lineage.connection_name,lineage.library_name "
            "FROM current_review_case_lineages lineage "
            "JOIN field_effect_manual_holds hold ON hold.review_case_id=lineage.review_case_id "
            "WHERE lineage.review_case_id=? AND hold.destination=? AND hold.active=1",
            (review_case_id, destination),
        ).fetchone()
        return None if row is None else (row[0], row[1])

    def field_effect_manual_hold_token(
        self, review_case_id: str, destination: str,
    ) -> str | None:
        row = self._connection.execute(
            "SELECT document FROM field_effect_manual_holds "
            "WHERE review_case_id=? AND destination=? AND active=1",
            (review_case_id, destination),
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
            token = value["decision_id"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise FieldEffectError("persistence") from error
        return token if type(token) is str else None

    def request_field_effect_manual_return(
        self, review_case_id: str, destination: str, hold_token: str,
        scan_id: str, requested_at: int, changed_at: int,
    ) -> bool:
        """Persist an accepted return request against one exact human hold."""
        if (
            any(type(value) is not str or not value for value in
                (review_case_id, destination, hold_token, scan_id))
            or type(requested_at) is not int or requested_at < 0
            or type(changed_at) is not int or changed_at < 0
        ):
            raise FieldEffectError("invalid_manual_hold")
        transaction = self._transaction("request_field_effect_manual_return")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT document FROM field_effect_manual_holds "
                "WHERE review_case_id=? AND destination=? AND active=1",
                (review_case_id, destination),
            ).fetchone()
            if row is None:
                transaction.rollback()
                return False
            active_document = row[0]
            raw = json.loads(active_document)
            if raw.get("decision_id") != hold_token:
                transaction.rollback()
                return False
            scan = self._connection.execute(
                "SELECT scan.state FROM review_preparation_scans scan "
                "JOIN current_review_case_lineages lineage "
                "ON lineage.review_case_id=? "
                "AND lineage.connection_name=scan.connection_name "
                "AND lineage.library_name=scan.library_name "
                "WHERE scan.scan_id=?",
                (review_case_id, scan_id),
            ).fetchone()
            if scan is None:
                transaction.rollback()
                return False
            raw["pending_return"] = {
                "requested_at": requested_at,
                "scan_id": scan_id,
            }
            raw["updated_at"] = changed_at
            document = canonical_effect_json(raw)
            updated = self._connection.execute(
                "UPDATE field_effect_manual_holds SET updated_at=?,document=? "
                "WHERE review_case_id=? AND destination=? AND active=1 AND document=?",
                (changed_at, document, review_case_id, destination, active_document),
            ).rowcount
            if updated != 1:
                transaction.rollback()
                return False
            if scan[0] == "completed_with_failures":
                requeued = self._connection.execute(
                    "UPDATE review_preparation_scans SET state='queued',"
                    "requested_at=?,updated_at=MAX(?,?),"
                    "catalogue_generation=NULL,catalogue_fingerprint=NULL,total=0,"
                    "qualification_epoch=NULL,refresh_count=0,rescan_requested=0 WHERE scan_id=? "
                    "AND state='completed_with_failures'",
                    (requested_at, requested_at, changed_at, scan_id),
                ).rowcount
                if requeued != 1:
                    transaction.rollback()
                    return False
            transaction.commit()
            return True
        except (json.JSONDecodeError, KeyError, sqlite3.Error, TypeError) as error:
            transaction.rollback()
            raise FieldEffectError("persistence") from error

    def pending_field_effect_manual_returns(
        self, limit: int, after: tuple[int, str, str] | None = None,
    ) -> tuple[tuple[str, str, str, str, str, str, int, int], ...]:
        if (
            type(limit) is not int or not 1 <= limit <= 100
            or (
                after is not None
                and (
                    type(after) is not tuple
                    or len(after) != 3
                    or type(after[0]) is not int
                    or after[0] < 0
                    or any(type(value) is not str or not value for value in after[1:])
                )
            )
        ):
            raise FieldEffectError("invalid_manual_hold")
        try:
            cursor_clause = ""
            parameters: tuple[object, ...] = ()
            if after is not None:
                cursor_clause = (
                    "AND (hold.updated_at>? OR "
                    "(hold.updated_at=? AND hold.review_case_id>?) OR "
                    "(hold.updated_at=? AND hold.review_case_id=? "
                    "AND hold.destination>?)) "
                )
                parameters = (
                    after[0], after[0], after[1],
                    after[0], after[1], after[2],
                )
            rows = self._connection.execute(
                "SELECT hold.review_case_id,hold.destination,lineage.connection_name,"
                "lineage.library_name,hold.document,hold.updated_at "
                "FROM field_effect_manual_holds hold "
                "JOIN current_review_case_lineages lineage "
                "ON lineage.review_case_id=hold.review_case_id "
                "WHERE hold.active=1 AND "
                "json_type(hold.document,'$.pending_return')='object' "
                + cursor_clause +
                "ORDER BY hold.updated_at,hold.review_case_id,hold.destination LIMIT ?",
                (*parameters, limit),
            ).fetchall()
            pending = []
            for (
                review_case_id, destination, connection, library, document, updated_at
            ) in rows:
                raw = json.loads(document)
                request = raw["pending_return"]
                pending.append((
                    review_case_id, destination, connection, library,
                    raw["decision_id"], request["scan_id"], request["requested_at"],
                    updated_at,
                ))
            return tuple(pending)
        except (json.JSONDecodeError, KeyError, sqlite3.Error, TypeError) as error:
            raise FieldEffectError("persistence") from error

    def _has_pending_field_effect_manual_return(self, scan_id: str) -> bool:
        return bool(self._connection.execute(
            "SELECT EXISTS(SELECT 1 FROM field_effect_manual_holds "
            "WHERE active=1 AND "
            "json_extract(document,'$.pending_return.scan_id')=?)",
            (scan_id,),
        ).fetchone()[0])

    def clear_field_effect_manual_hold(
        self, review_case_id: str, destination: str, changed_at: int,
        expected_hold_token: str | None = None,
    ) -> tuple[str, str] | None:
        """Atomically clear an active hold and return its rescan lineage."""
        context = self.field_effect_manual_hold_context(review_case_id, destination)
        if context is None:
            return None
        transaction = self._transaction("clear_field_effect_manual_hold")
        try:
            transaction.begin()
            hold = self._connection.execute(
                "SELECT document FROM field_effect_manual_holds "
                "WHERE review_case_id=? AND destination=? AND active=1",
                (review_case_id, destination),
            ).fetchone()
            if hold is None:
                transaction.rollback()
                return None
            active_document = hold[0]
            raw = json.loads(active_document)
            if expected_hold_token is not None and raw.get("decision_id") != expected_hold_token:
                transaction.rollback()
                return None
            document = canonical_effect_json({
                "active": False, "decision_id": raw["decision_id"],
                "destination": destination, "review_case_id": review_case_id,
                "pending_return": None,
                "schema_version": "field-effect-manual-hold-v2",
                "updated_at": changed_at,
            })
            updated = self._connection.execute(
                "UPDATE field_effect_manual_holds SET active=0,updated_at=?,document=? "
                "WHERE review_case_id=? AND destination=? AND active=1 AND document=?",
                (changed_at, document, review_case_id, destination, active_document),
            ).rowcount
            automatic = self._connection.execute(
                "SELECT decision_id FROM automatic_decisions "
                "WHERE review_case_id=? AND destination=?",
                (review_case_id, destination),
            ).fetchone()
            if updated == 1 and automatic is not None:
                authority = self._connection.execute(
                    "SELECT document FROM field_effect_authorities WHERE decision_id=?",
                    (automatic[0],),
                ).fetchone()
                if authority is not None:
                    authority_document = json.loads(authority[0])
                    authority_document["current"] = True
                    authority_document["updated_at"] = changed_at
                    self._connection.execute(
                        "UPDATE field_effect_authorities SET current=1,updated_at=?,document=? "
                        "WHERE decision_id=?",
                        (changed_at, canonical_effect_json(authority_document), automatic[0]),
                    )
            transaction.commit()
            return context if updated == 1 else None
        except (json.JSONDecodeError, KeyError, sqlite3.Error, TypeError) as error:
            transaction.rollback()
            raise FieldEffectError("persistence") from error

    def field_effect_dependencies_current(self, digests: tuple[str, ...]) -> bool:
        if (type(digests) is not tuple or not digests
                or any(type(value) is not str or len(value) != 64 for value in digests)):
            return False
        try:
            return all(
                self._connection.execute(
                    "SELECT 1 FROM current_review_case_lineages lineage "
                    "JOIN review_cases review ON review.review_case_id=lineage.review_case_id "
                    "WHERE json_extract(review.document,'$.target_fingerprint')=? LIMIT 1",
                    (digest,),
                ).fetchone()
                == (1,)
                or self._connection.execute(
                    "SELECT 1 FROM current_cover_considerations current "
                    "JOIN cover_considerations consideration ON consideration.consideration_id=current.consideration_id "
                    "JOIN current_review_case_lineages lineage ON lineage.review_case_id=current.review_case_id "
                    "WHERE consideration.document_digest=? "
                    "AND json_extract(consideration.document,'$.policy_reference')=? "
                    "AND json_extract(consideration.document,'$.replay_reference')=? LIMIT 1",
                    (
                        digest,
                        self._current_cover_policy_reference,
                        self._current_cover_replay_reference,
                    ),
                ).fetchone()
                == (1,)
                for digest in digests
            )
        except sqlite3.Error:
            return False

    def field_effect_backup_ready(self) -> bool:
        """Prove the exact-current durable store can form a consistent checkpoint."""
        try:
            row = self._connection.execute("PRAGMA quick_check").fetchone()
            return row == ("ok",) and not self._connection.in_transaction
        except sqlite3.Error:
            return False

    def next_field_effect_plan_id(
        self, last_library: str | None,
    ) -> tuple[str, str] | None:
        """Choose a durable ready plan while alternating when another library waits."""
        if self.field_effect_paused():
            return None
        try:
            parameters: tuple[object, ...] = ()
            preference = ""
            if last_library is not None:
                preference = "CASE WHEN library_name=? THEN 1 ELSE 0 END,"
                parameters = (last_library,)
            row = self._connection.execute(
                "SELECT plan_id,library_name FROM field_effects WHERE state IN "
                "('planned','retryable') ORDER BY " + preference
                + "updated_at,plan_id LIMIT 1",
                parameters,
            ).fetchone()
            return None if row is None else (row[0], row[1])
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def pending_field_effect_action_plan_ids(self, limit: int) -> tuple[str, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FieldEffectError("invalid_limit")
        try:
            return tuple(row[0] for row in self._connection.execute(
                "SELECT DISTINCT plan_id FROM field_effect_actions WHERE state IN "
                "('pending','claimed') ORDER BY updated_at,plan_id LIMIT ?",
                (limit,),
            ).fetchall())
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def field_effect_activity_summary(self) -> dict[str, object]:
        """Project Activity status without reading legacy event/report payloads."""
        return self.field_effect_activity_data(1, include_events=False)

    def field_effect_activity_data(
        self, limit: int = 50, *, include_events: bool = True
    ) -> dict[str, object]:
        """Project a bounded value-free status and chronological audit ledger."""
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(include_events) is not bool
        ):
            raise FieldEffectError("invalid_limit")
        try:
            consent_rows = self._connection.execute(
                "SELECT c.consent_id,c.updated_at,c.document,s.generation,"
                "r.resolved_policy_id,r.document,d.document "
                "FROM field_effect_consents c JOIN metadata_policy_state s "
                "ON s.singleton=1 JOIN metadata_policy_drafts d "
                "ON d.policy_id=s.active_policy_id "
                "LEFT JOIN metadata_policy_resolutions r "
                "ON r.generation=s.generation AND r.library_name="
                "json_extract(c.document,'$.consent.library') "
                "WHERE c.current=1 ORDER BY "
                "json_extract(c.document,'$.consent.connection'),"
                "json_extract(c.document,'$.consent.library'),"
                "json_extract(c.document,'$.consent.destination')"
            ).fetchall()
            consented_destinations: list[tuple[str, str, str]] = []
            profile: MetadataPolicyProfile | None = None
            for consent_id, updated_at, document, generation, resolved_id, resolved_document, draft_document in consent_rows:
                raw = json.loads(document)
                if (
                    type(raw) is not dict
                    or set(raw) != {"consent", "current", "schema_version", "updated_at"}
                    or raw.get("current") is not True
                    or raw.get("schema_version") != "field-effect-consent-state-v1"
                    or raw.get("updated_at") != updated_at
                    or canonical_effect_json(raw) != document
                ):
                    raise FieldEffectError("persistence")
                consent = EffectConsent.from_data(raw["consent"])
                if consent.consent_id != consent_id:
                    raise FieldEffectError("persistence")
                if resolved_id is not None:
                    resolved = ResolvedMetadataPolicy.from_data(
                        json.loads(resolved_document)
                    )
                    if (
                        resolved.library != consent.library
                        or resolved.resolved_id != resolved_id
                        or resolved.canonical_json != resolved_document
                    ):
                        raise FieldEffectError("persistence")
                    current_resolved_id = resolved.resolved_id
                elif generation == 1:
                    if profile is None:
                        profile = MetadataPolicyProfile.from_data(
                            json.loads(draft_document)
                        )
                    current_resolved_id = resolve_policy(
                        profile, consent.library
                    ).resolved_id
                else:
                    continue
                if consent.envelope_identity == self._field_effect_consent_envelope_identity(
                    consent.library, current_resolved_id
                ):
                    consented_destinations.append((
                        consent.connection, consent.library, consent.destination
                    ))
            counts = {
                state: count for state, count in self._connection.execute(
                    "SELECT state,count(*) FROM field_effects GROUP BY state"
                ).fetchall()
            }
            cleanup = self._connection.execute(
                "SELECT count(*),"
                "coalesce(sum(CASE WHEN attempt>=3 THEN 1 ELSE 0 END),0) "
                "FROM field_effect_cleanups WHERE state='pending'"
            ).fetchone()
            event_rows = [] if not include_events else self._connection.execute(
                "SELECT effect.connection_name,effect.destination,json_extract(event.document,'$.failure'),"
                "effect.library_name,event.occurred_at,"
                "json_extract(event.document,'$.origin'),lineage.report_id,event.kind,effect.work_id "
                "FROM field_effect_events event JOIN field_effects effect "
                "ON effect.plan_id=event.plan_id LEFT JOIN current_metadata_report_lineages lineage "
                "ON lineage.connection_name=effect.connection_name AND lineage.library_name=effect.library_name AND lineage.item_id=effect.work_id "
                "ORDER BY event.occurred_at DESC,event.event_id DESC LIMIT ?", (limit,),
            ).fetchall()
            report_ids = sorted({row[6] for row in event_rows if row[6] is not None})
            report_documents = {} if not report_ids else dict(self._connection.execute(
                "SELECT report_id,document FROM metadata_reports WHERE report_id IN ("
                + ",".join("?" for _ in report_ids) + ")",
                report_ids,
            ).fetchall())
            events = [{
                "connection": connection,
                "destination": destination,
                "failure": failure,
                "library": library,
                "occurred_at": occurred_at,
                "origin": origin,
                "report_id": report_id,
                "state": state,
                "work_id": work_id,
            } for connection, destination, failure, library, occurred_at, origin, report_id, state, work_id
                in event_rows]
            suspensions = [{
                "connection": connection,
                "destination": destination or None,
                "reason": reason,
                "scope": scope,
                "updated_at": updated_at,
            } for scope, connection, destination, reason, updated_at
                in self._connection.execute(
                    "SELECT scope,connection_name,destination,reason,updated_at "
                    "FROM field_effect_suspensions ORDER BY updated_at DESC,scope,"
                    "connection_name,destination LIMIT 64"
                ).fetchall()]
            return {
                "consented_libraries": [
                    {"connection": connection, "library": library}
                    for connection, library in sorted({
                        (connection, library)
                        for connection, library, _ in consented_destinations
                    })
                ],
                "consented_destinations": [
                    {"connection": connection, "library": library,
                     "destination": destination}
                    for connection, library, destination
                    in consented_destinations
                ],
                "counts": counts,
                "cleanup": {"pending": cleanup[0], "exhausted": cleanup[1]},
                "events": events,
                "report_documents": report_documents,
                "paused": self.field_effect_paused(),
                "suspensions": suspensions,
            }
        except (json.JSONDecodeError, MetadataPolicyError, sqlite3.Error,
                TypeError, ValueError) as error:
            raise FieldEffectError("persistence") from error

    def field_effect_activity_page(
        self,
        *,
        search: str | None,
        event_type: str,
        occurred_after: int,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        """Return one exact, bounded page from the durable owner activity ledgers."""
        if (
            (search is not None and (type(search) is not str or not search or len(search) > 128))
            or event_type not in {"all", "review", "policy", "metadata", "safety", "unknown"}
            or type(occurred_after) is not int
            or occurred_after < 0
            or type(page) is not int
            or not 1 <= page <= 1_000_000
            or page_size not in {25, 50, 100}
        ):
            raise FieldEffectError("invalid_activity_query")
        document_limit = 12_582_912
        scalar_limit = 512
        # The selected page and its joined report documents are validated
        # below.  Do not rescan every historical document on each poll: that
        # makes a bounded page read proportional to the whole durable corpus.
        transaction = self._transaction("field_effect_activity_page", deferred=True)
        try:
            transaction.begin()
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error
        # This is deliberately a read-only union over the existing immutable
        # ledgers.  It neither broadens write authority nor invents a second
        # audit store just for presentation.
        # ``events`` is deliberately rebound below from the union SQL text to
        # the projected event list; both shapes are declared here.
        events: str | list[dict[str, object]] = """
            SELECT event.event_id, effect.connection_name, effect.destination,
                   effect.library_name, event.occurred_at,
                   COALESCE(CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) THEN json_extract(event.document, '$.origin') END, 'unknown'),
                   lineage.report_id, event.kind, effect.work_id, event.document,
                   CASE WHEN event.kind IN ('retryable','stale','superseded','abandoned')
                             OR CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) THEN json_extract(event.document, '$.failure') END IS NOT NULL
                        THEN 'safety'
                        WHEN CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) THEN json_extract(event.document, '$.origin') END='human' THEN 'review'
                        WHEN CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) THEN json_extract(event.document, '$.origin') END='automatic_policy' THEN 'metadata'
                        ELSE 'unknown' END,
                   CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.fields[0].value') END, NULL, 'field_effect', NULL, event.plan_id, event.sequence, effect.decision_id,
                   CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) THEN 1 ELSE 0 END
              FROM field_effect_events event
              JOIN field_effects effect ON effect.plan_id=event.plan_id
         LEFT JOIN current_metadata_report_lineages lineage
                ON lineage.connection_name=effect.connection_name
               AND lineage.library_name=effect.library_name AND lineage.item_id=effect.work_id
              LEFT JOIN metadata_reports report ON report.report_id=lineage.report_id
            UNION ALL
            SELECT event.event_id, CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.connection') END, event.destination,
                   CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.library') END, event.occurred_at, 'human', review.report_id,
                   decision.outcome, CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.item') END, event.document, 'review', CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.fields[0].value') END, NULL, 'human_decision', decision.document, event.review_case_id, NULL, event.decision_id,
                   CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) THEN 1 ELSE 0 END
              FROM human_decision_events event
              JOIN human_decisions decision ON decision.decision_id=event.decision_id
              JOIN review_cases review ON review.review_case_id=event.review_case_id
              JOIN metadata_reports report ON report.report_id=review.report_id
            UNION ALL
            SELECT decision.decision_id, CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.connection') END, decision.destination,
                   CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.library') END, decision.occurred_at, 'automatic_policy', review.report_id,
                   'recorded', CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.item') END, decision.document, 'metadata', CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.fields[0].value') END, NULL, 'automatic_decision', NULL, decision.review_case_id, NULL, decision.decision_id,
                   CASE WHEN typeof(decision.document)='text' AND length(CAST(decision.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(decision.document) THEN 1 ELSE 0 END
              FROM automatic_decisions decision
              JOIN review_cases review ON review.review_case_id=decision.review_case_id
              JOIN metadata_reports report ON report.report_id=review.report_id
            UNION ALL
            SELECT no_change.event_id, no_change.connection_name, no_change.destination,
                   no_change.library_name, no_change.occurred_at, no_change.origin,
                   no_change.report_id, 'no_change_required', no_change.work_id, '{}',
                   'metadata', CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.fields[0].value') END,
                   NULL, 'field_effect_no_change', NULL, no_change.review_case_id,
                   NULL, no_change.decision_id, 1
              FROM field_effect_no_changes no_change
              JOIN metadata_reports report ON report.report_id=no_change.report_id
            UNION ALL
            SELECT event.event_id, CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.connection') END, 'cover', CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.library') END,
                   event.occurred_at, CASE event.kind WHEN 'automatic_prepared' THEN 'automatic_pipeline' WHEN 'owner_retry_prepared' THEN 'owner_retry' WHEN 'selected' THEN 'human' WHEN 'kept_incumbent' THEN 'human' WHEN 'no_action' THEN 'human' ELSE 'unknown' END, review.report_id,
                   CASE WHEN event.kind IN ('automatic_prepared','owner_retry_prepared') THEN json_extract(consideration.document, '$.reviewer_status') ELSE event.kind END, CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.item') END,
                   event.document,
                   CASE WHEN event.kind IN ('stale','superseded') THEN 'safety' WHEN event.kind='automatic_prepared' THEN 'metadata' WHEN event.kind IN ('owner_retry_prepared','selected','kept_incumbent','no_action') THEN 'review' ELSE 'unknown' END,
                   CASE WHEN typeof(report.document)='text' AND length(CAST(report.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(report.document) THEN json_extract(report.document, '$.target.fields[0].value') END, NULL, 'cover_safeguard', CASE WHEN json_extract(consideration.document, '$.reviewer_status') IN ('completed','unavailable','failed') THEN json_object('reviewer_status', json_extract(consideration.document, '$.reviewer_status')) ELSE '{}' END, event.review_case_id, NULL, event.consideration_id,
                   CASE WHEN typeof(event.document)='text' AND length(CAST(event.document AS BLOB)) BETWEEN 2 AND 2097152 AND json_valid(event.document) AND typeof(consideration.document)='text' AND length(CAST(consideration.document AS BLOB)) BETWEEN 2 AND 12582912 AND json_valid(consideration.document) THEN 1 ELSE 0 END
              FROM cover_consideration_events event
              JOIN cover_considerations consideration ON consideration.consideration_id=event.consideration_id
              JOIN review_cases review ON review.review_case_id=event.review_case_id
              JOIN metadata_reports report ON report.report_id=review.report_id
            UNION ALL
            SELECT transition.transition_id, NULL, 'policy', 'Library policy', transition.updated_at,
                   'policy', NULL, transition.state, 'Library policy', '{}', 'policy',
                   'Library policy', 'Policy generation ' || transition.generation, 'policy_transition', NULL, NULL, NULL, NULL, 1
              FROM metadata_policy_transitions transition
        """
        where = ["occurred_at>=?"]
        parameters: list[object] = [occurred_after]
        if event_type != "all":
            where.append("event_type=?")
            parameters.append(event_type)
        if search is not None:
            escaped = search.casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            where.append(
                "(ml_casefold(work_id) LIKE ? ESCAPE '\\' OR ml_casefold(library) LIKE ? ESCAPE '\\' "
                "OR ml_casefold(destination) LIKE ? ESCAPE '\\' OR ml_casefold(state) LIKE ? ESCAPE '\\' "
                "OR ml_casefold(coalesce(title,'')) LIKE ? ESCAPE '\\' OR ml_casefold(coalesce(detail,'')) LIKE ? ESCAPE '\\')"
            )
            parameters.extend((like, like, like, like, like, like))
        predicate = " WHERE " + " AND ".join(where)
        projection = "WITH activity_events(event_id,connection,destination,library,occurred_at,origin,report_id,state,work_id,document,event_type,title,detail,source_kind,source_document,source_parent_id,source_sequence,source_identity,source_valid) AS (" + cast(str, events) + ") "
        def waiting_scalar(value: object, *, nullable: bool = False) -> str | None:
            if value is None and nullable:
                return None
            if type(value) is not str or not value or len(value) > scalar_limit:
                raise FieldEffectError("persistence")
            return value
        try:
            total = int(self._connection.execute(
                projection + "SELECT count(*) FROM activity_events" + predicate, parameters
            ).fetchone()[0])
            page_count = max(1, (total + page_size - 1) // page_size)
            selected_page = min(page, page_count)
            rows = self._connection.execute(
                projection + "SELECT event_id,connection,destination,library,occurred_at,origin,"
                "report_id,state,work_id,document,event_type,title,detail,source_kind,source_document,source_parent_id,source_sequence,source_identity,source_valid FROM activity_events" + predicate
                + " ORDER BY occurred_at DESC,event_id DESC LIMIT ? OFFSET ?",
                (*parameters, page_size, (selected_page - 1) * page_size),
            ).fetchall()
            report_ids = sorted({row[6] for row in rows if row[6] is not None})
            report_rows = [] if not report_ids else self._connection.execute(
                "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document FROM metadata_reports WHERE report_id IN ("
                + ",".join("?" for _ in report_ids) + ")",
                report_ids,
            ).fetchall()
            report_documents = {}
            try:
                for report_row in report_rows:
                    if (
                        type(report_row[8]) is not str
                        or not 2 <= len(report_row[8].encode("utf-8")) <= document_limit
                    ):
                        raise MetadataReportError("persisted report is invalid")
                    report_documents[report_row[0]] = _validate_report_row(report_row)
            except MetadataReportError as error:
                raise FieldEffectError("persistence") from error
            # Every report joined to the selected page participates in the
            # response contract.  Validate it here (not through a corpus-wide
            # preflight) so malformed durable data fails closed without making
            # a bounded poll proportional to unrelated history.
            if set(report_documents) != set(report_ids):
                raise FieldEffectError("persistence")
            events = []
            decision: HumanDecision | AutomaticDecision
            for (
                event_id, connection, destination, library, occurred_at, origin,
                report_id, state, work_id, document, projected_type, title, detail, source_kind, source_document, source_parent_id, source_sequence, source_identity, source_valid,
            ) in rows:
                if (
                    source_valid != 1
                    or type(document) is not str
                    or not 2 <= len(document.encode("utf-8")) <= document_limit
                ):
                    raise FieldEffectError("persistence")
                raw = json.loads(document)
                if type(raw) is not dict:
                    raise FieldEffectError("persistence")
                def scalar(value: object, maximum: int = 512, *, nullable: bool = False) -> str | None:
                    if value is None and nullable:
                        return None
                    if type(value) is not str or not value or len(value) > maximum:
                        raise FieldEffectError("persistence")
                    return value
                event_id = scalar(event_id, 128)
                scalar(connection, nullable=True)
                destination = scalar(destination)
                library = scalar(library)
                origin = scalar(origin)
                state = scalar(state)
                work_id = scalar(work_id)
                projected_type = scalar(projected_type, 32)
                if projected_type not in {"review", "policy", "metadata", "safety", "unknown"}:
                    raise FieldEffectError("persistence")
                source_kind = scalar(source_kind)
                if source_kind not in {"field_effect", "field_effect_no_change", "human_decision", "automatic_decision", "cover_safeguard", "policy_transition"}:
                    raise FieldEffectError("persistence")
                if source_document is not None and (
                    type(source_document) is not str
                    or not 2 <= len(source_document.encode("utf-8")) <= document_limit
                ):
                    raise FieldEffectError("persistence")
                try:
                    if source_kind == "field_effect":
                        event_keys = {
                            "attempt", "decision_id", "desired_value", "destination",
                            "event_id", "failure", "kind", "occurred_at", "origin",
                            "plan_id", "pre_value", "schema_version", "sequence", "state",
                        }
                        if (
                            set(raw) != event_keys
                            or raw.get("schema_version") != "field-effect-event-v1"
                            or raw.get("event_id") != event_id
                            or raw.get("plan_id") != source_parent_id
                            or raw.get("decision_id") != source_identity
                            or raw.get("sequence") != source_sequence
                            or type(raw.get("sequence")) is not int
                            or raw["sequence"] < 1
                            or type(raw.get("attempt")) is not int
                            or not 0 <= raw["attempt"] <= 3
                            or raw.get("failure") not in FIELD_EFFECT_FAILURES
                            or raw.get("destination") != destination
                            or raw.get("kind") != state
                            or raw.get("state") != state
                            or raw.get("occurred_at") != occurred_at
                            or raw.get("origin") != origin
                            or canonical_effect_json(raw) != document
                            or event_id != "effect_event_" + hashlib.sha256(
                                canonical_effect_json({
                                    key: value for key, value in raw.items()
                                    if key != "event_id"
                                }).encode()
                            ).hexdigest()
                        ):
                            raise FieldEffectError("persistence")
                    elif source_kind == "field_effect_no_change":
                        if raw or document != "{}" or source_document is not None:
                            raise FieldEffectError("persistence")
                        record = FieldEffectNoChange.create(
                            decision_id=source_identity, review_case_id=source_parent_id,
                            # scalar() without nullable=True raises rather than
                            # returning None, so these are always str here.
                            report_id=report_id, connection=connection, library=cast(str, library),
                            work_id=cast(str, work_id), destination=cast(str, destination),
                            target_fingerprint=self._connection.execute(
                                "SELECT target_fingerprint FROM field_effect_no_changes WHERE decision_id=?",
                                (source_identity,),
                            ).fetchone()[0],
                            target_version=self._connection.execute(
                                "SELECT target_version FROM field_effect_no_changes WHERE decision_id=?",
                                (source_identity,),
                            ).fetchone()[0],
                            occurred_at=occurred_at,
                        )
                        if (
                            state != "no_change_required" or origin != record.origin
                            or event_id != record.event_id
                        ):
                            raise FieldEffectError("persistence")
                    elif source_kind == "human_decision":
                        if source_document is None:
                            raise FieldEffectError("persistence")
                        decision = HumanDecision.from_json(source_document)
                        event_keys = {
                            "actor", "decision_id", "destination", "event_id", "kind",
                            "occurred_at", "outcome", "review_case_id", "schema_version",
                        }
                        if (
                            set(raw) != event_keys
                            or raw.get("schema_version") != "human-decision-event-v1"
                            or raw.get("kind") != "human_decision_recorded"
                            or raw.get("event_id") != event_id
                            or raw.get("decision_id") != decision.decision_id
                            or raw.get("decision_id") != source_identity
                            or raw.get("destination") != destination
                            or raw.get("actor") != decision.actor
                            or raw.get("outcome") != state
                            or raw.get("outcome") != decision.outcome
                            or raw.get("occurred_at") != occurred_at
                            or raw.get("occurred_at") != decision.occurred_at
                            or raw.get("review_case_id") != decision.review_case_id
                            or raw.get("review_case_id") != source_parent_id
                            or decision.destination != destination
                            or decision.to_json() != source_document
                            or _canonical_json(raw) != document
                            or event_id != "human_decision_event_" + hashlib.sha256(
                                ("human-decision-event-v1:" + decision.decision_id).encode("utf-8")
                            ).hexdigest()
                        ):
                            raise FieldEffectError("persistence")
                    elif source_kind == "automatic_decision":
                        decision = AutomaticDecision.from_json(document)
                        if (
                            decision.decision_id != event_id
                            or decision.decision_id != source_identity
                            or decision.destination != destination
                            or decision.occurred_at != occurred_at
                            or origin != "automatic_policy"
                            or decision.review_case_id != source_parent_id
                            or decision.report_id != report_id
                            or decision.to_json() != document
                        ):
                            raise FieldEffectError("persistence")
                    elif source_kind == "cover_safeguard":
                        if source_document is None:
                            raise FieldEffectError("persistence")
                        consideration = json.loads(source_document)
                        if type(consideration) is not dict:
                            raise FieldEffectError("persistence")
                        reviewer_status = consideration.get("reviewer_status")
                        event_keys = {
                            "consideration_id", "decision_id", "kind", "occurred_at",
                            "review_case_id", "schema_version",
                        }
                        if (
                            set(raw) != event_keys
                            or raw.get("schema_version") != "cover-consideration-event-v1"
                            or raw.get("consideration_id") != source_identity
                            or raw.get("kind") not in {
                                "automatic_prepared", "owner_retry_prepared", "superseded", "selected",
                                "kept_incumbent", "no_action", "stale",
                            }
                            or (
                                raw.get("kind") in {"automatic_prepared", "owner_retry_prepared"}
                                and reviewer_status not in {"completed", "unavailable", "failed"}
                            )
                            or state != (
                                reviewer_status if raw.get("kind") in {"automatic_prepared", "owner_retry_prepared"}
                                else raw.get("kind")
                            )
                            or (raw.get("kind") == "automatic_prepared" and (origin, projected_type) != ("automatic_pipeline", "metadata"))
                            or (raw.get("kind") == "owner_retry_prepared" and (origin, projected_type) != ("owner_retry", "review"))
                            or (raw.get("kind") in {"selected", "kept_incumbent", "no_action"} and (origin, projected_type) != ("human", "review"))
                            or (raw.get("kind") in {"stale", "superseded"} and (origin, projected_type) != ("unknown", "safety"))
                            or raw.get("occurred_at") != occurred_at
                            or raw.get("review_case_id") != source_parent_id
                            or _canonical_json(raw) != document
                            or event_id != "cover_consideration_event_" + hashlib.sha256(
                                document.encode("utf-8")
                            ).hexdigest()
                        ):
                            raise FieldEffectError("persistence")
                    elif raw or document != "{}" or source_document is not None:
                        raise FieldEffectError("persistence")
                except (AutomaticDecisionError, HumanDecisionError, TypeError, ValueError) as error:
                    raise FieldEffectError("persistence") from error
                event_title = scalar(title, nullable=True)
                failure = scalar(raw.get("failure"), nullable=True)
                if report_id is not None:
                    scalar(report_id)
                source_label = {
                    "field_effect": "Recorded field effect",
                    "field_effect_no_change": "Recorded no-change decision",
                    "human_decision": "Recorded human decision",
                    "automatic_decision": "Recorded automatic decision",
                    "cover_safeguard": "Recorded cover safeguard",
                    "policy_transition": "Recorded policy transition",
                }[source_kind]
                evidence = [{"label": source_label, "value": state.replace("_", " ")}]
                lifecycle = scalar(raw.get("state"), nullable=True)
                if source_kind == "field_effect" and lifecycle is not None:
                    evidence.append({"label": "Recorded lifecycle", "value": lifecycle.replace("_", " ")})
                if failure is not None:
                    evidence.append({"label": "Recorded failure", "value": failure.replace("_", " ")})
                if source_kind == "policy_transition":
                    transition = scalar(detail)
                    evidence.append({"label": "Transition generation", "value": transition})
                if type(occurred_at) is not int or not 0 <= occurred_at <= 253_402_300_799:
                    raise FieldEffectError("persistence")
                events.append({
                    "connection": connection,
                    "destination": destination,
                    "event_id": event_id,
                    "event_type": projected_type,
                    "failure": failure,
                    "library": library,
                    "occurred_at": occurred_at,
                    "origin": origin,
                    "report_id": report_id,
                    "state": state,
                    "work_id": work_id,
                    **({"work_label": event_title} if event_title else {}),
                    "evidence": evidence,
                })
            waiting = self._connection.execute(
                "SELECT effect.connection_name,effect.destination,effect.library_name,effect.work_id,effect.state,"
                "lineage.report_id FROM field_effects effect "
                "LEFT JOIN current_metadata_report_lineages lineage ON lineage.connection_name=effect.connection_name "
                "AND lineage.library_name=effect.library_name AND lineage.item_id=effect.work_id "
                "WHERE effect.state IN ('planned','claimed','applying','reconciling','retryable') "
                "ORDER BY effect.updated_at DESC,effect.plan_id DESC LIMIT 1"
            ).fetchone()
            waiting_title = "Recorded work"
            if waiting is not None:
                for value in waiting[:5]:
                    waiting_scalar(value)
                waiting_report_id = waiting_scalar(waiting[5], nullable=True)
                if waiting_report_id is not None:
                    waiting_report_row = self._connection.execute(
                        "SELECT report_id,connection_name,library_name,item_id,file_identity,content_digest,schema_version,document_digest,document "
                        "FROM metadata_reports WHERE report_id=?",
                        (waiting_report_id,),
                    ).fetchone()
                    if (
                        waiting_report_row is None
                        or type(waiting_report_row[8]) is not str
                        or not 2 <= len(waiting_report_row[8].encode("utf-8")) <= document_limit
                    ):
                        raise FieldEffectError("persistence")
                    try:
                        waiting_report_document = _validate_report_row(waiting_report_row)
                        waiting_report = MetadataReport.from_json(waiting_report_document)
                    except (MetadataReportError, ReportValueError) as error:
                        raise FieldEffectError("persistence") from error
                    if (
                        waiting_report.target.connection != waiting[0]
                        or waiting_report.target.library != waiting[2]
                        or waiting_report.target.item != waiting[3]
                    ):
                        raise FieldEffectError("persistence")
                    waiting_title = waiting_report.target.current_title or "Untitled"
            result = {
                "events": events,
                "page": selected_page,
                "page_count": page_count,
                "page_size": page_size,
                "report_documents": report_documents,
                "current_waiting": None if waiting is None else {
                    "destination": waiting[1], "library": waiting[2],
                    "work_id": waiting[3], "state": waiting[4],
                    "work_label": waiting_title,
                },
                "total": total,
            }
            transaction.commit()
            return result
        except (json.JSONDecodeError, RecursionError, sqlite3.Error, TypeError, ValueError) as error:
            transaction.rollback()
            raise FieldEffectError("persistence") from error
        except FieldEffectError:
            transaction.rollback()
            raise

    def record_field_effect(self, operation: FieldEffectOperation) -> FieldEffectOperation:
        """Persist a deterministic plan and its first audit fact atomically."""
        transaction = self._transaction("record_field_effect")
        try:
            if (operation.state != "planned" or operation.attempt != 0
                    or operation.failure is not None
                    or operation.claim_token is not None
                    or operation.updated_at != operation.plan.created_at):
                raise FieldEffectError("invalid_initial_operation")
            document = canonical_effect_json(operation.to_data())
            transaction.begin()
            existing = self._connection.execute(
                "SELECT document FROM field_effects WHERE plan_id=?",
                (operation.plan.plan_id,),
            ).fetchone()
            if existing is not None:
                persisted = FieldEffectOperation.from_json(existing[0])
                persisted_identity = persisted.plan.to_data()
                attempted_identity = operation.plan.to_data()
                persisted_identity.pop("created_at")
                attempted_identity.pop("created_at")
                if persisted_identity != attempted_identity:
                    raise FieldEffectError("idempotency_conflict")
                if (persisted.state in {"superseded", "abandoned"}
                        and operation.plan.authority.origin == "automatic_policy"
                        and self._connection.execute(
                            "SELECT current FROM field_effect_authorities WHERE decision_id=?",
                            (operation.plan.authority.decision_id,),
                        ).fetchone() == (1,)):
                    self._connection.execute(
                        "UPDATE field_effects SET state='planned',attempt=0,updated_at=?,document=? "
                        "WHERE plan_id=? AND state IN ('superseded','abandoned')",
                        (operation.updated_at, document, operation.plan.plan_id),
                    )
                    self._record_field_effect_event(
                        operation, "planned", operation.updated_at
                    )
                    transaction.commit()
                    return operation
                transaction.commit()
                return persisted
            authority_document = canonical_effect_json({
                "authority": operation.plan.to_data()["authority"], "current": True,
                "schema_version": "field-effect-authority-state-v1",
                "updated_at": operation.updated_at,
            })
            authority_existing = self._connection.execute(
                "SELECT document FROM field_effect_authorities WHERE decision_id=?",
                (operation.plan.authority.decision_id,),
            ).fetchone()
            if authority_existing is None:
                self._connection.execute(
                    "INSERT INTO field_effect_authorities(decision_id,current,updated_at,document) VALUES(?,1,?,?)",
                    (operation.plan.authority.decision_id, operation.updated_at, authority_document),
                )
            else:
                persisted_authority = json.loads(authority_existing[0])
                if (persisted_authority.get("authority")
                        != operation.plan.to_data()["authority"]):
                    raise FieldEffectError("authority_conflict")
                if persisted_authority.get("current") is not True:
                    self._connection.execute(
                        "UPDATE field_effect_authorities SET current=1,updated_at=?,document=? "
                        "WHERE decision_id=?",
                        (operation.updated_at, authority_document,
                         operation.plan.authority.decision_id),
                    )
            consent_document = canonical_effect_json({
                "consent": operation.plan.consent.to_data(), "current": True,
                "schema_version": "field-effect-consent-state-v1",
                "updated_at": operation.plan.consent.granted_at,
            })
            consent_existing = self._connection.execute(
                "SELECT document FROM field_effect_consents WHERE consent_id=?",
                (operation.plan.consent.consent_id,),
            ).fetchone()
            if consent_existing is None:
                self._connection.execute(
                    "INSERT INTO field_effect_consents(consent_id,current,updated_at,document) VALUES(?,1,?,?)",
                    (operation.plan.consent.consent_id,
                     operation.plan.consent.granted_at, consent_document),
                )
            elif consent_existing[0] != consent_document:
                raise FieldEffectError("consent_conflict")
            self._connection.execute(
                "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,work_id,destination,state,attempt,updated_at,document) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (operation.plan.plan_id, operation.plan.authority.decision_id,
                 operation.plan.connection, operation.plan.library, operation.plan.work_id,
                 operation.plan.destination, operation.state, operation.attempt,
                 operation.updated_at, document),
            )
            self._record_field_effect_event(operation, "planned", operation.updated_at)
            transaction.commit()
            return operation
        except (sqlite3.Error, FieldEffectError, TypeError, ValueError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def get_field_effect(self, plan_id: str) -> FieldEffectOperation:
        try:
            row = self._connection.execute(
                "SELECT document FROM field_effects WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None:
                raise FieldEffectError("not_found")
            return FieldEffectOperation.from_json(row[0])
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def field_effect_event_documents(self, plan_id: str) -> tuple[str, ...]:
        """Return one bounded, canonical, gap-free persisted audit sequence."""
        try:
            operation = self.get_field_effect(plan_id)
            rows = self._connection.execute(
                "SELECT event_id,sequence,kind,occurred_at,document "
                "FROM field_effect_events WHERE plan_id=? ORDER BY sequence",
                (plan_id,),
            ).fetchall()
            if not rows or len(rows) > 100:
                raise FieldEffectError("invalid_event")
            documents: list[str] = []
            event_keys = {"attempt", "decision_id", "desired_value", "destination",
                          "event_id", "failure", "kind", "occurred_at", "origin",
                          "plan_id", "pre_value", "schema_version", "sequence", "state"}
            for expected_sequence, (event_id, sequence, kind, occurred_at, document) in enumerate(rows, 1):
                raw = json.loads(document)
                if (type(raw) is not dict or set(raw) != event_keys
                        or raw["schema_version"] != "field-effect-event-v1"
                        or type(raw["sequence"]) is not int or raw["sequence"] < 1
                        or type(raw["attempt"]) is not int or not 0 <= raw["attempt"] <= 3
                        or type(raw["occurred_at"]) is not int or raw["occurred_at"] < 0
                        or raw["failure"] not in FIELD_EFFECT_FAILURES
                        or raw.get("plan_id") != plan_id
                        or raw.get("event_id") != event_id
                        or raw.get("sequence") != sequence
                        or raw.get("kind") != kind
                        or raw.get("occurred_at") != occurred_at
                        or sequence != expected_sequence
                        or raw.get("state") != kind
                        or raw["decision_id"] != operation.plan.authority.decision_id
                        or raw["origin"] != operation.plan.authority.origin
                        or raw["destination"] != operation.plan.destination
                        or raw["pre_value"] != operation.plan.pre_value
                        or raw["desired_value"] != operation.plan.desired_value
                        or canonical_effect_json(raw) != document
                        or event_id != "effect_event_" + hashlib.sha256(
                            canonical_effect_json({key: value for key, value in raw.items()
                                                   if key != "event_id"}).encode()
                        ).hexdigest()):
                    raise FieldEffectError("invalid_event")
                documents.append(document)
            last = json.loads(documents[-1])
            if ((last["state"], last["attempt"], last["failure"])
                    != (operation.state, operation.attempt, operation.failure)):
                raise FieldEffectError("invalid_event")
            return tuple(documents)
        except (sqlite3.Error, json.JSONDecodeError) as error:
            raise FieldEffectError("persistence") from error

    def suspend_field_effect_capability(self, *, scope: str, connection: str,
                                        destination: str, reason: str,
                                        changed_at: int) -> None:
        reasons = {"connection_unavailable", "insufficient_permission",
                   "field_unavailable", "unexpected_effect_behavior"}
        if (scope not in {"connection", "destination"}
                or type(connection) is not str or not 1 <= len(connection) <= 128
                or type(destination) is not str or not 1 <= len(destination) <= 64
                or reason not in reasons or type(changed_at) is not int
                or changed_at < 0):
            raise FieldEffectError("invalid_suspension")
        stored_destination = "" if scope == "connection" else destination
        document = canonical_effect_json({
            "connection": connection,
            "destination": stored_destination,
            "reason": reason,
            "schema_version": "field-effect-suspension-v1",
            "scope": scope,
            "updated_at": changed_at,
        })
        transaction = self._transaction("suspend_field_effect_capability")
        try:
            transaction.begin()
            existing = self._connection.execute(
                "SELECT updated_at FROM field_effect_suspensions "
                "WHERE scope=? AND connection_name=? AND destination=?",
                (scope, connection, stored_destination),
            ).fetchone()
            if existing is not None and existing[0] > changed_at:
                raise FieldEffectError("invalid_suspension")
            self._connection.execute(
                "INSERT INTO field_effect_suspensions(scope,connection_name,destination,reason,updated_at,document) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(scope,connection_name,destination) DO UPDATE SET "
                "reason=excluded.reason,updated_at=excluded.updated_at,document=excluded.document "
                "WHERE field_effect_suspensions.reason!='unexpected_effect_behavior' "
                "OR excluded.reason='unexpected_effect_behavior'",
                (scope, connection, stored_destination, reason, changed_at, document),
            )
            transaction.commit()
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def field_effect_capability_suspension(self, connection: str,
                                           destination: str) -> str | None:
        try:
            row = self._connection.execute(
                "SELECT reason FROM field_effect_suspensions WHERE "
                "connection_name=? AND ((scope='connection' AND destination='') "
                "OR (scope='destination' AND destination=?)) "
                "ORDER BY CASE scope WHEN 'connection' THEN 0 ELSE 1 END LIMIT 1",
                (connection, destination),
            ).fetchone()
            return None if row is None else str(row[0])
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def clear_field_effect_capability_suspension(
        self, *, scope: str, connection: str, destination: str, reason: str,
        observed_at: int,
    ) -> None:
        reasons = {"connection_unavailable", "insufficient_permission",
                   "field_unavailable", "unexpected_effect_behavior"}
        if (scope not in {"connection", "destination"}
                or type(connection) is not str or not connection
                or (scope == "connection" and destination != "")
                or (scope == "destination" and (type(destination) is not str
                                                 or not destination))
                or reason not in reasons
                or type(observed_at) is not int or observed_at < 0):
            raise FieldEffectError("invalid_suspension")
        transaction = self._transaction("clear_field_effect_capability_suspension")
        try:
            transaction.begin()
            self._connection.execute(
                "DELETE FROM field_effect_suspensions "
                "WHERE scope=? AND connection_name=? AND destination=? "
                "AND reason=? AND updated_at<?",
                (scope, connection, destination, reason, observed_at),
            )
            transaction.commit()
        except sqlite3.Error as error:
            transaction.rollback()
            raise FieldEffectError("persistence") from error

    def claim_field_effect(self, plan_id: str, expected_state: str,
                           claim_token: str, claimed_at: int) -> FieldEffectOperation:
        transaction = self._transaction("claim_field_effect")
        try:
            transaction.begin()
            if self._connection.execute(
                "SELECT paused FROM field_effect_control WHERE singleton=1"
            ).fetchone() != (0,):
                raise FieldEffectError("paused")
            row = self._connection.execute(
                "SELECT document,state FROM field_effects WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None or row[1] != expected_state:
                raise FieldEffectError("claim_conflict")
            persisted = FieldEffectOperation.from_json(row[0])
            if self.field_effect_capability_suspension(
                    persisted.plan.connection, persisted.plan.destination) is not None:
                raise FieldEffectError("capability")
            if self._connection.execute(
                "SELECT current FROM field_effect_authorities WHERE decision_id=?",
                (persisted.plan.authority.decision_id,),
            ).fetchone() != (1,):
                raise FieldEffectError("authority")
            if self._connection.execute(
                "SELECT current FROM field_effect_consents WHERE consent_id=?",
                (persisted.plan.consent.consent_id,),
            ).fetchone() != (1,):
                raise FieldEffectError("consent")
            if (
                persisted.plan.consent.envelope_identity
                != self.field_effect_consent_envelope_identity(
                    persisted.plan.library
                )
            ):
                raise FieldEffectError("consent")
            authority = persisted.plan.authority
            if authority.source == "persisted_review_case":
                try:
                    case_envelope = json.loads(
                        self.get_review_case(authority.review_case_id)
                    )
                    case = case_envelope["case"]
                    target = case["target"]
                except (MetadataReportError, KeyError, TypeError,
                        json.JSONDecodeError) as error:
                    raise FieldEffectError("authority") from error
                if (case_envelope.get("current") is not True
                        or not self._review_case_accepts_authority(
                            authority.review_case_id
                        )
                        or case.get("target_fingerprint")
                        != authority.target_fingerprint
                        or (target.get("connection"), target.get("library"),
                            target.get("item"))
                        != (persisted.plan.connection, persisted.plan.library,
                            persisted.plan.work_id)):
                    raise FieldEffectError("authority")
                table = ("human_decisions" if authority.origin == "human"
                         else "automatic_decisions")
                decision = self._connection.execute(
                    f"SELECT document FROM {table} WHERE decision_id=? "
                    "AND review_case_id=? AND destination=?",
                    (authority.decision_id, authority.review_case_id,
                     persisted.plan.destination),
                ).fetchone()
                if (decision is None
                        or json.loads(decision[0]).get("target_fingerprint")
                        != authority.target_fingerprint):
                    raise FieldEffectError("authority")
                if authority.origin == "automatic_policy":
                    automatic = json.loads(decision[0])
                    safety = self._connection.execute(
                        "SELECT paused,global_suspension,category_suspensions_document "
                        "FROM automatic_safety_state WHERE singleton=1"
                    ).fetchone()
                    suspended = {} if safety is None else dict(json.loads(safety[2]))
                    if (safety is None or safety[0] != 0 or safety[1] is not None
                            or automatic["policy_category"] in suspended):
                        raise FieldEffectError("authority")
            operation = persisted.transition(
                "claimed", at=claimed_at, claim_token=claim_token
            )
            self._update_field_effect(operation, expected_state)
            self._record_field_effect_event(operation, "claimed", claimed_at)
            transaction.commit()
            return operation
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def transition_field_effect(self, plan_id: str, expected_state: str,
                                state: str, changed_at: int,
                                failure: str | None = None) -> FieldEffectOperation:
        if state == "reconciling":
            return self.schedule_field_effect_reconciliation(
                plan_id, expected_state, changed_at,
                "ambiguous" if failure is None else failure,
            )
        transaction = self._transaction("transition_field_effect")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT document,state FROM field_effects WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None or row[1] != expected_state:
                raise FieldEffectError("transition_conflict")
            operation = FieldEffectOperation.from_json(row[0]).transition(
                state, at=changed_at, failure=failure
            )
            self._update_field_effect(operation, expected_state)
            self._record_field_effect_event(operation, state, changed_at)
            self._cover_considerations._retire_stale_cover_consideration(
                operation, state, changed_at
            )
            self._schedule_field_effect_cleanup(operation, changed_at)
            transaction.commit()
            return operation
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

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
        row = self._connection.execute(
            "SELECT document,current FROM field_effect_authorities WHERE decision_id=?",
            (decision_id,),
        ).fetchone()
        if row is None:
            if required:
                raise FieldEffectError("authority")
            return 0
        if row[1] == 1:
            raw = json.loads(row[0])
            raw["current"] = False
            raw["updated_at"] = changed_at
            self._connection.execute(
                "UPDATE field_effect_authorities SET current=0,updated_at=?,document=? WHERE decision_id=?",
                (changed_at, canonical_effect_json(raw), decision_id),
            )
        rows = self._connection.execute(
            "SELECT document,state FROM field_effects WHERE decision_id=? AND state IN ('planned','retryable')",
            (decision_id,),
        ).fetchall()
        for document, state in rows:
            operation = FieldEffectOperation.from_json(document).transition(
                "superseded", at=changed_at, failure="authority_superseded"
            )
            self._update_field_effect(operation, state)
            self._record_field_effect_event(operation, "superseded", changed_at)
            self._schedule_field_effect_cleanup(operation, changed_at)
        return len(rows)

    def revoke_field_effect_consent(self, consent_id: str, changed_at: int) -> int:
        transaction = self._transaction("revoke_field_effect_consent")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT document FROM field_effect_consents WHERE consent_id=?",
                (consent_id,),
            ).fetchone()
            if row is None:
                raise FieldEffectError("consent")
            raw = json.loads(row[0])
            raw["current"] = False
            raw["updated_at"] = changed_at
            self._connection.execute(
                "UPDATE field_effect_consents SET current=0,updated_at=?,document=? WHERE consent_id=?",
                (changed_at, canonical_effect_json(raw), consent_id),
            )
            rows = self._connection.execute(
                "SELECT document,state FROM field_effects WHERE json_extract(document,'$.plan.consent.consent_id')=? AND state IN ('planned','retryable')",
                (consent_id,),
            ).fetchall()
            for document, state in rows:
                operation = FieldEffectOperation.from_json(document).transition(
                    "superseded", at=changed_at, failure="consent_revoked"
                )
                self._update_field_effect(operation, state)
                self._record_field_effect_event(operation, "superseded", changed_at)
                self._schedule_field_effect_cleanup(operation, changed_at)
            transaction.commit()
            return len(rows)
        except (sqlite3.Error, FieldEffectError, json.JSONDecodeError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def schedule_field_effect_reconciliation(self, plan_id: str,
                                             expected_state: str,
                                             changed_at: int,
                                             failure: str = "ambiguous") -> FieldEffectOperation:
        transaction = self._transaction("schedule_field_effect_reconciliation")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT document,state FROM field_effects WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None or row[1] != expected_state:
                raise FieldEffectError("transition_conflict")
            operation = FieldEffectOperation.from_json(row[0]).transition(
                "reconciling", at=changed_at, failure=failure
            )
            self._update_field_effect(operation, expected_state)
            self._record_field_effect_event(operation, "reconciling", changed_at)
            self._upsert_field_effect_action(operation, "targeted_read", "pending", 0,
                                             changed_at)
            transaction.commit()
            return operation
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def complete_field_effect_reconciliation(self, plan_id: str, state: str,
                                             failure: str | None, changed_at: int,
                                             reevaluate: bool = False) -> FieldEffectOperation:
        transaction = self._transaction("complete_field_effect_reconciliation")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT document,state FROM field_effects WHERE plan_id=?", (plan_id,)
            ).fetchone()
            if row is None or row[1] != "reconciling":
                raise FieldEffectError("transition_conflict")
            operation = FieldEffectOperation.from_json(row[0]).transition(
                state, at=changed_at, failure=failure
            )
            self._update_field_effect(operation, "reconciling")
            self._record_field_effect_event(operation, state, changed_at)
            self._cover_considerations._retire_stale_cover_consideration(
                operation, state, changed_at
            )
            action = self._connection.execute(
                "SELECT attempt FROM field_effect_actions WHERE plan_id=? "
                "AND kind='targeted_read' AND state='claimed'",
                (plan_id,),
            ).fetchone()
            if action is None:
                raise FieldEffectError("action")
            self._upsert_field_effect_action(operation, "targeted_read", "completed",
                                             action[0], changed_at)
            if reevaluate:
                self._upsert_field_effect_action(operation, "forward_reevaluation",
                                                 "pending", 0, changed_at)
            self._schedule_field_effect_cleanup(operation, changed_at)
            transaction.commit()
            return operation
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def retire_stale_cover_decision(
        self, decision_id: str, changed_at: int
    ) -> bool:
        return self._cover_considerations.retire_stale_cover_decision(
            decision_id, changed_at
        )

    def claim_next_field_effect_action(self, changed_at: int,
                                       plan_id: str | None = None) -> dict[str, object] | None:
        transaction = self._transaction("claim_next_field_effect_action")
        try:
            transaction.begin()
            parameters: tuple[object, ...] = () if plan_id is None else (plan_id,)
            where = "state IN ('pending','claimed')"
            if plan_id is not None:
                where += " AND plan_id=?"
            row = self._connection.execute(
                "SELECT action_id,plan_id,kind,state,attempt FROM field_effect_actions "
                f"WHERE {where} ORDER BY CASE kind WHEN 'targeted_read' THEN 0 ELSE 1 END,updated_at,action_id LIMIT 1",
                parameters,
            ).fetchone()
            if row is None:
                transaction.commit()
                return None
            operation = self.get_field_effect(row[1])
            attempt = min(row[4] + (1 if row[3] == "pending" else 0), 3)
            self._upsert_field_effect_action(
                operation, row[2], "claimed", attempt, changed_at
            )
            transaction.commit()
            return {"action_id": row[0], "plan_id": row[1],
                    "kind": row[2], "attempt": attempt}
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def complete_forward_reevaluation(self, action_id: str, changed_at: int) -> None:
        transaction = self._transaction("complete_forward_reevaluation")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT plan_id,attempt FROM field_effect_actions "
                "WHERE action_id=? AND kind='forward_reevaluation' AND state='claimed'",
                (action_id,),
            ).fetchone()
            if row is None:
                raise FieldEffectError("action")
            operation = self.get_field_effect(row[0])
            self._upsert_field_effect_action(
                operation, "forward_reevaluation", "completed", row[1], changed_at
            )
            transaction.commit()
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def field_effect_cleanup_pending(self, plan_id: str) -> bool:
        row: tuple[object, ...] | None = self._connection.execute(
            "SELECT state FROM field_effect_cleanups WHERE plan_id=?", (plan_id,)
        ).fetchone()
        return row == ("pending",)

    def pending_field_effect_cleanups(self, limit: int) -> tuple[str, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FieldEffectError("invalid_limit")
        return tuple(row[0] for row in self._connection.execute(
            "SELECT plan_id FROM field_effect_cleanups WHERE state='pending' AND attempt<3 "
            "ORDER BY updated_at,plan_id LIMIT ?", (limit,),
        ).fetchall())

    def pending_field_effect_plan_ids(self, limit: int) -> tuple[str, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise FieldEffectError("invalid_limit")
        return tuple(row[0] for row in self._connection.execute(
            "SELECT plan_id FROM field_effects "
            "WHERE state IN ('claimed','applying','reconciling') "
            "ORDER BY updated_at,plan_id LIMIT ?", (limit,),
        ).fetchall())

    def complete_field_effect_cleanup(self, plan_id: str, changed_at: int) -> None:
        transaction = self._transaction("complete_field_effect_cleanup")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT attempt FROM field_effect_cleanups WHERE plan_id=? AND state='pending'",
                (plan_id,),
            ).fetchone()
            if row is None:
                transaction.commit()
                return
            self._upsert_field_effect_cleanup(
                plan_id, "completed", min(row[0] + 1, 3), changed_at
            )
            transaction.commit()
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            raise FieldEffectError("persistence") from error

    def fail_field_effect_cleanup(self, plan_id: str, changed_at: int) -> None:
        transaction = self._transaction("fail_field_effect_cleanup")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT attempt FROM field_effect_cleanups WHERE plan_id=? AND state='pending'",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise FieldEffectError("cleanup")
            self._upsert_field_effect_cleanup(
                plan_id, "pending", min(row[0] + 1, 3), changed_at
            )
            transaction.commit()
        except (sqlite3.Error, FieldEffectError) as error:
            transaction.rollback()
            if isinstance(error, FieldEffectError):
                raise
            raise FieldEffectError("persistence") from error

    def set_field_effect_paused(self, paused: bool, changed_at: int) -> None:
        if type(paused) is not bool or type(changed_at) is not int or changed_at < 0:
            raise FieldEffectError("invalid_control")
        transaction = self._transaction("set_field_effect_paused")
        try:
            transaction.begin()
            self._connection.execute(
                "UPDATE field_effect_control SET paused=?,updated_at=? WHERE singleton=1",
                (int(paused), changed_at),
            )
            transaction.commit()
        except sqlite3.Error as error:
            transaction.rollback()
            raise FieldEffectError("persistence") from error

    def field_effect_paused(self) -> bool:
        try:
            return cast(
                "tuple[object, ...] | None",
                self._connection.execute(
                    "SELECT paused FROM field_effect_control WHERE singleton=1"
                ).fetchone(),
            ) == (1,)
        except sqlite3.Error as error:
            raise FieldEffectError("persistence") from error

    def operational_snapshot(self) -> dict[str, object]:
        """Return only bounded codes and scopes; never persisted effect values."""
        try:
            signals: list[dict[str, object]] = []
            safety = self._connection.execute(
                "SELECT paused,global_suspension,category_suspensions_document "
                "FROM automatic_safety_state WHERE singleton=1"
            ).fetchone()
            if safety is None:
                raise FieldEffectError("persistence")
            if safety[0]:
                signals.append(_operational_signal(
                    "field_suspended", "owner_pause", "runtime"
                ))
            if safety[1] is not None:
                signals.append(_operational_signal(
                    "field_suspended", "automatic_suspension", "runtime"
                ))
            categories = json.loads(safety[2])
            if type(categories) is not list:
                raise FieldEffectError("persistence")
            for entry in categories[:64]:
                if (type(entry) is not list or len(entry) != 2
                        or type(entry[0]) is not str):
                    raise FieldEffectError("persistence")
                category = entry[0]
                signals.append(_operational_signal(
                    "category_suspended", "automatic_suspension", "category",
                    category=category,
                ))
            if self.field_effect_paused():
                signals.append(_operational_signal(
                    "field_suspended", "owner_pause", "runtime"
                ))
            authentication = self._connection.execute(
                "SELECT work.failure_root,work.report_id,report.report_id,"
                "report.document FROM review_preparation_work AS work "
                "LEFT JOIN metadata_reports AS report "
                "ON report.report_id=work.report_id "
                "WHERE work.state='failed' "
                "AND work.failure_code='authentication' "
                "ORDER BY work.updated_at DESC,work.work_id LIMIT 256"
            ).fetchall()
            authentication_roots: set[str] = set()
            for failure_root, work_report_id, report_id, document in authentication:
                if work_report_id is None:
                    if failure_root is None:
                        raise FieldEffectError("persistence")
                    authentication_roots.add(failure_root)
                    continue
                if report_id != work_report_id or type(document) is not str:
                    raise FieldEffectError("persistence")
                try:
                    report = MetadataReport.from_json(document)
                except (ReportValueError, TypeError, ValueError) as error:
                    raise FieldEffectError("persistence") from error
                if report.report_id != report_id:
                    raise FieldEffectError("persistence")
                report_roots = {
                    provider.provider
                    for provider in report.providers
                    if any(
                        status in {
                            ProviderStatus.AUTHENTICATION,
                            ProviderStatus.AUTHORIZATION,
                        }
                        for status in (
                            provider.status,
                            *(lane.status for lane in provider.lanes),
                        )
                    )
                }
                if not report_roots:
                    raise FieldEffectError("persistence")
                authentication_roots.update(report_roots)
            for provider_root in sorted(authentication_roots)[:64]:
                signals.append(_operational_signal(
                    "authentication_failure", "provider_authentication",
                    "connection", connection=provider_root,
                ))
            rows = self._connection.execute(
                "SELECT connection_name,library_name,work_id,destination,state,document "
                "FROM field_effects WHERE state NOT IN ('verified','superseded','abandoned') "
                "ORDER BY updated_at DESC,plan_id LIMIT 256"
            ).fetchall()
            for connection, library, work_id, destination, state, document in rows:
                operation = FieldEffectOperation.from_json(document)
                failure = operation.failure
                if failure in {"target_currentness", "third_value", "readback"}:
                    kind, cause = "owner_action_required", "currentness_conflict"
                    scope = "field"
                    signal_library, signal_work, signal_field = (
                        library, work_id, destination
                    )
                elif failure == "backup":
                    kind, cause = "backup_failure", "backup"
                    scope = "library"
                    signal_library, signal_work, signal_field = library, None, None
                elif failure in {"capability", "target"}:
                    kind, cause = "service_incompatibility", "service_incompatibility"
                    scope = "connection"
                    signal_library, signal_work, signal_field = None, None, None
                elif failure in {"authority", "consent", "consent_revoked"}:
                    kind, cause = "field_suspended", "authority_unavailable"
                    scope = "field"
                    signal_library, signal_work, signal_field = (
                        library, work_id, destination
                    )
                elif state in {"claimed", "applying", "reconciling", "retryable"}:
                    kind, cause = "automatic_recovery", (
                        "reconciliation" if state == "reconciling" else "in_progress"
                    )
                    scope = "field"
                    signal_library, signal_work, signal_field = (
                        library, work_id, destination
                    )
                else:
                    kind, cause = "healthy_activity", "planned"
                    scope = "field"
                    signal_library, signal_work, signal_field = (
                        library, work_id, destination
                    )
                signals.append(_operational_signal(
                    kind, cause, scope, connection=connection,
                    library=signal_library, work_id=signal_work,
                    field=signal_field,
                ))
            return {"signals": signals}
        except (json.JSONDecodeError, sqlite3.Error, FieldEffectError) as error:
            raise FieldEffectError("persistence") from error

    def _upsert_field_effect_action(self, operation: FieldEffectOperation,
                                    kind: str, state: str, attempt: int,
                                    changed_at: int) -> None:
        action_id = "effect_action_" + hashlib.sha256(
            f"{operation.plan.plan_id}:{kind}".encode()
        ).hexdigest()
        document = canonical_effect_json({
            "action_id": action_id, "attempt": attempt, "kind": kind,
            "plan_id": operation.plan.plan_id,
            "schema_version": "field-effect-action-v1", "state": state,
            "updated_at": changed_at,
        })
        self._connection.execute(
            "INSERT INTO field_effect_actions(action_id,plan_id,kind,state,attempt,updated_at,document) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(plan_id,kind) DO UPDATE SET "
            "state=excluded.state,attempt=excluded.attempt,updated_at=excluded.updated_at,document=excluded.document",
            (action_id, operation.plan.plan_id, kind, state, attempt, changed_at, document),
        )

    def _schedule_field_effect_cleanup(self, operation: FieldEffectOperation,
                                       changed_at: int) -> None:
        if (operation.plan.destination == "cover"
                and operation.state in {"verified", "stale", "superseded", "abandoned"}):
            self._upsert_field_effect_cleanup(
                operation.plan.plan_id, "pending", 0, changed_at
            )

    def _upsert_field_effect_cleanup(self, plan_id: str, state: str,
                                     attempt: int, changed_at: int) -> None:
        document = canonical_effect_json({
            "attempt": attempt, "plan_id": plan_id,
            "schema_version": "field-effect-cleanup-v1", "state": state,
            "updated_at": changed_at,
        })
        self._connection.execute(
            "INSERT INTO field_effect_cleanups(plan_id,state,attempt,updated_at,document) "
            "VALUES(?,?,?,?,?) ON CONFLICT(plan_id) DO UPDATE SET "
            "state=excluded.state,attempt=excluded.attempt,updated_at=excluded.updated_at,document=excluded.document",
            (plan_id, state, attempt, changed_at, document),
        )

    def _update_field_effect(self, operation: FieldEffectOperation,
                             expected_state: str) -> None:
        cursor = self._connection.execute(
            "UPDATE field_effects SET state=?,attempt=?,updated_at=?,document=? WHERE plan_id=? AND state=?",
            (operation.state, operation.attempt, operation.updated_at,
             canonical_effect_json(operation.to_data()), operation.plan.plan_id,
             expected_state),
        )
        if cursor.rowcount != 1:
            raise FieldEffectError("transition_conflict")

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














def _operational_signal(kind: str, cause: str, scope: str, *,
                        connection: str | None = None,
                        library: str | None = None,
                        category: str | None = None,
                        work_id: str | None = None,
                        field: str | None = None) -> dict[str, object]:
    return {"category": category, "cause": cause, "connection": connection,
            "field": field, "kind": kind, "library": library,
            "scope": scope, "work_id": work_id}


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
