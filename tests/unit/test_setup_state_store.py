from __future__ import annotations

import sqlite3

import pytest

from mechanical_librarian.adapters.persistence.owner_settings import (
    _validate_owner_settings_content,
)
from mechanical_librarian.adapters.persistence.setup_state import SetupStateStore
from mechanical_librarian.domain.owner_settings import OwnerSettingsError
from mechanical_librarian.domain.setup_state import SetupStateDocument, SetupStateError

_BOOTSTRAP = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$hash"
_OWNER = "$argon2id$v=19$m=65536,t=3,p=4$b3duZXI$hash"


def _store() -> tuple[sqlite3.Connection, SetupStateStore]:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute(
        "CREATE TABLE owner_settings (singleton INTEGER PRIMARY KEY,revision INTEGER,document TEXT)"
    )
    connection.execute(
        "CREATE TABLE owner_setting_secrets (field_id TEXT PRIMARY KEY,value TEXT)"
    )
    return connection, SetupStateStore(connection)


def _next(state: SetupStateDocument, **changes: object) -> SetupStateDocument:
    return SetupStateDocument.model_validate(
        {**state.model_dump(), "revision": state.revision + 1, **changes}
    )


def _connected(store: SetupStateStore) -> SetupStateDocument:
    state = store.initialize(_BOOTSTRAP)
    state = store.write(
        _next(state, completed_steps=["claim"], claimed=True),
        state.revision,
        _BOOTSTRAP,
    )
    return store.write(
        _next(
            state,
            completed_steps=["claim", "connect"],
            connection={"name": "primary", "endpoint": "https://abs.example.invalid"},
        ),
        state.revision,
        _BOOTSTRAP,
        {"connection.primary.key": "abs-secret"},
    )


def test_initialize_binds_the_only_resumable_setup_state_to_bootstrap() -> None:
    _connection, store = _store()

    state = store.initialize(_BOOTSTRAP)

    assert state.next_step() == "claim"
    assert store.read(_BOOTSTRAP) == state
    with pytest.raises(SetupStateError):
        store.read(_OWNER)


def test_promotion_replaces_incomplete_state_with_normal_owner_authority() -> None:
    connection, store = _store()
    state = store.initialize(_BOOTSTRAP)
    state = _next(state, completed_steps=["claim"], claimed=True)
    state = store.write(state, 1, _BOOTSTRAP)
    state = _next(
        state,
        completed_steps=["claim", "connect"],
        connection={"name": "primary", "endpoint": "https://abs.example.invalid"},
    )
    state = store.write(state, 2, _BOOTSTRAP, {"connection.primary.key": "secret"})
    state = _next(state, completed_steps=["claim", "connect", "providers"])
    state = store.write(state, 3, _BOOTSTRAP, {"connection.primary.key": "secret"})
    state = _next(
        state,
        completed_steps=["claim", "connect", "providers", "libraries"],
        libraries=[
            {
                "name": "books",
                "connection": "primary",
                "library_id": "books",
                "media_kind": "ebook",
            }
        ],
    )
    state = store.write(state, 4, _BOOTSTRAP, {"connection.primary.key": "secret"})
    state = _next(
        state,
        completed_steps=["claim", "connect", "providers", "libraries", "mounts"],
        mounts=[{"library": "books", "state": "matched"}],
    )
    store.write(state, 5, _BOOTSTRAP, {"connection.primary.key": "secret"})

    store.promote(_BOOTSTRAP, _OWNER)

    encoded = connection.execute("SELECT document FROM owner_settings").fetchone()[0]
    assert '"schema_version":"owner-settings-v1"' in encoded
    assert connection.execute(
        "SELECT field_id,value FROM owner_setting_secrets ORDER BY field_id"
    ).fetchall() == [
        ("connection.primary.key", "secret"),
        ("owner.password.verifier", _OWNER),
    ]
    with pytest.raises(SetupStateError):
        store.read(_BOOTSTRAP)


def test_promotion_failpoint_retains_exact_resumable_state_and_retry_succeeds() -> None:
    connection, store = _store()
    state = _connected(store)
    state = store.write(
        _next(state, completed_steps=["claim", "connect", "providers"]),
        state.revision,
        _BOOTSTRAP,
    )
    state = store.write(
        _next(
            state,
            completed_steps=["claim", "connect", "providers", "libraries"],
            libraries=[
                {
                    "name": "books",
                    "connection": "primary",
                    "library_id": "books",
                    "media_kind": "ebook",
                    "mount": "books",
                }
            ],
        ),
        state.revision,
        _BOOTSTRAP,
    )
    state = store.write(
        _next(
            state,
            completed_steps=[
                "claim",
                "connect",
                "providers",
                "libraries",
                "mounts",
            ],
            mounts=[{"library": "books", "state": "matched"}],
        ),
        state.revision,
        _BOOTSTRAP,
    )
    connection.execute(
        "CREATE TRIGGER fail_owner_verifier BEFORE INSERT ON owner_setting_secrets "
        "WHEN NEW.field_id='owner.password.verifier' BEGIN SELECT RAISE(ABORT,'failpoint'); END"
    )

    with pytest.raises(SetupStateError):
        store.promote(_BOOTSTRAP, _OWNER, state.revision)

    assert store.read(_BOOTSTRAP) == state
    assert connection.execute(
        "SELECT field_id,value FROM owner_setting_secrets"
    ).fetchall() == [("connection.primary.key", "abs-secret")]
    connection.execute("DROP TRIGGER fail_owner_verifier")

    store.promote(_BOOTSTRAP, _OWNER, state.revision)

    assert connection.execute(
        "SELECT field_id FROM owner_setting_secrets ORDER BY field_id"
    ).fetchall() == [
        ("connection.primary.key",),
        ("owner.password.verifier",),
    ]


def test_schema_admission_accepts_only_the_closed_incomplete_setup_document() -> None:
    connection, store = _store()
    store.initialize(_BOOTSTRAP)

    _validate_owner_settings_content(connection)

    connection.execute(
        "INSERT INTO owner_setting_secrets(field_id,value) VALUES('setup.owner.verifier','x')"
    )
    with pytest.raises(OwnerSettingsError):
        _validate_owner_settings_content(connection)


def test_changing_a_recorded_step_removes_its_dependents() -> None:
    state = SetupStateDocument.model_validate(
        {
            "schema_version": "setup-state-v1",
            "revision": 1,
            "bootstrap_verifier_digest": "0" * 64,
        }
    )
    changed = state.invalidate_after("claim")
    assert changed.completed_steps == []
    assert changed.connection is None


def test_provider_key_can_be_added_without_resending_connection_key() -> None:
    connection, store = _store()
    state = _connected(store)

    state = store.write(
        _next(
            state,
            completed_steps=["claim", "connect", "providers"],
            providers=[
                {
                    "name": "hardcover",
                    "adapter": "hardcover",
                    "enabled": True,
                    "cover_enabled": False,
                }
            ],
        ),
        state.revision,
        _BOOTSTRAP,
        {"provider.hardcover.key": "provider-secret"},
    )

    assert store.read(_BOOTSTRAP) == state
    assert connection.execute(
        "SELECT field_id,value FROM owner_setting_secrets ORDER BY field_id"
    ).fetchall() == [
        ("connection.primary.key", "abs-secret"),
        ("provider.hardcover.key", "provider-secret"),
    ]


def test_provider_skip_preserves_connection_key_without_a_secret_delta() -> None:
    connection, store = _store()
    state = _connected(store)

    state = store.write(
        _next(state, completed_steps=["claim", "connect", "providers"]),
        state.revision,
        _BOOTSTRAP,
    )

    assert store.read(_BOOTSTRAP) == state
    assert connection.execute(
        "SELECT field_id,value FROM owner_setting_secrets"
    ).fetchall() == [("connection.primary.key", "abs-secret")]


def test_change_invalidation_removes_only_secrets_owned_by_dependents() -> None:
    connection, store = _store()
    state = _connected(store)
    state = store.write(
        _next(
            state,
            completed_steps=["claim", "connect", "providers"],
            providers=[
                {
                    "name": "hardcover",
                    "adapter": "hardcover",
                    "enabled": True,
                    "cover_enabled": False,
                }
            ],
        ),
        state.revision,
        _BOOTSTRAP,
        {"provider.hardcover.key": "provider-secret"},
    )

    changed = state.invalidate_after("providers").model_copy(
        update={"revision": state.revision + 1}
    )
    store.write(changed, state.revision, _BOOTSTRAP)

    assert connection.execute(
        "SELECT field_id,value FROM owner_setting_secrets"
    ).fetchall() == [("connection.primary.key", "abs-secret")]


@pytest.mark.parametrize(
    "delta",
    [
        {"unknown.key": "secret"},
        {"connection.primary.key": ""},
        {"connection.primary.key": None},
    ],
    ids=["unknown", "empty", "missing-required"],
)
def test_invalid_secret_delta_changes_neither_document_nor_secrets(
    delta: dict[str, str | None],
) -> None:
    connection, store = _store()
    state = _connected(store)
    before_document = connection.execute(
        "SELECT revision,document FROM owner_settings"
    ).fetchall()
    before_secrets = connection.execute(
        "SELECT field_id,value FROM owner_setting_secrets"
    ).fetchall()

    with pytest.raises(SetupStateError):
        store.write(
            _next(state, completed_steps=["claim", "connect", "providers"]),
            state.revision,
            _BOOTSTRAP,
            delta,
        )

    assert (
        connection.execute("SELECT revision,document FROM owner_settings").fetchall()
        == before_document
    )
    assert (
        connection.execute(
            "SELECT field_id,value FROM owner_setting_secrets"
        ).fetchall()
        == before_secrets
    )


def test_stale_write_changes_neither_document_nor_secrets() -> None:
    connection, store = _store()
    state = _connected(store)
    successor = store.write(
        _next(state, completed_steps=["claim", "connect", "providers"]),
        state.revision,
        _BOOTSTRAP,
    )
    before_document = connection.execute(
        "SELECT revision,document FROM owner_settings"
    ).fetchall()
    before_secrets = connection.execute(
        "SELECT field_id,value FROM owner_setting_secrets"
    ).fetchall()

    with pytest.raises(SetupStateError):
        store.write(
            successor.model_copy(update={"revision": successor.revision + 1}),
            state.revision,
            _BOOTSTRAP,
            {"connection.primary.key": "replacement"},
        )

    assert (
        connection.execute("SELECT revision,document FROM owner_settings").fetchall()
        == before_document
    )
    assert (
        connection.execute(
            "SELECT field_id,value FROM owner_setting_secrets"
        ).fetchall()
        == before_secrets
    )
