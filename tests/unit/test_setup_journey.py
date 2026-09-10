from __future__ import annotations

import sqlite3

import pytest
from pydantic import TypeAdapter

from mechanical_librarian.adapters.persistence.setup_state import SetupStateStore
from mechanical_librarian.application.setup_connection import (
    SetupConnectionOperation,
    SetupConnectionVerification,
)
from mechanical_librarian.application.setup_journey import (
    SetupJourneyError,
    SetupJourneyOperation,
)
from mechanical_librarian.domain.owner_settings import LibrarySettings, ProviderSettings
from mechanical_librarian.domain.setup_state import (
    SetupLibraryCandidate,
    SetupStateDocument,
)

_BOOTSTRAP = "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$hash"
_PROVIDER: TypeAdapter[ProviderSettings] = TypeAdapter(ProviderSettings)


def _store() -> tuple[sqlite3.Connection, SetupStateStore]:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.execute(
        "CREATE TABLE owner_settings (singleton INTEGER PRIMARY KEY,revision INTEGER,document TEXT)"
    )
    connection.execute(
        "CREATE TABLE owner_setting_secrets (field_id TEXT PRIMARY KEY,value TEXT)"
    )
    connection.execute(
        "CREATE TABLE reviewer_access (singleton INTEGER PRIMARY KEY,revision INTEGER,document TEXT)"
    )
    connection.execute(
        "CREATE TABLE reviewer_access_secrets (field_id TEXT PRIMARY KEY,value TEXT)"
    )
    return connection, SetupStateStore(connection)


def _connected(store: SetupStateStore) -> SetupStateDocument:
    state = store.initialize(_BOOTSTRAP)
    state = store.write(
        state.model_copy(
            update={"revision": 2, "completed_steps": ["claim"], "claimed": True}
        ),
        1,
        _BOOTSTRAP,
    )
    return SetupConnectionOperation(
        SetupConnectionVerification(lambda _endpoint, _key: None), store, _BOOTSTRAP
    ).connect(2, "primary", "https://abs.invalid", "abs-secret")


def _journey(
    store: SetupStateStore,
    candidates: tuple[SetupLibraryCandidate, ...] = (),
    mounts: dict[str, tuple[str, ...]] | None = None,
) -> SetupJourneyOperation:
    return SetupJourneyOperation(
        store,
        _BOOTSTRAP,
        lambda _document: candidates,
        {} if mounts is None else mounts,
    )


def test_provider_keys_and_open_library_contact_are_recorded_without_exposure() -> None:
    connection, store = _store()
    state = _connected(store)
    journey = _journey(store)
    providers = [
        _PROVIDER.validate_python(
            {
                "name": "hardcover",
                "adapter": "hardcover",
                "enabled": True,
                "cover_enabled": False,
            }
        ),
        _PROVIDER.validate_python(
            {
                "name": "inventaire",
                "adapter": "inventaire",
                "enabled": True,
                "cover_enabled": True,
            }
        ),
        _PROVIDER.validate_python(
            {
                "name": "open",
                "adapter": "open_library",
                "enabled": True,
                "cover_enabled": False,
                "contact_email": "operator@example.invalid",
            }
        ),
    ]

    recorded = journey.providers(
        state.revision, providers, {"provider.hardcover.key": "provider-secret"}
    )

    assert recorded.providers[1].adapter == "inventaire"
    assert recorded.providers[1].cover_enabled is True
    assert recorded.providers[2].contact_email == "operator@example.invalid"  # type: ignore[union-attr]
    assert "provider-secret" not in recorded.model_dump_json()
    assert connection.execute(
        "SELECT field_id FROM owner_setting_secrets ORDER BY field_id"
    ).fetchall() == [
        ("connection.primary.key",),
        ("provider.hardcover.key",),
    ]


def test_library_selection_rechecks_discovery_and_rejects_none_or_unsupported() -> None:
    _connection, store = _store()
    state = _connected(store)
    journey = _journey(
        store,
        (
            SetupLibraryCandidate(library_id="books", name="Books", media_kind="book"),
            SetupLibraryCandidate(
                library_id="podcasts", name="Podcasts", media_kind="unsupported"
            ),
        ),
    )
    state = journey.providers(state.revision, [], {})

    with pytest.raises(SetupJourneyError) as empty:
        journey.libraries(state.revision, [])
    with pytest.raises(SetupJourneyError) as unsupported:
        journey.libraries(
            state.revision,
            [
                LibrarySettings(
                    name="podcasts",
                    connection="primary",
                    library_id="podcasts",
                    media_kind="audiobook",
                )
            ],
        )

    assert empty.value.code == "invalid_libraries"
    assert unsupported.value.code == "invalid_libraries"
    assert store.read(_BOOTSTRAP).next_step() == "libraries"


def test_library_selection_rejects_duplicate_canonical_local_names() -> None:
    _connection, store = _store()
    state = _connected(store)
    journey = _journey(
        store,
        (
            SetupLibraryCandidate(library_id="one", name="One", media_kind="book"),
            SetupLibraryCandidate(library_id="two", name="Two", media_kind="book"),
        ),
    )
    state = journey.providers(state.revision, [], {})

    with pytest.raises(SetupJourneyError) as error:
        journey.libraries(
            state.revision,
            [
                LibrarySettings(
                    name="books",
                    connection="primary",
                    library_id=library_id,
                    media_kind="ebook",
                )
                for library_id in ("one", "two")
            ],
        )

    assert error.value.code == "invalid_libraries"
    assert store.read(_BOOTSTRAP) == state


def test_library_selection_preserves_discovered_name_and_owner_override() -> None:
    _connection, store = _store()
    state = _connected(store)
    journey = _journey(
        store,
        (
            SetupLibraryCandidate(
                library_id="one",
                name="Ebooks & Audiobooks: Collected Editions",
                media_kind="book",
            ),
            SetupLibraryCandidate(
                library_id="two", name="Second Shelf", media_kind="book"
            ),
        ),
    )
    state = journey.providers(state.revision, [], {})

    state = journey.libraries(
        state.revision,
        [
            LibrarySettings(
                name="books",
                connection="primary",
                library_id="one",
                media_kind="book",
            ),
            LibrarySettings(
                name="personal",
                display_name="My Reading Shelf",
                connection="primary",
                library_id="two",
                media_kind="book",
            ),
        ],
    )

    assert [library.display_name for library in state.libraries] == [
        "Ebooks & Audiobooks: Collected Editions",
        "My Reading Shelf",
    ]


def test_colliding_discovered_names_keep_unique_stable_names_and_exact_labels() -> None:
    _connection, store = _store()
    state = _connected(store)
    journey = _journey(
        store,
        (
            SetupLibraryCandidate(library_id="one", name="Books", media_kind="book"),
            SetupLibraryCandidate(library_id="two", name="Books!", media_kind="book"),
        ),
    )
    state = journey.providers(state.revision, [], {})

    state = journey.libraries(
        state.revision,
        [
            LibrarySettings(
                name="books",
                connection="primary",
                library_id="one",
                media_kind="book",
            ),
            LibrarySettings(
                name="books-2",
                connection="primary",
                library_id="two",
                media_kind="book",
            ),
        ],
    )

    assert [(library.name, library.display_name) for library in state.libraries] == [
        ("books", "Books"),
        ("books-2", "Books!"),
    ]


def test_library_discovery_failure_is_retryable_and_records_nothing() -> None:
    _connection, store = _store()
    state = _connected(store)

    def fail(document: SetupStateDocument) -> tuple[SetupLibraryCandidate, ...]:
        del document
        raise RuntimeError("private detail")

    journey = SetupJourneyOperation(store, _BOOTSTRAP, fail, {})
    state = journey.providers(state.revision, [], {})

    with pytest.raises(SetupJourneyError) as error:
        journey.discover(state.revision)

    assert error.value.code == "unavailable"
    assert "private detail" not in str(error.value)
    assert store.read(_BOOTSTRAP) == state


def test_mount_comparison_records_matched_ambiguous_missing_and_unavailable() -> None:
    _connection, store = _store()
    state = _connected(store)
    candidates = tuple(
        SetupLibraryCandidate(library_id=name, name=name.title(), media_kind="book")
        for name in ("matched", "ambiguous", "unmatched", "audio")
    )
    journey = _journey(
        store,
        candidates,
        {"/matched": ("books",), "/ambiguous": ("a", "b")},
    )
    state = journey.providers(state.revision, [], {})
    state = journey.libraries(
        state.revision,
        [
            LibrarySettings(
                name=name,
                connection="primary",
                library_id=name,
                media_kind="book",
            )
            for name in ("matched", "ambiguous", "unmatched", "audio")
        ],
    )

    state = journey.mounts(
        state.revision,
        {
            "matched": "/matched",
            "ambiguous": "/ambiguous",
            "unmatched": "/missing",
            "audio": None,
        },
    )

    assert {value.library: value.state for value in state.mounts} == {
        "ambiguous": "ambiguous",
        "audio": "unavailable",
        "matched": "matched",
        "unmatched": "missing",
    }
    assert state.next_step() == "mounts"
    assert "mounts" not in state.completed_steps
    assert (
        next(value for value in state.libraries if value.name == "matched").mount
        == "books"
    )


def test_mount_retest_uses_discovered_service_path_without_browser_input() -> None:
    _connection, store = _store()
    state = _connected(store)
    journey = _journey(
        store,
        (
            SetupLibraryCandidate(
                library_id="books",
                name="Books",
                media_kind="book",
                service_paths=("/service/books",),
            ),
        ),
        {"/service/books": ("books",)},
    )
    state = journey.providers(state.revision, [], {})
    state = journey.libraries(
        state.revision,
        [
            LibrarySettings(
                name="books",
                connection="primary",
                library_id="books",
                media_kind="book",
            )
        ],
    )
    state = journey.mounts(state.revision, {"books": None})
    assert state.mounts[0].state == "matched"
    assert state.libraries[0].mount == "books"


@pytest.mark.parametrize(
    ("service_paths", "mounts", "expected"),
    (
        (
            ("/service/one", "/service/missing"),
            {"/service/one": ("books",)},
            "missing",
        ),
        (
            ("/service/one", "/service/two"),
            {"/service/one": ("books",), "/service/two": ("other",)},
            "ambiguous",
        ),
        (
            ("/service/one", "/service/ambiguous"),
            {
                "/service/one": ("books",),
                "/service/ambiguous": ("books", "other"),
            },
            "ambiguous",
        ),
    ),
    ids=("missing-folder", "mixed-mounts", "ambiguous-folder"),
)
def test_every_discovered_service_path_must_resolve_to_one_consistent_mount(
    service_paths: tuple[str, ...],
    mounts: dict[str, tuple[str, ...]],
    expected: str,
) -> None:
    _connection, store = _store()
    state = _connected(store)
    journey = _journey(
        store,
        (
            SetupLibraryCandidate(
                library_id="books",
                name="Books",
                media_kind="book",
                service_paths=service_paths,
            ),
        ),
        mounts,
    )
    state = journey.providers(state.revision, [], {})
    state = journey.libraries(
        state.revision,
        [
            LibrarySettings(
                name="books",
                connection="primary",
                library_id="books",
                media_kind="book",
            )
        ],
    )

    state = journey.mounts(state.revision, {"books": None})

    assert state.mounts[0].state == expected
    assert state.libraries[0].mount is None
    assert state.next_step() == "mounts"


def test_every_discovered_service_path_may_resolve_to_the_same_mount() -> None:
    _connection, store = _store()
    state = _connected(store)
    journey = _journey(
        store,
        (
            SetupLibraryCandidate(
                library_id="books",
                name="Books",
                media_kind="book",
                service_paths=("/service/one", "/service/two"),
            ),
        ),
        {"/service/one": ("books",), "/service/two": ("books",)},
    )
    state = journey.providers(state.revision, [], {})
    state = journey.libraries(
        state.revision,
        [
            LibrarySettings(
                name="books",
                connection="primary",
                library_id="books",
                media_kind="book",
            )
        ],
    )

    state = journey.mounts(state.revision, {"books": None})

    assert state.mounts[0].state == "matched"
    assert state.libraries[0].mount == "books"


def test_change_removes_dependent_answers_and_only_their_secrets() -> None:
    connection, store = _store()
    state = _connected(store)
    journey = _journey(store)
    provider = _PROVIDER.validate_python(
        {
            "name": "hardcover",
            "adapter": "hardcover",
            "enabled": True,
            "cover_enabled": False,
        }
    )
    state = journey.providers(
        state.revision, [provider], {"provider.hardcover.key": "provider-secret"}
    )

    changed = journey.change(state.revision, "providers")

    assert changed.next_step() == "providers"
    assert connection.execute(
        "SELECT field_id FROM owner_setting_secrets"
    ).fetchall() == [("connection.primary.key",)]


def test_kickoff_atomically_promotes_and_preserves_connection_secret() -> None:
    connection, store = _store()
    state = _connected(store)
    candidate = SetupLibraryCandidate(
        library_id="books", name="Books", media_kind="book"
    )
    journey = _journey(store, (candidate,), {"/library": ("books",)})
    google = _PROVIDER.validate_python(
        {
            "name": "google",
            "adapter": "google_books",
            "enabled": True,
            "cover_enabled": False,
        }
    )
    state = journey.providers(state.revision, [google], {})
    state = journey.libraries(
        state.revision,
        [
            LibrarySettings(
                name="books",
                connection="primary",
                library_id="books",
                media_kind="ebook",
            )
        ],
    )
    state = journey.mounts(state.revision, {"books": "/library"})
    state = journey.reviewer(state.revision, 1, "skipped")

    journey.kick_off(state.revision)

    document = connection.execute("SELECT document FROM owner_settings").fetchone()[0]
    assert '"schema_version":"owner-settings-v4"' in document
    assert '"pending_restart":[]' in document
    assert connection.execute(
        "SELECT field_id,value FROM owner_setting_secrets ORDER BY field_id"
    ).fetchall() == [
        ("connection.primary.key", "abs-secret"),
        ("owner.password.verifier", _BOOTSTRAP),
    ]

    before = (
        connection.execute("SELECT revision,document FROM owner_settings").fetchall(),
        connection.execute(
            "SELECT field_id,value FROM owner_setting_secrets ORDER BY field_id"
        ).fetchall(),
    )
    with pytest.raises(SetupJourneyError, match="stale"):
        journey.kick_off(state.revision)
    after = (
        connection.execute("SELECT revision,document FROM owner_settings").fetchall(),
        connection.execute(
            "SELECT field_id,value FROM owner_setting_secrets ORDER BY field_id"
        ).fetchall(),
    )
    assert after == before
