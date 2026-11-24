"""The ledger and derived partitions of the application database (ADR-0108).

Every table and view is either ledger, as `schema.LEDGER_TABLES` and
`schema.LEDGER_VIEWS` list it, or derived, as this module pins it. A ledger
row never references a derived row, a ledger trigger or view reads only
ledger objects, and a trigger that writes derived state is derived.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from tests.support.activity_seed import seed_activity
from tests.support.genre_reports import (
    OFFERED_DESCRIPTION_COPY,
    formatted_description_report,
    genre_report,
)
from tests.support.ledger_copies import assert_ledger_copies
from tests.support.owner_values import OwnerStore

DERIVED_TABLES = frozenset(
    {
        "automatic_decisions",
        "browse_fact_identity",
        "cover_automatic_held",
        "cover_consideration_events",
        "cover_considerations",
        "cover_taste_judgements",
        "cover_taste_verdicts",
        "current_cover_considerations",
        "current_description_advice",
        "current_description_decisions",
        "current_metadata_report_lineages",
        "current_metadata_reports",
        "current_review_case_lineages",
        "current_review_packages",
        "description_advice_attempts",
        "description_advice_candidates",
        "description_preparation_pending",
        "level_decision_notes",
        "level_proposal_sets",
        "library_visit",
        "metadata_policy_transition_items",
        "metadata_report_browse_authors",
        "metadata_report_browse_facts",
        "metadata_report_cover_references",
        "metadata_report_value_flags",
        "metadata_reports",
        "proposal_set_browse_facts",
        "proposal_sets",
        "provider_recovery_outbox",
        "review_attention_facts",
        "review_case_episodes",
        "review_case_field_facts",
        "review_case_level_offers",
        "review_case_level_reconciliation",
        "review_case_transitions",
        "review_cases",
        "review_preparation_absences",
        "review_preparation_connection_conditions",
        "review_preparation_presentation",
        "review_preparation_scans",
        "review_preparation_work",
        "shadow_decision_outcomes",
        "shadow_outcome_field_facts",
    }
)
DERIVED_VIEWS = frozenset({"review_attention_refresh"})
# Ledger foreign keys into derived state that a later stage removes. Each
# entry is (table, column, referenced table).
KNOWN_LEDGER_TO_DERIVED_KEYS = frozenset(
    {
        # #2038 C2a's cover split: owner selections keep the consideration id
        # as plain provenance.
        ("cover_selection_authorities", "consideration_id", "cover_considerations"),
        # #2038 C2a's description split: owner authored descriptions keep the
        # case and candidate ids as plain provenance.
        ("description_decisions", "candidate_id", "description_advice_candidates"),
        ("description_decisions", "review_case_id", "review_cases"),
        # #2038 C2b deletes the manual holds; the record replaces them.
        ("field_effect_manual_holds", "review_case_id", "review_cases"),
    }
)
_WRITE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?"
    r"|DELETE\s+FROM)\s+(\w+)",
    re.IGNORECASE,
)
_NAME = re.compile(r"\w+")
_LEDGER = schema.LEDGER_TABLES | schema.LEDGER_VIEWS


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    try:
        schema._create_schema(connection)
        yield connection
    finally:
        connection.close()


def _objects(connection: sqlite3.Connection, kind: str) -> dict[str, tuple[str, str]]:
    return {
        name: (table, sql)
        for name, table, sql in connection.execute(
            "SELECT name,tbl_name,sql FROM sqlite_master "
            "WHERE type=? AND name NOT LIKE 'sqlite_%'",
            (kind,),
        )
    }


def _body(sql: str) -> str:
    return sql.split("BEGIN", 1)[1]


def _references(sql: str, names: frozenset[str] | set[str]) -> set[str]:
    return set(_NAME.findall(sql)) & names


def test_every_table_and_view_is_ledger_or_pinned_derived(
    connection: sqlite3.Connection,
) -> None:
    tables = set(_objects(connection, "table"))
    views = set(_objects(connection, "view"))
    assert not schema.LEDGER_TABLES & DERIVED_TABLES
    assert tables == schema.LEDGER_TABLES | DERIVED_TABLES
    assert not schema.LEDGER_VIEWS & DERIVED_VIEWS
    assert views == schema.LEDGER_VIEWS | DERIVED_VIEWS


def test_ledger_foreign_keys_reference_only_ledger_tables(
    connection: sqlite3.Connection,
) -> None:
    crossing = {
        (table, key[3], key[2])
        for table in schema.LEDGER_TABLES
        for key in connection.execute(f"PRAGMA foreign_key_list('{table}')")
        if key[2] not in schema.LEDGER_TABLES
    }
    assert crossing == KNOWN_LEDGER_TO_DERIVED_KEYS
    # Derived rows may reference ledger rows: the direction is one way only.
    assert any(
        key[2] in schema.LEDGER_TABLES
        for table in DERIVED_TABLES
        for key in connection.execute(f"PRAGMA foreign_key_list('{table}')")
    )


def test_ledger_views_read_only_ledger_tables(connection: sqlite3.Connection) -> None:
    names = set(_objects(connection, "table")) | set(_objects(connection, "view"))
    for view in schema.LEDGER_VIEWS:
        _, sql = _objects(connection, "view")[view]
        assert _references(sql.split(" AS ", 1)[1], names) <= _LEDGER, view


def test_triggers_writing_derived_state_are_derived_and_ledger_triggers_read_ledger(
    connection: sqlite3.Connection,
) -> None:
    names = set(_objects(connection, "table")) | set(_objects(connection, "view"))
    derived_on_ledger: set[str] = set()
    for name, (table, sql) in _objects(connection, "trigger").items():
        writes = set(_WRITE.findall(_body(sql)))
        if table not in _LEDGER:
            # A derived trigger never writes the ledger, so dropping and
            # recreating derived state leaves every ledger row as it was.
            assert not writes & _LEDGER, name
        elif writes - _LEDGER:
            # A trigger that writes derived state, such as an attention
            # trigger on a ledger table, is derived.
            assert writes == {"review_attention_refresh"}, name
            derived_on_ledger.add(table)
        else:
            assert _references(_body(sql), names) <= _LEDGER, name
    assert derived_on_ledger == {
        "automatic_safety_state",
        "current_cover_selection_authorities",
        "field_effect_manual_holds",
        "field_effect_suspensions",
        "human_decisions",
        "metadata_policy_resolutions",
        "metadata_policy_state",
    }


def test_the_classification_names_only_objects_the_schema_defines() -> None:
    expected = {name for _, name, _ in schema._expected_schema_objects()}
    assert _LEDGER <= expected
    assert DERIVED_TABLES | DERIVED_VIEWS <= expected


def test_seeded_activity_copies_equal_what_activity_and_history_read(
    tmp_path: Path,
) -> None:
    """Owner decisions and the writes of both origins, written the way the
    product writes them, carry the copies their readers show now."""
    seeded = seed_activity(tmp_path)
    try:
        checked = assert_ledger_copies(seeded.repository)
    finally:
        seeded.repository.close()
    assert checked["activity_human_decision"] > 0
    assert checked["history_target_field"] > 0
    # Its writes stand on synthetic decisions no table holds: Activity reads
    # no report for them, and their copies are empty alike.
    assert checked["activity_field_effect_unbound"] > 0


def test_owner_writes_and_no_change_copy_what_their_readers_show(
    tmp_path: Path,
) -> None:
    """An owner's accepted, written and kept answers, and an accept of the
    value already written, carry what Activity and history show now; an
    accepted description keeps the copy its offer chose (#2003)."""
    (tmp_path / "description").mkdir(mode=0o700)
    (tmp_path / "genres").mkdir(mode=0o700)
    store = OwnerStore(tmp_path / "description", formatted_description_report())
    try:
        accept = store.decide(
            "accept",
            {"kind": "text", "value": "A qualified description."},
            destination="description",
        )
        store.write(accept)
        store.write(store.decide("keep_current", destination="description"))
        (selected_form,) = store.repository._connection.execute(
            "SELECT selected_form FROM human_decisions WHERE decision_id=?",
            (accept.decision_id,),
        ).fetchone()
        assert selected_form == OFFERED_DESCRIPTION_COPY
        described = assert_ledger_copies(store.repository)
    finally:
        store.close()
    assert described["history_selected_form"] == 1
    assert described["activity_field_effect_human"] > 0
    genres = {"kind": "list", "value": ["Fantasy"]}
    store = OwnerStore(tmp_path / "genres", genre_report())
    try:
        first = store.decide("accept", genres, destination="genres")
        store.write(first)
        assert store.repository.clear_field_effect_manual_hold(
            store.case.review_case_id, "genres", store.clock(), first.decision_id
        )
        store.decide("accept", genres, destination="genres")
        settled = assert_ledger_copies(store.repository)
    finally:
        store.close()
    assert settled["activity_no_change_human"] == 1
    assert settled["activity_human_decision"] == 2


def test_automatic_writes_copy_what_activity_shows(tmp_path: Path) -> None:
    held = genre_report(current=("Drama",), genres=("Fantasy",))
    store = OwnerStore(tmp_path, None)
    try:
        store.automatic("genres", "replace", held.target.library)
        store.publish(held)
        store.write(
            next(
                AutomaticDecision.from_json(raw)
                for raw in store.repository.get_review_case_decisions(
                    store.case.review_case_id
                )
                if json.loads(raw)["schema_version"] == "automatic-decision-v1"
                and json.loads(raw)["destination"] == "genres"
            )
        )
        checked = assert_ledger_copies(store.repository)
        titles = store.repository._connection.execute(
            "SELECT DISTINCT display_title FROM field_effects"
        ).fetchall()
    finally:
        store.close()
    assert checked["activity_field_effect_automatic"] > 0
    assert titles == [(held.target.current_title,)]
