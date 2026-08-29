"""One bounded owner-facing projection over authoritative preparation rows."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING, cast

from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.domain.preparation_presentation import (
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


def accept_preparation_trigger(
    connection: sqlite3.Connection,
    trigger: PreparationTrigger,
    scan_id: str,
    connection_name: str,
    library_name: str,
    accepted_at: int,
) -> None:
    """Extend the visible nonterminal run or atomically replace its terminal run."""
    current = connection.execute(
        "SELECT run_id,state FROM review_preparation_presentation WHERE row_kind='run'"
    ).fetchone()
    if current is None or current[1] in {
        PreparationRunState.COMPLETED_CLEAN.value,
        PreparationRunState.COMPLETED_WITH_FAILURES.value,
        PreparationRunState.DISMISSED.value,
    }:
        previous = "" if current is None else cast(str, current[0])
        material = f"preparation-presentation-v1\0{previous}\0{trigger.value}\0{scan_id}\0{accepted_at}"
        run_id = "preparation_run_" + hashlib.sha256(material.encode()).hexdigest()
        connection.execute("DELETE FROM review_preparation_presentation")
        connection.execute(
            "INSERT INTO review_preparation_presentation("
            "row_id,row_kind,run_id,state,started_at,updated_at,total,read,searches_finished,compared,checked,ready,failed) "
            "VALUES('run','run',?,'starting',?,?,0,0,0,0,0,0,0)",
            (run_id, accepted_at, accepted_at),
        )
    else:
        run_id = cast(str, current[0])
    ordinal = connection.execute(
        "SELECT COUNT(*) FROM review_preparation_presentation WHERE row_kind='trigger'"
    ).fetchone()[0]
    connection.execute(
        "INSERT INTO review_preparation_presentation("
        "row_id,row_kind,run_id,trigger,scan_id,connection_name,library_name,ordinal,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (
            f"trigger:{ordinal}",
            "trigger",
            run_id,
            trigger.value,
            scan_id,
            connection_name,
            library_name,
            ordinal,
            accepted_at,
        ),
    )
    _refresh_run_state(connection, run_id, accepted_at)


def record_preparation_names(
    connection: sqlite3.Connection,
    scan_id: str,
    entries: tuple[CatalogueEntry, ...],
    updated_at: int,
) -> None:
    run = connection.execute(
        "SELECT run_id FROM review_preparation_presentation WHERE row_kind='run'"
    ).fetchone()
    enrolled = connection.execute(
        "SELECT 1 FROM review_preparation_presentation WHERE row_kind='trigger' AND scan_id=?",
        (scan_id,),
    ).fetchone()
    if run is None or enrolled is None:
        return
    run_id = cast(str, run[0])
    selected = {entry.item_id: entry for entry in entries}
    rows = connection.execute(
        "SELECT work_id,item_id,ordinal FROM review_preparation_work WHERE scan_id=? ORDER BY ordinal,work_id",
        (scan_id,),
    ).fetchall()
    for work_id, item_id, ordinal in rows:
        entry = selected.get(item_id)
        if entry is None:
            continue
        connection.execute(
            "INSERT INTO review_preparation_presentation("
            "row_id,row_kind,run_id,scan_id,work_id,ordinal,display_name,work_stage,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(row_id) DO UPDATE SET "
            "run_id=excluded.run_id,scan_id=excluded.scan_id,ordinal=excluded.ordinal,"
            "display_name=excluded.display_name,updated_at=excluded.updated_at",
            (
                "work:" + work_id,
                "work",
                run_id,
                scan_id,
                work_id,
                ordinal,
                entry.title,
                "extraction",
                updated_at,
            ),
        )
    connection.execute(
        "UPDATE review_preparation_presentation SET total=MAX(total,("
        "SELECT COUNT(*) FROM review_preparation_presentation AS work "
        "WHERE work.row_kind='work' AND work.run_id=?)),updated_at=? "
        "WHERE row_kind='run' AND run_id=?",
        (run_id, updated_at, run_id),
    )
    _refresh_run_state(connection, run_id, updated_at)


def refresh_preparation_presentation(
    connection: sqlite3.Connection, updated_at: int
) -> None:
    if (
        connection.execute(
            "SELECT 1 FROM review_preparation_presentation WHERE row_kind='run'"
        ).fetchone()
        is not None
    ):
        _refresh_preparation_presentation(connection, updated_at)


def refresh_preparation_run_state(
    connection: sqlite3.Connection, updated_at: int
) -> None:
    """Refresh only the bounded run envelope, never its work population."""
    run = connection.execute(
        "SELECT run_id FROM review_preparation_presentation WHERE row_kind='run'"
    ).fetchone()
    if run is not None:
        _refresh_run_state(connection, cast(str, run[0]), updated_at)


def refresh_preparation_work(
    connection: sqlite3.Connection, work_id: str, updated_at: int
) -> None:
    """Advance one enrolled work's high water in a fixed number of queries."""
    presentation = connection.execute(
        "SELECT run_id,work_stage,work_outcome,updated_at FROM review_preparation_presentation "
        "WHERE row_id=? AND row_kind='work'",
        ("work:" + work_id,),
    ).fetchone()
    if presentation is None:
        return
    authoritative = connection.execute(
        "SELECT stage,state,updated_at FROM review_preparation_work WHERE work_id=?",
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
    new_stage = "projection" if authoritative[0] == "complete" else authoritative[0]
    high_stage = max((old_stage, new_stage), key=lambda value: _STAGE_ORDER[value])
    old_outcome = cast(str | None, presentation[2])
    observed_outcome = (
        "ready"
        if authoritative[1] == "complete"
        else "failed"
        if authoritative[1] in {"failed", "stale"}
        else None
    )
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
        "UPDATE review_preparation_presentation SET work_stage=?,work_outcome=?,updated_at=? "
        "WHERE row_id=?",
        (high_stage, outcome, outcome_at, "work:" + work_id),
    )
    connection.execute(
        "UPDATE review_preparation_presentation SET updated_at=?,read=read+?,"
        "searches_finished=searches_finished+?,compared=compared+?,checked=checked+?,"
        "ready=ready+?,failed=failed+? WHERE row_kind='run' AND run_id=?",
        (updated_at, *increments, presentation[0]),
    )
    _refresh_run_state(connection, cast(str, presentation[0]), updated_at)


def _refresh_preparation_presentation(
    connection: sqlite3.Connection, updated_at: int
) -> None:
    run = connection.execute(
        "SELECT run_id,state,read,searches_finished,compared,checked,ready,failed,total "
        "FROM review_preparation_presentation WHERE row_kind='run'"
    ).fetchone()
    if run is None or run[1] == PreparationRunState.DISMISSED.value:
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
        "work.stage,work.state,work.updated_at FROM review_preparation_presentation AS presentation "
        "JOIN review_preparation_work AS work ON work.work_id=presentation.work_id "
        "WHERE presentation.row_kind='work' AND presentation.run_id=?",
        (run_id,),
    ).fetchall()
    observed = [0, 0, 0, 0, 0, 0]
    for row_id, old_stage, old_outcome, old_updated_at, current_stage, current_state, work_updated_at in work_rows:
        current_stage = "projection" if current_stage == "complete" else current_stage
        stage = max(
            (old_stage, current_stage), key=lambda value: _STAGE_ORDER[value]
        )
        outcome = old_outcome or (
            "ready"
            if current_state == "complete"
            else "failed"
            if current_state in {"failed", "stale"}
            else None
        )
        connection.execute(
            "UPDATE review_preparation_presentation SET work_stage=?,work_outcome=?,updated_at=? WHERE row_id=?",
            (
                stage,
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
    connection.execute(
        "UPDATE review_preparation_presentation SET total=?,read=?,searches_finished=?,"
        "compared=?,checked=?,ready=?,failed=? WHERE row_kind='run'",
        (max(cast(int, run[8]), len(work_rows)), *high_water),
    )
    _refresh_run_state(connection, run_id, updated_at)


def _refresh_run_state(
    connection: sqlite3.Connection, run_id: str, updated_at: int
) -> None:
    run = connection.execute(
        "SELECT state,completed_at FROM review_preparation_presentation "
        "WHERE row_kind='run' AND run_id=?",
        (run_id,),
    ).fetchone()
    if run is None or run[0] == PreparationRunState.DISMISSED.value:
        return
    blocked = (
        connection.execute(
            "SELECT 1 FROM review_preparation_control WHERE singleton=1 AND paused=1 UNION ALL "
            "SELECT 1 FROM review_preparation_connection_conditions AS condition "
            "WHERE condition.state IN ('paused','checking') AND EXISTS("
            "SELECT 1 FROM review_preparation_presentation AS trigger "
            "WHERE trigger.row_kind='trigger' AND trigger.run_id=? "
            "AND trigger.connection_name=condition.connection_name) LIMIT 1",
            (run_id,),
        ).fetchone()
        is not None
    )
    terminal = (
        connection.execute(
            "SELECT COUNT(*) FROM review_preparation_presentation AS trigger "
            "JOIN review_preparation_scans AS scan ON scan.scan_id=trigger.scan_id "
            "WHERE trigger.row_kind='trigger' AND trigger.run_id=? "
            "HAVING COUNT(*)>0 AND SUM(scan.state NOT IN ('completed','completed_with_failures'))=0",
            (run_id,),
        ).fetchone()
        is not None
    )
    if terminal:
        failed = connection.execute(
            "SELECT failed FROM review_preparation_presentation WHERE row_kind='run' AND run_id=?",
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
    elif connection.execute(
        "SELECT 1 FROM review_preparation_presentation AS trigger "
        "JOIN review_preparation_scans AS scan ON scan.scan_id=trigger.scan_id "
        "WHERE trigger.row_kind='trigger' AND trigger.run_id=? "
        "AND scan.state IN ('discovering','processing') LIMIT 1",
        (run_id,),
    ).fetchone() is not None:
        state = PreparationRunState.RUNNING
        completed_at = None
    else:
        state = PreparationRunState.STARTING
        completed_at = None
    connection.execute(
        "UPDATE review_preparation_presentation SET state=?,updated_at=?,completed_at=? "
        "WHERE row_kind='run' AND run_id=?",
        (state.value, updated_at, completed_at, run_id),
    )


def preparation_presentation_snapshot(
    connection: sqlite3.Connection, now: int
) -> PreparationPresentation | None:
    row = connection.execute(
        "SELECT run_id,state,started_at,updated_at,completed_at,total,read,searches_finished,compared,checked,ready,failed "
        "FROM review_preparation_presentation WHERE row_kind='run'"
    ).fetchone()
    if row is None:
        return None
    run_id = cast(str, row[0])
    triggers = tuple(
        PreparationTriggerRecord(
            PreparationTrigger(trigger),
            connection_name,
            library_name,
            scan_id,
            accepted_at,
        )
        for trigger, connection_name, library_name, scan_id, accepted_at in connection.execute(
            "SELECT trigger,connection_name,library_name,scan_id,updated_at "
            "FROM review_preparation_presentation WHERE row_kind='trigger' AND run_id=? ORDER BY ordinal",
            (run_id,),
        )
    )
    active_rows = connection.execute(
        "SELECT work.display_name,current.stage FROM review_preparation_presentation AS work "
        "JOIN review_preparation_work AS current ON current.work_id=work.work_id AND current.state='running' "
        "JOIN review_preparation_presentation AS admission "
        "ON admission.row_kind='trigger' AND admission.run_id=work.run_id "
        "AND admission.scan_id=work.scan_id "
        "AND admission.ordinal=(SELECT MIN(first_admission.ordinal) "
        "FROM review_preparation_presentation AS first_admission "
        "WHERE first_admission.row_kind='trigger' AND first_admission.run_id=work.run_id "
        "AND first_admission.scan_id=work.scan_id) "
        "WHERE work.row_kind='work' AND work.run_id=? AND work.work_outcome IS NULL "
        "ORDER BY admission.ordinal,work.ordinal,work.work_id",
        (run_id,),
    ).fetchall()
    active = tuple(
        PreparationActiveBook(name, _STAGE_COPY.get(stage, "Preparing review"))
        for name, stage in active_rows[:5]
    )
    completion_rows = connection.execute(
        "SELECT work.display_name,work.work_outcome,work.updated_at "
        "FROM review_preparation_presentation AS work "
        "JOIN review_preparation_presentation AS admission "
        "ON admission.row_kind='trigger' AND admission.run_id=work.run_id "
        "AND admission.scan_id=work.scan_id "
        "AND admission.ordinal=(SELECT MIN(first_admission.ordinal) "
        "FROM review_preparation_presentation AS first_admission "
        "WHERE first_admission.row_kind='trigger' AND first_admission.run_id=work.run_id "
        "AND first_admission.scan_id=work.scan_id) "
        "WHERE work.row_kind='work' AND work.run_id=? AND work.work_outcome IS NOT NULL "
        "ORDER BY work.updated_at DESC,admission.ordinal DESC,work.ordinal DESC,work.work_id DESC LIMIT 8",
        (run_id,),
    ).fetchall()
    completions = tuple(PreparationCompletion(*item) for item in completion_rows)
    started_at = cast(int, row[2])
    window_start = max(started_at, now - 300)
    completed_count = connection.execute(
        "SELECT COUNT(*) FROM review_preparation_presentation WHERE row_kind='work' "
        "AND run_id=? AND work_outcome IS NOT NULL AND updated_at>=?",
        (run_id, window_start),
    ).fetchone()[0]
    minutes = max((now - window_start) / 60, 1 / 60)
    return PreparationPresentation(
        run_id=run_id,
        state=PreparationRunState(row[1]),
        triggers=triggers,
        passage=PreparationPassage(*row[6:12]),
        active_books=active,
        active_overflow=max(len(active_rows) - len(active), 0),
        recent_completions=completions,
        throughput_per_minute=round(completed_count / minutes, 1),
        started_at=started_at,
        updated_at=row[3],
        completed_at=row[4],
        total=row[5],
    )


def dismiss_preparation_presentation(
    connection: sqlite3.Connection, run_id: str, dismissed_at: int
) -> None:
    current = connection.execute(
        "SELECT run_id,state FROM review_preparation_presentation WHERE row_kind='run'"
    ).fetchone()
    if current is None or current[0] != run_id:
        raise PreparationPresentationError("stale")
    if current[1] not in {
        PreparationRunState.COMPLETED_CLEAN.value,
        PreparationRunState.COMPLETED_WITH_FAILURES.value,
    }:
        raise PreparationPresentationError("not_dismissible")
    connection.execute(
        "UPDATE review_preparation_presentation SET state='dismissed',updated_at=? WHERE row_kind='run'",
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
            preparation_presentation_snapshot(connection, 0)
        except (TypeError, ValueError, sqlite3.Error) as error:
            raise MetadataReportError(
                "database is unavailable or incompatible"
            ) from error
