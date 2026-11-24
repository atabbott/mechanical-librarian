"""The ledger and derived partitions of the application database (ADR-0108).

Every table and view is either ledger, as `schema.LEDGER_TABLES` and
`schema.LEDGER_VIEWS` list it, or derived, as this module pins it. A ledger
row never references a derived row, a ledger trigger or view reads only
ledger objects, and a trigger that writes derived state is derived.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator

import pytest

from mechanical_librarian.adapters.persistence import schema

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
        # #2038 C2a gives decisions and no-change rows their item key.
        ("field_effect_no_changes", "report_id", "metadata_reports"),
        ("field_effect_no_changes", "review_case_id", "review_cases"),
        ("human_decision_events", "review_case_id", "review_cases"),
        ("human_decisions", "review_case_id", "review_cases"),
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
