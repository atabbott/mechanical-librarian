from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from multiprocessing import Event, Process
from pathlib import Path
import os
import sqlite3
import json
from unittest.mock import patch

from mechanical_librarian.adapters.persistence.catalogue import SQLiteCatalogueRepository, open_catalogue
from mechanical_librarian.application.catalogue import CatalogueBusyError, CatalogueEntry, CatalogueError, CatalogueStaleError, RefreshCurrentCatalogue, catalogue_fingerprint
from mechanical_librarian.application.configuration import ApplicationConfiguration, LibraryConfiguration, TargetConnectionConfiguration
from mechanical_librarian.bootstrap.composition import run_catalogue


def _entry(item: str = "item-1", *, title: str = "Title", author: str = "Author", path: str = "book.epub") -> CatalogueEntry:
    return CatalogueEntry("primary", "books", "library-id", item, 1, title, (author,), True, False, "singular-supported", "a" * 64, path, "epub", "books")


class _BlockingReader:
    def __init__(self, ready: Event, release: Event) -> None:
        self._ready = ready
        self._release = release

    def read_library(self, connection: str, library: LibraryConfiguration, remaining: int) -> tuple[CatalogueEntry, ...]:
        self._ready.set()
        self._release.wait(10)
        return (_entry(),)


def _configuration() -> ApplicationConfiguration:
    return ApplicationConfiguration((TargetConnectionConfiguration("primary", "https://example.invalid", "/private/token"),), (LibraryConfiguration("books", "primary", "library-id", "book", None, ()),), (), ())


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


def _hold_refresh_lock(path: str, ready: Event, release: Event) -> None:
    repository = open_catalogue(Path(path), create=True)
    try:
        RefreshCurrentCatalogue(_BlockingReader(ready, release), repository).refresh(_configuration())
    finally:
        repository.close()


class CatalogueRepositoryTests(unittest.TestCase):
    def test_first_library_refresh_builds_a_progressive_isolated_baseline(self) -> None:
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
            repository = open_catalogue(
                Path(directory) / "catalogue.sqlite", create=True
            )
            reader = Reader()
            configuration = _two_library_configuration()
            RefreshCurrentCatalogue(reader, repository).refresh_library(
                configuration, "primary", "books"
            )
            first = repository.inspect(
                catalogue_fingerprint(configuration).digest
            )
            RefreshCurrentCatalogue(reader, repository).refresh_library(
                configuration, "primary", "audio"
            )
            completed = repository.inspect(
                catalogue_fingerprint(configuration).digest
            )
            repository.close()

        self.assertEqual(reader.libraries, ["books", "audio"])
        self.assertEqual({entry.library for entry in first}, {"books"})
        self.assertEqual(
            {entry.library for entry in completed}, {"books", "audio"}
        )

    def test_latest_entry_survives_unrelated_library_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = open_catalogue(
                Path(directory) / "catalogue.sqlite", create=True
            )
            fingerprint = "a" * 64
            books = _entry()
            audio = CatalogueEntry(
                "primary", "audio", "audio-id", "audio-item", 1, "Audio", (),
                False, True, "singular-supported", "b" * 64, "/audio.m4b",
                "m4b", "books"
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

        self.assertEqual(found, books)

    def test_unavailable_first_library_does_not_block_healthy_baseline(self) -> None:
        class Reader:
            def read_library(self, connection, library, remaining):
                if library.name == "audio":
                    raise CatalogueError("unavailable")
                return (
                    CatalogueEntry(
                        connection, library.name, library.library_id, "book", 1,
                        "Book", (), True, False, "singular-supported", "a" * 64,
                        "/book.epub", "epub", "books",
                    ),
                )

        with tempfile.TemporaryDirectory() as directory:
            repository = open_catalogue(
                Path(directory) / "catalogue.sqlite", create=True
            )
            service = RefreshCurrentCatalogue(Reader(), repository)
            configuration = _two_library_configuration()
            with self.assertRaises(CatalogueError):
                service.refresh_library(configuration, "primary", "audio")
            service.refresh_library(configuration, "primary", "books")
            entries = repository.inspect(catalogue_fingerprint(configuration).digest)
            repository.close()

        self.assertEqual(tuple(entry.library for entry in entries), ("books",))

    def test_atomic_current_switch_and_stale_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.sqlite"
            repository = open_catalogue(path, create=True)
            repository.acquire_refresh()
            self.assertEqual(repository.publish("a" * 64, (_entry(),)), 1)
            self.assertEqual(repository.inspect("a" * 64), (_entry(),))
            with self.assertRaises(CatalogueStaleError):
                repository.inspect("b" * 64)
            repository.close()

    def test_cross_process_second_refresh_is_nonblocking_busy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.sqlite"
            first = open_catalogue(path, create=True)
            first.close()
            ready = Event(); release = Event()
            holder = Process(target=_hold_refresh_lock, args=(str(path), ready, release))
            holder.start()
            try:
                self.assertTrue(ready.wait(5))
                second = open_catalogue(path)
                with self.assertRaises(CatalogueBusyError):
                    RefreshCurrentCatalogue(_BlockingReader(Event(), Event()), second).refresh(_configuration())
            finally:
                release.set(); holder.join(10)
                self.assertEqual(holder.exitcode, 0)
                second.close()

    def test_existing_altered_schema_and_symlink_are_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.sqlite"
            repository = open_catalogue(path, create=True); repository.close()
            before = path.read_bytes()
            connection = sqlite3.connect(path)
            connection.execute("DROP INDEX catalogue_one_current")
            connection.commit(); connection.close()
            altered = path.read_bytes()
            with self.assertRaises(Exception):
                open_catalogue(path)
            self.assertEqual(path.read_bytes(), altered)
            link = Path(directory) / "link.sqlite"
            os.symlink(path, link)
            with self.assertRaises(Exception):
                open_catalogue(link)
            self.assertNotEqual(before, altered)

    def test_existing_extra_trigger_and_view_are_rejected_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.sqlite"
            repository = open_catalogue(path, create=True); repository.close()
            connection = sqlite3.connect(path)
            connection.execute("CREATE VIEW unexpected_view AS SELECT 1")
            connection.execute("CREATE TRIGGER unexpected_trigger AFTER INSERT ON catalogue_schema BEGIN SELECT 1; END")
            connection.commit(); connection.close()
            before = path.read_bytes()
            with self.assertRaises(Exception):
                open_catalogue(path)
            self.assertEqual(path.read_bytes(), before)

    def test_list_projection_suppresses_paths_and_get_is_exactly_extended(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.sqlite"
            repository = open_catalogue(path, create=True)
            repository.acquire_refresh(); repository.publish(catalogue_fingerprint(_configuration()).digest, (_entry(),)); repository.close()
            with patch("mechanical_librarian.bootstrap.composition.load_configuration_file", return_value=_configuration()):
                listed = json.loads(run_catalogue(Path("ignored"), path, "list", None, None, None, None))
                got = json.loads(run_catalogue(Path("ignored"), path, "get", "primary", "books", "library-id", "item-1"))
            public = {"connection", "library", "library_id", "item", "title", "authors", "ebook", "audio", "classification", "eligible"}
            self.assertEqual(set(listed[0]), public)
            self.assertTrue({"updated_at", "file_identity", "format", "service_path", "mount"}.isdisjoint(listed[0]))
            self.assertEqual(set(got[0]), public | {"updated_at", "file_identity", "format", "service_path", "mount"})

    def test_replacement_during_refresh_fails_before_publication_and_keeps_prior_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.sqlite"
            repository = open_catalogue(path, create=True)
            repository.acquire_refresh(); repository.publish("a" * 64, (_entry(),))
            repository.acquire_refresh()
            replacement = Path(directory) / "replacement.sqlite"; replacement.write_bytes(b"replacement")
            os.replace(replacement, path)
            with self.assertRaises(Exception):
                repository.publish("a" * 64, (_entry("item-2"),))
            self.assertEqual(repository.inspect("a" * 64), (_entry(),))
            repository.close()

    def test_refresh_retains_one_generation_and_publication_failure_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.sqlite"
            repository = open_catalogue(path, create=True)
            original = _entry(title="Prior title", author="Prior author", path="prior.epub")
            current = _entry("item-2", title="Current title", author="Current author", path="current.epub")
            repository.acquire_refresh(); repository.publish("a" * 64, (original,))
            repository.acquire_refresh(); repository.publish("a" * 64, (current,))
            self.assertEqual(repository._connection.execute("SELECT generation,current FROM catalogue_generations").fetchall(), [(2, 1)])
            self.assertEqual(repository._connection.execute("SELECT title,authors,service_path FROM catalogue_entries").fetchall(), [("Current title", '["Current author"]', "current.epub")])
            repository.acquire_refresh()
            with patch.object(repository, "_require_bound_name", side_effect=[None, CatalogueError("catalogue publication failed")]):
                with self.assertRaises(CatalogueError):
                    repository.publish("a" * 64, (_entry("item-3"),))
            self.assertEqual(repository.inspect("a" * 64), (current,))
            repository.close()

    def test_inspection_never_creates_an_absent_or_deleted_catalogue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.sqlite"
            with patch("mechanical_librarian.bootstrap.composition.load_configuration_file", return_value=_configuration()):
                for action, values in (("list", (None, None, None, None)), ("get", ("primary", "books", "library-id", "item-1"))):
                    with self.assertRaises(CatalogueStaleError):
                        run_catalogue(Path("ignored"), path, action, *values)
                    self.assertFalse(path.exists())
            open_catalogue(path, create=True).close()
            original = SQLiteCatalogueRepository._require_bound_name
            def remove_after_binding(repository: SQLiteCatalogueRepository, identity: os.stat_result) -> None:
                original(repository, identity)
                path.unlink()
            with patch.object(SQLiteCatalogueRepository, "_require_bound_name", new=remove_after_binding):
                with self.assertRaises(CatalogueStaleError):
                    open_catalogue(path)
            self.assertFalse(path.exists())

    def test_exact_get_rejects_zero_and_multiple_matches(self) -> None:
        class Repository:
            def __init__(self, entries: tuple[CatalogueEntry, ...]) -> None:
                self.entries = entries
            def inspect(self, _: str) -> tuple[CatalogueEntry, ...]:
                return self.entries
        exact = ("primary", "books", "library-id", "item-1")
        for entries in ((), (_entry(), _entry())):
            with self.assertRaises(CatalogueError):
                RefreshCurrentCatalogue(None, Repository(entries)).inspect(_configuration(), exact=exact)


if __name__ == "__main__":
    unittest.main()
