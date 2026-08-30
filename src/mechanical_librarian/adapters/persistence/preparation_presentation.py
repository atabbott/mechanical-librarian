"""One bounded owner-facing projection over authoritative preparation rows."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING, cast

from mechanical_librarian.adapters.persistence.transactions import TransactionScope
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.preparation_presentation import (
    TERMINAL_PREPARATION_STATES,
    PreparationActiveBook,
    PreparationCompletion,
    PreparationPassage,
    PreparationPresentation,
    PreparationPresentationError,
    PreparationRunState,
    PreparationTrigger,
    PreparationTriggerRecord,
)
from mechanical_librarian.domain.validation import (
    bounded_utf8_text,
    non_negative_integer,
    prefixed_hex_identity,
)

if TYPE_CHECKING:
    from mechanical_librarian.application.catalogue import CatalogueEntry


_STAGE_ORDER = {
    "extraction": 0,
    "provider": 1,
    "proposal": 2,
    "admission": 3,
    "projection": 4,
}
_STAGE_COPY = {
    "extraction": "Reading from library",
    "provider": "Finishing searches",
    "proposal": "Comparing candidates",
    "admission": "Checking metadata",
    "projection": "Preparing review",
}
_SQLITE_INTEGER_MAXIMUM = 9_223_372_036_854_775_807
_preparation_display_name = bounded_utf8_text(2048)


def _completion_sequence_count(connection: sqlite3.Connection, run_id: str) -> int:
    count_row = connection.execute(
        "SELECT completion_count FROM review_preparation_presentation "
        "WHERE row_id='run' AND run_id=?",
        (run_id,),
    ).fetchone()
    aggregate = connection.execute(
        "SELECT COUNT(completion_sequence),MIN(completion_sequence),MAX(completion_sequence),"
        "COALESCE(SUM(typeof(completion_sequence)<>'integer'),0) "
        "FROM review_preparation_presentation "
        "INDEXED BY review_preparation_presentation_completion_integrity "
        "WHERE row_kind='work' AND run_id=? AND completion_sequence IS NOT NULL",
        (run_id,),
    ).fetchone()
    count = None if count_row is None else count_row[0]
    actual, earliest, latest, noninteger = aggregate
    if (
        type(count) is not int
        or count < 0
        or count >= _SQLITE_INTEGER_MAXIMUM
        or type(actual) is not int
        or type(noninteger) is not int
        or noninteger != 0
        or actual != count
        or (count == 0) != (earliest is None)
        or (count == 0) != (latest is None)
        or (earliest is not None and (type(earliest) is not int or earliest != 1))
        or (latest is not None and (type(latest) is not int or latest != count))
    ):
        raise MetadataReportError("review preparation persistence failed") from None
    return count


def _next_completion_sequence(connection: sqlite3.Connection, run_id: str) -> int:
    count = _completion_sequence_count(connection, run_id)
    if count >= _SQLITE_INTEGER_MAXIMUM - 1:
        raise MetadataReportError("review preparation persistence failed") from None
    return count + 1


def _delete_preparation_work_rows(
    connection: sqlite3.Connection,
    run_id: str,
    row_ids: tuple[str, ...],
) -> None:
    if not row_ids:
        return
    _completion_sequence_count(connection, run_id)
    connection.executemany(
        "UPDATE review_preparation_presentation SET "
        "current_work_state=CASE work_outcome WHEN 'ready' THEN 'complete' ELSE 'failed' END,"
        "recovery_high_water_stage=NULL,recovery_high_water_outcome=NULL "
        "WHERE row_id=? AND work_outcome IS NOT NULL",
        ((row_id,) for row_id in row_ids),
    )
    connection.executemany(
        "DELETE FROM review_preparation_presentation WHERE row_id=? AND work_outcome IS NULL",
        ((row_id,) for row_id in row_ids),
    )
    completed = connection.execute(
        "SELECT row_id,completion_sequence FROM review_preparation_presentation "
        "WHERE row_kind='work' AND run_id=? AND completion_sequence IS NOT NULL "
        "ORDER BY completion_sequence",
        (run_id,),
    ).fetchall()
    connection.executemany(
        "UPDATE review_preparation_presentation SET completion_sequence=? WHERE row_id=?",
        (
            (expected, row_id)
            for expected, (row_id, _sequence) in enumerate(completed, 1)
        ),
    )
    connection.execute(
        "UPDATE review_preparation_presentation SET completion_count=?,active_count=("
        "SELECT COUNT(*) FROM review_preparation_presentation AS work "
        "WHERE work.row_kind='work' AND work.run_id=? AND work.current_work_state='running') "
        "WHERE row_id='run' AND run_id=?",
        (len(completed), run_id, run_id),
    )


def deferred_preparation_enrollment_id(connection: str, library: str) -> str:
    material = f"{connection}\0{library}"
    return "deferred_enrollment:" + hashlib.sha256(material.encode()).hexdigest()


def _visible_preparation_run(
    connection: sqlite3.Connection,
    trigger: PreparationTrigger,
    identity: str,
    accepted_at: int,
) -> tuple[str, bool]:
    current = connection.execute(
        "SELECT run_id,state FROM review_preparation_presentation WHERE row_id='run'"
    ).fetchone()
    replacing_terminal = current is not None and current[1] in {
        PreparationRunState.COMPLETED_CLEAN.value,
        PreparationRunState.COMPLETED_WITH_FAILURES.value,
        PreparationRunState.DISMISSED.value,
    }
    if current is None or replacing_terminal:
        previous = "" if current is None else cast(str, current[0])
        material = f"preparation-presentation-v1\0{previous}\0{trigger.value}\0{identity}\0{accepted_at}"
        run_id = "preparation_run_" + hashlib.sha256(material.encode()).hexdigest()
        connection.execute("DELETE FROM review_preparation_presentation")
        connection.execute(
            "INSERT INTO review_preparation_presentation("
            "row_id,row_kind,run_id,state,started_at,updated_at,total,active_count,read,searches_finished,compared,checked,ready,failed,completion_count) "
            "VALUES('run','run',?,'starting',?,?,0,0,0,0,0,0,0,0,0)",
            (run_id, accepted_at, accepted_at),
        )
        return run_id, replacing_terminal
    return cast(str, current[0]), False


def _record_preparation_trigger(
    connection: sqlite3.Connection,
    run_id: str,
    trigger: PreparationTrigger,
    accepted_at: int,
) -> None:
    trigger_ordinal = tuple(PreparationTrigger).index(trigger)
    connection.execute(
        "INSERT INTO review_preparation_presentation("
        "row_id,row_kind,run_id,trigger,trigger_ordinal,accepted_count,first_accepted_at,last_accepted_at,updated_at) "
        "VALUES(?,?,?,?,?,1,?,?,?) ON CONFLICT(row_id) DO UPDATE SET "
        "accepted_count=accepted_count+1,last_accepted_at=excluded.last_accepted_at,updated_at=excluded.updated_at",
        (
            f"trigger:{trigger.value}",
            "trigger",
            run_id,
            trigger.value,
            trigger_ordinal,
            accepted_at,
            accepted_at,
            accepted_at,
        ),
    )


def defer_preparation_library_addition(
    connection: sqlite3.Connection,
    connection_name: str,
    library_name: str,
    accepted_at: int,
    settings_revision: int,
) -> None:
    """Accept one paused library addition without creating claimable work."""
    row_id = deferred_preparation_enrollment_id(connection_name, library_name)
    run_id, _replacing_terminal = _visible_preparation_run(
        connection,
        PreparationTrigger.LIBRARY_ADDED,
        row_id,
        accepted_at,
    )
    if (
        connection.execute(
            "SELECT 1 FROM review_preparation_presentation "
            "WHERE row_id=? AND row_kind='deferred_enrollment'",
            (row_id,),
        ).fetchone()
        is None
    ):
        ordinal = connection.execute(
            "SELECT COUNT(*) FROM review_preparation_presentation "
            "WHERE row_kind IN ('enrollment','deferred_enrollment')"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO review_preparation_presentation("
            "row_id,row_kind,run_id,connection_name,library_name,ordinal,accepted_at,settings_revision,updated_at) "
            "VALUES(?,'deferred_enrollment',?,?,?,?,?,?,?)",
            (
                row_id,
                run_id,
                connection_name,
                library_name,
                ordinal,
                accepted_at,
                settings_revision,
                accepted_at,
            ),
        )
        _record_preparation_trigger(
            connection, run_id, PreparationTrigger.LIBRARY_ADDED, accepted_at
        )
    _refresh_run_state(connection, run_id, accepted_at)


def reconcile_preparation_library_additions(
    connection: sqlite3.Connection,
    previous_targets: tuple[tuple[str, str], ...],
    candidate_targets: tuple[tuple[str, str], ...],
    settings_revision: int,
    accepted_at: int,
) -> None:
    """Persist one Settings target delta in the caller-owned transaction."""
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='review_preparation_presentation'"
        ).fetchone()
        is None
    ):
        return
    candidate = set(candidate_targets)
    for row_id, target in (
        (row[0], (row[1], row[2]))
        for row in connection.execute(
            "SELECT row_id,connection_name,library_name FROM review_preparation_presentation "
            "WHERE row_kind='deferred_enrollment' ORDER BY ordinal,row_id"
        )
    ):
        if target not in candidate:
            connection.execute(
                "DELETE FROM review_preparation_presentation WHERE row_id=?", (row_id,)
            )
    additions = tuple(
        target for target in candidate_targets if target not in set(previous_targets)
    )
    for connection_name, library_name in additions:
        if (
            connection.execute(
                "SELECT 1 FROM review_preparation_scans WHERE connection_name=? "
                "AND library_name=? AND state IN ('queued','discovering','processing') LIMIT 1",
                (connection_name, library_name),
            ).fetchone()
            is not None
        ):
            raise sqlite3.IntegrityError("preparation addition authority conflicts")
        existing = connection.execute(
            "SELECT row_id,cycle_id,run_id FROM review_preparation_presentation "
            "WHERE row_kind='enrollment' AND connection_name=? AND library_name=?",
            (connection_name, library_name),
        ).fetchone()
        if existing is not None:
            work_rows = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT row_id FROM review_preparation_presentation WHERE row_kind='work' "
                    "AND scan_id=(SELECT scan_id FROM review_preparation_presentation WHERE row_id=?) "
                    "AND cycle_id=?",
                    (existing[0], existing[1]),
                )
            )
            _delete_preparation_work_rows(
                connection,
                cast(str, existing[2]),
                work_rows,
            )
            if (
                connection.execute(
                    "SELECT 1 FROM review_preparation_presentation WHERE row_kind='work' "
                    "AND scan_id=(SELECT scan_id FROM review_preparation_presentation WHERE row_id=?) "
                    "AND cycle_id=? LIMIT 1",
                    (existing[0], existing[1]),
                ).fetchone()
                is None
            ):
                connection.execute(
                    "DELETE FROM review_preparation_presentation WHERE row_id=?",
                    (existing[0],),
                )
        defer_preparation_library_addition(
            connection,
            connection_name,
            library_name,
            accepted_at,
            settings_revision,
        )
    authorities = connection.execute(
        "SELECT row_id,row_kind,scan_id,cycle_id FROM review_preparation_presentation "
        "WHERE row_kind IN ('enrollment','deferred_enrollment') ORDER BY ordinal,row_id"
    ).fetchall()
    if (
        not authorities
        and not connection.execute(
            "SELECT 1 FROM review_preparation_presentation WHERE row_kind='work' LIMIT 1"
        ).fetchone()
    ):
        connection.execute("DELETE FROM review_preparation_presentation")
        return
    for ordinal, (row_id, row_kind, scan_id, cycle_id) in enumerate(authorities):
        connection.execute(
            "UPDATE review_preparation_presentation SET ordinal=? WHERE row_id=?",
            (ordinal, row_id),
        )
        if row_kind == "enrollment" and cycle_id is not None:
            connection.execute(
                "UPDATE review_preparation_presentation SET enrollment_ordinal=? "
                "WHERE row_kind='work' AND scan_id=? AND cycle_id=?",
                (ordinal, scan_id, cycle_id),
            )


def accept_preparation_trigger(
    connection: sqlite3.Connection,
    trigger: PreparationTrigger,
    scan_id: str,
    connection_name: str,
    library_name: str,
    accepted_at: int,
    previous_scan_state: str | None = None,
    previous_rescan_requested: bool = False,
) -> None:
    """Extend the visible nonterminal run or atomically replace its terminal run."""
    deferred = (
        connection.execute(
            "SELECT run_id,ordinal FROM review_preparation_presentation "
            "WHERE row_id=? AND row_kind='deferred_enrollment'",
            (deferred_preparation_enrollment_id(connection_name, library_name),),
        ).fetchone()
        if trigger is PreparationTrigger.LIBRARY_ADDED
        else None
    )
    restarting_terminal_scan = previous_scan_state in {
        "completed",
        "completed_with_failures",
    }
    if deferred is None:
        run_id, replacing_terminal = _visible_preparation_run(
            connection, trigger, scan_id, accepted_at
        )
    else:
        run_id = cast(str, deferred[0])
        replacing_terminal = False
    enrollment_id = "enrollment:" + hashlib.sha256(scan_id.encode()).hexdigest()
    enrollment = connection.execute(
        "SELECT cycle_id,pending_cycle_id,cycle_state,ordinal FROM review_preparation_presentation "
        "WHERE row_id=? AND row_kind='enrollment'",
        (enrollment_id,),
    ).fetchone()
    active = previous_scan_state in {"discovering", "processing"}
    repeated = enrollment is not None and (
        (active and previous_rescan_requested and enrollment[1] is not None)
        or (
            previous_scan_state == "queued"
            and enrollment[0] is not None
            and enrollment[2] == "queued"
        )
    )
    if not repeated:
        material = f"preparation-cycle-v1\0{run_id}\0{scan_id}\0{accepted_at}\0{previous_scan_state or 'new'}"
        cycle_id = "preparation_cycle_" + hashlib.sha256(material.encode()).hexdigest()
        ordinal = (
            deferred[1]
            if deferred is not None
            else (
                connection.execute(
                    "SELECT COUNT(*) FROM review_preparation_presentation "
                    "WHERE row_kind IN ('enrollment','deferred_enrollment')"
                ).fetchone()[0]
                if enrollment is None
                else enrollment[3]
            )
        )
        connection.execute(
            "INSERT INTO review_preparation_presentation("
            "row_id,row_kind,run_id,scan_id,connection_name,library_name,cycle_id,pending_cycle_id,cycle_state,ordinal,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(row_id) DO UPDATE SET "
            "run_id=excluded.run_id,connection_name=excluded.connection_name,library_name=excluded.library_name,"
            "cycle_id=CASE WHEN review_preparation_presentation.cycle_state IN ('completed','completed_with_failures') "
            "THEN excluded.cycle_id ELSE COALESCE(review_preparation_presentation.cycle_id,excluded.cycle_id) END,"
            "pending_cycle_id=excluded.pending_cycle_id,cycle_state=excluded.cycle_state,updated_at=excluded.updated_at",
            (
                enrollment_id,
                "enrollment",
                run_id,
                scan_id,
                connection_name,
                library_name,
                enrollment[0]
                if active and enrollment is not None
                else None
                if active
                else cycle_id,
                cycle_id if active else None,
                "queued"
                if replacing_terminal
                or restarting_terminal_scan
                or (active and enrollment is None)
                else (previous_scan_state or "queued"),
                ordinal,
                accepted_at,
            ),
        )
        if enrollment is not None and enrollment[2] in {
            "completed",
            "completed_with_failures",
        }:
            connection.execute(
                "UPDATE review_preparation_presentation SET cycle_id=? "
                "WHERE row_kind='work' AND scan_id=? AND cycle_id=?",
                (cycle_id, scan_id, enrollment[0]),
            )
        if deferred is None:
            _record_preparation_trigger(connection, run_id, trigger, accepted_at)
        else:
            connection.execute(
                "DELETE FROM review_preparation_presentation WHERE row_id=?",
                (deferred_preparation_enrollment_id(connection_name, library_name),),
            )
    else:
        return
    _refresh_run_state(connection, run_id, accepted_at)


def record_preparation_names(
    connection: sqlite3.Connection,
    scan_id: str,
    entries: tuple[CatalogueEntry, ...],
    updated_at: int,
) -> None:
    run = connection.execute(
        "SELECT run_id FROM review_preparation_presentation WHERE row_id='run'"
    ).fetchone()
    enrolled = connection.execute(
        "SELECT cycle_id,ordinal FROM review_preparation_presentation WHERE row_kind='enrollment' "
        "AND scan_id=? AND cycle_id IS NOT NULL AND cycle_state NOT IN ('completed','completed_with_failures')",
        (scan_id,),
    ).fetchone()
    if run is None or enrolled is None:
        return
    run_id = cast(str, run[0])
    cycle_id, enrollment_ordinal = enrolled
    selected = {entry.item_id: entry for entry in entries}
    rows = connection.execute(
        "SELECT work_id,item_id,ordinal,state FROM review_preparation_work "
        "WHERE scan_id=? ORDER BY ordinal,work_id",
        (scan_id,),
    ).fetchall()
    current_work_ids = {work_id for work_id, _item_id, _ordinal, _state in rows}
    removed = tuple(
        "work:" + presentation_work_id
        for (presentation_work_id,) in connection.execute(
            "SELECT work_id FROM review_preparation_presentation WHERE row_kind='work' "
            "AND scan_id=? AND cycle_id=?",
            (scan_id, cycle_id),
        ).fetchall()
        if presentation_work_id not in current_work_ids
    )
    _delete_preparation_work_rows(connection, run_id, removed)
    for work_id, item_id, ordinal, current_state in rows:
        entry = selected.get(item_id)
        if entry is None:
            continue
        connection.execute(
            "INSERT INTO review_preparation_presentation("
            "row_id,row_kind,run_id,scan_id,cycle_id,work_id,ordinal,enrollment_ordinal,display_name,work_stage,current_work_state,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(row_id) DO UPDATE SET "
            "run_id=excluded.run_id,scan_id=excluded.scan_id,cycle_id=excluded.cycle_id,ordinal=excluded.ordinal,"
            "enrollment_ordinal=excluded.enrollment_ordinal,display_name=CASE "
            "WHEN review_preparation_presentation.work_outcome IS NULL THEN excluded.display_name "
            "ELSE review_preparation_presentation.display_name END,"
            "current_work_state=excluded.current_work_state,updated_at=CASE "
            "WHEN review_preparation_presentation.work_outcome IS NULL THEN excluded.updated_at "
            "ELSE review_preparation_presentation.updated_at END",
            (
                "work:" + work_id,
                "work",
                run_id,
                scan_id,
                cycle_id,
                work_id,
                ordinal,
                enrollment_ordinal,
                entry.title,
                "extraction",
                current_state,
                updated_at,
            ),
        )
    connection.execute(
        "UPDATE review_preparation_presentation SET total=MAX(total,("
        "SELECT COUNT(*) FROM review_preparation_presentation AS work "
        "WHERE work.row_kind='work' AND work.run_id=?)),active_count=("
        "SELECT COUNT(*) FROM review_preparation_presentation AS work WHERE work.row_kind='work' "
        "AND work.run_id=? AND work.current_work_state='running'),updated_at=? "
        "WHERE row_id='run' AND run_id=?",
        (run_id, run_id, updated_at, run_id),
    )
    _refresh_run_state(connection, run_id, updated_at)


def refresh_preparation_presentation(
    connection: sqlite3.Connection, updated_at: int
) -> None:
    run = connection.execute(
        "SELECT run_id FROM review_preparation_presentation WHERE row_id='run'"
    ).fetchone()
    if run is not None:
        _refresh_preparation_presentation(connection, updated_at)
        _adopt_enrollment_states(connection, updated_at)
        _refresh_run_state(connection, cast(str, run[0]), updated_at)


def refresh_preparation_run_state(
    connection: sqlite3.Connection, updated_at: int
) -> None:
    """Refresh only the bounded run envelope, never its work population."""
    run = connection.execute(
        "SELECT run_id FROM review_preparation_presentation WHERE row_id='run'"
    ).fetchone()
    if run is not None:
        _adopt_enrollment_states(connection, updated_at)
        _refresh_run_state(connection, cast(str, run[0]), updated_at)


def mark_preparation_recovery(connection: sqlite3.Connection, scan_id: str) -> None:
    """Bind retained high water to its authoritative work inside recovery."""
    enrollment_id = "enrollment:" + hashlib.sha256(scan_id.encode()).hexdigest()
    enrollment = connection.execute(
        "SELECT cycle_id FROM review_preparation_presentation WHERE row_id=? "
        "AND row_kind='enrollment' AND scan_id=? AND cycle_id IS NOT NULL "
        "AND cycle_state NOT IN ('completed','completed_with_failures')",
        (enrollment_id, scan_id),
    ).fetchone()
    if enrollment is None:
        return
    cycle_id = cast(str, enrollment[0])
    authoritative = connection.execute(
        "UPDATE review_preparation_work AS authoritative SET "
        "presentation_recovery_cycle_id=?,"
        "presentation_recovery_stage=presentation.work_stage,"
        "presentation_recovery_outcome=presentation.work_outcome "
        "FROM review_preparation_presentation AS presentation "
        "WHERE authoritative.scan_id=? "
        "AND presentation.row_id='work:'||authoritative.work_id "
        "AND NOT (presentation.work_stage='extraction' AND presentation.work_outcome IS NULL)",
        (cycle_id, scan_id),
    )
    presentation = connection.execute(
        "UPDATE review_preparation_presentation SET "
        "recovery_high_water_stage=work_stage,recovery_high_water_outcome=work_outcome "
        "WHERE scan_id=? "
        "AND NOT (work_stage='extraction' AND work_outcome IS NULL)",
        (scan_id,),
    )
    if authoritative.rowcount != presentation.rowcount:
        raise MetadataReportError("review preparation persistence failed") from None


def _clear_preparation_recovery(
    connection: sqlite3.Connection, scan_id: str, cycle_id: str | None
) -> None:
    """Remove both halves of one exact cycle's recovery witness."""
    if cycle_id is None:
        return
    connection.execute(
        "UPDATE review_preparation_work SET presentation_recovery_cycle_id=NULL,"
        "presentation_recovery_stage=NULL,presentation_recovery_outcome=NULL "
        "WHERE scan_id=? AND presentation_recovery_cycle_id=?",
        (scan_id, cycle_id),
    )
    connection.execute(
        "UPDATE review_preparation_presentation SET recovery_high_water_stage=NULL,"
        "recovery_high_water_outcome=NULL WHERE row_kind='work' AND scan_id=? AND cycle_id=?",
        (scan_id, cycle_id),
    )


def _reconcile_terminal_preparation_recovery(
    connection: sqlite3.Connection, scan_id: str, cycle_id: str | None
) -> None:
    """Settle one witnessed cycle from terminal authority before clearing it."""
    if cycle_id is None:
        return
    if connection.execute(
        "SELECT 1 FROM review_preparation_work AS authoritative "
        "LEFT JOIN review_preparation_presentation AS presentation "
        "ON presentation.row_kind='work' AND presentation.work_id=authoritative.work_id "
        "AND presentation.scan_id=authoritative.scan_id AND presentation.cycle_id=? "
        "WHERE authoritative.scan_id=? AND authoritative.presentation_recovery_cycle_id=? "
        "AND presentation.row_id IS NULL LIMIT 1",
        (cycle_id, scan_id, cycle_id),
    ).fetchone():
        raise MetadataReportError("review preparation persistence failed") from None
    rows = connection.execute(
        "SELECT presentation.row_id,presentation.work_stage,presentation.work_outcome,"
        "presentation.completion_sequence,presentation.recovery_high_water_stage,"
        "presentation.recovery_high_water_outcome,authoritative.work_id,"
        "authoritative.scan_id,authoritative.stage,authoritative.state,"
        "authoritative.failure_code,authoritative.presentation_recovery_cycle_id,"
        "authoritative.presentation_recovery_stage,"
        "authoritative.presentation_recovery_outcome "
        "FROM review_preparation_presentation AS presentation "
        "LEFT JOIN review_preparation_work AS authoritative "
        "ON authoritative.work_id=presentation.work_id "
        "WHERE presentation.row_kind='work' AND presentation.scan_id=? "
        "AND presentation.cycle_id=? AND (presentation.recovery_high_water_stage IS NOT NULL "
        "OR authoritative.presentation_recovery_cycle_id IS NOT NULL "
        "OR authoritative.presentation_recovery_stage IS NOT NULL "
        "OR authoritative.presentation_recovery_outcome IS NOT NULL)",
        (scan_id, cycle_id),
    ).fetchall()
    ready_switches = 0
    failed_switches = 0
    for (
        row_id,
        work_stage,
        old_outcome,
        completion_sequence,
        marker_stage,
        marker_outcome,
        work_id,
        authoritative_scan_id,
        authoritative_stage,
        authoritative_state,
        failure_code,
        witness_cycle,
        witness_stage,
        witness_outcome,
    ) in rows:
        if (
            work_id is None
            or authoritative_scan_id != scan_id
            or witness_cycle != cycle_id
            or witness_stage != marker_stage
            or witness_outcome != marker_outcome
            or marker_stage != work_stage
            or marker_outcome != old_outcome
            or authoritative_state not in {"complete", "failed", "stale"}
            or old_outcome not in {"ready", "failed"}
            or completion_sequence is None
        ):
            raise MetadataReportError("review preparation persistence failed") from None
        observed_stage, observed_outcome = _presentation_observation(
            authoritative_stage, authoritative_state, failure_code
        )
        if observed_outcome not in {"ready", "failed"}:
            raise MetadataReportError("review preparation persistence failed") from None
        ready_switches += observed_outcome == "ready" and old_outcome != "ready"
        failed_switches += observed_outcome == "failed" and old_outcome != "failed"
        connection.execute(
            "UPDATE review_preparation_presentation SET work_stage=?,current_work_state=?,"
            "work_outcome=?,recovery_high_water_stage=NULL,"
            "recovery_high_water_outcome=NULL WHERE row_id=?",
            (
                max(
                    (work_stage, observed_stage),
                    key=lambda stage: _STAGE_ORDER[stage],
                ),
                authoritative_state,
                observed_outcome,
                row_id,
            ),
        )
        connection.execute(
            "UPDATE review_preparation_work SET presentation_recovery_cycle_id=NULL,"
            "presentation_recovery_stage=NULL,presentation_recovery_outcome=NULL "
            "WHERE work_id=? AND presentation_recovery_cycle_id=?",
            (work_id, cycle_id),
        )
    if ready_switches or failed_switches:
        connection.execute(
            "UPDATE review_preparation_presentation SET ready=MIN(total,ready+?),"
            "failed=MIN(total,failed+?) WHERE row_id='run'",
            (ready_switches, failed_switches),
        )


def _adopt_enrollment_states(connection: sqlite3.Connection, updated_at: int) -> None:
    rows = connection.execute(
        "SELECT enrollment.row_id,enrollment.scan_id,enrollment.cycle_id,enrollment.pending_cycle_id,"
        "enrollment.cycle_state,scan.state "
        "FROM review_preparation_presentation AS enrollment JOIN review_preparation_scans AS scan "
        "ON scan.scan_id=enrollment.scan_id WHERE enrollment.row_kind='enrollment'"
    ).fetchall()
    for row_id, scan_id, cycle_id, pending_cycle_id, cycle_state, scan_state in rows:
        if pending_cycle_id is not None and scan_state == "queued":
            _clear_preparation_recovery(connection, scan_id, cycle_id)
            connection.execute(
                "UPDATE review_preparation_presentation SET cycle_id=? "
                "WHERE row_kind='work' AND scan_id=? AND cycle_id=?",
                (pending_cycle_id, scan_id, cycle_id),
            )
            connection.execute(
                "UPDATE review_preparation_presentation SET cycle_id=?,pending_cycle_id=NULL,"
                "cycle_state='queued',updated_at=? WHERE row_id=?",
                (pending_cycle_id, updated_at, row_id),
            )
        elif pending_cycle_id is not None and scan_state in {
            "completed",
            "completed_with_failures",
        }:
            _reconcile_terminal_preparation_recovery(connection, scan_id, cycle_id)
            _clear_preparation_recovery(connection, scan_id, cycle_id)
            connection.execute(
                "UPDATE review_preparation_presentation SET pending_cycle_id=NULL,"
                "cycle_state=?,updated_at=? WHERE row_id=?",
                (scan_state, updated_at, row_id),
            )
        elif cycle_id is not None and cycle_state not in {
            "completed",
            "completed_with_failures",
        }:
            if scan_state in {"completed", "completed_with_failures"}:
                _reconcile_terminal_preparation_recovery(connection, scan_id, cycle_id)
                _clear_preparation_recovery(connection, scan_id, cycle_id)
            connection.execute(
                "UPDATE review_preparation_presentation SET cycle_state=?,updated_at=? WHERE row_id=?",
                (scan_state, updated_at, row_id),
            )


def _presentation_observation(
    stage: str, state: str, failure_code: str | None
) -> tuple[str, str | None]:
    report_only = failure_code == "local_evidence_only"
    return (
        "projection" if stage == "complete" or report_only else stage,
        "ready"
        if state == "complete" or report_only
        else "failed"
        if state in {"failed", "stale"}
        else None,
    )


def refresh_preparation_work(
    connection: sqlite3.Connection, work_id: str, updated_at: int
) -> None:
    """Advance one enrolled work's high water in a fixed number of queries."""
    presentation = connection.execute(
        "SELECT work.run_id,work.work_stage,work.work_outcome,work.updated_at,work.current_work_state,work.completion_sequence,"
        "work.recovery_high_water_stage,work.recovery_high_water_outcome,work.cycle_id "
        "FROM review_preparation_presentation AS work JOIN review_preparation_presentation AS enrollment "
        "ON enrollment.row_kind='enrollment' AND enrollment.scan_id=work.scan_id "
        "AND enrollment.cycle_id=work.cycle_id AND enrollment.cycle_state NOT IN ('completed','completed_with_failures') "
        "WHERE work.row_id=? AND work.row_kind='work'",
        ("work:" + work_id,),
    ).fetchone()
    if presentation is None:
        return
    authoritative = connection.execute(
        "SELECT stage,state,updated_at,failure_code,scan_id,presentation_recovery_cycle_id,"
        "presentation_recovery_stage,presentation_recovery_outcome "
        "FROM review_preparation_work WHERE work_id=?",
        (work_id,),
    ).fetchone()
    if authoritative is None:
        _delete_preparation_work_rows(
            connection, cast(str, presentation[0]), ("work:" + work_id,)
        )
        _refresh_run_state(connection, cast(str, presentation[0]), updated_at)
        return
    old_stage = cast(str, presentation[1])
    new_stage, observed_outcome = _presentation_observation(
        authoritative[0], authoritative[1], authoritative[3]
    )
    high_stage = max((old_stage, new_stage), key=lambda value: _STAGE_ORDER[value])
    old_outcome = cast(str | None, presentation[2])
    outcome = old_outcome or observed_outcome
    marker_stage, marker_outcome = presentation[6:8]
    witness_cycle, witness_stage, witness_outcome = authoritative[5:8]
    witnessed = witness_cycle is not None
    if (marker_stage is not None) != witnessed or (
        witnessed
        and (
            witness_cycle != presentation[8]
            or witness_stage != marker_stage
            or witness_outcome != marker_outcome
        )
    ):
        raise MetadataReportError("review preparation persistence failed") from None
    recovered = witnessed and not (
        _STAGE_ORDER[new_stage] < _STAGE_ORDER[marker_stage]
        or (marker_outcome is not None and observed_outcome != marker_outcome)
    )
    if recovered:
        connection.execute(
            "UPDATE review_preparation_work SET presentation_recovery_cycle_id=NULL,"
            "presentation_recovery_stage=NULL,presentation_recovery_outcome=NULL "
            "WHERE work_id=?",
            (work_id,),
        )
    elif witnessed:
        marker_stage, marker_outcome = high_stage, outcome
        connection.execute(
            "UPDATE review_preparation_work SET presentation_recovery_stage=?,"
            "presentation_recovery_outcome=? WHERE work_id=?",
            (marker_stage, marker_outcome, work_id),
        )
    outcome_at = presentation[3] if old_outcome is not None else authoritative[2]
    completion_sequence = presentation[5]
    if old_outcome is None and outcome is not None:
        completion_sequence = _next_completion_sequence(
            connection, cast(str, presentation[0])
        )
    old_rank = _STAGE_ORDER[old_stage]
    new_rank = _STAGE_ORDER[high_stage]
    increments = tuple(
        int(old_rank < threshold <= new_rank) for threshold in (1, 2, 3, 4)
    ) + (
        int(old_outcome != "ready" and outcome == "ready"),
        int(old_outcome != "failed" and outcome == "failed"),
    )
    connection.execute(
        "UPDATE review_preparation_presentation SET work_stage=?,current_work_state=?,work_outcome=?,updated_at=?,completion_sequence=?,"
        "recovery_high_water_stage=?,recovery_high_water_outcome=? "
        "WHERE row_id=?",
        (
            high_stage,
            authoritative[1],
            outcome,
            outcome_at,
            completion_sequence,
            None if recovered else marker_stage,
            None if recovered else marker_outcome,
            "work:" + work_id,
        ),
    )
    old_active = presentation[4] == "running"
    new_active = authoritative[1] == "running"
    connection.execute(
        "UPDATE review_preparation_presentation SET updated_at=?,active_count=active_count+?,read=read+?,"
        "searches_finished=searches_finished+?,compared=compared+?,checked=checked+?,"
        "ready=ready+?,failed=failed+?,completion_count=completion_count+? "
        "WHERE row_id='run' AND run_id=?",
        (
            updated_at,
            int(new_active) - int(old_active),
            *increments,
            int(old_outcome is None and outcome is not None),
            presentation[0],
        ),
    )
    _refresh_run_state(connection, cast(str, presentation[0]), updated_at)


def _refresh_preparation_presentation(
    connection: sqlite3.Connection, updated_at: int
) -> None:
    run = connection.execute(
        "SELECT run_id,state,read,searches_finished,compared,checked,ready,failed,total "
        "FROM review_preparation_presentation WHERE row_id='run'"
    ).fetchone()
    if run is None or run[1] in {state.value for state in TERMINAL_PREPARATION_STATES}:
        return
    run_id = cast(str, run[0])
    orphaned = tuple(
        row[0]
        for row in connection.execute(
            "SELECT presentation.row_id FROM review_preparation_presentation AS presentation "
            "WHERE presentation.row_kind='work' AND presentation.run_id=? AND NOT EXISTS("
            "SELECT 1 FROM review_preparation_work WHERE work_id=presentation.work_id)",
            (run_id,),
        )
    )
    _delete_preparation_work_rows(connection, run_id, orphaned)
    work_rows = connection.execute(
        "SELECT presentation.row_id,presentation.work_stage,presentation.work_outcome,presentation.updated_at,presentation.completion_sequence,"
        "presentation.recovery_high_water_stage,presentation.recovery_high_water_outcome,presentation.cycle_id,"
        "work.work_id,work.stage,work.state,work.updated_at,work.failure_code,"
        "work.presentation_recovery_cycle_id,work.presentation_recovery_stage,work.presentation_recovery_outcome "
        "FROM review_preparation_presentation AS presentation "
        "JOIN review_preparation_work AS work ON work.work_id=presentation.work_id "
        "JOIN review_preparation_presentation AS enrollment ON enrollment.row_kind='enrollment' "
        "AND enrollment.scan_id=presentation.scan_id AND enrollment.cycle_id=presentation.cycle_id "
        "AND enrollment.cycle_state NOT IN ('completed','completed_with_failures') "
        "WHERE presentation.row_kind='work' AND presentation.run_id=? "
        "ORDER BY work.updated_at,presentation.enrollment_ordinal,presentation.ordinal,presentation.work_id",
        (run_id,),
    ).fetchall()
    observed = [0, 0, 0, 0, 0, 0]
    completion_count = _completion_sequence_count(connection, run_id)
    new_completion_count = sum(
        old_outcome is None
        and _presentation_observation(current_stage, current_state, failure_code)[1]
        is not None
        for (
            _row_id,
            _old_stage,
            old_outcome,
            _old_updated_at,
            _completion_sequence,
            _marker_stage,
            _marker_outcome,
            _cycle_id,
            _work_id,
            current_stage,
            current_state,
            _work_updated_at,
            failure_code,
            _witness_cycle,
            _witness_stage,
            _witness_outcome,
        ) in work_rows
    )
    if new_completion_count > _SQLITE_INTEGER_MAXIMUM - 1 - completion_count:
        raise MetadataReportError("review preparation persistence failed") from None
    next_completion_sequence = completion_count + 1
    for (
        row_id,
        old_stage,
        old_outcome,
        old_updated_at,
        completion_sequence,
        marker_stage,
        marker_outcome,
        cycle_id,
        work_id,
        current_stage,
        current_state,
        work_updated_at,
        failure_code,
        witness_cycle,
        witness_stage,
        witness_outcome,
    ) in work_rows:
        current_stage, observed_outcome = _presentation_observation(
            current_stage, current_state, failure_code
        )
        stage = max((old_stage, current_stage), key=lambda value: _STAGE_ORDER[value])
        outcome = old_outcome or observed_outcome
        witnessed = witness_cycle is not None
        if (marker_stage is not None) != witnessed or (
            witnessed
            and (
                witness_cycle != cycle_id
                or witness_stage != marker_stage
                or witness_outcome != marker_outcome
            )
        ):
            raise MetadataReportError("review preparation persistence failed") from None
        recovered = witnessed and not (
            _STAGE_ORDER[current_stage] < _STAGE_ORDER[marker_stage]
            or (marker_outcome is not None and observed_outcome != marker_outcome)
        )
        if recovered:
            connection.execute(
                "UPDATE review_preparation_work SET presentation_recovery_cycle_id=NULL,"
                "presentation_recovery_stage=NULL,presentation_recovery_outcome=NULL "
                "WHERE work_id=?",
                (work_id,),
            )
        elif witnessed:
            marker_stage, marker_outcome = stage, outcome
            connection.execute(
                "UPDATE review_preparation_work SET presentation_recovery_stage=?,"
                "presentation_recovery_outcome=? WHERE work_id=?",
                (marker_stage, marker_outcome, work_id),
            )
        if old_outcome is None and outcome is not None:
            completion_sequence = next_completion_sequence
            next_completion_sequence += 1
            completion_count += 1
        connection.execute(
            "UPDATE review_preparation_presentation SET work_stage=?,current_work_state=?,work_outcome=?,updated_at=?,completion_sequence=?,"
            "recovery_high_water_stage=?,recovery_high_water_outcome=? WHERE row_id=?",
            (
                stage,
                current_state,
                outcome,
                old_updated_at if old_outcome is not None else work_updated_at,
                completion_sequence,
                None if recovered else marker_stage,
                None if recovered else marker_outcome,
                row_id,
            ),
        )
        rank = _STAGE_ORDER[stage]
        observed[0] += rank >= 1
        observed[1] += rank >= 2
        observed[2] += rank >= 3
        observed[3] += rank >= 4
        observed[4] += outcome == "ready"
        observed[5] += outcome == "failed"
    high_water = tuple(
        max(cast(int, run[index + 2]), observed[index]) for index in range(6)
    )
    active_count = sum(
        current_state == "running"
        for _row_id, _old_stage, outcome, _old_updated_at, _completion_sequence, _marker_stage, _marker_outcome, _cycle_id, _work_id, _current_stage, current_state, _work_updated_at, _failure_code, _witness_cycle, _witness_stage, _witness_outcome in work_rows
    )
    connection.execute(
        "UPDATE review_preparation_presentation SET total=?,read=?,searches_finished=?,"
        "compared=?,checked=?,ready=?,failed=?,active_count=?,completion_count=? "
        "WHERE row_id='run'",
        (
            max(cast(int, run[8]), len(work_rows)),
            *high_water,
            active_count,
            completion_count,
        ),
    )
    _refresh_run_state(connection, run_id, updated_at)


def _derived_run_state(
    connection: sqlite3.Connection, run_id: str, failed: int
) -> PreparationRunState:
    blocked = (
        connection.execute(
            "SELECT 1 FROM review_preparation_control WHERE singleton=1 AND paused=1 UNION ALL "
            "SELECT 1 FROM review_preparation_presentation AS deferred "
            "WHERE deferred.row_kind='deferred_enrollment' AND deferred.run_id=? UNION ALL "
            "SELECT 1 FROM review_preparation_connection_conditions AS condition "
            "WHERE condition.state IN ('paused','checking') AND EXISTS("
            "SELECT 1 FROM review_preparation_presentation AS enrollment "
            "WHERE enrollment.row_kind='enrollment' AND enrollment.run_id=? "
            "AND enrollment.connection_name=condition.connection_name "
            "AND (enrollment.pending_cycle_id IS NOT NULL OR "
            "enrollment.cycle_state NOT IN ('completed','completed_with_failures'))) LIMIT 1",
            (run_id, run_id),
        ).fetchone()
        is not None
    )
    terminal = (
        connection.execute(
            "SELECT COUNT(*) FROM review_preparation_presentation AS enrollment "
            "WHERE enrollment.row_kind='enrollment' AND enrollment.run_id=? "
            "HAVING COUNT(*)>0 AND SUM(enrollment.pending_cycle_id IS NOT NULL OR "
            "enrollment.cycle_state NOT IN ('completed','completed_with_failures'))=0 "
            "AND NOT EXISTS(SELECT 1 FROM review_preparation_presentation AS deferred "
            "WHERE deferred.row_kind='deferred_enrollment' AND deferred.run_id=?)",
            (run_id, run_id),
        ).fetchone()
        is not None
    )
    if terminal:
        return (
            PreparationRunState.COMPLETED_WITH_FAILURES
            if failed
            else PreparationRunState.COMPLETED_CLEAN
        )
    if blocked:
        return PreparationRunState.BLOCKED
    if (
        connection.execute(
            "SELECT 1 FROM review_preparation_presentation AS enrollment "
            "WHERE enrollment.row_kind='enrollment' AND enrollment.run_id=? "
            "AND enrollment.cycle_state IN ('discovering','processing') LIMIT 1",
            (run_id,),
        ).fetchone()
        is not None
    ):
        return PreparationRunState.RUNNING
    return PreparationRunState.STARTING


def _refresh_run_state(
    connection: sqlite3.Connection, run_id: str, updated_at: int
) -> None:
    run = connection.execute(
        "SELECT state,completed_at,failed FROM review_preparation_presentation "
        "WHERE row_id='run' AND run_id=?",
        (run_id,),
    ).fetchone()
    if run is None or run[0] in {state.value for state in TERMINAL_PREPARATION_STATES}:
        return
    state = _derived_run_state(connection, run_id, cast(int, run[2]))
    completed_at = (
        run[1]
        if run[1] is not None
        else updated_at
        if state
        in {
            PreparationRunState.COMPLETED_CLEAN,
            PreparationRunState.COMPLETED_WITH_FAILURES,
        }
        else None
    )
    connection.execute(
        "UPDATE review_preparation_presentation SET state=?,updated_at=?,completed_at=? "
        "WHERE row_id='run' AND run_id=?",
        (state.value, updated_at, completed_at, run_id),
    )


def preparation_presentation_snapshot(
    connection: sqlite3.Connection, now: int
) -> PreparationPresentation | None:
    transaction = TransactionScope(
        connection, "preparation_snapshot", savepoint_only=True
    )
    transaction.begin()
    try:
        row = connection.execute(
            "SELECT run_id,state,started_at,updated_at,completed_at,total,active_count,"
            "read,searches_finished,compared,checked,ready,failed FROM review_preparation_presentation WHERE row_id='run'"
        ).fetchone()
        if row is None:
            transaction.commit()
            return None
        run_id = cast(str, row[0])
        triggers = tuple(
            PreparationTriggerRecord(
                PreparationTrigger(trigger), count, first_at, last_at
            )
            for trigger, count, first_at, last_at in connection.execute(
                "SELECT trigger,accepted_count,first_accepted_at,last_accepted_at "
                "FROM review_preparation_presentation WHERE row_kind='trigger' AND run_id=? ORDER BY trigger_ordinal",
                (run_id,),
            )
        )
        active_rows = connection.execute(
            "SELECT presentation.display_name,authoritative.stage FROM review_preparation_presentation AS presentation "
            "INDEXED BY review_preparation_presentation_active CROSS JOIN review_preparation_work AS authoritative "
            "ON authoritative.work_id=presentation.work_id "
            "WHERE presentation.row_kind='work' AND presentation.run_id=? "
            "AND presentation.current_work_state='running' AND authoritative.state='running' "
            "ORDER BY presentation.enrollment_ordinal,presentation.ordinal,presentation.work_id LIMIT 5",
            (run_id,),
        ).fetchall()
        active = tuple(
            PreparationActiveBook(name, _STAGE_COPY.get(stage, "Preparing review"))
            for name, stage in active_rows
        )
        completion_rows = connection.execute(
            "SELECT display_name,work_outcome,updated_at FROM review_preparation_presentation "
            "WHERE row_kind='work' AND run_id=? AND work_outcome IS NOT NULL "
            "ORDER BY completion_sequence DESC LIMIT 8",
            (run_id,),
        ).fetchall()
        started_at = cast(int, row[2])
        window_start = max(started_at, now - 300)
        completed_count = connection.execute(
            "SELECT COUNT(*) FROM review_preparation_presentation WHERE row_kind='work' "
            "AND run_id=? AND work_outcome IS NOT NULL AND updated_at>=?",
            (run_id, window_start),
        ).fetchone()[0]
        result = PreparationPresentation(
            run_id=run_id,
            state=PreparationRunState(row[1]),
            triggers=triggers,
            passage=PreparationPassage(*row[7:13]),
            active_books=active,
            active_overflow=max(cast(int, row[6]) - len(active), 0),
            recent_completions=tuple(
                PreparationCompletion(*item) for item in completion_rows
            ),
            throughput_per_minute=round(
                completed_count / max((now - window_start) / 60, 1 / 60), 1
            ),
            started_at=started_at,
            updated_at=row[3],
            completed_at=row[4],
            total=row[5],
        )
        transaction.commit()
        return result
    except BaseException as error:
        transaction.rollback()
        raise error from None


def dismiss_preparation_presentation(
    connection: sqlite3.Connection, run_id: str, _dismissed_at: int
) -> None:
    current = connection.execute(
        "SELECT run_id,state FROM review_preparation_presentation WHERE row_id='run'"
    ).fetchone()
    if current is None or current[0] != run_id:
        raise PreparationPresentationError("stale")
    if current[1] not in {
        PreparationRunState.COMPLETED_CLEAN.value,
        PreparationRunState.COMPLETED_WITH_FAILURES.value,
    }:
        raise PreparationPresentationError("not_dismissible")
    connection.execute(
        "DELETE FROM review_preparation_presentation WHERE run_id=?", (run_id,)
    )


def validate_preparation_presentation(connection: sqlite3.Connection) -> None:
    preparation_run_identity = prefixed_hex_identity("preparation_run_")
    review_scan_identity = prefixed_hex_identity("review_scan_")
    preparation_cycle_identity = prefixed_hex_identity("preparation_cycle_")
    review_work_identity = prefixed_hex_identity("review_work_")
    preparation_target_name = bounded_utf8_text(128)
    run_count = connection.execute(
        "SELECT COUNT(*) FROM review_preparation_presentation WHERE row_kind='run'"
    ).fetchone()[0]
    total = connection.execute(
        "SELECT COUNT(*) FROM review_preparation_presentation"
    ).fetchone()[0]
    witnessed_authority = connection.execute(
        "SELECT 1 FROM review_preparation_work WHERE "
        "presentation_recovery_cycle_id IS NOT NULL OR "
        "presentation_recovery_stage IS NOT NULL OR "
        "presentation_recovery_outcome IS NOT NULL LIMIT 1"
    ).fetchone()
    if run_count not in {0, 1} or (run_count == 0 and (total or witnessed_authority)):
        raise MetadataReportError("database is unavailable or incompatible")
    if connection.execute(
        "SELECT 1 FROM review_preparation_work AS authoritative "
        "LEFT JOIN review_preparation_presentation AS presentation "
        "ON presentation.row_kind='work' AND presentation.work_id=authoritative.work_id "
        "AND presentation.scan_id=authoritative.scan_id "
        "AND presentation.cycle_id=authoritative.presentation_recovery_cycle_id "
        "WHERE (authoritative.presentation_recovery_cycle_id IS NOT NULL "
        "OR authoritative.presentation_recovery_stage IS NOT NULL "
        "OR authoritative.presentation_recovery_outcome IS NOT NULL) "
        "AND presentation.row_id IS NULL LIMIT 1"
    ).fetchone():
        raise MetadataReportError("database is unavailable or incompatible")
    if run_count:
        try:
            (
                run_id,
                stored_run_state,
                started_at,
                run_updated_at,
                completed_at,
                total_books,
                active_count,
                read,
                searches_finished,
                compared,
                checked,
                ready,
                failed,
                completion_count,
            ) = connection.execute(
                "SELECT run_id,state,started_at,updated_at,completed_at,total,active_count,"
                "read,searches_finished,compared,checked,ready,failed,completion_count "
                "FROM review_preparation_presentation WHERE row_id='run'"
            ).fetchone()
            if stored_run_state == PreparationRunState.DISMISSED.value:
                raise ValueError("persisted dismissed presentation")
            if connection.execute(
                "SELECT COUNT(*) FROM review_preparation_presentation WHERE run_id<>?",
                (run_id,),
            ).fetchone()[0]:
                raise ValueError("foreign presentation run")
            common = {"row_id", "row_kind", "run_id", "updated_at"}
            allowed = {
                "run": common
                | {
                    "state",
                    "started_at",
                    "completed_at",
                    "total",
                    "active_count",
                    "read",
                    "searches_finished",
                    "compared",
                    "checked",
                    "ready",
                    "failed",
                    "completion_count",
                },
                "trigger": common
                | {
                    "trigger",
                    "trigger_ordinal",
                    "accepted_count",
                    "first_accepted_at",
                    "last_accepted_at",
                },
                "enrollment": common
                | {
                    "scan_id",
                    "connection_name",
                    "library_name",
                    "cycle_id",
                    "pending_cycle_id",
                    "cycle_state",
                    "ordinal",
                },
                "deferred_enrollment": common
                | {
                    "connection_name",
                    "library_name",
                    "ordinal",
                    "accepted_at",
                    "settings_revision",
                },
                "work": common
                | {
                    "scan_id",
                    "cycle_id",
                    "work_id",
                    "ordinal",
                    "enrollment_ordinal",
                    "display_name",
                    "work_stage",
                    "current_work_state",
                    "work_outcome",
                    "recovery_high_water_stage",
                    "recovery_high_water_outcome",
                    "completion_sequence",
                },
            }
            columns = tuple(
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(review_preparation_presentation)"
                )
            )
            for row in connection.execute(
                "SELECT * FROM review_preparation_presentation"
            ):
                values = dict(zip(columns, row, strict=True))
                row_kind = values["row_kind"]
                relevant = allowed.get(row_kind)
                if relevant is None:
                    raise ValueError("invalid presentation row kind")
                if any(
                    value is not None and column not in relevant
                    for column, value in zip(columns, row, strict=True)
                ):
                    raise ValueError("irrelevant presentation columns")
                if (
                    type(values["row_id"]) is not str
                    or type(row_kind) is not str
                    or not preparation_run_identity(values["run_id"])
                    or not non_negative_integer(values["updated_at"])
                    or (
                        row_kind != "run"
                        and not started_at <= values["updated_at"] <= run_updated_at
                    )
                ):
                    raise ValueError("invalid presentation common fields")
                if row_kind == "run":
                    counters = tuple(
                        values[name]
                        for name in (
                            "total",
                            "active_count",
                            "read",
                            "searches_finished",
                            "compared",
                            "checked",
                            "ready",
                            "failed",
                            "completion_count",
                        )
                    )
                    if (
                        values["row_id"] != "run"
                        or type(values["state"]) is not str
                        or values["state"]
                        not in {state.value for state in PreparationRunState}
                        or not non_negative_integer(values["started_at"])
                        or any(not non_negative_integer(value) for value in counters)
                        or values["updated_at"] < values["started_at"]
                        or (
                            (
                                values["state"]
                                in {
                                    state.value for state in TERMINAL_PREPARATION_STATES
                                }
                            )
                            != (values["completed_at"] is not None)
                        )
                        or (
                            values["completed_at"] is not None
                            and (
                                not non_negative_integer(values["completed_at"])
                                or values["completed_at"] < values["started_at"]
                                or values["completed_at"] > values["updated_at"]
                            )
                        )
                    ):
                        raise ValueError("invalid presentation run")
                elif row_kind == "trigger":
                    if (
                        type(values["trigger"]) is not str
                        or values["trigger"]
                        not in {trigger.value for trigger in PreparationTrigger}
                        or type(values["trigger_ordinal"]) is not int
                        or not 0 <= values["trigger_ordinal"] < len(PreparationTrigger)
                        or type(values["accepted_count"]) is not int
                        or values["accepted_count"] <= 0
                        or not non_negative_integer(values["first_accepted_at"])
                        or not non_negative_integer(values["last_accepted_at"])
                        or not values["first_accepted_at"]
                        <= values["last_accepted_at"]
                        <= values["updated_at"]
                        or values["first_accepted_at"] < started_at
                        or (
                            completed_at is not None
                            and values["last_accepted_at"] > completed_at
                        )
                    ):
                        raise ValueError("invalid presentation trigger")
                elif row_kind == "enrollment":
                    if (
                        not review_scan_identity(values["scan_id"])
                        or not preparation_target_name(values["connection_name"])
                        or not preparation_target_name(values["library_name"])
                        or (
                            values["cycle_id"] is not None
                            and not preparation_cycle_identity(values["cycle_id"])
                        )
                        or (
                            values["pending_cycle_id"] is not None
                            and not preparation_cycle_identity(
                                values["pending_cycle_id"]
                            )
                        )
                        or values["cycle_id"] == values["pending_cycle_id"]
                        or type(values["cycle_state"]) is not str
                        or values["cycle_state"]
                        not in {
                            "queued",
                            "discovering",
                            "processing",
                            "completed",
                            "completed_with_failures",
                        }
                        or not non_negative_integer(values["ordinal"])
                    ):
                        raise ValueError("invalid presentation enrollment")
                elif row_kind == "deferred_enrollment":
                    if (
                        not preparation_target_name(values["connection_name"])
                        or not preparation_target_name(values["library_name"])
                        or not non_negative_integer(values["ordinal"])
                        or not non_negative_integer(values["accepted_at"])
                        or not started_at
                        <= values["accepted_at"]
                        <= values["updated_at"]
                        or type(values["settings_revision"]) is not int
                        or values["settings_revision"] <= 0
                    ):
                        raise ValueError("invalid deferred enrollment")
                elif (
                    not review_scan_identity(values["scan_id"])
                    or not preparation_cycle_identity(values["cycle_id"])
                    or not review_work_identity(values["work_id"])
                    or not non_negative_integer(values["ordinal"])
                    or not non_negative_integer(values["enrollment_ordinal"])
                    or not _preparation_display_name(values["display_name"])
                    or type(values["work_stage"]) is not str
                    or values["work_stage"] not in _STAGE_ORDER
                    or type(values["current_work_state"]) is not str
                    or values["current_work_state"]
                    not in {
                        "queued",
                        "delayed",
                        "running",
                        "complete",
                        "failed",
                        "stale",
                    }
                    or (
                        values["work_outcome"] is not None
                        and type(values["work_outcome"]) is not str
                    )
                    or values["work_outcome"] not in {None, "ready", "failed"}
                    or values["recovery_high_water_stage"]
                    not in {
                        None,
                        *tuple(_STAGE_ORDER),
                    }
                    or values["recovery_high_water_outcome"]
                    not in {None, "ready", "failed"}
                    or (
                        values["recovery_high_water_stage"] is None
                        and values["recovery_high_water_outcome"] is not None
                    )
                    or (
                        completed_at is not None and values["updated_at"] > completed_at
                    )
                    or (
                        (values["work_outcome"] is None)
                        != (values["completion_sequence"] is None)
                    )
                    or (
                        values["completion_sequence"] is not None
                        and (
                            type(values["completion_sequence"]) is not int
                            or values["completion_sequence"] <= 0
                        )
                    )
                ):
                    raise ValueError("invalid presentation work")
            enrollments = connection.execute(
                "SELECT scan_id,cycle_id,pending_cycle_id,ordinal,row_id,connection_name,library_name,"
                "cycle_state,updated_at "
                "FROM review_preparation_presentation "
                "WHERE row_kind='enrollment' ORDER BY ordinal"
            ).fetchall()
            deferred = connection.execute(
                "SELECT connection_name,library_name,ordinal,row_id,accepted_at,settings_revision "
                "FROM review_preparation_presentation "
                "WHERE row_kind='deferred_enrollment' ORDER BY ordinal"
            ).fetchall()
            ordinals = sorted(
                [row[3] for row in enrollments] + [row[2] for row in deferred]
            )
            if ordinals != list(range(len(ordinals))):
                raise ValueError("noncanonical enrollment ordinals")
            if any(
                type(connection_name) is not str
                or type(library_name) is not str
                or not 1 <= len(connection_name) <= 128
                or not 1 <= len(library_name) <= 128
                or row_id
                != deferred_preparation_enrollment_id(connection_name, library_name)
                for connection_name, library_name, _ordinal, row_id, _accepted_at, _settings_revision in deferred
            ):
                raise ValueError("noncanonical deferred enrollment")
            deferred_targets = {(row[0], row[1]) for row in deferred}
            if deferred:
                settings = connection.execute(
                    "SELECT revision,document FROM owner_settings WHERE singleton=1"
                ).fetchone()
                if settings is None:
                    raise ValueError("missing deferred settings authority")
                configured_targets = set(
                    connection.execute(
                        "SELECT json_extract(value,'$.connection'),json_extract(value,'$.name') "
                        "FROM owner_settings,json_each(owner_settings.document,'$.libraries') "
                        "WHERE owner_settings.singleton=1"
                    )
                )
                if any(
                    target not in configured_targets or row[5] > settings[0]
                    for row in deferred
                    for target in ((row[0], row[1]),)
                ):
                    raise ValueError("invalid deferred settings authority")
            nonterminal_enrolled_targets = {
                (row[5], row[6])
                for row in enrollments
                if row[7] not in {"completed", "completed_with_failures"}
                or row[2] is not None
            }
            active_scan_targets = set(
                connection.execute(
                    "SELECT connection_name,library_name FROM review_preparation_scans "
                    "WHERE state IN ('queued','discovering','processing')"
                )
            )
            if (
                deferred_targets & active_scan_targets
                or deferred_targets & nonterminal_enrolled_targets
            ):
                raise ValueError("overlapping deferred enrollment")
            cycle_ids = [
                cycle_id
                for row in enrollments
                for cycle_id in row[1:3]
                if cycle_id is not None
            ]
            if len(cycle_ids) != len(set(cycle_ids)):
                raise ValueError("noncanonical presentation cycles")
            completion_sequences = sorted(
                row[0]
                for row in connection.execute(
                    "SELECT completion_sequence FROM review_preparation_presentation "
                    "WHERE row_kind='work' AND completion_sequence IS NOT NULL"
                )
            )
            if (
                any(type(value) is not int for value in completion_sequences)
                or completion_sequences != list(range(1, len(completion_sequences) + 1))
                or completion_count != len(completion_sequences)
                or completion_count >= _SQLITE_INTEGER_MAXIMUM
            ):
                raise ValueError("noncanonical completion sequence")
            historical_cycles: set[tuple[str, str]] = set()
            for (
                scan_id,
                cycle_id,
                pending_cycle_id,
                _ordinal,
                row_id,
                connection_name,
                library_name,
                cycle_state,
                _enrollment_updated_at,
            ) in enrollments:
                if (
                    row_id
                    != "enrollment:"
                    + hashlib.sha256(cast(str, scan_id).encode()).hexdigest()
                ):
                    raise ValueError("noncanonical enrollment identity")
                target = connection.execute(
                    "SELECT connection_name,library_name,state,rescan_requested,updated_at "
                    "FROM review_preparation_scans "
                    "WHERE scan_id=?",
                    (scan_id,),
                ).fetchone()
                if target is None or target[:2] != (connection_name, library_name):
                    raise ValueError("orphan enrollment")
                scan_state, rescan_requested, scan_updated_at = target[2:]
                if pending_cycle_id is not None:
                    if (
                        scan_state not in {"discovering", "processing"}
                        or rescan_requested != 1
                        or (cycle_id is None and cycle_state != "queued")
                        or (cycle_id is not None and cycle_state != scan_state)
                    ):
                        raise ValueError("invalid pending presentation cycle")
                elif cycle_state in {"completed", "completed_with_failures"}:
                    if cycle_state != scan_state and not (
                        _enrollment_updated_at < scan_updated_at
                    ):
                        raise ValueError("invalid terminal presentation cycle")
                    if cycle_state != scan_state:
                        historical_cycles.add((scan_id, cycle_id))
                elif (
                    cycle_state
                    not in {
                        "queued": {"queued"},
                        "discovering": {"discovering"},
                        "processing": {"discovering", "processing"},
                        "completed": {"completed"},
                        "completed_with_failures": {"completed_with_failures"},
                    }[scan_state]
                ):
                    raise ValueError("invalid presentation cycle state")
            trigger_rows = connection.execute(
                "SELECT trigger,trigger_ordinal FROM review_preparation_presentation "
                "WHERE row_kind='trigger' ORDER BY trigger_ordinal"
            ).fetchall()
            if any(
                row_id != f"trigger:{trigger}"
                or ordinal
                != tuple(PreparationTrigger).index(PreparationTrigger(trigger))
                for trigger, ordinal, row_id in connection.execute(
                    "SELECT trigger,trigger_ordinal,row_id FROM review_preparation_presentation WHERE row_kind='trigger'"
                )
            ) or len({row[0] for row in trigger_rows}) != len(trigger_rows):
                raise ValueError("noncanonical trigger summaries")
            enrollment_cycles = {
                (row[0], row[1]): (
                    row[3],
                    row[8],
                    (row[0], row[1]) in historical_cycles,
                )
                for row in enrollments
                if row[1] is not None
            }
            retained_passage = [0, 0, 0, 0, 0, 0]
            work_count = 0
            for (
                scan_id,
                cycle_id,
                work_id,
                ordinal,
                enrollment_ordinal,
                row_id,
                display_name,
                work_stage,
                current_state,
                work_outcome,
                work_updated_at,
                recovery_stage,
                recovery_outcome,
                authoritative_scan_id,
                authoritative_ordinal,
                authoritative_stage,
                authoritative_state,
                _authoritative_updated_at,
                authoritative_failure_code,
                witness_cycle,
                witness_stage,
                witness_outcome,
            ) in connection.execute(
                "SELECT presentation.scan_id,presentation.cycle_id,presentation.work_id,"
                "presentation.ordinal,presentation.enrollment_ordinal,presentation.row_id,"
                "presentation.display_name,presentation.work_stage,"
                "presentation.current_work_state,presentation.work_outcome,"
                "presentation.updated_at,presentation.recovery_high_water_stage,"
                "presentation.recovery_high_water_outcome,authoritative.scan_id,"
                "authoritative.ordinal,authoritative.stage,authoritative.state,"
                "authoritative.updated_at,authoritative.failure_code,"
                "authoritative.presentation_recovery_cycle_id,"
                "authoritative.presentation_recovery_stage,"
                "authoritative.presentation_recovery_outcome "
                "FROM review_preparation_presentation AS presentation "
                "LEFT JOIN review_preparation_work AS authoritative "
                "ON authoritative.work_id=presentation.work_id "
                "WHERE presentation.row_kind='work'"
            ):
                enrollment = enrollment_cycles.get((scan_id, cycle_id))
                if enrollment is None or enrollment[0] != enrollment_ordinal:
                    raise ValueError("orphan presentation work cycle")
                if row_id != "work:" + cast(str, work_id):
                    raise ValueError("noncanonical presentation work identity")
                if authoritative_scan_id is None:
                    retained_state = "complete" if work_outcome == "ready" else "failed"
                    if (
                        work_outcome is None
                        or current_state != retained_state
                        or recovery_stage is not None
                    ):
                        raise ValueError("orphan authoritative work")
                elif (authoritative_scan_id, authoritative_ordinal) != (
                    scan_id,
                    ordinal,
                ) or (authoritative_state != current_state and not enrollment[2]):
                    raise ValueError("noncanonical presentation work")
                if authoritative_scan_id is not None:
                    observed_stage, observed_outcome = _presentation_observation(
                        authoritative_stage,
                        authoritative_state,
                        authoritative_failure_code,
                    )
                    sticky_observation = (
                        enrollment[2] or work_updated_at < enrollment[1]
                    )
                    presentation_rank = _STAGE_ORDER[work_stage]
                    observed_rank = _STAGE_ORDER[observed_stage]
                    recovery = recovery_stage is not None
                    witnessed = witness_cycle is not None
                    if (
                        (witness_cycle is None) != (witness_stage is None)
                        or (witness_cycle is None and witness_outcome is not None)
                        or recovery != witnessed
                        or (
                            witnessed
                            and (
                                enrollment[2]
                                or not preparation_cycle_identity(witness_cycle)
                                or witness_cycle != cycle_id
                                or type(witness_stage) is not str
                                or witness_stage not in _STAGE_ORDER
                                or witness_stage != recovery_stage
                                or (
                                    witness_outcome is not None
                                    and type(witness_outcome) is not str
                                )
                                or witness_outcome not in {None, "ready", "failed"}
                                or witness_outcome != recovery_outcome
                                or recovery_stage != work_stage
                                or recovery_outcome != work_outcome
                                or current_state != authoritative_state
                                or not (
                                    _STAGE_ORDER[observed_stage]
                                    < _STAGE_ORDER[recovery_stage]
                                    or (
                                        recovery_outcome is not None
                                        and observed_outcome != recovery_outcome
                                    )
                                )
                            )
                        )
                    ):
                        raise ValueError("invalid preparation recovery high water")
                    if (
                        (presentation_rank < observed_rank and not enrollment[2])
                        or (
                            presentation_rank > observed_rank
                            and not sticky_observation
                            and not recovery
                        )
                        or (
                            work_outcome != observed_outcome
                            and not (
                                work_outcome is not None
                                and (sticky_observation or recovery)
                            )
                        )
                    ):
                        raise ValueError("invalid presentation work observation")
                elif recovery_stage is not None:
                    raise ValueError("orphan preparation recovery high water")
                if not _preparation_display_name(display_name):
                    raise ValueError("invalid presentation display name")
                rank = _STAGE_ORDER[work_stage]
                retained_passage[0] += rank >= 1
                retained_passage[1] += rank >= 2
                retained_passage[2] += rank >= 3
                retained_passage[3] += rank >= 4
                retained_passage[4] += work_outcome == "ready"
                retained_passage[5] += work_outcome == "failed"
                work_count += 1
            if (
                any(
                    actual < minimum
                    for actual, minimum in zip(
                        (read, searches_finished, compared, checked, ready, failed),
                        retained_passage,
                        strict=True,
                    )
                )
                or total_books < work_count
            ):
                raise ValueError("invalid presentation high water")
            observed_active = connection.execute(
                "SELECT COUNT(*) FROM review_preparation_presentation WHERE row_kind='work' "
                "AND current_work_state='running'"
            ).fetchone()[0]
            if active_count != observed_active:
                raise ValueError("invalid active count")
            derived_state = _derived_run_state(connection, run_id, failed)
            if (
                stored_run_state == PreparationRunState.DISMISSED.value
                and derived_state
                not in {
                    PreparationRunState.COMPLETED_CLEAN,
                    PreparationRunState.COMPLETED_WITH_FAILURES,
                }
            ) or (
                stored_run_state != PreparationRunState.DISMISSED.value
                and stored_run_state != derived_state.value
            ):
                raise ValueError("invalid presentation run state")
            preparation_presentation_snapshot(connection, 0)
        except (
            PreparationPresentationError,
            TypeError,
            ValueError,
            sqlite3.Error,
        ):
            invalid = True
        else:
            invalid = False
        if invalid:
            raise MetadataReportError(
                "database is unavailable or incompatible"
            ) from None
