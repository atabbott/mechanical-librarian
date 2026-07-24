from __future__ import annotations

import tempfile
import unittest
from multiprocessing import Event, Process
from pathlib import Path
import os
import sqlite3

from mechanical_librarian.adapters.persistence.catalogue import open_catalogue
from mechanical_librarian.application.catalogue import CatalogueBusyError, CatalogueEntry, CatalogueStaleError, RefreshCurrentCatalogue
from mechanical_librarian.application.configuration import ApplicationConfiguration, LibraryConfiguration, TargetConnectionConfiguration


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


if __name__ == "__main__":
    unittest.main()
