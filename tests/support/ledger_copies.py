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
from typing import Any, cast

from mechanical_librarian.adapters.persistence import cover_taste, field_effect_activity
from mechanical_librarian.adapters.persistence.metadata_reports import (
    SQLiteMetadataReportRepository,
)
from mechanical_librarian.domain.description_advice import DescriptionAdviceCandidate
from mechanical_librarian.domain.review_case import ReviewCase


def _arm(repository: SQLiteMetadataReportRepository, arm: Any) -> list[Any]:
    sql = arm(lambda *_: "", field_effect_activity._ACTIVITY_REPORT_SCALARS)
    return repository._connection.execute(sql).fetchall()


def _origin(repository: SQLiteMetadataReportRepository, decision_id: str) -> str:
    """Which arm of Activity's field-effect join names the plan's case."""
    for table, origin in (
        ("human_decisions", "human"),
        ("automatic_decisions", "automatic"),
        ("automatic_cover_selections", "automatic_cover"),
        ("owner_authored_descriptions", "owner_description"),
        ("automatic_description_decisions", "automatic_description"),
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
    # Activity: an owner's authored description reads its work from its
    # case's report and its candidate from the candidate row.
    owner_descriptions = {
        row[0]: row[1:]
        for row in connection.execute(
            "SELECT decision_id,connection_name,library_name,item_id,candidate_document "
            "FROM owner_authored_descriptions"
        )
    }
    for row in _arm(repository, field_effect_activity._description_decision_arm):
        if row[0] in owner_descriptions:
            assert owner_descriptions[row[0]] == (row[1], row[3], row[8], row[14])
            checked["activity_owner_description"] += 1
    # Review history: each authored description shows the candidate it chose.
    for decision_id, (*_, candidate_document) in owner_descriptions.items():
        (case_id,) = connection.execute(
            "SELECT review_case_id FROM owner_authored_descriptions WHERE decision_id=?",
            (decision_id,),
        ).fetchone()
        (selected,) = (
            entry
            for entry in repository.get_review_case_history(case_id, 100)
            if entry.get("kind") == "description_selected"
            and cast("dict[str, object]", entry["decision"])["decision_id"]
            == decision_id
        )
        assert (
            selected["candidate"]
            == DescriptionAdviceCandidate.from_json(candidate_document).model_dump()
        )
        checked["history_owner_description"] += 1
    # Taste: each owner cover choice copies the facts the taste profile
    # counts it by, as the comparison and report read now.
    taste_copies = {
        row[0]: row[1:]
        for row in connection.execute(
            "SELECT selection_id,transport_sha256,width,height,offered_arts,"
            "report_format FROM owner_cover_selections"
        )
    }
    for selection_id, (
        selection_document,
        consideration_document,
        currentness_document,
        report_format,
    ) in (
        (row[0], row[1:])
        for row in connection.execute(
            "SELECT selection.selection_id,selection.document,"
            "consideration.document,consideration.currentness_document,"
            "facts.format FROM owner_cover_selections selection "
            "JOIN cover_considerations consideration "
            "ON consideration.consideration_id=selection.consideration_id "
            "LEFT JOIN metadata_report_browse_facts facts "
            "ON facts.report_id=consideration.report_id"
        )
    ):
        chosen = json.loads(selection_document).get("candidate_reference")
        offered: dict[str, Any] = next(
            (
                candidate
                for candidate in json.loads(consideration_document)["candidates"]
                if candidate.get("reference") == chosen
                and candidate.get("candidate_kind") == "candidate"
            ),
            {},
        )
        stage = json.loads(currentness_document).get("cover_stage")
        arts = stage.get("arts") if isinstance(stage, dict) else None
        assert taste_copies[selection_id] == (
            offered.get("transport_sha256")
            if type(offered.get("transport_sha256")) is str
            else None,
            offered.get("width") if type(offered.get("width")) is int else None,
            offered.get("height") if type(offered.get("height")) is int else None,
            arts if type(arts) is int else None,
            report_format if type(report_format) is str else None,
        )
        checked["taste_owner_selection"] += 1
    # Taste: the build's decisions read the chosen cover's digest and size,
    # and its eligibility the art count and format, as the copies hold them.
    for source in repository.cover_taste_store().decisions():
        chosen_source = next(
            candidate
            for candidate in source.candidates
            if candidate.reference == source.chosen_reference
        )
        assert taste_copies[source.selection_id][:3] == (
            chosen_source.transport_sha256,
            chosen_source.width,
            chosen_source.height,
        )
        checked["taste_decision"] += 1
    for selection_id, arts, report_format in connection.execute(
        "SELECT selection.selection_id,json_extract("
        "consideration.currentness_document,'$.cover_stage.arts'),facts.format "
        + cover_taste._ELIGIBLE,
        (cover_taste.TASTE_MIN_ARTS,),
    ):
        assert taste_copies[selection_id][3:] == (arts, report_format)
        checked["taste_eligible"] += 1
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
