"""Book matching as an owner setting (#1355, S2 and the A part of S4).

`matching.work_identity` is one group of the owner settings document. Saving
it writes no Activity row; it asks review preparation to re-check each current
cover comparison classified under another level, and the settings view says
how far that check has come.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from mechanical_librarian.adapters.persistence.review_preparation import (
    matching_check_counts,
    request_matching_check,
)
from mechanical_librarian.application.configuration import ReviewWebConfiguration
from mechanical_librarian.application.owner_settings import (
    FileOwnerSettingsRepository,
    OwnerSettingsOperations,
    file_settings_projection,
)
from mechanical_librarian.domain.owner_settings import (
    OWNER_SETTINGS_SCHEMA,
    MatchingCheck,
    OwnerSettingsDocument,
    OwnerSettingsError,
    WorkIdentityLevelValue,
)
from mechanical_librarian.domain.work_identity_levels import WorkIdentityLevel
from mechanical_librarian.infrastructure.configuration import (
    load_configuration_document,
)
from tests.unit.test_metadata_configuration import _review_with_work_identity
from tests.unit.test_owner_settings_store import _document, _seed_full_store


def _with_level(revision: int, level: str) -> OwnerSettingsDocument:
    value = _document(revision).model_dump()
    value["matching"] = {"work_identity": level}
    return OwnerSettingsDocument.model_validate(value)


def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]
    return {
        name: connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        for name in tables
    }


def test_the_document_holds_book_matching_and_defaults_to_identifier() -> None:
    document = _document()
    assert document.schema_version == OWNER_SETTINGS_SCHEMA
    assert document.matching.work_identity == "identifier"
    for level in ("Identifier", "exact", "", 1):
        with pytest.raises(ValidationError):
            _with_level(1, level)  # type: ignore[arg-type]
    previous = _document().model_dump(exclude={"matching"})
    previous["schema_version"] = "owner-settings-v4"
    with pytest.raises(ValidationError):
        OwnerSettingsDocument.model_validate(previous)


def test_saving_book_matching_round_trips_and_writes_no_activity(
    tmp_path: Path,
) -> None:
    repository, store = _seed_full_store(tmp_path / "matching.sqlite3")
    connection = repository._connection
    before = _row_counts(connection)

    committed = store.write(_with_level(2, "title_and_creator"), 1, (), accepted_at=100)

    assert committed.document.matching.work_identity == "title_and_creator"
    assert store.read().document.matching.work_identity == "title_and_creator"
    # An empty library: nothing to check, so nothing is being checked.
    assert committed.matching_check == MatchingCheck(
        current_works=0, checking=0, failed=0
    )
    changed = {
        name for name, count in _row_counts(connection).items() if count != before[name]
    }
    # Only the settings row changed: no Activity, event or audit row.
    assert changed <= {"owner_settings"}
    assert not any("activity" in name or "event" in name for name in changed)
    field = {item.field_id: item for item in store.read().fields}[
        "matching.work_identity"
    ]
    assert (field.managed_by_file, field.apply_effect) == (False, "applied_live")
    repository.close()


def test_a_stale_revision_is_refused_and_changes_nothing(tmp_path: Path) -> None:
    repository, store = _seed_full_store(tmp_path / "stale.sqlite3")
    store.write(_with_level(2, "title_and_creator"), 1, (), accepted_at=100)
    with pytest.raises(OwnerSettingsError, match="stale"):
        store.write(_with_level(2, "title_and_creator_exact"), 1, (), accepted_at=101)
    assert store.read().document.matching.work_identity == "title_and_creator"
    repository.close()


def test_file_authority_projects_and_locks_book_matching() -> None:
    configuration = load_configuration_document(
        json.dumps(
            _review_with_work_identity(work_identity="title_and_creator_exact")
        ).encode()
    )
    view = file_settings_projection(configuration)
    assert view.document.matching.work_identity == "title_and_creator_exact"
    assert view.authority == "file"
    assert {item.field_id: item.managed_by_file for item in view.fields}[
        "matching.work_identity"
    ]
    operations = OwnerSettingsOperations(FileOwnerSettingsRepository(view.document))
    with pytest.raises(OwnerSettingsError, match="locked"):
        operations.write(_with_level(2, "identifier"), 1, ())


def test_file_authority_reads_the_matching_check_for_its_level(tmp_path: Path) -> None:
    """File mode checks its Book matching level at startup, so Policy reads the
    check's counts for the file's level there too (#1355, amendment 5)."""
    configuration = load_configuration_document(
        json.dumps(
            _review_with_work_identity(work_identity="title_and_creator_exact")
        ).encode()
    )
    view = file_settings_projection(configuration)
    assert view.matching_check is None
    repository, _store = _seed_full_store(tmp_path / "file.sqlite3")
    asked: list[str] = []

    def counted(level: str) -> MatchingCheck:
        asked.append(level)
        return repository.matching_check(level)

    try:
        read = OwnerSettingsOperations(
            FileOwnerSettingsRepository.of(view, counted)
        ).read()
    finally:
        repository.close()
    assert asked == ["title_and_creator_exact"]
    assert read.authority == "file"
    assert read.document == view.document
    assert read.matching_check == MatchingCheck(current_works=0, checking=0, failed=0)


def test_the_review_configuration_refuses_a_level_that_is_not_a_member() -> None:
    with pytest.raises(ValueError, match="review web configuration is invalid"):
        ReviewWebConfiguration(
            canonical_origin="http://review.test",
            owner_password_hash_file="/synthetic/owner.hash",
            work_identity="title_and_creator",  # type: ignore[arg-type]
        )


def test_the_matching_check_on_an_empty_library_requeues_nothing(
    tmp_path: Path,
) -> None:
    repository, _store = _seed_full_store(tmp_path / "empty.sqlite3")
    connection = repository._connection
    before = _row_counts(connection)
    connection.execute("BEGIN")
    request_matching_check(connection, "title_and_creator", updated_at=100)
    connection.execute("COMMIT")
    assert _row_counts(connection) == before
    assert matching_check_counts(connection, "title_and_creator") == (0, 0, 0)
    repository.close()


def test_the_settings_level_values_are_the_levels() -> None:
    """Both settings models' literal is exactly the levels, in their order."""
    assert get_args(WorkIdentityLevelValue) == tuple(
        level.value for level in WorkIdentityLevel
    )
