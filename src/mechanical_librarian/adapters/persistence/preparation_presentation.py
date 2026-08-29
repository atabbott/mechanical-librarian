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
    _refresh_preparation_presentation(connection, accepted_at)


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
    _refresh_preparation_presentation(connection, updated_at)


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
    connection.execute(
        "UPDATE review_preparation_presentation AS presentation SET "
        "work_stage=(SELECT CASE WHEN stage='complete' THEN 'projection' ELSE stage END "
        "FROM review_preparation_work WHERE work_id=presentation.work_id),"
        "work_outcome=(SELECT CASE WHEN state='complete' THEN 'ready' WHEN state IN ('failed','stale') THEN 'failed' END FROM review_preparation_work WHERE work_id=presentation.work_id),"
        "updated_at=(SELECT updated_at FROM review_preparation_work WHERE work_id=presentation.work_id) "
        "WHERE row_kind='work' AND run_id=? AND EXISTS(SELECT 1 FROM review_preparation_work WHERE work_id=presentation.work_id)",
        (run_id,),
    )
    work_rows = connection.execute(
        "SELECT work_stage,work_outcome FROM review_preparation_presentation WHERE row_kind='work' AND run_id=?",
        (run_id,),
    ).fetchall()
    observed = [0, 0, 0, 0, 0, 0]
    for stage, outcome in work_rows:
        rank = 5 if outcome is not None else _STAGE_ORDER.get(stage, -1)
        observed[0] += rank >= 1
        observed[1] += rank >= 2
        observed[2] += rank >= 3
        observed[3] += rank >= 4
        observed[4] += outcome == "ready"
        observed[5] += outcome == "failed"
    high_water = tuple(
        max(cast(int, run[index + 2]), observed[index]) for index in range(6)
    )
    scan_ids = [
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT scan_id FROM review_preparation_presentation WHERE row_kind='trigger' AND run_id=?",
            (run_id,),
        )
    ]
    scan_states = []
    for scan_id in scan_ids:
        row = connection.execute(
            "SELECT state FROM review_preparation_scans WHERE scan_id=?", (scan_id,)
        ).fetchone()
        if row is not None:
            scan_states.append(row[0])
    blocked = (
        connection.execute(
            "SELECT 1 FROM review_preparation_control WHERE singleton=1 AND paused=1 UNION ALL "
            "SELECT 1 FROM review_preparation_connection_conditions WHERE state IN ('paused','checking') LIMIT 1"
        ).fetchone()
        is not None
    )
    terminal = bool(scan_states) and all(
        state in {"completed", "completed_with_failures"} for state in scan_states
    )
    if terminal:
        state = (
            PreparationRunState.COMPLETED_WITH_FAILURES
            if high_water[5]
            else PreparationRunState.COMPLETED_CLEAN
        )
        completed_at = updated_at
    elif blocked:
        state = PreparationRunState.BLOCKED
        completed_at = None
    elif work_rows or any(
        state in {"discovering", "processing"} for state in scan_states
    ):
        state = PreparationRunState.RUNNING
        completed_at = None
    else:
        state = PreparationRunState.STARTING
        completed_at = None
    connection.execute(
        "UPDATE review_preparation_presentation SET state=?,updated_at=?,completed_at=?,total=?,"
        "read=?,searches_finished=?,compared=?,checked=?,ready=?,failed=? WHERE row_kind='run'",
        (
            state.value,
            updated_at,
            completed_at,
            max(cast(int, run[8]), len(work_rows)),
            *high_water,
        ),
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
        "SELECT work.display_name,work.work_stage FROM review_preparation_presentation AS work "
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
        raise MetadataReportError("preparation presentation is stale")
    if current[1] not in {
        PreparationRunState.COMPLETED_CLEAN.value,
        PreparationRunState.COMPLETED_WITH_FAILURES.value,
    }:
        raise MetadataReportError("preparation presentation is not dismissible")
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
