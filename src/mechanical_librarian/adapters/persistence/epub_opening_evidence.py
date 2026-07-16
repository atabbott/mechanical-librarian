"""SQLite persistence for durable fenced EPUB opening evidence."""

from __future__ import annotations

import hashlib
import json
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
                self._validate_work_prefix(work_id)
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
                evidence = self._reusable(
                    work_key,
                    source,
                    source_fingerprint,
                    governing_digest,
                )
                if evidence is not None:
                    last_event = self._connection.execute(
                        "SELECT occurred_at FROM epub_evidence_events WHERE work_id=? "
                        "ORDER BY sequence DESC LIMIT 1",
                        (work_id,),
                    ).fetchone()
                    if (
                        last_event is None
                        or _parse_timestamp(str(last_event[0])) > accepted_at
                    ):
                        raise ValueError("EPUB reuse time is inconsistent")
                    self._event(
                        work_id,
                        "reused",
                        accepted_at,
                        actor=initiating_authority,
                        allow_missing_job=True,
                    )
                    return work_id, evidence
                existing = self._connection.execute(
                    "SELECT work_id,source_fingerprint,governing_digest,binding_digest,state,"
                    "terminal_failure "
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
                    state = str(existing[4])
                    if state in {"failed_terminal", "cancelled"}:
                        try:
                            terminal_failure = EpubEvidenceFailure(str(existing[5]))
                        except ValueError as error:
                            raise ValueError(
                                "EPUB terminal failure is unsupported"
                            ) from error
                        if (state == "cancelled") != (
                            terminal_failure is EpubEvidenceFailure.CANCELLED
                        ):
                            raise ValueError(
                                "EPUB terminal state and failure disagree"
                            )
                        return str(existing[0]), EpubEvidenceResult(
                            EpubEvidenceMode.FAILED,
                            failure=terminal_failure,
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
    ) -> EpubEvidenceClaim | EpubEvidenceFailure | None:
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
                self._validate_work_prefix(work_id)
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
                    expired = self._connection.execute(
                        "UPDATE epub_evidence_attempts SET finished_at=?,failure='lease_expired' "
                        "WHERE work_id=? AND attempt=? AND fence=? AND finished_at IS NULL",
                        (
                            _format_timestamp(claimed_at),
                            work_id,
                            attempt,
                            fence,
                        ),
                    )
                    if expired.rowcount != 1:
                        raise ValueError("EPUB expired attempt is inconsistent")
                    state = "retry_wait"
                    expired_job = self._connection.execute(
                        "UPDATE epub_evidence_jobs SET state='retry_wait',lease_owner=NULL,"
                        "lease_expires_at=NULL,terminal_failure='lease_expired',updated_at=? "
                        "WHERE work_id=? AND state='running' AND fence=?",
                        (_format_timestamp(claimed_at), work_id, fence),
                    )
                    if expired_job.rowcount != 1:
                        raise ValueError("EPUB expired job is inconsistent")
                    expired_worker = self._connection.execute(
                        "SELECT worker_id FROM epub_evidence_attempts WHERE work_id=? "
                        "AND attempt=? AND fence=?",
                        (work_id, attempt, fence),
                    ).fetchone()
                    if expired_worker is None:
                        raise ValueError("EPUB expired attempt worker is unavailable")
                    self._event(
                        work_id,
                        "failed",
                        claimed_at,
                        actor=str(expired_worker[0]),
                        attempt=attempt,
                        fence=fence,
                        from_state="running",
                        to_state="retry_wait",
                    )
                    if attempt >= int(row[6]):
                        self._connection.execute(
                            "UPDATE epub_evidence_jobs SET state='failed_terminal',"
                            "terminal_failure='retry_exhausted',updated_at=? WHERE work_id=?",
                            (_format_timestamp(claimed_at), work_id),
                        )
                        self._event(
                            work_id,
                            "retry_exhausted",
                            claimed_at,
                            actor=worker_id,
                            attempt=attempt,
                            fence=fence,
                            from_state="retry_wait",
                            to_state="failed_terminal",
                        )
                        return EpubEvidenceFailure.RETRY_EXHAUSTED
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
        except ValueError:
            return EpubEvidenceFailure.CORRUPT_STATE
        except sqlite3.Error:
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
                current_attempt = self._validate_work_prefix(claim.work_id)
                if current_attempt is None or (
                    int(current_attempt[0]),
                    int(current_attempt[1]),
                    str(current_attempt[2]),
                ) != (claim.attempt, claim.fence, claim.worker_id):
                    raise ValueError("EPUB publication attempt is inconsistent")
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
                completed_attempt = self._connection.execute(
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
                if completed_attempt.rowcount != 1:
                    raise ValueError("EPUB publication attempt was not completed")
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
        if failure is EpubEvidenceFailure.RETRY_EXHAUSTED:
            return EpubEvidenceResult(
                EpubEvidenceMode.FAILED,
                failure=EpubEvidenceFailure.INVALID_INVOCATION,
            )
        try:
            with _transaction(self._connection):
                row = self._live_claim(claim, failed_at)
                if row is None:
                    raise _PublicationFenceLost
                current_attempt = self._validate_work_prefix(claim.work_id)
                if current_attempt is None or (
                    int(current_attempt[0]),
                    int(current_attempt[1]),
                    str(current_attempt[2]),
                ) != (claim.attempt, claim.fence, claim.worker_id):
                    raise ValueError("EPUB failure attempt is inconsistent")
                state = (
                    "cancelled"
                    if failure is EpubEvidenceFailure.CANCELLED
                    else "retry_wait"
                    if retryable and claim.attempt < 3
                    else "failed_terminal"
                )
                completed_attempt = self._connection.execute(
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
                if completed_attempt.rowcount != 1:
                    raise ValueError("EPUB failed attempt was not completed")
                self._event(
                    claim.work_id,
                    "cancelled"
                    if state == "cancelled"
                    else "failed",
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
                    "lease_expires_at=NULL,terminal_failure=?,cancellation_requested=?,"
                    "updated_at=? WHERE work_id=? "
                    "AND state='running' AND attempt=? AND fence=? AND lease_owner=? "
                    "AND cancellation_requested=0 AND lease_expires_at>?",
                    (
                        state,
                        failure.value,
                        1 if state == "cancelled" else 0,
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

    def _validate_work_prefix(self, work_id: str) -> tuple[object, ...] | None:
        job = self._connection.execute(
            "SELECT state,attempt,fence,initiating_authority,created_at,"
            "terminal_outcome,terminal_failure,lease_owner,lease_expires_at,"
            "cancellation_requested,updated_at FROM epub_evidence_jobs WHERE work_id=?",
            (work_id,),
        ).fetchone()
        if job is None or str(job[0]) not in {
            "pending",
            "running",
            "retry_wait",
            "failed_terminal",
            "cancelled",
        }:
            return None
        state = str(job[0])
        job_attempt = int(job[1])
        job_fence = int(job[2])
        terminal_failure = None if job[6] is None else str(job[6])
        attempts = self._connection.execute(
            "SELECT attempt,fence,worker_id,started_at,finished_at,outcome,failure "
            "FROM epub_evidence_attempts WHERE work_id=? ORDER BY attempt",
            (work_id,),
        ).fetchall()
        events = self._connection.execute(
            "SELECT event_id,kind,actor,attempt,fence,from_state,to_state,sequence,"
            "occurred_at FROM epub_evidence_events WHERE work_id=? ORDER BY sequence",
            (work_id,),
        ).fetchall()
        expected_events: list[tuple[object, ...]] = [
            _epub_event_record(
                work_id,
                "accepted",
                str(job[3]),
                0,
                0,
                "missing",
                "pending",
                1,
                str(job[4]),
            )
        ]
        valid = (
            len(attempts) == job_attempt
            and job_fence == job_attempt
            and job[5] is None
            and bool(job[9]) == (state == "cancelled")
        )
        previous_time = _parse_timestamp(str(job[4]))
        exhaustion_event_required = False
        for ordinal, attempt_row in enumerate(attempts, start=1):
            attempt_number = int(attempt_row[0])
            attempt_fence = int(attempt_row[1])
            worker_id = str(attempt_row[2])
            started_at = str(attempt_row[3])
            started_time = _parse_timestamp(started_at)
            valid = valid and (
                attempt_number == ordinal
                and attempt_fence == ordinal
                and started_time >= previous_time
            )
            expected_events.append(
                _epub_event_record(
                    work_id,
                    "claimed",
                    worker_id,
                    attempt_number,
                    attempt_fence,
                    "pending" if ordinal == 1 else "retry_wait",
                    "running",
                    len(expected_events) + 1,
                    started_at,
                )
            )
            current_running = ordinal == len(attempts) and state == "running"
            if current_running:
                valid = valid and (
                    attempt_row[4] is None
                    and attempt_row[5] is None
                    and attempt_row[6] is None
                )
                previous_time = started_time
                continue
            finished_at = (
                None if attempt_row[4] is None else str(attempt_row[4])
            )
            if finished_at is None:
                valid = False
                continue
            finished_time = _parse_timestamp(finished_at)
            valid = valid and (
                finished_time >= started_time
                and attempt_row[5] is None
                and attempt_row[6] is not None
            )
            next_event = (
                events[len(expected_events)]
                if len(events) > len(expected_events)
                else None
            )
            cancelled_running = (
                state == "cancelled"
                and ordinal == len(attempts)
                and next_event is not None
                and str(next_event[1]) == "cancelled"
            )
            if cancelled_running:
                valid = valid and str(attempt_row[6]) == "cancelled"
                expected_events.append(
                    _epub_event_record(
                        work_id,
                        "cancelled",
                        str(next_event[2]),
                        attempt_number,
                        attempt_fence,
                        "running",
                        "cancelled",
                        len(expected_events) + 1,
                        finished_at,
                    )
                )
            else:
                failure_target = (
                    "failed_terminal"
                    if state == "failed_terminal"
                    and ordinal == len(attempts)
                    and terminal_failure != "retry_exhausted"
                    else "retry_wait"
                )
                exhaustion_event_required = (
                    state == "failed_terminal"
                    and ordinal == len(attempts)
                    and terminal_failure == "retry_exhausted"
                    and failure_target == "retry_wait"
                )
                expected_events.append(
                    _epub_event_record(
                        work_id,
                        "failed",
                        worker_id,
                        attempt_number,
                        attempt_fence,
                        "running",
                        failure_target,
                        len(expected_events) + 1,
                        finished_at,
                    )
                )
            previous_time = finished_time
        if state == "pending":
            valid = valid and (
                not attempts
                and terminal_failure is None
                and job[7] is None
                and job[8] is None
            )
        elif state == "running":
            valid = valid and (
                bool(attempts)
                and terminal_failure is None
                and job[7] is not None
                and job[8] is not None
                and str(job[7]) == str(attempts[-1][2])
                and _parse_timestamp(str(job[8]))
                > _parse_timestamp(str(attempts[-1][3]))
            )
        elif state == "retry_wait":
            valid = valid and (
                bool(attempts)
                and terminal_failure == str(attempts[-1][6])
                and job[7] is None
                and job[8] is None
            )
        elif state == "failed_terminal":
            valid = valid and (
                bool(attempts)
                and terminal_failure is not None
                and terminal_failure != "cancelled"
                and job[7] is None
                and job[8] is None
                and (
                    (
                        exhaustion_event_required
                        and str(attempts[-1][6]) == "lease_expired"
                    )
                    or terminal_failure == str(attempts[-1][6])
                )
            )
            if exhaustion_event_required:
                retry_event = (
                    events[len(expected_events)]
                    if len(events) > len(expected_events)
                    else None
                )
                valid = valid and retry_event is not None
                if retry_event is not None:
                    expected_events.append(
                        _epub_event_record(
                            work_id,
                            "retry_exhausted",
                            str(retry_event[2]),
                            job_attempt,
                            job_fence,
                            "retry_wait",
                            "failed_terminal",
                            len(expected_events) + 1,
                            str(job[10]),
                        )
                    )
        else:
            valid = valid and (
                terminal_failure == "cancelled"
                and job[7] is None
                and job[8] is None
            )
            already_cancelled = bool(expected_events) and str(
                expected_events[-1][1]
            ) == "cancelled"
            if not already_cancelled:
                cancel_event = (
                    events[len(expected_events)]
                    if len(events) > len(expected_events)
                    else None
                )
                valid = valid and cancel_event is not None
                if cancel_event is not None:
                    expected_events.append(
                        _epub_event_record(
                            work_id,
                            "cancelled",
                            str(cancel_event[2]),
                            job_attempt,
                            job_fence,
                            "pending" if not attempts else "retry_wait",
                            "cancelled",
                            len(expected_events) + 1,
                            str(job[10]),
                        )
                    )
        valid = valid and _parse_timestamp(str(job[10])) >= previous_time
        if not valid or tuple(map(tuple, events)) != tuple(expected_events):
            raise ValueError("EPUB work history prefix is inconsistent")
        return attempts[-1] if attempts else None

    def _reusable(
        self,
        work_key: str,
        source: ResolvedEpubSource,
        source_fingerprint: str,
        governing_digest: str,
    ) -> EpubEvidenceResult | None:
        locator = source.locator
        job = self._connection.execute(
            "SELECT work_id,target_reference,snapshot_reference,file_index,"
            "binding_digest,source_fingerprint,governing_digest,state,attempt,fence,"
            "evidence_id,terminal_outcome,terminal_failure,initiating_authority,created_at "
            "FROM epub_evidence_jobs "
            "WHERE work_key=?",
            (work_key,),
        ).fetchone()
        expected_work_id = "epub_work_" + work_key[:32]
        if job is None:
            orphan = self._connection.execute(
                "SELECT (SELECT COUNT(*) FROM epub_opening_evidence WHERE work_key=?),"
                "(SELECT COUNT(*) FROM epub_evidence_checkpoints WHERE work_id=?)",
                (work_key, expected_work_id),
            ).fetchone()
            if tuple(map(int, orphan)) != (0, 0):
                raise ValueError("EPUB reusable work state is orphaned")
            return None
        job_core = (
            str(job[0]),
            str(job[1]),
            str(job[2]),
            int(job[3]),
            str(job[4]),
            str(job[5]),
            str(job[6]),
        )
        if job_core != (
            expected_work_id,
            locator.target_reference,
            locator.snapshot_reference,
            locator.file_index,
            source.binding_digest,
            source_fingerprint,
            governing_digest,
        ):
            raise ValueError("EPUB reusable job identity is inconsistent")
        evidence = self._connection.execute(
            "SELECT e.evidence_id,e.evidence_digest,e.work_key,e.target_reference,"
            "e.snapshot_reference,e.file_index,e.binding_digest,e.source_fingerprint,"
            "e.governing_digest,e.outcome,e.report_json,e.report_digest,e.claim_count,"
            "e.citation_count,e.selected_count,e.rejected_count,e.warning_count,"
            "e.package_mode,e.parser_mode,CASE WHEN c.evidence_id=e.evidence_id "
            "THEN 1 ELSE 0 END FROM epub_opening_evidence e LEFT JOIN "
            "current_epub_opening_evidence c ON c.target_reference=e.target_reference "
            "AND c.file_index=e.file_index WHERE e.evidence_id=?",
            (job[10],),
        ).fetchone() if job[10] is not None else None
        work_evidence = self._connection.execute(
            "SELECT evidence_id FROM epub_opening_evidence WHERE work_key=?",
            (work_key,),
        ).fetchall()
        checkpoints = self._connection.execute(
            "SELECT attempt,fence,evidence_id,accepted_at FROM epub_evidence_checkpoints "
            "WHERE work_id=? ORDER BY attempt,fence",
            (expected_work_id,),
        ).fetchall()
        attempts = self._connection.execute(
            "SELECT attempt,fence,worker_id,started_at,finished_at,outcome,failure "
            "FROM epub_evidence_attempts WHERE work_id=? ORDER BY attempt",
            (expected_work_id,),
        ).fetchall()
        events = self._connection.execute(
            "SELECT event_id,kind,actor,attempt,fence,from_state,to_state,sequence,"
            "occurred_at FROM epub_evidence_events WHERE work_id=? ORDER BY sequence",
            (expected_work_id,),
        ).fetchall()
        state = str(job[7])
        if state != "succeeded":
            self._validate_work_prefix(expected_work_id)
            if (
                job[10] is not None
                or job[11] is not None
                or evidence is not None
                or work_evidence
                or checkpoints
                or any(str(event[1]) == "published" for event in events)
            ):
                raise ValueError("EPUB non-succeeded work has immutable results")
            return None
        expected_events: list[tuple[object, ...]] = [
            _epub_event_record(
                expected_work_id,
                "accepted",
                str(job[13]),
                0,
                0,
                "missing",
                "pending",
                1,
                str(job[14]),
            )
        ]
        previous_time = _parse_timestamp(str(job[14]))
        attempt_history_valid = len(attempts) == int(job[8])
        for ordinal, attempt_row in enumerate(attempts, start=1):
            attempt_number = int(attempt_row[0])
            attempt_fence = int(attempt_row[1])
            worker_id = str(attempt_row[2])
            started_at = str(attempt_row[3])
            finished_at = (
                None if attempt_row[4] is None else str(attempt_row[4])
            )
            attempt_history_valid = attempt_history_valid and (
                attempt_number == ordinal
                and attempt_fence == ordinal
                and _parse_timestamp(started_at) >= previous_time
            )
            expected_events.append(
                _epub_event_record(
                    expected_work_id,
                    "claimed",
                    worker_id,
                    attempt_number,
                    attempt_fence,
                    "pending" if ordinal == 1 else "retry_wait",
                    "running",
                    len(expected_events) + 1,
                    started_at,
                )
            )
            if finished_at is None:
                attempt_history_valid = False
                continue
            finished_time = _parse_timestamp(finished_at)
            attempt_history_valid = (
                attempt_history_valid
                and finished_time >= _parse_timestamp(started_at)
            )
            if ordinal < len(attempts):
                attempt_history_valid = attempt_history_valid and (
                    attempt_row[5] is None and attempt_row[6] is not None
                )
                expected_events.append(
                    _epub_event_record(
                        expected_work_id,
                        "failed",
                        worker_id,
                        attempt_number,
                        attempt_fence,
                        "running",
                        "retry_wait",
                        len(expected_events) + 1,
                        finished_at,
                    )
                )
            else:
                attempt_history_valid = attempt_history_valid and (
                    attempt_number == int(job[8])
                    and attempt_fence == int(job[9])
                    and attempt_row[5] is not None
                    and attempt_row[6] is None
                )
                expected_events.append(
                    _epub_event_record(
                        expected_work_id,
                        "published",
                        worker_id,
                        attempt_number,
                        attempt_fence,
                        "running",
                        "succeeded",
                        len(expected_events) + 1,
                        finished_at,
                    )
                )
            previous_time = finished_time
        core_event_count = len(expected_events)
        reusable_events_valid = True
        for sequence, event in enumerate(
            events[core_event_count:], start=core_event_count + 1
        ):
            event_time = _parse_timestamp(str(event[8]))
            reusable_events_valid = reusable_events_valid and (
                tuple(event)
                == _epub_event_record(
                    expected_work_id,
                    "reused",
                    str(event[2]),
                    0,
                    0,
                    "succeeded",
                    "succeeded",
                    sequence,
                    str(event[8]),
                )
                and event_time >= previous_time
            )
            previous_time = event_time
        terminal_attempt = attempts[-1] if attempts else None
        finished_at = (
            None
            if terminal_attempt is None or terminal_attempt[4] is None
            else str(terminal_attempt[4])
        )
        if (
            job[10] is None
            or job[11] is None
            or job[12] is not None
            or evidence is None
            or len(work_evidence) != 1
            or str(work_evidence[0][0]) != str(job[10])
            or not attempt_history_valid
            or finished_at is None
            or terminal_attempt is None
            or str(terminal_attempt[5]) != str(job[11])
            or checkpoints
            != [(int(job[8]), int(job[9]), str(job[10]), finished_at)]
            or tuple(map(tuple, events[:core_event_count]))
            != tuple(expected_events)
            or not reusable_events_valid
        ):
            raise ValueError("EPUB succeeded work state is inconsistent")
        report = EpubOpeningReport.from_json(str(evidence[10]))
        evidence_id, evidence_digest, report_digest, parser_mode = (
            epub_evidence_identity(
                work_key=work_key,
                binding_digest=source.binding_digest,
                source_fingerprint=source_fingerprint,
                governing_digest=governing_digest,
                report=report,
            )
        )
        selected_count = sum(
            value.disposition.value == "selected" for value in report.resources
        )
        expected = (
            evidence_id,
            evidence_digest,
            work_key,
            locator.target_reference,
            locator.snapshot_reference,
            locator.file_index,
            source.binding_digest,
            source_fingerprint,
            governing_digest,
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
        observed = tuple(evidence[:19])
        if observed != expected:
            raise ValueError("EPUB reusable evidence identity is inconsistent")
        if str(job[11]) != report.outcome.value:
            raise ValueError("EPUB succeeded work outcome is inconsistent")
        return result_from_report(
            EpubEvidenceMode.REUSED,
            evidence_id,
            report,
            current_applicable=bool(evidence[19]),
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
        record = _epub_event_record(
            work_id,
            kind,
            actor,
            attempt,
            fence,
            from_state,
            to_state,
            sequence,
            _format_timestamp(occurred_at),
        )
        self._connection.execute(
            "INSERT INTO epub_evidence_events(event_id,work_id,kind,actor,attempt,fence,"
            "from_state,to_state,sequence,occurred_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (record[0], work_id, *record[1:]),
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
        locator = connection.execute(
            "SELECT 1 FROM inventory_snapshot_locator_index WHERE adapter_id=? "
            "AND connection_id=? AND library_id=? AND external_item=? "
            "AND snapshot_id=?",
            (*identity, snapshot.id.value),
        ).fetchone()
        if locator is not None:
            raise ValueError("current inventory binding set pointer is unavailable")
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


def _epub_event_record(
    work_id: str,
    kind: str,
    actor: str,
    attempt: int,
    fence: int,
    from_state: str,
    to_state: str,
    sequence: int,
    occurred_at: str,
) -> tuple[object, ...]:
    values = (
        kind,
        actor,
        attempt,
        fence,
        from_state,
        to_state,
        sequence,
        occurred_at,
    )
    digest = hashlib.sha256(
        json.dumps(
            (work_id, *values), ensure_ascii=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return ("epub_event_" + digest[:32], *values)


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
