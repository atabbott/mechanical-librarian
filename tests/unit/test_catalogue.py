from __future__ import annotations

import tempfile
import unittest
from multiprocessing import Event, Process
from pathlib import Path
import os
import sqlite3
import json
from unittest.mock import patch

from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.application.catalogue import CatalogueBusyError, CatalogueEntry, CatalogueStaleError, RefreshCurrentCatalogue, catalogue_fingerprint
from mechanical_librarian.application.configuration import ApplicationConfiguration, LibraryConfiguration, TargetConnectionConfiguration
from mechanical_librarian.bootstrap.composition import run_catalogue


def _entry(item: str = "item-1") -> CatalogueEntry:
    return CatalogueEntry("primary", "books", "library-id", item, 1, "Title", ("Author",), True, False, "singular-supported", "a" * 64, "book.epub", "epub", "books")


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


def _hold_refresh_lock(path: str, ready: Event, release: Event) -> None:
    repository = open_catalogue(Path(path))
    try:
        RefreshCurrentCatalogue(_BlockingReader(ready, release), repository).refresh(_configuration())
    finally:
        repository.close()


class CatalogueRepositoryTests(unittest.TestCase):
    def test_atomic_current_switch_and_stale_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.sqlite"
            repository = open_catalogue(path)
            repository.acquire_refresh()
            self.assertEqual(repository.publish("a" * 64, (_entry(),)), 1)
            self.assertEqual(repository.inspect("a" * 64), (_entry(),))
            with self.assertRaises(CatalogueStaleError):
                repository.inspect("b" * 64)
            repository.close()

    def test_cross_process_second_refresh_is_nonblocking_busy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalogue.sqlite"
            first = open_catalogue(path)
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
            repository = open_catalogue(path); repository.close()
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
            repository = open_catalogue(path); repository.close()
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
            repository = open_catalogue(path)
            repository.acquire_refresh(); repository.publish(catalogue_fingerprint(_configuration()).digest, (_entry(),)); repository.close()
            with patch("mechanical_librarian.bootstrap.composition.load_configuration_file", return_value=_configuration()):
                listed = json.loads(run_catalogue(Path("ignored"), path, "list", None, None, None, None))
                got = json.loads(run_catalogue(Path("ignored"), path, "get", "primary", "books", "library-id", "item-1"))
            public = {"connection", "library", "library_id", "item", "title", "authors", "ebook", "audio", "classification", "eligible"}
            self.assertEqual(set(listed[0]), public)
            self.assertTrue({"updated_at", "file_identity", "format", "service_path", "mount"}.isdisjoint(listed[0]))
            self.assertEqual(set(got[0]), public | {"updated_at", "file_identity", "format", "service_path", "mount"})


if __name__ == "__main__":
    unittest.main()
