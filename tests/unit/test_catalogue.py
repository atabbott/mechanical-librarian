from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
from dataclasses import replace
from multiprocessing import Event, Process
from multiprocessing.synchronize import Event as EventType
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from mechanical_librarian.adapters.persistence import catalogue as catalogue_module
from mechanical_librarian.adapters.persistence.catalogue import (
    CatalogueSchemaAdmission,
    SQLiteCatalogueRepository,
    open_catalogue,
)
from mechanical_librarian.application.catalogue import (
    CatalogueBusyError,
    CatalogueEntry,
    CatalogueError,
    CatalogueRepository,
    CatalogueStaleError,
    RefreshCurrentCatalogue,
    catalogue_fingerprint,
)
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    TargetConnectionConfiguration,
)
from mechanical_librarian.bootstrap.composition import run_catalogue


def _entry(
    item: str = "item-1",
    *,
    title: str = "Title",
    author: str = "Author",
    path: str = "book.epub",
    item_path: str | None = None,
) -> CatalogueEntry:
    return CatalogueEntry(
        "primary",
        "books",
        "library-id",
        item,
        1,
        title,
        (author,),
        True,
        False,
        "singular-supported",
        "a" * 64,
        path,
        "epub",
        "books",
        item_path=item_path,
    )


class _BlockingReader:
    def __init__(self, ready: EventType, release: EventType) -> None:
        self._ready = ready
        self._release = release

    def read_library(
        self, connection: str, library: LibraryConfiguration, remaining: int
    ) -> tuple[CatalogueEntry, ...]:
        self._ready.set()
        self._release.wait(10)
        return (_entry(),)


def _configuration() -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/token"
            ),
        ),
        (LibraryConfiguration("books", "primary", "library-id", "book", None, ()),),
        (),
        (),
    )


def _two_library_configuration() -> ApplicationConfiguration:
    return ApplicationConfiguration(
        (
            TargetConnectionConfiguration(
                "primary", "https://example.invalid", "/private/token"
            ),
        ),
        (
            LibraryConfiguration("audio", "primary", "audio-id", "book", None, ()),
            LibraryConfiguration("books", "primary", "library-id", "book", None, ()),
        ),
        (),
        (),
    )


def _hold_refresh_lock(path: str, ready: EventType, release: EventType) -> None:
    repository = open_catalogue(Path(path), create=True)
    try:
        RefreshCurrentCatalogue(_BlockingReader(ready, release), repository).refresh(
            _configuration()
        )
    finally:
        repository.close()


def test_first_library_refresh_builds_a_progressive_isolated_baseline() -> None:
    class Reader:
        def __init__(self) -> None:
            self.libraries: list[str] = []

        def read_library(self, connection, library, remaining):
            self.libraries.append(library.name)
            return (
                CatalogueEntry(
                    connection,
                    library.name,
                    library.library_id,
                    f"{library.name}-item",
                    1,
                    library.name,
                    (),
                    True,
                    False,
                    "singular-supported",
                    "a" * 64,
                    f"/{library.name}.epub",
                    "epub",
                    "books",
                ),
            )

    with tempfile.TemporaryDirectory() as directory:
        repository = open_catalogue(Path(directory) / "catalogue.sqlite", create=True)
        reader = Reader()
        configuration = _two_library_configuration()
        RefreshCurrentCatalogue(reader, repository).refresh_library(
            configuration, "primary", "books"
        )
        first = repository.inspect(catalogue_fingerprint(configuration).digest)
        RefreshCurrentCatalogue(reader, repository).refresh_library(
            configuration, "primary", "audio"
        )
        completed = repository.inspect(catalogue_fingerprint(configuration).digest)
        repository.close()

    assert reader.libraries == ["books", "audio"]
    assert {entry.library for entry in first} == {"books"}
    assert {entry.library for entry in completed} == {"books", "audio"}


def test_latest_entry_survives_unrelated_library_refresh() -> None:
    with tempfile.TemporaryDirectory() as directory:
        repository = open_catalogue(Path(directory) / "catalogue.sqlite", create=True)
        fingerprint = "a" * 64
        books = _entry()
        audio = CatalogueEntry(
            "primary",
            "audio",
            "audio-id",
            "audio-item",
            1,
            "Audio",
            (),
            False,
            True,
            "singular-supported",
            "b" * 64,
            "/audio.m4b",
            "m4b",
            "books",
        )
        repository.acquire_refresh()
        repository.publish(fingerprint, (books, audio))
        repository.acquire_refresh()
        repository.publish(fingerprint, (books, replace(audio, updated_at=2)))
        found = repository.latest_entry(
            fingerprint,
            ("primary", "books", "library-id", "item-1"),
        )
        repository.close()

    assert found == books


def test_unavailable_first_library_does_not_block_healthy_baseline() -> None:
    class Reader:
        def read_library(self, connection, library, remaining):
            if library.name == "audio":
                raise CatalogueError("unavailable")
            return (
                CatalogueEntry(
                    connection,
                    library.name,
                    library.library_id,
                    "book",
                    1,
                    "Book",
                    (),
                    True,
                    False,
                    "singular-supported",
                    "a" * 64,
                    "/book.epub",
                    "epub",
                    "books",
                ),
            )

    with tempfile.TemporaryDirectory() as directory:
        repository = open_catalogue(Path(directory) / "catalogue.sqlite", create=True)
        service = RefreshCurrentCatalogue(Reader(), repository)
        configuration = _two_library_configuration()
        with pytest.raises(CatalogueError):
            service.refresh_library(configuration, "primary", "audio")
        service.refresh_library(configuration, "primary", "books")
        entries = repository.inspect(catalogue_fingerprint(configuration).digest)
        repository.close()

    assert tuple(entry.library for entry in entries) == ("books",)


def test_refresh_cleanup_failure_never_replaces_the_refresh_failure() -> None:
    """A lock release that fails must not become the failure the caller sees.

    Both refresh lanes release the lock in a cleanup-only handler annotated
    as re-raising the failure exactly as it arrived. `abort_refresh` is as
    undeclared as the reader and the publish it guards, so a release that
    raised replaced the active exception and the bare `raise` never ran:
    the caller was told the lock broke rather than why the refresh did
    (#698).
    """
    deep = CatalogueError("deep read failed")
    library = CatalogueError("library read failed")

    class Repository:
        def __init__(self) -> None:
            self.aborted = 0

        def acquire_refresh(self) -> None:
            return None

        def abort_refresh(self) -> None:
            self.aborted += 1
            raise RuntimeError("refresh lock is wedged")

        def inspect(self, fingerprint):
            raise library

        def publish(self, fingerprint, entries) -> int:
            raise AssertionError("publication is unreachable here")

    class Reader:
        def read_library(self, connection, library_configuration, remaining):
            raise deep

    configuration = _configuration()
    repository = Repository()
    service = RefreshCurrentCatalogue(Reader(), cast("CatalogueRepository", repository))

    for expected, run in (
        (deep, lambda: service.refresh(configuration)),
        (
            library,
            lambda: service.refresh_library(configuration, "primary", "books"),
        ),
    ):
        with pytest.raises(CatalogueError) as raised:
            run()
        assert raised.value is expected
        assert [
            "refresh lock release failed: RuntimeError('refresh lock is wedged')"
        ] == list(getattr(raised.value, "__notes__", []))
    assert repository.aborted == 2


def test_atomic_current_switch_and_stale_fingerprint() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        repository = open_catalogue(path, create=True)
        repository.acquire_refresh()
        assert repository.publish("a" * 64, (_entry(),)) == 1
        assert repository.inspect("a" * 64) == (_entry(),)
        with pytest.raises(CatalogueStaleError):
            repository.inspect("b" * 64)
        repository.close()


def test_cross_process_second_refresh_is_nonblocking_busy() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        first = open_catalogue(path, create=True)
        first.close()
        ready = Event()
        release = Event()
        holder = Process(target=_hold_refresh_lock, args=(str(path), ready, release))
        holder.start()
        try:
            assert ready.wait(5)
            second = open_catalogue(path)
            started = time.monotonic()
            with pytest.raises(CatalogueBusyError):
                RefreshCurrentCatalogue(
                    _BlockingReader(Event(), Event()), second
                ).refresh(_configuration())
            # Opens and reads wait out a lock; a second refresh never does.
            assert time.monotonic() - started < 1
            assert second._connection.execute("PRAGMA busy_timeout").fetchone() == (
                5000,
            )
        finally:
            release.set()
            holder.join(10)
            assert holder.exitcode == 0
            second.close()


def test_existing_altered_schema_and_symlink_are_rejected_without_mutation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        repository = open_catalogue(path, create=True)
        repository.close()
        before = path.read_bytes()
        connection = sqlite3.connect(path)
        connection.execute("DROP INDEX catalogue_one_current")
        connection.commit()
        connection.close()
        altered = path.read_bytes()
        with pytest.raises(CatalogueError):
            open_catalogue(path)
        assert path.read_bytes() == altered
        link = Path(directory) / "link.sqlite"
        os.symlink(path, link)
        with pytest.raises(OSError):
            open_catalogue(link)
        assert before != altered


def test_existing_extra_trigger_and_view_are_rejected_without_mutation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        repository = open_catalogue(path, create=True)
        repository.close()
        connection = sqlite3.connect(path)
        connection.execute("CREATE VIEW unexpected_view AS SELECT 1")
        connection.execute(
            "CREATE TRIGGER unexpected_trigger AFTER INSERT ON catalogue_schema BEGIN SELECT 1; END"
        )
        connection.commit()
        connection.close()
        before = path.read_bytes()
        with pytest.raises(CatalogueError):
            open_catalogue(path)
        assert path.read_bytes() == before


def test_list_projection_suppresses_paths_and_get_is_exactly_extended() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        repository = open_catalogue(path, create=True)
        repository.acquire_refresh()
        repository.publish(catalogue_fingerprint(_configuration()).digest, (_entry(),))
        repository.close()
        with patch(
            "mechanical_librarian.bootstrap.composition.load_configuration_file",
            return_value=_configuration(),
        ):
            listed = json.loads(
                run_catalogue(Path("ignored"), path, "list", None, None, None, None)
            )
            got = json.loads(
                run_catalogue(
                    Path("ignored"),
                    path,
                    "get",
                    "primary",
                    "books",
                    "library-id",
                    "item-1",
                )
            )
        public = {
            "connection",
            "library",
            "library_id",
            "item",
            "title",
            "authors",
            "ebook",
            "audio",
            "classification",
            "eligible",
        }
        assert set(listed[0]) == public
        assert {
            "updated_at",
            "file_identity",
            "format",
            "service_path",
            "mount",
        }.isdisjoint(listed[0])
        assert set(got[0]) == public | {
            "updated_at",
            "file_identity",
            "format",
            "service_path",
            "mount",
        }


def test_replacement_during_refresh_fails_before_publication_and_keeps_prior_current() -> (
    None
):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        repository = open_catalogue(path, create=True)
        repository.acquire_refresh()
        repository.publish("a" * 64, (_entry(),))
        repository.acquire_refresh()
        replacement = Path(directory) / "replacement.sqlite"
        replacement.write_bytes(b"replacement")
        os.replace(replacement, path)
        with pytest.raises(CatalogueError):
            repository.publish("a" * 64, (_entry("item-2"),))
        assert repository.inspect("a" * 64) == (_entry(),)
        repository.close()


def test_refresh_retains_one_generation_and_publication_failure_rolls_back() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        repository = open_catalogue(path, create=True)
        original = _entry(title="Prior title", author="Prior author", path="prior.epub")
        current = _entry(
            "item-2",
            title="Current title",
            author="Current author",
            path="current.epub",
        )
        repository.acquire_refresh()
        repository.publish("a" * 64, (original,))
        repository.acquire_refresh()
        repository.publish("a" * 64, (current,))
        assert repository._connection.execute(
            "SELECT generation,current FROM catalogue_generations"
        ).fetchall() == [(2, 1)]
        assert repository._connection.execute(
            "SELECT title,authors,service_path FROM catalogue_entries"
        ).fetchall() == [("Current title", '["Current author"]', "current.epub")]
        repository.acquire_refresh()
        with patch.object(
            repository,
            "_require_bound_name",
            side_effect=[None, CatalogueError("catalogue publication failed")],
        ):
            with pytest.raises(CatalogueError):
                repository.publish("a" * 64, (_entry("item-3"),))
        assert repository.inspect("a" * 64) == (current,)
        repository.close()


def test_inspection_never_creates_an_absent_or_deleted_catalogue() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        with patch(
            "mechanical_librarian.bootstrap.composition.load_configuration_file",
            return_value=_configuration(),
        ):
            for action, values in (
                ("list", (None, None, None, None)),
                ("get", ("primary", "books", "library-id", "item-1")),
            ):
                with pytest.raises(CatalogueStaleError):
                    run_catalogue(Path("ignored"), path, action, *values)
                assert not path.exists()
        open_catalogue(path, create=True).close()
        original = SQLiteCatalogueRepository._require_bound_name

        def remove_after_binding(
            repository: SQLiteCatalogueRepository, identity: os.stat_result
        ) -> None:
            original(repository, identity)
            path.unlink()

        with patch.object(
            SQLiteCatalogueRepository, "_require_bound_name", new=remove_after_binding
        ):
            with pytest.raises(CatalogueStaleError):
                open_catalogue(path)
        assert not path.exists()


@pytest.mark.parametrize(
    "entries",
    (
        pytest.param((), id="zero_matches"),
        pytest.param((_entry(), _entry()), id="multiple_matches"),
    ),
)
def test_exact_get_rejects_zero_and_multiple_matches(
    entries: tuple[CatalogueEntry, ...],
) -> None:
    class Repository:
        def __init__(self, entries: tuple[CatalogueEntry, ...]) -> None:
            self.entries = entries

        def inspect(self, _: str) -> tuple[CatalogueEntry, ...]:
            return self.entries

    exact = ("primary", "books", "library-id", "item-1")
    with pytest.raises(CatalogueError):
        RefreshCurrentCatalogue(None, Repository(entries)).inspect(
            _configuration(), exact=exact
        )


def test_library_admission_validates_once_and_detaches_after_failure() -> None:
    from mechanical_librarian.adapters.persistence import catalogue as adapter

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        writer = open_catalogue(path, create=True)
        writer.acquire_refresh()
        writer.publish("a" * 64, (_entry(),))
        connection = sqlite3.connect(":memory:", uri=True, isolation_level=None)
        admission = adapter.CatalogueReadAdmission(path, "a" * 64)
        with patch.object(
            adapter, "_validate_schema", wraps=adapter._validate_schema
        ) as validate:
            for _ in range(3):
                with admission.attach(connection):
                    connection.execute("BEGIN")
                    assert connection.execute(
                        "SELECT title FROM retained_catalogue.catalogue_entries"
                    ).fetchall() == [("Title",)]
                    connection.execute("COMMIT")
            assert validate.call_count == 1
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                with admission.attach(connection):
                    connection.execute(
                        "UPDATE retained_catalogue.catalogue_entries SET title='Forbidden'"
                    )
            assert connection.execute("PRAGMA database_list").fetchall() == [
                (0, "main", "")
            ]
            with admission.attach(connection):
                assert writer.inspect("a" * 64)[0].title == "Title"
            assert validate.call_count == 1
        writer.close()
        admission.close()
        connection.close()


def test_library_admission_missing_then_available_and_drift_requires_reopen() -> None:
    from mechanical_librarian.adapters.persistence.catalogue import (
        CatalogueReadAdmission,
    )

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        connection = sqlite3.connect(":memory:", uri=True, isolation_level=None)
        admission = CatalogueReadAdmission(path, "a" * 64)
        with admission.attach(connection):
            assert not admission.active
        assert not path.exists()
        writer = open_catalogue(path, create=True)
        writer.close()
        with admission.attach(connection):
            assert admission.active
        path.rename(path.with_suffix(".old"))
        with pytest.raises(CatalogueError, match="pathname changed"):
            with admission.attach(connection):
                pytest.fail("deleted admitted pathname accepted")
        writer = open_catalogue(path, create=True)
        writer.close()
        with pytest.raises(CatalogueError, match="pathname changed"):
            with admission.attach(connection):
                pytest.fail("replacement admitted")
        admission.close()
        admission = CatalogueReadAdmission(path, "a" * 64)
        with admission.attach(connection):
            assert admission.active
        with sqlite3.connect(path) as changed:
            changed.execute("CREATE TABLE unapproved(value TEXT)")
        with pytest.raises(CatalogueError, match="schema changed"):
            with admission.attach(connection):
                pytest.fail("schema drift admitted")
        admission.close()
        admission = CatalogueReadAdmission(path, "a" * 64)
        with pytest.raises(CatalogueError, match="schema is unsupported"):
            with admission.attach(connection):
                pytest.fail("unknown schema admitted")
        admission.close()
        assert connection.execute("PRAGMA database_list").fetchall() == [
            (0, "main", "")
        ]
        connection.close()


def test_item_path_round_trips_and_stays_out_of_the_source_fingerprint() -> None:
    """The item's own path (#1478) is kept for identifiers files only."""
    assert (
        _entry(item_path="Shelf/Book").source_fingerprint == _entry().source_fingerprint
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        repository = open_catalogue(path, create=True)
        stored = (_entry(item_path="Shelf/Book"), _entry("item-2"))
        repository.acquire_refresh()
        generation = repository.publish("a" * 64, stored)
        assert repository.inspect("a" * 64) == stored
        key = ("primary", "books", "library-id", "item-1")
        assert repository.current_entry(generation, "a" * 64, key) == stored[0]
        assert repository.latest_entry("a" * 64, key) == stored[0]
        with pytest.raises(sqlite3.IntegrityError):
            repository._connection.execute(
                "UPDATE catalogue_entries SET item_path='' WHERE item_id='item-1'"
            )
        repository.close()


def test_a_catalogue_main_created_before_item_paths_is_refused_unchanged(
    tmp_path: Path,
) -> None:
    """A catalogue-v2 database from 5f775fd, whose entries have no item path,
    fails schema validation and is left as it was (#1478)."""
    fixture = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/predecessor-stores-5f775fd.json"
        ).read_text(encoding="utf-8")
    )["catalogue"]
    path = tmp_path / "catalogue.sqlite"
    connection = sqlite3.connect(path)
    try:
        connection.executescript("".join(sql + ";" for sql in fixture["sql"]))
        connection.execute(
            "INSERT INTO catalogue_schema(version) VALUES(?)", (fixture["version"],)
        )
        connection.commit()
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(catalogue_entries)")
        }
    finally:
        connection.close()
    assert "item_path" not in columns
    before = path.read_bytes()
    with pytest.raises(CatalogueError):
        open_catalogue(path)
    assert path.read_bytes() == before


def _exclusive_holder(path: Path) -> sqlite3.Connection:
    """A real connection holding the catalogue's file lock until it closes."""
    holder = sqlite3.connect(
        path, timeout=0, isolation_level=None, check_same_thread=False
    )
    holder.execute("PRAGMA locking_mode = EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    return holder


def test_open_waits_through_a_brief_exclusive_hold() -> None:
    """#2024: an open waits out a short lock instead of failing at once."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        open_catalogue(path, create=True).close()
        holder = _exclusive_holder(path)
        release = threading.Timer(0.3, holder.close)
        started = time.monotonic()
        release.start()
        try:
            repository = open_catalogue(path)
        finally:
            release.join()
        assert time.monotonic() - started >= 0.25
        repository.close()


def test_busy_open_and_read_are_busy_and_other_sqlite_failures_are_not() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        repository = open_catalogue(path, create=True)
        repository.close()
        holder = _exclusive_holder(path)
        try:
            with (
                patch.object(catalogue_module, "_BUSY_TIMEOUT_SECONDS", 0.0),
                pytest.raises(CatalogueBusyError) as busy,
            ):
                open_catalogue(path)
        finally:
            holder.close()
        cause = busy.value.__cause__
        assert isinstance(cause, sqlite3.OperationalError)
        assert cause.sqlite_errorcode == sqlite3.SQLITE_BUSY
        reader = open_catalogue(path)
        connection = reader._connection

        class Contended:
            """A read whose SELECT meets another writer's lock."""

            @property
            def in_transaction(self) -> bool:
                return connection.in_transaction

            def execute(self, sql: str, *arguments: object) -> sqlite3.Cursor:
                if sql.startswith("SELECT"):
                    error = sqlite3.OperationalError("database is locked")
                    error.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
                    raise error
                return connection.execute(sql, *arguments)

        reader._connection = cast(sqlite3.Connection, Contended())
        with pytest.raises(CatalogueBusyError):
            reader.latest_entry("a" * 64, ("primary", "books", "id", "item"))
        reader._connection = connection
        assert not connection.in_transaction
        dropper = sqlite3.connect(path)
        dropper.execute("DROP TABLE catalogue_entries")
        dropper.execute("DROP TABLE catalogue_generations")
        dropper.commit()
        dropper.close()
        with pytest.raises(CatalogueError) as missing:
            reader.latest_entry("a" * 64, ("primary", "books", "id", "item"))
        assert type(missing.value) is CatalogueError
        assert isinstance(missing.value.__cause__, sqlite3.OperationalError)
        assert missing.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_ERROR
        assert not connection.in_transaction
        reader.close()
        damaged = Path(directory) / "damaged.sqlite"
        damaged.write_bytes(b"not a database" * 512)
        damaged.chmod(0o600)
        with pytest.raises(CatalogueError) as failed:
            open_catalogue(damaged)
        assert type(failed.value) is CatalogueError
        assert str(failed.value) == "catalogue database is unavailable"
        assert isinstance(failed.value.__cause__, sqlite3.DatabaseError)
        assert failed.value.__cause__.sqlite_errorcode == sqlite3.SQLITE_NOTADB


def _validation_spy() -> tuple[list[bool], list[str]]:
    full: list[bool] = []
    readonly: list[str] = []
    validate = catalogue_module._validate_schema
    existing = SQLiteCatalogueRepository._validate_existing_readonly

    def schema(*arguments: object, **keywords: object) -> None:
        full.append(cast(bool, keywords.get("full", True)))
        validate(*arguments, **keywords)

    def readonly_pass(repository: SQLiteCatalogueRepository) -> None:
        readonly.append("readonly")
        existing(repository)

    patch.object(catalogue_module, "_validate_schema", schema).start()
    patch.object(
        SQLiteCatalogueRepository, "_validate_existing_readonly", readonly_pass
    ).start()
    return full, readonly


def test_schema_admission_validates_a_file_version_in_full_once() -> None:
    """#2024: later opens of an unchanged file check only the exact schema."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        open_catalogue(path, create=True).close()
        admission = CatalogueSchemaAdmission()
        try:
            full, readonly = _validation_spy()
            for _ in range(3):
                open_catalogue(path, admission=admission).close()
            # Read-only pass and full main check once, then exact rows only.
            assert readonly == ["readonly"]
            assert full == [True, True, False, False]
            full.clear()
            readonly.clear()
            for _ in range(2):
                open_catalogue(path).close()
            # Without an admission every open validates in full.
            assert readonly == ["readonly", "readonly"]
            assert full == [True, True, True, True]
            full.clear()
            readonly.clear()
            writer = sqlite3.connect(path)
            writer.execute("UPDATE catalogue_schema SET version=version")
            writer.commit()
            writer.close()
            open_catalogue(path, admission=admission).close()
            # A write that reached the file forces a full validation again.
            assert readonly == ["readonly"]
            assert full == [True, True]
        finally:
            patch.stopall()


def test_schema_admission_still_refuses_a_changed_schema() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "catalogue.sqlite"
        open_catalogue(path, create=True).close()
        connection = sqlite3.connect(path)
        connection.execute("CREATE VIEW unexpected_view AS SELECT 1")
        connection.commit()
        connection.close()
        admission = CatalogueSchemaAdmission()
        # Even a matching key, as if the change left the file's times alone,
        # keeps the exact schema comparison.
        admission.admit(path.stat())
        try:
            full, readonly = _validation_spy()
            with pytest.raises(CatalogueError, match="catalogue schema is unsupported"):
                open_catalogue(path, admission=admission)
            assert readonly == [] and full == [False]
        finally:
            patch.stopall()
        with pytest.raises(CatalogueError):
            open_catalogue(path, admission=admission)


def _unread(item: str = "unread-1", code: str = "no_media") -> CatalogueEntry:
    return CatalogueEntry(
        "primary",
        "books",
        "library-id",
        item,
        1,
        "Title",
        (),
        False,
        False,
        "needs-attention",
        unread=code,
    )


def test_an_unread_entry_round_trips_and_its_check_is_closed() -> None:
    """An item the service could not give keeps its reason (#2013)."""
    stored = (_entry(), _unread(), _unread("unread-2", "changed"))
    assert _unread().source_fingerprint != _unread(code="unreadable").source_fingerprint
    with tempfile.TemporaryDirectory() as directory:
        repository = open_catalogue(Path(directory) / "catalogue.sqlite", create=True)
        repository.acquire_refresh()
        repository.publish("a" * 64, stored)
        assert repository.inspect("a" * 64) == stored
        for statement in (
            "UPDATE catalogue_entries SET unread='no_media' WHERE item_id='item-1'",
            "UPDATE catalogue_entries SET unread='gone' WHERE item_id='unread-1'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                repository._connection.execute(statement)
        repository.close()


@pytest.mark.parametrize(
    "fields",
    (
        {"unread": "gone"},
        {"classification": "none"},
        {"file_identity": "a" * 64},
        {"mount": "books"},
    ),
)
def test_an_unread_entry_has_nothing_to_prepare(fields: dict[str, object]) -> None:
    with pytest.raises(CatalogueError):
        replace(_unread(), **fields)


def test_a_catalogue_v3_main_without_unread_reasons_is_refused_unchanged(
    tmp_path: Path,
) -> None:
    """The catalogue-v3 shape, rebuilt here from the current DDL without its
    `unread` column, fails schema validation and is left as it was (#2013)."""
    from mechanical_librarian.adapters.persistence import catalogue as module

    unread = (
        " unread TEXT CHECK(unread IS NULL OR (classification='needs-attention' "
        "AND unread IN ('no_media','unreadable','oversized','changed'))),"
    )
    assert unread in module._SQL[3]
    statements = [
        sql.replace(module.CATALOGUE_SCHEMA, "catalogue-v3").replace(unread, "")
        for sql in module._SQL
    ]
    path = tmp_path / "catalogue.sqlite"
    connection = sqlite3.connect(path)
    try:
        connection.executescript("".join(statements))
        connection.execute(
            "INSERT INTO catalogue_schema(version) VALUES('catalogue-v3')"
        )
        connection.commit()
    finally:
        connection.close()
    before = path.read_bytes()
    with pytest.raises(CatalogueError):
        open_catalogue(path)
    assert path.read_bytes() == before
