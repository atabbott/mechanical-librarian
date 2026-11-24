"""Each ledger copy equals what its reader shows from derived state now.

ADR-0108 copies onto ledger rows what Activity and review history read through
derived rows today, so a reader can show them once those rows are gone (#2038).
This module reads the same rows the way those readers do -- Activity's own
union arms and the review history -- and compares every copy with them. It
returns how many rows of each kind it compared, so a caller can show which
arms its data reached.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from mechanical_librarian.adapters.persistence import field_effect_activity
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
)
from mechanical_librarian.domain.review_case import ReviewCase


def _arm(repository: SQLiteMetadataReportRepository, arm: Any) -> list[Any]:
    sql = arm(lambda *_: "", field_effect_activity._ACTIVITY_REPORT_SCALARS)
    return repository._connection.execute(sql).fetchall()


def _origin(repository: SQLiteMetadataReportRepository, decision_id: str) -> str:
    """Which arm of Activity's field-effect join names the plan's case."""
    for table, origin in (
        ("human_decisions", "human"),
        ("automatic_decisions", "automatic"),
        ("cover_selection_authorities", "automatic_cover"),
        ("description_decisions", "description"),
    ):
        if repository._connection.execute(
            f"SELECT 1 FROM {table} WHERE decision_id=?", (decision_id,)
        ).fetchone():
            return origin
    # A synthetic plan whose decision no table holds: Activity reads no
    # report for it.
    return "unbound"


def assert_ledger_copies(repository: SQLiteMetadataReportRepository) -> Counter[str]:
    """Compare every copy in the store with its derived reading now."""
    connection = repository._connection
    checked: Counter[str] = Counter()
    # Activity: an owner decision's row reads its case's report.
    for row in _arm(repository, field_effect_activity._human_decision_arm):
        assert connection.execute(
            "SELECT connection_name,library_name,item_id,display_title "
            "FROM human_decisions WHERE decision_id=?",
            (row[17],),
        ).fetchone() == (row[1], row[3], row[8], row[11])
        assert connection.execute(
            "SELECT connection_name,library_name,item_id FROM human_decision_events "
            "WHERE event_id=?",
            (row[0],),
        ).fetchone() == (row[1], row[3], row[8])
        checked["activity_human_decision"] += 1
    # Activity: a write plan's rows read the report of its decision's case.
    for row in _arm(repository, field_effect_activity._field_effect_arm):
        (title, decision_id) = connection.execute(
            "SELECT display_title,decision_id FROM field_effects WHERE plan_id=?",
            (row[15],),
        ).fetchone()
        assert title == row[11]
        checked["activity_field_effect_" + _origin(repository, decision_id)] += 1
    # Activity: a no-change row reads its own report.
    for row in _arm(repository, field_effect_activity._field_effect_no_change_arm):
        assert connection.execute(
            "SELECT display_title FROM field_effect_no_changes WHERE event_id=?",
            (row[0],),
        ).fetchone() == (row[11],)
        checked["activity_no_change_" + row[5]] += 1
    # Review history: every decision, with an event or not, reads its case's
    # target field; an accepted description shows the copy its offer chose.
    histories: dict[str, dict[str, dict[str, object]]] = {}
    for (
        decision_id,
        case_id,
        destination,
        outcome,
        item,
        title,
        target_field,
        selected_form,
    ) in connection.execute(
        "SELECT decision_id,review_case_id,destination,outcome,"
        "json_array(connection_name,library_name,item_id),display_title,"
        "target_field_document,selected_form FROM human_decisions"
    ):
        case = ReviewCase.create_from_data(
            json.loads(repository.get_review_case(case_id))["case"]
        )
        fields = case.target["fields"]
        assert isinstance(fields, list)
        assert json.loads(target_field) == next(
            (field for field in fields if field.get("destination") == destination),
            None,
        )
        assert json.loads(item) == [
            case.target["connection"],
            case.target["library"],
            case.target["item"],
        ]
        assert (title,) == connection.execute(
            "SELECT json_extract(document,'$.target.fields[0].value') "
            "FROM metadata_reports WHERE report_id=?",
            (case.report_id,),
        ).fetchone()
        checked["history_target_field"] += 1
        if destination == "description" and outcome == "accept":
            if case_id not in histories:
                histories[case_id] = {
                    str(entry.get("decision_id")): entry
                    for entry in repository.get_review_case_history(case_id, 100)
                }
            assert selected_form == histories[case_id][decision_id].get(
                "selected_formatted"
            )
            checked["history_selected_form"] += 1
        else:
            assert selected_form is None
    return checked
