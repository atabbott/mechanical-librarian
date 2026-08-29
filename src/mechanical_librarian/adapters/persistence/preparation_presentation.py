"""One bounded owner-facing projection over authoritative preparation rows."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING, cast

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
            "row_id,row_kind,run_id,state,started_at,updated_at,total,active_count,read,searches_finished,compared,checked,ready,failed) "
            "VALUES('run','run',?,'starting',?,?,0,0,0,0,0,0,0,0)",
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
            "row_id,row_kind,run_id,connection_name,library_name,ordinal,updated_at) "
            "VALUES(?,'deferred_enrollment',?,?,?,?,?)",
            (row_id, run_id, connection_name, library_name, ordinal, accepted_at),
        )
        _record_preparation_trigger(
            connection, run_id, PreparationTrigger.LIBRARY_ADDED, accepted_at
        )
    _refresh_run_state(connection, run_id, accepted_at)


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
    for (presentation_work_id,) in connection.execute(
        "SELECT work_id FROM review_preparation_presentation WHERE row_kind='work' "
        "AND scan_id=? AND cycle_id=?",
        (scan_id, cycle_id),
    ).fetchall():
        if presentation_work_id not in current_work_ids:
            connection.execute(
                "DELETE FROM review_preparation_presentation WHERE row_id=?",
                ("work:" + presentation_work_id,),
            )
    for work_id, item_id, ordinal, current_state in rows:
        entry = selected.get(item_id)
        if entry is None:
            continue
        connection.execute(
            "INSERT INTO review_preparation_presentation("
            "row_id,row_kind,run_id,scan_id,cycle_id,work_id,ordinal,enrollment_ordinal,display_name,work_stage,current_work_state,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(row_id) DO UPDATE SET "
            "run_id=excluded.run_id,scan_id=excluded.scan_id,cycle_id=excluded.cycle_id,ordinal=excluded.ordinal,"
            "enrollment_ordinal=excluded.enrollment_ordinal,display_name=excluded.display_name,"
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


def _adopt_enrollment_states(connection: sqlite3.Connection, updated_at: int) -> None:
    rows = connection.execute(
        "SELECT enrollment.row_id,enrollment.scan_id,enrollment.cycle_id,enrollment.pending_cycle_id,"
        "enrollment.cycle_state,scan.state "
        "FROM review_preparation_presentation AS enrollment JOIN review_preparation_scans AS scan "
        "ON scan.scan_id=enrollment.scan_id WHERE enrollment.row_kind='enrollment'"
    ).fetchall()
    for row_id, scan_id, cycle_id, pending_cycle_id, cycle_state, scan_state in rows:
        if pending_cycle_id is not None and scan_state == "queued":
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
            connection.execute(
                "UPDATE review_preparation_presentation SET pending_cycle_id=NULL,"
                "cycle_state=?,updated_at=? WHERE row_id=?",
                (scan_state, updated_at, row_id),
            )
        elif cycle_id is not None and cycle_state not in {
            "completed",
            "completed_with_failures",
        }:
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
        "SELECT work.run_id,work.work_stage,work.work_outcome,work.updated_at,work.current_work_state "
        "FROM review_preparation_presentation AS work JOIN review_preparation_presentation AS enrollment "
        "ON enrollment.row_kind='enrollment' AND enrollment.scan_id=work.scan_id "
        "AND enrollment.cycle_id=work.cycle_id AND enrollment.cycle_state NOT IN ('completed','completed_with_failures') "
        "WHERE work.row_id=? AND work.row_kind='work'",
        ("work:" + work_id,),
    ).fetchone()
    if presentation is None:
        return
    authoritative = connection.execute(
        "SELECT stage,state,updated_at,failure_code FROM review_preparation_work WHERE work_id=?",
        (work_id,),
    ).fetchone()
    if authoritative is None:
        connection.execute(
            "DELETE FROM review_preparation_presentation WHERE row_id=?",
            ("work:" + work_id,),
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
    outcome_at = presentation[3] if old_outcome is not None else authoritative[2]
    old_rank = _STAGE_ORDER[old_stage]
    new_rank = _STAGE_ORDER[high_stage]
    increments = tuple(
        int(old_rank < threshold <= new_rank) for threshold in (1, 2, 3, 4)
    ) + (
        int(old_outcome != "ready" and outcome == "ready"),
        int(old_outcome != "failed" and outcome == "failed"),
    )
    connection.execute(
        "UPDATE review_preparation_presentation SET work_stage=?,current_work_state=?,work_outcome=?,updated_at=? "
        "WHERE row_id=?",
        (high_stage, authoritative[1], outcome, outcome_at, "work:" + work_id),
    )
    old_active = presentation[4] == "running"
    new_active = authoritative[1] == "running"
    connection.execute(
        "UPDATE review_preparation_presentation SET updated_at=?,active_count=active_count+?,read=read+?,"
        "searches_finished=searches_finished+?,compared=compared+?,checked=checked+?,"
        "ready=ready+?,failed=failed+? WHERE row_id='run' AND run_id=?",
        (updated_at, int(new_active) - int(old_active), *increments, presentation[0]),
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
    connection.execute(
        "DELETE FROM review_preparation_presentation AS presentation "
        "WHERE row_kind='work' AND run_id=? AND NOT EXISTS("
        "SELECT 1 FROM review_preparation_work WHERE work_id=presentation.work_id)",
        (run_id,),
    )
    work_rows = connection.execute(
        "SELECT presentation.row_id,presentation.work_stage,presentation.work_outcome,presentation.updated_at,"
        "work.stage,work.state,work.updated_at,work.failure_code FROM review_preparation_presentation AS presentation "
        "JOIN review_preparation_work AS work ON work.work_id=presentation.work_id "
        "JOIN review_preparation_presentation AS enrollment ON enrollment.row_kind='enrollment' "
        "AND enrollment.scan_id=presentation.scan_id AND enrollment.cycle_id=presentation.cycle_id "
        "AND enrollment.cycle_state NOT IN ('completed','completed_with_failures') "
        "WHERE presentation.row_kind='work' AND presentation.run_id=?",
        (run_id,),
    ).fetchall()
    observed = [0, 0, 0, 0, 0, 0]
    for (
        row_id,
        old_stage,
        old_outcome,
        old_updated_at,
        current_stage,
        current_state,
        work_updated_at,
        failure_code,
    ) in work_rows:
        current_stage, observed_outcome = _presentation_observation(
            current_stage, current_state, failure_code
        )
        stage = max((old_stage, current_stage), key=lambda value: _STAGE_ORDER[value])
        outcome = old_outcome or observed_outcome
        connection.execute(
            "UPDATE review_preparation_presentation SET work_stage=?,current_work_state=?,work_outcome=?,updated_at=? WHERE row_id=?",
            (
                stage,
                current_state,
                outcome,
                old_updated_at if old_outcome is not None else work_updated_at,
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
        for _row_id, _old_stage, outcome, _old_updated_at, _current_stage, current_state, _work_updated_at, _failure_code in work_rows
    )
    connection.execute(
        "UPDATE review_preparation_presentation SET total=?,read=?,searches_finished=?,"
        "compared=?,checked=?,ready=?,failed=?,active_count=? WHERE row_id='run'",
        (max(cast(int, run[8]), len(work_rows)), *high_water, active_count),
    )
    _refresh_run_state(connection, run_id, updated_at)


def _refresh_run_state(
    connection: sqlite3.Connection, run_id: str, updated_at: int
) -> None:
    run = connection.execute(
        "SELECT state,completed_at FROM review_preparation_presentation "
        "WHERE row_id='run' AND run_id=?",
        (run_id,),
    ).fetchone()
    if run is None or run[0] in {state.value for state in TERMINAL_PREPARATION_STATES}:
        return
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
        failed = connection.execute(
            "SELECT failed FROM review_preparation_presentation WHERE row_id='run' AND run_id=?",
            (run_id,),
        ).fetchone()[0]
        state = (
            PreparationRunState.COMPLETED_WITH_FAILURES
            if failed
            else PreparationRunState.COMPLETED_CLEAN
        )
        completed_at = run[1] if run[1] is not None else updated_at
    elif blocked:
        state = PreparationRunState.BLOCKED
        completed_at = None
    elif (
        connection.execute(
            "SELECT 1 FROM review_preparation_presentation AS enrollment "
            "WHERE enrollment.row_kind='enrollment' AND enrollment.run_id=? "
            "AND enrollment.cycle_state IN ('discovering','processing') LIMIT 1",
            (run_id,),
        ).fetchone()
        is not None
    ):
        state = PreparationRunState.RUNNING
        completed_at = None
    else:
        state = PreparationRunState.STARTING
        completed_at = None
    connection.execute(
        "UPDATE review_preparation_presentation SET state=?,updated_at=?,completed_at=? "
        "WHERE row_id='run' AND run_id=?",
        (state.value, updated_at, completed_at, run_id),
    )


def preparation_presentation_snapshot(
    connection: sqlite3.Connection, now: int
) -> PreparationPresentation | None:
    connection.execute("SAVEPOINT preparation_snapshot")
    try:
        row = connection.execute(
            "SELECT run_id,state,started_at,updated_at,completed_at,total,active_count,"
            "read,searches_finished,compared,checked,ready,failed FROM review_preparation_presentation WHERE row_id='run'"
        ).fetchone()
        if row is None:
            connection.execute("RELEASE preparation_snapshot")
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
            "ORDER BY updated_at DESC,enrollment_ordinal DESC,ordinal DESC,work_id DESC LIMIT 8",
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
        connection.execute("RELEASE preparation_snapshot")
        return result
    except Exception:
        connection.execute("ROLLBACK TO preparation_snapshot")
        connection.execute("RELEASE preparation_snapshot")
        raise


def dismiss_preparation_presentation(
    connection: sqlite3.Connection, run_id: str, dismissed_at: int
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
        "UPDATE review_preparation_presentation SET state='dismissed',updated_at=? WHERE row_id='run'",
        (dismissed_at,),
    )


def validate_preparation_presentation(connection: sqlite3.Connection) -> None:
    run_count = connection.execute(
        "SELECT COUNT(*) FROM review_preparation_presentation WHERE row_kind='run'"
    ).fetchone()[0]
    total = connection.execute(
        "SELECT COUNT(*) FROM review_preparation_presentation"
    ).fetchone()[0]
    if run_count not in {0, 1} or (run_count == 0 and total):
        raise MetadataReportError("database is unavailable or incompatible")
    if run_count:
        try:
            run_id, active_count = connection.execute(
                "SELECT run_id,active_count FROM review_preparation_presentation WHERE row_id='run'"
            ).fetchone()
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
                | {"connection_name", "library_name", "ordinal"},
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
                row_kind = cast(str, row[columns.index("row_kind")])
                relevant = allowed.get(row_kind)
                if relevant is None:
                    raise ValueError("invalid presentation row kind")
                if any(
                    value is not None and column not in relevant
                    for column, value in zip(columns, row, strict=True)
                ):
                    raise ValueError("irrelevant presentation columns")
            enrollments = connection.execute(
                "SELECT scan_id,cycle_id,pending_cycle_id,ordinal,row_id,connection_name,library_name "
                "FROM review_preparation_presentation "
                "WHERE row_kind='enrollment' ORDER BY ordinal"
            ).fetchall()
            deferred = connection.execute(
                "SELECT connection_name,library_name,ordinal,row_id "
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
                for connection_name, library_name, _ordinal, row_id in deferred
            ):
                raise ValueError("noncanonical deferred enrollment")
            cycle_ids = [
                cycle_id
                for row in enrollments
                for cycle_id in row[1:3]
                if cycle_id is not None
            ]
            if any(
                type(cycle_id) is not str
                or len(cycle_id) != 82
                or not cycle_id.startswith("preparation_cycle_")
                or any(
                    character not in "0123456789abcdef" for character in cycle_id[18:]
                )
                for cycle_id in cycle_ids
            ):
                raise ValueError("noncanonical presentation cycle identity")
            if len(cycle_ids) != len(set(cycle_ids)):
                raise ValueError("noncanonical presentation cycles")
            for (
                scan_id,
                _cycle_id,
                _pending,
                _ordinal,
                row_id,
                connection_name,
                library_name,
            ) in enrollments:
                if (
                    row_id
                    != "enrollment:"
                    + hashlib.sha256(cast(str, scan_id).encode()).hexdigest()
                ):
                    raise ValueError("noncanonical enrollment identity")
                target = connection.execute(
                    "SELECT connection_name,library_name FROM review_preparation_scans "
                    "WHERE scan_id=?",
                    (scan_id,),
                ).fetchone()
                if target != (connection_name, library_name):
                    raise ValueError("orphan enrollment")
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
                (row[0], row[1]): row[3] for row in enrollments if row[1] is not None
            }
            for (
                scan_id,
                cycle_id,
                work_id,
                ordinal,
                enrollment_ordinal,
                row_id,
            ) in connection.execute(
                "SELECT scan_id,cycle_id,work_id,ordinal,enrollment_ordinal,row_id "
                "FROM review_preparation_presentation WHERE row_kind='work'"
            ):
                if enrollment_cycles.get((scan_id, cycle_id)) != enrollment_ordinal:
                    raise ValueError("orphan presentation work cycle")
                if row_id != "work:" + cast(str, work_id):
                    raise ValueError("noncanonical presentation work identity")
                authoritative = connection.execute(
                    "SELECT scan_id,ordinal FROM review_preparation_work WHERE work_id=?",
                    (work_id,),
                ).fetchone()
                if authoritative != (scan_id, ordinal):
                    raise ValueError("noncanonical presentation work")
            observed_active = connection.execute(
                "SELECT COUNT(*) FROM review_preparation_presentation WHERE row_kind='work' "
                "AND current_work_state='running'"
            ).fetchone()[0]
            if active_count != observed_active:
                raise ValueError("invalid active count")
            preparation_presentation_snapshot(connection, 0)
        except (TypeError, ValueError, sqlite3.Error):
            invalid = True
        else:
            invalid = False
        if invalid:
            raise MetadataReportError(
                "database is unavailable or incompatible"
            ) from None
