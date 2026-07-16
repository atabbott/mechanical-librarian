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
from mechanical_librarian.domain.identifiers import SnapshotId
from mechanical_librarian.domain.inventory import FileReference
from mechanical_librarian.domain.inventory_v2 import (
    InventoryBindingSetMember,
    InventoryFileBinding,
    InventoryObservation,
    InventorySnapshotV2,
    ValidatedInventoryFileBinding,
    opaque_snapshot_reference,
    opaque_target_reference,
    validate_inventory_binding_set,
)


class _PublicationFenceLost(Exception):
    """Roll back publication when ownership is no longer live."""


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
            "s.observed_at "
            "FROM current_inventory_v2 c JOIN inventory_v2_snapshots s USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "JOIN inventory_snapshot_locator_index i USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "WHERE i.target_reference=? AND i.snapshot_reference=?",
            (locator.target_reference, locator.snapshot_reference),
        ).fetchall()
        if len(rows) > 1:
            return EpubSourceResolution(failure=EpubEvidenceFailure.AMBIGUOUS_REFERENCE)
        if not rows:
            return EpubSourceResolution(
                failure=self._resolution_failure(locator, mount_configuration_digest)
            )
        row = rows[0]
        observation = InventoryObservation.from_json(
            str(row[2]), _parse_timestamp(str(row[3]))
        )
        snapshot = InventorySnapshotV2.create(observation)
        if (
            snapshot.id != SnapshotId(str(row[0]))
            or snapshot.fingerprint.digest != str(row[1])
            or opaque_target_reference(observation.target)
            != locator.target_reference
            or opaque_snapshot_reference(snapshot) != locator.snapshot_reference
        ):
            raise ValueError("current inventory snapshot identity is inconsistent")
        if (
            observation.media_type.value != "ebook"
            or observation.lifecycle.value != "active"
        ):
            return EpubSourceResolution(
                failure=self._resolution_failure(
                    locator, mount_configuration_digest
                )
            )
        current_bindings = _validated_current_inventory_bindings(
            self._connection, snapshot
        )
        matches = [
            ResolvedEpubSource(
                locator,
                value.digest,
                mount_configuration_digest,
                value.binding.reference.mount,
                value.binding.reference.relative_path,
            )
            for value in current_bindings
            if value.binding.file_index == locator.file_index
            and value.binding.media_kind == "epub"
            and value.binding.mount_configuration_digest
            == mount_configuration_digest
        ]
        if len(matches) != 1:
            return EpubSourceResolution(
                failure=self._resolution_failure(
                    locator,
                    mount_configuration_digest,
                    current_bindings,
                )
            )
        return EpubSourceResolution(source=matches[0])

    def _resolution_failure(
        self,
        locator: EpubEvidenceLocator,
        mount_configuration_digest: str,
        current_bindings: tuple[ValidatedInventoryFileBinding, ...] | None = None,
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
        exact_binding = self._connection.execute(
            "SELECT 1 FROM inventory_snapshot_file_bindings "
            "WHERE target_reference=? AND snapshot_reference=? LIMIT 1",
            (locator.target_reference, locator.snapshot_reference),
        ).fetchone()
        if matching_snapshot_rows and exact_binding is None:
            return EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE
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
            "JOIN inventory_snapshot_locator_index i USING "
            "(adapter_id,connection_id,library_id,external_item,snapshot_id) "
            "WHERE i.target_reference=? AND i.snapshot_reference=? LIMIT 1",
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
        current_index_rows = [
            (
                value.binding.mount_configuration_digest,
                value.binding.media_kind,
            )
            for value in current_bindings or ()
            if value.binding.file_index == locator.file_index
        ]
        if not current_index_rows:
            return EpubEvidenceFailure.SOURCE_REFERENCE_UNAVAILABLE
        if any(str(row[1]) != "epub" for row in current_index_rows):
            return EpubEvidenceFailure.NON_EPUB
        if all(
            str(row[0]) != mount_configuration_digest
            for row in current_index_rows
        ):
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
                self._connection.execute(
                    "DELETE FROM current_epub_opening_evidence WHERE target_reference=? "
                    "AND file_index=? AND lane_revision<?",
                    (
                        source.locator.target_reference,
                        source.locator.file_index,
                        lane_revision,
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
        publication_clock: Callable[[], datetime] | None = None,
    ) -> EpubEvidenceResult | None:
        if (
            type(claim) is not EpubEvidenceClaim
            or type(source) is not ResolvedEpubSource
            or not _digest(source_fingerprint)
            or not _digest(governing_digest)
            or type(report) is not EpubOpeningReport
            or not _valid_time(published_at)
            or not callable(source_is_current)
            or (publication_clock is not None and not callable(publication_clock))
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
                if epub_work_key(
                    source, source_fingerprint, governing_digest
                ) != claim.work_key:
                    raise ValueError("EPUB work identity is inconsistent")
                if self._resolve(source.locator, source.mount_configuration_digest).source != source:
                    return None
                if not source_is_current():
                    return EpubEvidenceResult(
                        EpubEvidenceMode.FAILED,
                        failure=EpubEvidenceFailure.SOURCE_CHANGED,
                    )
                published_at = (
                    published_at
                    if publication_clock is None
                    else publication_clock()
                )
                if not _valid_time(published_at) or self._live_claim(
                    claim, published_at
                ) is None:
                    return None
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
                newest_lane_revision = int(
                    self._connection.execute(
                        "SELECT MAX(lane_revision) FROM epub_evidence_jobs "
                        "WHERE target_reference=? AND file_index=?",
                        (
                            source.locator.target_reference,
                            source.locator.file_index,
                        ),
                    ).fetchone()[0]
                )
                current_applicable = lane_revision == newest_lane_revision and (
                    current is None or lane_revision >= int(current[2])
                )
                if current_applicable:
                    sequence = (
                        lane_revision
                        if current is None
                        else int(current[1]) + (str(current[0]) != evidence_id)
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
                result = result_from_report(
                    EpubEvidenceMode.EXECUTED,
                    evidence_id,
                    report,
                    current_applicable=current_applicable,
                )
                self._event(
                    claim.work_id,
                    "published",
                    published_at,
                    actor=claim.worker_id,
                    attempt=claim.attempt,
                    fence=claim.fence,
                    from_state="running",
                    to_state="succeeded",
                )
                finalized_at = (
                    published_at
                    if publication_clock is None
                    else publication_clock()
                )
                if not _valid_time(finalized_at):
                    raise _PublicationFenceLost
                cursor = self._connection.execute(
                    "UPDATE epub_evidence_jobs SET state='succeeded',lease_owner=NULL,"
                    "lease_expires_at=NULL,terminal_outcome=?,terminal_failure=NULL,"
                    "evidence_id=?,updated_at=? WHERE work_id=? AND state='running' "
                    "AND attempt=? AND fence=? AND lease_owner=? AND cancellation_requested=0 "
                    "AND lease_expires_at>?",
                    (
                        report.outcome.value,
                        evidence_id,
                        _format_timestamp(finalized_at),
                        claim.work_id,
                        claim.attempt,
                        claim.fence,
                        claim.worker_id,
                        _format_timestamp(finalized_at),
                    ),
                )
                if cursor.rowcount != 1:
                    raise _PublicationFenceLost
            return result
        except _PublicationFenceLost:
            return None
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
        failure_clock: Callable[[], datetime] | None = None,
    ) -> EpubEvidenceResult:
        result = EpubEvidenceResult(EpubEvidenceMode.FAILED, failure=failure)
        if (
            type(claim) is not EpubEvidenceClaim
            or type(failure) is not EpubEvidenceFailure
            or type(retryable) is not bool
            or not _valid_time(failed_at)
            or (failure_clock is not None and not callable(failure_clock))
        ):
            return result
        try:
            with _transaction(self._connection):
                row = self._live_claim(claim, failed_at)
                if row is None:
                    raise _PublicationFenceLost
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
                self._event(
                    claim.work_id,
                    "failed",
                    failed_at,
                    actor=claim.worker_id,
                    attempt=claim.attempt,
                    fence=claim.fence,
                    from_state="running",
                    to_state=state,
                )
                finalized_at = (
                    failed_at if failure_clock is None else failure_clock()
                )
                if not _valid_time(finalized_at):
                    raise _PublicationFenceLost
                cursor = self._connection.execute(
                    "UPDATE epub_evidence_jobs SET state=?,lease_owner=NULL,"
                    "lease_expires_at=NULL,terminal_failure=?,updated_at=? WHERE work_id=? "
                    "AND state='running' AND attempt=? AND fence=? AND lease_owner=? "
                    "AND cancellation_requested=0 AND lease_expires_at>?",
                    (
                        state,
                        failure.value,
                        _format_timestamp(finalized_at),
                        claim.work_id,
                        claim.attempt,
                        claim.fence,
                        claim.worker_id,
                        _format_timestamp(finalized_at),
                    ),
                )
                if cursor.rowcount != 1:
                    raise _PublicationFenceLost
        except (_PublicationFenceLost, sqlite3.Error, ValueError):
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
            "SELECT e.evidence_id,e.evidence_digest,e.binding_digest,"
            "e.source_fingerprint,e.governing_digest,e.outcome,e.report_json,"
            "e.report_digest,e.claim_count,e.citation_count,e.selected_count,"
            "e.rejected_count,e.warning_count,e.package_mode,e.parser_mode,"
            "CASE WHEN c.evidence_id=e.evidence_id THEN 1 ELSE 0 END FROM "
            "epub_evidence_jobs j JOIN epub_opening_evidence e ON "
            "e.evidence_id=j.evidence_id LEFT JOIN "
            "current_epub_opening_evidence c ON c.target_reference=e.target_reference "
            "AND c.file_index=e.file_index WHERE j.work_key=? AND j.state='succeeded' "
            "AND e.work_key=j.work_key AND e.target_reference=? AND e.file_index=?",
            (work_key, locator.target_reference, locator.file_index),
        ).fetchone()
        if row is None:
            return None
        report = EpubOpeningReport.from_json(str(row[6]))
        evidence_id, evidence_digest, report_digest, parser_mode = (
            epub_evidence_identity(
                work_key=work_key,
                binding_digest=str(row[2]),
                source_fingerprint=str(row[3]),
                governing_digest=str(row[4]),
                report=report,
            )
        )
        selected_count = sum(
            value.disposition.value == "selected" for value in report.resources
        )
        expected = (
            evidence_id,
            evidence_digest,
            report.outcome.value,
            report.to_json(),
            report_digest,
            len(report.claims),
            len(report.claims),
            selected_count,
            len(report.resources) - selected_count,
            len(report.warnings),
            "validated",
            parser_mode,
        )
        observed = (
            str(row[0]),
            str(row[1]),
            str(row[5]),
            str(row[6]),
            str(row[7]),
            int(row[8]),
            int(row[9]),
            int(row[10]),
            int(row[11]),
            int(row[12]),
            str(row[13]),
            str(row[14]),
        )
        if observed != expected:
            raise ValueError("EPUB reusable evidence identity is inconsistent")
        return result_from_report(
            EpubEvidenceMode.REUSED,
            evidence_id,
            report,
            current_applicable=bool(row[15]),
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


def _validated_current_inventory_bindings(
    connection: sqlite3.Connection,
    snapshot: InventorySnapshotV2,
) -> tuple[ValidatedInventoryFileBinding, ...]:
    target = snapshot.observation.target
    identity = (
        target.adapter.value,
        target.connection.value,
        target.library.value,
        target.external_item.value,
    )
    pointer = connection.execute(
        "SELECT snapshot_id,binding_set_digest FROM "
        "current_inventory_snapshot_binding_sets WHERE adapter_id=? "
        "AND connection_id=? AND library_id=? AND external_item=?",
        identity,
    ).fetchone()
    if pointer is None:
        return ()
    if str(pointer[0]) != snapshot.id.value:
        raise ValueError("current inventory binding set snapshot is inconsistent")
    set_digest = str(pointer[1])
    header = connection.execute(
        "SELECT adapter_id,connection_id,library_id,external_item,snapshot_id,"
        "binding_count FROM inventory_snapshot_binding_sets "
        "WHERE binding_set_digest=?",
        (set_digest,),
    ).fetchone()
    if header is None:
        raise ValueError("current inventory binding set identity is inconsistent")
    rows = connection.execute(
        "SELECT m.file_index,m.binding_digest,b.adapter_id,b.connection_id,"
        "b.library_id,b.external_item,b.snapshot_id,b.target_reference,"
        "b.snapshot_reference,b.file_index,b.file_identity,b.media_kind,"
        "b.mount_configuration_digest,b.mount_name,b.relative_path "
        "FROM inventory_snapshot_binding_set_members m LEFT JOIN "
        "inventory_snapshot_file_bindings b ON b.binding_digest=m.binding_digest "
        "WHERE m.binding_set_digest=? ORDER BY m.file_index",
        (set_digest,),
    ).fetchall()
    members: list[InventoryBindingSetMember] = []
    for row in rows:
        if any(value is None for value in row[2:]):
            raise ValueError("current inventory binding set member is unavailable")
        members.append(
            InventoryBindingSetMember(
                int(row[0]),
                str(row[1]),
                (str(row[2]), str(row[3]), str(row[4]), str(row[5])),
                str(row[6]),
                str(row[7]),
                str(row[8]),
                InventoryFileBinding(
                    int(row[9]),
                    str(row[10]),
                    str(row[11]),
                    str(row[12]),
                    FileReference(str(row[13]), str(row[14])),
                ),
            )
        )
    return validate_inventory_binding_set(
        snapshot,
        set_digest,
        (str(header[0]), str(header[1]), str(header[2]), str(header[3])),
        str(header[4]),
        int(header[5]),
        tuple(members),
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
