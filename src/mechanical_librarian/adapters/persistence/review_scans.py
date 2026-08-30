"""The review-scan and target-connection half of review preparation.

Owns `review_preparation_scans`, the singleton `review_preparation_control`
that pauses the pipeline, and `review_preparation_connection_conditions`: which
scan is current, whether preparation is running at all, and whether one target
connection may be reached, must be re-qualified, is deferred, or is held.

It also writes `review_preparation_work`, and that is deliberate rather than a
leak. A scan reset, a target-connection deferral and a recovery all discard the
work rows the previous posture produced, in the same transaction that records
the new posture -- separating them would put one atomic posture change across
two modules.

The pipeline-action lifecycle that consumes those work rows lives in
`review_preparation.py`. Measured at the split, the two halves make no calls to
each other in either direction: this module decides what preparation may run,
and the other module runs it.
"""

from __future__ import annotations

import sqlite3
from typing import cast

from mechanical_librarian.adapters.persistence.preparation_presentation import (
    accept_preparation_trigger,
    defer_preparation_library_addition,
    mark_preparation_recovery,
    refresh_preparation_presentation,
    refresh_preparation_run_state,
)
from mechanical_librarian.adapters.persistence.transactions import ConnectionScoped
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import (
    PipelineAction,
    PipelineProgress,
    ReviewPipelineError,
    TargetConnectionCondition,
    is_first_boot_scan_id,
    pipeline_scan_id,
)
from mechanical_librarian.domain.preparation_presentation import PreparationTrigger
from mechanical_librarian.domain.validation import (
    non_negative_integer,
    prefixed_hex_identity,
)

# The eight buckets #641 names, expressed once over the two persisted columns
# that decide them. Order is the order an owner reads them in: the work has not
# started, it is moving through the stages, it is ready, or it could not finish.
#
# A terminal state is tested before a stage, because `stage` on a failed row is
# where it stopped rather than where it is. `extraction` is the one stage split
# by state: a work queued for extraction has not started, and one running it is
# being read, and those are different sentences to an owner watching.
_STAGE_BUCKETS = (
    ("queued", "w.state IN ('queued','delayed') AND w.stage='extraction'"),
    ("extracting", "w.state='running' AND w.stage='extraction'"),
    ("providers", "w.state IN ('queued','delayed','running') AND w.stage='provider'"),
    ("proposals", "w.state IN ('queued','delayed','running') AND w.stage='proposal'"),
    ("admission", "w.state IN ('queued','delayed','running') AND w.stage='admission'"),
    (
        "projection",
        "w.state IN ('queued','delayed','running') AND w.stage='projection'",
    ),
    ("ready", "w.state='complete'"),
    ("failed", "w.state IN ('failed','stale')"),
)
_STAGE_COUNT_SQL = ",".join(
    f"COALESCE(SUM(CASE WHEN {predicate} THEN 1 ELSE 0 END),0)"
    for _name, predicate in _STAGE_BUCKETS
)


_EMPTY_STAGES = tuple((name, 0) for name, _predicate in _STAGE_BUCKETS)
_preparation_fingerprint = prefixed_hex_identity("")


def _stages(row: tuple[object, ...], offset: int) -> tuple[tuple[str, int], ...]:
    return tuple(
        (name, int(cast(int, row[offset + index])))
        for index, (name, _predicate) in enumerate(_STAGE_BUCKETS)
    )


class SQLiteReviewScanRepository(ConnectionScoped):
    """Admit, pause, recover and gate the scans review preparation may run."""

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
        progress = self._request_review_scan(
            connection,
            library,
            library_id,
            producer_fingerprint,
            requested_at,
            first_boot=first_boot,
            trigger=trigger,
            defer_when_paused=False,
        )
        if progress is None:
            raise MetadataReportError("review preparation state is invalid")
        return progress

    def request_library_addition(
        self,
        connection: str,
        library: str,
        library_id: str,
        producer_fingerprint: str,
        requested_at: int,
    ) -> PipelineProgress | None:
        return self._request_review_scan(
            connection,
            library,
            library_id,
            producer_fingerprint,
            requested_at,
            first_boot=False,
            trigger=PreparationTrigger.LIBRARY_ADDED,
            defer_when_paused=True,
        )

    def deferred_library_additions(self) -> tuple[tuple[str, str], ...]:
        try:
            return tuple(
                self._connection.execute(
                    "SELECT connection_name,library_name "
                    "FROM review_preparation_presentation "
                    "WHERE row_kind='deferred_enrollment' ORDER BY ordinal"
                )
            )
        except sqlite3.Error as error:
            raise MetadataReportError(
                "review preparation persistence failed"
            ) from error

    def _request_review_scan(
        self,
        connection: str,
        library: str,
        library_id: str,
        producer_fingerprint: str,
        requested_at: int,
        *,
        first_boot: bool,
        trigger: PreparationTrigger | None,
        defer_when_paused: bool,
    ) -> PipelineProgress | None:
        if (
            any(
                type(value) is not str or not 1 <= len(value) <= 128
                for value in (connection, library, library_id)
            )
            or not _preparation_fingerprint(producer_fingerprint)
            or not non_negative_integer(requested_at)
            or (trigger is not None and type(trigger) is not PreparationTrigger)
        ):
            raise MetadataReportError("review preparation request is invalid")
        transaction = self._transaction("request_review_scan")
        try:
            transaction.begin()
            control = self._connection.execute(
                "SELECT paused FROM review_preparation_control WHERE singleton=1"
            ).fetchone()
            if control is None:
                raise MetadataReportError("review preparation control is unavailable")
            if bool(control[0]):
                if defer_when_paused:
                    defer_preparation_library_addition(
                        self._connection,
                        connection,
                        library,
                        requested_at,
                        cast(
                            int,
                            (
                                self._connection.execute(
                                    "SELECT revision FROM owner_settings WHERE singleton=1"
                                ).fetchone()
                                or (1,)
                            )[0],
                        ),
                    )
                    transaction.commit()
                    return None
                raise ReviewPipelineError("paused")
            progress = self._request_review_scan_unpaused(
                connection,
                library,
                library_id,
                producer_fingerprint,
                requested_at,
                first_boot=first_boot,
                trigger=trigger,
            )
            transaction.commit()
            return progress
        except (sqlite3.Error, MetadataReportError, ReviewPipelineError) as error:
            transaction.rollback()
            if isinstance(error, (MetadataReportError, ReviewPipelineError)):
                raise
            raise MetadataReportError(
                "review preparation persistence failed"
            ) from error

    def _request_review_scan_unpaused(
        self,
        connection: str,
        library: str,
        library_id: str,
        producer_fingerprint: str,
        requested_at: int,
        *,
        first_boot: bool,
        trigger: PreparationTrigger | None,
        promoting_deferred: bool = False,
        presentation_accepted_at: int | None = None,
    ) -> PipelineProgress:
        row = self._connection.execute(
            "SELECT scan_id,state,producer_fingerprint,rescan_requested FROM review_preparation_scans "
            "WHERE connection_name=? AND library_name=? "
            "ORDER BY requested_at DESC,scan_id DESC LIMIT 1",
            (connection, library),
        ).fetchone()
        if (
            row is not None
            and row[1] in {"completed", "completed_with_failures"}
            and is_first_boot_scan_id(row[0], connection, library)
            and not promoting_deferred
            and trigger is None
        ):
            latest = self._connection.execute(
                "SELECT scan_id,connection_name,library_name,state "
                "FROM review_preparation_scans s WHERE s.scan_id=("
                "SELECT selected.scan_id FROM review_preparation_scans selected "
                "WHERE selected.connection_name=s.connection_name "
                "AND selected.library_name=s.library_name "
                "ORDER BY selected.requested_at DESC,selected.scan_id DESC LIMIT 1)"
            ).fetchall()
            first_preparation_incomplete = all(
                is_first_boot_scan_id(scan_id, row_connection, row_library)
                for scan_id, row_connection, row_library, _state in latest
            ) and any(
                state not in {"completed", "completed_with_failures"}
                for _scan_id, _connection, _library, state in latest
            )
            if first_preparation_incomplete:
                progress = self.review_pipeline_progress(connection, library)
                if progress is None:
                    raise MetadataReportError("review preparation state is invalid")
                return progress
            row = None
            first_boot = False
        if row is not None:
            if row[1] in {"queued", "discovering", "processing"}:
                if row[2] != producer_fingerprint:
                    raise MetadataReportError(
                        "review preparation producer changed during active work"
                    )
                if row[1] == "queued":
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET requested_at=?,"
                        "updated_at=? WHERE scan_id=?",
                        (requested_at, requested_at, row[0]),
                    )
                else:
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET rescan_requested=1,updated_at=? WHERE scan_id=?",
                        (requested_at, row[0]),
                    )
            else:
                self._connection.execute(
                    "UPDATE review_preparation_scans SET state='queued',"
                    "library_id=?,producer_fingerprint=?,requested_at=?,updated_at=?,"
                    "catalogue_generation=NULL,catalogue_fingerprint=NULL,total=0,"
                    "qualification_epoch=NULL,refresh_count=0,"
                    "rescan_requested=0 WHERE scan_id=?",
                    (
                        library_id,
                        producer_fingerprint,
                        requested_at,
                        requested_at,
                        row[0],
                    ),
                )
            self._refresh_target_connection_affected(connection)
            if first_boot and trigger is None:
                trigger = PreparationTrigger.INITIAL_SETUP
            if trigger is not None:
                accept_preparation_trigger(
                    self._connection,
                    trigger,
                    cast(str, row[0]),
                    connection,
                    library,
                    requested_at
                    if presentation_accepted_at is None
                    else presentation_accepted_at,
                    cast(str, row[1]),
                    bool(row[3]),
                )
            progress = self.review_pipeline_progress(connection, library)
            if progress is None:
                raise MetadataReportError("review preparation state is invalid")
            return progress
        scan_id = pipeline_scan_id(connection, library, first_boot=first_boot)
        self._connection.execute(
            "INSERT INTO review_preparation_scans("
            "scan_id,connection_name,library_name,library_id,producer_fingerprint,"
            "state,requested_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                scan_id,
                connection,
                library,
                library_id,
                producer_fingerprint,
                "queued",
                requested_at,
                requested_at,
            ),
        )
        self._refresh_target_connection_affected(connection)
        if first_boot and trigger is None:
            trigger = PreparationTrigger.INITIAL_SETUP
        if trigger is not None:
            accept_preparation_trigger(
                self._connection,
                trigger,
                scan_id,
                connection,
                library,
                requested_at
                if presentation_accepted_at is None
                else presentation_accepted_at,
                None,
                False,
            )
        return PipelineProgress(
            scan_id,
            connection,
            library,
            "queued",
            0,
            0,
            0,
            0,
            False,
            requested_at,
            _EMPTY_STAGES,
        )

    def review_pipeline_progress(
        self, connection: str, library: str
    ) -> PipelineProgress | None:
        try:
            row = self._connection.execute(
                "SELECT scan_id,state,total,rescan_requested,updated_at FROM review_preparation_scans "
                "WHERE connection_name=? AND library_name=? "
                "ORDER BY requested_at DESC LIMIT 1",
                (connection, library),
            ).fetchone()
            if row is None:
                return None
            counts = self._connection.execute(
                "SELECT "
                "COALESCE(SUM(CASE WHEN w.state IN ('complete','failed','stale') THEN 1 ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN w.state='complete' AND w.review_case_id IS NOT NULL THEN 1 ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN w.state IN ('failed','stale') THEN 1 ELSE 0 END),0),"
                f"{_STAGE_COUNT_SQL} "
                "FROM review_preparation_work w WHERE w.scan_id=?",
                (row[0],),
            ).fetchone()
            return PipelineProgress(
                row[0],
                connection,
                library,
                row[1],
                counts[0],
                row[2],
                counts[1],
                counts[2],
                bool(row[3]),
                row[4],
                _stages(counts, 3),
            )
        except sqlite3.Error as error:
            raise MetadataReportError(
                "review preparation persistence failed"
            ) from error

    def review_pipeline_status(
        self, libraries: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[PipelineProgress | None, ...], bool]:
        if (
            type(libraries) is not tuple
            or len(libraries) > 128
            or len(libraries) != len(set(libraries))
            or any(
                type(connection) is not str
                or type(library) is not str
                or not 1 <= len(connection) <= 64
                or not 1 <= len(library) <= 64
                for connection, library in libraries
            )
        ):
            raise MetadataReportError("review preparation scope is invalid")
        transaction = self._transaction("review_pipeline_status", deferred=True)
        try:
            transaction.begin()
            control = self._connection.execute(
                "SELECT paused FROM review_preparation_control WHERE singleton=1"
            ).fetchone()
            if control is None:
                raise MetadataReportError("review preparation control is unavailable")
            scope = (
                " OR ".join(
                    "(s.connection_name=? AND s.library_name=?)" for _ in libraries
                )
                or "0"
            )
            rows = self._connection.execute(
                "SELECT s.scan_id,s.connection_name,s.library_name,s.state,s.total,"
                "s.rescan_requested,s.updated_at,"
                "COALESCE(SUM(CASE WHEN w.state IN ('complete','failed','stale') THEN 1 ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN w.state='complete' AND w.review_case_id IS NOT NULL THEN 1 ELSE 0 END),0),"
                "COALESCE(SUM(CASE WHEN w.state IN ('failed','stale') THEN 1 ELSE 0 END),0),"
                f"{_STAGE_COUNT_SQL} "
                "FROM review_preparation_scans s "
                "LEFT JOIN review_preparation_work w ON w.scan_id=s.scan_id "
                f"WHERE ({scope}) AND s.scan_id=(SELECT selected.scan_id "
                "FROM review_preparation_scans selected "
                "WHERE selected.connection_name=s.connection_name "
                "AND selected.library_name=s.library_name "
                "ORDER BY selected.requested_at DESC,selected.scan_id DESC LIMIT 1) "
                "GROUP BY s.scan_id,s.connection_name,s.library_name,s.state,s.total,"
                "s.rescan_requested,s.updated_at",
                tuple(value for library in libraries for value in library),
            ).fetchall()
            transaction.commit()
            progress = {
                (row[1], row[2]): PipelineProgress(
                    row[0],
                    row[1],
                    row[2],
                    row[3],
                    row[7],
                    row[4],
                    row[8],
                    row[9],
                    bool(row[5]),
                    row[6],
                    _stages(row, 10),
                )
                for row in rows
            }
            return tuple(progress.get(library) for library in libraries), bool(
                control[0]
            )
        except (sqlite3.Error, MetadataReportError) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(
                "review preparation persistence failed"
            ) from error

    def review_pipeline_paused(self) -> bool:
        try:
            row = self._connection.execute(
                "SELECT paused FROM review_preparation_control WHERE singleton=1"
            ).fetchone()
            if row is None:
                raise MetadataReportError("review preparation control is unavailable")
            return bool(row[0])
        except sqlite3.Error as error:
            raise MetadataReportError(
                "review preparation persistence failed"
            ) from error

    def set_review_pipeline_paused(self, paused: bool, updated_at: int) -> None:
        if type(paused) is not bool or type(updated_at) is not int or updated_at < 0:
            raise MetadataReportError("review preparation control is invalid")
        transaction = self._transaction("set_review_pipeline_paused")
        failed = False
        try:
            transaction.begin()
            self._connection.execute(
                "UPDATE review_preparation_control SET paused=?,updated_at=? WHERE singleton=1",
                (paused, updated_at),
            )
            refresh_preparation_run_state(self._connection, updated_at)
            transaction.commit()
        except Exception:
            transaction.rollback()
            failed = True
        if failed:
            raise MetadataReportError("review preparation persistence failed") from None

    def resume_review_pipeline(
        self,
        libraries: tuple[tuple[str, str, str, str], ...],
        updated_at: int,
    ) -> None:
        configured = self._validated_library_scope(libraries, updated_at)
        transaction = self._transaction("resume_review_pipeline")
        failure: BaseException | None
        try:
            transaction.begin()
            self._drain_library_additions(configured, updated_at, clear_pause=True)
            transaction.commit()
        except BaseException as error:
            transaction.rollback()
            failure = error
        else:
            failure = None
        if isinstance(failure, Exception):
            raise MetadataReportError("review preparation persistence failed") from None
        if failure is not None:
            raise failure from None

    def drain_library_additions(
        self,
        libraries: tuple[tuple[str, str, str, str], ...],
        updated_at: int,
    ) -> None:
        configured = self._validated_library_scope(libraries, updated_at)
        transaction = self._transaction("drain_library_additions")
        failure: BaseException | None
        try:
            transaction.begin()
            self._drain_library_additions(configured, updated_at, clear_pause=False)
            transaction.commit()
        except BaseException as error:
            transaction.rollback()
            failure = error
        else:
            failure = None
        if isinstance(failure, Exception):
            raise MetadataReportError("review preparation persistence failed") from None
        if failure is not None:
            raise failure from None

    @staticmethod
    def _validated_library_scope(
        libraries: tuple[tuple[str, str, str, str], ...], updated_at: int
    ) -> dict[tuple[str, str], tuple[str, str]]:
        if (
            type(libraries) is not tuple
            or len(libraries) > 128
            or not non_negative_integer(updated_at)
            or any(
                type(item) is not tuple
                or len(item) != 4
                or any(
                    type(value) is not str or not 1 <= len(value) <= 128
                    for value in item[:3]
                )
                or len(item[3]) != 64
                or not _preparation_fingerprint(item[3])
                for item in libraries
            )
        ):
            raise MetadataReportError("review preparation scope is invalid")
        if len({item[:2] for item in libraries}) != len(libraries):
            raise MetadataReportError("review preparation scope is invalid")
        return {(item[0], item[1]): item[2:] for item in libraries}

    def _drain_library_additions(
        self,
        configured: dict[tuple[str, str], tuple[str, str]],
        updated_at: int,
        *,
        clear_pause: bool,
    ) -> None:
        if not clear_pause:
            paused = self._connection.execute(
                "SELECT paused FROM review_preparation_control WHERE singleton=1"
            ).fetchone()
            if paused is None:
                raise MetadataReportError("review preparation control is unavailable")
            if bool(paused[0]):
                refresh_preparation_run_state(self._connection, updated_at)
                return
        deferred = self._connection.execute(
            "SELECT row_id,connection_name,library_name,ordinal,accepted_at "
            "FROM review_preparation_presentation "
            "WHERE row_kind='deferred_enrollment' ORDER BY ordinal,row_id"
        ).fetchall()
        for row_id, connection, library, _ordinal, _accepted_at in deferred:
            if (connection, library) not in configured:
                self._connection.execute(
                    "DELETE FROM review_preparation_presentation WHERE row_id=?",
                    (row_id,),
                )
        authorities = self._connection.execute(
            "SELECT row_id,row_kind,scan_id,cycle_id FROM review_preparation_presentation "
            "WHERE row_kind IN ('enrollment','deferred_enrollment') "
            "ORDER BY ordinal,row_id"
        ).fetchall()
        if (
            not authorities
            and self._connection.execute(
                "SELECT COUNT(*) FROM review_preparation_presentation WHERE row_kind='work'"
            ).fetchone()[0]
            == 0
        ):
            self._connection.execute("DELETE FROM review_preparation_presentation")
        else:
            for ordinal, (row_id, row_kind, scan_id, cycle_id) in enumerate(
                authorities
            ):
                self._connection.execute(
                    "UPDATE review_preparation_presentation SET ordinal=?,updated_at=? WHERE row_id=?",
                    (ordinal, updated_at, row_id),
                )
                if row_kind == "enrollment" and cycle_id is not None:
                    self._connection.execute(
                        "UPDATE review_preparation_presentation SET enrollment_ordinal=? "
                        "WHERE row_kind='work' AND scan_id=? AND cycle_id=?",
                        (ordinal, scan_id, cycle_id),
                    )
        if clear_pause:
            self._connection.execute(
                "UPDATE review_preparation_control SET paused=0,updated_at=? WHERE singleton=1",
                (updated_at,),
            )
        surviving = self._connection.execute(
            "SELECT connection_name,library_name,accepted_at FROM review_preparation_presentation "
            "WHERE row_kind='deferred_enrollment' ORDER BY ordinal,row_id"
        ).fetchall()
        for connection, library, accepted_at in surviving:
            library_id, fingerprint = configured[(connection, library)]
            self._request_review_scan_unpaused(
                connection,
                library,
                library_id,
                fingerprint,
                updated_at,
                first_boot=False,
                trigger=PreparationTrigger.LIBRARY_ADDED,
                promoting_deferred=True,
                presentation_accepted_at=accepted_at,
            )
        refresh_preparation_run_state(self._connection, updated_at)

    def recover_review_pipeline(
        self, producers: tuple[tuple[str, str, str, str], ...]
    ) -> None:
        transaction = self._transaction("recover_review_pipeline")
        try:
            transaction.begin()
            self._connection.execute(
                "UPDATE review_preparation_scans SET state='queued' WHERE state='discovering'"
            )
            self._connection.execute(
                "UPDATE review_preparation_work SET state='failed',failure_code='failed',"
                "failure_root=NULL "
                "WHERE state='running' AND attempt>=3"
            )
            self._connection.execute(
                "UPDATE review_preparation_work SET state='queued' "
                "WHERE state='running' AND attempt<3"
            )
            # A process cannot complete a check it abandoned. Preserve the
            # condition and return it to the owner-actionable state.
            self._connection.execute(
                "UPDATE review_preparation_connection_conditions "
                "SET state='paused' WHERE state='checking'"
            )
            configured_connections = tuple(
                sorted({connection for connection, *_ in producers})
            )
            if configured_connections:
                placeholders = ",".join("?" for _ in configured_connections)
                self._connection.execute(
                    "DELETE FROM review_preparation_connection_conditions "
                    f"WHERE connection_name NOT IN ({placeholders})",
                    configured_connections,
                )
            configured_scopes = {
                (connection, library) for connection, library, *_ in producers
            }
            active_scans = self._connection.execute(
                "SELECT scan_id,connection_name,library_name FROM review_preparation_scans "
                "WHERE state IN ('queued','discovering','processing')"
            ).fetchall()
            for scan_id, connection, library in active_scans:
                if (connection, library) in configured_scopes:
                    continue
                self._connection.execute(
                    "UPDATE review_preparation_work SET state='stale',failure_code='stale',"
                    "failure_root=NULL WHERE scan_id=? AND state IN ('queued','delayed','running')",
                    (scan_id,),
                )
                self._connection.execute(
                    "UPDATE review_preparation_scans SET state='completed_with_failures' "
                    "WHERE scan_id=? AND state IN ('queued','discovering','processing')",
                    (scan_id,),
                )
            for connection, library, library_id, producer_fingerprint in producers:
                row = self._connection.execute(
                    "SELECT scan_id,state,library_id,producer_fingerprint "
                    "FROM review_preparation_scans "
                    "WHERE connection_name=? AND library_name=? "
                    "ORDER BY requested_at DESC,scan_id DESC LIMIT 1",
                    (connection, library),
                ).fetchone()
                if (
                    row is not None
                    and row[1] in {"queued", "discovering", "processing"}
                    and (row[2], row[3]) != (library_id, producer_fingerprint)
                ):
                    mark_preparation_recovery(self._connection, cast(str, row[0]))
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state='queued',"
                        "library_id=?,producer_fingerprint=?,catalogue_generation=NULL,"
                        "catalogue_fingerprint=NULL,qualification_epoch=NULL,total=0,refresh_count=0,"
                        "rescan_requested=0 "
                        "WHERE scan_id=?",
                        (library_id, producer_fingerprint, row[0]),
                    )
                    self._connection.execute(
                        "UPDATE review_preparation_work SET stage='extraction',"
                        "state='queued',attempt=0,recovery_attempt=0,library_id=?,producer_fingerprint=?,"
                        "report_id=NULL,recovery_source_report_id=NULL,"
                        "proposal_set_id=NULL,review_case_id=NULL,failure_code=NULL,"
                        "failure_root=NULL "
                        "WHERE scan_id=?",
                        (library_id, producer_fingerprint, row[0]),
                    )
            latest = self._connection.execute(
                "SELECT MAX(updated_at) FROM ("
                "SELECT COALESCE(MAX(updated_at),0) AS updated_at FROM review_preparation_scans "
                "UNION ALL SELECT COALESCE(MAX(updated_at),0) FROM review_preparation_work)"
            ).fetchone()[0]
            refresh_preparation_presentation(self._connection, latest)
            transaction.commit()
        except BaseException as error:
            transaction.rollback()
            if isinstance(error, sqlite3.Error):
                sqlite_failure = True
            else:
                raise
        else:
            sqlite_failure = False
        if sqlite_failure:
            raise MetadataReportError("review preparation recovery failed") from None

    def target_connection_conditions(self) -> tuple[TargetConnectionCondition, ...]:
        try:
            rows = self._connection.execute(
                "SELECT connection_name,code,state,epoch,affected,updated_at "
                "FROM review_preparation_connection_conditions "
                "ORDER BY connection_name"
            ).fetchall()
            return tuple(TargetConnectionCondition(*row) for row in rows)
        except (sqlite3.Error, TypeError, ValueError) as error:
            raise MetadataReportError(
                "target connection condition is unavailable"
            ) from error

    def guard_target_connection(self, action: PipelineAction, checked_at: int) -> bool:
        """Return a claimed action to queue when its connection is held."""
        transaction = self._transaction("guard_target_connection")
        try:
            transaction.begin()
            condition = self._connection.execute(
                "SELECT state FROM review_preparation_connection_conditions "
                "WHERE connection_name=?",
                (action.connection,),
            ).fetchone()
            if condition is None or condition[0] not in {"paused", "checking"}:
                transaction.commit()
                return True
            if action.stage == "discovery":
                updated = self._connection.execute(
                    "UPDATE review_preparation_scans SET state='queued',updated_at=? "
                    "WHERE scan_id=? AND state='discovering'",
                    (checked_at, action.scan_id),
                ).rowcount
            else:
                updated = self._connection.execute(
                    "UPDATE review_preparation_work SET state='queued',attempt=0,updated_at=? "
                    "WHERE work_id=? AND stage=? AND state='running' AND attempt=?",
                    (checked_at, action.action_id, action.stage, action.attempt),
                ).rowcount
            if updated != 1:
                raise MetadataReportError("review preparation state changed")
            transaction.commit()
            return False
        except (sqlite3.Error, MetadataReportError) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(
                "target connection condition is unavailable"
            ) from error

    def discovery_requires_qualification(self, action: PipelineAction) -> bool:
        if action.stage != "discovery":
            raise MetadataReportError("review preparation state changed")
        try:
            row = self._connection.execute(
                "SELECT qualification_epoch FROM review_preparation_scans "
                "WHERE scan_id=? AND state='discovering'",
                (action.scan_id,),
            ).fetchone()
            if row is None:
                raise MetadataReportError("review preparation state changed")
            return row[0] is None
        except sqlite3.Error as error:
            raise MetadataReportError(
                "target connection condition is unavailable"
            ) from error

    def qualify_review_discovery(
        self, action: PipelineAction, qualified_at: int
    ) -> bool:
        """Persist successful preflight authority before target enumeration."""
        if action.stage != "discovery":
            raise MetadataReportError("review preparation state changed")
        transaction = self._transaction("qualify_review_discovery")
        try:
            transaction.begin()
            condition = self._connection.execute(
                "SELECT state,epoch FROM review_preparation_connection_conditions "
                "WHERE connection_name=?",
                (action.connection,),
            ).fetchone()
            if condition is not None and condition[0] in {"paused", "checking"}:
                updated = self._connection.execute(
                    "UPDATE review_preparation_scans SET state='queued',updated_at=? "
                    "WHERE scan_id=? AND state='discovering'",
                    (qualified_at, action.scan_id),
                ).rowcount
                if updated != 1:
                    raise MetadataReportError("review preparation state changed")
                transaction.commit()
                return False
            epoch = 0 if condition is None else condition[1]
            updated = self._connection.execute(
                "UPDATE review_preparation_scans SET qualification_epoch=?,updated_at=? "
                "WHERE scan_id=? AND state='discovering' AND qualification_epoch IS NULL",
                (epoch, qualified_at, action.scan_id),
            ).rowcount
            if updated != 1:
                raise MetadataReportError("review preparation state changed")
            transaction.commit()
            return True
        except (sqlite3.Error, MetadataReportError) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(
                "target connection condition is unavailable"
            ) from error

    def defer_for_target_connection(
        self, action: PipelineAction, code: str, updated_at: int
    ) -> None:
        if code not in {"tls", "authentication", "authorization", "unavailable"}:
            raise MetadataReportError("target connection condition is invalid")
        transaction = self._transaction("defer_for_target_connection")
        try:
            transaction.begin()
            current = self._connection.execute(
                "SELECT epoch,state FROM review_preparation_connection_conditions "
                "WHERE connection_name=?",
                (action.connection,),
            ).fetchone()
            epoch = 1 if current is None else current[0] + (current[1] != "paused")
            self._connection.execute(
                "INSERT INTO review_preparation_connection_conditions("
                "connection_name,code,state,epoch,affected,updated_at) VALUES(?,?,'paused',?,?,?) "
                "ON CONFLICT(connection_name) DO UPDATE SET code=excluded.code,state='paused',"
                "epoch=excluded.epoch,affected=excluded.affected,updated_at=excluded.updated_at",
                (action.connection, code, epoch, 0, updated_at),
            )
            if action.stage == "discovery":
                updated = self._connection.execute(
                    "UPDATE review_preparation_scans SET state='queued',qualification_epoch=NULL,updated_at=? "
                    "WHERE scan_id=? AND state='discovering'",
                    (updated_at, action.scan_id),
                ).rowcount
            else:
                updated = self._connection.execute(
                    "UPDATE review_preparation_work SET state='queued',attempt=0,updated_at=? "
                    "WHERE work_id=? AND stage=? AND state='running' AND attempt=?",
                    (updated_at, action.action_id, action.stage, action.attempt),
                ).rowcount
            if updated != 1:
                raise MetadataReportError("review preparation state changed")
            affected = self._target_connection_affected(action.connection)
            self._connection.execute(
                "UPDATE review_preparation_connection_conditions SET affected=? "
                "WHERE connection_name=?",
                (affected, action.connection),
            )
            transaction.commit()
        except (sqlite3.Error, MetadataReportError) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(
                "target connection condition is unavailable"
            ) from error

    def hold_target_connection(
        self, connection: str, code: str, updated_at: int
    ) -> None:
        """Persist a connection-wide hold without changing a retry checkpoint."""
        if code not in {"tls", "authentication", "authorization", "unavailable"}:
            raise MetadataReportError("target connection condition is invalid")
        transaction = self._transaction("hold_target_connection")
        try:
            transaction.begin()
            current = self._connection.execute(
                "SELECT epoch,state FROM review_preparation_connection_conditions "
                "WHERE connection_name=?",
                (connection,),
            ).fetchone()
            epoch = 1 if current is None else current[0] + (current[1] != "paused")
            self._connection.execute(
                "INSERT INTO review_preparation_connection_conditions("
                "connection_name,code,state,epoch,affected,updated_at) VALUES(?,?,'paused',?,?,?) "
                "ON CONFLICT(connection_name) DO UPDATE SET code=excluded.code,state='paused',"
                "epoch=excluded.epoch,affected=excluded.affected,updated_at=excluded.updated_at",
                (
                    connection,
                    code,
                    epoch,
                    self._target_connection_affected(connection),
                    updated_at,
                ),
            )
            refresh_preparation_run_state(self._connection, updated_at)
            transaction.commit()
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError(
                "target connection condition is unavailable"
            ) from error

    def _target_connection_affected(self, connection: str) -> int:
        # The projection is a sum of two COUNT(*) sub-selects, always one int.
        return cast(
            int,
            self._connection.execute(
                "SELECT (SELECT count(*) FROM review_preparation_scans scan "
                "WHERE scan.connection_name=? AND scan.state IN ('queued','discovering','processing') "
                "AND NOT EXISTS(SELECT 1 FROM review_preparation_work work "
                "WHERE work.scan_id=scan.scan_id AND work.state IN ('queued','delayed','running')))+"
                "(SELECT count(*) FROM review_preparation_work "
                "WHERE connection_name=? AND state IN ('queued','delayed','running'))",
                (connection, connection),
            ).fetchone()[0],
        )

    def _refresh_target_connection_affected(self, connection: str) -> None:
        """Keep an existing hold truthful as newly admitted work arrives."""
        self._connection.execute(
            "UPDATE review_preparation_connection_conditions SET affected=? "
            "WHERE connection_name=? AND state IN ('paused','checking')",
            (self._target_connection_affected(connection), connection),
        )

    def begin_target_connection_check(
        self, connection: str, checked_at: int
    ) -> TargetConnectionCondition | None:
        transaction = self._transaction("begin_target_connection_check")
        try:
            transaction.begin()
            row = self._connection.execute(
                "SELECT code,state,epoch,affected,updated_at FROM review_preparation_connection_conditions "
                "WHERE connection_name=?",
                (connection,),
            ).fetchone()
            if row is None or row[1] != "paused":
                transaction.commit()
                return None
            epoch = row[2] + 1
            if (
                self._connection.execute(
                    "UPDATE review_preparation_connection_conditions SET state='checking',epoch=?,updated_at=? "
                    "WHERE connection_name=? AND state='paused' AND epoch=?",
                    (epoch, checked_at, connection, row[2]),
                ).rowcount
                != 1
            ):
                raise MetadataReportError("target connection check changed")
            transaction.commit()
            return TargetConnectionCondition(
                connection, row[0], "checking", epoch, row[3], checked_at
            )
        except (sqlite3.Error, MetadataReportError) as error:
            transaction.rollback()
            if isinstance(error, MetadataReportError):
                raise
            raise MetadataReportError(
                "target connection condition is unavailable"
            ) from error

    def finish_target_connection_check(
        self, connection: str, epoch: int, code: str | None, checked_at: int
    ) -> bool:
        if code is not None and code not in {
            "tls",
            "authentication",
            "authorization",
            "unavailable",
        }:
            raise MetadataReportError("target connection condition is invalid")
        transaction = self._transaction("finish_target_connection_check")
        try:
            transaction.begin()
            affected = self._target_connection_affected(connection)
            updated = self._connection.execute(
                "UPDATE review_preparation_connection_conditions SET state=?,code=COALESCE(?,code),"
                "affected=?,updated_at=? WHERE connection_name=? AND state='checking' AND epoch=?",
                (
                    "recovered" if code is None else "paused",
                    code,
                    affected,
                    checked_at,
                    connection,
                    epoch,
                ),
            ).rowcount
            if updated == 1 and code is None:
                self._connection.execute(
                    "UPDATE review_preparation_scans SET qualification_epoch=?,updated_at=? "
                    "WHERE connection_name=? AND state='queued' "
                    "AND qualification_epoch IS NULL",
                    (epoch, checked_at, connection),
                )
            transaction.commit()
            return updated == 1
        except sqlite3.Error as error:
            transaction.rollback()
            raise MetadataReportError(
                "target connection condition is unavailable"
            ) from error
