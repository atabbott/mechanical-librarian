from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path

import pytest

from mechanical_librarian.adapters.persistence.metadata_reports import open_repository
from mechanical_librarian.adapters.persistence.owner_settings import (
    _validate_owner_settings_content,
)
from mechanical_librarian.adapters.persistence.review_sessions import (
    SQLiteReviewSessionRepository,
)
from mechanical_librarian.adapters.persistence.schema import _create_schema
from mechanical_librarian.adapters.persistence.setup_state import SetupStateStore
from mechanical_librarian.application.review_web import OwnerSessionService
from mechanical_librarian.domain.metadata_policy import MetadataPolicyProfile
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


def _real_store() -> tuple[sqlite3.Connection, SetupStateStore]:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    _create_schema(connection)
    return connection, SetupStateStore(connection)


def _next(state: SetupStateDocument, **changes: object) -> SetupStateDocument:
    return SetupStateDocument.model_validate(
        {**state.model_dump(), "revision": state.revision + 1, **changes}
    )


def _claimed(store: SetupStateStore, state: SetupStateDocument) -> SetupStateDocument:
    return store.claim_owner(
        expected_revision=state.revision,
        owner_verifier=_OWNER,
    )


def _connected(store: SetupStateStore) -> SetupStateDocument:
    state = store.initialize(_BOOTSTRAP)
    state = _claimed(store, state)
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


def test_initialize_creates_the_only_verifier_free_resumable_setup_state() -> None:
    _connection, store = _store()

    state = store.initialize(_BOOTSTRAP)

    assert state.next_step() == "claim"
    assert state.bootstrap_verifier_digest == "0" * 64
    assert store.read() == state
    assert store.read(_OWNER) == state


def test_only_the_exact_seeded_schema_is_an_initialization_predecessor() -> None:
    connection, store = _real_store()
    assert store.is_exact_schema_only()

    connection.execute(
        "UPDATE review_preparation_control SET claim_sequence=1 WHERE singleton=1"
    )

    assert not store.is_exact_schema_only()


def test_setup_write_rollback_cannot_discard_a_returned_owner_session(
    tmp_path: Path,
) -> None:
    database = tmp_path / "application.sqlite3"
    repository = open_repository(database, cross_thread=True)
    state = repository.setup_state_store()
    state.initialize(_BOOTSTRAP)
    entered = threading.Event()
    release = threading.Event()

    def fail_setup_write() -> None:
        entered.set()
        assert release.wait(5)
        raise sqlite3.OperationalError("synthetic failpoint")

    repository._connection.create_function("fail_setup_write", 0, fail_setup_write)
    repository._connection.execute(
        "CREATE TRIGGER fail_setup_write BEFORE UPDATE ON owner_settings "
        "BEGIN SELECT fail_setup_write(); END"
    )
    session_store = SQLiteReviewSessionRepository.open(
        tmp_path / "review-sessions.sqlite"
    )
    tokens = iter(("s" * 32, "c" * 32))
    sessions = OwnerSessionService(
        session_store,
        lambda _password: True,
        lambda token: hashlib.sha256(token.encode()).hexdigest(),
        lambda: 1_000,
        3_600,
        lambda _length: next(tokens),
    )
    document = state.read(_BOOTSTRAP)
    write_failure: list[Exception] = []
    login_tokens: list[object] = []

    def write_state() -> None:
        try:
            state.claim_owner(
                expected_revision=document.revision,
                owner_verifier=_OWNER,
            )
        except Exception as error:
            write_failure.append(error)

    def login() -> None:
        login_tokens.append(sessions.login("owner password"))

    writer = threading.Thread(target=write_state)
    signer = threading.Thread(target=login)
    writer.start()
    assert entered.wait(5)
    # The session store is a separate database (#912): the login completes
    # while the setup claim transaction is still open, instead of queueing
    # behind it on a shared connection.
    signer.start()
    signer.join(5)
    assert not signer.is_alive()
    assert len(login_tokens) == 1
    release.set()
    writer.join(5)

    try:
        assert len(write_failure) == 1
        assert isinstance(write_failure[0], SetupStateError)
        # The claim's rollback ran on the application database only; the
        # returned session is untouched.
        assert sessions.authenticate("s" * 32, "c" * 32) == "owner"
    finally:
        session_store.close()
        repository.close()


def test_promotion_replaces_incomplete_state_with_normal_owner_authority() -> None:
    connection, store = _store()
    state = store.initialize(_BOOTSTRAP)
    state = _claimed(store, state)
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
        "CREATE TRIGGER fail_owner_promotion BEFORE UPDATE ON owner_settings "
        "WHEN json_extract(NEW.document,'$.schema_version')='owner-settings-v1' "
        "BEGIN SELECT RAISE(ABORT,'failpoint'); END"
    )

    with pytest.raises(SetupStateError):
        store.promote(_BOOTSTRAP, _OWNER, state.revision)

    assert store.read(_BOOTSTRAP) == state
    assert connection.execute(
        "SELECT field_id,value FROM owner_setting_secrets ORDER BY field_id"
    ).fetchall() == [
        ("connection.primary.key", "abs-secret"),
        ("owner.password.verifier", _OWNER),
    ]
    connection.execute("DROP TRIGGER fail_owner_promotion")

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
        ("owner.password.verifier", _OWNER),
        ("provider.hardcover.key", "provider-secret"),
    ]


def test_google_books_is_allowed_without_a_key_but_hardcover_is_not() -> None:
    _connection, store = _store()
    state = _connected(store)
    google = _next(
        state,
        completed_steps=["claim", "connect", "providers"],
        providers=[
            {
                "name": "google",
                "adapter": "google_books",
                "enabled": True,
                "cover_enabled": False,
            }
        ],
    )

    assert store.write(google, state.revision, _BOOTSTRAP) == google

    hardcover = _next(
        google,
        providers=[
            {
                "name": "hardcover",
                "adapter": "hardcover",
                "enabled": True,
                "cover_enabled": False,
            }
        ],
    )
    with pytest.raises(SetupStateError):
        store.write(hardcover, google.revision, _BOOTSTRAP)


def test_resume_rejects_invalid_secret_value_and_runtime_rows() -> None:
    connection, store = _store()
    _connected(store)
    connection.execute(
        "UPDATE owner_setting_secrets SET value='Bearer invalid' "
        "WHERE field_id='connection.primary.key'"
    )
    with pytest.raises(SetupStateError):
        store.read(_BOOTSTRAP)

    connection.execute(
        "UPDATE owner_setting_secrets SET value='abs-secret' "
        "WHERE field_id='connection.primary.key'"
    )
    connection.execute("CREATE TABLE metadata_reports(report_id TEXT)")
    connection.execute("INSERT INTO metadata_reports VALUES('report')")
    with pytest.raises(SetupStateError):
        store.read(_BOOTSTRAP)


def test_resume_rejects_a_second_valid_seeded_policy_draft() -> None:
    connection, store = _real_store()
    _connected(store)
    builtin = MetadataPolicyProfile.builtin()
    second = MetadataPolicyProfile.create(
        "Second valid policy", dict(builtin.options), dict(builtin.destinations)
    )
    connection.execute(
        "INSERT INTO metadata_policy_drafts(policy_id,document) VALUES(?,?)",
        (second.policy_id, second.canonical_json),
    )

    with pytest.raises(SetupStateError):
        store.read(_BOOTSTRAP)


def test_resume_rejects_changed_seeded_singleton_value() -> None:
    connection, store = _real_store()
    _connected(store)
    connection.execute("UPDATE review_preparation_control SET paused=1")

    with pytest.raises(SetupStateError):
        store.read(_BOOTSTRAP)


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
        "SELECT field_id,value FROM owner_setting_secrets ORDER BY field_id"
    ).fetchall() == [
        ("connection.primary.key", "abs-secret"),
        ("owner.password.verifier", _OWNER),
    ]


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
        "SELECT field_id,value FROM owner_setting_secrets ORDER BY field_id"
    ).fetchall() == [
        ("connection.primary.key", "abs-secret"),
        ("owner.password.verifier", _OWNER),
    ]


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
