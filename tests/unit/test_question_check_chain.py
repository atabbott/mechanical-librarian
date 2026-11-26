"""The question check's reading of an answer's carry chain (ADR-0108, steps 2
and 3): a subtitle a title plan wrote as its companion is written with that
plan's value, a title plan still writing it as its companion defers it, and
an authored description's own plan in flight defers it."""

from __future__ import annotations

import json
import sqlite3

import pytest

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
    # Only a verified title plan wrote it; a planned one is a write still
    # under way.
    _plan(connection, "1", "human_decision_" + "d" * 64, "title", "planned", companion)
    assert _chain_plan(connection, _SUBTITLE, "subtitle") == ("in_flight", None)
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


def _companion(decision: str, failure: str | None = None) -> dict[str, object]:
    plan: dict[str, object] = {
        "plan": {
            "desired_value": "The Title",
            "subtitle_companion": {
                "authority": {"decision_id": decision},
                "desired_value": "A Tale",
            },
        }
    }
    if failure is not None:
        plan["failure"] = failure
    return plan


@pytest.mark.parametrize(
    ("state", "failure", "expected"),
    (
        ("planned", None, "in_flight"),
        ("claimed", None, "in_flight"),
        ("applying", None, "in_flight"),
        ("reconciling", None, "in_flight"),
        ("retryable", None, "in_flight"),
        ("stale", "connection", "in_flight"),
        # Negative controls: an ended title plan is no write under way.
        ("stale", "target_changed", "none"),
        ("superseded", None, "none"),
        ("abandoned", None, "none"),
    ),
    ids=(
        "planned",
        "claimed",
        "applying",
        "reconciling",
        "retryable",
        "stale_connection",
        "stale_target_changed",
        "superseded",
        "abandoned",
    ),
)
def test_a_title_plan_writing_the_subtitle_as_its_companion_defers_it(
    state: str, failure: str | None, expected: str
) -> None:
    """Step 2 (root ruling on h2b item 6): a title plan still writing the
    subtitle answer as its companion is a write of that answer still under
    way, as `answer_written_sql` counts a verified one as its write. The
    companion's own plan state never stops the subtitle for the owner."""
    connection = _ledger()
    _plan(
        connection,
        "1",
        "human_decision_" + "d" * 64,
        "title",
        state,
        _companion(_SUBTITLE, failure),
    )
    assert _chain_plan(connection, _SUBTITLE, "subtitle") == (expected, None)
    connection.close()


def test_a_title_plan_in_flight_defers_only_the_subtitle_it_names() -> None:
    """Negative control: a title plan in flight whose companion names another
    answer, or a title plan with no companion, leaves this subtitle as it
    is."""
    connection = _ledger()
    _plan(
        connection,
        "1",
        "human_decision_" + "d" * 64,
        "title",
        "applying",
        _companion("human_decision_" + "e" * 64),
    )
    _plan(
        connection,
        "2",
        "human_decision_" + "d" * 64,
        "title",
        "planned",
        {"plan": {"desired_value": "The Title"}},
    )
    assert _chain_plan(connection, _SUBTITLE, "subtitle") == ("none", None)
    connection.close()
