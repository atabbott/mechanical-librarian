import sqlite3
from typing import cast

import pytest
from pydantic import ValidationError

from mechanical_librarian.adapters.persistence.documents import _canonical_json
from mechanical_librarian.adapters.persistence.owner_settings import OwnerSettingsStore
from mechanical_librarian.application.cover_consideration import (
    CoverConsiderationService,
)
from mechanical_librarian.application.cover_policy import CoverPolicy
from mechanical_librarian.application.owner_settings import FileOwnerSettingsRepository
from mechanical_librarian.domain.owner_settings import (
    OwnerSettingsDocument,
    OwnerSettingsError,
    SecretWrite,
    file_field_metadata,
)


def _document(revision: int = 1) -> OwnerSettingsDocument:
    return OwnerSettingsDocument.model_validate(
        {
            "schema_version": "owner-settings-v1",
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


def test_document_rejects_noncanonical_references_and_bounds() -> None:
    value = _document().model_dump()
    value["libraries"][0]["provider_connections"] = ["open", "books"]
    with pytest.raises(ValidationError, match="duplicated"):
        OwnerSettingsDocument.model_validate(value)
    value = _document().model_dump()
    value["connections"][0]["endpoint"] = "x" * 2049
    with pytest.raises(ValidationError):
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
            "enabled": False,
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
        == "restart_required"
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
        cast("object", object()),
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


def test_restart_effects_are_durable_while_reduced_motion_is_live() -> None:
    store = _store()
    changed = _document(2).model_copy(update={"provider_recovery_generation": 2})
    result = store.write(changed, 1, ())
    assert result.pending_restart == ("provider_recovery_generation",)
    assert next(
        item
        for item in result.fields
        if item.field_id == "provider_recovery_generation"
    ).pending_restart
    live = result.document.model_copy(
        update={"revision": 3, "reduced_motion": "always_reduced"}
    )
    result = store.write(live, 2, ())
    assert result.pending_restart == ("provider_recovery_generation",)


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

    assert view.pending_restart == ("providers",)
    assert credentials == {"connection.primary.key": "target-secret-1234"}
    assert verifier == "$argon2id$synthetic"
    store.acknowledge_runtime(view.document.revision)
    acknowledged = store.read()
    assert acknowledged.pending_restart == ()
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


def test_disabled_provider_credentials_are_removed_and_enabled_hardcover_is_required() -> (
    None
):
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
    result = store.write(OwnerSettingsDocument.model_validate(disabled), 2, ())
    assert [item.field_id for item in result.secrets if item.configured] == [
        "connection.primary.key"
    ]

    hardcover = result.document.model_dump()
    hardcover["revision"] = 4
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
        store.write(OwnerSettingsDocument.model_validate(hardcover), 3, ())

    stored = store.write(
        OwnerSettingsDocument.model_validate(hardcover),
        3,
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
    assert stored.document.revision == 4


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
