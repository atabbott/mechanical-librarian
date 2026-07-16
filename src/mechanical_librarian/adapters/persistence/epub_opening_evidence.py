"""SQLite persistence for durable fenced EPUB opening evidence."""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Iterator

from mechanical_librarian.domain.epub_opening import EpubOpeningReport
from mechanical_librarian.domain.epub_opening_evidence import (
    EpubEvidenceClaim,
    EpubEvidenceFailure,
    EpubEvidenceLocator,
    EpubEvidenceMode,
    EpubRecoverableWork,
    EpubEvidenceResult,
    EpubSourceResolution,
    ResolvedEpubSource,
    epub_evidence_identity,
    epub_work_key,
    result_from_report,
)
from mechanical_librarian.domain.identifiers import SnapshotId, canonical_json
from mechanical_librarian.domain.inventory_v2 import (
    InventoryObservation,
    InventorySnapshotV2,
    opaque_snapshot_reference,
    opaque_target_reference,
)


class SQLiteEpubOpeningEvidenceRepository:
    """Resolve private bindings and publish immutable evidence atomically."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def next_recoverable(
        self,
        mount_configuration_digests: frozenset[str],
        governing_digest: str,
        now: datetime,
    ) -> EpubRecoverableWork | None:
        if (
            type(mount_configuration_digests) is not frozenset
            or not mount_configuration_digests
            or any(not _digest(value) for value in mount_configuration_digests)
            or not _digest(governing_digest)
            or not _valid_time(now)
        ):
            return None
        placeholders = ",".join("?" for _ in mount_configuration_digests)
        row = self._connection.execute(
            "SELECT j.work_id,j.work_key,j.target_reference,j.snapshot_reference,j.file_index,"
            "b.mount_configuration_digest,j.source_fingerprint FROM epub_evidence_jobs j "
            "JOIN inventory_snapshot_file_bindings b ON b.binding_digest=j.binding_digest "
            "WHERE j.governing_digest=? AND j.state IN ('pending','retry_wait','running') "
            "AND (j.state!='running' OR j.lease_expires_at<=?) "
            f"AND b.mount_configuration_digest IN ({placeholders}) "
            "AND j.cancellation_requested=0 ORDER BY j.created_at,j.lane_revision,j.work_id LIMIT 1",
            (governing_digest, _format_timestamp(now), *sorted(mount_configuration_digests)),
        ).fetchone()
        return (
            None
            if row is None
            else EpubRecoverableWork(
                str(row[0]),
                str(row[1]),
                EpubEvidenceLocator(str(row[2]), str(row[3]), int(row[4])),
                str(row[5]),
                str(row[6]),
            )
        )

    def cancel(self, work_id: str, authority: str, cancelled_at: datetime) -> bool:
        if type(work_id) is not str or type(authority) is not str or not authority or not _valid_time(cancelled_at):
            return False
        try:
            with _transaction(self._connection):
                row = self._connection.execute(
                    "SELECT state,attempt,fence FROM epub_evidence_jobs WHERE work_id=?",
                    (work_id,),
                ).fetchone()
                if row is None or str(row[0]) not in {"pending", "retry_wait", "running"}:
                    return False
                if str(row[0]) == "running":
                    self._connection.execute(
                        "UPDATE epub_evidence_attempts SET finished_at=?,failure='cancelled' "
                        "WHERE work_id=? AND attempt=? AND finished_at IS NULL",
                        (_format_timestamp(cancelled_at), work_id, int(row[1])),
                    )
                self._connection.execute(
                    "UPDATE epub_evidence_jobs SET state='cancelled',cancellation_requested=1,"
                    "lease_owner=NULL,lease_expires_at=NULL,terminal_failure='cancelled',updated_at=? "
                    "WHERE work_id=?",
                    (_format_timestamp(cancelled_at), work_id),
                )
                self._event(work_id, "cancelled", cancelled_at, actor=authority, attempt=int(row[1]), fence=int(row[2]), from_state=str(row[0]), to_state="cancelled")
                return True
        except (sqlite3.Error, ValueError):
            return False

    def resolve(
        self, locator: EpubEvidenceLocator, mount_configuration_digest: str
    ) -> EpubSourceResolution:
        if (
            type(locator) is not EpubEvidenceLocator
            or not _digest(mount_configuration_digest)
        ):
            return EpubSourceResolution(failure=EpubEvidenceFailure.INVALID_INVOCATION)
        try:
            return self._resolve(locator, mount_configuration_digest)
        except sqlite3.Error:
            return EpubSourceResolution(failure=EpubEvidenceFailure.PERSISTENCE_FAILURE)
        except ValueError:
            return EpubSourceResolution(failure=EpubEvidenceFailure.CORRUPT_STATE)

    def _resolve(
        self, locator: EpubEvidenceLocator, mount_configuration_digest: str
    ) -> EpubSourceResolution:
        rows = self._connection.execute(
            "SELECT s.snapshot_id,s.fingerprint_digest,s.canonical_observation,"
            "s.observed_at,b.binding_digest,b.mount_name,b.relative_path,"
            "b.file_identity,b.media_kind,b.mount_configuration_digest,"
            "s.adapter_id,s.connection_id,s.library_id,s.external_item "
            "FROM current_inventory_v2 c JOIN inventory_v2_snapshots s USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "JOIN inventory_snapshot_file_bindings b USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "WHERE b.target_reference=? AND b.snapshot_reference=? "
            "AND b.file_index=? AND b.media_kind='epub' "
            "AND b.mount_configuration_digest=?",
            (locator.target_reference, locator.snapshot_reference, locator.file_index, mount_configuration_digest),
        ).fetchall()
        if len(rows) > 1:
            return EpubSourceResolution(failure=EpubEvidenceFailure.AMBIGUOUS_REFERENCE)
        if not rows:
            return EpubSourceResolution(
                failure=self._resolution_failure(locator, mount_configuration_digest)
            )
        matches: list[ResolvedEpubSource] = []
        for row in rows:
            observation = InventoryObservation.from_json(
                str(row[2]), _parse_timestamp(str(row[3]))
            )
            snapshot = InventorySnapshotV2.create(observation)
            if (
                snapshot.id != SnapshotId(str(row[0]))
                or snapshot.fingerprint.digest != str(row[1])
                or observation.media_type.value != "ebook"
                or observation.lifecycle.value != "active"
                or opaque_target_reference(observation.target)
                != locator.target_reference
                or opaque_snapshot_reference(snapshot)
                != locator.snapshot_reference
            ):
                continue
            binding_value = {
                "file_identity": str(row[7]),
                "file_index": locator.file_index,
                "media_kind": str(row[8]),
                "mount": str(row[5]),
                "mount_configuration_digest": str(row[9]),
                "relative_path": str(row[6]),
                "schema": "inventory-file-binding-v1",
                "snapshot": str(row[0]),
                "snapshot_fingerprint": str(row[1]),
                "target": [str(value) for value in row[10:14]],
            }
            if hashlib.sha256(
                canonical_json(binding_value).encode("utf-8")
            ).hexdigest() != str(row[4]):
                raise ValueError("inventory file binding identity is inconsistent")
            matches.append(
                ResolvedEpubSource(
                    locator,
                    str(row[4]),
                    mount_configuration_digest,
                    str(row[5]),
                    str(row[6]),
                )
            )
        if len(matches) != 1:
            return EpubSourceResolution(failure=EpubEvidenceFailure.BINDING_INTEGRITY_FAILURE)
        return EpubSourceResolution(source=matches[0])

    def _resolution_failure(
        self, locator: EpubEvidenceLocator, mount_configuration_digest: str
    ) -> EpubEvidenceFailure:
        snapshot_index_rows = self._connection.execute(
            "SELECT target_reference,media_type,lifecycle "
            "FROM inventory_snapshot_locator_index WHERE snapshot_reference=? LIMIT 2",
            (locator.snapshot_reference,),
        ).fetchall()
        if snapshot_index_rows and any(
            str(row[0]) != locator.target_reference for row in snapshot_index_rows
        ):
            return EpubEvidenceFailure.WRONG_TARGET
        matching_snapshot_rows = tuple(
            row for row in snapshot_index_rows if str(row[0]) == locator.target_reference
        )
        if matching_snapshot_rows:
            if any(str(row[1]) != "ebook" for row in matching_snapshot_rows):
                return EpubEvidenceFailure.NON_EPUB
            if any(str(row[2]) != "active" for row in matching_snapshot_rows):
                return EpubEvidenceFailure.STALE_SNAPSHOT
        snapshot_rows = self._connection.execute(
            "SELECT target_reference FROM inventory_snapshot_file_bindings "
            "WHERE snapshot_reference=? LIMIT 2", (locator.snapshot_reference,)
        ).fetchall()
        if snapshot_rows and any(str(row[0]) != locator.target_reference for row in snapshot_rows):
            return EpubEvidenceFailure.WRONG_TARGET
        target_exists = self._connection.execute(
            "SELECT 1 FROM inventory_snapshot_file_bindings WHERE target_reference=? LIMIT 1",
            (locator.target_reference,),
        ).fetchone()
        if target_exists is None:
            return EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE
        known_snapshot = self._connection.execute(
            "SELECT 1 FROM inventory_snapshot_locator_index WHERE target_reference=? "
            "AND snapshot_reference=? UNION SELECT 1 FROM inventory_snapshot_file_bindings "
            "WHERE target_reference=? AND snapshot_reference=? LIMIT 1",
            (
                locator.target_reference,
                locator.snapshot_reference,
                locator.target_reference,
                locator.snapshot_reference,
            ),
        ).fetchone()
        if known_snapshot is None:
            return EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE
        current = self._connection.execute(
            "SELECT 1 FROM current_inventory_v2 c JOIN inventory_v2_snapshots s USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "JOIN inventory_snapshot_file_bindings b USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "WHERE b.target_reference=? AND b.snapshot_reference=? LIMIT 1",
            (locator.target_reference, locator.snapshot_reference),
        ).fetchone()
        if current is None:
            return EpubEvidenceFailure.STALE_SNAPSHOT
        index_row = self._connection.execute(
            "SELECT mount_configuration_digest,media_kind FROM inventory_snapshot_file_bindings "
            "WHERE target_reference=? AND snapshot_reference=? AND file_index=? LIMIT 2",
            (locator.target_reference, locator.snapshot_reference, locator.file_index),
        ).fetchall()
        if not index_row:
            return EpubEvidenceFailure.FILE_INDEX_OUT_OF_RANGE
        if any(str(row[1]) != "epub" for row in index_row):
            return EpubEvidenceFailure.NON_EPUB
        if all(str(row[0]) != mount_configuration_digest for row in index_row):
            return EpubEvidenceFailure.MOUNT_MISMATCH
        return EpubEvidenceFailure.BINDING_INTEGRITY_FAILURE

    def accept(
        self,
        source: ResolvedEpubSource,
        source_fingerprint: str,
        governing_digest: str,
        accepted_at: datetime,
        initiating_authority: str = "local_operator",
    ) -> tuple[str, EpubEvidenceResult | None] | None:
        if (
            type(source) is not ResolvedEpubSource
            or not _digest(source_fingerprint)
            or not _digest(governing_digest)
            or not _valid_time(accepted_at)
            or type(initiating_authority) is not str
            or not 1 <= len(initiating_authority) <= 64
        ):
            return None
        work_key = epub_work_key(source, source_fingerprint, governing_digest)
        work_id = "epub_work_" + work_key[:32]
        try:
            with _transaction(self._connection):
                if (
                    self._resolve(
                        source.locator, source.mount_configuration_digest
                    ).source != source
                ):
                    return None
                evidence = self._reusable(work_key, source.locator)
                if evidence is not None:
                    self._event(work_id, "reused", accepted_at, actor=initiating_authority, allow_missing_job=True)
                    return work_id, evidence
                existing = self._connection.execute(
                    "SELECT work_id,source_fingerprint,governing_digest,binding_digest,state "
                    "FROM epub_evidence_jobs WHERE work_key=?",
                    (work_key,),
                ).fetchone()
                if existing is not None:
                    if tuple(map(str, existing[1:4])) != (
                        source_fingerprint,
                        governing_digest,
                        source.binding_digest,
                    ):
                        raise ValueError("EPUB work identity collision")
                    if str(existing[4]) in {"failed_terminal", "cancelled"}:
                        return str(existing[0]), EpubEvidenceResult(
                            EpubEvidenceMode.FAILED,
                            failure=EpubEvidenceFailure.RETRY_EXHAUSTED,
                        )
                    return str(existing[0]), None
                timestamp = _format_timestamp(accepted_at)
                lane_revision = int(self._connection.execute(
                    "SELECT COALESCE(MAX(lane_revision),0)+1 FROM epub_evidence_jobs "
                    "WHERE target_reference=? AND file_index=?",
                    (source.locator.target_reference, source.locator.file_index),
                ).fetchone()[0])
                self._connection.execute(
                    "INSERT INTO epub_evidence_jobs(work_id,work_key,target_reference,"
                    "snapshot_reference,file_index,binding_digest,source_fingerprint,"
                    "governing_digest,initiating_authority,lane_revision,state,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        work_id,
                        work_key,
                        source.locator.target_reference,
                        source.locator.snapshot_reference,
                        source.locator.file_index,
                        source.binding_digest,
                        source_fingerprint,
                        governing_digest,
                        initiating_authority,
                        lane_revision,
                        "pending",
                        timestamp,
                        timestamp,
                    ),
                )
                self._event(work_id, "accepted", accepted_at, actor=initiating_authority, from_state="missing", to_state="pending")
                return work_id, None
        except ValueError:
            return work_id, EpubEvidenceResult(
                EpubEvidenceMode.FAILED, failure=EpubEvidenceFailure.CORRUPT_STATE
            )
        except sqlite3.Error:
            return None

    def claim(
        self,
        work_id: str,
        worker_id: str,
        claimed_at: datetime,
        lease_duration: timedelta,
        capacity: int = 1,
    ) -> EpubEvidenceClaim | None:
        if (
            type(work_id) is not str
            or not work_id.startswith("epub_work_")
            or type(worker_id) is not str
            or not worker_id
            or len(worker_id) > 64
            or not _valid_time(claimed_at)
            or type(lease_duration) is not timedelta
            or not 1 <= lease_duration.total_seconds() <= 3_700
            or type(capacity) is not int
            or not 1 <= capacity <= 64
        ):
            return None
        try:
            with _transaction(self._connection):
                row = self._connection.execute(
                    "SELECT work_key,state,attempt,fence,lease_expires_at,"
                    "cancellation_requested,max_attempts FROM epub_evidence_jobs "
                    "WHERE work_id=?",
                    (work_id,),
                ).fetchone()
                if row is None or bool(row[5]):
                    return None
                state = str(row[1])
                attempt = int(row[2])
                fence = int(row[3])
                if state == "running":
                    if _parse_timestamp(str(row[4])) > claimed_at:
                        return None
                    self._connection.execute(
                        "UPDATE epub_evidence_attempts SET finished_at=?,failure='lease_expired' "
                        "WHERE work_id=? AND attempt=? AND finished_at IS NULL",
                        (_format_timestamp(claimed_at), work_id, attempt),
                    )
                    state = "retry_wait"
                    self._connection.execute(
                        "UPDATE epub_evidence_jobs SET state='retry_wait',lease_owner=NULL,"
                        "lease_expires_at=NULL,terminal_failure='lease_expired',updated_at=? "
                        "WHERE work_id=? AND state='running' AND fence=?",
                        (_format_timestamp(claimed_at), work_id, fence),
                    )
                    if attempt >= int(row[6]):
                        self._connection.execute(
                            "UPDATE epub_evidence_jobs SET state='failed_terminal',"
                            "terminal_failure='retry_exhausted',updated_at=? WHERE work_id=?",
                            (_format_timestamp(claimed_at), work_id),
                        )
                        self._event(work_id, "retry_exhausted", claimed_at, actor=worker_id, attempt=attempt, fence=fence, from_state="running", to_state="failed_terminal")
                        return None
                if state not in {"pending", "retry_wait"} or attempt >= int(row[6]):
                    return None
                active = int(self._connection.execute(
                    "SELECT COUNT(*) FROM epub_evidence_jobs WHERE state='running' "
                    "AND lease_expires_at>?", (_format_timestamp(claimed_at),)
                ).fetchone()[0])
                if active >= capacity:
                    return None
                next_attempt = attempt + 1
                next_fence = fence + 1
                expires = claimed_at + lease_duration
                cursor = self._connection.execute(
                    "UPDATE epub_evidence_jobs SET state='running',attempt=?,fence=?,"
                    "lease_owner=?,lease_expires_at=?,terminal_failure=NULL,updated_at=? "
                    "WHERE work_id=? AND state IN ('pending','retry_wait') AND attempt=? "
                    "AND fence=? AND cancellation_requested=0",
                    (
                        next_attempt,
                        next_fence,
                        worker_id,
                        _format_timestamp(expires),
                        _format_timestamp(claimed_at),
                        work_id,
                        attempt,
                        fence,
                    ),
                )
                if cursor.rowcount != 1:
                    return None
                self._connection.execute(
                    "INSERT INTO epub_evidence_attempts(work_id,attempt,fence,worker_id,"
                    "started_at) VALUES (?,?,?,?,?)",
                    (
                        work_id,
                        next_attempt,
                        next_fence,
                        worker_id,
                        _format_timestamp(claimed_at),
                    ),
                )
                self._event(work_id, "claimed", claimed_at, actor=worker_id, attempt=next_attempt, fence=next_fence, from_state=state, to_state="running")
                return EpubEvidenceClaim(
                    work_id, str(row[0]), next_attempt, next_fence, worker_id
                )
        except (sqlite3.Error, ValueError):
            return None

    def publish(
        self,
        claim: EpubEvidenceClaim,
        source: ResolvedEpubSource,
        source_fingerprint: str,
        governing_digest: str,
        report: EpubOpeningReport,
        published_at: datetime,
        source_is_current: Callable[[], bool] = lambda: True,
    ) -> EpubEvidenceResult | None:
        if (
            type(claim) is not EpubEvidenceClaim
            or type(source) is not ResolvedEpubSource
            or not _digest(source_fingerprint)
            or not _digest(governing_digest)
            or type(report) is not EpubOpeningReport
            or not _valid_time(published_at)
            or not callable(source_is_current)
        ):
            return None
        try:
            with _transaction(self._connection):
                row = self._live_claim(claim, published_at)
                if row is None or (
                    str(row[0]), str(row[1]), str(row[2]), str(row[3])
                ) != (
                    claim.work_key,
                    source.binding_digest,
                    source_fingerprint,
                    governing_digest,
                ):
                    return None
                if self._resolve(source.locator, source.mount_configuration_digest).source != source:
                    return None
                if not source_is_current():
                    return EpubEvidenceResult(
                        EpubEvidenceMode.FAILED,
                        failure=EpubEvidenceFailure.SOURCE_CHANGED,
                    )
                if (
                    report.source_fingerprint is not None
                    and report.source_fingerprint.digest != source_fingerprint
                ):
                    return None
                evidence_id, evidence_digest, report_digest, parser_mode = (
                    epub_evidence_identity(
                        work_key=claim.work_key,
                        binding_digest=source.binding_digest,
                        source_fingerprint=source_fingerprint,
                        governing_digest=governing_digest,
                        report=report,
                    )
                )
                report_json = report.to_json()
                selected_count = sum(
                    value.disposition.value == "selected" for value in report.resources
                )
                values = (
                    evidence_digest,
                    claim.work_key,
                    source.locator.target_reference,
                    source.locator.snapshot_reference,
                    source.locator.file_index,
                    source.binding_digest,
                    source_fingerprint,
                    governing_digest,
                    report.outcome.value,
                    report_json,
                    report_digest,
                    len(report.claims),
                    len(report.claims),
                    selected_count,
                    len(report.resources) - selected_count,
                    len(report.warnings),
                    "validated",
                    parser_mode,
                )
                existing = self._connection.execute(
                    "SELECT evidence_digest,work_key,target_reference,snapshot_reference,"
                    "file_index,binding_digest,source_fingerprint,governing_digest,outcome,"
                    "report_json,report_digest,claim_count,citation_count,selected_count,"
                    "rejected_count,warning_count,package_mode,parser_mode "
                    "FROM epub_opening_evidence WHERE evidence_id=?",
                    (evidence_id,),
                ).fetchone()
                if existing is None:
                    self._connection.execute(
                        "INSERT INTO epub_opening_evidence(evidence_id,evidence_digest,"
                        "work_key,target_reference,snapshot_reference,file_index,binding_digest,"
                        "source_fingerprint,governing_digest,outcome,report_json,report_digest,"
                        "claim_count,citation_count,selected_count,rejected_count,warning_count,"
                        "package_mode,parser_mode,created_at) VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (evidence_id, *values, _format_timestamp(published_at)),
                    )
                elif tuple(existing) != values:
                    raise ValueError("EPUB evidence identity collision")
                current = self._connection.execute(
                    "SELECT evidence_id,sequence,lane_revision FROM current_epub_opening_evidence "
                    "WHERE target_reference=? AND file_index=?",
                    (source.locator.target_reference, source.locator.file_index),
                ).fetchone()
                lane_revision = int(self._connection.execute(
                    "SELECT lane_revision FROM epub_evidence_jobs WHERE work_id=?",
                    (claim.work_id,),
                ).fetchone()[0])
                current_applicable = current is None or lane_revision >= int(current[2])
                if current_applicable:
                    sequence = 1 if current is None else int(current[1]) + (
                        str(current[0]) != evidence_id
                    )
                    self._connection.execute(
                        "INSERT INTO current_epub_opening_evidence(target_reference,file_index,"
                        "evidence_id,sequence,lane_revision,updated_at) VALUES (?,?,?,?,?,?) ON CONFLICT("
                        "target_reference,file_index) DO UPDATE SET evidence_id=excluded.evidence_id,"
                        "sequence=excluded.sequence,lane_revision=excluded.lane_revision,updated_at=excluded.updated_at",
                        (
                            source.locator.target_reference,
                            source.locator.file_index,
                            evidence_id,
                            sequence,
                            lane_revision,
                            _format_timestamp(published_at),
                        ),
                    )
                self._connection.execute(
                    "INSERT INTO epub_evidence_checkpoints(work_id,attempt,fence,evidence_id,"
                    "accepted_at) VALUES (?,?,?,?,?)",
                    (
                        claim.work_id,
                        claim.attempt,
                        claim.fence,
                        evidence_id,
                        _format_timestamp(published_at),
                    ),
                )
                self._connection.execute(
                    "UPDATE epub_evidence_attempts SET finished_at=?,outcome=? WHERE "
                    "work_id=? AND attempt=? AND fence=? AND finished_at IS NULL",
                    (
                        _format_timestamp(published_at),
                        report.outcome.value,
                        claim.work_id,
                        claim.attempt,
                        claim.fence,
                    ),
                )
                cursor = self._connection.execute(
                    "UPDATE epub_evidence_jobs SET state='succeeded',lease_owner=NULL,"
                    "lease_expires_at=NULL,terminal_outcome=?,terminal_failure=NULL,"
                    "evidence_id=?,updated_at=? WHERE work_id=? AND state='running' "
                    "AND attempt=? AND fence=? AND lease_owner=? AND cancellation_requested=0",
                    (
                        report.outcome.value,
                        evidence_id,
                        _format_timestamp(published_at),
                        claim.work_id,
                        claim.attempt,
                        claim.fence,
                        claim.worker_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("EPUB publication fence changed")
                self._event(claim.work_id, "published", published_at, actor=claim.worker_id, attempt=claim.attempt, fence=claim.fence, from_state="running", to_state="succeeded")
                return result_from_report(EpubEvidenceMode.EXECUTED, evidence_id, report, current_applicable=current_applicable)
        except ValueError:
            return EpubEvidenceResult(
                EpubEvidenceMode.FAILED, failure=EpubEvidenceFailure.CORRUPT_STATE
            )
        except sqlite3.Error:
            return None

    def fail(
        self,
        claim: EpubEvidenceClaim,
        failure: EpubEvidenceFailure,
        failed_at: datetime,
        *,
        retryable: bool,
    ) -> EpubEvidenceResult:
        result = EpubEvidenceResult(EpubEvidenceMode.FAILED, failure=failure)
        if (
            type(claim) is not EpubEvidenceClaim
            or type(failure) is not EpubEvidenceFailure
            or type(retryable) is not bool
            or not _valid_time(failed_at)
        ):
            return result
        try:
            with _transaction(self._connection):
                row = self._live_claim(claim, failed_at)
                if row is None:
                    return result
                state = "retry_wait" if retryable and claim.attempt < 3 else "failed_terminal"
                self._connection.execute(
                    "UPDATE epub_evidence_attempts SET finished_at=?,failure=? WHERE "
                    "work_id=? AND attempt=? AND fence=? AND finished_at IS NULL",
                    (
                        _format_timestamp(failed_at),
                        failure.value,
                        claim.work_id,
                        claim.attempt,
                        claim.fence,
                    ),
                )
                self._connection.execute(
                    "UPDATE epub_evidence_jobs SET state=?,lease_owner=NULL,"
                    "lease_expires_at=NULL,terminal_failure=?,updated_at=? WHERE work_id=? "
                    "AND state='running' AND attempt=? AND fence=? AND lease_owner=?",
                    (
                        state,
                        failure.value,
                        _format_timestamp(failed_at),
                        claim.work_id,
                        claim.attempt,
                        claim.fence,
                        claim.worker_id,
                    ),
                )
                self._event(claim.work_id, "failed", failed_at, actor=claim.worker_id, attempt=claim.attempt, fence=claim.fence, from_state="running", to_state=state)
        except (sqlite3.Error, ValueError):
            return EpubEvidenceResult(
                EpubEvidenceMode.FAILED,
                failure=EpubEvidenceFailure.PERSISTENCE_FAILURE,
            )
        return result

    def _live_claim(
        self, claim: EpubEvidenceClaim, now: datetime
    ) -> tuple[object, ...] | None:
        row = self._connection.execute(
            "SELECT work_key,binding_digest,source_fingerprint,governing_digest,"
            "lease_expires_at,cancellation_requested FROM epub_evidence_jobs WHERE "
            "work_id=? AND state='running' AND attempt=? AND fence=? AND lease_owner=?",
            (claim.work_id, claim.attempt, claim.fence, claim.worker_id),
        ).fetchone()
        return (
            row
            if row is not None
            and not bool(row[5])
            and _parse_timestamp(str(row[4])) > now
            else None
        )

    def _reusable(
        self, work_key: str, locator: EpubEvidenceLocator
    ) -> EpubEvidenceResult | None:
        row = self._connection.execute(
            "SELECT e.evidence_id,e.report_json,CASE WHEN c.evidence_id=e.evidence_id "
            "THEN 1 ELSE 0 END FROM epub_opening_evidence e LEFT JOIN "
            "current_epub_opening_evidence c ON c.target_reference=e.target_reference "
            "AND c.file_index=e.file_index WHERE e.work_key=? AND e.target_reference=? "
            "AND e.file_index=?",
            (work_key, locator.target_reference, locator.file_index),
        ).fetchone()
        if row is None:
            return None
        report = EpubOpeningReport.from_json(str(row[1]))
        return result_from_report(
            EpubEvidenceMode.REUSED,
            str(row[0]),
            report,
            current_applicable=bool(row[2]),
        )

    def _event(
        self,
        work_id: str,
        kind: str,
        occurred_at: datetime,
        *,
        actor: str,
        attempt: int = 0,
        fence: int = 0,
        from_state: str = "succeeded",
        to_state: str = "succeeded",
        allow_missing_job: bool = False,
    ) -> None:
        if allow_missing_job:
            row = self._connection.execute(
                "SELECT work_id FROM epub_evidence_jobs WHERE work_id=?", (work_id,)
            ).fetchone()
            if row is None:
                return
        sequence = int(
            self._connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM epub_evidence_events WHERE work_id=?",
                (work_id,),
            ).fetchone()[0]
        )
        digest = hashlib.sha256(f"{work_id}:{sequence}:{kind}".encode()).hexdigest()
        self._connection.execute(
            "INSERT INTO epub_evidence_events(event_id,work_id,kind,actor,attempt,fence,"
            "from_state,to_state,sequence,occurred_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "epub_event_" + digest[:32],
                work_id,
                kind,
                actor,
                attempt,
                fence,
                from_state,
                to_state,
                sequence,
                _format_timestamp(occurred_at),
            ),
        )


def _digest(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _valid_time(value: object) -> bool:
    return (
        type(value) is datetime
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=timezone.utc
    )


@contextmanager
def _transaction(connection: sqlite3.Connection) -> Iterator[None]:
    if connection.in_transaction:
        raise sqlite3.OperationalError("nested EPUB evidence transaction")
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except Exception:
        connection.rollback()
        raise


__all__ = ["SQLiteEpubOpeningEvidenceRepository"]
