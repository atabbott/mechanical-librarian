import sqlite3
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.owner_settings import OwnerSettingsStore
from mechanical_librarian.adapters.persistence.preparation_presentation import (
    refresh_preparation_presentation,
)
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
    CoverConsiderationStore,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.metadata_report import MetadataReportError
from mechanical_librarian.application.owner_settings import FileOwnerSettingsRepository
from mechanical_librarian.domain.owner_settings import (
    OwnerSettingsDocument,
    OwnerSettingsError,
    SecretWrite,
    file_field_metadata,
)
from mechanical_librarian.domain.preparation_presentation import PreparationTrigger


def _document(revision: int = 1) -> OwnerSettingsDocument:
    return OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v2",
            "revision": revision,
            "connections": [
                {"name": "primary", "endpoint": "https://library.example.invalid"}
            ],
            "providers": [
                {"name": "books", "adapter": "google_books", "enabled": True},
                {
                    "name": "open",
                    "adapter": "open_library",
                    "enabled": True,
                    "contact_email": "owner@example.invalid",
                },
            ],
            "libraries": [
                {
                    "name": "ebooks",
                    "connection": "primary",
                    "library_id": "library-1",
                    "media_kind": "ebook",
                    "provider_connections": ["books", "open"],
                    "mount": "books",
                }
            ],
        }
    )


class _FailingCommitConnection(sqlite3.Connection):
    fail_commit = False

    def commit(self) -> None:
        if self.fail_commit:
            raise sqlite3.OperationalError("synthetic commit fault")
        super().commit()


def _store(connection: sqlite3.Connection | None = None) -> OwnerSettingsStore:
    connection = connection or sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE owner_settings (singleton INTEGER PRIMARY KEY NOT NULL CHECK(singleton=1),revision INTEGER NOT NULL CHECK(revision>=1),document TEXT NOT NULL)"
    )
    connection.execute(
        "CREATE TABLE owner_setting_secrets (field_id TEXT PRIMARY KEY NOT NULL,value TEXT NOT NULL)"
    )
    value = _document()
    connection.execute(
        "INSERT INTO owner_settings VALUES(1,?,?)",
        (value.revision, _canonical_json(value.model_dump())),
    )
    connection.execute(
        "INSERT INTO owner_setting_secrets VALUES(?,?)",
        ("connection.primary.key", "target-secret-1234"),
    )
    connection.commit()
    return OwnerSettingsStore(connection)


def _cover_document(revision: int) -> OwnerSettingsDocument:
    value = _document(revision).model_dump()
    value["cover_preferences"] = {
        "avoid_promotional_text": True,
        "avoid_tie_in_branding": True,
        "prefer_minimal_composition": True,
    }
    return OwnerSettingsDocument.model_validate(value)


def _settings_with_libraries(
    revision: int, names: tuple[str, ...]
) -> OwnerSettingsDocument:
    value = _document(revision).model_dump()
    template = value["libraries"][0]
    value["libraries"] = [
        {**template, "name": name, "library_id": f"library-{index}"}
        for index, name in enumerate(names, 1)
    ]
    return OwnerSettingsDocument.model_validate(value)


def _seed_full_store(
    path: Path, names: tuple[str, ...] = ("ebooks",)
) -> tuple[object, OwnerSettingsStore]:
    repository = open_repository(path)
    document = _settings_with_libraries(1, names)
    repository._connection.execute(
        "INSERT INTO owner_settings VALUES(1,?,?)",
        (document.revision, _canonical_json(document.model_dump())),
    )
    repository._connection.executemany(
        "INSERT INTO owner_setting_secrets VALUES(?,?)",
        (
            ("connection.primary.key", "target-secret-1234"),
            ("provider.books.key", "provider-secret-1234"),
            ("owner.password.verifier", "$argon2id$synthetic"),
        ),
    )
    repository._connection.commit()
    return repository, repository.owner_settings_store()


def test_document_rejects_noncanonical_references_and_bounds() -> None:
    value = _document().model_dump()
    value["libraries"][0]["provider_connections"] = ["open", "books"]
    with pytest.raises(ValidationError, match="duplicated"):
        OwnerSettingsDocument.model_validate(value)
    value = _document().model_dump()
    value["connections"][0]["endpoint"] = "x" * 2049
    with pytest.raises(ValidationError):
        OwnerSettingsDocument.model_validate(value)


def test_library_display_name_is_optional_canonical_and_preserves_exact_punctuation() -> (
    None
):
    legacy = _document()
    assert "display_name" not in legacy.model_dump()["libraries"][0]

    value = legacy.model_dump()
    value["libraries"][0]["display_name"] = "Ebooks & Audiobooks: Collected Editions"
    document = OwnerSettingsDocument.model_validate(value)

    assert (
        document.libraries[0].display_name == "Ebooks & Audiobooks: Collected Editions"
    )
    assert "display_name" in document.model_dump()["libraries"][0]


@pytest.mark.parametrize("display_name", (" leading", "trailing ", "nul\x00name"))
def test_library_display_name_rejects_noncanonical_text(display_name: str) -> None:
    value = _document().model_dump()
    value["libraries"][0]["display_name"] = display_name

    with pytest.raises((OwnerSettingsError, ValidationError)):
        OwnerSettingsDocument.model_validate(value)


def test_maximum_bounded_document_fits_the_persistence_and_response_contract() -> None:
    value = _document().model_dump()
    value["connections"] = [
        {
            "name": f"connection-{index:02d}",
            "endpoint": "https://" + "x" * 2032 + ".invalid",
            "adapter": "audiobookshelf",
        }
        for index in range(32)
    ]
    value["providers"] = [
        {
            "name": f"provider-{index:02d}",
            "adapter": "google_books",
            "enabled": True,
            "cover_enabled": False,
        }
        for index in range(32)
    ]
    value["libraries"] = [
        {
            "name": f"library-{index:03d}",
            "connection": f"connection-{index % 32:02d}",
            "library_id": "x" * 128,
            "media_kind": "ebook",
            "provider_connections": [
                f"provider-{provider:02d}" for provider in range(32)
            ],
            "mount": None,
            "policy_override": None,
        }
        for index in range(128)
    ]
    document = OwnerSettingsDocument.model_validate(value)
    encoded = document.model_dump_json().encode()
    assert 65_536 < len(encoded) <= 262_144


@pytest.mark.parametrize(
    "value",
    [" token", "token ", "Bearer token", "line\nbreak", "é", "x" * 2049],
)
def test_secret_write_uses_the_adapter_credential_contract(value: str) -> None:
    with pytest.raises(ValidationError):
        SecretWrite(field_id="connection.primary.key", operation="set", value=value)


def test_secret_write_hides_its_value_and_marks_it_write_only() -> None:
    canary = "settings-secret-repr-canary"
    value = SecretWrite(
        field_id="connection.primary.key", operation="replace", value=canary
    )

    assert canary not in repr(value)
    assert SecretWrite.model_json_schema()["properties"]["value"]["writeOnly"] is True


def test_store_returns_redacted_secret_presence_and_metadata() -> None:
    view = _store().read()
    assert view.authority == "store"
    assert view.secrets[0].model_dump() == {
        "field_id": "connection.primary.key",
        "configured": True,
        "indicator": "Configured · ····1234",
    }
    assert all(
        "target-secret" not in str(value)
        for value in (view.model_dump(), view.model_dump_json())
    )
    assert (
        next(
            value for value in view.fields if value.field_id == "reduced_motion"
        ).apply_effect
        == "applied_live"
    )
    assert (
        next(
            value for value in view.fields if value.field_id == "connections"
        ).apply_effect
        == "applied_live"
    )
    assert next(
        value for value in view.fields if value.field_id == "mounts"
    ).managed_by_file
    assert all(
        item.managed_by_file and item.managed_path == "/data/config.json"
        for item in file_field_metadata(view.document)
    )


def test_store_and_live_cover_authority_advance_or_roll_back_together() -> None:
    connection = sqlite3.connect(":memory:", factory=_FailingCommitConnection)
    store = _store(connection)
    changed = CoverPolicy(True, True, True)
    bound: dict[str, str] = {}
    fail_binding = True

    def bind(policy_reference: str, replay_reference: str) -> None:
        if fail_binding and policy_reference == changed.reference:
            raise RuntimeError("synthetic binding fault")
        bound.update(policy=policy_reference, replay=replay_reference)

    service = CoverConsiderationService(
        cast("CoverConsiderationStore", object()),
        read_preview=lambda _artifact, _variant: b"",
        read_preview_digest=lambda _digest: b"",
        clock=lambda: 0,
        bind_policy=bind,
    )
    previous = CoverPolicy()
    service.update_policy(previous)
    previous_replay = service.replay_reference

    with pytest.raises(RuntimeError, match="binding fault"):
        store.write(_cover_document(2), 1, (), service.policy_update(changed))

    assert store.read().document.revision == 1
    assert store.read().document.cover_preferences == _document().cover_preferences
    assert service._policy.reference == previous.reference
    assert service.replay_reference == previous_replay
    assert bound == {"policy": previous.reference, "replay": previous_replay}

    fail_binding = False
    connection.fail_commit = True
    with pytest.raises(OwnerSettingsError, match="unavailable"):
        store.write(_cover_document(2), 1, (), service.policy_update(changed))

    assert store.read().document.revision == 1
    assert service._policy.reference == previous.reference
    assert service.replay_reference == previous_replay
    assert bound == {"policy": previous.reference, "replay": previous_replay}

    connection.fail_commit = False
    committed = store.write(_cover_document(2), 1, (), service.policy_update(changed))

    assert committed.document == store.read().document
    assert committed.document.revision == 2
    assert committed.document.cover_preferences == _cover_document(2).cover_preferences
    assert service._policy.reference == changed.reference
    assert bound == {"policy": changed.reference, "replay": service.replay_reference}


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE owner_settings SET revision=revision+1",
        "UPDATE owner_settings SET document=' '||document",
        "INSERT INTO owner_setting_secrets VALUES('provider.unknown.key','secret-1234')",
        "INSERT INTO owner_setting_secrets VALUES('INVALID','secret-1234')",
        "INSERT INTO owner_setting_secrets VALUES('provider.books.key','bad secret')",
        "DELETE FROM owner_settings",
    ),
)
def test_store_read_refuses_corrupt_canonical_authority(statement: str) -> None:
    store = _store()
    store._connection.execute(statement)
    store._connection.commit()

    with pytest.raises(OwnerSettingsError, match="owner settings are unavailable"):
        store.read()


def test_store_is_atomic_and_enforces_cas_and_secret_operations() -> None:
    store = _store()
    updated = _document(2)
    result = store.write(
        updated,
        1,
        (
            SecretWrite(
                field_id="provider.books.key",
                operation="set",
                value="provider-secret-5678",
            ),
        ),
    )
    assert result.document.revision == 2
    assert [value.field_id for value in result.secrets if value.configured] == [
        "connection.primary.key",
        "provider.books.key",
    ]
    with pytest.raises(OwnerSettingsError, match="stale"):
        store.write(_document(2), 1, ())
    next_document = result.document.model_copy(update={"revision": 3})
    with pytest.raises(OwnerSettingsError, match="operation"):
        store.write(
            next_document,
            2,
            (
                SecretWrite(
                    field_id="provider.books.key", operation="set", value="second"
                ),
            ),
        )
    with pytest.raises(OwnerSettingsError, match="required secret"):
        store.write(
            next_document,
            2,
            (SecretWrite(field_id="connection.primary.key", operation="clear"),),
        )
    assert store.read().document.revision == 2


@pytest.mark.parametrize("failed_ordinal", (0, 1))
def test_settings_addition_intents_share_the_settings_transaction(
    tmp_path: Path, failed_ordinal: int
) -> None:
    repository, store = _seed_full_store(tmp_path / "settings-intents.sqlite3")
    candidate = _settings_with_libraries(2, ("ebooks", "history", "poetry"))
    failed_name = ("history", "poetry")[failed_ordinal]
    repository._connection.execute(
        "CREATE TEMP TRIGGER fail_settings_intent BEFORE INSERT ON review_preparation_presentation "
        f"WHEN NEW.row_kind='deferred_enrollment' AND NEW.library_name='{failed_name}' "
        "BEGIN SELECT RAISE(ABORT,'synthetic intent failure'); END"
    )

    with pytest.raises(OwnerSettingsError, match="unavailable"):
        store.write(candidate, 1, (), accepted_at=100)

    assert repository._connection.in_transaction is False
    assert store.read().document.revision == 1
    assert repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_presentation"
    ).fetchone() == (0,)
    repository._connection.execute("DROP TRIGGER fail_settings_intent")
    committed = store.write(candidate, 1, (), accepted_at=100)
    rows = repository._connection.execute(
        "SELECT library_name,ordinal,accepted_at,settings_revision "
        "FROM review_preparation_presentation WHERE row_kind='deferred_enrollment' "
        "ORDER BY ordinal,row_id"
    ).fetchall()
    trigger = repository._connection.execute(
        "SELECT accepted_count,first_accepted_at,last_accepted_at "
        "FROM review_preparation_presentation WHERE row_id='trigger:library_added'"
    ).fetchone()
    repository._connection.execute(
        "CREATE TEMP TRIGGER fail_settings_drain BEFORE INSERT ON review_preparation_scans "
        f"WHEN NEW.library_name='{failed_name}' "
        "BEGIN SELECT RAISE(ABORT,'synthetic drain failure'); END"
    )
    scope = (
        ("primary", "ebooks", "library-1", "f" * 64),
        ("primary", "history", "library-2", "f" * 64),
        ("primary", "poetry", "library-3", "f" * 64),
    )
    with pytest.raises(Exception, match="persistence failed"):
        repository.drain_library_additions(scope, 101)
    assert repository._connection.in_transaction is False
    assert repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_scans"
    ).fetchone() == (0,)
    assert repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_presentation "
        "WHERE row_kind='deferred_enrollment'"
    ).fetchone() == (2,)
    repository._connection.execute("DROP TRIGGER fail_settings_drain")
    repository.drain_library_additions(scope, 102)
    repository.drain_library_additions(scope, 103)
    promoted = repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_scans"
    ).fetchone()
    requested = repository._connection.execute(
        "SELECT DISTINCT requested_at FROM review_preparation_scans"
    ).fetchall()
    deferred = repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_presentation "
        "WHERE row_kind='deferred_enrollment'"
    ).fetchone()
    accepted = repository._connection.execute(
        "SELECT accepted_count FROM review_preparation_presentation "
        "WHERE row_id='trigger:library_added'"
    ).fetchone()
    repository.close()

    assert committed.document.revision == 2
    assert rows == [("history", 0, 100, 2), ("poetry", 1, 100, 2)]
    assert trigger == (2, 100, 100)
    assert promoted == (2,)
    assert requested == [(102,)]
    assert deferred == (0,)
    assert accepted == (2,)


@pytest.mark.parametrize("mixed", (False, True))
def test_settings_removal_cancels_undrained_intent_without_rewriting_survivor(
    tmp_path: Path, mixed: bool
) -> None:
    path = tmp_path / f"settings-removal-{mixed}.sqlite3"
    repository, store = _seed_full_store(path)
    added = ("ebooks", "history", "poetry") if mixed else ("ebooks", "history")
    store.write(_settings_with_libraries(2, added), 1, (), accepted_at=100)
    retained = ("ebooks", "poetry") if mixed else ("ebooks",)
    store.write(_settings_with_libraries(3, retained), 2, (), accepted_at=110)
    rows = repository._connection.execute(
        "SELECT library_name,ordinal,accepted_at,settings_revision "
        "FROM review_preparation_presentation WHERE row_kind='deferred_enrollment'"
    ).fetchall()
    trigger = repository._connection.execute(
        "SELECT accepted_count FROM review_preparation_presentation "
        "WHERE row_id='trigger:library_added'"
    ).fetchone()
    repository.close()

    reopened = open_repository(path, create=False)
    assert rows == ([("poetry", 0, 100, 2)] if mixed else [])
    assert trigger == ((2,) if mixed else None)
    presentation = reopened.preparation_presentation(111)
    assert (presentation is not None) is mixed
    reopened.close()


def test_paused_settings_addition_stays_visible_until_atomic_resume(
    tmp_path: Path,
) -> None:
    repository, store = _seed_full_store(tmp_path / "paused-settings-addition.sqlite3")
    repository.set_review_pipeline_paused(True, 90)
    store.write(
        _settings_with_libraries(2, ("ebooks", "history")),
        1,
        (),
        accepted_at=100,
    )
    scope = (
        ("primary", "ebooks", "library-1", "f" * 64),
        ("primary", "history", "library-2", "f" * 64),
    )
    blocked = repository.preparation_presentation(100)
    repository.drain_library_additions(scope, 101)
    assert repository._connection.execute(
        "SELECT COUNT(*) FROM review_preparation_scans"
    ).fetchone() == (0,)
    repository.resume_review_pipeline(scope, 102)
    promoted = repository.preparation_presentation(102)
    rows = repository._connection.execute(
        "SELECT row_kind,library_name FROM review_preparation_presentation "
        "WHERE row_kind IN ('enrollment','deferred_enrollment')"
    ).fetchall()
    accepted = repository._connection.execute(
        "SELECT accepted_count FROM review_preparation_presentation "
        "WHERE row_id='trigger:library_added'"
    ).fetchone()
    repository.close()

    assert blocked is not None and blocked.state.value == "blocked"
    assert not blocked.dismissible
    assert promoted is not None and promoted.state.value == "starting"
    assert rows == [("enrollment", "history")]
    assert accepted == (1,)


@pytest.mark.parametrize("dismissed", (False, True))
def test_settings_remove_and_readd_uses_intent_despite_terminal_scan_history(
    tmp_path: Path, dismissed: bool
) -> None:
    repository, store = _seed_full_store(
        tmp_path / f"settings-readd-{dismissed}.sqlite3", ("ebooks", "history")
    )
    scan = repository.request_review_scan(
        "primary",
        "ebooks",
        "library-1",
        "f" * 64,
        80,
        trigger=PreparationTrigger.MANUAL_SCAN,
    )
    repository._connection.execute(
        "UPDATE review_preparation_scans SET state='completed',updated_at=81 WHERE scan_id=?",
        (scan.scan_id,),
    )
    refresh_preparation_presentation(repository._connection, 81)
    if dismissed:
        terminal = repository.preparation_presentation(81)
        assert terminal is not None
        repository.dismiss_preparation_presentation(terminal.run_id, 82)
    store.write(_settings_with_libraries(2, ("history",)), 1, (), accepted_at=90)
    store.write(
        _settings_with_libraries(3, ("ebooks", "history")),
        2,
        (),
        accepted_at=100,
    )
    intent = repository.preparation_presentation(100)
    repository.drain_library_additions(
        (
            ("primary", "ebooks", "library-1", "f" * 64),
            ("primary", "history", "library-2", "f" * 64),
        ),
        101,
    )
    progress = repository.review_pipeline_progress("primary", "ebooks")
    presentation = repository.preparation_presentation(101)
    accepted = repository._connection.execute(
        "SELECT accepted_count FROM review_preparation_presentation "
        "WHERE row_id='trigger:library_added'"
    ).fetchone()
    repository.close()

    assert intent is not None and intent.state.value == "blocked"
    assert progress is not None and progress.state == "queued"
    assert presentation is not None and presentation.run_id == intent.run_id
    assert accepted == (1,)


@pytest.mark.parametrize(
    "mutation",
    (
        "UPDATE review_preparation_presentation SET settings_revision=3 "
        "WHERE row_kind='deferred_enrollment'",
        "UPDATE review_preparation_presentation SET library_name='private-target' "
        "WHERE row_kind='deferred_enrollment'",
    ),
)
def test_deferred_settings_authority_corruption_is_value_free(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / "deferred-settings-corruption.sqlite3"
    repository, store = _seed_full_store(path)
    store.write(
        _settings_with_libraries(2, ("ebooks", "history")),
        1,
        (),
        accepted_at=100,
    )
    repository.close()
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA ignore_check_constraints=ON")
    connection.execute(mutation)
    connection.commit()
    connection.close()
    before = path.read_bytes()

    with pytest.raises(
        MetadataReportError, match="^database is unavailable or incompatible$"
    ) as raised:
        open_repository(path, create=False)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert path.read_bytes() == before


def test_store_rejects_client_authored_restart_state() -> None:
    store = _store()
    forged = _document(2).model_copy(update={"pending_restart": ["libraries"]})

    with pytest.raises(OwnerSettingsError, match="restart state"):
        store.write(forged, 1, ())

    assert store.read().document.revision == 1


@pytest.mark.parametrize("field", ("mount", "policy_override"))
def test_store_rejects_non_settings_owned_library_mutation(field: str) -> None:
    store = _store()
    value = _document(2).model_dump()
    value["libraries"][0][field] = (
        "other"
        if field == "mount"
        else {"options": {"date_precision": "full_date"}, "destinations": {}}
    )

    with pytest.raises(OwnerSettingsError, match="settings document is invalid"):
        store.write(OwnerSettingsDocument.model_validate(value), 1, ())

    assert store.read().document.libraries[0].mount == "books"
    assert store.read().document.libraries[0].policy_override is None


def test_store_rejects_display_name_mutation_outside_setup() -> None:
    store = _store()
    value = _document(2).model_dump()
    value["libraries"][0]["display_name"] = "Changed in a raw PATCH"

    with pytest.raises(OwnerSettingsError, match="settings document is invalid"):
        store.write(OwnerSettingsDocument.model_validate(value), 1, ())

    assert store.read().document.libraries[0].display_name is None


def test_store_managed_fields_are_live_without_restart_debt() -> None:
    store = _store()
    changed = _document(2).model_copy(update={"provider_recovery_generation": 2})
    result = store.write(changed, 1, ())
    assert result.document.pending_restart == []
    assert (
        next(
            item
            for item in result.fields
            if item.field_id == "provider_recovery_generation"
        ).apply_effect
        == "applied_live"
    )
    live = result.document.model_copy(
        update={"revision": 3, "reduced_motion": "always_reduced"}
    )
    result = store.write(live, 2, ())
    assert result.document.pending_restart == []


def test_runtime_activation_requires_complete_authority_and_ack_clears_applied_debt() -> (
    None
):
    store = _store()
    connection = store._connection
    connection.execute(
        "INSERT INTO owner_setting_secrets VALUES(?,?)",
        ("owner.password.verifier", "$argon2id$synthetic"),
    )
    connection.commit()
    staged = _document(2).model_copy(
        update={"provider_recovery_generation": 2, "pending_restart": ["providers"]}
    )
    connection.execute(
        "UPDATE owner_settings SET revision=?,document=? WHERE singleton=1",
        (2, _canonical_json(staged.model_dump())),
    )
    connection.commit()

    view, credentials, verifier = store.activate_runtime()

    assert view.document.pending_restart == ["providers"]
    assert credentials == {"connection.primary.key": "target-secret-1234"}
    assert verifier == "$argon2id$synthetic"
    store.acknowledge_runtime(view.document.revision)
    acknowledged = store.read()
    assert acknowledged.document.pending_restart == []
    assert acknowledged.document.revision == 3
    with pytest.raises(OwnerSettingsError, match="stale"):
        store.write(_document(3), 2, ())


def test_runtime_acknowledgement_noop_and_fault_preserve_revision_parity() -> None:
    store = _store()
    store.acknowledge_runtime(1)
    assert store.read().document.revision == 1
    staged = _document(2).model_copy(update={"pending_restart": ["providers"]})
    store._connection.execute(
        "UPDATE owner_settings SET revision=?,document=? WHERE singleton=1",
        (2, _canonical_json(staged.model_dump())),
    )
    store._connection.execute(
        "CREATE TRIGGER reject_owner_settings_ack BEFORE UPDATE ON owner_settings "
        "BEGIN SELECT RAISE(ABORT,'synthetic'); END"
    )
    store._connection.commit()

    with pytest.raises(OwnerSettingsError, match="unavailable"):
        store.acknowledge_runtime(2)

    stored_revision, encoded = store._connection.execute(
        "SELECT revision,document FROM owner_settings"
    ).fetchone()
    assert stored_revision == 2
    assert OwnerSettingsDocument.model_validate_json(encoded).revision == 2
    assert OwnerSettingsDocument.model_validate_json(encoded).pending_restart == [
        "providers"
    ]


def test_write_returns_prevalidated_commit_without_post_commit_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store()
    monkeypatch.setattr(
        store,
        "read",
        lambda: (_ for _ in ()).throw(OwnerSettingsError("synthetic read failure")),
    )

    committed = store.write(_document(2), 1, ())

    assert committed.document.revision == 2
    stored_revision, encoded = store._connection.execute(
        "SELECT revision,document FROM owner_settings"
    ).fetchone()
    assert stored_revision == 2
    assert OwnerSettingsDocument.model_validate_json(encoded).revision == 2


def test_runtime_activation_refuses_missing_owner_verifier() -> None:
    with pytest.raises(OwnerSettingsError, match="unavailable"):
        _store().activate_runtime()


def test_disabled_provider_credentials_are_retained_until_explicit_clear() -> None:
    store = _store()
    first = store.write(
        _document(2),
        1,
        (
            SecretWrite(
                field_id="provider.books.key",
                operation="set",
                value="google-secret-1234",
            ),
        ),
    )
    disabled = first.document.model_dump()
    disabled["revision"] = 3
    disabled["providers"][0]["enabled"] = False
    disabled["providers"][0]["cover_enabled"] = False
    disabled["libraries"][0]["provider_connections"] = ["open"]
    result = store.write(OwnerSettingsDocument.model_validate(disabled), 2, ())
    assert [item.field_id for item in result.secrets if item.configured] == [
        "connection.primary.key",
        "provider.books.key",
    ]
    store._connection.execute(
        "INSERT INTO owner_setting_secrets VALUES(?,?)",
        ("owner.password.verifier", "$argon2id$synthetic"),
    )
    store._connection.commit()
    _, runtime_credentials, _ = store.activate_runtime()
    assert runtime_credentials == {"connection.primary.key": "target-secret-1234"}

    disabled_replacement = result.document.model_dump()
    disabled_replacement["revision"] = 4
    replaced = store.write(
        OwnerSettingsDocument.model_validate(disabled_replacement),
        3,
        (
            SecretWrite(
                field_id="provider.books.key",
                operation="replace",
                value="replacement-secret-5678",
            ),
        ),
    )
    assert (
        next(
            item.indicator
            for item in replaced.secrets
            if item.field_id == "provider.books.key"
        )
        == "Configured · ····5678"
    )

    reenabled = replaced.document.model_dump()
    reenabled["revision"] = 5
    reenabled["providers"][0]["enabled"] = True
    reenabled["libraries"][0]["provider_connections"] = ["open"]
    restored = store.write(OwnerSettingsDocument.model_validate(reenabled), 4, ())
    assert restored.document.libraries[0].provider_connections == ["open"]

    disabled_again = restored.document.model_dump()
    disabled_again["revision"] = 6
    disabled_again["providers"][0]["enabled"] = False
    cleared = store.write(
        OwnerSettingsDocument.model_validate(disabled_again),
        5,
        (SecretWrite(field_id="provider.books.key", operation="clear"),),
    )
    assert [item.field_id for item in cleared.secrets if item.configured] == [
        "connection.primary.key"
    ]


def test_enabled_hardcover_requires_a_key() -> None:
    store = _store()
    result = store.read()

    hardcover = result.document.model_dump()
    hardcover["revision"] = 2
    hardcover["providers"] = [
        {
            "name": "hardcover",
            "adapter": "hardcover",
            "enabled": True,
            "cover_enabled": False,
        }
    ]
    hardcover["libraries"][0]["provider_connections"] = ["hardcover"]
    connection = store._connection
    connection.execute(
        "INSERT INTO owner_setting_secrets VALUES(?,?)",
        ("owner.password.verifier", "$argon2id$synthetic"),
    )
    connection.commit()
    with pytest.raises(OwnerSettingsError, match="required secret"):
        store.write(OwnerSettingsDocument.model_validate(hardcover), 1, ())

    stored = store.write(
        OwnerSettingsDocument.model_validate(hardcover),
        1,
        (
            SecretWrite(
                field_id="provider.hardcover.key",
                operation="set",
                value="hardcover-secret-1234",
            ),
        ),
    )
    _, credentials, _ = store.activate_runtime()
    assert credentials == {
        "connection.primary.key": "target-secret-1234",
        "provider.hardcover.key": "hardcover-secret-1234",
    }
    assert stored.document.revision == 2


def test_store_rejects_new_association_to_a_disabled_provider() -> None:
    value = _document().model_dump()
    value["revision"] = 2
    value["providers"][0]["enabled"] = False
    value["providers"][0]["cover_enabled"] = False

    with pytest.raises(OwnerSettingsError, match="references"):
        _store().write(OwnerSettingsDocument.model_validate(value), 1, ())


def test_legacy_disabled_association_loads_suppressed_and_canonicalises_on_save() -> (
    None
):
    store = _store()
    legacy = _document().model_dump()
    legacy["providers"][0]["enabled"] = False
    legacy["providers"][0]["cover_enabled"] = False
    legacy_document = OwnerSettingsDocument.model_validate(legacy)
    encoded = _canonical_json(legacy_document.model_dump())
    store._connection.execute(
        "UPDATE owner_settings SET document=? WHERE singleton=1", (encoded,)
    )
    store._connection.execute(
        "INSERT INTO owner_setting_secrets VALUES(?,?)",
        ("provider.books.key", "google-secret-1234"),
    )
    store._connection.execute(
        "INSERT INTO owner_setting_secrets VALUES(?,?)",
        ("owner.password.verifier", "$argon2id$synthetic"),
    )
    store._connection.commit()

    view = store.read()

    assert view.document.libraries[0].provider_connections == ["open"]
    assert store._connection.execute(
        "SELECT document FROM owner_settings WHERE singleton=1"
    ).fetchone() == (encoded,)
    _, runtime_credentials, _ = store.activate_runtime()
    assert runtime_credentials == {"connection.primary.key": "target-secret-1234"}

    candidate = view.document.model_copy(update={"revision": 2})
    snapshot, candidate_credentials, _ = store.activation_snapshot(candidate, 1, ())
    assert snapshot.document.libraries[0].provider_connections == ["open"]
    assert candidate_credentials == {"connection.primary.key": "target-secret-1234"}
    saved = store.write(candidate, 1, ())
    stored = OwnerSettingsDocument.model_validate_json(
        store._connection.execute(
            "SELECT document FROM owner_settings WHERE singleton=1"
        ).fetchone()[0]
    )
    assert stored.libraries[0].provider_connections == ["open"]

    reenabled_value = saved.document.model_dump()
    reenabled_value["revision"] = 3
    reenabled_value["providers"][0]["enabled"] = True
    reenabled = store.write(
        OwnerSettingsDocument.model_validate(reenabled_value), 2, ()
    )
    assert reenabled.document.libraries[0].provider_connections == ["open"]
    _, reenabled_credentials, _ = store.activate_runtime()
    assert reenabled_credentials == {
        "connection.primary.key": "target-secret-1234",
        "provider.books.key": "google-secret-1234",
    }


def test_store_rejects_unknown_or_duplicate_secret_writes_without_mutation() -> None:
    store = _store()
    update = _document(2)
    with pytest.raises(OwnerSettingsError, match="operation"):
        store.write(
            update,
            1,
            (
                SecretWrite(
                    field_id="provider.unknown.key",
                    operation="set",
                    value="secret-1234",
                ),
            ),
        )
    with pytest.raises(OwnerSettingsError, match="operation"):
        store.write(
            update,
            1,
            (
                SecretWrite(
                    field_id="provider.books.key", operation="set", value="secret-1234"
                ),
                SecretWrite(field_id="provider.books.key", operation="clear"),
            ),
        )
    assert store.read().document.revision == 1


def test_file_authority_is_fully_locked_without_store_state() -> None:
    repository = FileOwnerSettingsRepository(_document())
    assert repository.read().authority == "file"
    assert all(item.managed_by_file for item in repository.read().fields)
    with pytest.raises(OwnerSettingsError, match="locked"):
        repository.write(_document(2), 1, ())
