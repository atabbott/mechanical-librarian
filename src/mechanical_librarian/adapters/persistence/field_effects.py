"""The field-effect aggregate: the writes and the aggregate's own reads.

Owns `field_effects` and every table that hangs off one plan -- the actions
queue, the authorities, the consents, the manual holds, the capability
suspensions, the no-change records, the cleanups, the append-only events, and
the singleton `field_effect_control` pause. Thirty-eight methods move verbatim,
every `except` block and message intact.

The cross-aggregate query and projection side -- the activity page and the
pending-decision readers, which join the review, decision, cover, policy and
report aggregates -- is not here. It owns no table and reads six aggregates, so
it belongs with the composition root or a projection module of its own.

Four seams cross the boundary, and every one is injected as a lambda resolved
through the composing repository at call time rather than as a bound method
captured at construction. That is load-bearing rather than stylistic: three of
the four are substituted by the field-effect and review-pipeline tests, on the
instance or on the class, to force a failure or drift a value, and a captured
bound method would bypass the substitution and leave those tests passing while
testing nothing.

- `_record_field_effect_event` writes `field_effect_events`, so it belongs
  here, and it is deliberately still on the composing repository.
  `test_guarded_faults_are_classified_by_cause` patches it on the repository
  *class* and drives it through `service.apply`; once the six callers moved
  here, resolving `self._record_field_effect_event` inside this module would
  find this class instead and the forced disk failure would never happen. It
  moves when the composition root owns dispatch. `claim_field_effect`, which
  also calls it, is still on the composing repository for its own reasons.
- `field_effect_consent_envelope_identity` binds consent to the exact current
  automatic policy semantics and reads the policy aggregate to do it.
  `test_semantic_consent_drift_wins_the_atomic_claim_race` patches it on the
  class.
- `field_effect_authority_source` resolves a persisted decision against its
  current case lineage across four aggregates.
- `_retire_stale_cover_consideration` belongs to the cover-consideration
  aggregate, which owns that write.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from typing import cast

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.transactions import ConnectionScoped
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from mechanical_librarian.domain.field_effect import (
    FIELD_EFFECT_FAILURES,
    EffectConsent,
    FieldEffectError,
    FieldEffectNoChange,
    FieldEffectOperation,
    project_decision_value,
)
from mechanical_librarian.domain.field_effect import (
    canonical_json as canonical_effect_json,
)
from mechanical_librarian.domain.human_decision import HumanDecision


class SQLiteFieldEffectRepository(ConnectionScoped):
    """Plan, claim, transition, suspend and retire one library's field effects."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        *,
        record_field_effect_event: Callable[[FieldEffectOperation, str, int], None],
        field_effect_consent_envelope_identity: Callable[[str], str],
        field_effect_authority_source: Callable[
            [HumanDecision | AutomaticDecision], dict[str, object]
        ],
        retire_stale_cover_consideration: Callable[
            [FieldEffectOperation, str, int], None
        ],
    ) -> None:
        super().__init__(connection, binding)
        self._record_field_effect_event = record_field_effect_event
        self.field_effect_consent_envelope_identity = (
            field_effect_consent_envelope_identity
        )
        self.field_effect_authority_source = field_effect_authority_source
        self._retire_stale_cover_consideration = retire_stale_cover_consideration

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
            self._retire_stale_cover_consideration(
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
            self._retire_stale_cover_consideration(
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
