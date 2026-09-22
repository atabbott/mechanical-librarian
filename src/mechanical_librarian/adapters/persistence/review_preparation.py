"""The pipeline-action lifecycle half of review preparation.

Owns `review_preparation_work` -- the one row per stage of one scan that the
pipeline claims, advances, completes and fails -- and
`review_preparation_absences`, the record of targets a discovery saw and a
later stage did not. It is the half that runs preparation; the half that
decides whether preparation may run at all owns the scans, the pause control
and the target-connection conditions and lives in `review_scans.py`. Measured
across all thirty-three preparation methods, the two halves call each other in
neither direction.

`_review_pipeline_claim` moves here with the two methods that write and take
it: it is a claim over this module's work rows and nothing else observes it.

Five seams cross the boundary and each is injected as a lambda through the
composing repository rather than as a bound method, which is load-bearing
rather than stylistic. `test_failed_action_requeues_a_pending_manual_return`
and two siblings substitute `_has_pending_field_effect_manual_return` on the
repository and then drive `claim_review_pipeline_action` and
`fail_review_pipeline_action`. Resolving the attribute at call time keeps the
substitution reachable; capturing the bound method at construction would leave
those tests passing while testing nothing. The same holds when the field-effect
aggregate moves, as long as its delegating stub stays on the repository.

- `_has_pending_field_effect_manual_return` reads the field-effect holds that
  requeue a failed action instead of retiring it.
- `_require_staged_shadow_projection_current` re-checks the claimed staged case
  before package-internal projection.
- `_refresh_metadata_policy_transition` settles the policy transition an
  advancing or failing action belongs to. Policy owns that write.
- `_enqueue_provider_recovery_outbox` records the package cutover a completing
  action produces, inside this module's transaction. The outbox aggregate owns
  that table and its `enqueue` is deliberately scope-free for exactly this.
- `_restart_metadata_policy_work` is not injected: it writes
  `review_preparation_work`, so under the ownership rule it belongs here, and
  the metadata-policy aggregate has always received it as an injected callable
  from the composing repository. That injection now resolves to this module.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import sys
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import cast

from mechanical_librarian.adapters.persistence.binding import _LinuxDatabaseBinding
from mechanical_librarian.adapters.persistence.preparation_presentation import (
    record_preparation_names,
    refresh_preparation_presentation,
    refresh_preparation_run_state,
    refresh_preparation_work,
)
from mechanical_librarian.adapters.persistence.transactions import (
    ConnectionScoped,
    LocalOperationLease,
    _revoke_connection,
    _unwind_outer,
    local_operation,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.review_pipeline import PipelineAction
from mechanical_librarian.domain.audio_inspection import MAX_AUDIO_TRACKS
from mechanical_librarian.domain.metadata_report import MetadataReport
from mechanical_librarian.domain.review_case import (
    ReviewAdmissionStaleError,
    ReviewCase,
    ReviewCaseValueError,
    ReviewTransition,
)

# The exact vocabulary `review_preparation_work.failure_code` accepts. It
# duplicates the CHECK in `schema.py`, which cannot be generated from this set
# because `sqlite_master` stores that DDL verbatim; the two are held equal by
# `test_the_failure_code_allow_list_and_its_normalisation_stay_one_vocabulary`,
# so a code added to one and not the other fails the gate instead of silently
# collapsing to `failed` (#734).
_FAILURE_CODES = frozenset(
    (
        "provider",
        "configuration",
        "authentication",
        "stale",
        "transient",
        "projection",
        "failed",
        "currentness",
        "persistence",
        "unsupported_media",
        "unreadable_media",
        "parser_refused",
        "multi_file_budget_exceeded",
        "missing_required_input",
        "malformed_retained_data",
        "internal_failure",
        "unknown",
    )
)
# The failure codes a track ordinal is evidence for: exactly the media
# terminals one file of a composed audiobook can end a whole item on. A root
# under any of them names the refusing file and nothing else, which is what
# makes "a file could not be read" actionable across a hundred of them.
_TRACK_ROOT_CODES = frozenset(
    (
        "unsupported_media",
        "unreadable_media",
        "parser_refused",
        "multi_file_budget_exceeded",
    )
)
# The value-free track grammar the CHECK in `schema.py` admits beside it: an
# ordinal, never a name, so nothing about the media itself is persisted here.
_TRACK_ROOT = re.compile(r"track_([1-9]\d{0,2})")


def _admits_track_root(code: str, value: object) -> bool:
    """Say whether one refusing track's ordinal belongs on this failure.

    The ordinal is admitted only for a track the composition bound could have
    produced, so a value past `MAX_AUDIO_TRACKS` is a defect in the caller
    rather than evidence and is dropped instead of persisted.
    """
    if code not in _TRACK_ROOT_CODES or type(value) is not str:
        return False
    matched = _TRACK_ROOT.fullmatch(value)
    return matched is not None and int(matched.group(1)) <= MAX_AUDIO_TRACKS


class SQLiteReviewPreparationRepository(ConnectionScoped):
    """Claim, advance, complete and fail the work of one review scan."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        binding: _LinuxDatabaseBinding | None = None,
        *,
        has_pending_field_effect_manual_return: Callable[[str], bool],
        require_staged_shadow_projection_current: Callable[[str, PipelineAction], None],
        refresh_metadata_policy_transition: Callable[[str, int], None],
        enqueue_provider_recovery_outbox: Callable[
            [str, str, str, tuple[str | None, str, str | None, int | None, str], int],
            None,
        ],
    ) -> None:
        super().__init__(connection, binding)
        self._has_pending_field_effect_manual_return = (
            has_pending_field_effect_manual_return
        )
        self._require_staged_shadow_projection_current = (
            require_staged_shadow_projection_current
        )
        self._refresh_metadata_policy_transition = refresh_metadata_policy_transition
        self._enqueue_provider_recovery_outbox = enqueue_provider_recovery_outbox
        self._review_pipeline_claim: PipelineAction | None = None
        self._claim_owner: Callable[[PipelineAction | None], None] | None = None
        self._pending_claim: tuple[PipelineAction | None] | None = None
        self._publication_guard: Callable[[], AbstractContextManager[bool]] | None = (
            None
        )
        self._publication_admitted = False
        self._output_lease: LocalOperationLease | None = None

    def bind_review_pipeline_owner(
        self,
        owner: Callable[[PipelineAction | None], None] | None,
        publication_guard: Callable[[], AbstractContextManager[bool]] | None = None,
    ) -> None:
        """Bind one executing lane, never a subscriber or a durable authority."""
        self._claim_owner = owner
        self._publication_guard = publication_guard
        self._pending_claim = None

    @contextmanager
    def publication_claim_guard(self) -> Iterator[bool]:
        """Scope successor admission to the report's writer transaction only."""
        if self._publication_guard is None:
            yield False
            return
        try:
            with self._publication_guard() as admitted:
                self._publication_admitted = admitted
                yield admitted
        finally:
            self._publication_admitted = False
            self._pending_claim = None

    def commit_review_pipeline_claim(self) -> None:
        """Transfer the prepared receipt before post-commit reads or releases."""
        pending = self._pending_claim
        self._pending_claim = None
        if pending is not None and self._claim_owner is not None:
            self._claim_owner(pending[0])

    def release_review_pipeline_claim(self) -> None:
        """A committed connection hold returns the exact claim to its queue."""
        self._pending_claim = None
        if self._claim_owner is not None:
            self._claim_owner(None)

    @local_operation
    def claim_review_pipeline_action(self, claimed_at: int) -> PipelineAction | None:
        transaction = self._transaction("claim_review_pipeline_action")
        try:
            transaction.begin()
            active = self._connection.execute(
                "SELECT (SELECT COUNT(*) FROM review_preparation_scans WHERE state='discovering')+"
                "(SELECT COUNT(*) FROM review_preparation_work WHERE state='running')"
            ).fetchone()[0]
            if active >= 2:
                transaction.commit()
                return None
            control = self._connection.execute(
                "SELECT claim_sequence,last_library,circuit_open_until "
                "FROM review_preparation_control WHERE singleton=1"
            ).fetchone()
            if control is None:
                raise MetadataReportError("review preparation control is unavailable")
            if control[1] is not None and (
                type(control[1]) is not str
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", control[1]) is None
            ):
                raise MetadataReportError("review preparation control is invalid")
            # Promote due work before the eligible library/ordinal claim. The
            # due index bounds this transition even when a cohort begins with
            # many delayed rows.
            self._connection.execute(
                "UPDATE review_preparation_work SET state='queued',updated_at=? "
                "WHERE state='delayed' AND retry_not_before<=?",
                (claimed_at, claimed_at),
            )
            scan = self._connection.execute(
                "SELECT scan_id,connection_name,library_name,library_id,producer_fingerprint "
                "FROM review_preparation_scans WHERE state='queued' "
                "AND NOT EXISTS(SELECT 1 FROM review_preparation_connection_conditions c "
                "WHERE c.connection_name=review_preparation_scans.connection_name "
                "AND c.state IN ('paused','checking')) "
                "ORDER BY requested_at,scan_id LIMIT 1"
            ).fetchone()
            if scan is not None:
                self._connection.execute(
                    "UPDATE review_preparation_scans SET state='discovering',updated_at=? "
                    "WHERE scan_id=? AND state='queued'",
                    (claimed_at, scan[0]),
                )
                refresh_preparation_run_state(self._connection, claimed_at)
                action = PipelineAction(
                    scan[0],
                    scan[0],
                    "discovery",
                    1,
                    scan[1],
                    scan[2],
                    scan[3],
                    scan[4],
                )
                self._pending_claim = (action,)
                transaction.commit(after_commit=self.commit_review_pipeline_claim)
                return action
            library = self._connection.execute(
                "SELECT library_name FROM review_preparation_work WHERE state='queued' "
                "AND NOT EXISTS(SELECT 1 FROM review_preparation_connection_conditions c "
                "WHERE c.connection_name=review_preparation_work.connection_name "
                "AND c.state IN ('paused','checking')) "
                "AND EXISTS(SELECT 1 FROM review_preparation_scans s "
                "WHERE s.scan_id=review_preparation_work.scan_id "
                "AND s.state='processing') "
                "GROUP BY library_name "
                "ORDER BY CASE WHEN library_name>? THEN 0 ELSE 1 END,library_name "
                "LIMIT 1",
                (control[1] or "",),
            ).fetchone()
            # `ordinal` is the persisted discovery order for this cohort. A
            # stage transition deliberately changes `updated_at`, so using it
            # here makes an early work fall behind work still waiting at the
            # preceding stage and turns first-ready latency into a full-stage
            # drain. Ineligible rows are excluded above; every eligible
            # successor therefore keeps its cohort position across stages.
            ordering = "ordinal"
            work = (
                None
                if library is None
                else self._connection.execute(
                    "SELECT work_id,scan_id,stage,attempt,connection_name,library_name,library_id,"
                    "producer_fingerprint,catalogue_generation,catalogue_fingerprint,"
                    "item_id,file_identity,source_revision,"
                    "report_id,proposal_set_id,review_case_id,source_fingerprint,"
                    "(SELECT checkpoint FROM current_review_case_lineages lineage "
                    "WHERE lineage.connection_name=review_preparation_work.connection_name "
                    "AND lineage.library_name=review_preparation_work.library_name "
                    "AND lineage.item_id=review_preparation_work.item_id "
                    "AND lineage.proposal_set_id=review_preparation_work.proposal_set_id "
                    "AND lineage.report_id=review_preparation_work.report_id "
                    "AND lineage.review_case_id=review_preparation_work.review_case_id) "
                    ",(SELECT generation FROM metadata_policy_state WHERE singleton=1) "
                    "FROM review_preparation_work WHERE state='queued' AND library_name=? "
                    "AND NOT EXISTS(SELECT 1 FROM review_preparation_connection_conditions c "
                    "WHERE c.connection_name=review_preparation_work.connection_name "
                    "AND c.state IN ('paused','checking')) "
                    "AND EXISTS(SELECT 1 FROM review_preparation_scans s "
                    "WHERE s.scan_id=review_preparation_work.scan_id "
                    "AND s.state='processing') "
                    f"ORDER BY {ordering} LIMIT 1",
                    (library[0],),
                ).fetchone()
            )
            if work is not None:
                attempt = work[3] + 1
                updated = self._connection.execute(
                    "UPDATE review_preparation_work SET state='running',attempt=?,updated_at=? "
                    "WHERE work_id=? AND state='queued'",
                    (attempt, claimed_at, work[0]),
                ).rowcount
                if updated != 1:
                    raise MetadataReportError("review preparation claim changed")
                self._connection.execute(
                    "UPDATE review_preparation_control SET claim_sequence=claim_sequence+1,"
                    "last_library=?,updated_at=? WHERE singleton=1",
                    (work[5], claimed_at),
                )
                refresh_preparation_work(self._connection, work[0], claimed_at)
                action = PipelineAction(
                    work[0],
                    work[1],
                    work[2],
                    attempt,
                    work[4],
                    work[5],
                    work[6],
                    work[7],
                    work[8],
                    work[9],
                    work[10],
                    work[11],
                    work[12],
                    work[13],
                    work[14],
                    work[15],
                    work[16],
                    work[17],
                    work[18],
                )
                self._pending_claim = (action,)
                transaction.commit(after_commit=self.commit_review_pipeline_claim)
                return action
            scans = self._connection.execute(
                "SELECT scan_id,rescan_requested FROM review_preparation_scans "
                "WHERE state='processing' AND NOT EXISTS("
                "SELECT 1 FROM review_preparation_work w "
                "WHERE w.scan_id=review_preparation_scans.scan_id "
                "AND w.state IN ('queued','delayed','running'))"
            ).fetchall()
            for scan_id, rescan_requested in scans:
                pending_manual_return = self._has_pending_field_effect_manual_return(
                    scan_id
                )
                failures = self._connection.execute(
                    "SELECT COUNT(*) FROM review_preparation_work "
                    "WHERE scan_id=? AND state='failed'",
                    (scan_id,),
                ).fetchone()[0]
                stale = self._connection.execute(
                    "SELECT COUNT(*) FROM review_preparation_work "
                    "WHERE scan_id=? AND state='stale'",
                    (scan_id,),
                ).fetchone()[0]
                if rescan_requested and pending_manual_return:
                    if failures or stale:
                        self._connection.execute(
                            "DELETE FROM review_preparation_absences WHERE scan_id=?",
                            (scan_id,),
                        )
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state='queued',"
                        "catalogue_generation=NULL,catalogue_fingerprint=NULL,total=0,"
                        "qualification_epoch=NULL,refresh_count=0,rescan_requested=0,updated_at=? "
                        "WHERE scan_id=?",
                        (claimed_at, scan_id),
                    )
                elif failures:
                    self._connection.execute(
                        "DELETE FROM review_preparation_absences WHERE scan_id=?",
                        (scan_id,),
                    )
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state='completed_with_failures',"
                        "rescan_requested=0,updated_at=? WHERE scan_id=?",
                        (claimed_at, scan_id),
                    )
                elif rescan_requested:
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state='queued',"
                        "catalogue_generation=NULL,catalogue_fingerprint=NULL,total=0,"
                        "qualification_epoch=NULL,rescan_requested=0,updated_at=? WHERE scan_id=?",
                        (claimed_at, scan_id),
                    )
                else:
                    state = "completed_with_failures" if stale else "completed"
                    if stale:
                        self._connection.execute(
                            "DELETE FROM review_preparation_absences WHERE scan_id=?",
                            (scan_id,),
                        )
                    else:
                        self._commit_review_absences(scan_id)
                    self._connection.execute(
                        "UPDATE review_preparation_scans SET state=?,updated_at=? WHERE scan_id=?",
                        (state, claimed_at, scan_id),
                    )
            refresh_preparation_run_state(self._connection, claimed_at)
            transaction.commit()
            return None
        except BaseException as error:
            transaction.rollback()
            if not isinstance(error, Exception):
                interruption = error
            elif isinstance(error, MetadataReportError):
                raise
            elif isinstance(error, sqlite3.Error):
                raise MetadataReportError("review preparation claim failed") from error
            else:
                raise
        raise interruption from None

    @local_operation
    def claim_review_pipeline_work(
        self, work_id: str, claimed_at: int
    ) -> PipelineAction | None:
        """Claim one named queued successor without bypassing stage handling."""
        if type(work_id) is not str or type(claimed_at) is not int or claimed_at < 0:
            raise MetadataReportError("review preparation state is invalid")
        transaction = self._transaction("claim_review_pipeline_work")
        try:
            transaction.begin()
            self._connection.execute(
                "UPDATE review_preparation_work SET state='queued',updated_at=? "
                "WHERE work_id=? AND state='delayed' AND retry_not_before<=?",
                (claimed_at, work_id, claimed_at),
            )
            work = self._connection.execute(
                "SELECT work_id,scan_id,stage,attempt,connection_name,library_name,library_id,"
                "producer_fingerprint,catalogue_generation,catalogue_fingerprint,item_id,"
                "file_identity,source_revision,report_id,proposal_set_id,review_case_id,"
                "source_fingerprint,(SELECT checkpoint FROM current_review_case_lineages lineage "
                "WHERE lineage.connection_name=review_preparation_work.connection_name "
                "AND lineage.library_name=review_preparation_work.library_name "
                "AND lineage.item_id=review_preparation_work.item_id "
                "AND lineage.proposal_set_id=review_preparation_work.proposal_set_id "
                "AND lineage.report_id=review_preparation_work.report_id "
                "AND lineage.review_case_id=review_preparation_work.review_case_id),"
                "(SELECT generation FROM metadata_policy_state WHERE singleton=1) "
                "FROM review_preparation_work WHERE work_id=? AND state='queued' "
                "AND EXISTS(SELECT 1 FROM review_preparation_scans s "
                "WHERE s.scan_id=review_preparation_work.scan_id AND s.state='processing')",
                (work_id,),
            ).fetchone()
            if work is None:
                transaction.commit()
                return None
            attempt = work[3] + 1
            if (
                self._connection.execute(
                    "UPDATE review_preparation_work SET state='running',attempt=?,updated_at=? "
                    "WHERE work_id=? AND state='queued'",
                    (attempt, claimed_at, work_id),
                ).rowcount
                != 1
            ):
                raise MetadataReportError("review preparation claim changed")
            refresh_preparation_work(self._connection, work_id, claimed_at)
            action = PipelineAction(
                work[0],
                work[1],
                work[2],
                attempt,
                work[4],
                work[5],
                work[6],
                work[7],
                work[8],
                work[9],
                work[10],
                work[11],
                work[12],
                work[13],
                work[14],
                work[15],
                work[16],
                work[17],
                work[18],
            )
            self._pending_claim = (action,)
            transaction.commit(after_commit=self.commit_review_pipeline_claim)
            return action
        except BaseException as error:
            transaction.rollback()
            if not isinstance(error, Exception):
                interruption = error
            elif isinstance(error, MetadataReportError):
                raise
            elif isinstance(error, sqlite3.Error):
                raise MetadataReportError("review preparation claim failed") from error
            else:
                raise
        raise interruption from None

    @local_operation
    def stage_provider_retry_successor(
        self, source_report_id: str, report: MetadataReport, staged_at: int
    ) -> str:
        """Attach a retry report to its existing work before publication commits."""
        if (
            not self._connection.in_transaction
            or type(source_report_id) is not str
            or not source_report_id
            or type(report) is not MetadataReport
            or type(staged_at) is not int
            or staged_at < 0
        ):
            raise MetadataReportError("provider retry staging is invalid")
        row = self._connection.execute(
            "SELECT work_id,scan_id FROM review_preparation_work WHERE connection_name=? "
            "AND library_name=? AND item_id=? AND file_identity=? "
            "AND report_id=? AND state IN ('complete','failed','stale')",
            (
                report.target.connection,
                report.target.library,
                report.target.item,
                report.file.identity,
                source_report_id,
            ),
        ).fetchone()
        if row is None:
            raise MetadataReportError("provider retry staging is stale")
        if (
            self._connection.execute(
                "UPDATE review_preparation_work SET stage='provider',state='queued',attempt=0,"
                "recovery_attempt=0,retry_not_before=0,report_id=?,proposal_set_id=NULL,"
                "review_case_id=NULL,recovery_source_report_id=?,failure_code=NULL,failure_root=NULL,"
                "outcome=NULL,updated_at=? "
                "WHERE work_id=? AND state IN ('complete','failed','stale')",
                (report.report_id, source_report_id, staged_at, row[0]),
            ).rowcount
            != 1
        ):
            raise MetadataReportError("provider retry staging changed")
        self._connection.execute(
            "UPDATE review_preparation_scans SET state='processing',updated_at=? "
            "WHERE scan_id=? AND state IN ('completed','completed_with_failures')",
            (staged_at, row[1]),
        )
        return cast(str, row[0])

    @local_operation
    def provider_retry_work_id(self, report_id: str) -> str:
        try:
            row = self._connection.execute(
                "SELECT work_id FROM review_preparation_work WHERE report_id=? "
                "AND stage IN ('provider','proposal','admission','projection','complete')",
                (report_id,),
            ).fetchone()
        except sqlite3.Error as error:
            raise MetadataReportError(
                "provider retry staging is unavailable"
            ) from error
        if row is None:
            raise MetadataReportError("provider retry staging is stale")
        return cast(str, row[0])

    @local_operation
    def provider_retry_complete(self, report_id: str) -> bool:
        try:
            row = self._connection.execute(
                "SELECT 1 FROM review_preparation_work w WHERE w.report_id=? "
                "AND w.stage='complete' AND w.state='complete' AND EXISTS("
                "SELECT 1 FROM current_review_packages p WHERE p.connection_name=w.connection_name "
                "AND p.library_name=w.library_name AND p.item_id=w.item_id AND p.report_id=w.report_id)",
                (report_id,),
            ).fetchone()
        except sqlite3.Error as error:
            raise MetadataReportError(
                "provider retry staging is unavailable"
            ) from error
        return row is not None

    @local_operation
    def completed_provider_retry_successor(self, source_report_id: str) -> str | None:
        """Return only the exact staged successor that completed package cutover."""
        if type(source_report_id) is not str or not source_report_id:
            raise MetadataReportError("provider retry staging is invalid")
        try:
            rows = self._connection.execute(
                "SELECT w.report_id FROM review_preparation_work w "
                "JOIN current_review_packages p ON p.connection_name=w.connection_name "
                "AND p.library_name=w.library_name AND p.item_id=w.item_id "
                "AND p.report_id=w.report_id WHERE w.recovery_source_report_id=? "
                "AND w.stage='complete' AND w.state='complete' LIMIT 2",
                (source_report_id,),
            ).fetchall()
        except sqlite3.Error as error:
            raise MetadataReportError(
                "provider retry staging is unavailable"
            ) from error
        if len(rows) > 1:
            raise MetadataReportError("provider retry staging conflicts")
        return None if not rows else rows[0][0]

    @local_operation
    def complete_review_discovery(
        self,
        action: PipelineAction,
        generation: int,
        fingerprint: str,
        entries: tuple[CatalogueEntry, ...],
        completed_at: int,
    ) -> None:
        if action.stage != "discovery":
            raise MetadataReportError("review preparation state is invalid")
        selected = tuple(
            entry
            for entry in entries
            if entry.connection == action.connection
            and entry.library == action.library
            and entry.library_id == action.library_id
        )
        eligible = tuple(
            entry
            for entry in selected
            if entry.eligible and entry.file_identity is not None
        )
        present_items = {entry.item_id for entry in selected}
        eligible_items = {entry.item_id for entry in eligible}
        transaction = self._transaction("complete_review_discovery")
        try:
            transaction.begin()
            self._pending_claim = (None,)
            current = self._connection.execute(
                "SELECT state FROM review_preparation_scans WHERE scan_id=?",
                (action.scan_id,),
            ).fetchone()
            if current != ("discovering",):
                raise MetadataReportError("review preparation state changed")
            previous_retirements = self._connection.execute(
                "SELECT item_id,proposal_set_id,report_id,review_case_id,checkpoint,kind,reason "
                "FROM review_preparation_absences WHERE scan_id=? ORDER BY item_id",
                (action.scan_id,),
            ).fetchall()
            pointers = self._connection.execute(
                "SELECT item_id,proposal_set_id,report_id,review_case_id,checkpoint "
                "FROM current_review_case_lineages "
                "WHERE connection_name=? AND library_name=? AND review_case_id IS NOT NULL",
                (action.connection, action.library),
            ).fetchall()
            self._connection.execute(
                "DELETE FROM review_preparation_absences WHERE scan_id=?",
                (action.scan_id,),
            )
            for item_id, proposal_id, report_id, case_id, checkpoint in pointers:
                if item_id in eligible_items:
                    continue
                kind = "tombstoned" if item_id not in present_items else "retired"
                reason = "absent" if kind == "tombstoned" else "ineligible"
                self._connection.execute(
                    "INSERT INTO review_preparation_absences("
                    "scan_id,item_id,proposal_set_id,report_id,review_case_id,checkpoint,kind,reason"
                    ") VALUES(?,?,?,?,?,?,?,?)",
                    (
                        action.scan_id,
                        item_id,
                        proposal_id,
                        report_id,
                        case_id,
                        checkpoint,
                        kind,
                        reason,
                    ),
                )
            staged_retirements = self._connection.execute(
                "SELECT item_id,proposal_set_id,report_id,review_case_id,checkpoint,kind,reason "
                "FROM review_preparation_absences WHERE scan_id=? ORDER BY item_id",
                (action.scan_id,),
            ).fetchall()
            confirmation_required = (
                bool(staged_retirements) and not previous_retirements
            )
            if previous_retirements:
                confirmed = set(previous_retirements).intersection(staged_retirements)
                for candidate in staged_retirements:
                    if candidate not in confirmed:
                        self._connection.execute(
                            "DELETE FROM review_preparation_absences "
                            "WHERE scan_id=? AND item_id=?",
                            (action.scan_id, candidate[0]),
                        )
            for ordinal, entry in enumerate(
                sorted(eligible, key=lambda value: value.item_id)
            ):
                assert entry.file_identity is not None
                material = (
                    f"review-preparation-work-v1\0{action.connection}\0"
                    f"{action.library}\0{entry.item_id}"
                )
                work_id = (
                    "review_work_"
                    + hashlib.sha256(material.encode("utf-8")).hexdigest()
                )
                existing = self._connection.execute(
                    "SELECT file_identity,source_fingerprint,producer_fingerprint,"
                    "stage,state,report_id,proposal_set_id,review_case_id,outcome "
                    "FROM review_preparation_work WHERE work_id=?",
                    (work_id,),
                ).fetchone()
                stage = "extraction"
                state = "queued"
                report_id = proposal_set_id = review_case_id = None
                failure_code = failure_root = outcome = None
                if existing is not None and existing[:3] == (
                    entry.file_identity,
                    entry.source_fingerprint,
                    action.producer_fingerprint,
                ):
                    stage, previous_state = existing[3], existing[4]
                    report_id, proposal_set_id, review_case_id = existing[5:8]
                    previous_outcome = existing[8]
                    report_current = True
                    if report_id is not None:
                        report_current = self._connection.execute(
                            "SELECT report_id FROM current_metadata_reports "
                            "WHERE connection_name=? AND library_name=? AND item_id=? "
                            "AND file_identity=?",
                            (
                                action.connection,
                                action.library,
                                entry.item_id,
                                entry.file_identity,
                            ),
                        ).fetchone() == (report_id,)
                    if not report_current or previous_state == "stale":
                        stage = "extraction"
                        report_id = proposal_set_id = review_case_id = None
                    elif (
                        previous_state == "complete"
                        and previous_outcome == "evidence_only"
                    ):
                        # The file this outcome is terminal about has not moved:
                        # re-inspecting it would re-hash gigabytes to reach the
                        # same recorded evidence.
                        stage = "complete"
                        state = "complete"
                        outcome = previous_outcome
                    elif previous_state == "failed" and stage == "provider":
                        stage = "extraction"
                        report_id = proposal_set_id = review_case_id = None
                    elif previous_state == "complete":
                        pointer = self._connection.execute(
                            "SELECT proposal_set_id,report_id,review_case_id,outcome "
                            "FROM current_review_case_lineages WHERE connection_name=? "
                            "AND library_name=? AND item_id=?",
                            (action.connection, action.library, entry.item_id),
                        ).fetchone()
                        pointer_matches = (
                            pointer is not None
                            and pointer[:3]
                            == (proposal_set_id, report_id, review_case_id)
                            and (
                                pointer[3] in {"admitted", "superseded"}
                                if review_case_id is not None
                                else pointer[3] in {"no_case", "retired"}
                            )
                        )
                        pending_description = (
                            self._connection.execute(
                                "SELECT 1 FROM description_preparation_pending WHERE review_case_id=?",
                                (review_case_id,),
                            ).fetchone()
                            is not None
                        )
                        if pointer_matches and pending_description:
                            stage = "projection"
                            state = "queued"
                            outcome = None
                        elif pointer_matches:
                            stage = "complete"
                            state = "complete"
                            outcome = (
                                "case" if review_case_id is not None else "no_case"
                            )
                        else:
                            stage = "admission"
                            review_case_id = None
                self._connection.execute(
                    "INSERT INTO review_preparation_work("
                    "work_id,scan_id,ordinal,connection_name,library_name,library_id,"
                    "item_id,file_identity,source_revision,source_fingerprint,producer_fingerprint,"
                    "catalogue_generation,catalogue_fingerprint,stage,state,attempt,recovery_attempt,retry_not_before,"
                    "report_id,proposal_set_id,review_case_id,failure_code,failure_root,outcome,updated_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(work_id) DO UPDATE SET ordinal=excluded.ordinal,"
                    "library_id=excluded.library_id,file_identity=excluded.file_identity,"
                    "source_revision=excluded.source_revision,"
                    "source_fingerprint=excluded.source_fingerprint,"
                    "producer_fingerprint=excluded.producer_fingerprint,"
                    "catalogue_generation=excluded.catalogue_generation,"
                    "catalogue_fingerprint=excluded.catalogue_fingerprint,"
                    "stage=excluded.stage,state=excluded.state,attempt=0,recovery_attempt=0,retry_not_before=0,"
                    "report_id=excluded.report_id,proposal_set_id=excluded.proposal_set_id,"
                    "review_case_id=excluded.review_case_id,recovery_source_report_id="
                    "CASE WHEN excluded.report_id IS NULL THEN NULL "
                    "ELSE review_preparation_work.recovery_source_report_id END,"
                    "failure_code=excluded.failure_code,"
                    "failure_root=excluded.failure_root,"
                    "outcome=excluded.outcome,"
                    "updated_at=excluded.updated_at",
                    (
                        work_id,
                        action.scan_id,
                        ordinal,
                        action.connection,
                        action.library,
                        action.library_id,
                        entry.item_id,
                        entry.file_identity,
                        entry.updated_at,
                        entry.source_fingerprint,
                        action.producer_fingerprint,
                        generation,
                        fingerprint,
                        stage,
                        state,
                        0,
                        0,
                        0,
                        report_id,
                        proposal_set_id,
                        review_case_id,
                        failure_code,
                        failure_root,
                        outcome,
                        completed_at,
                    ),
                )
            for work_id, item_id in self._connection.execute(
                "SELECT work_id,item_id FROM review_preparation_work WHERE scan_id=?",
                (action.scan_id,),
            ).fetchall():
                if item_id not in eligible_items:
                    self._connection.execute(
                        "DELETE FROM review_preparation_work WHERE work_id=?",
                        (work_id,),
                    )
            self._connection.execute(
                "UPDATE review_preparation_scans SET state='processing',"
                "catalogue_generation=?,catalogue_fingerprint=?,total=?,"
                "refresh_count=refresh_count+1,rescan_requested="
                "CASE WHEN ? THEN 1 ELSE rescan_requested END,"
                "updated_at=? "
                "WHERE scan_id=?",
                (
                    generation,
                    fingerprint,
                    len(eligible),
                    confirmation_required,
                    completed_at,
                    action.scan_id,
                ),
            )
            record_preparation_names(
                self._connection, action.scan_id, selected, completed_at
            )
            refresh_preparation_presentation(self._connection, completed_at)
            transaction.commit(after_commit=self.commit_review_pipeline_claim)
            return
        except BaseException as error:
            transaction.rollback()
            if not isinstance(error, Exception):
                interruption = error
            elif isinstance(error, MetadataReportError):
                raise
            elif isinstance(error, (sqlite3.Error, ReviewCaseValueError)):
                raise MetadataReportError(
                    "review preparation discovery failed"
                ) from error
            else:
                raise
        raise interruption from None

    def _commit_review_absences(self, scan_id: str) -> None:
        scan = self._connection.execute(
            "SELECT connection_name,library_name FROM review_preparation_scans "
            "WHERE scan_id=? AND state='processing'",
            (scan_id,),
        ).fetchone()
        if scan is None:
            raise MetadataReportError("review preparation state changed")
        connection, library = scan
        candidates = self._connection.execute(
            "SELECT item_id,proposal_set_id,report_id,review_case_id,checkpoint,kind,reason "
            "FROM review_preparation_absences WHERE scan_id=? ORDER BY item_id",
            (scan_id,),
        ).fetchall()
        for (
            item_id,
            proposal_id,
            report_id,
            case_id,
            checkpoint,
            kind,
            reason,
        ) in candidates:
            current = self._connection.execute(
                "SELECT proposal_set_id,report_id,review_case_id,checkpoint "
                "FROM current_review_case_lineages WHERE connection_name=? "
                "AND library_name=? AND item_id=?",
                (connection, library, item_id),
            ).fetchone()
            if current != (proposal_id, report_id, case_id, checkpoint):
                continue
            published = self._connection.execute(
                "SELECT proposal_set_id,report_id,review_case_id,checkpoint "
                "FROM current_review_packages WHERE connection_name=? "
                "AND library_name=? AND item_id=?",
                (connection, library, item_id),
            ).fetchone()
            transition = ReviewTransition(
                connection,
                library,
                item_id,
                checkpoint + 1,
                proposal_id,
                report_id,
                kind,
                case_id,
                None,
                reason,
                "review-preparation-v1",
            )
            self._connection.execute(
                "INSERT INTO review_case_transitions("
                "transition_id,connection_name,library_name,item_id,checkpoint,actor,"
                "from_review_case_id,to_review_case_id,proposal_set_id,report_id,kind,reason"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    transition.transition_id,
                    connection,
                    library,
                    item_id,
                    checkpoint + 1,
                    transition.actor,
                    case_id,
                    None,
                    proposal_id,
                    report_id,
                    kind,
                    reason,
                ),
            )
            self._connection.execute(
                "UPDATE current_review_case_lineages SET review_case_id=NULL,"
                "transition_id=?,checkpoint=?,outcome=? "
                "WHERE connection_name=? AND library_name=? AND item_id=? "
                "AND review_case_id=? AND checkpoint=?",
                (
                    transition.transition_id,
                    checkpoint + 1,
                    kind,
                    connection,
                    library,
                    item_id,
                    case_id,
                    checkpoint,
                ),
            )
            if published is not None:
                if (
                    self._connection.execute(
                        "DELETE FROM current_review_packages WHERE connection_name=? AND library_name=? AND item_id=? AND proposal_set_id=? AND report_id=? AND review_case_id=? AND checkpoint=?",
                        (connection, library, item_id, *published),
                    ).rowcount
                    != 1
                ):
                    raise MetadataReportError("review preparation state changed")
        self._connection.execute(
            "DELETE FROM review_preparation_absences WHERE scan_id=?", (scan_id,)
        )

    def advance_review_pipeline_action(
        self,
        action: PipelineAction,
        output_id: str | None,
        completed_at: int,
        claim_next: bool = False,
        exact_next: bool = False,
    ) -> PipelineAction | None:
        if (
            type(claim_next) is not bool
            or type(exact_next) is not bool
            or (claim_next and exact_next)
        ):
            raise MetadataReportError("review preparation state is invalid")
        stages = {
            "extraction": ("provider", "report_id"),
            "provider": ("proposal", None),
            "proposal": ("admission", "proposal_set_id"),
            "admission": ("projection", "review_case_id"),
            "projection": ("complete", None),
        }
        target = stages.get(action.stage)
        if target is None:
            raise MetadataReportError("review preparation state is invalid")
        if action.stage == "admission" and output_id is None:
            target = ("complete", None)
        lease = self._output_lease
        if lease is None:
            lease = self._operation_lease()
            lease.acquire()
        try:
            if not self._connection.in_transaction:
                self._connection.execute("BEGIN IMMEDIATE")
            if action.stage == "projection":
                if action.review_case_id is None:
                    raise MetadataReportError("review preparation state changed")
                self._require_staged_shadow_projection_current(
                    action.review_case_id, action
                )
            values: list[object] = [target[0], completed_at]
            assignment = ""
            if target[1] is not None:
                assignment = f",{target[1]}=?"
                values.append(output_id)
            values.extend((action.action_id, action.stage, action.attempt))
            updated = self._connection.execute(
                "UPDATE review_preparation_work SET stage=?,attempt=0,state="
                + ("'complete'" if target[0] == "complete" else "'queued'")
                + ",outcome="
                + (
                    ("'case'" if action.stage == "projection" else "'no_case'")
                    if target[0] == "complete"
                    else "NULL"
                )
                + ",updated_at=?"
                + assignment
                + " WHERE work_id=? AND stage=? AND state='running' AND attempt=?",
                tuple(values),
            ).rowcount
            if updated != 1:
                raise MetadataReportError("review preparation state changed")
            if action.stage == "extraction" and output_id is not None:
                transition_row = self._connection.execute(
                    "SELECT transition_id,report_id FROM metadata_policy_transition_items "
                    "WHERE work_id=? AND state='pending'",
                    (action.action_id,),
                ).fetchone()
                if transition_row is not None and output_id != transition_row[1]:
                    self._connection.execute(
                        "UPDATE metadata_policy_transition_items SET state='failed',failure_code='stale',updated_at=? "
                        "WHERE transition_id=? AND work_id=? AND state='pending'",
                        (completed_at, transition_row[0], action.action_id),
                    )
                    self._refresh_metadata_policy_transition(
                        transition_row[0], completed_at
                    )
            if (
                target[0] == "complete"
                and action.stage in {"admission", "projection"}
                and action.proposal_set_id is not None
                and action.report_id is not None
            ):
                pointer = self._connection.execute(
                    "SELECT proposal_set_id,report_id,review_case_id,checkpoint,outcome FROM current_review_case_lineages "
                    "WHERE connection_name=? AND library_name=? AND item_id=?",
                    (action.connection, action.library, action.item_id),
                ).fetchone()
                if (
                    pointer is None
                    or pointer[0] != action.proposal_set_id
                    or pointer[1] != action.report_id
                    or pointer[4]
                    not in {"admitted", "superseded", "no_case", "retired"}
                    or (
                        action.stage == "projection"
                        and pointer[2] != action.review_case_id
                    )
                ):
                    raise MetadataReportError("review preparation state changed")
                self._connection.execute(
                    "INSERT INTO current_review_packages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET "
                    "proposal_set_id=excluded.proposal_set_id,report_id=excluded.report_id,"
                    "review_case_id=excluded.review_case_id,checkpoint=excluded.checkpoint,outcome=excluded.outcome",
                    (action.connection, action.library, action.item_id, *pointer),
                )
                self._enqueue_provider_recovery_outbox(
                    # PipelineAction.item_id is optional on the dataclass, but
                    # the pointer lookup immediately above matched a lineage
                    # row on exactly this connection/library/item triple.
                    action.connection,
                    action.library,
                    cast(str, action.item_id),
                    pointer,
                    completed_at,
                )
            if target[0] == "complete":
                transition_row = self._connection.execute(
                    "SELECT transition_id FROM metadata_policy_transition_items WHERE work_id=? AND state='pending'",
                    (action.action_id,),
                ).fetchone()
                if transition_row is not None:
                    self._connection.execute(
                        "UPDATE metadata_policy_transition_items SET state='complete',failure_code=NULL,updated_at=? WHERE transition_id=? AND work_id=? AND state='pending'",
                        (completed_at, transition_row[0], action.action_id),
                    )
                    self._refresh_metadata_policy_transition(
                        transition_row[0], completed_at
                    )
            if action.stage == "provider":
                self._connection.execute(
                    "UPDATE review_preparation_control SET circuit_failures="
                    "CASE WHEN circuit_open_until<=? THEN 0 ELSE circuit_failures END,"
                    "circuit_open_until=CASE WHEN circuit_open_until<=? THEN 0 "
                    "ELSE circuit_open_until END,updated_at=? WHERE singleton=1",
                    (completed_at, completed_at, completed_at),
                )
            refresh_preparation_work(self._connection, action.action_id, completed_at)
            next_action = (
                self.claim_review_pipeline_action(completed_at)
                if claim_next
                else (
                    self.claim_review_pipeline_work(action.action_id, completed_at)
                    if exact_next and target[0] != "complete"
                    else None
                )
            )
            self._pending_claim = (next_action,)
            self._connection.commit()
            self.commit_review_pipeline_claim()
            return next_action
        except BaseException as error:
            if self._connection.in_transaction:
                self._connection.rollback()
            if not isinstance(error, Exception):
                interruption = error
            elif isinstance(error, MetadataReportError):
                raise
            elif isinstance(error, ReviewAdmissionStaleError):
                raise MetadataReportError("review preparation state changed") from error
            elif isinstance(error, sqlite3.Error):
                raise MetadataReportError(
                    "review preparation persistence failed"
                ) from error
            else:
                raise
        finally:
            try:
                clean = not self._connection.in_transaction
            except BaseException:
                clean = False
            if clean or _revoke_connection(self._connection):
                if lease is self._output_lease:
                    self._output_lease = None
                lease.release()
        raise interruption from None

    def begin_review_pipeline_output(self) -> None:
        lease = self._operation_lease()
        lease.acquire()
        try:
            already_open = self._connection.in_transaction
        except BaseException:
            if _revoke_connection(self._connection):
                lease.release()
            raise
        if already_open:
            lease.release()
            raise MetadataReportError("review preparation state is invalid")
        failure: BaseException | None = None
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if not self._connection.in_transaction:
                raise MetadataReportError("review preparation state is invalid")
            self._output_lease = lease
        except BaseException as error:
            cleanup_proven, _cleanup_error = _unwind_outer(self._connection)
            if cleanup_proven or _revoke_connection(self._connection):
                lease.release()
            failure = error
        if isinstance(failure, sqlite3.Error):
            raise MetadataReportError(
                "review preparation persistence failed"
            ) from failure
        if failure is not None:
            raise failure from None

    def take_review_pipeline_claim(self) -> PipelineAction | None:
        action = self._review_pipeline_claim
        self._review_pipeline_claim = None
        return action

    def rollback_review_pipeline_output(self) -> None:
        original = sys.exception()
        cleanup_proven, cleanup_error = _unwind_outer(self._connection)
        releasable = cleanup_proven or _revoke_connection(self._connection)
        if releasable and self._output_lease is not None:
            lease = self._output_lease
            self._output_lease = None
            lease.release()
        if original is not None:
            return
        if isinstance(cleanup_error, sqlite3.Error):
            raise MetadataReportError(
                "review preparation persistence failed"
            ) from cleanup_error
        if cleanup_error is not None:
            raise cleanup_error from None

    @local_operation
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
        """Bind report publication and provider disposition atomically."""
        if (
            not self._connection.in_transaction
            or action.stage != "extraction"
            or type(recovery_delay_seconds) is not int
            or not 0 <= recovery_delay_seconds <= 86_400
            or provider_failure_code
            not in {
                None,
                "provider",
                "configuration",
                "authentication",
                "transient",
                # Not a provider disposition at all: the report is complete and
                # published, and no work identity was admitted for it, so the
                # work ends here -- completed, with its evidence recorded --
                # rather than queueing a proposal with nothing to compare.
                "evidence_only",
            }
            or type(claim_next) is not bool
            or (
                provider_failure_root is not None
                and (
                    provider_failure_code != "authentication"
                    or type(provider_failure_root) is not str
                    or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider_failure_root)
                    is None
                )
            )
            or (
                provider_failure_code == "authentication"
                and provider_failure_root is None
            )
        ):
            raise MetadataReportError("review preparation state is invalid")
        if provider_failure_code == "transient":
            updated = self._connection.execute(
                "UPDATE review_preparation_work SET "
                "stage=CASE WHEN recovery_attempt<2 THEN 'extraction' ELSE 'proposal' END,"
                "state=CASE WHEN recovery_attempt<2 THEN 'delayed' ELSE 'queued' END,attempt=0,recovery_attempt=recovery_attempt+1,"
                "retry_not_before=CASE WHEN recovery_attempt<2 THEN ? "
                "ELSE 0 END,report_id=?,proposal_set_id=NULL,review_case_id=NULL,"
                "failure_code='transient',failure_root=NULL,"
                "updated_at=? WHERE work_id=? AND stage='extraction' "
                "AND state='running' AND attempt=?",
                (
                    completed_at
                    + max(
                        recovery_delay_seconds,
                        5 * (1 << min(3, action.attempt)),
                    ),
                    report_id,
                    completed_at,
                    action.action_id,
                    action.attempt,
                ),
            ).rowcount
        else:
            recorded = provider_failure_code == "evidence_only"
            terminal_failure = provider_failure_code in {
                "authentication",
                "configuration",
            }
            updated = self._connection.execute(
                "UPDATE review_preparation_work SET stage=?,state=?,attempt=0,"
                "retry_not_before=0,report_id=?,failure_code=?,failure_root=?,outcome=?,"
                "updated_at=? WHERE work_id=? "
                "AND stage='extraction' AND state='running' AND attempt=?",
                (
                    "complete"
                    if recorded
                    else "provider"
                    if terminal_failure
                    else "proposal",
                    "complete"
                    if recorded
                    else "failed"
                    if terminal_failure
                    else "queued",
                    report_id,
                    None if recorded else provider_failure_code,
                    provider_failure_root,
                    "evidence_only" if recorded else None,
                    completed_at,
                    action.action_id,
                    action.attempt,
                ),
            ).rowcount
        if updated != 1:
            raise MetadataReportError("review preparation state changed")
        if provider_failure_code == "evidence_only":
            # Publication registers this row itself only when nothing follows
            # it, and the pipeline always passes a disposition.  An ebook
            # acquires its package later, at proposal or admission; an
            # audiobook reaches neither stage, so the row it is browsed and
            # searched by has to be written here or never.
            registered = self._connection.execute(
                "INSERT INTO current_review_packages(connection_name,library_name,item_id,proposal_set_id,report_id,review_case_id,checkpoint,outcome) "
                "SELECT connection_name,library_name,item_id,NULL,report_id,NULL,NULL,'evidence_only' "
                "FROM metadata_reports WHERE report_id=? "
                "ON CONFLICT(connection_name,library_name,item_id) DO UPDATE SET "
                "proposal_set_id=NULL,report_id=excluded.report_id,review_case_id=NULL,checkpoint=NULL,outcome='evidence_only'",
                (report_id,),
            ).rowcount
            if registered != 1:
                # The row is selected from the report this call was handed, so
                # writing nothing means that report is not there: exactly the
                # unreachable-evidence failure this registration exists to
                # prevent, and it must not pass silently.
                raise MetadataReportError("review preparation state changed")
        transition_row = self._connection.execute(
            "SELECT transition_id,report_id FROM metadata_policy_transition_items "
            "WHERE work_id=? AND state='pending'",
            (action.action_id,),
        ).fetchone()
        if transition_row is not None:
            work_state = self._connection.execute(
                "SELECT state FROM review_preparation_work WHERE work_id=?",
                (action.action_id,),
            ).fetchone()
            failure_code = (
                "stale"
                if report_id != transition_row[1]
                else (
                    "failed"
                    if work_state is not None and work_state[0] in {"failed", "stale"}
                    else None
                )
            )
            if failure_code is not None:
                self._connection.execute(
                    "UPDATE metadata_policy_transition_items SET state='failed',"
                    "failure_code=?,updated_at=? WHERE transition_id=? AND work_id=? "
                    "AND state='pending'",
                    (
                        failure_code,
                        completed_at,
                        transition_row[0],
                        action.action_id,
                    ),
                )
                self._refresh_metadata_policy_transition(
                    transition_row[0], completed_at
                )
        refresh_preparation_work(self._connection, action.action_id, completed_at)
        self._review_pipeline_claim = (
            self.claim_review_pipeline_action(completed_at)
            if claim_next and (self._claim_owner is None or self._publication_admitted)
            else None
        )
        self._pending_claim = (self._review_pipeline_claim,)

    @local_operation
    def fail_review_pipeline_action(
        self,
        action: PipelineAction,
        code: str,
        retryable: bool,
        completed_at: int,
        failure_root: str | None = None,
    ) -> None:
        transaction = self._transaction("fail_review_pipeline_action")
        if action.stage == "discovery":
            try:
                transaction.begin()
                pending_manual_return = self._has_pending_field_effect_manual_return(
                    action.scan_id
                )
                updated = self._connection.execute(
                    "UPDATE review_preparation_scans SET state="
                    "CASE WHEN rescan_requested=1 AND ? THEN 'queued' "
                    "ELSE 'completed_with_failures' END,"
                    "refresh_count=CASE WHEN rescan_requested=1 AND ? "
                    "THEN 0 ELSE refresh_count END,"
                    "rescan_requested=0,updated_at=? "
                    "WHERE scan_id=? AND state='discovering'",
                    (
                        pending_manual_return,
                        pending_manual_return,
                        completed_at,
                        action.scan_id,
                    ),
                ).rowcount
                if updated != 1:
                    raise MetadataReportError("review preparation state changed")
                refresh_preparation_run_state(self._connection, completed_at)
                self._pending_claim = (None,)
                transaction.commit(after_commit=self.commit_review_pipeline_claim)
                return
            except BaseException as error:
                transaction.rollback()
                if not isinstance(error, Exception):
                    interruption = error
                elif isinstance(error, MetadataReportError):
                    raise
                elif isinstance(error, sqlite3.Error):
                    raise MetadataReportError(
                        "review preparation persistence failed"
                    ) from error
                else:
                    raise
            raise interruption from None
        if code not in _FAILURE_CODES:
            code = "failed"
        if failure_root is not None:
            if code == "authentication":
                if (
                    type(failure_root) is not str
                    or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", failure_root) is None
                ):
                    code = "failed"
                    failure_root = None
            elif not _admits_track_root(code, failure_root):
                # Under every other code the root is detail rather than the
                # failure itself, so a root this vocabulary does not admit is
                # dropped and the code the pipeline chose still reaches the
                # owner. Keeping it would mean the CHECK refusing the write.
                failure_root = None
        state = (
            "queued"
            if retryable and action.attempt < 3
            else ("stale" if code in {"stale", "currentness"} else "failed")
        )
        try:
            transaction.begin()
            if code == "transient" and retryable:
                updated = self._connection.execute(
                    "UPDATE review_preparation_work SET stage='extraction',"
                    "state=CASE WHEN recovery_attempt<2 THEN 'delayed' ELSE 'failed' END,"
                    "attempt=0,recovery_attempt=recovery_attempt+1,"
                    "retry_not_before=CASE WHEN recovery_attempt<2 THEN ? ELSE 0 END,"
                    "report_id=NULL,recovery_source_report_id=NULL,"
                    "proposal_set_id=NULL,review_case_id=NULL,"
                    "failure_code='transient',failure_root=NULL,updated_at=? WHERE work_id=? AND stage=? "
                    "AND state='running' AND attempt=?",
                    (
                        completed_at + 5 * (1 << min(3, action.attempt)),
                        completed_at,
                        action.action_id,
                        action.stage,
                        action.attempt,
                    ),
                ).rowcount
                if updated == 1:
                    state = self._connection.execute(
                        "SELECT state FROM review_preparation_work WHERE work_id=?",
                        (action.action_id,),
                    ).fetchone()[0]
            else:
                updated = self._connection.execute(
                    "UPDATE review_preparation_work SET state=?,failure_code=?,failure_root=?,updated_at=? "
                    "WHERE work_id=? AND stage=? AND state='running' AND attempt=?",
                    (
                        state,
                        code,
                        failure_root,
                        completed_at,
                        action.action_id,
                        action.stage,
                        action.attempt,
                    ),
                ).rowcount
            if updated != 1:
                raise MetadataReportError("review preparation state changed")
            if state == "stale":
                self._connection.execute(
                    "UPDATE review_preparation_scans SET rescan_requested="
                    "CASE WHEN refresh_count<3 THEN 1 ELSE 0 END,updated_at=? "
                    "WHERE scan_id=?",
                    (completed_at, action.scan_id),
                )
            if state in {"failed", "stale"}:
                transition_row = self._connection.execute(
                    "SELECT transition_id FROM metadata_policy_transition_items WHERE work_id=? AND state='pending'",
                    (action.action_id,),
                ).fetchone()
                if transition_row is not None:
                    self._connection.execute(
                        "UPDATE metadata_policy_transition_items SET state='failed',failure_code=?,updated_at=? WHERE transition_id=? AND work_id=? AND state='pending'",
                        (
                            "stale" if state == "stale" else "failed",
                            completed_at,
                            transition_row[0],
                            action.action_id,
                        ),
                    )
                    self._refresh_metadata_policy_transition(
                        transition_row[0], completed_at
                    )
            refresh_preparation_work(self._connection, action.action_id, completed_at)
            self._pending_claim = (None,)
            transaction.commit(after_commit=self.commit_review_pipeline_claim)
            return
        except BaseException as error:
            transaction.rollback()
            if not isinstance(error, Exception):
                interruption = error
            elif isinstance(error, MetadataReportError):
                raise
            elif isinstance(error, sqlite3.Error):
                raise MetadataReportError(
                    "review preparation persistence failed"
                ) from error
            else:
                raise
        raise interruption from None

    @local_operation
    def review_case_ready_at(self, review_case_id: str) -> int | None:
        """Return the exact preparation-complete checkpoint for advisory timing."""
        try:
            row = self._connection.execute(
                "SELECT updated_at FROM review_preparation_work "
                "WHERE review_case_id=? AND stage='complete' AND state='complete' "
                "ORDER BY updated_at DESC,work_id LIMIT 1",
                (review_case_id,),
            ).fetchone()
            if row is None:
                return None
            if len(row) != 1 or type(row[0]) is not int or row[0] < 0:
                raise MetadataReportError("review case readiness is unavailable")
            return row[0]
        except sqlite3.Error as error:
            raise MetadataReportError("review case readiness is unavailable") from error

    def _schedule_current_shadow_work(self, case: ReviewCase, updated_at: int) -> None:
        """Reuse an existing current pipeline row; never create a scheduler."""
        lineage = (
            case.target["connection"],
            case.target["library"],
            case.target["item"],
        )
        current = self._connection.execute(
            "SELECT l.report_id,f.report_id,c.report_id,c.review_case_id "
            "FROM current_metadata_report_lineages l "
            "JOIN metadata_reports m ON m.report_id=l.report_id "
            "LEFT JOIN current_metadata_reports f ON "
            "f.connection_name=l.connection_name AND "
            "f.library_name=l.library_name AND f.item_id=l.item_id AND "
            "f.file_identity=m.file_identity "
            "LEFT JOIN current_review_case_lineages c ON "
            "c.connection_name=l.connection_name AND "
            "c.library_name=l.library_name AND c.item_id=l.item_id "
            "WHERE l.connection_name=? AND l.library_name=? AND l.item_id=?",
            lineage,
        ).fetchone()
        if (
            current is None
            or type(current[0]) is not str
            or current[0] != current[1]
            or (current[2] is not None and type(current[2]) is not str)
            or (current[3] is not None and type(current[3]) is not str)
        ):
            raise MetadataReportError("shadow outcome currentness is unavailable")
        current_report_id, _, review_report_id, review_case_id = current
        if (
            current_report_id == case.report_id
            and review_report_id == case.report_id
            and review_case_id == case.review_case_id
        ):
            return
        row = self._connection.execute(
            "SELECT work_id,report_id,review_case_id FROM review_preparation_work "
            "WHERE connection_name=? AND library_name=? AND item_id=? "
            "ORDER BY updated_at DESC,work_id DESC LIMIT 1",
            lineage,
        ).fetchone()
        if row is None:
            return
        if (
            review_case_id is not None
            and review_report_id == current_report_id
            and row[2] == review_case_id
            and row[1] == current_report_id
        ):
            self._connection.execute(
                "UPDATE review_preparation_work SET stage='projection',state='queued',"
                "attempt=0,recovery_attempt=0,failure_code=NULL,failure_root=NULL,"
                "outcome=NULL,updated_at=? WHERE work_id=?",
                (updated_at, row[0]),
            )
        else:
            self._restart_metadata_policy_work(
                row[0], "proposal", current_report_id, updated_at
            )

    def _restart_metadata_policy_work(
        self,
        work_id: str,
        work_stage: str,
        report_id: str,
        updated_at: int,
    ) -> None:
        restart_stage = "extraction" if work_stage == "extraction" else "proposal"
        self._connection.execute(
            "UPDATE review_preparation_work SET stage=?,state='queued',attempt=0,"
            "recovery_attempt=0,report_id=?,proposal_set_id=NULL,review_case_id=NULL,"
            "recovery_source_report_id=CASE WHEN ?='extraction' THEN NULL "
            "ELSE recovery_source_report_id END,"
            "failure_code=NULL,failure_root=NULL,outcome=NULL,updated_at=? WHERE work_id=?",
            (
                restart_stage,
                None if restart_stage == "extraction" else report_id,
                restart_stage,
                updated_at,
                work_id,
            ),
        )
