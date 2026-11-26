"""The ledger and derived partitions of the application database (ADR-0108).

Every table and view is either ledger, as `schema.LEDGER_TABLES` and
`schema.LEDGER_VIEWS` list it, or derived, as this module pins it. A ledger
row never references a derived row, a ledger trigger or view reads only
ledger objects, and a trigger that writes derived state is derived.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence import schema
from mechanical_librarian.adapters.persistence.level_reconciliation import target_level
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.review_preparation import (
    request_matching_check,
)
from mechanical_librarian.domain.automatic_decision import AutomaticDecision
from tests.support.activity_seed import seed_activity
from tests.support.cover_taste import record_owner_cover_decisions
from tests.support.description_advice import _prepared
from tests.support.genre_reports import (
    OFFERED_DESCRIPTION_COPY,
    formatted_description_report,
    genre_report,
)
from tests.support.ledger_copies import assert_ledger_copies
from tests.support.ledger_partition import (
    UNTIL_C2B,
    copied_store,
    opens,
    regenerate,
    reopen_without_derived_rows,
    rewrite,
    rewrite_no_change,
)
from tests.support.owner_values import OwnerStore

DERIVED_TABLES = frozenset(
    {
        "automatic_cover_pointers",
        "automatic_cover_selections",
        "automatic_decisions",
        "automatic_description_decisions",
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
        "review_preparation_control",
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
        "field_effect_manual_holds",
        "field_effect_suspensions",
        "human_decisions",
        "metadata_policy_resolutions",
        "metadata_policy_state",
        "owner_cover_pointers",
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


def test_the_owner_preparation_controls_are_their_own_ledger_row(
    tmp_path: Path,
) -> None:
    """Pausing, acknowledging the setup handover and the Book matching level
    are the owner's and live in the ledger row; the scheduling row keeps when
    preparation last moved (ADR-0108)."""
    repository = open_repository(tmp_path / "reports.sqlite")
    try:
        connection = repository._connection
        owner = "SELECT paused,first_boot_acknowledged,work_identity_level FROM review_preparation_owner_control"
        assert connection.execute(owner).fetchall() == [(0, 0, "identifier")]
        repository.set_review_pipeline_paused(True, 5)
        repository.acknowledge_first_boot_preparation()
        request_matching_check(connection, "title_and_creator", updated_at=6)
        connection.commit()
        assert connection.execute(owner).fetchall() == [(1, 1, "title_and_creator")]
        assert repository.review_pipeline_paused() is True
        assert target_level(connection).value == "title_and_creator"
        assert connection.execute(
            "SELECT claim_sequence,updated_at FROM review_preparation_control"
        ).fetchall() == [(0, 5)]
    finally:
        repository.close()


def test_a_store_without_its_one_owner_control_row_is_refused(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The owner's preparation controls are one ledger row, which open
    requires as it requires the answer counter's (ADR-0108)."""
    repository = open_repository(tmp_path / "reports.sqlite")
    try:
        path = copied_store(repository, tmp_path_factory.mktemp("edited") / "copy")
    finally:
        repository.close()
    assert opens(path)
    assert rewrite(path, "DELETE FROM review_preparation_owner_control") == 1
    assert not opens(path)


def test_an_owner_authored_description_is_a_ledger_row_with_its_copies(
    tmp_path: Path,
) -> None:
    """The owner's choice of advised text keeps its work and the chosen
    candidate verbatim, the copies Activity and history read now, and the
    case's chosen advice names a decision of exactly one half (#2038)."""
    repository, store, binding, candidate, _ = _prepared(tmp_path)
    try:
        owner = store.admit(candidate.candidate_id, binding, "human", "b" * 64, 11)
        connection = repository._connection
        assert connection.execute(
            "SELECT decision_id FROM owner_authored_descriptions"
        ).fetchall() == [(owner.decision_id,)]
        assert connection.execute(
            "SELECT count(*) FROM automatic_description_decisions"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT decision_id FROM current_description_decisions"
        ).fetchall() == [(owner.decision_id,)]
        with pytest.raises(sqlite3.IntegrityError, match="description decision"):
            connection.execute(
                "UPDATE current_description_decisions SET decision_id=?",
                ("automatic_decision_" + "0" * 64,),
            )
        checked = assert_ledger_copies(repository)
        decoded = reopen_without_derived_rows(repository, tmp_path / "regen")
    finally:
        repository.close()
    assert checked["activity_owner_description"] == 1
    assert checked["history_owner_description"] == 1
    assert decoded["owner_authored_descriptions.candidate_document"] == 1


@pytest.mark.parametrize(
    ("assignment", "regenerated_opens"),
    [
        # The chosen candidate is addressed by its content, so its copy
        # verifies itself with or without the candidate row.
        (
            "candidate_document=json_set(candidate_document,'$.model','another-model')",
            False,
        ),
        ("item_id='another-book'", True),
        ("display_title='Another title'", True),
    ],
)
def test_an_owner_authored_description_agrees_with_its_case_and_candidate(
    tmp_path: Path, assignment: str, regenerated_opens: bool
) -> None:
    """While the candidate and the case are there, the copy and the work must
    equal them; once a regeneration took them, the work stands as copied."""
    repository, store, binding, candidate, _ = _prepared(tmp_path)
    try:
        store.admit(candidate.candidate_id, binding, "human", "b" * 64, 11)
        path = copied_store(repository, tmp_path / "changed")
    finally:
        repository.close()
    rewrite(path, f"UPDATE owner_authored_descriptions SET {assignment}")
    if not assignment.startswith("candidate_document"):
        # The record of the owner's answers copies the work and the title
        # too, so an edit that stands once regenerated moves both (ADR-0108).
        rewrite(path, f"UPDATE owner_field_answers SET {assignment}")
    assert not opens(path)
    regenerate(path)
    assert opens(path) is regenerated_opens


@pytest.mark.parametrize(
    "edits",
    [
        (
            "DELETE FROM owner_authored_descriptions",
            "DELETE FROM owner_field_answers",
        ),
        (
            "UPDATE current_description_decisions "
            "SET decision_id='human_decision_" + "0" * 64 + "'",
        ),
    ],
    ids=["decision_deleted", "pointer_moved"],
)
def test_a_case_chosen_description_names_a_decision_of_one_half(
    tmp_path: Path, edits: tuple[str, ...]
) -> None:
    """The case's chosen advice is derived and goes with a regeneration;
    while it is there it names a decision in exactly one half (#2038)."""
    repository, store, binding, candidate, _ = _prepared(tmp_path)
    try:
        store.admit(candidate.candidate_id, binding, "human", "b" * 64, 11)
        path = copied_store(repository, tmp_path / "changed")
    finally:
        repository.close()
    for sql in edits:
        rewrite(path, sql)
    assert not opens(path)
    regenerate(path)
    assert opens(path)


# The first owner decision of the seeded Activity store that no plan or
# no-change row names; a later answer replaced it in the record, so only its
# events and its case hold it.
_UNWRITTEN = (
    "(SELECT decision_id FROM human_decisions decision WHERE NOT EXISTS("
    "SELECT 1 FROM field_effects WHERE decision_id=decision.decision_id) "
    "AND NOT EXISTS(SELECT 1 FROM field_effect_no_changes "
    "WHERE decision_id=decision.decision_id) ORDER BY ordinal LIMIT 1)"
)
# Each copy moved wherever the ledger holds it, so only the case or the
# transition's items, while they are there, can tell (ADR-0108).
_CASE_AND_ITEM_COPIES = {
    # Every owner decision of the seeded field moves, with its events and
    # its record, since open folds the record from them all (ADR-0108).
    "decision_item_key": (
        "UPDATE human_decision_events SET item_id='another-book'",
        "UPDATE human_decisions SET item_id='another-book'",
        "UPDATE owner_field_answers SET item_id='another-book' WHERE source='human'",
    ),
    "display_title": (
        f"UPDATE human_decisions SET display_title='Another title' "
        f"WHERE decision_id={_UNWRITTEN}",
    ),
    "target_field_document": (
        "UPDATE human_decisions SET target_field_document=CASE "
        "target_field_document WHEN 'null' THEN '{}' ELSE 'null' END "
        f"WHERE decision_id={_UNWRITTEN}",
    ),
    "target_fingerprint": (
        f"UPDATE human_decisions SET target_fingerprint='{'0' * 64}',"
        f"document=json_set(document,'$.target_fingerprint','{'0' * 64}') "
        f"WHERE decision_id={_UNWRITTEN}",
    ),
    # Its state stays the one the counts give.
    "transition_counts": (
        "UPDATE metadata_policy_transitions SET completed=completed+1,"
        "failed=failed-1 WHERE failed>1",
    ),
}
# Edits no store opens with, its derived rows there or not: an owner
# decision's event names the decision's case, and a transition's state is the
# one its size and settled counts give.
_LEDGER_DISAGREEMENTS = {
    "event_case": (
        "UPDATE human_decision_events SET review_case_id='review_case_"
        + "0" * 64
        + "'",
    ),
    "transition_state": (
        "UPDATE metadata_policy_transitions SET state=CASE state "
        "WHEN 'processing' THEN 'completed' ELSE 'processing' END",
    ),
    "transition_total": (
        "UPDATE metadata_policy_transitions SET total=total+1 "
        "WHERE state<>'processing'",
    ),
}


@pytest.fixture
def activity(tmp_path: Path) -> Path:
    """A copy of the seeded Activity store, with its derived rows."""
    (tmp_path / "seed").mkdir(mode=0o700)
    seeded = seed_activity(tmp_path / "seed")
    try:
        return copied_store(seeded.repository, tmp_path / "activity")
    finally:
        seeded.repository.close()


@pytest.mark.parametrize("copy", sorted(_CASE_AND_ITEM_COPIES))
def test_a_ledger_copy_agrees_with_its_derived_row_while_it_is_there(
    activity: Path, copy: str
) -> None:
    """An owner decision's work, fingerprint, title and target field are its
    case's, and a transition's settled counts its items': cases never change
    and the counts settle with the items, so while they are there each copy
    equals them; once a regeneration took them, the copy stands."""
    for sql in _CASE_AND_ITEM_COPIES[copy]:
        rewrite(activity, sql)
    assert not opens(activity)
    regenerate(activity)
    assert opens(activity)


@pytest.mark.parametrize("disagreement", sorted(_LEDGER_DISAGREEMENTS))
def test_an_event_and_a_transition_state_agree_with_the_ledger_always(
    activity: Path, disagreement: str
) -> None:
    for sql in _LEDGER_DISAGREEMENTS[disagreement]:
        rewrite(activity, sql)
    assert not opens(activity)
    regenerate(activity)
    assert not opens(activity)


def _owner_genres(root: Path) -> Path:
    """A copy of a store whose owner wrote genres and then accepted the
    written value again: a plan and a no-change row, both the owner's."""
    root.mkdir(mode=0o700)
    genres = {"kind": "list", "value": ["Fantasy"]}
    store = OwnerStore(root, genre_report())
    try:
        first = store.decide("accept", genres, destination="genres")
        store.write(first)
        assert store.repository.clear_field_effect_manual_hold(
            store.case.review_case_id, "genres", store.clock(), first.decision_id
        )
        store.decide("accept", genres, destination="genres")
        return copied_store(store.repository, root / "copy")
    finally:
        store.close()


@pytest.mark.parametrize("table", ["field_effects", "field_effect_no_changes"])
def test_a_plan_and_a_no_change_title_agree_with_their_report(
    tmp_path: Path, table: str
) -> None:
    """A write plan and a no-change row show the title of the report their
    decision was made over; reports never change, so while it is there the
    copy equals it, and once a regeneration took it the copy stands."""
    path = _owner_genres(tmp_path / "genres")
    rewrite(path, f"UPDATE {table} SET display_title='Another title'")
    assert not opens(path)
    regenerate(path)
    assert opens(path)


# What open validation cannot check once the derived rows a ledger value was
# copied from, or names, are gone (ADR-0108), by class. Each entry names a
# ledger edit that, on a store whose derived partition was regenerated, still
# opens. A check added for one of them, or a gap added, changes this list
# deliberately.
ACCEPTED_GAPS = (
    # Display copies; the selected form is never checked.
    "display_title",
    "target_field_document",
    "selected_form",
    "authored_description_display_title",
    "plan_display_title",
    "no_change_display_title",
    # An owner cover choice's copies of its comparison.
    "preview_digest",
    "original_digest",
    "transport_digest",
    "observed_identity",
    "rank_key",
    "consideration_digest",
    "taste_copies",
    # The case episode an owner answer was given in, which open's fold of
    # the record compares only with the episode of the answer it replaces.
    "decision_episode",
    "authored_description_episode",
    # A settled transition's counts, its state kept.
    "transition_counts",
    # References into derived state.
    "decision_provenance",
    "cover_comparison_reference",
    "absent_cover_predecessor",
    "no_change_report_id",
    "no_change_target_version",
    "item_key_without_ledger_reference",
    "decision_item_key_without_ledger_reference",
)
_OTHER_DIGEST = "0" * 64
# An owner cover choice made to supersede a selection that is not there.
_SUPERSEDES_ABSENT = (
    "UPDATE owner_cover_selections SET supersedes_selection_id='cover_selection_"
    f"{_OTHER_DIGEST}',document=json_set(document,"
    f"'$.supersedes_selection_id','cover_selection_{_OTHER_DIGEST}',"
    "'$.confirmation.expected_selection_id','cover_selection_"
    f"{_OTHER_DIGEST}')"
)


def _regenerated(path: Path, root: Path) -> Path:
    """A regenerated copy of the store at ``path``, under a new ``root``."""
    root.mkdir(mode=0o700)
    copy = root / "copy.sqlite3"
    shutil.copyfile(path, copy)
    return copy


@pytest.fixture
def owner_cover(tmp_path: Path) -> Iterator[Path]:
    """A copy of a store holding one owner cover choice made through the
    real decision command, with its comparison and report."""
    repository = open_repository(tmp_path / "reports.sqlite")
    try:
        record_owner_cover_decisions(tmp_path, repository, 1)
        yield copied_store(repository, tmp_path / "store")
    finally:
        repository.close()


def test_a_regenerated_store_opens_and_every_ledger_row_decodes(
    tmp_path: Path,
) -> None:
    """Every derived row deleted, the store opens unchanged and each ledger
    document decodes: owner decisions and their events, the writes of both
    origins and their events, and the policy (ADR-0108)."""
    seeded = seed_activity(tmp_path)
    try:
        decoded = reopen_without_derived_rows(seeded.repository, tmp_path / "regen")
    finally:
        seeded.repository.close()
    for table in (
        "human_decisions",
        "human_decision_events",
        "field_effects",
        "field_effect_events",
        "field_effect_authorities",
        "field_effect_consents",
        "metadata_policy_transitions",
        "metadata_policy_resolutions",
    ):
        assert decoded[table] > 0, table
    assert decoded["human_decisions.display_title"] == decoded["human_decisions"]
    assert (
        decoded["human_decisions.target_field_document"] == decoded["human_decisions"]
    )


def test_only_the_manual_holds_still_reference_derived_state() -> None:
    """A regeneration deletes exactly the ledger rows that still point into
    derived state, which C2b removes with the holds themselves."""
    assert {table for table, _, _ in KNOWN_LEDGER_TO_DERIVED_KEYS} == UNTIL_C2B


def test_an_owner_cover_choice_stands_without_its_comparison(
    owner_cover: Path, tmp_path: Path
) -> None:
    regenerated = _regenerated(owner_cover, tmp_path / "regen")
    regenerate(regenerated)
    assert opens(owner_cover)
    assert opens(regenerated)
    connection = sqlite3.connect(regenerated)
    try:
        assert connection.execute(
            "SELECT count(*),count(width),count(report_format) "
            "FROM owner_cover_selections"
        ).fetchone() == (1, 1, 1)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "assignment",
    [
        "transport_sha256='" + _OTHER_DIGEST + "'",
        "width=width+1",
        "height=height+1",
        "offered_arts=offered_arts+1",
        "report_format='mp3'",
        "document=json_set(document,'$.consideration_digest','" + _OTHER_DIGEST + "')",
        "document=json_set(document,'$.preview_sha256','" + _OTHER_DIGEST + "')",
        "document=json_set(document,'$.original_sha256','" + _OTHER_DIGEST + "')",
        "document=json_set(document,'$.rank_key',json('[0]'))",
        "document=json_set(document,'$.observed_identity',json('null'))",
    ],
)
def test_an_owner_cover_copy_agrees_with_its_comparison_while_it_is_there(
    owner_cover: Path, tmp_path: Path, assignment: str
) -> None:
    """Present, the comparison and report each copy was read from must
    agree with it; once a regeneration took them, the copy stands."""
    rewrite(owner_cover, f"UPDATE owner_cover_selections SET {assignment}")
    assert not opens(owner_cover)
    regenerate(owner_cover)
    assert opens(owner_cover)


@pytest.mark.parametrize(
    "assignment",
    [
        # The cover the accept chose is the decision's, not the comparison's.
        "document=json_set(document,'$.candidate_artifact_id','cover_"
        + _OTHER_DIGEST
        + "')",
        "document=json_set(document,'$.confirmation.expected_selection_id',"
        "'cover_selection_" + _OTHER_DIGEST + "')",
        "document=json_set(document,'$.candidate_kind',json('null'))",
        # The target's cover field is the one the decision copied.
        "document=json_set(document,'$.target.fields[15].value','"
        + _OTHER_DIGEST
        + "')",
        "document=json_set(document,'$.target.item','item-9')",
        "document=json_set(document,'$.decision_document',"
        "json_set(json_extract(document,'$.decision_document'),"
        "'$.occurred_at',201))",
    ],
)
def test_an_owner_cover_choice_agrees_with_its_decision_always(
    owner_cover: Path, tmp_path: Path, assignment: str
) -> None:
    rewrite(owner_cover, f"UPDATE owner_cover_selections SET {assignment}")
    assert not opens(owner_cover)
    regenerate(owner_cover)
    assert not opens(owner_cover)


def test_an_owner_cover_choice_names_a_present_predecessor_while_compared(
    owner_cover: Path,
) -> None:
    """A regeneration takes Automatic's selection with the comparison an
    owner's choice was made over, never one without the other: while the
    comparison is there, the selection it superseded is there too."""
    rewrite(owner_cover, _SUPERSEDES_ABSENT)
    assert not opens(owner_cover)
    regenerate(owner_cover)
    assert opens(owner_cover)


def test_an_owner_no_change_agrees_with_its_case_while_it_is_there(
    tmp_path: Path,
) -> None:
    """An owner's accept of the value already written names its case's
    report while that case is there; regenerated, the row stands."""
    genres = {"kind": "list", "value": ["Fantasy"]}
    store = OwnerStore(tmp_path, genre_report())
    try:
        first = store.decide("accept", genres, destination="genres")
        store.write(first)
        assert store.repository.clear_field_effect_manual_hold(
            store.case.review_case_id, "genres", store.clock(), first.decision_id
        )
        store.decide("accept", genres, destination="genres")
        decoded = reopen_without_derived_rows(store.repository, tmp_path / "regen")
        path = copied_store(store.repository, tmp_path / "changed")
    finally:
        store.close()
    assert decoded["field_effect_no_changes.display_title"] == 1
    rewrite_no_change(path, "report_id", "report_" + _OTHER_DIGEST)
    assert not opens(path)
    regenerate(path)
    assert opens(path)
    # The accept it names is ledger, and must hold the row's value always.
    rewrite_no_change(path, "target_version", _OTHER_DIGEST)
    assert not opens(path)


@pytest.mark.parametrize("moved", ["decision", "decision_and_events"])
def test_every_ledger_row_naming_an_owner_decision_names_its_work(
    tmp_path: Path, moved: str
) -> None:
    """A decision moved to another work, alone or with its events, disagrees
    with the plan, no-change row and events that name it: open refuses with
    or without the derived rows."""
    genres = {"kind": "list", "value": ["Fantasy"]}
    store = OwnerStore(tmp_path, genre_report())
    try:
        first = store.decide("accept", genres, destination="genres")
        store.write(first)
        assert store.repository.clear_field_effect_manual_hold(
            store.case.review_case_id, "genres", store.clock(), first.decision_id
        )
        settled = store.decide("accept", genres, destination="genres")
        path = copied_store(store.repository, tmp_path / "changed")
    finally:
        store.close()
    tables = ("human_decisions",) + (
        ("human_decision_events",) if moved == "decision_and_events" else ()
    )
    for decision_id in (first.decision_id, settled.decision_id):
        for table in tables:
            rewrite(
                path,
                f"UPDATE {table} SET item_id='another-book' WHERE decision_id=?",
                (decision_id,),
            )
        assert not opens(path)
        regenerate(path)
        assert not opens(path)


def test_an_owner_cover_choice_names_its_decisions_work(
    owner_cover: Path,
) -> None:
    for table in ("human_decisions", "human_decision_events"):
        rewrite(owner_cover, f"UPDATE {table} SET item_id='item-9'")
    regenerate(owner_cover)
    assert not opens(owner_cover)


def _gap_stores(root: Path, owner_cover: Path) -> dict[str, Path]:
    """A copy of each store an accepted gap is shown on, derived rows and
    all: the seeded Activity store, an owner cover choice, an owner's genres
    plan and no-change row, an accepted description and its selected form,
    and an owner authored description written and settled with no change."""
    root.mkdir(mode=0o700)
    (root / "seed").mkdir(mode=0o700)
    seeded = seed_activity(root / "seed")
    try:
        activity = copied_store(seeded.repository, root / "activity")
    finally:
        seeded.repository.close()
    (root / "advice").mkdir(mode=0o700)
    repository, store, binding, candidate, _ = _prepared(root / "advice")
    try:
        store.admit(candidate.candidate_id, binding, "human", "b" * 64, 11)
        advice = copied_store(repository, root / "advice-store")
    finally:
        repository.close()
    (root / "same").mkdir(mode=0o700)
    repository, store, binding, candidate, _ = _prepared(
        root / "same", same_current=True
    )
    try:
        authored = store.admit(candidate.candidate_id, binding, "human", "a" * 64, 11)
        assert repository.record_current_field_effect_no_change(authored, 12)
        authored_no_change = copied_store(repository, root / "same-store")
    finally:
        repository.close()
    (root / "described").mkdir(mode=0o700)
    owner = OwnerStore(root / "described", formatted_description_report())
    try:
        owner.write(
            owner.decide(
                "accept",
                {"kind": "text", "value": "A qualified description."},
                destination="description",
            )
        )
        described = copied_store(owner.repository, root / "described-store")
    finally:
        owner.close()
    return {
        "activity": activity,
        "advice": advice,
        "authored_no_change": authored_no_change,
        "cover": owner_cover,
        "described": described,
        "genres": _owner_genres(root / "genres"),
    }


def _no_change(column: str, value: str) -> Callable[[Path], None]:
    return lambda path: rewrite_no_change(path, column, value)


def test_accepted_gaps_are_the_listed_ones(owner_cover: Path, tmp_path: Path) -> None:
    """Each listed gap is a ledger value that, its derived rows gone, open
    cannot check; this test edits each and the store still opens."""
    stores = _gap_stores(tmp_path / "stores", owner_cover)
    selection = "UPDATE owner_cover_selections SET "
    case = "review_case_" + _OTHER_DIGEST
    # Every edit to an owner answer the record holds moves the record too, as
    # its copy of the answer must equal it (ADR-0108).
    gaps: dict[str, tuple[str, *tuple[str | Callable[[Path], None], ...]]] = {
        "display_title": (
            "activity",
            "UPDATE human_decisions SET display_title='Another title'",
            "UPDATE owner_field_answers SET display_title='Another title' "
            "WHERE source='human'",
        ),
        "target_field_document": (
            "activity",
            "UPDATE human_decisions SET target_field_document='null'",
        ),
        "selected_form": (
            "described",
            "UPDATE human_decisions SET selected_form='Another form' "
            "WHERE selected_form IS NOT NULL",
        ),
        "authored_description_display_title": (
            "advice",
            "UPDATE owner_authored_descriptions SET display_title='Another title'",
            "UPDATE owner_field_answers SET display_title='Another title'",
        ),
        "plan_display_title": (
            "genres",
            "UPDATE field_effects SET display_title='Another title'",
        ),
        "no_change_display_title": (
            "genres",
            "UPDATE field_effect_no_changes SET display_title='Another title'",
        ),
        "preview_digest": (
            "cover",
            selection + "document=json_set(document,'$.preview_sha256','"
            f"{_OTHER_DIGEST}')",
        ),
        "original_digest": (
            "cover",
            selection + "document=json_set(document,'$.original_sha256','"
            f"{_OTHER_DIGEST}')",
        ),
        "transport_digest": (
            "cover",
            selection + f"transport_sha256='{_OTHER_DIGEST}'",
        ),
        "observed_identity": (
            "cover",
            selection + "document=json_set(document,'$.observed_identity',"
            "json('null'))",
        ),
        "rank_key": (
            "cover",
            selection + "document=json_set(document,'$.rank_key',json('[0]'))",
        ),
        "consideration_digest": (
            "cover",
            selection + "document=json_set(document,'$.consideration_digest','"
            f"{_OTHER_DIGEST}')",
        ),
        "taste_copies": (
            "cover",
            selection + "width=1,height=1,offered_arts=1,report_format='mp3'",
        ),
        # Every answer of the case's episode moves with it, so the record's
        # fold, which compares an answer's episode with the one it replaces,
        # reads the same answers.
        "decision_episode": (
            "activity",
            "UPDATE human_decisions SET document=json_set(document,'$.episode',"
            "json_extract(document,'$.episode')+1)",
            "UPDATE owner_field_answers SET episode=episode+1 WHERE source='human'",
        ),
        # Its only answer is the first, so an episode before it needs a
        # later ordinal.
        "authored_description_episode": (
            "advice",
            "UPDATE owner_answer_ordinal SET ordinal=ordinal+1",
            "UPDATE owner_authored_descriptions SET ordinal=ordinal+1,episode=episode+1",
            "UPDATE owner_field_answers SET ordinal=ordinal+1,episode=episode+1",
        ),
        "transition_counts": (
            "activity",
            _CASE_AND_ITEM_COPIES["transition_counts"][0],
        ),
        # The case, proposal and fingerprint an owner decision was made on.
        # The case moves for every answer of the field and its record, which
        # the fold reads together.
        "decision_provenance": (
            "activity",
            f"UPDATE human_decisions SET review_case_id='{case}',"
            f"document=json_set(document,'$.review_case_id','{case}')",
            f"UPDATE human_decision_events SET review_case_id='{case}'",
            f"UPDATE owner_field_answers SET review_case_id='{case}' "
            "WHERE source='human'",
            "UPDATE human_decisions SET proposal_id='review_proposal_"
            f"{_OTHER_DIGEST}',document=json_set(document,'$.proposal_id',"
            f"'review_proposal_{_OTHER_DIGEST}') WHERE decision_id={_UNWRITTEN}",
            _CASE_AND_ITEM_COPIES["target_fingerprint"][0],
        ),
        "cover_comparison_reference": (
            "cover",
            selection + f"consideration_id='cover_consideration_{_OTHER_DIGEST}',"
            "document=json_set(document,'$.consideration_id',"
            f"'cover_consideration_{_OTHER_DIGEST}')",
        ),
        # An owner's choice may supersede one of Automatic's, which a
        # regeneration takes; so its predecessor may be absent.
        "absent_cover_predecessor": ("cover", _SUPERSEDES_ABSENT),
        "no_change_report_id": (
            "genres",
            _no_change("report_id", "report_" + _OTHER_DIGEST),
        ),
        # An advised text's no-change row is at the report's version.
        "no_change_target_version": (
            "authored_no_change",
            _no_change("target_version", _OTHER_DIGEST),
        ),
        # No plan or no-change row names this authored description yet.
        "item_key_without_ledger_reference": (
            "advice",
            "UPDATE owner_authored_descriptions SET item_id='another-book'",
            "UPDATE owner_field_answers SET item_id='another-book'",
        ),
        # Nor this owner decision; its events move with it.
        "decision_item_key_without_ledger_reference": (
            "activity",
            *_CASE_AND_ITEM_COPIES["decision_item_key"],
        ),
    }
    assert tuple(gaps) == ACCEPTED_GAPS
    for name, (store, *edits) in gaps.items():
        changed = _regenerated(stores[store], tmp_path / name)
        regenerate(changed)
        for edit in edits:
            if isinstance(edit, str):
                assert rewrite(changed, edit) > 0, (name, edit)
            else:
                edit(changed)
        assert opens(changed), name
