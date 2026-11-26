"""The question check's reading of an answer's carry chain (ADR-0108, steps 2
and 3): a subtitle a title plan wrote as its companion is written with that
plan's value, and an authored description's own plan in flight defers it."""

from __future__ import annotations

import json
import sqlite3

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.question_check_runner import (
    _chain_plan,
)

_SUBTITLE = "human_decision_" + "a" * 64
_AUTHORED = "human_decision_" + "b" * 64


def _ledger() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    schema._register_casefold(connection)
    schema._create_schema(connection)
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        "INSERT INTO human_decisions(ordinal,decision_id,review_case_id,proposal_id,"
        "target_fingerprint,destination,actor,outcome,selected_value_document,"
        "idempotency_digest,request_digest,occurred_at,document,connection_name,"
        "library_name,item_id,target_field_document) VALUES(1,?,?,?,?,'subtitle',"
        "'owner','accept','null',?,?,1,'{}','home','Books','item-1','null')",
        (
            _SUBTITLE,
            "review_case_" + "0" * 64,
            "review_proposal_" + "0" * 64,
            "0" * 64,
            "1" * 64,
            "0" * 64,
        ),
    )
    connection.execute(
        "INSERT INTO owner_authored_descriptions(decision_id,review_case_id,"
        "candidate_id,idempotency_digest,document,connection_name,library_name,"
        "item_id,candidate_document,ordinal,episode) VALUES(?,?,?,?,'{}','home',"
        "'Books','item-2','{}',2,0)",
        (
            _AUTHORED,
            "review_case_" + "0" * 64,
            "description_candidate_" + "0" * 64,
            "9" * 64,
        ),
    )
    return connection


def _plan(
    connection: sqlite3.Connection,
    tag: str,
    decision: str,
    destination: str,
    state: str,
    document: dict[str, object],
) -> None:
    connection.execute(
        "INSERT INTO field_effects(plan_id,decision_id,connection_name,library_name,"
        "work_id,destination,state,attempt,updated_at,document) "
        "VALUES(?,?,'home','Books','item-1',?,?,1,1,?)",
        ("effect_plan_" + tag * 64, decision, destination, state, json.dumps(document)),
    )


def test_a_subtitle_written_as_a_title_companion_is_written_with_its_value() -> None:
    connection = _ledger()
    assert _chain_plan(connection, _SUBTITLE, "subtitle") == ("none", None)
    companion: dict[str, object] = {
        "plan": {
            "desired_value": "The Title",
            "subtitle_companion": {
                "authority": {"decision_id": _SUBTITLE},
                "desired_value": "A Tale",
            },
        }
    }
    # Only a verified title plan wrote it.
    _plan(connection, "1", "human_decision_" + "d" * 64, "title", "planned", companion)
    assert _chain_plan(connection, _SUBTITLE, "subtitle") == ("none", None)
    connection.execute("UPDATE field_effects SET state='verified'")
    assert _chain_plan(connection, _SUBTITLE, "subtitle") == ("none", ("A Tale",))
    connection.close()


def test_an_authored_description_reads_its_own_plan() -> None:
    connection = _ledger()
    assert _chain_plan(connection, _AUTHORED, "description") == ("none", None)
    _plan(
        connection,
        "2",
        _AUTHORED,
        "description",
        "applying",
        {"plan": {"desired_value": "Text"}},
    )
    assert _chain_plan(connection, _AUTHORED, "description")[0] == "in_flight"
    connection.execute("UPDATE field_effects SET state='verified'")
    assert _chain_plan(connection, _AUTHORED, "description") == ("none", ("Text",))
    connection.close()
